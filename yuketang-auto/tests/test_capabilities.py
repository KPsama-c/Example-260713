"""能力边界：跳播 / 签到辅助 / 续播。"""

from __future__ import annotations

from yuketang.capabilities import (
    TAIL_SEC_DEFAULT,
    TAIL_SEC_MAX,
    TAIL_SEC_MIN,
    PlaybackCapabilities,
    capabilities_from_cfg,
    clamp_tail_seek_sec,
    compute_tail_seek_time,
    may_tail_seek,
)


def test_defaults_are_safe():
    caps = capabilities_from_cfg({})
    assert caps.resume_partial is True
    assert caps.allow_skip_ahead is False
    assert caps.allow_checkin_assist is False
    assert caps.tail_seek_enabled is False
    assert caps.require_threshold_before_tail is True
    assert TAIL_SEC_MIN <= caps.tail_seek_sec <= TAIL_SEC_MAX


def test_enable_skip_ahead():
    caps = capabilities_from_cfg({"allow_skip_ahead": True, "tail_seek_sec": 120})
    assert caps.allow_skip_ahead is True
    assert caps.tail_seek_enabled is True
    assert caps.tail_seek_sec == 120.0


def test_checkin_assist_enables_tail():
    caps = capabilities_from_cfg({"allow_checkin_assist": True})
    assert caps.allow_checkin_assist is True
    assert caps.tail_seek_enabled is True
    assert caps.allow_skip_ahead is False


def test_bool_aliases():
    assert capabilities_from_cfg({"skip_ahead": "on"}).allow_skip_ahead is True
    assert capabilities_from_cfg({"checkin_assist": "yes"}).allow_checkin_assist is True
    assert capabilities_from_cfg({"resume_partial": "关"}).resume_partial is False


def test_clamp_tail_seek_sec():
    assert clamp_tail_seek_sec(10) == TAIL_SEC_MIN
    assert clamp_tail_seek_sec(999) == TAIL_SEC_MAX
    assert clamp_tail_seek_sec("90") == 90.0
    assert clamp_tail_seek_sec("bad") == TAIL_SEC_DEFAULT


def test_compute_tail_seek_time():
    t = compute_tail_seek_time(3600, 90)
    assert t is not None
    assert abs(t - (3600 - 90)) < 0.01
    assert compute_tail_seek_time(5, 90) is None
    # 很短的片：至少从 50% 起
    t2 = compute_tail_seek_time(100, 90)
    assert t2 is not None
    assert t2 >= 50.0


def test_may_tail_seek_requires_threshold():
    caps = PlaybackCapabilities(allow_skip_ahead=True, tail_seek_sec=90)
    assert may_tail_seek(caps, local_ratio=0.64, complete_ratio=0.65) is False
    assert may_tail_seek(caps, local_ratio=0.65, complete_ratio=0.65) is True
    assert may_tail_seek(caps, local_ratio=0.90, complete_ratio=0.65, already_done=True) is False


def test_may_tail_seek_disabled():
    caps = PlaybackCapabilities()
    assert may_tail_seek(caps, local_ratio=0.9, complete_ratio=0.65) is False


def test_may_tail_seek_without_threshold_gate():
    caps = PlaybackCapabilities(
        allow_checkin_assist=True,
        require_threshold_before_tail=False,
    )
    assert may_tail_seek(caps, local_ratio=0.1, complete_ratio=0.65) is True


def test_summary_contains_flags():
    s = PlaybackCapabilities(allow_skip_ahead=True, tail_seek_sec=60).summary()
    assert "跳播" in s or "片尾" in s
    assert "60" in s
