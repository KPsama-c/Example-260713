"""直播回放播放：overview 页静音 + 自定义倍速播多段。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Locator, Page

from yuketang import selectors as S
from yuketang.capabilities import (
    PlaybackCapabilities,
    compute_tail_seek_time,
    may_tail_seek,
)
from yuketang.logs import (
    basic_info_finish_replay,
    is_live_viewed,
    page_shows_replay_done,
    platform_replay_confirmed,
    replay_segment_count,
)
from yuketang.player import dismiss_popups
from yuketang.progress import PartialStore
from yuketang.rate import clamp_rate
from yuketang.urls import lesson_overview_url


@dataclass
class ReplayResult:
    """单节回放结果。

    ok: 播放流程是否正常结束（含已达本地阈值、平台确认、取消）
    platform_confirmed: 平台是否确认「已观看回放」——仅此时应写入断点
    local_ratio: 本地估算观看比例
    cancelled: 用户取消
    reason: 简短说明
    """

    ok: bool
    platform_confirmed: bool = False
    local_ratio: float = 0.0
    cancelled: bool = False
    reason: str = ""

    def __bool__(self) -> bool:
        """兼容旧代码 if watch_replay(...): 视为「播放侧成功」。"""
        return self.ok and not self.cancelled


def _find_video(page: Page) -> Locator | None:
    for sel in ("video", "video.vjs-tech", ".video-player video", ".video-container video"):
        loc = page.locator(sel)
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


def _video_stats(video: Locator) -> dict:
    try:
        return video.evaluate(
            """el => ({
                t: el.currentTime || 0,
                d: el.duration || 0,
                paused: !!el.paused,
                ended: !!el.ended,
                rate: el.playbackRate || 1,
                src: el.currentSrc || el.src || ''
            })"""
        )
    except Exception:
        return {
            "t": 0,
            "d": 0,
            "paused": True,
            "ended": False,
            "rate": 1.0,
            "src": "",
        }


def _apply_rate_muted(video: Locator, rate: float) -> float:
    """对 video 静音并设置自定义倍速，返回实际写入的 rate。"""
    r = float(rate)
    try:
        actual = video.evaluate(
            """(el, rate) => {
                el.muted = true;
                el.volume = 0;
                try { el.playbackRate = rate; } catch (e) {}
                const p = el.play();
                if (p && p.catch) p.catch(() => {});
                return el.playbackRate || rate;
            }""",
            r,
        )
        return float(actual or r)
    except Exception:
        return r


def click_play(page: Page, *, log: Callable[[str], None] = print) -> None:
    for sel in S.REPLAY_PLAY_CANDIDATES:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if loc.is_visible(timeout=800):
                loc.click(timeout=2_000, force=True)
                log(f"[replay] 点击: {sel}")
                page.wait_for_timeout(500)
                return
        except Exception:
            continue
    for text in ("立即播放", "从这一页播放", "播放"):
        try:
            loc = page.get_by_text(text, exact=False).first
            if loc.count() and loc.is_visible(timeout=500):
                loc.click(timeout=2_000, force=True)
                log(f"[replay] 点击文案: {text}")
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def configure_play(page: Page, *, rate: float, log: Callable[[str], None] = print) -> bool:
    """点播放 + 静音 + 应用用户自定义倍速。"""
    dismiss_popups(page, log=log)
    click_play(page, log=log)
    video = _find_video(page)
    if video is None:
        page.wait_for_timeout(1500)
        video = _find_video(page)
    if video is None:
        log("[replay] 未找到 video")
        return False

    rate = clamp_rate(rate)
    try:
        actual = _apply_rate_muted(video, rate)
        log(f"[replay] 已静音 {actual}x 播放（目标 {rate}x）")
    except Exception as e:
        log(f"[replay] 配置 video 失败: {e}")
        return False

    page.wait_for_timeout(800)
    st = _video_stats(video)
    if st.get("paused"):
        log(f"[replay] 仍暂停，再点播放并维持 {rate}x")
        click_play(page, log=log)
        try:
            _apply_rate_muted(video, rate)
        except Exception:
            pass
    return True


# 单段切到下一段时用较高比例，避免 65% 就提前切段导致总进度不够
_SEGMENT_END_RATIO = 0.97


def _fmt_eta(sec: float) -> str:
    if sec <= 0 or sec > 24 * 3600:
        return "-"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s:02d}秒"
    return f"{s}秒"


def _progress_bar(pct: float, width: int = 18) -> str:
    """ASCII only; Windows console GBK cannot print block glyphs."""
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    filled = max(0, min(width, filled))
    return "#" * filled + "-" * (width - filled)


def _seek_video(video: Locator, t: float) -> bool:
    """真实 seek 到已观测过的时刻（续播，非跳播伪造）。"""
    try:
        ok = video.evaluate(
            """(el, t) => {
                try {
                    const d = el.duration || 0;
                    if (!d || !(t > 0)) return false;
                    const target = Math.min(Math.max(0, t), Math.max(0, d - 1.5));
                    el.currentTime = target;
                    return true;
                } catch (e) { return false; }
            }""",
            float(t),
        )
        return bool(ok)
    except Exception:
        return False


def watch_replay(
    page: Page,
    *,
    classroom_id: str,
    lesson_id: str,
    origin: str = "https://www.yuketang.cn",
    rate: float = 1.25,
    complete_ratio: float = 0.65,
    max_watch_sec: int = 7200,
    log: Callable[[str], None] = print,
    on_progress: Callable[[dict], None] | None = None,
    title: str = "",
    should_cancel: Callable[[], bool] | None = None,
    confirm_grace_sec: int = 120,
    soft_boost: float = 0.10,
    partial: PartialStore | None = None,
    resume_partial: bool = True,
    capabilities: PlaybackCapabilities | None = None,
) -> ReplayResult:
    """打开 overview 并播到有效进度。

    complete_ratio：本地阈值（默认 65%）。
    confirm_grace_sec：达线后继续真实播放并轮询平台确认的宽限秒数。
    soft_boost：未确认时再往上播的比例（如 0.10 → 最高 min(ratio+0.10, 0.95)）。
    capabilities：跳播/签到辅助/续播边界；None 时仅用 resume_partial（默认保守）。
    返回 ReplayResult：仅 platform_confirmed=True 时上层应写入断点。

    播放过程中绝不 page.goto 离开 overview。
    续播：仅 seek 到本机 partial 曾观测到的 currentTime（真实续播，不伪造心跳）。
    片尾 seek：仅当 capabilities 显式允许且已达阈值时，真 seek 到 duration-tail_sec。
    """
    caps = capabilities or PlaybackCapabilities(resume_partial=resume_partial)
    # 参数与 capabilities 对齐：显式 resume_partial 覆盖 caps 中的开关
    do_resume = bool(resume_partial and caps.resume_partial)
    def _cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    # 供退出时写 partial（循环内更新）
    snap: dict[str, object] = {
        "ratio": 0.0,
        "watched": 0.0,
        "denom": 0.0,
        "seg_t": 0.0,
        "seg_d": 0.0,
        "finished": set(),
        "segs_d": {},
        "src_suffix": "",
    }

    def _clear_partial() -> None:
        if partial is not None:
            try:
                partial.remove(str(classroom_id), str(lesson_id))
            except Exception:
                pass

    def _save_partial_from_snap(ratio_v: float) -> None:
        if partial is None or ratio_v < 0.02:
            return
        try:
            fin = snap.get("finished") or []
            if isinstance(fin, set):
                fin_list = [str(x) for x in fin]
            else:
                fin_list = [str(x) for x in list(fin)]  # type: ignore[arg-type]
            segs_raw = snap.get("segs_d") or {}
            segs_map = (
                {str(k): float(v) for k, v in segs_raw.items()}  # type: ignore[union-attr]
                if isinstance(segs_raw, dict)
                else {}
            )
            partial.upsert(
                classroom_id=str(classroom_id),
                lesson_id=str(lesson_id),
                title=title or str(lesson_id),
                local_ratio=float(ratio_v),
                watched_sec=float(snap.get("watched") or 0),
                total_sec=float(snap.get("denom") or 0),
                segment_time=float(snap.get("seg_t") or 0),
                segment_duration=float(snap.get("seg_d") or 0),
                finished_keys=fin_list,
                seg_durations=segs_map,
                src_suffix=str(snap.get("src_suffix") or ""),
            )
        except Exception:
            pass

    def _result(
        *,
        ok: bool,
        confirmed: bool = False,
        ratio_v: float = 0.0,
        cancelled: bool = False,
        reason: str = "",
    ) -> ReplayResult:
        thr = max(0.5, min(float(complete_ratio), 1.0))
        if confirmed or (ok and not cancelled and ratio_v + 1e-9 >= thr):
            _clear_partial()
        elif cancelled or (not ok and ratio_v >= 0.02) or (
            ok and not confirmed and ratio_v < thr and ratio_v >= 0.02
        ):
            # 取消 / 失败 / 未达线的 ok 边角 — 保留续播点
            _save_partial_from_snap(ratio_v)
        if on_progress and (confirmed or cancelled or not ok):
            on_progress(
                {
                    "title": title,
                    "pct": round(ratio_v * 100, 1),
                    "phase": "cancelled" if cancelled else ("done" if ok else "fail"),
                    "eta_sec": 0,
                    "eta_text": "0秒",
                    "platform_confirmed": confirmed,
                }
            )
        return ReplayResult(
            ok=ok,
            platform_confirmed=confirmed,
            local_ratio=ratio_v,
            cancelled=cancelled,
            reason=reason,
        )

    url = lesson_overview_url(origin, lesson_id)
    log(f"[replay] 打开: {url}")
    if on_progress:
        on_progress({"title": title, "pct": 0.0, "phase": "opening", "eta_sec": None})

    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=12_000)
    except Exception:
        pass
    page.wait_for_timeout(900)

    if _cancelled():
        return _result(ok=False, cancelled=True, reason="cancelled")

    segs, total_sec = replay_segment_count(page, lesson_id, origin=origin)
    target_sec = total_sec * complete_ratio if total_sec > 0 else 0.0
    if segs or total_sec:
        wall_est = (target_sec / max(float(rate), 0.5)) if target_sec else 0
        log(
            f"[replay] 分片约 {segs} 段, 总时长约 {total_sec/60:.1f} 分钟, "
            f"有效阈值 {complete_ratio*100:.0f}% ≈ {target_sec/60:.1f} 分钟内容"
        )
        log(
            f"[replay] 预计墙钟约 {_fmt_eta(wall_est)}（按 {rate}x 估算，仅作参考）"
            " | 播放中每约 8 秒心跳"
        )

    ui0 = page_shows_replay_done(page)
    if ui0 is True:
        log("[replay] 页面已显示已观看回放 [OK]")
        return _result(ok=True, confirmed=True, ratio_v=1.0, reason="ui_already_done")
    if basic_info_finish_replay(page, lesson_id, origin=origin) is True:
        log("[replay] basic-info 已 finishReplay [OK]")
        return _result(ok=True, confirmed=True, ratio_v=1.0, reason="basic_info")

    if not configure_play(page, rate=rate, log=log):
        return _result(ok=False, reason="no_video")

    rate = clamp_rate(rate)
    primary_ratio = max(0.5, min(float(complete_ratio), 1.0))
    boost_cap = max(0.0, min(float(soft_boost), 0.40))
    max_ratio = min(primary_ratio + boost_cap, 0.95)
    grace_sec = max(0, int(confirm_grace_sec))
    log(
        f"[replay] 正在播放 @ {rate}x(静音) 目标 {primary_ratio*100:.0f}%"
        f" grace={grace_sec}s boost至{max_ratio*100:.0f}% 工作中..."
    )

    deadline = time.time() + max_watch_sec
    last_t = -1.0
    last_src = ""
    stall_since = time.time()
    last_log = 0.0
    last_basic_check = 0.0
    last_ui_check = 0.0
    last_rate_fix = 0.0
    last_partial_save = 0.0
    started_at = time.time()
    segments_seen: set[str] = set()
    finished_keys: set[str] = set()
    seg_durations: dict[str, float] = {}
    ended_streak = 0
    ratio = primary_ratio  # 当前生效阈值（可达 boost）
    tick = 0
    progress = 0.0
    grace_until = 0.0  # >0 表示宽限中
    boosted = False

    # 片尾 seek 状态（skip_ahead / checkin_assist）
    tail_seek_done = False

    # 续播：恢复已观测段 + seek 到上次 segment_time（仅当播放器回到更早位置）
    resume_item = None
    if partial is not None and do_resume:
        resume_item = partial.get(str(classroom_id), str(lesson_id))
    if resume_item is not None and float(resume_item.local_ratio or 0) >= 0.02:
        for k in resume_item.finished_keys or []:
            if k:
                finished_keys.add(str(k))
                segments_seen.add(str(k))
        for k, v in (resume_item.seg_durations or {}).items():
            try:
                seg_durations[str(k)] = max(float(v), seg_durations.get(str(k), 0.0))
            except (TypeError, ValueError):
                pass
        want_t = float(resume_item.segment_time or 0)
        video0 = _find_video(page)
        if video0 is not None and want_t > 3:
            st0 = _video_stats(video0)
            cur0 = float(st0.get("t") or 0)
            # 仅在播放器进度明显落后于本机观测时 seek（避免向前跳未看内容）
            if cur0 + 5.0 < want_t:
                if _seek_video(video0, want_t):
                    log(
                        f"[replay] 续播 seek → {want_t:.0f}s"
                        f"（本地曾达 {resume_item.local_ratio*100:.1f}%；真播放续上，非伪造）"
                    )
                    try:
                        _apply_rate_muted(video0, rate)
                    except Exception:
                        pass
                else:
                    log("[replay] 续播 seek 失败，将从当前进度真播")
            else:
                log(
                    f"[replay] 续播：播放器已在 {cur0:.0f}s"
                    f"（本地记录 {resume_item.local_ratio*100:.1f}%）"
                )
        elif resume_item.local_ratio > 0.05:
            log(
                f"[replay] 续播：已恢复累计段进度"
                f"（本地 {resume_item.local_ratio*100:.1f}%）"
            )

    def _finished_sec() -> float:
        return sum(seg_durations[k] for k in finished_keys if k in seg_durations)

    def _emit(progress_v: float, watched_sec: float, denom: float, cur_rate: float, st: dict) -> None:
        remain_ratio = max(0.0, ratio - progress_v)
        remain_content = remain_ratio * denom if denom > 0 else 0.0
        eta = remain_content / max(cur_rate, 0.5)
        elapsed = time.time() - started_at
        phase = "grace" if grace_until > time.time() else "playing"
        if on_progress:
            on_progress(
                {
                    "title": title,
                    "pct": round(progress_v * 100, 1),
                    "target_pct": round(ratio * 100, 0),
                    "watched_min": round(watched_sec / 60, 1),
                    "total_min": round(denom / 60, 1),
                    "eta_sec": int(eta),
                    "eta_text": _fmt_eta(eta),
                    "rate": cur_rate,
                    "paused": bool(st.get("paused")),
                    "segs": len(segments_seen),
                    "elapsed_sec": int(elapsed),
                    "phase": phase,
                    "total_sec_est": total_sec,
                }
            )

    def _confirm_now() -> bool:
        # 播放中禁止导航
        return platform_replay_confirmed(
            page,
            lesson_id,
            classroom_id=classroom_id,
            origin=origin,
            allow_navigation=False,
        )

    while time.time() < deadline:
        if _cancelled():
            log("[replay] 收到取消请求，停止本节")
            return _result(
                ok=False, cancelled=True, ratio_v=progress, reason="cancelled"
            )

        now = time.time()
        tick += 1

        # 仅轻量 basic-info + UI，绝不全量翻 logs / goto
        if now - last_basic_check > 25:
            last_basic_check = now
            fr = basic_info_finish_replay(page, lesson_id, origin=origin)
            if fr is True:
                log("[replay] basic-info.finishReplay=true [OK]")
                return _result(
                    ok=True, confirmed=True, ratio_v=max(progress, ratio), reason="basic_info"
                )

        if now - last_ui_check > 20:
            last_ui_check = now
            if page_shows_replay_done(page) is True:
                log("[replay] UI 已变为已观看回放 [OK]")
                return _result(
                    ok=True, confirmed=True, ratio_v=max(progress, ratio), reason="ui"
                )

        video = _find_video(page)
        if video is None:
            log("[replay] video 丢失，尝试恢复...")
            configure_play(page, rate=rate, log=log)
            page.wait_for_timeout(1200)
            continue

        st = _video_stats(video)
        t, d = float(st["t"]), float(st["d"])
        cur_rate = float(st.get("rate") or 1.0)
        src = str(st.get("src") or "")
        src_key = src.split("?")[0][-48:] if src else ""
        if src_key:
            segments_seen.add(src_key)
            if d > 0:
                seg_durations[src_key] = max(seg_durations.get(src_key, 0.0), d)

        if now - last_rate_fix > 10 and abs(cur_rate - rate) > 0.05:
            actual = _apply_rate_muted(video, rate)
            log(f"[replay] 倍速纠正 {cur_rate}x -> {actual}x")
            last_rate_fix = now
            cur_rate = actual
        elif now - last_rate_fix > 25:
            _apply_rate_muted(video, rate)
            last_rate_fix = now

        finished_seg_sec = _finished_sec()
        cur_extra = 0.0 if (src_key and src_key in finished_keys) else max(t, 0.0)
        watched_sec = finished_seg_sec + cur_extra
        denom = total_sec if total_sec > 0 else (sum(seg_durations.values()) or d or 1.0)
        progress = watched_sec / denom if denom > 0 else 0.0
        snap["ratio"] = progress
        snap["watched"] = watched_sec
        snap["denom"] = denom
        snap["seg_t"] = t
        snap["seg_d"] = d
        snap["finished"] = set(finished_keys)
        snap["segs_d"] = dict(seg_durations)
        snap["src_suffix"] = src_key

        if progress >= ratio and watched_sec > 5:
            # 达线：可选片尾真 seek（skip_ahead / checkin_assist）→ grace → soft_boost
            if (
                not tail_seek_done
                and may_tail_seek(
                    caps,
                    local_ratio=progress,
                    complete_ratio=primary_ratio,
                    already_done=tail_seek_done,
                )
            ):
                seek_t = compute_tail_seek_time(d, caps.tail_seek_sec)
                # 仅当播放器明显落后于片尾目标时才 seek（避免已在尾部时空跳）
                if seek_t is not None and t + 8.0 < seek_t:
                    mode = (
                        "签到辅助"
                        if caps.allow_checkin_assist
                        else "跳播/片尾"
                    )
                    log(
                        f"[replay] 本地已达 {progress*100:.1f}%，"
                        f"能力边界允许{mode}：真 seek → {seek_t:.0f}s "
                        f"(片长 {d:.0f}s，尾段真播约 {caps.tail_seek_sec:.0f}s)"
                    )
                    if _seek_video(video, seek_t):
                        tail_seek_done = True
                        try:
                            _apply_rate_muted(video, rate)
                        except Exception:
                            pass
                        page.wait_for_timeout(800)
                        # 片尾 seek 后进入 grace，让尾段真实播放并轮询
                        if grace_until <= 0 and grace_sec > 0:
                            grace_until = time.time() + max(grace_sec, int(caps.tail_seek_sec / max(rate, 0.5)) + 15)
                            log(
                                f"[replay] 片尾 seek 后进入确认宽限 "
                                f"{_fmt_eta(int(grace_until - time.time()))}（真播尾段）"
                            )
                        continue
                    log("[replay] 片尾 seek 失败，继续从当前位置真播")
                    tail_seek_done = True  # 避免死循环反复 seek
                else:
                    tail_seek_done = True  # 已在尾部或无法计算

            # 达线：先 grace 继续真播 + 轮询；仍未确认则 soft_boost 抬高阈值
            if grace_until <= 0:
                log(
                    f"[replay] 本地已达 {progress*100:.1f}% "
                    f"({watched_sec/60:.1f}/{denom/60:.1f} 分钟, 阈值 {ratio*100:.0f}%)"
                )
                page.wait_for_timeout(1500)
                if _confirm_now():
                    log("[replay] 平台已确认完成态 [OK]")
                    return _result(
                        ok=True, confirmed=True, ratio_v=progress, reason="confirmed"
                    )
                if grace_sec > 0:
                    grace_until = time.time() + grace_sec
                    no_skip = "不跳播" if not caps.tail_seek_enabled else "尾段真播中"
                    log(
                        f"[replay] 进入确认宽限 {_fmt_eta(grace_sec)} "
                        f"（继续播放并轮询平台，{no_skip}）"
                    )
                else:
                    grace_until = time.time()  # 立即走 boost/结束分支

            if grace_until > time.time():
                # 宽限中：保持播放，周期性确认在循环顶部已做
                pass
            else:
                # 宽限结束
                if _confirm_now():
                    log("[replay] 宽限内平台已确认 [OK]")
                    return _result(
                        ok=True, confirmed=True, ratio_v=progress, reason="grace_confirmed"
                    )
                # 若允许片尾但尚未 seek，再给一次机会（boost 前）
                if (
                    not tail_seek_done
                    and may_tail_seek(
                        caps,
                        local_ratio=progress,
                        complete_ratio=primary_ratio,
                    )
                ):
                    seek_t = compute_tail_seek_time(d, caps.tail_seek_sec)
                    if seek_t is not None and t + 8.0 < seek_t and _seek_video(video, seek_t):
                        tail_seek_done = True
                        log(
                            f"[replay] 宽限结束前片尾真 seek → {seek_t:.0f}s，继续真播尾段"
                        )
                        grace_until = time.time() + max(30, int(caps.tail_seek_sec / max(rate, 0.5)) + 10)
                        try:
                            _apply_rate_muted(video, rate)
                        except Exception:
                            pass
                        continue
                    tail_seek_done = True
                if not boosted and max_ratio > ratio + 0.001:
                    boosted = True
                    old = ratio
                    ratio = max_ratio
                    grace_until = 0.0
                    log(
                        f"[replay] 平台未确认，提升目标 {old*100:.0f}% -> {ratio*100:.0f}% "
                        "继续真实播放 (soft_boost)"
                    )
                else:
                    log(
                        "[replay] 本地达标但平台未确认 — 不写断点 (SOFT)，"
                        "下次 list 仍会出现 / 结束时对账"
                    )
                    return _result(
                        ok=True,
                        confirmed=False,
                        ratio_v=progress,
                        reason="local_threshold_soft",
                    )

        segment_done = d > 0 and (bool(st.get("ended")) or t / d >= _SEGMENT_END_RATIO)
        if segment_done:
            ended_streak += 1
            if src_key:
                finished_keys.add(src_key)
            log(
                f"[replay] 本段结束 {t:.0f}/{d:.0f}s 总进度 {progress*100:.1f}% "
                f"segs={len(segments_seen)}"
            )
            page.wait_for_timeout(1000)
            st2 = _video_stats(video)
            src2 = str(st2.get("src") or "")
            if src2 and src2 != src:
                log(f"[replay] 进入下一段 已累计 {_finished_sec()/60:.1f} 分钟")
                ended_streak = 0
                try:
                    _apply_rate_muted(video, rate)
                except Exception:
                    configure_play(page, rate=rate, log=log)
            elif ended_streak >= 2:
                if _confirm_now():
                    log("[replay] 播完且完成态确认 [OK]")
                    return _result(
                        ok=True, confirmed=True, ratio_v=progress, reason="ended_confirmed"
                    )
                if segs > 1 and len(segments_seen) < segs:
                    log(f"[replay] 分片 {len(segments_seen)}/{segs}，继续...")
                    click_play(page, log=log)
                    try:
                        _apply_rate_muted(video, rate)
                    except Exception:
                        pass
                    ended_streak = 0
                else:
                    finished_seg_sec = _finished_sec()
                    progress = finished_seg_sec / denom if denom > 0 else progress
                    if progress >= ratio or (target_sec > 0 and finished_seg_sec >= target_sec):
                        log(f"[replay] 视频结束且本地达 {ratio*100:.0f}%")
                        confirmed = False
                        for _ in range(5):
                            if _cancelled():
                                return _result(
                                    ok=False,
                                    cancelled=True,
                                    ratio_v=progress,
                                    reason="cancelled",
                                )
                            page.wait_for_timeout(3000)
                            if basic_info_finish_replay(page, lesson_id, origin=origin) is True:
                                confirmed = True
                                break
                            if page_shows_replay_done(page) is True:
                                confirmed = True
                                break
                        # 安全：结束时允许 no-nav logs 再查一次
                        if not confirmed:
                            lv = is_live_viewed(
                                page,
                                classroom_id,
                                lesson_id,
                                origin=origin,
                                allow_navigation=False,
                            )
                            confirmed = lv is True
                        if confirmed:
                            log("[replay] 结束且平台确认 [OK]")
                        else:
                            log("[replay] 结束但平台未确认 — 不写断点")
                        return _result(
                            ok=True,
                            confirmed=confirmed,
                            ratio_v=progress,
                            reason="ended",
                        )
                    log("[replay] 视频侧结束，等待平台同步...")
                    for _ in range(5):
                        if _cancelled():
                            return _result(
                                ok=False, cancelled=True, ratio_v=progress, reason="cancelled"
                            )
                        page.wait_for_timeout(4000)
                        if basic_info_finish_replay(page, lesson_id, origin=origin) is True:
                            return _result(
                                ok=True,
                                confirmed=True,
                                ratio_v=progress,
                                reason="sync_basic",
                            )
                        if page_shows_replay_done(page) is True:
                            return _result(
                                ok=True, confirmed=True, ratio_v=progress, reason="sync_ui"
                            )
                    log("[replay] 同步超时，本地进度不足确认阈值")
                    return _result(
                        ok=progress >= ratio * 0.95,
                        confirmed=False,
                        ratio_v=progress,
                        reason="sync_timeout",
                    )
        else:
            ended_streak = 0

        if abs(t - last_t) < 0.25 and src == last_src:
            if time.time() - stall_since > 18:
                log("[replay] 进度停滞，恢复播放...")
                click_play(page, log=log)
                try:
                    _apply_rate_muted(video, rate)
                except Exception:
                    pass
                stall_since = time.time()
        else:
            stall_since = time.time()
            last_t = t
            last_src = src

        if now - last_log > 8:
            pct = progress * 100
            remain_ratio = max(0.0, ratio - progress)
            eta = (remain_ratio * denom) / max(cur_rate, 0.5) if denom > 0 else 0
            toward = min(100.0, (progress / ratio) * 100.0) if ratio > 0 else pct
            bar = _progress_bar(toward)
            paused_tag = "暂停" if st.get("paused") else "播放中"
            log(
                f"[replay] {paused_tag} [{bar}] {pct:.1f}%/{ratio*100:.0f}% "
                f"| 段内 {t:.0f}/{d:.0f}s @ {cur_rate}x "
                f"| 约 {_fmt_eta(eta)} 达线 | 已跑 {_fmt_eta(now - started_at)}"
            )
            _emit(progress, watched_sec, denom, cur_rate, st)
            last_log = now
        elif tick % 2 == 0 and on_progress:
            _emit(progress, watched_sec, denom, cur_rate, st)

        # 周期性落盘 partial，便于崩溃/中断后续播
        if partial is not None and progress >= 0.02 and now - last_partial_save > 12:
            _save_partial_from_snap(progress)
            last_partial_save = now

        page.wait_for_timeout(1000)

    log("[replay] 超时")
    return _result(ok=False, ratio_v=progress, reason="timeout")
