#!/usr/bin/env python3
"""NarraFork / 脚本用 CLI：list | next | all | stop | status

  python nfctl.py list
  python nfctl.py next
  python nfctl.py status
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yinghua.jobs import STATE, run_automation, start_job_async
from yinghua.settings import load_settings, public_settings

CONFIG_PATH = ROOT / "config.yaml"


def main() -> int:
    ap = argparse.ArgumentParser(description="yinghua-auto nfctl")
    ap.add_argument(
        "command",
        choices=["list", "next", "once", "all", "stop", "status", "settings"],
        help="动作",
    )
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="后台线程跑（stop/status 用）；默认同步",
    )
    args = ap.parse_args()
    cfg = load_settings(Path(args.config))
    cmd = args.command
    if cmd == "once":
        cmd = "next"

    if cmd == "settings":
        data = public_settings(cfg)
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else data)
        return 0

    if cmd == "status":
        snap = STATE.snapshot()
        if args.json:
            print(json.dumps(snap, ensure_ascii=False, indent=2))
        else:
            print(
                f"running={snap['running']} action={snap['action']} "
                f"msg={snap['message']} done={snap['done']} fail={snap['fail']}"
            )
            for line in snap.get("logs", [])[-15:]:
                print(line)
        return 0

    if cmd == "stop":
        ok = STATE.request_cancel()
        msg = "已请求停止" if ok else "当前无运行任务"
        print(json.dumps({"ok": ok, "message": msg}, ensure_ascii=False) if args.json else msg)
        return 0 if ok else 1

    if args.async_mode:
        ok, msg = start_job_async(root=ROOT, cfg=cfg, action=cmd)
        print(json.dumps({"ok": ok, "message": msg}, ensure_ascii=False) if args.json else msg)
        return 0 if ok else 1

    result = run_automation(root=ROOT, cfg=cfg, action=cmd, log=print)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"ok={result.get('ok')} done={result.get('done')} "
            f"fail={result.get('fail')} pending={result.get('pending_count')}"
        )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
