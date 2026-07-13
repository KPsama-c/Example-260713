"""跨模块小工具。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse


def origin_of(url: str, fallback: str = "https://cdcas.yuruixxkj.com") -> str:
    p = urlparse(url or "")
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    p2 = urlparse(fallback or "")
    if p2.scheme and p2.netloc:
        return f"{p2.scheme}://{p2.netloc}"
    return fallback.rstrip("/")


def resolve_path(base: Path, p: str | Path) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def fmt_eta(sec: float) -> str:
    if sec <= 0 or sec > 48 * 3600:
        return "-"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s:02d}秒"
    return f"{s}秒"


def progress_key(course_id: str, node_id: str) -> str:
    """断点键：课程隔离。"""
    c = str(course_id or "").strip()
    n = str(node_id or "").strip()
    if not n:
        return ""
    if not c:
        return n
    return f"{c}:{n}"


def parse_progress_key(key: str) -> tuple[str | None, str]:
    key = str(key or "")
    if ":" in key:
        c, _, rest = key.partition(":")
        if c and rest:
            return c, rest
    return None, key


def abs_url(base: str, href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(base.rstrip("/") + "/", href.lstrip("/"))
