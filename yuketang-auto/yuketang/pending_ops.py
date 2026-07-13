"""待办加载、平台对账、SOFT 筛选、时长估算（菜单 / jobs 共用）。"""

from __future__ import annotations

from typing import Callable

from yuketang.job_state import LogFn
from yuketang.logs import (
    fetch_all_activities,
    list_pending_replays,
    normalize_attend_filter,
    replay_segment_count,
)
from yuketang.progress import ProgressStore, SoftStore
from yuketang.util import progress_key

DEFAULT_LESSON_SEC = 60 * 60  # 无时长 API 时的默认估算


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
    fallback = float(default_sec if default_sec is not None else DEFAULT_LESSON_SEC)
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
