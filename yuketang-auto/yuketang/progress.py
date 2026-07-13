"""断点进度读写。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class ProgressStore:
    path: Path
    completed: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "ProgressStore":
        p = Path(path)
        store = cls(path=p)
        if not p.exists():
            return store
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            store.completed = list(data.get("completed") or [])
            store.meta = dict(data.get("meta") or {})
        except (json.JSONDecodeError, OSError):
            pass
        return store

    def is_done(self, key: str) -> bool:
        return key in self.completed

    def mark_done(self, key: str, title: str = "") -> None:
        if key not in self.completed:
            self.completed.append(key)
        self.meta[key] = {"title": title, "at": _now()}
        self.save()

    def unmark(self, key: str) -> bool:
        """移除断点；返回是否曾存在。"""
        key = str(key)
        if key not in self.completed and key not in self.meta:
            return False
        self.completed = [k for k in self.completed if k != key]
        self.meta.pop(key, None)
        self.save()
        return True

    def clear(self) -> int:
        n = len(self.completed)
        self.completed = []
        self.meta = {}
        self.save()
        return n

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "completed": self.completed,
            "meta": self.meta,
            "updated_at": _now(),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@dataclass
class FailedItem:
    key: str
    title: str
    reason: str
    at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class FailedStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.items: list[FailedItem] = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for it in raw.get("items") or []:
                    self.items.append(
                        FailedItem(
                            key=it.get("key", ""),
                            title=it.get("title", ""),
                            reason=it.get("reason", ""),
                            at=it.get("at", _now()),
                        )
                    )
            except (json.JSONDecodeError, OSError):
                pass

    def add(self, key: str, title: str, reason: str) -> None:
        self.items.append(FailedItem(key=key, title=title, reason=reason))
        self.save()

    def clear(self) -> int:
        n = len(self.items)
        self.items = []
        self.save()
        return n

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": [i.to_dict() for i in self.items],
            "updated_at": _now(),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
