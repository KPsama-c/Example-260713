from pathlib import Path

from yuketang.history import append_run_history, load_run_history


def test_append_and_load(tmp_path: Path):
    root = tmp_path
    append_run_history(
        root,
        {
            "action": "once",
            "attend_filter": "all",
            "done": 1,
            "soft": 0,
            "fail": 0,
            "cancelled": False,
            "classroom_id": "11111",
            "title": "不应写入",
        },
    )
    items = load_run_history(root)
    assert len(items) == 1
    assert items[0]["done"] == 1
    assert items[0]["classroom_id"] == "11111"
    assert "title" not in items[0]


def test_max_ten(tmp_path: Path):
    root = tmp_path
    for i in range(15):
        append_run_history(root, {"action": "all", "done": i, "classroom_id": str(i)})
    items = load_run_history(root)
    assert len(items) == 10
    assert items[0]["done"] == 14  # 最新在前
