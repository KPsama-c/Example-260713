"""考试模块（M2 stub）。默认禁用，不自动交卷。"""

from __future__ import annotations

from typing import Any, Callable

from playwright.sync_api import Page

from yinghua import selectors as S
from yinghua.settings import exam_auto_submit, exam_enabled

LogFn = Callable[[str], None]


def is_exam_page(page: Page) -> bool:
    url = (page.url or "").lower()
    if "/user/exam" in url or "exam" in url:
        return True
    try:
        if page.locator(S.EXAM_MAIN).count() > 0:
            return True
    except Exception:
        pass
    return False


def run_exam_if_enabled(
    page: Page,
    cfg: dict[str, Any],
    *,
    log: LogFn = print,
) -> dict[str, Any]:
    if not exam_enabled(cfg):
        log("[exam] 已禁用（exam.enabled=false），跳过")
        return {"ok": True, "skipped": True, "reason": "disabled"}
    if not is_exam_page(page):
        return {"ok": True, "skipped": True, "reason": "not_exam_page"}

    log("[exam] M2 stub：仅检测页面，不自动答题")
    if exam_auto_submit(cfg):
        log("[exam] 警告: auto_submit=true 但 stub 仍不交卷")
    return {
        "ok": True,
        "skipped": False,
        "reason": "stub",
        "auto_submit": False,
        "message": "请人工完成考试；自动答题尚未实现",
    }
