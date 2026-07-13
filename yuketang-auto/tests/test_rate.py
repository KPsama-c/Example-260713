from yuketang.rate import clamp_rate, parse_rate_value


def test_clamp():
    assert clamp_rate(0.1) >= 0.5
    assert clamp_rate(99) <= 3.0
    assert abs(clamp_rate(1.25) - 1.25) < 1e-6


def test_parse():
    assert parse_rate_value("1.5") == 1.5
    assert parse_rate_value("bad") is None
