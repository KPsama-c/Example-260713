"""断点进度 / 失败记录。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yinghua.util import parse_progress_key, progress_key


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class ProgressStore:
    path: Path
    completed: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    course_id: str = ""

    @classmethod
    def load(cls, path: str | Path, *, course_id: str = "") -> "ProgressStore":
        p = Path(path)
        store = cls(path=p, course_id=str(course_id or ""))
        if not p.exists():
            return store
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            store.completed = list(data.get("completed") or [])
            store.meta = dict(data.get("meta") or {})
        except (json.JSONDecodeError, OSError):
            pass
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "completed": self.completed,
            "meta": self.meta,
            "updated_at": _now(),
            "course_id": self.course_id,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_done(self, key: str) -> bool:
        return key in self.completed

    def is_node_done(self, course_id: str, node_id: str) -> bool:
        node_id = str(node_id)
        k = progress_key(course_id, node_id)
        if k in self.completed:
            return True
        if node_id in self.completed:
            return True
        return False

    def mark_done(
        self,
        key: str,
        title: str = "",
        *,
        course_id: str = "",
        node_id: str = "",
    ) -> None:
        cid = str(course_id or self.course_id or "")
        nid = str(node_id or "")
        if cid and nid:
            key = progress_key(cid, nid)
        key = str(key)
        if not key:
            return
        if key not in self.completed:
            self.completed.append(key)
        if title:
            self.meta[key] = {"title": title, "at": _now()}
        self.save()

    def unmark(self, key: str) -> None:
        if key in self.completed:
            self.completed.remove(key)
        self.meta.pop(key, None)
        self.save()

    def clear(self) -> int:
        n = len(self.completed)
        self.completed = []
        self.meta = {}
        self.save()
        return n


@dataclass
class FailedStore:
    path: Path
    items: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.items = list(data.get("items") or [])
            except (json.JSONDecodeError, OSError):
                self.items = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"items": self.items, "updated_at": _now()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def add(self, key: str, title: str = "", reason: str = "") -> None:
        self.items.append(
            {"key": key, "title": title, "reason": reason, "at": _now()}
        )
        # 去重：同 key 只保留最新
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for it in reversed(self.items):
            k = str(it.get("key") or "")
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        self.items = list(reversed(out))[-100:]
        self.save()

    def clear(self) -> int:
        n = len(self.items)
        self.items = []
        self.save()
        return n
