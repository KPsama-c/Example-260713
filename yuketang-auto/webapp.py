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
from yuketang.browser import BrowserSession
from yuketang.classrooms import fetch_joined_classrooms, rooms_to_dicts
from yuketang.doctor import run_doctor
from yuketang.history import load_run_history
from yuketang.jobs import (
    STATE,
    clear_failed_store,
    clear_progress_store,
    start_job_async,
)
from yuketang.progress import SoftStore
from yuketang.rate import parse_rate_value
from yuketang.settings import (
    activate_profile,
    apply_classroom_input,
    delete_profile,
    has_classroom,
    list_profiles,
    load_settings,
    resolve_runtime,
    save_settings,
    upsert_profile,
)
from yuketang.util import resolve_path

TEMPLATE_DIR = ROOT / "webui" / "templates"
CONFIG_PATH = ROOT / "config.yaml"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))


def _public_cfg() -> dict:
    cfg = load_settings(CONFIG_PATH)
    _, cid, _ = resolve_runtime(cfg) if has_classroom(cfg) else ("", "", [])
    profiles = list_profiles(cfg)
    return {
        "version": __version__,
        "classroom_id": cid or (cfg.get("classroom_id") or "") or "",
        "course_url": cfg.get("course_url") or "",
        "playback_rate": cfg.get("playback_rate", 1.25),
        "complete_ratio": cfg.get("complete_ratio", 0.65),
        "attend_filter": cfg.get("attend_filter", "all") or "all",
        "headless": bool(cfg.get("headless", False)),
        "profiles": profiles,
        "active_profile": str(cfg.get("active_profile") or "") or "",
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

    af = str(data.get("attend_filter") or cfg.get("attend_filter") or "all").strip().lower()
    if af in ("all", "absent", "present"):
        cfg["attend_filter"] = af

    if not has_classroom(cfg):
        return jsonify({"ok": False, "error": "请填写 classroom_id 或有效学习日志 URL"}), 400

    # 保存时写入/更新配置档
    _, cid, _ = resolve_runtime(cfg)
    if cid:
        profile_name = str(data.get("profile_name") or "").strip() or str(
            cfg.get("active_profile") or cid
        )
        upsert_profile(
            cfg,
            classroom_id=cid,
            name=profile_name,
            course_url=str(cfg.get("course_url") or ""),
            activate=True,
        )

    save_settings(CONFIG_PATH, cfg)
    primary, cid, _ = resolve_runtime(cfg)
    return jsonify({
        "ok": True,
        "classroom_id": cid,
        "course_url": primary or cfg.get("course_url"),
        "playback_rate": cfg.get("playback_rate"),
        "complete_ratio": cfg.get("complete_ratio"),
        "attend_filter": cfg.get("attend_filter", "all"),
        "headless": cfg.get("headless"),
        "profiles": list_profiles(cfg),
        "active_profile": str(cfg.get("active_profile") or ""),
    })


@app.post("/api/profile/activate")
def api_profile_activate():
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中，请先停止再切换课堂"}), 409
    data = request.get_json(silent=True) or {}
    key = str(data.get("key") or data.get("classroom_id") or data.get("name") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "缺少 key（name 或 classroom_id）"}), 400
    cfg = load_settings(CONFIG_PATH)
    if not activate_profile(cfg, key):
        return jsonify({"ok": False, "error": f"未找到配置档: {key}"}), 404
    save_settings(CONFIG_PATH, cfg)
    return jsonify({"ok": True, **_public_cfg(), "message": f"已切换到 {cfg.get('active_profile')}"})


@app.post("/api/profile/delete")
def api_profile_delete():
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中，请先停止"}), 409
    data = request.get_json(silent=True) or {}
    key = str(data.get("key") or data.get("classroom_id") or data.get("name") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "缺少 key（name 或 classroom_id）"}), 400
    cfg = load_settings(CONFIG_PATH)
    if not delete_profile(cfg, key):
        return jsonify({"ok": False, "error": f"未找到配置档: {key}"}), 404
    save_settings(CONFIG_PATH, cfg)
    return jsonify({"ok": True, **_public_cfg(), "message": f"已删除配置档 {key}"})


@app.get("/api/history")
def api_history():
    items = load_run_history(ROOT)
    return jsonify({"ok": True, "items": items})


@app.get("/api/doctor")
def api_doctor():
    """本机环境自检（不连雨课堂业务）。"""
    result = run_doctor(ROOT)
    return jsonify({"ok": bool(result.get("ok")), **result})


@app.get("/api/soft")
def api_soft_list():
    """本地达标但平台未确认的课（soft.json）。"""
    cfg = load_settings(CONFIG_PATH)
    soft_path = resolve_path(ROOT, cfg.get("soft_file", "data/soft.json"))
    store = SoftStore(soft_path)
    want_all = str(request.args.get("all") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    cid = ""
    if not want_all and has_classroom(cfg):
        _, cid, _ = resolve_runtime(cfg)
        cid = str(cid or "")
    items = store.as_dicts(cid if cid and not want_all else None)
    return jsonify({
        "ok": True,
        "classroom_id": cid or None,
        "count": len(items),
        "items": items,
    })


@app.post("/api/soft/clear")
def api_soft_clear():
    """清除 soft 记录（不清除断点）。body: {all?: bool} 默认仅当前课堂。"""
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中，请先停止"}), 409
    data = request.get_json(silent=True) or {}
    cfg = load_settings(CONFIG_PATH)
    soft_path = resolve_path(ROOT, cfg.get("soft_file", "data/soft.json"))
    store = SoftStore(soft_path)
    clear_all = bool(data.get("all"))
    if clear_all:
        n = store.clear()
        return jsonify({"ok": True, "cleared": n, "message": f"已清除全部 SOFT {n} 条"})
    if not has_classroom(cfg):
        return jsonify({"ok": False, "error": "请先配置课堂，或传 all=true"}), 400
    _, cid, _ = resolve_runtime(cfg)
    if not cid:
        return jsonify({"ok": False, "error": "无法解析 classroom_id"}), 400
    n = store.clear_classroom(str(cid))
    return jsonify({
        "ok": True,
        "cleared": n,
        "classroom_id": str(cid),
        "message": f"已清除本课 SOFT {n} 条",
    })


@app.post("/api/logs/clear")
def api_logs_clear():
    STATE.clear_display_logs()
    return jsonify({"ok": True, "message": "已清空界面日志"})


@app.post("/api/profile/upsert")
def api_profile_upsert():
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中，请先停止"}), 409
    data = request.get_json(silent=True) or {}
    cid = str(data.get("classroom_id") or "").strip()
    if not cid:
        return jsonify({"ok": False, "error": "缺少 classroom_id"}), 400
    name = str(data.get("name") or cid).strip()
    url = str(data.get("course_url") or "").strip()
    activate = bool(data.get("activate", True))
    cfg = load_settings(CONFIG_PATH)
    upsert_profile(
        cfg,
        classroom_id=cid,
        name=name,
        course_url=url,
        activate=activate,
    )
    save_settings(CONFIG_PATH, cfg)
    return jsonify({"ok": True, **_public_cfg(), "message": f"已保存配置档 {name}"})


@app.post("/api/run")
def api_run():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "list").strip().lower()
    # 兼容简写
    attend_filter = data.get("attend_filter")
    if action in ("all_absent", "list_absent", "once_absent"):
        attend_filter = "absent"
        action = action.replace("_absent", "")
    if action in ("soft_only", "retry_soft"):
        action = "soft"
    if action not in ("list", "once", "all", "selected", "soft"):
        return jsonify({
            "ok": False,
            "message": "action 必须是 list/once/all/selected/soft",
        }), 400

    # 观看类任务需前端勾选免责（list 仅刷新待办也要求，强化知情）
    if not data.get("accept_risk"):
        return jsonify({"ok": False, "message": "请先勾选同意免责声明"}), 400

    cfg = load_settings(CONFIG_PATH)
    if not has_classroom(cfg):
        return jsonify({"ok": False, "message": "请先保存有效课堂配置"}), 400

    if attend_filter is None:
        attend_filter = cfg.get("attend_filter", "all")
    raw_ids = data.get("lesson_ids") or []
    lesson_ids = [str(x) for x in raw_ids if x] if isinstance(raw_ids, list) else []
    if action == "selected" and not lesson_ids:
        return jsonify({"ok": False, "message": "请先勾选要观看的课程"}), 400
    ok, msg = start_job_async(
        root=ROOT,
        cfg=cfg,
        action=action,
        attend_filter=str(attend_filter),
        lesson_ids=lesson_ids or None,
    )
    code = 200 if ok else 409
    return jsonify({"ok": ok, "message": msg}), code


@app.get("/api/status")
def api_status():
    try:
        since = int(request.args.get("since") or 0)
    except (TypeError, ValueError):
        since = 0
    return jsonify(STATE.snapshot(since=since))


@app.post("/api/cancel")
def api_cancel():
    ok = STATE.request_cancel()
    if ok:
        return jsonify({"ok": True, "message": "已请求停止，将在当前检查点退出"})
    return jsonify({"ok": False, "message": "当前没有运行中的任务"}), 409


@app.post("/api/clear-progress")
def api_clear_progress():
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中，请先停止"}), 409
    cfg = load_settings(CONFIG_PATH)
    n = clear_progress_store(ROOT, cfg)
    return jsonify({"ok": True, "message": f"已清除 {n} 条本地断点", "cleared": n})


@app.post("/api/clear-failed")
def api_clear_failed():
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中，请先停止"}), 409
    cfg = load_settings(CONFIG_PATH)
    n = clear_failed_store(ROOT, cfg)
    return jsonify({"ok": True, "message": f"已清除 {n} 条失败记录", "cleared": n})


@app.get("/api/classrooms")
def api_classrooms():
    """用已保存登录态拉取「我的班级」列表（短暂打开浏览器）。"""
    if STATE.running:
        return jsonify({"ok": False, "error": "任务运行中，请稍后再试"}), 409

    cfg = load_settings(CONFIG_PATH)
    storage = ROOT / str(cfg.get("storage_state") or "data/storage_state.json")
    if not storage.is_absolute():
        storage = (ROOT / storage).resolve()
    if not storage.exists():
        return jsonify({
            "ok": False,
            "error": "尚未登录。请先填写任意课堂并「刷新待办」完成一次登录。",
            "classrooms": [],
        }), 400

    try:
        with BrowserSession(headless=True, storage_state=storage) as session:
            page = session.page
            assert page is not None
            page.goto(
                "https://www.yuketang.cn/v2/web/index",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(1500)
            rooms = fetch_joined_classrooms(page, log=print)
            session.save_state()
    except Exception as e:
        return jsonify({"ok": False, "error": f"拉取班级失败: {e}", "classrooms": []}), 500

    return jsonify({
        "ok": True,
        "classrooms": rooms_to_dicts(rooms),
        "message": f"共 {len(rooms)} 个班级",
    })


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
