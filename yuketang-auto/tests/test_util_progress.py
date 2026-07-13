"""纯函数/本地存储单测（不连雨课堂）。"""

from __future__ import annotations

from pathlib import Path

from yuketang.progress import ProgressStore, SoftStore
from yuketang.util import fmt_eta, parse_progress_key, progress_key


def test_progress_key():
    assert progress_key("c1", "l1") == "c1:l1"
    assert parse_progress_key("c1:l1") == ("c1", "l1")
    assert parse_progress_key("bare") == (None, "bare")


def test_fmt_eta():
    assert fmt_eta(65) == "1分05秒"
    assert fmt_eta(0) == "-"


def test_progress_namespace_and_lookup(tmp_path: Path):
    p = tmp_path / "progress.json"
    store = ProgressStore.load(p, classroom_id="100")
    store.mark_done("", "T1", classroom_id="100", lesson_id="L1")
    assert "100:L1" in store.completed
    assert store.is_lesson_done("100", "L1")
    assert not store.is_lesson_done("200", "L1")
    keys = store.keys_for_lookup("100")
    assert "L1" in keys and "100:L1" in keys
    keys2 = store.keys_for_lookup("200")
    assert "L1" not in keys2


def test_migrate_bare(tmp_path: Path):
    p = tmp_path / "progress.json"
    p.write_text(
        '{"completed": ["oldL"], "meta": {"oldL": {"title": "x"}}}',
        encoding="utf-8",
    )
    store = ProgressStore.load(p, classroom_id="9", migrate=True)
    assert "9:oldL" in store.completed
    assert "oldL" not in store.completed


def test_soft_store(tmp_path: Path):
    s = SoftStore(tmp_path / "soft.json")
    s.add(classroom_id="1", lesson_id="2", title="t", local_ratio=0.7)
    assert len(s.for_classroom("1")) == 1
    s.remove("1", "2")
    assert s.for_classroom("1") == []


def test_replay_result_bool():
    from yuketang.replay import ReplayResult

    assert ReplayResult(ok=True, platform_confirmed=False)
    assert not ReplayResult(ok=True, cancelled=True)
    assert not ReplayResult(ok=False)
