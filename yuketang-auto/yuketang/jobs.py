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
from urllib.parse import urlparse

from yuketang.browser import BrowserSession
from yuketang.classrooms import resolve_classroom_id as resolve_joined_classroom
from yuketang.login import ensure_login
from yuketang.logs import (
    LogsApiError,
    fetch_all_activities,
    list_pending_replays,
    normalize_attend_filter,
)
from yuketang.progress import FailedStore, ProgressStore
from yuketang.rate import resolve_playback_rate
from yuketang.replay import ReplayResult, watch_replay
from yuketang.settings import has_classroom, resolve_runtime, save_settings

LogFn = Callable[[str], None]


def _origin_of(url: str) -> str:
    p = urlparse(url)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return "https://www.yuketang.cn"


def _resolve_path(base: Path, p: str | Path) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _fmt_eta(sec: float) -> str:
    if sec <= 0 or sec > 48 * 3600:
        return "-"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s:02d}秒"
    return f"{s}秒"


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
) -> dict[str, int]:
    """用平台 live_viewed 对账本地断点。"""
    added = 0
    removed = 0
    try:
        items = fetch_all_activities(page, classroom_id, origin=origin, log=lambda *_: None)
    except Exception as e:
        log(f"[progress] 对账跳过: {e}")
        return {"added": 0, "removed": 0}

    by_id = {it.lesson_id: it for it in items}
    # 平台已看 -> 补写
    for lid, it in by_id.items():
        if it.live_viewed and lid not in progress.completed:
            progress.mark_done(lid, it.title)
            added += 1
    # 本地有、平台未看 -> 剔除（修复历史误 mark）
    for key in list(progress.completed):
        it = by_id.get(key)
        if it is not None and not it.live_viewed:
            progress.unmark(key)
            removed += 1
            log(f"[progress] 剔除误断点（平台未看）: {it.title or key}")
    if added or removed:
        log(f"[progress] 对账完成: 补写 {added}，剔除 {removed}")
    return {"added": added, "removed": removed}


def run_automation(
    *,
    root: Path,
    cfg: dict[str, Any],
    action: str,
    log: LogFn | None = None,
    attend_filter: str | None = None,
) -> dict[str, Any]:
    """同步执行。action: list | once | all。"""
    log = log or print
    action = (action or "list").strip().lower()
    if action not in ("list", "once", "all", "all_absent", "list_absent", "once_absent"):
        return {"ok": False, "error": f"未知动作: {action}"}

    if action.endswith("_absent"):
        attend_filter = "absent"
        action = action.replace("_absent", "") or "list"
    if action not in ("list", "once", "all"):
        return {"ok": False, "error": f"未知动作: {action}"}

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
    storage = _resolve_path(root, cfg.get("storage_state", "data/storage_state.json"))
    progress_path = _resolve_path(root, cfg.get("progress_file", "data/progress.json"))
    failed_path = _resolve_path(root, cfg.get("failed_file", "data/failed.json"))
    wait_login = int(cfg.get("wait_login_timeout_sec", 180))
    max_watch = int(cfg.get("max_watch_sec", 7200))
    complete_ratio = float(cfg.get("complete_ratio", 0.65))
    # 默认仅平台确认才写断点
    require_platform = bool(cfg.get("require_platform_confirm", True))
    shot_on_err = bool(cfg.get("screenshot_on_error", True))
    pause_cfg = cfg.get("pause_between_sec", [2, 6])
    if isinstance(pause_cfg, (list, tuple)) and len(pause_cfg) >= 2:
        pause_lo, pause_hi = float(pause_cfg[0]), float(pause_cfg[1])
    else:
        pause_lo, pause_hi = 2.0, 6.0

    progress = ProgressStore.load(progress_path)
    failed = FailedStore(failed_path)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    done_count = 0
    fail_count = 0
    soft_count = 0
    pending_preview: list[dict[str, Any]] = []
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
        origin = _origin_of(page.url or course_url)

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
            page.goto(
                f"{origin}/v2/web/studentLog/{classroom_id}",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(1200)
        except Exception as e:
            log(f"[job] 打开日志页警告: {e}")

        # 对账断点
        reconcile_progress_with_platform(
            page, classroom_id, progress, origin=origin, log=log
        )

        try:
            pending = list_pending_replays(
                page,
                classroom_id,
                progress_keys=set(progress.completed),
                origin=origin,
                attend_filter=af,
                log=log,
            )
        except LogsApiError as e:
            return {
                "ok": False,
                "error": str(e),
                "done": 0,
                "fail": 0,
                "classroom_id": classroom_id,
            }

        pending_preview = [
            {
                "title": it.title,
                "lesson_id": it.lesson_id,
                "attend": bool(it.attend_status),
            }
            for it in pending
        ]
        log(f"[job] 待观看 {len(pending)} 节（{af_label}）")
        for i, it in enumerate(pending, 1):
            tag = "缺勤" if not it.attend_status else "已签到"
            log(f"  {i}. [{tag}] {it.title}")

        STATE.pending_preview = pending_preview

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
                "message": (
                    "无待办"
                    if not pending
                    else f"共 {len(pending)} 节待观看（{af_label}）"
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

        limit = 1 if action == "once" else len(pending)
        targets = pending[:limit]

        # 粗估总墙钟（无精确时长时按 60 分钟/节）
        est_wall = 0.0
        for it in targets:
            # 无 per-item duration 时用默认
            est_wall += (60 * 60 * complete_ratio) / max(rate, 0.5)
        log(
            f"[job] 将处理 {len(targets)} 节；粗估总墙钟约 {_fmt_eta(est_wall)} "
            f"(按约60分钟/节 x {complete_ratio*100:.0f}% / {rate}x，仅参考)"
        )
        STATE.batch = {
            "total": len(targets),
            "remaining": len(targets),
            "est_wall_sec": int(est_wall),
            "est_wall_text": _fmt_eta(est_wall),
            "rate": rate,
            "complete_ratio": complete_ratio,
        }

        for idx, item in enumerate(targets, 1):
            if STATE.is_cancel_requested():
                log("[job] 用户取消，停止后续课程")
                break

            log("-" * 40)
            log(f"[job] ({idx}/{len(targets)}) {item.title}")
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

            def _on_prog(info: dict[str, Any], _idx=idx, _n=len(targets)) -> None:
                info = dict(info)
                info["index"] = _idx
                info["total"] = _n
                STATE.set_progress(info)
                # 更新批量剩余 ETA：本节 eta + 后面节粗估
                eta_sec = int(info.get("eta_sec") or 0)
                remain_after = max(0, _n - _idx)
                rest = remain_after * (60 * 60 * complete_ratio) / max(rate, 0.5)
                with STATE._lock:
                    STATE.batch = {
                        **STATE.batch,
                        "index": _idx,
                        "total": _n,
                        "remaining": remain_after + (1 if info.get("phase") == "playing" else 0),
                        "section_eta_sec": eta_sec,
                        "batch_eta_sec": int(eta_sec + rest),
                        "batch_eta_text": _fmt_eta(eta_sec + rest),
                    }

            result: ReplayResult = watch_replay(
                page,
                classroom_id=classroom_id,
                lesson_id=item.lesson_id,
                origin=origin,
                rate=rate,
                complete_ratio=complete_ratio,
                max_watch_sec=max_watch,
                log=log,
                on_progress=_on_prog,
                title=item.title,
                should_cancel=STATE.is_cancel_requested,
            )

            if result.cancelled:
                log("[job] 本节已取消")
                fail_count += 1
                break

            if result.platform_confirmed:
                progress.mark_done(item.key, item.title)
                done_count += 1
                session.save_state()
                log("[job] [OK] 平台已确认，已写入断点")
            elif result.ok:
                soft_count += 1
                session.save_state()
                if require_platform:
                    log(
                        f"[job] [SOFT] 本地进度 {result.local_ratio*100:.1f}% "
                        "但平台未确认 — 未写断点"
                    )
                else:
                    progress.mark_done(item.key, item.title)
                    done_count += 1
                    soft_count -= 1
                    log("[job] [OK] 本地达标已写断点（require_platform_confirm=false）")
            else:
                fail_count += 1
                failed.add(item.key, item.title, result.reason or "watch_replay failed")
                if shot_on_err:
                    session.screenshot(data_dir / f"fail_replay_{item.lesson_id}.png")
                log(f"[job] [FAIL] 本节失败 ({result.reason})")

            STATE.done = done_count
            STATE.fail = fail_count
            STATE.soft_done = soft_count

            if idx < len(targets) and not STATE.is_cancel_requested():
                delay = random.uniform(pause_lo, pause_hi)
                log(f"[job] 休息 {delay:.1f}s")
                # 可中断休息
                end_sleep = time.time() + delay
                while time.time() < end_sleep:
                    if STATE.is_cancel_requested():
                        break
                    time.sleep(0.3)

        session.save_state()

    cancelled = STATE.is_cancel_requested()
    msg_parts = [f"成功(平台确认) {done_count}", f"本地未确认 {soft_count}", f"失败 {fail_count}"]
    if cancelled:
        msg_parts.append("已取消")
    return {
        "ok": fail_count == 0 and not cancelled,
        "done": done_count,
        "fail": fail_count,
        "soft_done": soft_count,
        "pending": pending_preview,
        "classroom_id": classroom_id,
        "cancelled": cancelled,
        "message": ", ".join(msg_parts),
    }


def start_job_async(
    *,
    root: Path,
    cfg: dict[str, Any],
    action: str,
    attend_filter: str | None = None,
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
                log=STATE.log,
            )
            with STATE._lock:
                STATE.ok = bool(result.get("ok"))
                STATE.done = int(result.get("done") or 0)
                STATE.fail = int(result.get("fail") or 0)
                STATE.soft_done = int(result.get("soft_done") or 0)
                STATE.pending_preview = list(result.get("pending") or [])
                STATE.message = str(result.get("message") or result.get("error") or "完成")
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
    path = _resolve_path(root, cfg.get("progress_file", "data/progress.json"))
    store = ProgressStore.load(path)
    return store.clear()


def clear_failed_store(root: Path, cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or {}
    path = _resolve_path(root, cfg.get("failed_file", "data/failed.json"))
    store = FailedStore(path)
    return store.clear()
