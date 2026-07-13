"""直播回放播放：overview 页静音 + 自定义倍速播多段。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Locator, Page

from yuketang import selectors as S
from yuketang.logs import (
    basic_info_finish_replay,
    is_live_viewed,
    page_shows_replay_done,
    platform_replay_confirmed,
    replay_segment_count,
)
from yuketang.player import dismiss_popups
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
) -> ReplayResult:
    """打开 overview 并播到有效进度。

    complete_ratio：相对整节总时长的本地停播线（默认 65%）。
    返回 ReplayResult：仅 platform_confirmed=True 时上层应写入断点。

    播放过程中绝不 page.goto 离开 overview（完成检查 allow_navigation=False）。
    """
    def _cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    def _result(
        *,
        ok: bool,
        confirmed: bool = False,
        ratio_v: float = 0.0,
        cancelled: bool = False,
        reason: str = "",
    ) -> ReplayResult:
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
    log(f"[replay] 正在播放 @ {rate}x(静音) 目标 {complete_ratio*100:.0f}% 工作中...")

    deadline = time.time() + max_watch_sec
    last_t = -1.0
    last_src = ""
    stall_since = time.time()
    last_log = 0.0
    last_basic_check = 0.0
    last_ui_check = 0.0
    last_rate_fix = 0.0
    started_at = time.time()
    segments_seen: set[str] = set()
    finished_keys: set[str] = set()
    seg_durations: dict[str, float] = {}
    ended_streak = 0
    ratio = max(0.5, min(float(complete_ratio), 1.0))
    tick = 0
    progress = 0.0

    def _finished_sec() -> float:
        return sum(seg_durations[k] for k in finished_keys if k in seg_durations)

    def _emit(progress_v: float, watched_sec: float, denom: float, cur_rate: float, st: dict) -> None:
        remain_ratio = max(0.0, ratio - progress_v)
        remain_content = remain_ratio * denom if denom > 0 else 0.0
        eta = remain_content / max(cur_rate, 0.5)
        elapsed = time.time() - started_at
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
                    "phase": "playing",
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

        if progress >= ratio and watched_sec > 5:
            log(
                f"[replay] 本地已达 {progress*100:.1f}% "
                f"({watched_sec/60:.1f}/{denom/60:.1f} 分钟, 阈值 {ratio*100:.0f}%)"
            )
            page.wait_for_timeout(2500)
            confirmed = _confirm_now()
            # 再等几轮 basic-info 同步
            if not confirmed:
                log("[replay] 等待平台同步完成态...")
                for _ in range(4):
                    if _cancelled():
                        return _result(
                            ok=False, cancelled=True, ratio_v=progress, reason="cancelled"
                        )
                    page.wait_for_timeout(3000)
                    if basic_info_finish_replay(page, lesson_id, origin=origin) is True:
                        confirmed = True
                        break
                    if page_shows_replay_done(page) is True:
                        confirmed = True
                        break
            if confirmed:
                log("[replay] 平台已确认完成态 [OK]")
            else:
                log(
                    "[replay] 本地达标但平台未确认 — 不写断点，下次 list 仍会出现 "
                    "(可稍后重跑或提高 complete_ratio)"
                )
            return _result(
                ok=True,
                confirmed=confirmed,
                ratio_v=progress,
                reason="local_threshold" if not confirmed else "confirmed",
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

        page.wait_for_timeout(1000)

    log("[replay] 超时")
    return _result(ok=False, ratio_v=progress, reason="timeout")
