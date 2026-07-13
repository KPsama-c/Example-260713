"""视频播放：关弹窗、验证码、静音倍速、状态机 videoStage 1/2。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Frame, Locator, Page

from yinghua import selectors as S
from yinghua.captcha import handle_captcha

LogFn = Callable[[str], None]


@dataclass
class WatchResult:
    ok: bool
    reason: str = ""
    watched_sec: float = 0.0
    duration_sec: float = 0.0
    ratio: float = 0.0


def dismiss_popups(page: Page, *, rounds: int = 4, log: LogFn = print) -> None:
    for _ in range(rounds):
        clicked = False
        for sel in S.DISMISS_CANDIDATES:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                if loc.is_visible(timeout=400):
                    # 避免点掉验证码相关
                    txt = ""
                    try:
                        txt = loc.inner_text(timeout=200) or ""
                    except Exception:
                        pass
                    if "验证码" in txt:
                        continue
                    loc.click(timeout=1_500)
                    log(f"[player] 关闭弹窗: {sel}")
                    clicked = True
                    page.wait_for_timeout(400)
            except Exception:
                continue
        if not clicked:
            break


def _find_video_in_scope(scope: Page | Frame) -> Locator | None:
    for sel in ("video", "video.vjs-tech", ".vjs-tech", ".video-player video"):
        loc = scope.locator(sel)
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


def find_video(page: Page) -> tuple[Page | Frame, Locator] | None:
    v = _find_video_in_scope(page)
    if v is not None:
        return page, v
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        v = _find_video_in_scope(frame)
        if v is not None:
            return frame, v
    page.wait_for_timeout(1500)
    v = _find_video_in_scope(page)
    if v is not None:
        return page, v
    for frame in page.frames:
        v = _find_video_in_scope(frame)
        if v is not None:
            return frame, v
    return None


def click_play_buttons(page: Page, log: LogFn = print) -> None:
    for sel in S.PLAY_BUTTON_CANDIDATES:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=500):
                loc.click(timeout=2_000)
                log(f"[player] 点击播放: {sel}")
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


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


def configure_and_play(
    page: Page,
    *,
    rate: float = 1.5,
    cfg: dict | None = None,
    log: LogFn = print,
) -> bool:
    dismiss_popups(page, log=log)
    handle_captcha(page, cfg=cfg or {}, log=log)
    click_play_buttons(page, log=log)

    found = find_video(page)
    if not found:
        log("[player] 未找到 <video>")
        return False

    _, video = found
    rate = max(0.5, min(float(rate), 3.0))
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
        log(f"[player] 静音 {rate}x 播放")
    except Exception as e:
        log(f"[player] 配置 video 失败: {e}")
        return False

    page.wait_for_timeout(800)
    try:
        if video.evaluate("el => el.paused"):
            log("[player] 仍暂停，1.0x 重试")
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


def wait_until_done(
    page: Page,
    *,
    complete_ratio: float = 0.95,
    max_watch_sec: int = 7200,
    rate: float = 1.5,
    cfg: dict | None = None,
    log: LogFn = print,
    should_cancel: Callable[[], bool] | None = None,
) -> WatchResult:
    """videoStage: 1=播 2=核对进度补刷。"""
    deadline = time.time() + max_watch_sec
    last_t = -1.0
    stall_since = time.time()
    last_log = 0.0
    stage = 1
    best_t = 0.0
    best_d = 0.0

    while time.time() < deadline:
        if should_cancel and should_cancel():
            return WatchResult(False, "cancelled", best_t, best_d, best_t / best_d if best_d else 0)

        dismiss_popups(page, rounds=1, log=lambda *_: None)
        handle_captcha(page, cfg=cfg or {}, timeout_sec=90, log=log)

        found = find_video(page)
        if not found:
            page.wait_for_timeout(1000)
            found = find_video(page)
            if not found:
                if best_d > 0 and best_t / best_d >= complete_ratio:
                    return WatchResult(True, "lost_video_but_ratio_ok", best_t, best_d, best_t / best_d)
                log("[player] 播放中丢失 video")
                return WatchResult(False, "lost_video", best_t, best_d, 0)

        _, video = found
        st = _video_stats(video)
        t, d = float(st["t"]), float(st["d"])
        ended = bool(st["ended"])
        paused = bool(st["paused"])
        best_t = max(best_t, t)
        best_d = max(best_d, d)

        ratio = (t / d) if d > 0 else 0.0
        if d > 0 and (ended or ratio >= complete_ratio):
            # stage 2: 核对是否回退
            if stage == 1:
                stage = 2
                log(f"[player] stage2 核对进度 {t:.1f}/{d:.1f}s ({100*ratio:.1f}%)")
                page.wait_for_timeout(1500)
                st2 = _video_stats(video)
                t2, d2 = float(st2["t"]), float(st2["d"])
                r2 = (t2 / d2) if d2 > 0 else 0.0
                if r2 >= complete_ratio or st2.get("ended"):
                    log(f"[player] 完成: {t2:.1f}/{d2:.1f}s")
                    return WatchResult(True, "done", t2, d2, r2)
                log("[player] stage2 进度不足，补刷")
                stage = 1
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
                    configure_and_play(page, rate=rate, cfg=cfg, log=log)
                continue
            log(f"[player] 完成: {t:.1f}/{d:.1f}s")
            return WatchResult(True, "done", t, d, ratio)

        if abs(t - last_t) < 0.2:
            if time.time() - stall_since > 15:
                log("[player] 进度停滞，恢复播放")
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
                log(f"[player] 进度 {t:.0f}/{d:.0f}s ({100*t/d:.1f}%) paused={paused} stage={stage}")
            else:
                log(f"[player] 等待 duration… t={t:.1f} paused={paused}")
            last_log = now

        page.wait_for_timeout(2000)

    log("[player] 等待超时")
    r = best_t / best_d if best_d else 0.0
    return WatchResult(r >= complete_ratio, "timeout", best_t, best_d, r)


def watch_current(
    page: Page,
    *,
    rate: float = 1.5,
    complete_ratio: float = 0.95,
    max_watch_sec: int = 7200,
    cfg: dict | None = None,
    log: LogFn = print,
    should_cancel: Callable[[], bool] | None = None,
) -> WatchResult:
    if not configure_and_play(page, rate=rate, cfg=cfg, log=log):
        return WatchResult(False, "no_video")
    return wait_until_done(
        page,
        complete_ratio=complete_ratio,
        max_watch_sec=max_watch_sec,
        rate=rate,
        cfg=cfg,
        log=log,
        should_cancel=should_cancel,
    )
