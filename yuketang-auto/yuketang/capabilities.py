"""播放能力边界：跳播 / 签到辅助 / 续播。

默认保守：
- 续播 resume：开（仅 seek 到本机已观测进度）
- 跳播 skip_ahead：关（达线后不允许跳到片尾未看区间）
- 签到辅助 checkin_assist：关（达线后不主动拖到片尾「补签到」）

说明：
- 本项目仍**不**伪造心跳、不协议层改签到字段。
- checkin_assist / skip_ahead 仅控制是否允许「真 seek 播放器」到片尾前 N 秒并真播完；
  平台最终是否变成「已签到 / 已观看」由雨课堂判定。
- 开启任一激进项时会在日志打醒目提示，并要求达到 complete_ratio 后才允许片尾 seek。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


# 片尾 seek 秒数硬边界（防止配成整集跳过）
TAIL_SEC_MIN = 30.0
TAIL_SEC_MAX = 180.0
TAIL_SEC_DEFAULT = 90.0


@dataclass(frozen=True)
class PlaybackCapabilities:
    """单次任务生效的能力边界（已规范化）。"""

    resume_partial: bool = True
    """允许从 partial 续播（只 seek 到本机曾观测到的时刻）。"""

    allow_skip_ahead: bool = False
    """允许在本地达 complete_ratio 后，真 seek 到片尾前 tail_sec 并真播完。"""

    allow_checkin_assist: bool = False
    """签到辅助：语义同片尾 seek（用户认为拖到最后几分钟可影响签到态）。
    仍不调用签到 API、不改 attend_status 字段。"""

    tail_seek_sec: float = TAIL_SEC_DEFAULT
    """片尾前保留真播的秒数（30–180）。"""

    require_threshold_before_tail: bool = True
    """片尾 seek 前必须已达 complete_ratio（防止开局就跳尾）。"""

    @property
    def tail_seek_enabled(self) -> bool:
        """是否启用「达线后片尾真 seek」。"""
        return bool(self.allow_skip_ahead or self.allow_checkin_assist)

    def summary(self) -> str:
        parts = [
            f"续播={'开' if self.resume_partial else '关'}",
            f"跳播/片尾={'开' if self.allow_skip_ahead else '关'}",
            f"签到辅助={'开' if self.allow_checkin_assist else '关'}",
        ]
        if self.tail_seek_enabled:
            parts.append(f"片尾真播{self.tail_seek_sec:.0f}s")
        return " · ".join(parts)


def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "y", "开", "是"):
        return True
    if s in ("0", "false", "no", "off", "n", "关", "否"):
        return False
    return default


def clamp_tail_seek_sec(sec: Any, default: float = TAIL_SEC_DEFAULT) -> float:
    try:
        v = float(sec)
    except (TypeError, ValueError):
        v = float(default)
    return max(TAIL_SEC_MIN, min(v, TAIL_SEC_MAX))


def capabilities_from_cfg(cfg: dict[str, Any] | None) -> PlaybackCapabilities:
    """从 config 解析能力边界；缺省安全。"""
    cfg = cfg or {}
    resume = _as_bool(cfg.get("resume_partial"), True)
    skip = _as_bool(cfg.get("allow_skip_ahead"), False)
    # 兼容旧键名 / 中文语义
    if "allow_skip_ahead" not in cfg and "skip_ahead" in cfg:
        skip = _as_bool(cfg.get("skip_ahead"), False)
    checkin = _as_bool(cfg.get("allow_checkin_assist"), False)
    if "allow_checkin_assist" not in cfg and "checkin_assist" in cfg:
        checkin = _as_bool(cfg.get("checkin_assist"), False)
    # 若只开了 checkin_assist，也视为启用片尾 seek
    tail = clamp_tail_seek_sec(
        cfg.get("tail_seek_sec", cfg.get("checkin_tail_sec", TAIL_SEC_DEFAULT))
    )
    req = _as_bool(cfg.get("require_threshold_before_tail"), True)
    return PlaybackCapabilities(
        resume_partial=resume,
        allow_skip_ahead=skip,
        allow_checkin_assist=checkin,
        tail_seek_sec=tail,
        require_threshold_before_tail=req,
    )


def log_capabilities(caps: PlaybackCapabilities, log: Callable[[str], None] = print) -> None:
    log(f"[capabilities] {caps.summary()}")
    if caps.tail_seek_enabled:
        log(
            "[capabilities] 已启用达线后片尾真 seek："
            f"播到阈值后跳到 duration-{caps.tail_seek_sec:.0f}s 并真播完。"
            "不伪造心跳；平台签到/回放确认仍以雨课堂为准。风险自负。"
        )


def compute_tail_seek_time(duration: float, tail_sec: float) -> float | None:
    """片尾 seek 目标 currentTime；duration 无效则 None。"""
    d = float(duration or 0)
    if d < 20:
        return None
    tail = clamp_tail_seek_sec(tail_sec)
    # 至少留 5s 缓冲，且不要 seek 到比 tail 更前太多时仍要求有可播区间
    target = max(0.0, d - tail)
    # 若片太短，至少从 50% 处起（仍真播后半）
    if target < d * 0.5:
        target = max(0.0, d * 0.5)
    if target >= d - 2.0:
        return None
    return target


def may_tail_seek(
    caps: PlaybackCapabilities,
    *,
    local_ratio: float,
    complete_ratio: float,
    already_done: bool = False,
) -> bool:
    """是否允许执行片尾 seek。"""
    if already_done:
        return False
    if not caps.tail_seek_enabled:
        return False
    if caps.require_threshold_before_tail:
        thr = max(0.0, min(float(complete_ratio), 1.0))
        if float(local_ratio) + 1e-6 < thr:
            return False
    return True
