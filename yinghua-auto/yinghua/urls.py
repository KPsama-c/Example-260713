"""英华系站点路径拼装（对齐 chaoxiankeji / 英华部署）。"""

from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse

from yinghua.settings import base_url_of


def join(base: str, path: str) -> str:
    base = (base or "").rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def home_url(cfg: dict) -> str:
    return base_url_of(cfg) + "/"


def study_record_urls(cfg: dict) -> list[str]:
    """候选目录入口（按优先级）。

    真站（cdcas.chaoxiankeji.com）路径：
    - 兴趣学习: /user/index/open?kind=run|finish|all
    - 院校课程: /user/index?kind=run
    - 学习记录(需 courseId): /user/study_record?courseId=
    - 视频记录: /user/study_record/video?courseId=
    - 课程目录: /user/course/chapter?courseId=
    - 节点播放: /user/node?courseId=&chapterId=&nodeId=
    """
    b = base_url_of(cfg)
    cid = str(cfg.get("course_id") or "").strip()
    urls: list[str] = []
    if cid:
        urls.extend(
            [
                join(b, f"/user/study_record/video?courseId={cid}"),
                join(b, f"/user/study_record?courseId={cid}"),
                join(b, f"/user/course/chapter?courseId={cid}"),
                join(b, f"/user/course?courseId={cid}"),
            ]
        )
    urls.extend(
        [
            join(b, "/user/index/open?kind=run"),
            join(b, "/user/index/open?kind=all"),
            join(b, "/user/index/open?kind=finish"),
            join(b, "/user/index/open"),
            join(b, "/user/index?kind=run"),
            join(b, "/user/index?kind=finish"),
            join(b, "/user/study_record"),
            join(b, "/student/course-study-record"),
            join(b, "/user/index"),
            join(b, "/user"),
        ]
    )
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def course_entry_url(cfg: dict) -> str:
    cu = str(cfg.get("course_url") or "").strip()
    if cu:
        return cu
    return study_record_urls(cfg)[0]


def is_video_path(url: str) -> bool:
    u = (url or "").lower()
    return any(
        x in u
        for x in (
            "/user/node",
            "nodeid=",
            "course-study",
            "/node?",
            "/node&",
        )
    )


def is_exam_path(url: str) -> bool:
    u = (url or "").lower()
    return "/user/exam" in u or "exam" in u.split("/")[-1]


def extract_course_id(url: str) -> str:
    try:
        q = parse_qs(urlparse(url or "").query)
        for key in ("courseId", "course_id", "cid"):
            if q.get(key):
                return str(q[key][0])
    except Exception:
        pass
    return ""


def extract_node_id(url: str) -> str:
    try:
        q = parse_qs(urlparse(url or "").query)
        for key in ("nodeId", "node_id", "id"):
            if q.get(key) and key != "id":
                return str(q[key][0])
        if q.get("nodeId"):
            return str(q["nodeId"][0])
    except Exception:
        pass
    return ""
