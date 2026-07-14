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
    "soft_file": "data/soft.json",
    "partial_file": "data/partial.json",
    "max_videos": 0,
    "wait_login_timeout_sec": 180,
    "screenshot_on_error": True,
    "pause_between_sec": [2, 6],
    # 多课堂配置档（断点仍用 classroom:lesson，单 progress 文件即可）
    "profiles": [],
    "active_profile": "",
    # —— 能力边界（默认保守）——
    "resume_partial": True,  # 续播：seek 到本机已观测进度
    "allow_skip_ahead": False,  # 跳播：达线后才可片尾 seek
    "allow_checkin_assist": False,  # 签到辅助：达线后片尾真播（不改签到 API）
    "tail_seek_sec": 90,  # 片尾真播秒数 30–180
    "require_threshold_before_tail": True,  # 片尾 seek 前必须达 complete_ratio
    "require_platform_confirm": True,
    "confirm_grace_sec": 120,
    "soft_boost": 0.10,
    "retry_per_lesson": 1,
    "skip_local_complete_on_all": True,
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
    "soft_file",
    "max_videos",
    "wait_login_timeout_sec",
    "screenshot_on_error",
    "pause_between_sec",
    "profiles",
    "active_profile",
    "require_platform_confirm",
    "confirm_grace_sec",
    "soft_boost",
    "retry_per_lesson",
    "partial_file",
    "resume_partial",
    "allow_skip_ahead",
    "allow_checkin_assist",
    "tail_seek_sec",
    "require_threshold_before_tail",
    "skip_local_complete_on_all",
)

_RE_DIGITS = re.compile(r"^\d{5,}$")


def _normalize_profiles(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for it in raw:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("classroom_id") or "").strip()
        if not cid or cid in seen:
            continue
        name = str(it.get("name") or cid).strip() or cid
        url = str(it.get("course_url") or "").strip()
        if not url:
            url = f"https://www.yuketang.cn/v2/web/studentLog/{cid}"
        out.append({"name": name, "classroom_id": cid, "course_url": url})
        seen.add(cid)
    return out


def list_profiles(cfg: dict[str, Any]) -> list[dict[str, str]]:
    return _normalize_profiles(cfg.get("profiles"))


def upsert_profile(
    cfg: dict[str, Any],
    *,
    classroom_id: str,
    name: str = "",
    course_url: str = "",
    activate: bool = False,
) -> dict[str, Any]:
    """新增或更新配置档；可选立即激活。"""
    cid = str(classroom_id or "").strip()
    if not cid:
        return cfg
    name = (name or "").strip() or cid
    url = (course_url or "").strip() or f"https://www.yuketang.cn/v2/web/studentLog/{cid}"
    profiles = list_profiles(cfg)
    found = False
    for p in profiles:
        if p["classroom_id"] == cid:
            p["name"] = name
            p["course_url"] = url
            found = True
            break
    if not found:
        profiles.append({"name": name, "classroom_id": cid, "course_url": url})
    cfg["profiles"] = profiles
    if activate:
        activate_profile(cfg, cid)
    return cfg


def delete_profile(cfg: dict[str, Any], key: str) -> bool:
    """按 name 或 classroom_id 删除配置档；不清除 progress。"""
    key = str(key or "").strip()
    if not key:
        return False
    profiles = list_profiles(cfg)
    removed_cid = ""
    kept: list[dict[str, str]] = []
    for p in profiles:
        if p["classroom_id"] == key or p["name"] == key:
            removed_cid = p["classroom_id"]
            continue
        kept.append(p)
    if not removed_cid:
        return False
    cfg["profiles"] = kept
    cur = str(cfg.get("classroom_id") or "")
    ap = str(cfg.get("active_profile") or "")
    was_active = cur == removed_cid or ap == key or ap == removed_cid
    if was_active:
        if kept:
            activate_profile(cfg, kept[0]["classroom_id"])
        else:
            cfg["active_profile"] = ""
    return True


def activate_profile(cfg: dict[str, Any], key: str) -> bool:
    """按 name 或 classroom_id 激活配置档，写回顶层 classroom_id/course_url。"""
    key = str(key or "").strip()
    if not key:
        return False
    for p in list_profiles(cfg):
        if p["classroom_id"] == key or p["name"] == key:
            cfg["classroom_id"] = p["classroom_id"]
            cfg["course_url"] = p["course_url"]
            cfg["active_profile"] = p["name"]
            return True
    # 允许直接激活未知 id：写入顶层并补档
    if _RE_DIGITS.match(key):
        apply_classroom_input(cfg, key)
        upsert_profile(
            cfg,
            classroom_id=key,
            name=key,
            course_url=str(cfg.get("course_url") or ""),
            activate=False,
        )
        cfg["active_profile"] = key
        return True
    return False


def ensure_profile_from_current(cfg: dict[str, Any]) -> None:
    """若当前有 classroom 且不在 profiles，自动补一条。"""
    if not has_classroom(cfg):
        return
    _, cid, _ = resolve_runtime(cfg)
    if not cid:
        return
    profiles = list_profiles(cfg)
    if any(p["classroom_id"] == cid for p in profiles):
        if not cfg.get("active_profile"):
            for p in profiles:
                if p["classroom_id"] == cid:
                    cfg["active_profile"] = p["name"]
                    break
        return
    name = str(cfg.get("active_profile") or cid)
    upsert_profile(
        cfg,
        classroom_id=cid,
        name=name,
        course_url=str(cfg.get("course_url") or ""),
        activate=False,
    )
    cfg["active_profile"] = name


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
    cfg["profiles"] = _normalize_profiles(cfg.get("profiles"))
    # 有 active_profile 时对齐顶层课堂
    ap = str(cfg.get("active_profile") or "").strip()
    if ap:
        activate_profile(cfg, ap)
    else:
        ensure_profile_from_current(cfg)
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
