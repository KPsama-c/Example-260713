"""后台任务：列表 / 观看一节 / 全部（供 CLI 与 Web UI 共用）。"""

from __future__ import annotations

import random
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from yuketang.browser import BrowserSession
from yuketang.classrooms import resolve_classroom_id as resolve_joined_classroom
from yuketang.history import append_run_history
from yuketang.login import ensure_login, is_logged_in
from yuketang.logs import (
    LogsApiError,
    fetch_all_activities,
    list_pending_replays,
    normalize_attend_filter,
    replay_segment_count,
)
from yuketang.progress import FailedStore, ProgressStore, SoftStore
from yuketang.rate import resolve_playback_rate
from yuketang.replay import ReplayResult, watch_replay
from yuketang.settings import has_classroom, resolve_runtime, save_settings
from yuketang.util import fmt_eta, origin_of, progress_key, resolve_path

LogFn = Callable[[str], None]

_DEFAULT_LESSON_SEC = 60 * 60  # 无时长 API 时的默认估算


@dataclass
class JobState:
    running: bool = False
    action: str = ""
    message: str = "空闲"
    ok: bool | None = None
    pending_preview: list[dict[str, Any]] = field(default_factory=list)
    done: int = 0
    fail: int = 0
    soft_done: int = 0  # 本地达标但平台未确认
    progress: dict[str, Any] = field(default_factory=dict)
    batch: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=800))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)
        try:
            print(line)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "gbk"
            print(str(line).encode(enc, errors="replace").decode(enc, errors="replace"))

    def request_cancel(self) -> bool:
        with self._lock:
            if not self.running:
                return False
            self.cancel_requested = True
            self.message = "正在停止…"
            return True

    def is_cancel_requested(self) -> bool:
        with self._lock:
            return self.cancel_requested

    def set_progress(self, info: dict[str, Any]) -> None:
        with self._lock:
            self.progress = dict(info or {})
            title = str(info.get("title") or "")[:36]
            pct = info.get("pct")
            eta = info.get("eta_text") or ""
            phase = info.get("phase") or ""
            if phase == "playing" and pct is not None:
                self.message = f"播放中 {pct}% 约{eta}达线" + (f" {title}" if title else "")
            elif phase == "opening":
                self.message = f"打开回放… {title}".strip()
            elif phase == "done":
                conf = info.get("platform_confirmed")
                tag = "平台已确认" if conf else "本地达标"
                self.message = f"本节{tag} {title}".strip()
            elif phase == "cancelled":
                self.message = "已取消"

    def clear_display_logs(self) -> None:
        """清空界面用日志缓冲（不删磁盘文件）。"""
        with self._lock:
            self.logs.clear()

    def snapshot(self, *, since: int = 0) -> dict[str, Any]:
        with self._lock:
            all_logs = list(self.logs)
            total = len(all_logs)
            since = max(0, min(int(since or 0), total))
            return {
                "running": self.running,
                "action": self.action,
                "message": self.message,
                "ok": self.ok,
                "pending_preview": list(self.pending_preview),
                "done": self.done,
                "fail": self.fail,
                "soft_done": self.soft_done,
                "progress": dict(self.progress),
                "batch": dict(self.batch),
                "cancel_requested": self.cancel_requested,
                "log_len": total,
                "log_since": since,
                "logs": all_logs[since:],
            }


STATE = JobState()
_worker: threading.Thread | None = None


def reconcile_progress_with_platform(
    page,
    classroom_id: str,
    progress: ProgressStore,
    *,
    origin: str,
    log: LogFn,
    soft: SoftStore | None = None,
) -> dict[str, int]:
    """用平台 live_viewed 对账本地断点与 SOFT 列表。"""
    added = 0
    removed = 0
    soft_promoted = 0
    try:
        items = fetch_all_activities(page, classroom_id, origin=origin, log=lambda *_: None)
    except Exception as e:
        log(f"[progress] 对账跳过: {e}")
        return {"added": 0, "removed": 0, "soft_promoted": 0}

    cid = str(classroom_id)
    by_id = {it.lesson_id: it for it in items}

    for lid, it in by_id.items():
        if it.live_viewed and not progress.is_lesson_done(cid, lid):
            progress.mark_done(
                progress_key(cid, lid),
                it.title,
                classroom_id=cid,
                lesson_id=lid,
            )
            added += 1
            if soft:
                soft.remove(cid, lid)

    for key in list(progress.completed):
        from yuketang.util import parse_progress_key

        c, lid = parse_progress_key(key)
        # 仅处理本课 namespaced，或旧裸键
        if c is not None and c != cid:
            continue
        if not lid:
            continue
        it = by_id.get(lid)
        if it is not None and not it.live_viewed:
            progress.unmark(key)
            removed += 1
            log(f"[progress] 剔除误断点（平台未看）: {it.title or key}")

    if soft:
        for s in list(soft.for_classroom(cid)):
            it = by_id.get(s.lesson_id)
            if it is not None and it.live_viewed:
                progress.mark_done(
                    s.key,
                    s.title or it.title,
                    classroom_id=cid,
                    lesson_id=s.lesson_id,
                )
                soft.remove(cid, s.lesson_id)
                soft_promoted += 1
                log(f"[progress] SOFT 转正: {s.title or s.lesson_id}")

    if added or removed or soft_promoted:
        log(
            f"[progress] 对账完成: 补写 {added}，剔除 {removed}，SOFT转正 {soft_promoted}"
        )
    return {"added": added, "removed": removed, "soft_promoted": soft_promoted}


def select_soft_targets(pending: list, soft: SoftStore, classroom_id: str) -> list:
    """pending ∩ soft.json（本课仍待平台确认的节）。"""
    soft_ids = {s.lesson_id for s in soft.for_classroom(str(classroom_id))}
    return [it for it in pending if it.lesson_id in soft_ids]


def load_pending_for_classroom(
    page,
    classroom_id: str,
    *,
    origin: str,
    progress: ProgressStore,
    soft: SoftStore | None = None,
    attend_filter: str = "all",
    log: LogFn | None = None,
    reconcile: bool = True,
    open_log_page: bool = True,
    wait_ms: int = 1200,
) -> list:
    """打开学习日志 →（可选）平台对账 → 返回待办列表。

    菜单与 run_automation 共用，保证 list 前断点/SOFT 语义一致。
    """
    log = log or print
    cid = str(classroom_id)
    if open_log_page:
        try:
            page.goto(
                f"{origin}/v2/web/studentLog/{cid}",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(int(wait_ms))
        except Exception as e:
            log(f"[job] 打开日志页警告: {e}")

    if reconcile:
        reconcile_progress_with_platform(
            page, cid, progress, origin=origin, log=log, soft=soft
        )

    return list_pending_replays(
        page,
        cid,
        progress_keys=progress.keys_for_lookup(cid),
        origin=origin,
        attend_filter=normalize_attend_filter(attend_filter),
        log=log,
    )


def enrich_duration_map(
    page,
    pending: list,
    *,
    origin: str,
    should_cancel: Callable[[], bool] | None = None,
    default_sec: float | None = None,
) -> dict[str, float]:
    """为待办串行拉时长；失败用 default。"""
    cancel_fn = should_cancel or (lambda: False)
    fallback = float(default_sec if default_sec is not None else _DEFAULT_LESSON_SEC)
    duration_map: dict[str, float] = {}
    for it in pending:
        if cancel_fn():
            break
        try:
            _segs, tot = replay_segment_count(page, it.lesson_id, origin=origin)
            duration_map[it.lesson_id] = tot if tot > 0 else fallback
        except Exception:
            duration_map[it.lesson_id] = fallback
        try:
            page.wait_for_timeout(80)
        except Exception:
            pass
    return duration_map


def normalize_job_action(action: str) -> tuple[str, str | None]:
    """返回 (归一化动作, 强制 attend_filter 或 None)。

    合法动作: list | once | all | selected | soft
    别名: soft_only / retry_soft → soft；*_absent → filter=absent
    """
    action = (action or "list").strip().lower()
    if action in ("soft_only", "retry_soft"):
        action = "soft"
    force_af: str | None = None
    if action.endswith("_absent"):
        force_af = "absent"
        action = action[: -len("_absent")] or "list"
    allowed = ("list", "once", "all", "selected", "soft")
    if action not in allowed:
        raise ValueError(f"未知动作: {action}")
    return action, force_af


def watch_lesson_batch(
    page,
    session: BrowserSession,
    *,
    classroom_id: str,
    origin: str,
    targets: list,
    rate: float,
    complete_ratio: float,
    max_watch: int,
    progress: ProgressStore,
    failed: FailedStore,
    soft: SoftStore,
    data_dir: Path,
    pause_lo: float = 2.0,
    pause_hi: float = 6.0,
    confirm_grace_sec: int = 120,
    soft_boost: float = 0.10,
    require_platform: bool = True,
    retry_per_lesson: int = 1,
    shot_on_err: bool = True,
    log: LogFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
    duration_map: dict[str, float] | None = None,
    update_state: bool = False,
) -> dict[str, Any]:
    """共享观看循环（菜单 / run_automation 共用）。

    返回 {done, fail, soft_done, cancelled}。
    仅 platform_confirmed 才 mark_done；本地达标未确认记 soft。
    """
    log = log or print
    cancel_fn = should_cancel or (lambda: False)
    done_count = 0
    fail_count = 0
    soft_count = 0
    cancelled = False
    if not targets:
        return {
            "done": 0,
            "fail": 0,
            "soft_done": 0,
            "cancelled": False,
        }

    dmap = duration_map or {}
    remain_content = [
        float(dmap.get(it.lesson_id, _DEFAULT_LESSON_SEC)) * complete_ratio
        for it in targets
    ]
    cid = str(classroom_id)
    attempts_max = 1 + max(0, int(retry_per_lesson))

    for idx, item in enumerate(targets, 1):
        if cancel_fn():
            log("[job] 用户取消，停止后续课程")
            cancelled = True
            break

        if not is_logged_in(page):
            log("[job] 登录态失效，请使用有界面模式重新登录")
            if shot_on_err:
                session.screenshot(data_dir / "session_expired.png")
            fail_count += 1
            break

        log("-" * 40)
        log(f"[job] ({idx}/{len(targets)}) {item.title}")
        if update_state:
            STATE.set_progress(
                {
                    "title": item.title,
                    "pct": 0.0,
                    "phase": "opening",
                    "index": idx,
                    "total": len(targets),
                }
            )
            STATE.batch = {
                **STATE.batch,
                "index": idx,
                "total": len(targets),
                "remaining": len(targets) - idx + 1,
                "current_title": item.title,
            }

        def _on_prog(
            info: dict[str, Any],
            _idx=idx,
            _n=len(targets),
            _remain=remain_content,
        ) -> None:
            if not update_state:
                return
            info = dict(info)
            info["index"] = _idx
            info["total"] = _n
            STATE.set_progress(info)
            eta_sec = int(info.get("eta_sec") or 0)
            rest_content = sum(_remain[_idx:])
            rest_wall = rest_content / max(rate, 0.5)
            with STATE._lock:
                STATE.batch = {
                    **STATE.batch,
                    "index": _idx,
                    "total": _n,
                    "remaining": max(0, _n - _idx)
                    + (1 if info.get("phase") in ("playing", "grace") else 0),
                    "section_eta_sec": eta_sec,
                    "batch_eta_sec": int(eta_sec + rest_wall),
                    "batch_eta_text": fmt_eta(eta_sec + rest_wall),
                }

        result: ReplayResult | None = None
        for attempt in range(1, attempts_max + 1):
            if cancel_fn():
                break
            if attempt > 1:
                log(f"[job] 重试本节 ({attempt}/{attempts_max})...")
                page.wait_for_timeout(1500)
            result = watch_replay(
                page,
                classroom_id=classroom_id,
                lesson_id=item.lesson_id,
                origin=origin,
                rate=rate,
                complete_ratio=complete_ratio,
                max_watch_sec=max_watch,
                log=log,
                on_progress=_on_prog if update_state else None,
                title=item.title,
                should_cancel=cancel_fn,
                confirm_grace_sec=confirm_grace_sec,
                soft_boost=soft_boost,
            )
            if result.cancelled or result.platform_confirmed or result.ok:
                break
        assert result is not None

        pkey = progress_key(cid, item.lesson_id)

        if result.cancelled:
            log("[job] 本节已取消")
            cancelled = True
            break

        if result.platform_confirmed:
            progress.mark_done(
                pkey,
                item.title,
                classroom_id=cid,
                lesson_id=item.lesson_id,
            )
            soft.remove(cid, item.lesson_id)
            done_count += 1
            session.save_state()
            log("[job] [OK] 平台已确认，已写入断点")
        elif result.ok:
            soft_count += 1
            session.save_state()
            if require_platform:
                soft.add(
                    classroom_id=cid,
                    lesson_id=item.lesson_id,
                    title=item.title,
                    local_ratio=result.local_ratio,
                )
                log(
                    f"[job] [SOFT] 本地 {result.local_ratio*100:.1f}% "
                    "平台未确认 — 未写断点，已记 soft 待对账"
                )
            else:
                progress.mark_done(
                    pkey,
                    item.title,
                    classroom_id=cid,
                    lesson_id=item.lesson_id,
                )
                done_count += 1
                soft_count -= 1
                log("[job] [OK] 本地达标已写断点（require_platform_confirm=false）")
        else:
            fail_count += 1
            failed.add(pkey, item.title, result.reason or "watch_replay failed")
            if shot_on_err:
                session.screenshot(data_dir / f"fail_replay_{item.lesson_id}.png")
            log(f"[job] [FAIL] 本节失败 ({result.reason})")

        if idx - 1 < len(remain_content):
            remain_content[idx - 1] = 0.0

        if update_state:
            STATE.done = done_count
            STATE.fail = fail_count
            STATE.soft_done = soft_count

        if idx < len(targets) and not cancel_fn():
            delay = random.uniform(pause_lo, pause_hi)
            log(f"[job] 休息 {delay:.1f}s")
            end_sleep = time.time() + delay
            while time.time() < end_sleep:
                if cancel_fn():
                    cancelled = True
                    break
                time.sleep(0.3)
            if cancelled:
                break

    return {
        "done": done_count,
        "fail": fail_count,
        "soft_done": soft_count,
        "cancelled": cancelled or cancel_fn(),
    }


def run_automation(
    *,
    root: Path,
    cfg: dict[str, Any],
    action: str,
    log: LogFn | None = None,
    attend_filter: str | None = None,
    lesson_ids: list[str] | None = None,
) -> dict[str, Any]:
    """同步执行。action: list | once | all | selected | soft。"""
    log = log or print
    try:
        action, force_af = normalize_job_action(action)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if force_af is not None:
        attend_filter = force_af

    af = normalize_attend_filter(
        attend_filter if attend_filter is not None else cfg.get("attend_filter", "all")
    )

    if not has_classroom(cfg):
        return {"ok": False, "error": "请先填写 classroom_id 或学习日志 URL"}

    rate = resolve_playback_rate(cli_rate=None, cfg=cfg, log=log)
    course_url, classroom_id, url_candidates = resolve_runtime(cfg)
    if not classroom_id:
        return {"ok": False, "error": "无法解析 classroom_id"}

    headless = bool(cfg.get("headless", False))
    storage = resolve_path(root, cfg.get("storage_state", "data/storage_state.json"))
    progress_path = resolve_path(root, cfg.get("progress_file", "data/progress.json"))
    failed_path = resolve_path(root, cfg.get("failed_file", "data/failed.json"))
    soft_path = resolve_path(root, cfg.get("soft_file", "data/soft.json"))
    wait_login = int(cfg.get("wait_login_timeout_sec", 180))
    max_watch = int(cfg.get("max_watch_sec", 7200))
    complete_ratio = float(cfg.get("complete_ratio", 0.65))
    require_platform = bool(cfg.get("require_platform_confirm", True))
    confirm_grace_sec = int(cfg.get("confirm_grace_sec", 120))
    soft_boost = float(cfg.get("soft_boost", 0.10))
    retry_per_lesson = max(0, int(cfg.get("retry_per_lesson", 1)))
    shot_on_err = bool(cfg.get("screenshot_on_error", True))
    pause_cfg = cfg.get("pause_between_sec", [2, 6])
    if isinstance(pause_cfg, (list, tuple)) and len(pause_cfg) >= 2:
        pause_lo, pause_hi = float(pause_cfg[0]), float(pause_cfg[1])
    else:
        pause_lo, pause_hi = 2.0, 6.0

    progress = ProgressStore.load(progress_path, classroom_id=str(classroom_id))
    failed = FailedStore(failed_path)
    soft = SoftStore(soft_path)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    migrated = progress.migrate_to_namespaced(str(classroom_id))
    if migrated:
        log(f"[progress] 已迁移 {migrated} 条旧断点为课堂隔离键")

    done_count = 0
    fail_count = 0
    soft_count = 0
    pending_preview: list[dict[str, Any]] = []
    batch_result: dict[str, Any] = {}
    targets: list = []
    af_label = {"all": "不限签到", "absent": "仅缺勤", "present": "仅已签到"}[af]

    log(
        f"[job] action={action} filter={af_label} classroom={classroom_id} "
        f"rate={rate}x headless={headless} require_platform={require_platform}"
    )

    with BrowserSession(headless=headless, storage_state=storage) as session:
        page = session.page
        assert page is not None
        ok_login, course_url = ensure_login(
            page,
            course_url=course_url,
            timeout_sec=wait_login,
            log=log,
            candidate_urls=url_candidates,
        )
        if not ok_login:
            if shot_on_err:
                session.screenshot(data_dir / "login_timeout.png")
            return {"ok": False, "error": "登录超时", "done": 0, "fail": 0}

        session.save_state()
        origin = origin_of(page.url or course_url)

        resolved, _rooms, resolve_msg = resolve_joined_classroom(
            page, str(classroom_id), log=log
        )
        if not resolved:
            return {
                "ok": False,
                "error": resolve_msg or "无法解析 classroom_id",
                "done": 0,
                "fail": 0,
            }
        if resolved != str(classroom_id):
            log(f"[job] classroom_id: {classroom_id} -> {resolved}")
            classroom_id = resolved
            cfg["classroom_id"] = resolved
            cfg["course_url"] = f"{origin}/v2/web/studentLog/{resolved}"
            try:
                save_settings(root / "config.yaml", cfg)
                log("[job] 已写回正确 classroom_id 到 config.yaml")
            except Exception as e:
                log(f"[job] 写回配置失败（可忽略）: {e}")
        else:
            log(f"[job] {resolve_msg}")

        try:
            pending = load_pending_for_classroom(
                page,
                str(classroom_id),
                origin=origin,
                progress=progress,
                soft=soft,
                attend_filter=af,
                log=log,
                reconcile=True,
            )
        except LogsApiError as e:
            return {
                "ok": False,
                "error": str(e),
                "done": 0,
                "fail": 0,
                "classroom_id": classroom_id,
            }

        soft_ids = {s.lesson_id for s in soft.for_classroom(str(classroom_id))}
        duration_map = enrich_duration_map(
            page,
            pending,
            origin=origin,
            should_cancel=STATE.is_cancel_requested,
        )

        pending_preview = [
            {
                "title": it.title,
                "lesson_id": it.lesson_id,
                "attend": bool(it.attend_status),
                "soft": it.lesson_id in soft_ids,
                "duration_sec": int(duration_map.get(it.lesson_id, _DEFAULT_LESSON_SEC)),
                "duration_min": round(
                    duration_map.get(it.lesson_id, _DEFAULT_LESSON_SEC) / 60, 1
                ),
            }
            for it in pending
        ]
        total_content = sum(duration_map.get(it.lesson_id, _DEFAULT_LESSON_SEC) for it in pending)
        est_wall_all = (total_content * complete_ratio) / max(rate, 0.5)
        log(f"[job] 待观看 {len(pending)} 节（{af_label}）")
        if pending:
            log(
                f"[job] 内容合计约 {total_content/60:.0f} 分钟，"
                f"按 {complete_ratio*100:.0f}% / {rate}x 墙钟约 {fmt_eta(est_wall_all)}"
            )
        for i, it in enumerate(pending, 1):
            tag = "缺勤" if not it.attend_status else "已签到"
            soft_tag = " SOFT" if it.lesson_id in soft_ids else ""
            dm = duration_map.get(it.lesson_id, _DEFAULT_LESSON_SEC) / 60
            log(f"  {i}. [{tag}{soft_tag}] {it.title} (~{dm:.0f}分)")

        STATE.pending_preview = pending_preview
        STATE.batch = {
            "total": len(pending),
            "remaining": len(pending),
            "est_wall_sec": int(est_wall_all),
            "est_wall_text": fmt_eta(est_wall_all),
            "total_content_sec": int(total_content),
            "rate": rate,
            "complete_ratio": complete_ratio,
        }

        if action == "list" or not pending:
            session.save_state()
            return {
                "ok": True,
                "done": 0,
                "fail": 0,
                "soft_done": 0,
                "pending": pending_preview,
                "classroom_id": classroom_id,
                "attend_filter": af,
                "est_wall_sec": int(est_wall_all),
                "est_wall_text": fmt_eta(est_wall_all),
                "message": (
                    "无待办"
                    if not pending
                    else f"共 {len(pending)} 节待观看（{af_label}），约 {fmt_eta(est_wall_all)}"
                ),
            }

        if STATE.is_cancel_requested():
            return {
                "ok": False,
                "done": 0,
                "fail": 0,
                "pending": pending_preview,
                "message": "已取消",
                "cancelled": True,
            }

        if action == "once":
            targets = pending[:1]
        elif action == "soft":
            targets = select_soft_targets(pending, soft, str(classroom_id))
            if not targets:
                return {
                    "ok": True,
                    "done": 0,
                    "fail": 0,
                    "soft_done": 0,
                    "pending": pending_preview,
                    "classroom_id": classroom_id,
                    "message": "无 SOFT 待重试（或已全部转正）",
                    "finished": True,
                }
            log(f"[job] 仅 SOFT 重试：{len(targets)} 节")
        elif action == "selected":
            want = {str(x) for x in (lesson_ids or [])}
            if not want:
                return {
                    "ok": False,
                    "error": "selected 需要 lesson_ids",
                    "pending": pending_preview,
                }
            targets = [it for it in pending if it.lesson_id in want]
            if not targets:
                return {
                    "ok": False,
                    "error": "所选课程均不在待办中",
                    "pending": pending_preview,
                }
        else:
            targets = list(pending)

        # 目标子集墙钟
        est_wall = sum(
            (duration_map.get(it.lesson_id, _DEFAULT_LESSON_SEC) * complete_ratio)
            / max(rate, 0.5)
            for it in targets
        )
        log(
            f"[job] 将处理 {len(targets)} 节；估总墙钟约 {fmt_eta(est_wall)} "
            f"(时长API + {complete_ratio*100:.0f}% / {rate}x)"
        )
        STATE.batch = {
            "total": len(targets),
            "remaining": len(targets),
            "est_wall_sec": int(est_wall),
            "est_wall_text": fmt_eta(est_wall),
            "rate": rate,
            "complete_ratio": complete_ratio,
        }

        batch_result = watch_lesson_batch(
            page,
            session,
            classroom_id=str(classroom_id),
            origin=origin,
            targets=targets,
            rate=rate,
            complete_ratio=complete_ratio,
            max_watch=max_watch,
            progress=progress,
            failed=failed,
            soft=soft,
            data_dir=data_dir,
            pause_lo=pause_lo,
            pause_hi=pause_hi,
            confirm_grace_sec=confirm_grace_sec,
            soft_boost=soft_boost,
            require_platform=require_platform,
            retry_per_lesson=retry_per_lesson,
            shot_on_err=shot_on_err,
            log=log,
            should_cancel=STATE.is_cancel_requested,
            duration_map=duration_map,
            update_state=True,
        )
        done_count = int(batch_result.get("done") or 0)
        fail_count = int(batch_result.get("fail") or 0)
        soft_count = int(batch_result.get("soft_done") or 0)

        # 结束再对账一次（非播放态，可导航）
        try:
            page.goto(
                f"{origin}/v2/web/studentLog/{classroom_id}",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(800)
            reconcile_progress_with_platform(
                page, classroom_id, progress, origin=origin, log=log, soft=soft
            )
        except Exception as e:
            log(f"[job] 结束对账跳过: {e}")

        session.save_state()

    cancelled = bool(batch_result.get("cancelled")) or STATE.is_cancel_requested()
    msg_parts = [
        f"成功(平台确认) {done_count}",
        f"本地未确认 {soft_count}",
        f"失败 {fail_count}",
    ]
    if cancelled:
        msg_parts.append("已取消")
    # list 仅刷新不写历史
    if action != "list":
        try:
            append_run_history(
                root,
                {
                    "action": action,
                    "attend_filter": af,
                    "done": done_count,
                    "soft": soft_count,
                    "fail": fail_count,
                    "cancelled": cancelled,
                    "classroom_id": str(classroom_id),
                },
            )
        except OSError:
            pass
    return {
        "ok": fail_count == 0 and not cancelled,
        "done": done_count,
        "fail": fail_count,
        "soft_done": soft_count,
        "pending": pending_preview,
        "classroom_id": classroom_id,
        "cancelled": cancelled,
        "message": ", ".join(msg_parts),
        "finished": action != "list",
    }


def start_job_async(
    *,
    root: Path,
    cfg: dict[str, Any],
    action: str,
    attend_filter: str | None = None,
    lesson_ids: list[str] | None = None,
) -> tuple[bool, str]:
    """在后台线程启动任务。若已有任务在跑则拒绝。"""
    global _worker
    if STATE.running:
        return False, "已有任务在运行，请等待结束或先停止"

    af = normalize_attend_filter(
        attend_filter if attend_filter is not None else cfg.get("attend_filter", "all")
    )
    label = {"all": "不限签到", "absent": "仅缺勤", "present": "仅已签到"}[af]

    def _run() -> None:
        with STATE._lock:
            STATE.running = True
            STATE.action = f"{action}/{af}"
            STATE.message = f"运行中…（{label}）"
            STATE.ok = None
            STATE.done = 0
            STATE.fail = 0
            STATE.soft_done = 0
            STATE.pending_preview = []
            STATE.progress = {}
            STATE.batch = {}
            STATE.cancel_requested = False
        try:
            result = run_automation(
                root=root,
                cfg=cfg,
                action=action,
                attend_filter=af,
                lesson_ids=lesson_ids,
                log=STATE.log,
            )
            with STATE._lock:
                STATE.ok = bool(result.get("ok"))
                STATE.done = int(result.get("done") or 0)
                STATE.fail = int(result.get("fail") or 0)
                STATE.soft_done = int(result.get("soft_done") or 0)
                STATE.pending_preview = list(result.get("pending") or [])
                STATE.message = str(result.get("message") or result.get("error") or "完成")
                if result.get("finished"):
                    STATE.progress = {
                        **STATE.progress,
                        "phase": "batch_done",
                        "platform_confirmed": STATE.done,
                        "soft": STATE.soft_done,
                        "fail": STATE.fail,
                    }
            if result.get("error"):
                STATE.log(f"[job] 错误: {result['error']}")
                with STATE._lock:
                    STATE.ok = False
        except Exception as e:
            with STATE._lock:
                STATE.ok = False
                STATE.message = f"异常: {e}"
            STATE.log(traceback.format_exc())
        finally:
            with STATE._lock:
                STATE.running = False
                STATE.cancel_requested = False

    _worker = threading.Thread(target=_run, name="yuketang-job", daemon=True)
    _worker.start()
    return True, f"任务已启动（{label}）"


def clear_progress_store(root: Path, cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or {}
    path = resolve_path(root, cfg.get("progress_file", "data/progress.json"))
    store = ProgressStore.load(path)
    return store.clear()


def clear_failed_store(root: Path, cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or {}
    path = resolve_path(root, cfg.get("failed_file", "data/failed.json"))
    store = FailedStore(path)
    return store.clear()
