"""登录检测与等待扫码/SSO。"""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import Page

from yuketang import selectors as S


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
    """启发式：有用户区且无明显登录入口。"""
    if _any_visible(page, S.LOGGED_IN_HINTS, timeout_ms=500):
        return True
    # URL 侧线索：进入学习页且不是 passport/login
    url = page.url.lower()
    if any(x in url for x in ("passport", "login", "sso")):
        return False
    if "yuketang" in url and "student" in url:
        # 学习相关页且无登录按钮
        if not _any_visible(page, S.LOGIN_HINTS, timeout_ms=400):
            return True
    return False


def wait_for_login(
    page: Page,
    *,
    timeout_sec: int = 180,
    poll_sec: float = 2.0,
    on_wait: Callable[[int], None] | None = None,
) -> bool:
    """阻塞直到登录成功或超时。返回是否成功。"""
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
    course_url: str,
    timeout_sec: int = 180,
    log: Callable[[str], None] = print,
    candidate_urls: list[str] | None = None,
) -> tuple[bool, str]:
    """打开课程页；若未登录则提示用户在浏览器内完成登录。

    返回 (成功?, 最终使用的课程 URL)。
    """
    candidates = list(candidate_urls or [course_url])
    if course_url not in candidates:
        candidates.insert(0, course_url)

    primary = candidates[0]
    log(f"[login] 打开: {primary}")
    page.goto(primary, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass

    if is_logged_in(page):
        log("[login] 已检测到登录态")
        final = _pick_working_course_url(page, candidates, log=log)
        return True, final

    log(
        f"[login] 未登录。请在弹出的浏览器中扫码/SSO 登录（{timeout_sec}s 内）…"
    )
    ok = wait_for_login(
        page,
        timeout_sec=timeout_sec,
        on_wait=lambda left: log(f"[login] 等待登录… 剩余约 {left}s"),
    )
    if ok:
        log("[login] 登录成功")
        final = _pick_working_course_url(page, candidates, log=log)
        return True, final

    log("[login] 登录超时")
    return False, primary


def _pick_working_course_url(
    page: Page,
    candidates: list[str],
    *,
    log: Callable[[str], None] = print,
) -> str:
    """依次尝试候选课程 URL，选第一个能打开且不像 404 的。"""
    for u in candidates:
        try:
            log(f"[login] 尝试课程页: {u}")
            page.goto(u, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:
                pass
            page.wait_for_timeout(600)
            body = ""
            try:
                body = page.locator("body").inner_text(timeout=3_000)[:500]
            except Exception:
                pass
            bad = any(
                x in body
                for x in ("404", "页面不存在", "无权限", "没有权限", "Not Found")
            )
            if bad:
                log(f"[login] 跳过无效页: {u}")
                continue
            # 有一定内容即可
            if len(body.strip()) > 20 or "yuketang" in page.url:
                log(f"[login] 使用课程页: {page.url}")
                return page.url or u
        except Exception as e:
            log(f"[login] 打开失败 {u}: {e}")
            continue
    # 回退第一个
    try:
        page.goto(candidates[0], wait_until="domcontentloaded")
    except Exception:
        pass
    return page.url or candidates[0]
