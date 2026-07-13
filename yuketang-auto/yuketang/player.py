"""视频播放：关弹窗、静音、倍速、等待结束。"""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import Frame, Page, Locator

from yuketang import selectors as S


def dismiss_popups(page: Page, *, rounds: int = 4, log: Callable[[str], None] = print) -> None:
    for _ in range(rounds):
        clicked = False
        for sel in S.DISMISS_CANDIDATES:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                if loc.is_visible(timeout=400):
                    loc.click(timeout=1_500)
                    log(f"[player] 关闭弹窗: {sel}")
                    clicked = True
                    page.wait_for_timeout(400)
            except Exception:
                continue
        if not clicked:
            break


def _find_video_in_scope(scope: Page | Frame) -> Locator | None:
    for sel in ("video", "video.vjs-tech", ".vjs-tech"):
        loc = scope.locator(sel)
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


def find_video(page: Page) -> tuple[Page | Frame, Locator] | None:
    """在主文档与 iframe 中找 video。"""
    v = _find_video_in_scope(page)
    if v is not None:
        return page, v

    # iframe
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        v = _find_video_in_scope(frame)
        if v is not None:
            return frame, v

    # 再等一会儿（懒加载）
    page.wait_for_timeout(1500)
    v = _find_video_in_scope(page)
    if v is not None:
        return page, v
    for frame in page.frames:
        v = _find_video_in_scope(frame)
        if v is not None:
            return frame, v
    return None


def click_play_buttons(page: Page, log: Callable[[str], None] = print) -> None:
    for sel in S.PLAY_BUTTON_CANDIDATES:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=500):
                loc.click(timeout=2_000)
                log(f"[player] 点击播放控件: {sel}")
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def configure_and_play(
    page: Page,
    *,
    rate: float = 1.5,
    log: Callable[[str], None] = print,
) -> bool:
    """静音、设倍速、play。失败返回 False。"""
    dismiss_popups(page, log=log)
    click_play_buttons(page, log=log)

    found = find_video(page)
    if not found:
        log("[player] 未找到 <video> 元素")
        return False

    scope, video = found
    rate = max(0.5, min(float(rate), 2.5))

    try:
        video.evaluate(
            """(el, rate) => {
                el.muted = true;
                el.volume = 0;
                try { el.playbackRate = rate; } catch (e) {}
                const p = el.play();
                if (p && p.catch) p.catch(() => {});
            }""",
            rate,
        )
        log(f"[player] 已静音并尝试 {rate}x 播放")
    except Exception as e:
        log(f"[player] 配置 video 失败: {e}")
        return False

    # 若仍 paused，降速重试 + 再点播放
    page.wait_for_timeout(800)
    try:
        paused = video.evaluate("el => el.paused")
        if paused:
            log("[player] 仍为暂停，降为 1.0x 并重试 play")
            click_play_buttons(page, log=log)
            video.evaluate(
                """(el) => {
                    el.muted = true;
                    el.playbackRate = 1.0;
                    const p = el.play();
                    if (p && p.catch) p.catch(() => {});
                }"""
            )
    except Exception:
        pass

    return True


def _video_stats(video: Locator) -> dict:
    try:
        return video.evaluate(
            """el => ({
                t: el.currentTime || 0,
                d: el.duration || 0,
                paused: !!el.paused,
                ended: !!el.ended,
                rate: el.playbackRate || 1
            })"""
        )
    except Exception:
        return {"t": 0, "d": 0, "paused": True, "ended": False, "rate": 1}


def wait_until_done(
    page: Page,
    *,
    complete_ratio: float = 0.65,
    max_watch_sec: int = 7200,
    rate: float = 1.5,
    log: Callable[[str], None] = print,
) -> bool:
    """轮询直到播完/进度达标/超时。"""
    deadline = time.time() + max_watch_sec
    last_t = -1.0
    stall_since = time.time()
    last_log = 0.0

    while time.time() < deadline:
        dismiss_popups(page, rounds=1, log=lambda *_: None)

        found = find_video(page)
        if not found:
            # 可能已跳转或页面结构变了
            page.wait_for_timeout(1000)
            found = find_video(page)
            if not found:
                log("[player] 播放中丢失 video")
                return False

        _, video = found
        st = _video_stats(video)
        t, d = float(st["t"]), float(st["d"])
        ended = bool(st["ended"])
        paused = bool(st["paused"])

        if d > 0 and (ended or t / d >= complete_ratio):
            log(f"[player] 完成: {t:.1f}/{d:.1f}s")
            return True

        # 卡住：进度不动
        if abs(t - last_t) < 0.2:
            if time.time() - stall_since > 15:
                log("[player] 进度停滞，尝试恢复播放")
                try:
                    video.evaluate(
                        """(el, rate) => {
                            el.muted = true;
                            el.playbackRate = rate;
                            const p = el.play();
                            if (p && p.catch) p.catch(() => {});
                        }""",
                        rate,
                    )
                except Exception:
                    click_play_buttons(page, log=log)
                stall_since = time.time()
        else:
            stall_since = time.time()
            last_t = t

        now = time.time()
        if now - last_log > 20:
            if d > 0:
                log(f"[player] 进度 {t:.0f}/{d:.0f}s ({100*t/d:.1f}%) paused={paused}")
            else:
                log(f"[player] 等待 duration… t={t:.1f} paused={paused}")
            last_log = now

        page.wait_for_timeout(2000)

    log("[player] 等待超时")
    return False


def watch_current(
    page: Page,
    *,
    rate: float = 1.5,
    complete_ratio: float = 0.65,
    max_watch_sec: int = 7200,
    log: Callable[[str], None] = print,
) -> bool:
    if not configure_and_play(page, rate=rate, log=log):
        return False
    return wait_until_done(
        page,
        complete_ratio=complete_ratio,
        max_watch_sec=max_watch_sec,
        rate=rate,
        log=log,
    )
