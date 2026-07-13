"""跨模块小工具。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def origin_of(url: str) -> str:
    p = urlparse(url or "")
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return "https://www.yuketang.cn"


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


def progress_key(classroom_id: str, lesson_id: str) -> str:
    """断点键：课堂隔离，避免多课串号。"""
    c = str(classroom_id or "").strip()
    l = str(lesson_id or "").strip()
    if not l:
        return ""
    if not c:
        return l
    return f"{c}:{l}"


def parse_progress_key(key: str) -> tuple[str | None, str]:
    """返回 (classroom_id|None, lesson_id)。无前缀时 classroom 为 None。"""
    key = str(key or "")
    if ":" in key:
        c, _, rest = key.partition(":")
        if c and rest:
            return c, rest
    return None, key
