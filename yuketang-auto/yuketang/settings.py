"""本地配置：默认值、加载/保存、与 CLI 合并。

优先级：CLI / 本次交互 > config.yaml > DEFAULTS
config.yaml 可选；不存在时用默认值 + 向导。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from yuketang.urls import expand_course_urls, resolve_classroom_id

DEFAULTS: dict[str, Any] = {
    "course_url": "",
    "classroom_id": None,
    "prefer_desktop": True,
    "playback_rate": 1.25,
    "playback_rate_min": 0.5,
    "playback_rate_max": 3.0,
    "headless": False,
    "complete_ratio": 0.65,
    "attend_filter": "all",  # all | absent | present
    "max_watch_sec": 7200,
    "storage_state": "data/storage_state.json",
    "progress_file": "data/progress.json",
    "failed_file": "data/failed.json",
    "max_videos": 0,
    "wait_login_timeout_sec": 180,
    "screenshot_on_error": True,
    "pause_between_sec": [2, 6],
}

_SAVE_KEYS = (
    "course_url",
    "classroom_id",
    "prefer_desktop",
    "playback_rate",
    "playback_rate_min",
    "playback_rate_max",
    "headless",
    "complete_ratio",
    "attend_filter",
    "max_watch_sec",
    "storage_state",
    "progress_file",
    "failed_file",
    "max_videos",
    "wait_login_timeout_sec",
    "screenshot_on_error",
    "pause_between_sec",
)

_RE_DIGITS = re.compile(r"^\d{5,}$")


def load_settings(path: Path) -> dict[str, Any]:
    """加载配置；文件不存在或为空则返回 DEFAULTS 副本。"""
    cfg = dict(DEFAULTS)
    if not path.exists():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for k, v in raw.items():
        if v is not None:
            cfg[k] = v
    return cfg


def is_placeholder_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return True
    return "请替换" in u or "your_" in u.lower() or "classroom_id" in u and "http" not in u


def has_classroom(cfg: dict[str, Any]) -> bool:
    cid = cfg.get("classroom_id")
    if cid is not None and str(cid).strip() and str(cid).strip().lower() != "null":
        return True
    url = str(cfg.get("course_url") or "")
    if is_placeholder_url(url):
        return False
    return bool(resolve_classroom_id(url, None))


def apply_classroom_input(cfg: dict[str, Any], raw: str) -> dict[str, Any]:
    """把用户输入的 URL 或纯数字 ID 写入 cfg（就地并返回）。"""
    raw = (raw or "").strip()
    if not raw:
        return cfg
    if _RE_DIGITS.match(raw):
        cfg["classroom_id"] = raw
        cfg["course_url"] = f"https://www.yuketang.cn/v2/web/studentLog/{raw}"
        return cfg
    # URL 或含 ID 的字符串
    cid = resolve_classroom_id(raw, None)
    if cid:
        cfg["classroom_id"] = cid
        # 保留用户原始 URL；若只是一段路径也补全
        if raw.startswith("http"):
            cfg["course_url"] = raw
        else:
            cfg["course_url"] = f"https://www.yuketang.cn/v2/web/studentLog/{cid}"
    else:
        # 无法解析时仍写入，后续报错
        cfg["course_url"] = raw if raw.startswith("http") else cfg.get("course_url") or ""
    return cfg


def resolve_runtime(cfg: dict[str, Any]) -> tuple[str, str, list[str]]:
    """返回 (course_url 主入口, classroom_id, 候选 URL 列表)。"""
    prefer = bool(cfg.get("prefer_desktop", True))
    explicit = cfg.get("classroom_id")
    if explicit is not None and str(explicit).strip() and str(explicit).lower() != "null":
        cid = str(explicit).strip()
    else:
        cid = resolve_classroom_id(str(cfg.get("course_url") or ""), None) or ""

    url_raw = str(cfg.get("course_url") or "").strip()
    if is_placeholder_url(url_raw) and cid:
        url_raw = f"https://www.yuketang.cn/v2/web/studentLog/{cid}"
        cfg["course_url"] = url_raw

    candidates = expand_course_urls(url_raw, prefer_desktop=prefer)
    if not candidates and cid:
        candidates = expand_course_urls(
            f"https://www.yuketang.cn/v2/web/studentLog/{cid}",
            prefer_desktop=prefer,
        )
    primary = candidates[0] if candidates else url_raw
    return primary, cid, candidates


def save_settings(path: Path, cfg: dict[str, Any]) -> None:
    """写入精简 config.yaml（保留用户额外键）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}

    out = dict(existing)
    for k in _SAVE_KEYS:
        if k in cfg:
            out[k] = cfg[k]
    # 清理占位
    if is_placeholder_url(str(out.get("course_url") or "")):
        cid = out.get("classroom_id")
        if cid:
            out["course_url"] = f"https://www.yuketang.cn/v2/web/studentLog/{cid}"

    header = (
        "# 由雨课堂助手自动生成/更新，请勿提交到公开仓库\n"
        "# 也可直接改这里；或运行 python main.py 用菜单修改\n"
    )
    body = yaml.safe_dump(
        out,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    path.write_text(header + body, encoding="utf-8")
