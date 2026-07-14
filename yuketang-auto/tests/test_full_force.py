"""全量观看 full：列表模式、跳过规则、动作归一。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yuketang.jobs import normalize_job_action
from yuketang.pending_ops import filter_skip_full_force
from yuketang.progress import ProgressStore, SoftItem, SoftStore


class _Item:
    def __init__(self, lesson_id: str, title: str = "") -> None:
        self.lesson_id = lesson_id
        self.title = title or lesson_id
        self.attend_status = False
        self.live_viewed = False


def test_normalize_full_aliases():
    assert normalize_job_action("full") == ("full", None)
    assert normalize_job_action("full_force") == ("full", None)
    assert normalize_job_action("force_all") == ("full", None)


def test_filter_skip_full_by_progress(tmp_path: Path):
    prog = ProgressStore(tmp_path / "p.json")
    prog.mark_done("c1:a", "A", classroom_id="c1", lesson_id="a")
    soft = SoftStore(tmp_path / "s.json")
    pending = [_Item("a"), _Item("b"), _Item("c")]
    kept, skipped = filter_skip_full_force(
        pending,
        classroom_id="c1",
        complete_ratio=0.65,
        progress=prog,
        soft=soft,
    )
    assert [x.lesson_id for x in kept] == ["b", "c"]
    assert len(skipped) == 1
    assert skipped[0][2] == "progress"


def test_filter_skip_full_by_soft(tmp_path: Path):
    prog = ProgressStore(tmp_path / "p.json")
    soft = SoftStore(tmp_path / "s.json")
    soft.items = [
        SoftItem(
            key="c1:b",
            classroom_id="c1",
            lesson_id="b",
            title="B",
            local_ratio=0.70,
        ),
        SoftItem(
            key="c1:c",
            classroom_id="c1",
            lesson_id="c",
            title="C",
            local_ratio=0.40,
        ),
    ]
    pending = [_Item("a"), _Item("b"), _Item("c")]
    kept, skipped = filter_skip_full_force(
        pending,
        classroom_id="c1",
        complete_ratio=0.65,
        progress=prog,
        soft=soft,
    )
    assert [x.lesson_id for x in kept] == ["a", "c"]
    assert len(skipped) == 1
    assert skipped[0][0].lesson_id == "b"
    assert skipped[0][2] == "soft"


def test_filter_skip_full_soft_below_threshold_kept(tmp_path: Path):
    soft = SoftStore(tmp_path / "s.json")
    soft.items = [
        SoftItem(
            key="c1:x",
            classroom_id="c1",
            lesson_id="x",
            title="X",
            local_ratio=0.64,
        ),
    ]
    kept, skipped = filter_skip_full_force(
        [_Item("x")],
        classroom_id="c1",
        complete_ratio=0.65,
        progress=None,
        soft=soft,
    )
    assert len(kept) == 1
    assert skipped == []


def test_filter_skip_full_empty():
    kept, skipped = filter_skip_full_force(
        [],
        classroom_id="c1",
        complete_ratio=0.65,
    )
    assert kept == []
    assert skipped == []
