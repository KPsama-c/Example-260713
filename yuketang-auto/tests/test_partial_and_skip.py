"""partial 续播存储 + 全部跳过本地达线。"""

from pathlib import Path

from yuketang.pending_ops import filter_skip_local_complete, local_complete_ratio_map
from yuketang.progress import PartialStore, SoftItem, SoftStore


class _Item:
    def __init__(self, lesson_id: str, title: str = "") -> None:
        self.lesson_id = lesson_id
        self.title = title or lesson_id


def test_partial_upsert_get_remove(tmp_path: Path):
    store = PartialStore(tmp_path / "partial.json")
    store.upsert(
        classroom_id="c1",
        lesson_id="L1",
        title="课1",
        local_ratio=0.42,
        watched_sec=100,
        total_sec=240,
        segment_time=55,
        finished_keys=["segA"],
        seg_durations={"segA": 60.0},
    )
    it = store.get("c1", "L1")
    assert it is not None
    assert abs(it.local_ratio - 0.42) < 1e-6
    assert it.segment_time == 55
    assert "segA" in it.finished_keys
    # reload
    store2 = PartialStore(tmp_path / "partial.json")
    assert store2.get("c1", "L1") is not None
    assert store2.remove("c1", "L1")
    assert store2.get("c1", "L1") is None


def test_filter_skip_only_soft_at_threshold(tmp_path: Path):
    """仅 SOFT≥阈值才跳过；partial 再高也不跳过（不确定则重看/续看）。"""
    soft = SoftStore(tmp_path / "soft.json")
    soft.items = [
        SoftItem(
            key="c1:a",
            classroom_id="c1",
            lesson_id="a",
            title="A",
            local_ratio=0.70,
        ),
        SoftItem(
            key="c1:b",
            classroom_id="c1",
            lesson_id="b",
            title="B",
            local_ratio=0.50,  # 未达 65%，不跳过
        ),
    ]
    # partial 0.80 无 SOFT → 不跳过，应续看/重看
    partial_ratios = {"c": 0.80, "d": 0.30}
    pending = [_Item("a"), _Item("b"), _Item("c"), _Item("d"), _Item("e")]
    kept, skipped = filter_skip_local_complete(
        pending,
        classroom_id="c1",
        complete_ratio=0.65,
        soft=soft,
        partial_ratios=partial_ratios,
        enabled=True,
    )
    assert [x.lesson_id for x in kept] == ["b", "c", "d", "e"]
    skipped_ids = {x.lesson_id for x, _ in skipped}
    assert skipped_ids == {"a"}


def test_filter_disabled_keeps_all(tmp_path: Path):
    soft = SoftStore(tmp_path / "soft.json")
    soft.items = [
        SoftItem(key="c1:a", classroom_id="c1", lesson_id="a", title="A", local_ratio=0.9)
    ]
    kept, skipped = filter_skip_local_complete(
        [_Item("a"), _Item("b")],
        classroom_id="c1",
        complete_ratio=0.65,
        soft=soft,
        enabled=False,
    )
    assert len(kept) == 2 and skipped == []


def test_local_complete_ratio_map_merges_max():
    soft = SoftStore.__new__(SoftStore)
    soft.items = [
        SoftItem(key="c1:x", classroom_id="c1", lesson_id="x", title="", local_ratio=0.4)
    ]
    m = local_complete_ratio_map(soft, "c1", partial_ratios={"x": 0.55, "y": 0.1})
    assert abs(m["x"] - 0.55) < 1e-9
    assert abs(m["y"] - 0.1) < 1e-9
