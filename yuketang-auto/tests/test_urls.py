from yuketang.urls import parse_ids, resolve_classroom_id, lesson_overview_url


def test_parse_student_log():
    u = "https://www.yuketang.cn/v2/web/studentLog/12345678"
    assert parse_ids(u)["classroom_id"] == "12345678"


def test_parse_m_logs_two_ids():
    u = "https://www.yuketang.cn/m/v2/course/normalcourse/logs/111/222"
    ids = parse_ids(u)
    assert ids["course_id"] == "111"
    assert ids["classroom_id"] == "222"


def test_resolve_prefers_explicit():
    assert resolve_classroom_id(classroom_id="99") == "99"


def test_overview_url():
    assert "lesson/student/L1/overview" in lesson_overview_url("https://www.yuketang.cn", "L1")
