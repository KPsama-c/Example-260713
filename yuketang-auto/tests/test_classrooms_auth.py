"""班级列表：区分认证失败与空列表。"""

from __future__ import annotations

from yuketang.classrooms import (
    FetchRoomsResult,
    auth_error_user_message,
    is_auth_error_payload,
    rooms_to_dicts,
    ClassroomInfo,
)


def test_is_auth_error_sessionid():
    assert is_auth_error_payload({"errcode": 401002, "errmsg": "Cookie has no sessionid"})
    assert is_auth_error_payload({"errcode": "401002", "errmsg": "no sessionid"})
    assert is_auth_error_payload({"code": 50000, "msg": "UNAUTHENTICATED"})


def test_is_auth_error_not_for_empty_ok():
    assert not is_auth_error_payload({"errcode": 0, "data": {"list": []}})
    assert not is_auth_error_payload({"errcode": 500, "errmsg": "server error"})
    assert not is_auth_error_payload(None)


def test_auth_error_user_message_mentions_relogin():
    msg = auth_error_user_message("Cookie has no sessionid")
    assert "sessionid" in msg or "登录" in msg
    assert "刷新待办" in msg
    assert "无头" in msg


def test_fetch_rooms_result_defaults():
    r = FetchRoomsResult(rooms=[])
    assert r.ok is True
    assert r.auth_failed is False


def test_rooms_to_dicts():
    rooms = [
        ClassroomInfo("1", "9", "班", "课", "张"),
    ]
    d = rooms_to_dicts(rooms)
    assert d[0]["classroom_id"] == "1"
    assert "课" in d[0]["label"]
