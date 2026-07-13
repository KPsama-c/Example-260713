"""直播回放播放：overview 页静音 + 自定义倍速播多段。"""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import Locator, Page

from yuketang import selectors as S
from yuketang.logs import basic_info_finish_replay, is_live_viewed, replay_segment_count
from yuketang.player import dismiss_popups
from yuketang.rate import clamp_rate
from yuketang.urls import lesson_overview_url


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
) -> bool:
    """打开 overview 并播到有效进度。

    complete_ratio 默认 0.65：相对**整节总时长**达到 65% 即视为达标
    （平台有效线通常 ≥60%）。平台若更早标记「已观看回放」也会提前结束。
    """
    url = lesson_overview_url(origin, lesson_id)
    log(f"[replay] 打开: {url}")
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    page.wait_for_timeout(2000)

    segs, total_sec = replay_segment_count(page, lesson_id, origin=origin)
    target_sec = total_sec * complete_ratio if total_sec > 0 else 0.0
    if segs or total_sec:
        log(
            f"[replay] 分片约 {segs} 段, 总时长约 {total_sec/60:.1f} 分钟, "
            f"有效阈值 {complete_ratio*100:.0f}% ≈ {target_sec/60:.1f} 分钟"
        )

    body = ""
    try:
        body = page.locator("body").inner_text(timeout=3_000)
    except Exception:
        pass
    if "已观看回放" in body and "未观看回放" not in body:
        log("[replay] 页面已显示已观看回放")
        return True

    if not configure_play(page, rate=rate, log=log):
        return False

    rate = clamp_rate(rate)
    log(f"[replay] 目标倍速 {rate}x（播放中若被页面重置会自动拉回）")

    deadline = time.time() + max_watch_sec
    last_t = -1.0
    last_src = ""
    stall_since = time.time()
    last_log = 0.0
    last_api_check = 0.0
    last_rate_fix = 0.0
    segments_seen: set[str] = set()
    finished_keys: set[str] = set()
    seg_durations: dict[str, float] = {}
    ended_streak = 0
    ratio = max(0.5, min(float(complete_ratio), 1.0))

    def _finished_sec() -> float:
        return sum(seg_durations[k] for k in finished_keys if k in seg_durations)

    while time.time() < deadline:
        now = time.time()
        if now - last_api_check > 45:
            last_api_check = now
            fr = basic_info_finish_replay(page, lesson_id, origin=origin)
            if fr is True:
                log("[replay] basic-info.finishReplay=true")
                return True
            lv = is_live_viewed(page, classroom_id, lesson_id, origin=origin)
            if lv is True:
                log("[replay] logs.live_viewed=true")
                return True

        try:
            body = page.locator("body").inner_text(timeout=2_000)
            if "已观看回放" in body and "未观看回放" not in body:
                log("[replay] UI 已变为已观看回放")
                return True
        except Exception:
            pass

        video = _find_video(page)
        if video is None:
            log("[replay] video 丢失，尝试恢复")
            configure_play(page, rate=rate, log=log)
            page.wait_for_timeout(2000)
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

        # 自定义倍速被播放器重置时定期拉回
        if now - last_rate_fix > 12 and abs(cur_rate - rate) > 0.05:
            actual = _apply_rate_muted(video, rate)
            log(f"[replay] 倍速已纠正 {cur_rate}x → {actual}x")
            last_rate_fix = now
            cur_rate = actual
        elif now - last_rate_fix > 30:
            # 即使接近目标也周期性 re-apply，防切段后丢失
            _apply_rate_muted(video, rate)
            last_rate_fix = now

        # 已完成分片 + 当前分片进度（当前分片未计入 finished 时才加 t）
        finished_seg_sec = _finished_sec()
        cur_extra = 0.0 if (src_key and src_key in finished_keys) else max(t, 0.0)
        watched_sec = finished_seg_sec + cur_extra
        denom = total_sec if total_sec > 0 else (sum(seg_durations.values()) or d or 1.0)
        progress = watched_sec / denom if denom > 0 else 0.0

        # 整节达到有效比例（默认 65%）即可结束
        if progress >= ratio and watched_sec > 5:
            log(
                f"[replay] 已达有效进度 {progress*100:.1f}% "
                f"({watched_sec/60:.1f}/{denom/60:.1f} 分钟, 阈值 {ratio*100:.0f}%)"
            )
            page.wait_for_timeout(3000)
            fr = basic_info_finish_replay(page, lesson_id, origin=origin)
            lv = is_live_viewed(page, classroom_id, lesson_id, origin=origin)
            if fr or lv:
                log("[replay] 平台已确认完成态")
            else:
                log("[replay] 本地已达阈值；若日志未变「已观看回放」可再跑或提高 complete_ratio")
            return True

        # 本段播到末尾 → 记入 finished，尝试下一段
        segment_done = d > 0 and (bool(st.get("ended")) or t / d >= _SEGMENT_END_RATIO)
        if segment_done:
            ended_streak += 1
            if src_key:
                finished_keys.add(src_key)
            log(
                f"[replay] 本段将结束 {t:.0f}/{d:.0f}s "
                f"总进度约 {progress*100:.1f}% segs={len(segments_seen)}"
            )
            page.wait_for_timeout(1500)
            st2 = _video_stats(video)
            src2 = str(st2.get("src") or "")
            if src2 and src2 != src:
                log(f"[replay] 下一段，已完成分片累计 {_finished_sec()/60:.1f} 分钟")
                ended_streak = 0
                try:
                    _apply_rate_muted(video, rate)
                except Exception:
                    configure_play(page, rate=rate, log=log)
            elif ended_streak >= 2:
                fr = basic_info_finish_replay(page, lesson_id, origin=origin)
                lv = is_live_viewed(page, classroom_id, lesson_id, origin=origin)
                if fr or lv:
                    log("[replay] 播完且完成态确认")
                    return True
                if segs > 1 and len(segments_seen) < segs:
                    log(f"[replay] 分片 {len(segments_seen)}/{segs}，尝试继续")
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
                        log(f"[replay] 视频结束且已达 {ratio*100:.0f}% 阈值")
                        return True
                    log("[replay] 视频侧结束，等待平台同步…")
                    for _ in range(6):
                        page.wait_for_timeout(5000)
                        if basic_info_finish_replay(page, lesson_id, origin=origin):
                            return True
                        if is_live_viewed(page, classroom_id, lesson_id, origin=origin):
                            return True
                    log("[replay] 结束但完成态未确认（按本地进度记结果）")
                    return progress >= ratio * 0.95
        else:
            ended_streak = 0

        # 停滞恢复
        if abs(t - last_t) < 0.25 and src == last_src:
            if time.time() - stall_since > 20:
                log("[replay] 进度停滞，恢复播放")
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

        if now - last_log > 25:
            log(
                f"[replay] 段内 {t:.0f}/{d:.0f}s @ {cur_rate}x | 总进度 {progress*100:.1f}% "
                f"(目标 {ratio*100:.0f}%) paused={st.get('paused')} segs={len(segments_seen)}"
            )
            last_log = now

        page.wait_for_timeout(2000)

    log("[replay] 超时")
    return False
