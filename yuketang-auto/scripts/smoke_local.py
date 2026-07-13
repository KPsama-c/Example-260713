#!/usr/bin/env python3
"""本机回归烟雾（不连雨课堂业务）：doctor + pytest + 关键 import。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    print("== smoke_local ==")
    from yuketang import __version__
    from yuketang.doctor import run_doctor
    from yuketang.jobs import (
        normalize_job_action,
        watch_lesson_batch,
        load_pending_for_classroom,
        STATE,
    )

    print(f"version: {__version__}")
    assert callable(watch_lesson_batch)
    assert callable(load_pending_for_classroom)
    assert normalize_job_action("soft_only") == ("soft", None)
    assert normalize_job_action("all_absent") == ("all", "absent")
    assert STATE is not None

    doc = run_doctor(ROOT)
    print(f"doctor: {'OK' if doc.get('ok') else 'FAIL'} ({len(doc.get('checks') or [])} checks)")
    if not doc.get("ok"):
        for c in doc.get("checks") or []:
            if not c.get("ok"):
                print(f"  !! {c.get('name')}: {c.get('detail')}")
        return 2

    print("pytest …")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line"],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        print("pytest FAIL")
        return r.returncode
    print("smoke_local: all green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
