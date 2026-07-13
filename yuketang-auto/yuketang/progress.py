"""断点进度 / SOFT / 失败记录读写。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yuketang.util import parse_progress_key, progress_key


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class ProgressStore:
    path: Path
    completed: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    classroom_id: str = ""

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        classroom_id: str = "",
        migrate: bool = True,
    ) -> "ProgressStore":
        p = Path(path)
        store = cls(path=p, classroom_id=str(classroom_id or ""))
        if not p.exists():
            return store
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            store.completed = list(data.get("completed") or [])
            store.meta = dict(data.get("meta") or {})
        except (json.JSONDecodeError, OSError):
            pass
        if migrate and store.classroom_id:
            store.migrate_to_namespaced(store.classroom_id)
        return store

    def keys_for_lookup(self, classroom_id: str | None = None) -> set[str]:
        """list_pending 匹配集：本课 namespaced + 裸 lesson_id（不含其它课）。"""
        cid = str(classroom_id or self.classroom_id or "")
        out: set[str] = set()
        for k in self.completed:
            c, lid = parse_progress_key(k)
            if not lid:
                continue
            if c is None:
                # 旧裸键：默认归属当前课堂
                out.add(lid)
                if cid:
                    out.add(progress_key(cid, lid))
            elif not cid or c == cid:
                out.add(lid)
                out.add(k)
            # 其它课堂的 namespaced 键：忽略
        return out

    def is_done(self, key: str) -> bool:
        return key in self.completed

    def is_lesson_done(self, classroom_id: str, lesson_id: str) -> bool:
        lesson_id = str(lesson_id)
        k = progress_key(classroom_id, lesson_id)
        if k in self.completed:
            return True
        if lesson_id in self.completed:
            # 裸键：若存在其它课的同 lesson namespaced，则不算本课完成
            for x in self.completed:
                c, lid = parse_progress_key(x)
                if lid == lesson_id and c and c != str(classroom_id):
                    return False
            return True
        return False

    def mark_done(
        self,
        key: str,
        title: str = "",
        *,
        classroom_id: str = "",
        lesson_id: str = "",
    ) -> None:
        """优先写入 namespaced 键；同时清理同课裸键避免重复。"""
        cid = str(classroom_id or self.classroom_id or "")
        lid = str(lesson_id or "")
        if not key and cid and lid:
            key = progress_key(cid, lid)
        key = str(key)
        if cid and lid:
            key = progress_key(cid, lid)
            # 去掉同课裸 lesson_id
            if lid in self.completed:
                self.completed = [x for x in self.completed if x != lid]
            self.meta.pop(lid, None)
        if key not in self.completed:
            self.completed.append(key)
        self.meta[key] = {"title": title, "at": _now(), "classroom_id": cid or None}
        self.save()

    def unmark(self, key: str) -> bool:
        key = str(key)
        c, lid = parse_progress_key(key)
        removed = False
        targets = {key}
        if lid:
            targets.add(lid)
            if c:
                targets.add(progress_key(c, lid))
            elif self.classroom_id:
                targets.add(progress_key(self.classroom_id, lid))
        new_completed = [k for k in self.completed if k not in targets]
        if len(new_completed) != len(self.completed):
            removed = True
        self.completed = new_completed
        for t in targets:
            if t in self.meta:
                self.meta.pop(t, None)
                removed = True
        if removed:
            self.save()
        return removed

    def unmark_lesson(self, classroom_id: str, lesson_id: str) -> bool:
        return self.unmark(progress_key(classroom_id, lesson_id))

    def migrate_to_namespaced(self, classroom_id: str) -> int:
        """将裸 lesson_id 迁为 classroom:lesson（仅当前课堂语境）。返回迁移条数。"""
        cid = str(classroom_id or "")
        if not cid:
            return 0
        changed = 0
        new_list: list[str] = []
        new_meta: dict[str, Any] = {}
        seen: set[str] = set()
        for k in self.completed:
            c, lid = parse_progress_key(k)
            if c is None and lid:
                nk = progress_key(cid, lid)
                if nk not in seen:
                    new_list.append(nk)
                    seen.add(nk)
                old_m = self.meta.get(k) or self.meta.get(nk) or {}
                new_meta[nk] = {**old_m, "migrated_from": k, "at": old_m.get("at") or _now()}
                changed += 1
            else:
                if k not in seen:
                    new_list.append(k)
                    seen.add(k)
                if k in self.meta:
                    new_meta[k] = self.meta[k]
        # 保留其它 meta
        for mk, mv in self.meta.items():
            if mk not in new_meta and mk not in self.completed:
                new_meta[mk] = mv
        if changed:
            self.completed = new_list
            self.meta = new_meta
            self.save()
        return changed

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
            "version": 2,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@dataclass
class SoftItem:
    key: str
    classroom_id: str
    lesson_id: str
    title: str
    local_ratio: float
    at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SoftStore:
    """本地达标但平台未确认的课，待事后对账。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.items: list[SoftItem] = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for it in raw.get("items") or []:
                    self.items.append(
                        SoftItem(
                            key=str(it.get("key") or ""),
                            classroom_id=str(it.get("classroom_id") or ""),
                            lesson_id=str(it.get("lesson_id") or ""),
                            title=str(it.get("title") or ""),
                            local_ratio=float(it.get("local_ratio") or 0),
                            at=str(it.get("at") or _now()),
                        )
                    )
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass

    def add(
        self,
        *,
        classroom_id: str,
        lesson_id: str,
        title: str,
        local_ratio: float,
    ) -> None:
        key = progress_key(classroom_id, lesson_id)
        self.items = [
            x
            for x in self.items
            if not (x.lesson_id == lesson_id and x.classroom_id == classroom_id)
        ]
        self.items.append(
            SoftItem(
                key=key,
                classroom_id=str(classroom_id),
                lesson_id=str(lesson_id),
                title=title,
                local_ratio=float(local_ratio),
            )
        )
        self.save()

    def remove(self, classroom_id: str, lesson_id: str) -> bool:
        n0 = len(self.items)
        self.items = [
            x
            for x in self.items
            if not (x.classroom_id == classroom_id and x.lesson_id == lesson_id)
        ]
        if len(self.items) != n0:
            self.save()
            return True
        return False

    def for_classroom(self, classroom_id: str) -> list[SoftItem]:
        cid = str(classroom_id)
        return [x for x in self.items if x.classroom_id == cid]

    def as_dicts(self, classroom_id: str | None = None) -> list[dict[str, Any]]:
        """序列化 soft 项；classroom_id 为空则返回全部。"""
        items = (
            self.for_classroom(classroom_id)
            if classroom_id
            else list(self.items)
        )
        return [i.to_dict() for i in items]

    def clear(self) -> int:
        n = len(self.items)
        self.items = []
        self.save()
        return n

    def clear_classroom(self, classroom_id: str) -> int:
        """清除某课堂的 soft 项，返回删除条数。"""
        cid = str(classroom_id)
        n0 = len(self.items)
        self.items = [x for x in self.items if x.classroom_id != cid]
        n = n0 - len(self.items)
        if n:
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


@dataclass
class PartialItem:
    """单节中断进度（真实播放观测值，用于续播 seek，非伪造心跳）。"""

    key: str
    classroom_id: str
    lesson_id: str
    title: str
    local_ratio: float
    watched_sec: float = 0.0
    total_sec: float = 0.0
    segment_time: float = 0.0
    segment_duration: float = 0.0
    finished_keys: list[str] = field(default_factory=list)
    seg_durations: dict[str, float] = field(default_factory=dict)
    src_suffix: str = ""
    at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PartialStore:
    """未达 complete_ratio 的中断进度；达线后一般转 soft 并清除。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.items: list[PartialItem] = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for it in raw.get("items") or []:
                    fk = it.get("finished_keys") or []
                    sd = it.get("seg_durations") or {}
                    if not isinstance(sd, dict):
                        sd = {}
                    self.items.append(
                        PartialItem(
                            key=str(it.get("key") or ""),
                            classroom_id=str(it.get("classroom_id") or ""),
                            lesson_id=str(it.get("lesson_id") or ""),
                            title=str(it.get("title") or ""),
                            local_ratio=float(it.get("local_ratio") or 0),
                            watched_sec=float(it.get("watched_sec") or 0),
                            total_sec=float(it.get("total_sec") or 0),
                            segment_time=float(it.get("segment_time") or 0),
                            segment_duration=float(it.get("segment_duration") or 0),
                            finished_keys=[str(x) for x in fk],
                            seg_durations={str(k): float(v) for k, v in sd.items()},
                            src_suffix=str(it.get("src_suffix") or ""),
                            at=str(it.get("at") or _now()),
                        )
                    )
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass

    def get(self, classroom_id: str, lesson_id: str) -> PartialItem | None:
        cid, lid = str(classroom_id), str(lesson_id)
        for x in self.items:
            if x.classroom_id == cid and x.lesson_id == lid:
                return x
        return None

    def upsert(
        self,
        *,
        classroom_id: str,
        lesson_id: str,
        title: str,
        local_ratio: float,
        watched_sec: float = 0.0,
        total_sec: float = 0.0,
        segment_time: float = 0.0,
        segment_duration: float = 0.0,
        finished_keys: list[str] | None = None,
        seg_durations: dict[str, float] | None = None,
        src_suffix: str = "",
    ) -> None:
        key = progress_key(classroom_id, lesson_id)
        self.items = [
            x
            for x in self.items
            if not (x.classroom_id == str(classroom_id) and x.lesson_id == str(lesson_id))
        ]
        self.items.append(
            PartialItem(
                key=key,
                classroom_id=str(classroom_id),
                lesson_id=str(lesson_id),
                title=title,
                local_ratio=float(local_ratio),
                watched_sec=float(watched_sec),
                total_sec=float(total_sec),
                segment_time=float(segment_time),
                segment_duration=float(segment_duration),
                finished_keys=list(finished_keys or []),
                seg_durations=dict(seg_durations or {}),
                src_suffix=str(src_suffix or ""),
            )
        )
        self.save()

    def remove(self, classroom_id: str, lesson_id: str) -> bool:
        n0 = len(self.items)
        self.items = [
            x
            for x in self.items
            if not (x.classroom_id == str(classroom_id) and x.lesson_id == str(lesson_id))
        ]
        if len(self.items) != n0:
            self.save()
            return True
        return False

    def for_classroom(self, classroom_id: str) -> list[PartialItem]:
        cid = str(classroom_id)
        return [x for x in self.items if x.classroom_id == cid]

    def local_ratio_map(self, classroom_id: str) -> dict[str, float]:
        return {x.lesson_id: float(x.local_ratio) for x in self.for_classroom(classroom_id)}

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
            "version": 1,
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
