from pathlib import Path

from yuketang.doctor import format_doctor_report, run_doctor


def test_doctor_runs(tmp_path: Path):
    # 在临时目录：无 chromium 也可能 fail，但结构应完整
    root = tmp_path
    (root / ".gitignore").write_text("config.yaml\ndata/*\n", encoding="utf-8")
    result = run_doctor(root)
    assert "checks" in result
    names = {c["name"] for c in result["checks"]}
    assert "python" in names
    assert "import:playwright" in names
    text = format_doctor_report(result)
    assert "环境自检" in text
