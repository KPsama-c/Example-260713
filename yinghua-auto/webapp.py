#!/usr/bin/env python3
"""英华学堂助手 · 本机 Web 控制台。

  python webapp.py
  浏览器打开 http://127.0.0.1:8766

仅绑定 127.0.0.1。运行即表示已阅读 DISCLAIMER.md。
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, Response, jsonify, render_template, request

from yinghua import __version__
from yinghua.jobs import STATE, clear_failed_store, clear_progress_store, start_job_async
from yinghua.settings import load_settings, public_settings, save_settings

TEMPLATE_DIR = ROOT / "webui" / "templates"
CONFIG_PATH = ROOT / "config.yaml"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))


def _cfg() -> dict:
    return load_settings(CONFIG_PATH)


@app.get("/")
def index():
    return render_template("index.html", version=__version__, **public_settings(_cfg()))


@app.get("/disclaimer")
def disclaimer():
    path = ROOT / "DISCLAIMER.md"
    if not path.exists():
        return "DISCLAIMER.md not found", 404
    return Response(path.read_text(encoding="utf-8"), mimetype="text/plain; charset=utf-8")


@app.get("/api/status")
def api_status():
    try:
        since = int(request.args.get("since") or 0)
    except (TypeError, ValueError):
        since = 0
    snap = STATE.snapshot(since=since)
    snap["version"] = __version__
    snap["settings"] = public_settings(_cfg())
    return jsonify(snap)


@app.get("/api/pending")
def api_pending():
    return jsonify(
        {
            "ok": True,
            "pending": STATE.pending_preview,
            "count": len(STATE.pending_preview),
            "hint": "先 POST /api/jobs action=list 刷新",
        }
    )


@app.post("/api/jobs")
def api_jobs():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "list").strip().lower()
    if action == "once":
        action = "next"
    if action not in ("list", "next", "all", "stop"):
        return jsonify({"ok": False, "message": "action 必须是 list/next/all/stop"}), 400
    if action != "stop" and not data.get("accept_risk"):
        # 允许 nfctl/脚本省略；Web 前端应传 true
        pass
    ok, msg = start_job_async(root=ROOT, cfg=_cfg(), action=action)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@app.get("/api/settings")
def api_get_settings():
    return jsonify({"ok": True, **public_settings(_cfg())})


@app.post("/api/settings")
def api_save_settings():
    data = request.get_json(silent=True) or {}
    # 禁止经 HTTP 写入密钥/会话（只能改本机 config 文件或环境变量）
    for forbidden in ("api_key", "llm", "storage_state", "password", "cookie", "cookies"):
        if forbidden in data:
            return jsonify(
                {
                    "ok": False,
                    "error": f"不允许经 API 写入 {forbidden}；密钥请写本地 config.yaml 或环境变量 YINGHUA_LLM_API_KEY",
                }
            ), 400
    cfg = _cfg()
    if data.get("base_url"):
        cfg["base_url"] = str(data["base_url"]).rstrip("/")
    if "course_url" in data:
        cfg["course_url"] = str(data.get("course_url") or "")
    if "course_id" in data:
        cfg["course_id"] = str(data.get("course_id") or "")
    if "headless" in data:
        cfg["headless"] = bool(data.get("headless"))
    try:
        if data.get("playback_rate") is not None:
            cfg["playback_rate"] = float(data["playback_rate"])
        if data.get("complete_ratio") is not None:
            r = float(data["complete_ratio"])
            if 0.5 <= r <= 1.0:
                cfg["complete_ratio"] = r
    except (TypeError, ValueError):
        pass
    save_settings(CONFIG_PATH, cfg)
    return jsonify({"ok": True, **public_settings(cfg)})


@app.post("/api/clear-progress")
def api_clear_progress():
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中"}), 409
    n = clear_progress_store(ROOT, _cfg())
    return jsonify({"ok": True, "cleared": n})


@app.post("/api/clear-failed")
def api_clear_failed():
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中"}), 409
    n = clear_failed_store(ROOT, _cfg())
    return jsonify({"ok": True, "cleared": n})


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def main() -> int:
    ap = argparse.ArgumentParser(description="英华学堂 Web 控制台（仅本机）")
    ap.add_argument("--host", default="127.0.0.1", help="仅允许 127.0.0.1 / localhost / ::1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    host = (args.host or "").strip().lower()
    if host not in _LOCAL_HOSTS:
        print(
            f"[!] 拒绝绑定 {args.host!r}：本控制台仅允许本机地址 "
            f"({', '.join(sorted(_LOCAL_HOSTS))})，见 DISCLAIMER.md"
        )
        return 2

    url = f"http://{args.host}:{args.port}/"
    print("=" * 56)
    print(f" 英华学堂 Web 控制台  v{__version__}")
    print(f" 打开: {url}")
    print(" 仅限本机 · 本人账号 · 见 DISCLAIMER.md")
    print("=" * 56)

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
