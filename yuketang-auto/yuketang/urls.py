"""课程 URL 规范化：移动端 /m/ 与桌面端互转。

路径形态：
  /m/v2/course/normalcourse/logs/{course_id}/{classroom_id}
  /v2/web/studentLog/{classroom_id}

注意：两段 ID 时 **classroom_id 是第二段**；studentLog 必须用 classroom_id。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


# 例: /m/v2/course/normalcourse/logs/{course_id}/{classroom_id}
_RE_M_LOGS = re.compile(
    r"/m/v2/course/normalcourse/logs/(\d+)(?:/(\d+))?",
    re.I,
)
_RE_WEB_STUDENT_LOG = re.compile(r"/v2/web/studentLog/(\d+)", re.I)
_RE_WEB_STUDENT_LOG2 = re.compile(r"/v2/web/student-log/(\d+)", re.I)


def _origin(url: str) -> str:
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return "https://www.yuketang.cn"
    return f"{p.scheme}://{p.netloc}"


def parse_ids(url: str) -> dict[str, str | None]:
    """从 URL 或纯数字解析 course_id / classroom_id。"""
    url = (url or "").strip()
    # 纯 classroom_id
    if re.fullmatch(r"\d{5,}", url):
        return {"course_id": None, "classroom_id": url}
    m = _RE_M_LOGS.search(url)
    if m:
        a, b = m.group(1), m.group(2)
        if b:
            return {"course_id": a, "classroom_id": b}
        # 仅一段时当作 classroom_id（兼容）
        return {"course_id": None, "classroom_id": a}

    m2 = _RE_WEB_STUDENT_LOG.search(url) or _RE_WEB_STUDENT_LOG2.search(url)
    if m2:
        return {"course_id": None, "classroom_id": m2.group(1)}

    return {"course_id": None, "classroom_id": None}


def resolve_classroom_id(
    url: str = "",
    classroom_id: str | int | None = None,
) -> str | None:
    if classroom_id is not None and str(classroom_id).strip():
        return str(classroom_id).strip()
    ids = parse_ids(url)
    return ids.get("classroom_id")


def lesson_overview_url(origin: str, lesson_id: str) -> str:
    return f"{origin.rstrip('/')}/m/v2/lesson/student/{lesson_id}/overview"


def lesson_report_url(
    origin: str,
    classroom_id: str,
    lesson_id: str,
    activity_id: str | int,
) -> str:
    return (
        f"{origin.rstrip('/')}/v2/web/student-lesson-report/"
        f"{classroom_id}/{lesson_id}/{activity_id}"
    )


def expand_course_urls(url: str, *, prefer_desktop: bool = True) -> list[str]:
    """
    给定用户 URL，返回按优先级排序的候选打开地址。
    /logs/{course}/{classroom} → 优先 studentLog/{classroom}
    """
    url = (url or "").strip()
    if not url:
        return []

    origin = _origin(url)
    seen: list[str] = []
    out: list[str] = []

    def add(u: str) -> None:
        u = u.strip()
        if u and u not in seen:
            seen.append(u)
            out.append(u)

    m = _RE_M_LOGS.search(url)
    if m:
        course_id, classroom_id = m.group(1), m.group(2)
        # 有两段：第二段才是 classroom
        if classroom_id:
            desktop = [
                f"{origin}/v2/web/studentLog/{classroom_id}",
                f"{origin}/v2/web/student-log/{classroom_id}",
            ]
            # course_id 的 studentLog 常无权限，放最后兜底
            desktop_fallback = [
                f"{origin}/v2/web/studentLog/{course_id}",
            ]
            mobile = [
                url,
                f"{origin}/m/v2/course/normalcourse/logs/{course_id}/{classroom_id}",
            ]
            if prefer_desktop:
                for u in desktop + mobile + desktop_fallback:
                    add(u)
            else:
                for u in mobile + desktop + desktop_fallback:
                    add(u)
            return out

        # 仅一段
        cid = course_id
        desktop = [
            f"{origin}/v2/web/studentLog/{cid}",
            f"{origin}/v2/web/student-log/{cid}",
        ]
        mobile = [url, f"{origin}/m/v2/course/normalcourse/logs/{cid}"]
        seq = desktop + mobile if prefer_desktop else mobile + desktop
        for u in seq:
            add(u)
        return out

    m2 = _RE_WEB_STUDENT_LOG.search(url) or _RE_WEB_STUDENT_LOG2.search(url)
    if m2:
        cid = m2.group(1)
        add(url)
        add(f"{origin}/v2/web/studentLog/{cid}")
        add(f"{origin}/v2/web/student-log/{cid}")
        add(f"{origin}/m/v2/course/normalcourse/logs/{cid}")
        return out

    add(url)
    return out


def primary_course_url(url: str, *, prefer_desktop: bool = True) -> str:
    urls = expand_course_urls(url, prefer_desktop=prefer_desktop)
    return urls[0] if urls else url
