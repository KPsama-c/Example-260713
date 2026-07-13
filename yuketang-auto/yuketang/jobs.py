"""后台任务编排：列表 / 观看 / 异步（供 CLI 与 Web UI 共用）。

实现已拆到：
- job_state：JobState / STATE
- pending_ops：对账 / 待办 / soft / 时长 / action 归一
- watch_batch：共享观看循环

本模块 re-export 以保持 import 路径稳定。
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

from yuketang.browser import BrowserSession
from yuketang.classrooms import resolve_classroom_id as resolve_joined_classroom
from yuketang.history import append_run_history
from yuketang.job_state import STATE, JobState, LogFn
from yuketang.login import ensure_login
from yuketang.logs import LogsApiError, normalize_attend_filter
from yuketang.pending_ops import (
    DEFAULT_LESSON_SEC,
    enrich_duration_map,
    filter_skip_local_complete,
    load_pending_for_classroom,
    normalize_job_action,
    reconcile_progress_with_platform,
    select_soft_targets,
)
from yuketang.progress import FailedStore, PartialStore, ProgressStore, SoftStore
from yuketang.rate import resolve_playback_rate
from yuketang.settings import has_classroom, resolve_runtime, save_settings
from yuketang.util import fmt_eta, origin_of, resolve_path
from yuketang.watch_batch import watch_lesson_batch

# 兼容旧名
_DEFAULT_LESSON_SEC = DEFAULT_LESSON_SEC

_worker: threading.Thread | None = None

__all__ = [
    "STATE",
    "JobState",
    "LogFn",
    "clear_failed_store",
    "clear_progress_store",
    "enrich_duration_map",
    "filter_skip_local_complete",
    "load_pending_for_classroom",
    "normalize_job_action",
    "reconcile_progress_with_platform",
    "run_automation",
    "select_soft_targets",
    "start_job_async",
    "watch_lesson_batch",
]


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
    partial_path = resolve_path(root, cfg.get("partial_file", "data/partial.json"))
    wait_login = int(cfg.get("wait_login_timeout_sec", 180))
    max_watch = int(cfg.get("max_watch_sec", 7200))
    complete_ratio = float(cfg.get("complete_ratio", 0.65))
    require_platform = bool(cfg.get("require_platform_confirm", True))
    confirm_grace_sec = int(cfg.get("confirm_grace_sec", 120))
    soft_boost = float(cfg.get("soft_boost", 0.10))
    retry_per_lesson = max(0, int(cfg.get("retry_per_lesson", 1)))
    shot_on_err = bool(cfg.get("screenshot_on_error", True))
    skip_local_on_all = bool(cfg.get("skip_local_complete_on_all", True))
    resume_partial = bool(cfg.get("resume_partial", True))
    pause_cfg = cfg.get("pause_between_sec", [2, 6])
    if isinstance(pause_cfg, (list, tuple)) and len(pause_cfg) >= 2:
        pause_lo, pause_hi = float(pause_cfg[0]), float(pause_cfg[1])
    else:
        pause_lo, pause_hi = 2.0, 6.0

    progress = ProgressStore.load(progress_path, classroom_id=str(classroom_id))
    failed = FailedStore(failed_path)
    soft = SoftStore(soft_path)
    partial = PartialStore(partial_path)
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
        partial_map = partial.local_ratio_map(str(classroom_id))
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
                "partial_pct": round(float(partial_map.get(it.lesson_id, 0.0)) * 100, 1),
                "duration_sec": int(duration_map.get(it.lesson_id, DEFAULT_LESSON_SEC)),
                "duration_min": round(
                    duration_map.get(it.lesson_id, DEFAULT_LESSON_SEC) / 60, 1
                ),
            }
            for it in pending
        ]
        total_content = sum(
            duration_map.get(it.lesson_id, DEFAULT_LESSON_SEC) for it in pending
        )
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
            pp = float(partial_map.get(it.lesson_id, 0.0))
            part_tag = f" 续{pp*100:.0f}%" if pp >= 0.02 and it.lesson_id not in soft_ids else ""
            dm = duration_map.get(it.lesson_id, DEFAULT_LESSON_SEC) / 60
            log(f"  {i}. [{tag}{soft_tag}{part_tag}] {it.title} (~{dm:.0f}分)")

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
            # all：默认跳过本地已达 complete_ratio（SOFT / partial）
            targets, skipped_local = filter_skip_local_complete(
                pending,
                classroom_id=str(classroom_id),
                complete_ratio=complete_ratio,
                soft=soft,
                partial_ratios=partial_map,
                enabled=skip_local_on_all,
            )
            if skipped_local:
                log(
                    f"[job] 全部：跳过本地已达 ≥{complete_ratio*100:.0f}% 的 "
                    f"{len(skipped_local)} 节（可用「仅 SOFT 再跑」补刷平台）"
                )
                for it, r in skipped_local[:8]:
                    log(f"  - skip {getattr(it, 'title', it.lesson_id)} ({r*100:.0f}%)")
                if len(skipped_local) > 8:
                    log(f"  - …另 {len(skipped_local) - 8} 节")
            if not targets and skipped_local:
                return {
                    "ok": True,
                    "done": 0,
                    "fail": 0,
                    "soft_done": 0,
                    "pending": pending_preview,
                    "classroom_id": classroom_id,
                    "message": (
                        f"待办均已本地达 ≥{complete_ratio*100:.0f}%，已跳过 "
                        f"{len(skipped_local)} 节；可用「仅 SOFT 再跑」"
                    ),
                    "finished": True,
                    "skipped_local": len(skipped_local),
                }

        est_wall = sum(
            (duration_map.get(it.lesson_id, DEFAULT_LESSON_SEC) * complete_ratio)
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
            partial=partial,
            resume_partial=resume_partial,
        )
        done_count = int(batch_result.get("done") or 0)
        fail_count = int(batch_result.get("fail") or 0)
        soft_count = int(batch_result.get("soft_done") or 0)

        try:
            page.goto(
                f"{origin}/v2/web/studentLog/{classroom_id}",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(800)
            reconcile_progress_with_platform(
                page,
                classroom_id,
                progress,
                origin=origin,
                log=log,
                soft=soft,
                partial=partial,
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
