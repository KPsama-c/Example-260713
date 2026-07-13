"""验证码 / 弹层处理（M1：检测 + 人工；兼容 video.py API）。"""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import Page

from yinghua import selectors as S

LogFn = Callable[[str], None]


def captcha_visible(page: Page) -> bool:
    return has_playback_captcha(page)


def has_playback_captcha(page: Page) -> bool:
    """layui 验证码层或含验证码文案的可见层。"""
    for sel in S.CAPTCHA_CANDIDATES:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=300):
                continue
            # 纯关闭按钮不算验证码
            try:
                txt = (loc.inner_text(timeout=300) or "") + (
                    page.locator(S.CAPTCHA_LAYER).first.inner_text(timeout=200) or ""
                )
            except Exception:
                txt = ""
            if "验证码" in txt or "captcha" in txt.lower() or sel.endswith("img"):
                return True
            # 有图+输入框的 layer
            try:
                if page.locator(S.CAPTCHA_IMG).count() and page.locator(S.CAPTCHA_INPUT).count():
                    return True
            except Exception:
                pass
        except Exception:
            continue
    # 更严一点：layer 内同时有 img + input
    try:
        layer = page.locator(".layui-layer:visible").first
        if layer.count():
            html = layer.inner_html(timeout=500) or ""
            if "验证码" in html or (
                "<img" in html.lower() and "<input" in html.lower()
            ):
                return True
    except Exception:
        pass
    return False


def layer_suggests_locked(page: Page) -> bool:
    hints = ("未开放", "尚未开放", "未开启", "已锁定", "暂未开放", "章节锁定", "无权限")
    try:
        for sel in (".layui-layer:visible", ".layui-layer-content", ".el-message-box"):
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=200):
                txt = loc.inner_text(timeout=400) or ""
                if any(h in txt for h in hints):
                    return True
    except Exception:
        pass
    return False


def dismiss_plain_dialog(page: Page) -> bool:
    """关掉非验证码的普通确认层。"""
    clicked = False
    for sel in S.DISMISS_CANDIDATES:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible(timeout=250):
                continue
            txt = ""
            try:
                txt = loc.inner_text(timeout=200) or ""
            except Exception:
                pass
            if "验证码" in txt:
                continue
            # 避免在验证码层乱点确定
            if has_playback_captcha(page) and txt in ("确定", "提交", "确认"):
                continue
            loc.click(timeout=1200)
            clicked = True
            page.wait_for_timeout(300)
        except Exception:
            continue
    return clicked


def wait_captcha_gone(page: Page, timeout_ms: int = 120_000) -> bool:
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if not has_playback_captcha(page):
            return True
        page.wait_for_timeout(800)
    return not has_playback_captcha(page)


def handle_captcha(
    page: Page,
    *,
    cfg: dict | None = None,
    timeout_sec: int = 120,
    log: LogFn = print,
) -> bool:
    """若出现验证码：默认等待用户手输；auto_solve 预留。

    返回 True=已消失或无验证码；False=超时仍在。
    """
    cfg = cfg or {}
    cap = cfg.get("captcha") or {}
    if not bool(cap.get("enabled", True)):
        return True
    if not has_playback_captcha(page):
        return True

    auto = bool(cap.get("auto_solve", False))
    if auto:
        log("[captcha] auto_solve 已开，但 M1 未接 ONNX，回退人工")
    log(f"[captcha] 检测到验证码弹层，请在浏览器内完成（{timeout_sec}s）…")
    ok = wait_captcha_gone(page, timeout_ms=timeout_sec * 1000)
    if ok:
        log("[captcha] 验证码已消失")
    else:
        log("[captcha] 等待验证码超时")
    return ok
