"""课程目录 / 学习记录解析（尽力）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from playwright.sync_api import Page

from yinghua import selectors as S


@dataclass
class ChapterItem:
    title: str
    href: str | None = None
    status: str = ""
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_sidebar_sections(page: Page) -> list[ChapterItem]:
    items: list[ChapterItem] = []
    sidebar = page.locator(S.SIDEBAR).first
    root = sidebar if sidebar.count() else page.locator("body")
    links = root.locator(S.COURSE_LINKS)
    n = links.count()
    for i in range(min(n, 500)):
        el = links.nth(i)
        try:
            title_el = el.locator(S.SECTION_TITLE).first
            title = (
                title_el.inner_text(timeout=500).strip()
                if title_el.count()
                else el.inner_text(timeout=500).strip()
            )
            title = " ".join(title.split())
            href = el.get_attribute("href")
            if not title:
                continue
            items.append(ChapterItem(title=title, href=href))
        except Exception:
            continue
    return items


def list_study_record_rows(page: Page) -> list[ChapterItem]:
    items: list[ChapterItem] = []
    # 尝试切到视频记录（真站 .stuelearn-tab / 旧站 .tab）
    try:
        for sel in (".stuelearn-tab a", ".tab", "a[href*='study_record/video']"):
            tabs = page.locator(sel)
            for i in range(min(tabs.count(), 12)):
                t = tabs.nth(i)
                text = t.inner_text(timeout=500) or ""
                href = t.get_attribute("href") or ""
                if S.VIDEO_TAB_TEXT in text or "study_record/video" in href:
                    if "curr" not in (t.get_attribute("class") or "") and "active" not in (
                        t.get_attribute("class") or ""
                    ):
                        t.click(timeout=2000)
                        page.wait_for_timeout(1500)
                    break
    except Exception:
        pass

    rows = page.locator(S.RECORD_ROWS)
    n = rows.count()
    for i in range(min(n, 300)):
        row = rows.nth(i)
        try:
            status = ""
            for sel in (
                "td:last-child span",
                "td:last-child",
                ".col-status span",
                "td:nth-last-child(2) span",
            ):
                loc = row.locator(sel).first
                if loc.count():
                    status = (loc.inner_text(timeout=500) or "").strip()
                    if status:
                        break
            title = ""
            link = row.locator(
                "td:first-child a[href*='node'], td a[href*='nodeId'], "
                "td:first-child a, .video-link a, a[href*='node']"
            ).first
            href = None
            if link.count():
                title = (link.inner_text(timeout=500) or "").strip()
                href = link.get_attribute("href")
            if not title:
                title = (
                    row.locator("td:first-child, .video-link").first.inner_text(timeout=500) or ""
                ).strip()
            title = " ".join(title.split())
            done = any(x in status for x in ("已学", "已完成", "100%")) and "未学" not in status
            if "未学完" in status:
                done = False
            locked = any(x in status for x in ("未开放", "尚未开放", "锁定", "未开启"))
            if not title:
                continue
            items.append(
                ChapterItem(
                    title=title,
                    href=href,
                    status=status,
                    done=done or locked,
                )
            )
        except Exception:
            continue
    return items


def progress_text(page: Page) -> str | None:
    try:
        el = page.locator(S.PROGRESS_CELL).first
        if el.count():
            return (el.inner_text(timeout=1000) or "").strip()
    except Exception:
        return None
    return None
