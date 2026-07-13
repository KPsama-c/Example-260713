"""待办加载、平台对账、SOFT 筛选、时长估算（菜单 / jobs 共用）。"""

from __future__ import annotations

from typing import Any, Callable

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
    partial: "PartialStore | None" = None,
) -> dict[str, int]:
    """用平台 live_viewed 对账本地断点与 SOFT 列表。"""
    from yuketang.progress import PartialStore  # noqa: F401 — typing/runtime optional

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
            if partial is not None:
                partial.remove(cid, lid)

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
                if partial is not None:
                    partial.remove(cid, s.lesson_id)
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


def soft_local_ratio_map(soft: SoftStore | None, classroom_id: str) -> dict[str, float]:
    """仅 SOFT 记录：lesson_id -> local_ratio（本地已完整跑到停线的证据）。"""
    out: dict[str, float] = {}
    if soft is None:
        return out
    for s in soft.for_classroom(str(classroom_id)):
        out[s.lesson_id] = max(out.get(s.lesson_id, 0.0), float(s.local_ratio or 0))
    return out


def local_complete_ratio_map(
    soft: SoftStore | None,
    classroom_id: str,
    *,
    partial_ratios: dict[str, float] | None = None,
) -> dict[str, float]:
    """lesson_id -> 本地已知最高比例（soft ∪ partial，仅展示/ETA 用）。"""
    out = soft_local_ratio_map(soft, classroom_id)
    for lid, r in (partial_ratios or {}).items():
        out[str(lid)] = max(out.get(str(lid), 0.0), float(r or 0))
    return out


def filter_skip_local_complete(
    pending: list,
    *,
    classroom_id: str,
    complete_ratio: float,
    soft: SoftStore | None = None,
    partial_ratios: dict[str, float] | None = None,
    enabled: bool = True,
) -> tuple[list, list[tuple[Any, float]]]:
    """「全部」用：仅跳过「本地已明确达线」的节。

    判定标准（确定才跳过）：
    - soft.json 中该节 local_ratio ≥ complete_ratio
      （表示本机已真实播完 0→阈值，只是平台未确认）

    不因 partial 跳过：中断进度再高也只用于续播；无 SOFT 则视为不确定 → 重看/续看。
    partial_ratios 参数保留兼容，不参与跳过判定。

    返回 (保留列表, [(item, soft_ratio), ...跳过])。
    soft 动作 / 勾选观看 不要走此过滤。
    """
    del partial_ratios  # 兼容旧调用；跳过判定不用 partial
    if not enabled or not pending:
        return list(pending), []
    thr = max(0.0, min(float(complete_ratio), 1.0))
    soft_ratios = soft_local_ratio_map(soft, classroom_id)
    kept: list = []
    skipped: list[tuple[Any, float]] = []
    for it in pending:
        lid = str(getattr(it, "lesson_id", "") or "")
        r = float(soft_ratios.get(lid, 0.0))
        # 仅 SOFT 且明确 ≥ 阈值才跳过；无记录或不足 → 保留（续看/重看）
        if lid in soft_ratios and r + 1e-9 >= thr:
            skipped.append((it, r))
        else:
            kept.append(it)
    return kept, skipped


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
