"""最近运行摘要（无隐私正文，仅计数与时间）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX = 10


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def history_path(root: Path) -> Path:
    return Path(root) / "data" / "run_history.json"


def load_run_history(root: Path, *, max_entries: int = _MAX) -> list[dict[str, Any]]:
    """读取最近记录（新→旧）。兼容 items/entries 两种键。"""
    path = history_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("items")
    if raw is None:
        raw = data.get("entries")
    items = [e for e in (raw or []) if isinstance(e, dict)]
    return items[: max(1, int(max_entries))]


def append_run_history(root: Path, entry: dict[str, Any], *, max_entries: int = _MAX) -> None:
    """追加一条摘要；仅保留最近 max_entries 条。"""
    path = history_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = load_run_history(root, max_entries=10_000)
    # 规范化：去掉可能的标题类字段
    clean = {
        "at": _now(),
        "action": str(entry.get("action") or "")[:40],
        "attend_filter": str(entry.get("attend_filter") or "")[:16],
        "classroom_id": str(entry.get("classroom_id") or "")[:32],
        "done": int(entry.get("done") or 0),
        "soft": int(entry.get("soft") or 0),
        "fail": int(entry.get("fail") or 0),
        "cancelled": bool(entry.get("cancelled")),
    }
    items.insert(0, clean)
    items = items[: max(1, int(max_entries))]
    path.write_text(
        json.dumps(
            {"items": items, "updated_at": clean["at"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
