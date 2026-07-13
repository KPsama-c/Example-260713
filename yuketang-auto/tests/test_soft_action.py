"""soft 动作：白名单归一 + pending ∩ soft 选目标。"""

from pathlib import Path

import pytest

from yuketang.jobs import normalize_job_action, select_soft_targets
from yuketang.progress import SoftItem, SoftStore


class _Item:
    def __init__(self, lesson_id: str) -> None:
        self.lesson_id = lesson_id


def test_normalize_soft_aliases():
    assert normalize_job_action("soft") == ("soft", None)
    assert normalize_job_action("soft_only") == ("soft", None)
    assert normalize_job_action("retry_soft") == ("soft", None)
    assert normalize_job_action("all_absent") == ("all", "absent")
    assert normalize_job_action("list") == ("list", None)


def test_normalize_rejects_junk():
    with pytest.raises(ValueError, match="未知动作"):
        normalize_job_action("hack")


def test_select_soft_targets(tmp_path: Path):
    soft = SoftStore(tmp_path / "soft.json")
    soft.items = [
        SoftItem(
            key="c1:b",
            classroom_id="c1",
            lesson_id="b",
            title="B",
            local_ratio=0.9,
        ),
        SoftItem(
            key="c1:c",
            classroom_id="c1",
            lesson_id="c",
            title="C",
            local_ratio=0.8,
        ),
        SoftItem(
            key="c1:d",
            classroom_id="c1",
            lesson_id="d",
            title="D",
            local_ratio=0.7,
        ),
    ]
    pending = [_Item("a"), _Item("b"), _Item("c")]
    out = select_soft_targets(pending, soft, "c1")
    assert [x.lesson_id for x in out] == ["b", "c"]


def test_select_soft_empty(tmp_path: Path):
    soft = SoftStore(tmp_path / "soft.json")
    assert select_soft_targets([_Item("a")], soft, "c1") == []
