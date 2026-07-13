"""登录检测与等待扫码/账号登录。"""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import Page

from yinghua import selectors as S
from yinghua.urls import course_entry_url, home_url, study_record_urls


def _any_visible(page: Page, candidates: list[str], timeout_ms: int = 800) -> bool:
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if loc.is_visible(timeout=timeout_ms):
                return True
        except Exception:
            continue
    return False


def is_logged_in(page: Page) -> bool:
    if _any_visible(page, S.LOGGED_IN_HINTS, timeout_ms=500):
        return True
    url = (page.url or "").lower()
    if any(x in url for x in ("passport", "login", "sso", "signin", "oauth")):
        return False
    # 已进入学习相关页且无明显登录表单
    if any(x in url for x in ("study", "course", "user/", "student", "node")):
        if not _any_visible(page, S.LOGIN_HINTS, timeout_ms=400):
            return True
    return False


def wait_for_login(
    page: Page,
    *,
    timeout_sec: int = 300,
    poll_sec: float = 2.0,
    on_wait: Callable[[int], None] | None = None,
) -> bool:
    deadline = time.time() + timeout_sec
    last_report = -1
    while time.time() < deadline:
        if is_logged_in(page):
            return True
        left = int(deadline - time.time())
        if on_wait and left // 10 != last_report:
            last_report = left // 10
            on_wait(left)
        time.sleep(poll_sec)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:
            pass
    return is_logged_in(page)


def ensure_login(
    page: Page,
    *,
    cfg: dict,
    timeout_sec: int = 300,
    log: Callable[[str], None] = print,
) -> tuple[bool, str]:
    """打开站点；未登录则等待用户在浏览器内完成登录。

    返回 (成功?, 当前 URL)。
    """
    candidates = []
    entry = course_entry_url(cfg)
    candidates.append(entry)
    candidates.append(home_url(cfg))
    for u in study_record_urls(cfg):
        if u not in candidates:
            candidates.append(u)

    primary = candidates[0]
    log(f"[login] 打开: {primary}")
    page.goto(primary, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass

    if is_logged_in(page):
        log("[login] 已检测到登录态")
        return True, page.url

    log(f"[login] 未登录。请在弹出的浏览器中完成登录（{timeout_sec}s 内）…")
    ok = wait_for_login(
        page,
        timeout_sec=timeout_sec,
        on_wait=lambda left: log(f"[login] 等待登录… 剩余约 {left}s"),
    )
    if ok:
        log("[login] 登录成功")
        return True, page.url
    log("[login] 登录超时")
    return False, page.url
