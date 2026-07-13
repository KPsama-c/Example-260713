"""soft 动作：从 pending ∩ soft_ids 选目标（纯逻辑片段）。"""


def select_soft_targets(pending_ids: list[str], soft_ids: set[str]) -> list[str]:
    return [x for x in pending_ids if x in soft_ids]


def test_soft_intersection():
    pending = ["a", "b", "c"]
    soft = {"b", "c", "d"}
    assert select_soft_targets(pending, soft) == ["b", "c"]


def test_soft_empty():
    assert select_soft_targets(["a"], set()) == []
