"""共享观看循环（菜单 / run_automation 共用）。"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Callable

from yuketang.browser import BrowserSession
from yuketang.job_state import STATE, LogFn
from yuketang.login import is_logged_in
from yuketang.pending_ops import DEFAULT_LESSON_SEC
from yuketang.progress import FailedStore, PartialStore, ProgressStore, SoftStore
from yuketang.replay import ReplayResult, watch_replay
from yuketang.util import fmt_eta, progress_key


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
    partial: PartialStore | None = None,
    resume_partial: bool = True,
) -> dict[str, Any]:
    """共享观看循环。

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
        float(dmap.get(it.lesson_id, DEFAULT_LESSON_SEC)) * complete_ratio
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
                partial=partial,
                resume_partial=resume_partial,
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
            if partial is not None:
                partial.remove(cid, item.lesson_id)
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
                if partial is not None:
                    partial.remove(cid, item.lesson_id)
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
                if partial is not None:
                    partial.remove(cid, item.lesson_id)
                done_count += 1
                soft_count -= 1
                log("[job] [OK] 本地达标已写断点（require_platform_confirm=false）")
        else:
            fail_count += 1
            failed.add(pkey, item.title, result.reason or "watch_replay failed")
            if shot_on_err:
                session.screenshot(data_dir / f"fail_replay_{item.lesson_id}.png")
            log(f"[job] [FAIL] 本节失败 ({result.reason})")
            if result.cancelled and result.local_ratio >= 0.02:
                log(
                    f"[job] 已保存中断进度约 {result.local_ratio*100:.1f}% "
                    "（下次可续播）"
                )

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
