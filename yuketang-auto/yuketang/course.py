"""课程目录解析：收集未完成视频叶子。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Locator, Page

from yuketang import selectors as S


@dataclass
class LeafItem:
    key: str
    title: str
    kind: str  # video | other | unknown
    done: bool
    # 用于再次定位点击：文本 + 索引
    text_sample: str
    index: int


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _make_key(title: str, index: int, href: str = "") -> str:
    raw = f"{index}|{title}|{href}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _looks_video(text: str) -> bool:
    t = text.lower()
    if any(h.lower() in t for h in S.NON_VIDEO_HINTS):
        # 若同时带视频词，仍可能是视频标题含「作业」等，保守：非视频优先
        if not any(h.lower() in t for h in S.VIDEO_TYPE_HINTS):
            return False
    if any(h.lower() in t for h in S.VIDEO_TYPE_HINTS):
        return True
    # 雨课堂很多叶子没有「视频」二字，默认当候选视频（由播放页验证）
    return True


def _looks_done(text: str, class_attr: str = "") -> bool:
    blob = f"{text} {class_attr}"
    # 明确未完成优先
    if any(h in blob for h in S.UNDONE_HINTS):
        return False
    if any(h in blob for h in S.DONE_HINTS):
        return True
    # class 常见 finish / done / complete
    low = class_attr.lower()
    if re.search(r"(finish|done|complete|success|learned)", low):
        return True
    if re.search(r"(unfinish|undone|not-start|notstart|learning)", low):
        return False
    return False


def _find_leaf_locator(page: Page) -> Locator | None:
    for sel in S.LEAF_ROW_CANDIDATES:
        loc = page.locator(sel)
        try:
            n = loc.count()
        except Exception:
            continue
        if n > 0:
            return loc
    return None


def collect_leaves(
    page: Page,
    *,
    log: Callable[[str], None] = print,
) -> list[LeafItem]:
    """从当前课程页收集叶子列表。"""
    page.wait_for_timeout(800)
    root = _find_leaf_locator(page)
    if root is None:
        # 兜底：可点击的列表行
        root = page.locator(
            'div[class*="leaf"], li[class*="leaf"], '
            'div[class*="activity"], div[class*="section-item"]'
        )
        if root.count() == 0:
            log("[course] 未找到章节叶子，请用 scripts/dump_page.py 导出页面后改 selectors.py")
            return []

    total = root.count()
    log(f"[course] 候选叶子节点: {total}")
    items: list[LeafItem] = []
    for i in range(total):
        node = root.nth(i)
        try:
            if not node.is_visible():
                continue
            text = _norm(node.inner_text(timeout=2_000))
        except Exception:
            continue
        if not text or len(text) < 2:
            continue
        # 过滤过长块（可能是整章容器）
        if len(text) > 200:
            continue

        try:
            class_attr = node.get_attribute("class") or ""
        except Exception:
            class_attr = ""
        try:
            href = ""
            link = node.locator("a").first
            if link.count():
                href = link.get_attribute("href") or ""
        except Exception:
            href = ""

        is_video = _looks_video(text)
        done = _looks_done(text, class_attr)
        # 子元素 class 再扫一遍
        try:
            html = node.evaluate("el => el.outerHTML")[:800]
            if not done:
                done = _looks_done(text, html)
            if is_video is False:
                pass
            elif any(h in html for h in ("icon-shipin", "icon-video", "video-icon", "type-video")):
                is_video = True
        except Exception:
            html = ""

        title = text.split("\n")[0][:120]
        kind = "video" if is_video else "other"
        # 明显非视频
        if any(h in text for h in S.NON_VIDEO_HINTS) and not any(
            h in text for h in S.VIDEO_TYPE_HINTS
        ):
            kind = "other"

        key = _make_key(title, i, href)
        items.append(
            LeafItem(
                key=key,
                title=title,
                kind=kind,
                done=done,
                text_sample=title,
                index=i,
            )
        )

    videos = [x for x in items if x.kind == "video"]
    undone = [x for x in videos if not x.done]
    log(f"[course] 解析: 共 {len(items)} 项, 视频 {len(videos)}, 未完成视频 {len(undone)}")
    return items


def list_pending_videos(
    page: Page,
    *,
    progress_keys: set[str] | None = None,
    log: Callable[[str], None] = print,
) -> list[LeafItem]:
    items = collect_leaves(page, log=log)
    progress_keys = progress_keys or set()
    pending = [
        x
        for x in items
        if x.kind == "video" and (not x.done) and (x.key not in progress_keys)
    ]
    # 若完成态识别全失败导致 pending 为空但存在视频，退化为「全部视频减断点」
    if not pending:
        videos = [x for x in items if x.kind == "video"]
        pending = [x for x in videos if x.key not in progress_keys]
        if pending and all(x.done for x in videos):
            log("[course] 页面显示视频均已完成")
            return []
        if pending:
            log("[course] 完成态识别不确定，将按断点文件跳过已跑项后尝试剩余视频")
    return pending


def click_leaf(page: Page, item: LeafItem, *, log: Callable[[str], None] = print) -> bool:
    """按标题文本点击叶子。"""
    title = item.text_sample
    # 精确文本
    candidates = [
        page.get_by_text(title, exact=True),
        page.get_by_text(title, exact=False),
        page.locator(f'text="{title}"'),
    ]
    for loc in candidates:
        try:
            target = loc.first
            if target.count() == 0:
                continue
            target.scroll_into_view_if_needed(timeout=5_000)
            target.click(timeout=5_000)
            log(f"[course] 已点击: {title}")
            page.wait_for_timeout(1200)
            return True
        except Exception:
            continue

    # 回退：用当初 index 的宽选择器
    root = _find_leaf_locator(page)
    if root is not None:
        try:
            node = root.nth(item.index)
            node.scroll_into_view_if_needed(timeout=5_000)
            node.click(timeout=5_000)
            log(f"[course] 已点击(index={item.index}): {title}")
            page.wait_for_timeout(1200)
            return True
        except Exception as e:
            log(f"[course] 点击失败: {title} ({e})")
            return False
    log(f"[course] 无法定位: {title}")
    return False
