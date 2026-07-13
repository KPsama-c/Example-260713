from yuketang.logs import normalize_attend_filter


def test_normalize():
    assert normalize_attend_filter("all") == "all"
    assert normalize_attend_filter("absent") == "absent"
    assert normalize_attend_filter("present") == "present"
    assert normalize_attend_filter("ABSENT") == "absent"
    assert normalize_attend_filter("weird") == "all"
    assert normalize_attend_filter(None) == "all"
