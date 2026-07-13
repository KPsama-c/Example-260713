"""SoftStore 序列化与按课清除。"""

from pathlib import Path

from yuketang.progress import SoftStore


def test_as_dicts_and_clear_classroom(tmp_path: Path):
    p = tmp_path / "soft.json"
    s = SoftStore(p)
    s.add(classroom_id="c1", lesson_id="l1", title="A", local_ratio=0.9)
    s.add(classroom_id="c1", lesson_id="l2", title="B", local_ratio=0.8)
    s.add(classroom_id="c2", lesson_id="l3", title="C", local_ratio=0.7)
    assert len(s.as_dicts()) == 3
    assert len(s.as_dicts("c1")) == 2
    assert s.as_dicts("c1")[0]["lesson_id"] in ("l1", "l2")
    n = s.clear_classroom("c1")
    assert n == 2
    assert len(s.as_dicts()) == 1
    assert s.as_dicts()[0]["classroom_id"] == "c2"


def test_clear_all(tmp_path: Path):
    s = SoftStore(tmp_path / "s.json")
    s.add(classroom_id="c1", lesson_id="x", title="X", local_ratio=0.5)
    assert s.clear() == 1
    assert s.as_dicts() == []
