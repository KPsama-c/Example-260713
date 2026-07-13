"""本地配置：默认值、加载/保存、与 CLI 合并。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "base_url": "https://cdcas.yuruixxkj.com",
    "course_url": "",
    "course_id": "",
    "headless": False,
    "playback_rate": 1.0,
    "complete_ratio": 0.95,
    "max_watch_sec": 7200,
    "max_videos": 0,
    "wait_login_timeout_sec": 300,
    "screenshot_on_error": True,
    "pause_between_sec": [2, 6],
    "storage_state": "data/storage_state.json",
    "progress_file": "data/progress.json",
    "failed_file": "data/failed.json",
    "captcha": {"enabled": True, "auto_solve": False},
    "exam": {"enabled": False, "auto_submit": False},
    "llm": {
        "enabled": False,
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "endpoint_id": "",
        "api_key": "",
    },
}

_SAVE_KEYS = (
    "base_url",
    "course_url",
    "course_id",
    "headless",
    "playback_rate",
    "complete_ratio",
    "max_watch_sec",
    "max_videos",
    "wait_login_timeout_sec",
    "screenshot_on_error",
    "pause_between_sec",
    "storage_state",
    "progress_file",
    "failed_file",
    "captcha",
    "exam",
    "llm",
)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load_settings(path: Path) -> dict[str, Any]:
    import os

    cfg = dict(DEFAULTS)
    # nested defaults copy
    cfg["captcha"] = dict(DEFAULTS["captcha"])
    cfg["exam"] = dict(DEFAULTS["exam"])
    cfg["llm"] = dict(DEFAULTS["llm"])
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                cfg = _deep_merge(cfg, raw)
        except Exception:
            pass
    # 环境变量优先（勿把密钥写进仓库）
    env_key = (os.environ.get("YINGHUA_LLM_API_KEY") or "").strip()
    if env_key:
        cfg.setdefault("llm", {})["api_key"] = env_key
        cfg["llm"]["enabled"] = True
    return cfg


def save_settings(path: Path, cfg: dict[str, Any]) -> None:
    """写入本地 config。注意：config.yaml 必须在 .gitignore 中，勿提交仓库。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: cfg.get(k, DEFAULTS.get(k)) for k in _SAVE_KEYS}
    # example 文件名时强制清空密钥，避免误写进可提交样例
    if path.name in ("config.example.yaml", "config.example.yml"):
        llm = dict(data.get("llm") or {})
        llm["api_key"] = ""
        data["llm"] = llm
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def base_url_of(cfg: dict[str, Any]) -> str:
    """返回站点 origin（scheme://host[:port]），去掉 path。"""
    from urllib.parse import urlparse

    raw = str(cfg.get("base_url") or DEFAULTS["base_url"]).strip()
    if not raw:
        raw = str(DEFAULTS["base_url"])
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return raw.rstrip("/")


def exam_enabled(cfg: dict[str, Any]) -> bool:
    ex = cfg.get("exam") or {}
    return bool(ex.get("enabled", False))


def exam_auto_submit(cfg: dict[str, Any]) -> bool:
    ex = cfg.get("exam") or {}
    return bool(ex.get("auto_submit", False))


def public_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """给 Web/status 用，剥离密钥。"""
    llm = dict(cfg.get("llm") or {})
    llm.pop("api_key", None)
    if llm.get("endpoint_id"):
        llm["endpoint_id"] = "***" if len(str(llm["endpoint_id"])) > 4 else llm["endpoint_id"]
    return {
        "base_url": base_url_of(cfg),
        "course_url": cfg.get("course_url") or "",
        "course_id": cfg.get("course_id") or "",
        "headless": bool(cfg.get("headless", False)),
        "playback_rate": cfg.get("playback_rate", 1.0),
        "complete_ratio": cfg.get("complete_ratio", 0.95),
        "max_videos": cfg.get("max_videos", 0),
        "exam": {
            "enabled": exam_enabled(cfg),
            "auto_submit": exam_auto_submit(cfg),
        },
        "captcha": dict(cfg.get("captcha") or {}),
        "llm": {
            "enabled": bool(llm.get("enabled", False)),
            "base_url": llm.get("base_url") or "",
            "has_key": bool((cfg.get("llm") or {}).get("api_key")),
        },
    }
