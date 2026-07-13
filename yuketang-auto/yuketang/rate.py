"""播放倍速：解析、校验、提示。

用户可通过 config.yaml 的 playback_rate 或命令行 --rate / --speed 自定义倍数。
优先级：命令行 > 配置文件 > 默认值。
"""

from __future__ import annotations

import re
from typing import Any

# HTML5 video 常见可用区间；过低/过高多数浏览器会拒绝或无意义
DEFAULT_RATE = 1.25
DEFAULT_MIN = 0.5
DEFAULT_MAX = 3.0
# 超过此值时提示可能影响平台记进度
WARN_ABOVE = 1.5

# 常用预设（文档与 --list-rates 用）
PRESETS: dict[str, float] = {
    "slow": 0.75,
    "normal": 1.0,
    "1x": 1.0,
    "1.25x": 1.25,
    "1.5x": 1.5,
    "1.75x": 1.75,
    "2x": 2.0,
    "fast": 1.5,
    "faster": 2.0,
}

_RE_NUM = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*[xX倍]?\s*$")


def parse_rate_value(raw: Any) -> float | None:
    """把用户输入解析为浮点倍速。支持: 1.25, '1.25', '1.25x', '2倍', 预设名。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    key = s.lower().replace(" ", "")
    if key in PRESETS:
        return PRESETS[key]
    # 去掉中文「倍速」等
    key = key.replace("倍速", "").replace("倍", "x")
    m = _RE_NUM.match(key)
    if m:
        return float(m.group(1))
    # 纯数字失败
    try:
        return float(s)
    except ValueError:
        return None


def clamp_rate(
    rate: float,
    *,
    min_rate: float = DEFAULT_MIN,
    max_rate: float = DEFAULT_MAX,
) -> float:
    lo = float(min_rate)
    hi = float(max_rate)
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(float(rate), hi))


def resolve_playback_rate(
    *,
    cli_rate: Any = None,
    cfg: dict[str, Any] | None = None,
    log: Any = print,
) -> float:
    """解析最终倍速。

    配置项：
      playback_rate / rate / speed: 目标倍速
      playback_rate_min / playback_rate_max: 允许区间（默认 0.5~3.0）
    """
    cfg = cfg or {}
    min_rate = float(cfg.get("playback_rate_min", DEFAULT_MIN))
    max_rate = float(cfg.get("playback_rate_max", DEFAULT_MAX))

    chosen: float | None = None
    source = "default"

    parsed_cli = parse_rate_value(cli_rate)
    if parsed_cli is not None:
        chosen = parsed_cli
        source = "cli"
    else:
        for key in ("playback_rate", "rate", "speed"):
            if key in cfg and cfg[key] is not None:
                p = parse_rate_value(cfg[key])
                if p is not None:
                    chosen = p
                    source = f"config:{key}"
                    break

    if chosen is None:
        chosen = DEFAULT_RATE
        source = "default"

    clamped = clamp_rate(chosen, min_rate=min_rate, max_rate=max_rate)
    if abs(clamped - chosen) > 1e-6:
        log(
            f"[rate] 倍速 {chosen}x 超出允许范围 "
            f"[{min_rate}, {max_rate}]，已钳制为 {clamped}x"
        )
    if clamped > WARN_ABOVE:
        log(
            f"[rate] 当前 {clamped}x > {WARN_ABOVE}x，"
            "过高可能导致平台不记「已观看回放」，建议 1.0~1.5"
        )
    log(f"[rate] 使用倍速 {clamped}x（来源: {source}）")
    return clamped


def rate_help_text() -> str:
    lines = [
        "自定义播放倍速：",
        f"  默认 {DEFAULT_RATE}x，建议 1.0~1.5，允许区间默认 {DEFAULT_MIN}~{DEFAULT_MAX}",
        "  配置: playback_rate: 1.5",
        "  命令: --rate 1.5  或  --speed 2x  或  --rate normal",
        "  预设: " + ", ".join(f"{k}={v}" for k, v in PRESETS.items()),
    ]
    return "\n".join(lines)
