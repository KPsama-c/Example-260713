"""视频播放与章节切换。"""

from __future__ import annotations

import random
import time
from typing import Callable

from playwright.sync_api import Page

from yinghua import selectors as S
from yinghua.captcha import (
    dismiss_plain_dialog,
    has_playback_captcha,
    layer_suggests_locked,
    wait_captcha_gone,
)


LogFn = Callable[[str], None]


def _log(log: LogFn | None, msg: str) -> None:
    if log:
        log(msg)


def find_video(page: Page):
    loc = page.locator(S.VIDEO).first
    if loc.count():
        return loc
    return None


def setup_and_play(page: Page, *, mute: bool = True, rate: float = 1.0, log: LogFn | None = None) -> bool:
    v = find_video(page)
    if not v:
        _log(log, "未找到 video 元素")
        return False
    try:
        page.evaluate(
            """([mute, rate]) => {
                const v = document.querySelector('video, .video-player');
                if (!v) return false;
                v.muted = !!mute;
                try { v.playbackRate = rate; } catch (e) {}
                return true;
            }""",
            [mute, rate],
        )
        # 尝试 play
        try:
            v.evaluate("el => el.play && el.play()")
        except Exception:
            page.keyboard.press("Space")
        _log(log, f"开始播放 rate={rate} mute={mute}")
        return True
    except Exception as e:
        _log(log, f"播放失败: {e}")
        return False


def video_state(page: Page) -> dict:
    try:
        return page.evaluate(
            """() => {
                const v = document.querySelector('video, .video-player');
                if (!v) return {found: false};
                return {
                    found: true,
                    paused: !!v.paused,
                    ended: !!v.ended,
                    currentTime: v.currentTime || 0,
                    duration: v.duration || 0,
                    readyState: v.readyState || 0,
                };
            }"""
        )
    except Exception:
        return {"found": False}


def wait_until_ended(
    page: Page,
    *,
    max_watch_sec: float = 7200,
    stuck_check_interval: float = 30,
    stuck_min_diff: float = 0.5,
    on_tick: LogFn | None = None,
) -> str:
    """
    阻塞直到 ended / locked / captcha_timeout / stuck_reload / timeout。
    返回 reason 字符串。
    """
    start = time.time()
    last_t = 0.0
    stuck = 0
    last_stuck_check = time.time()

    while time.time() - start < max_watch_sec:
        if has_playback_captcha(page):
            _log(on_tick, "检测到播放验证码，请在浏览器中完成…")
            ok = wait_captcha_gone(page, timeout_ms=180_000)
            if not ok:
                return "captcha_timeout"
            setup_and_play(page, log=on_tick)
            continue

        if layer_suggests_locked(page):
            dismiss_plain_dialog(page)
            return "locked"

        dismiss_plain_dialog(page)

        st = video_state(page)
        if not st.get("found"):
            # 可能是考试/作业页
            if _looks_like_non_video(page):
                return "non_video"
            time.sleep(1)
            continue

        if st.get("ended"):
            return "ended"

        if st.get("paused"):
            setup_and_play(page, log=None)

        now = time.time()
        if now - last_stuck_check >= stuck_check_interval:
            cur = float(st.get("currentTime") or 0)
            if abs(cur - last_t) < stuck_min_diff and not st.get("paused"):
                stuck += 1
                _log(on_tick, f"疑似卡顿 ({stuck})")
                if stuck > 3:
                    return "stuck"
            else:
                stuck = 0
            last_t = cur
            last_stuck_check = now
            if on_tick:
                dur = float(st.get("duration") or 0)
                on_tick(f"播放中 {cur:.0f}/{dur:.0f}s")

        time.sleep(1)

    return "timeout"


def _looks_like_non_video(page: Page) -> bool:
    try:
        texts = page.locator("button span, .el-button, .detmain-tabs .item span").all_inner_texts()
        joined = " ".join(texts)
        return any(x in joined for x in ("考试", "作业", "练习", "测验"))
    except Exception:
        return False


def current_sidebar_index(page: Page) -> int:
    try:
        return page.evaluate(
            """() => {
                const sel = 'a[target="_self"], .section-item';
                const sidebar = document.querySelector('.detmain-navlist, .course-sidebar, .section-list, .detmain-navs');
                const root = sidebar || document;
                const links = Array.from(root.querySelectorAll(sel));
                const url = new URL(location.href);
                const nodeId = url.searchParams.get('nodeId');
                const h1 = (document.querySelector('h1') || {}).innerText || '';
                for (let i = 0; i < links.length; i++) {
                    const a = links[i];
                    try {
                        const lu = new URL(a.href, location.origin);
                        if (nodeId && lu.searchParams.get('nodeId') === nodeId) return i;
                        if (lu.pathname === url.pathname && lu.search === url.search) return i;
                    } catch (e) {}
                    const title = (a.querySelector('.section-title') || a).innerText || '';
                    if (h1 && title && (h1.includes(title.trim()) || title.trim().includes(h1.trim()))) return i;
                }
                return -1;
            }"""
        )
    except Exception:
        return -1


def click_next_section(page: Page, log: LogFn | None = None) -> bool:
    idx = current_sidebar_index(page)
    try:
        n = page.evaluate(
            """() => {
                const sel = 'a[target="_self"], .section-item';
                const sidebar = document.querySelector('.detmain-navlist, .course-sidebar, .section-list, .detmain-navs');
                const root = sidebar || document;
                return root.querySelectorAll(sel).length;
            }"""
        )
        if idx >= 0 and idx + 1 < int(n or 0):
            page.evaluate(
                """(nextIdx) => {
                    const sel = 'a[target="_self"], .section-item';
                    const sidebar = document.querySelector('.detmain-navlist, .course-sidebar, .section-list, .detmain-navs');
                    const root = sidebar || document;
                    const links = root.querySelectorAll(sel);
                    const next = links[nextIdx];
                    if (!next) return;
                    const header = next.querySelector('.section-header') || next;
                    header.click();
                }""",
                idx + 1,
            )
            _log(log, f"切换下一节 index={idx + 1}")
            page.wait_for_timeout(2500)
            return True
    except Exception as e:
        _log(log, f"侧栏下一节失败: {e}")

    # 全局「下一章」按钮
    try:
        clicked = page.evaluate(
            """() => {
                const els = Array.from(document.querySelectorAll('a, button, span'));
                for (const el of els) {
                    const text = (el.innerText || '').trim();
                    if (!/下一章|下一节|下一页|Next Chapter|Next Section/i.test(text)) continue;
                    if (el.closest('.pagebar-bbs, .discuss-list, .bbs-list, .pagebar')) continue;
                    if (/记录|首页|尾页/.test(text)) continue;
                    if (el.offsetParent === null) continue;
                    el.click();
                    return true;
                }
                return false;
            }"""
        )
        if clicked:
            _log(log, "点击全局下一章按钮")
            page.wait_for_timeout(3000)
            return True
    except Exception:
        pass
    return False


def pause_random(lo_hi: list | tuple) -> None:
    try:
        lo, hi = float(lo_hi[0]), float(lo_hi[1])
    except Exception:
        lo, hi = 2.0, 6.0
    time.sleep(random.uniform(lo, hi))
