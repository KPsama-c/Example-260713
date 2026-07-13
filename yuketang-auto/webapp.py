#!/usr/bin/env python3
"""雨课堂助手 · 本机 Web 控制台。

  python webapp.py
  浏览器打开 http://127.0.0.1:8765

仅绑定 127.0.0.1，请勿改成 0.0.0.0 暴露到公网。
运行即表示已阅读 DISCLAIMER.md。
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, render_template, request

from yuketang import __version__
from yuketang.jobs import STATE, start_job_async
from yuketang.rate import parse_rate_value
from yuketang.settings import (
    apply_classroom_input,
    has_classroom,
    load_settings,
    resolve_runtime,
    save_settings,
)

TEMPLATE_DIR = ROOT / "webui" / "templates"
CONFIG_PATH = ROOT / "config.yaml"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))


def _public_cfg() -> dict:
    cfg = load_settings(CONFIG_PATH)
    _, cid, _ = resolve_runtime(cfg) if has_classroom(cfg) else ("", "", [])
    return {
        "version": __version__,
        "classroom_id": cid or (cfg.get("classroom_id") or "") or "",
        "course_url": cfg.get("course_url") or "",
        "playback_rate": cfg.get("playback_rate", 1.25),
        "complete_ratio": cfg.get("complete_ratio", 0.65),
        "headless": bool(cfg.get("headless", False)),
    }


@app.get("/")
def index():
    return render_template("index.html", **_public_cfg())


@app.get("/disclaimer")
def disclaimer():
    path = ROOT / "DISCLAIMER.md"
    if not path.exists():
        return "DISCLAIMER.md not found", 404
    # 简单纯文本
    from flask import Response

    return Response(path.read_text(encoding="utf-8"), mimetype="text/plain; charset=utf-8")


@app.get("/api/settings")
def api_get_settings():
    return jsonify({"ok": True, **_public_cfg()})


@app.post("/api/settings")
def api_save_settings():
    data = request.get_json(silent=True) or {}
    cfg = load_settings(CONFIG_PATH)

    raw_id = str(data.get("classroom_id") or "").strip()
    raw_url = str(data.get("course_url") or "").strip()
    if raw_url:
        apply_classroom_input(cfg, raw_url)
    if raw_id:
        apply_classroom_input(cfg, raw_id)

    rate = parse_rate_value(data.get("playback_rate"))
    if rate is not None:
        cfg["playback_rate"] = rate

    try:
        ratio = float(data.get("complete_ratio", cfg.get("complete_ratio", 0.65)))
        if 0.5 <= ratio <= 1.0:
            cfg["complete_ratio"] = ratio
    except (TypeError, ValueError):
        pass

    if "headless" in data:
        cfg["headless"] = bool(data.get("headless"))

    if not has_classroom(cfg):
        return jsonify({"ok": False, "error": "请填写 classroom_id 或有效学习日志 URL"}), 400

    save_settings(CONFIG_PATH, cfg)
    primary, cid, _ = resolve_runtime(cfg)
    return jsonify({
        "ok": True,
        "classroom_id": cid,
        "course_url": primary or cfg.get("course_url"),
        "playback_rate": cfg.get("playback_rate"),
        "complete_ratio": cfg.get("complete_ratio"),
        "headless": cfg.get("headless"),
    })


@app.post("/api/run")
def api_run():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "list").strip().lower()
    if action not in ("list", "once", "all"):
        return jsonify({"ok": False, "message": "action 必须是 list/once/all"}), 400

    cfg = load_settings(CONFIG_PATH)
    if not has_classroom(cfg):
        return jsonify({"ok": False, "message": "请先保存有效课堂配置"}), 400

    ok, msg = start_job_async(root=ROOT, cfg=cfg, action=action)
    code = 200 if ok else 409
    return jsonify({"ok": ok, "message": msg}), code


@app.get("/api/status")
def api_status():
    return jsonify(STATE.snapshot())


def main() -> int:
    ap = argparse.ArgumentParser(description="雨课堂助手 Web 控制台（仅本机）")
    ap.add_argument("--host", default="127.0.0.1", help="默认仅本机 127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("[!] 安全警告: 不建议绑定非本机地址，登录 Cookie 可能被局域网访问。")

    url = f"http://{args.host}:{args.port}/"
    print("=" * 56)
    print(f" 雨课堂 Web 控制台  v{__version__}")
    print(f" 打开: {url}")
    print(" 仅限本机自用 · 见 DISCLAIMER.md")
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
