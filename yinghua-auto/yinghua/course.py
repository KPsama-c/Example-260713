"""课程目录解析：兴趣学习 / 学习记录 / 节点链接。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from playwright.sync_api import Page

from yinghua import selectors as S
from yinghua.progress import ProgressStore
from yinghua.urls import (
    course_entry_url,
    extract_course_id,
    extract_node_id,
    is_video_path,
    study_record_urls,
)
from yinghua.util import abs_url, origin_of, progress_key

LogFn = Callable[[str], None]


@dataclass
class Section:
    node_id: str
    title: str
    href: str
    done_hint: bool = False
    is_video: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _node_id_from_href(href: str, title: str) -> str:
    href = (href or "").strip()
    nid = extract_node_id(href)
    if nid:
        return nid
    if href:
        m = re.search(r"(?:node|id|chapter|section)[=/](\w+)", href, re.I)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{3,})(?:[/?#]|$)", href)
        if m:
            return m.group(1)
        path = urlparse(href).path or href
        return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]
    return hashlib.md5((title or "unknown").encode("utf-8")).hexdigest()[:12]


def _text_has_any(text: str, hints: list[str]) -> bool:
    t = text or ""
    return any(h in t for h in hints)


def _is_nav_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if t in getattr(S, "NAV_TITLE_BLACKLIST", []):
        return True
    # 纯数字分页
    if re.fullmatch(r"\d{1,3}", t):
        return True
    return False


def _row_status_done(page: Page, link_el) -> bool:
    """学习记录表格：看同一行状态列是否「已学」。"""
    try:
        row = link_el.locator("xpath=ancestor::tr[1]")
        if not row.count():
            return False
        # 优先最后一列状态，避免「完成时间」误触发
        try:
            last_td = row.first.locator("td").last
            if last_td.count():
                status = (last_td.inner_text(timeout=400) or "").strip()
                if any(x in status for x in ("未学完", "未学", "尚未")):
                    return False
                if _text_has_any(status, S.DONE_HINTS):
                    return True
        except Exception:
            pass
        text = (row.first.inner_text(timeout=500) or "").strip()
        if any(x in text for x in ("未学完", "尚未学习")):
            return False
        # 整行仅当出现明确「已学」且非未学
        if re.search(r"(?<![未尚])已学", text) or "已完成" in text or "已学完" in text:
            return True
    except Exception:
        pass
    return False


def list_sections_from_page(
    page: Page,
    *,
    base_url: str,
    log: LogFn = print,
) -> list[Section]:
    """尽力从当前页解析章节/视频入口（优先 /user/node）。"""
    origin = origin_of(page.url or base_url, base_url)
    found: list[Section] = []
    seen: set[str] = set()

    selectors = list(S.SECTION_LINK_CANDIDATES)
    for sel in selectors:
        try:
            locs = page.locator(sel)
            n = min(locs.count(), 500)
        except Exception:
            continue
        for i in range(n):
            try:
                el = locs.nth(i)
                if not el.is_visible(timeout=200):
                    continue
                href = ""
                try:
                    href = el.get_attribute("href") or ""
                except Exception:
                    href = ""
                if not href or href.startswith("javascript"):
                    try:
                        inner = el.locator("a[href]").first
                        if inner.count():
                            href = inner.get_attribute("href") or href
                            title = (inner.inner_text(timeout=500) or "").strip()
                        else:
                            title = (el.inner_text(timeout=500) or "").strip()
                    except Exception:
                        title = (el.inner_text(timeout=500) or "").strip()
                else:
                    title = (el.inner_text(timeout=500) or "").strip()

                title = re.sub(r"\s+", " ", title).strip()
                if not title or len(title) > 200:
                    continue
                if _is_nav_title(title):
                    continue

                href = abs_url(origin, href)
                if not href or href.rstrip("/") == origin.rstrip("/"):
                    continue

                # 只要节点/视频路径；避免整站导航被 a[href*='study'] 扫进
                if not is_video_path(href) and "/user/node" not in href:
                    # 课程目录页可能仅有相对 node 链
                    if "nodeId=" not in href and "/node" not in href.lower():
                        continue

                is_video = True
                if _text_has_any(title, S.NON_VIDEO_HINTS) and not _text_has_any(
                    title, S.VIDEO_TYPE_HINTS
                ):
                    is_video = False

                done_hint = _text_has_any(title, S.DONE_HINTS) or _row_status_done(page, el)

                nid = _node_id_from_href(href, title)
                key = f"{nid}|{href}"
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    Section(
                        node_id=nid,
                        title=title[:120],
                        href=href,
                        done_hint=done_hint,
                        is_video=is_video,
                    )
                )
            except Exception:
                continue
        # 已有足够节点就不必再用宽松选择器
        if len(found) >= 3 and any("/user/node" in s.href or "nodeId=" in s.href for s in found):
            break

    log(f"[course] 当前页解析到 {len(found)} 个入口")
    return found


def list_enrolled_courses(page: Page, *, base_url: str, log: LogFn = print) -> list[dict[str, str]]:
    """从「我的课程 / 兴趣学习」列表解析课程卡片。"""
    origin = origin_of(page.url or base_url, base_url)
    courses: list[dict[str, str]] = []
    seen: set[str] = set()
    for sel in getattr(S, "COURSE_CARD_LINKS", []) + [
        "a[href*='courseId=']",
    ]:
        try:
            locs = page.locator(sel)
            n = min(locs.count(), 80)
        except Exception:
            continue
        for i in range(n):
            try:
                el = locs.nth(i)
                href = el.get_attribute("href") or ""
                title = re.sub(r"\s+", " ", (el.inner_text(timeout=500) or "").strip())
                href = abs_url(origin, href)
                if not href or href in seen:
                    continue
                if "courseId=" not in href and "/user/course" not in href:
                    continue
                if _is_nav_title(title) and "courseId=" not in href:
                    continue
                cid = extract_course_id(href)
                if not cid:
                    continue
                seen.add(href)
                courses.append(
                    {
                        "course_id": cid,
                        "title": title[:120] or cid,
                        "href": href,
                        "study_record": abs_url(
                            origin, f"/user/study_record?courseId={cid}"
                        ),
                        "study_video": abs_url(
                            origin, f"/user/study_record/video?courseId={cid}"
                        ),
                        "chapter": abs_url(
                            origin, f"/user/course/chapter?courseId={cid}"
                        ),
                    }
                )
            except Exception:
                continue
    log(f"[course] 课程卡片 {len(courses)}")
    return courses


def open_course_index(
    page: Page,
    cfg: dict,
    *,
    log: LogFn = print,
) -> str:
    """打开能解析出节点的页面，返回最终 URL。"""
    base = str(cfg.get("base_url") or "")
    urls: list[str] = []
    entry = course_entry_url(cfg)
    urls.append(entry)
    for u in study_record_urls(cfg):
        if u not in urls:
            urls.append(u)

    last = page.url
    best_url = last
    best_count = 0

    for u in urls:
        try:
            log(f"[course] 打开目录: {u}")
            page.goto(u, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:
                pass
            page.wait_for_timeout(800)
            last = page.url

            # 若是课程列表页，点进第一门课的视频记录
            nodes = list_sections_from_page(page, base_url=base, log=lambda *_: None)
            if len(nodes) > best_count:
                best_count = len(nodes)
                best_url = last
            if len(nodes) >= 3:
                log(f"[course] 使用入口 {u}（{len(nodes)} 节）")
                return last

            courses = list_enrolled_courses(page, base_url=base, log=lambda *_: None)
            for c in courses[:5]:
                for key in ("study_video", "study_record", "chapter"):
                    cu = c.get(key) or ""
                    if not cu:
                        continue
                    try:
                        log(f"[course] 进入课程 {c.get('title')}: {cu}")
                        page.goto(cu, wait_until="domcontentloaded")
                        page.wait_for_timeout(1000)
                        nodes = list_sections_from_page(
                            page, base_url=base, log=lambda *_: None
                        )
                        last = page.url
                        if len(nodes) > best_count:
                            best_count = len(nodes)
                            best_url = last
                        if len(nodes) >= 3:
                            log(
                                f"[course] 使用课程 {c.get('title')} · {len(nodes)} 节"
                            )
                            return last
                    except Exception as e:
                        log(f"[course] 课程页失败: {e}")
        except Exception as e:
            log(f"[course] 打开失败 {u}: {e}")

    if best_count > 0:
        try:
            page.goto(best_url, wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            log(f"[course] 回退最佳入口 {best_url}（{best_count} 节）")
            return best_url
        except Exception:
            pass
    return last


def _collect_paginated_sections(
    page: Page,
    *,
    base_url: str,
    log: LogFn = print,
    max_pages: int = 12,
) -> list[Section]:
    """当前学习记录表 + 点击下一页合并。"""
    all_sec = list_sections_from_page(page, base_url=base_url, log=log)
    seen = {s.href for s in all_sec}
    for _ in range(max_pages - 1):
        next_el = None
        for sel in (
            "a.page-btn.next:not(.disabled)",
            ".pagebar-bbs a.next:not(.disabled)",
            "a.page-btn[data-url*='page=']",
        ):
            try:
                locs = page.locator(sel)
                for i in range(min(locs.count(), 6)):
                    el = locs.nth(i)
                    cls = el.get_attribute("class") or ""
                    if "disabled" in cls or "first" in cls or "prev" in cls:
                        continue
                    text = (el.inner_text(timeout=300) or "").strip()
                    data_url = el.get_attribute("data-url") or ""
                    if "下一页" in text or "next" in cls or "page=" in data_url:
                        if "上一页" in text or "首页" in text:
                            continue
                        next_el = el
                        if "下一页" in text:
                            break
            except Exception:
                continue
            if next_el is not None and "下一页" in (
                (next_el.inner_text(timeout=200) or "") if next_el else ""
            ):
                break
        if next_el is None:
            break
        try:
            if "disabled" in (next_el.get_attribute("class") or ""):
                break
            next_el.click(timeout=2000)
            page.wait_for_timeout(1200)
            more = list_sections_from_page(page, base_url=base_url, log=lambda *_: None)
            added = 0
            for s in more:
                if s.href not in seen:
                    seen.add(s.href)
                    all_sec.append(s)
                    added += 1
            if added == 0:
                break
            log(f"[course] 分页 +{added}，累计 {len(all_sec)}")
        except Exception:
            break
    return all_sec


def list_pending(
    page: Page,
    cfg: dict,
    progress: ProgressStore,
    *,
    log: LogFn = print,
    videos_only: bool = True,
) -> list[Section]:
    open_course_index(page, cfg, log=log)
    base = str(cfg.get("base_url") or "")
    all_sec = _collect_paginated_sections(page, base_url=base, log=log)

    # 合并 catalog 学习记录行（兼容旧路径）
    try:
        from yinghua.catalog import list_sidebar_sections, list_study_record_rows

        extra = list_study_record_rows(page) + list_sidebar_sections(page)
        origin = origin_of(page.url or base, base)
        seen = {s.href for s in all_sec}
        for it in extra:
            href = abs_url(origin, it.href or "")
            if not href or href in seen:
                continue
            if not is_video_path(href) and "nodeId=" not in href:
                continue
            title = it.title or ""
            if _is_nav_title(title):
                continue
            nid = _node_id_from_href(href, title)
            all_sec.append(
                Section(
                    node_id=nid,
                    title=title[:120],
                    href=href,
                    done_hint=bool(it.done),
                    is_video=True,
                )
            )
            seen.add(href)
    except Exception as e:
        log(f"[course] catalog 合并跳过: {e}")

    course_id = str(
        cfg.get("course_id")
        or extract_course_id(page.url or "")
        or origin_of(page.url or base, base)
    )
    pending: list[Section] = []
    done_n = 0
    for s in all_sec:
        if videos_only and not s.is_video:
            continue
        if s.done_hint:
            done_n += 1
            continue
        if progress.is_node_done(course_id, s.node_id):
            done_n += 1
            continue
        if progress.is_done(progress_key(course_id, s.node_id)):
            done_n += 1
            continue
        pending.append(s)
    log(f"[course] 待办 {len(pending)} / 总计 {len(all_sec)}（页内已学提示 {done_n}）")
    return pending
