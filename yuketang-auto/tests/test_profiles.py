from yuketang.settings import (
    activate_profile,
    apply_classroom_input,
    list_profiles,
    upsert_profile,
)


def test_upsert_and_activate():
    cfg: dict = {"profiles": [], "active_profile": ""}
    upsert_profile(cfg, classroom_id="111", name="课A", activate=True)
    upsert_profile(cfg, classroom_id="222", name="课B", activate=False)
    assert len(list_profiles(cfg)) == 2
    assert cfg["classroom_id"] == "111"
    assert activate_profile(cfg, "课B")
    assert cfg["classroom_id"] == "222"
    assert cfg["active_profile"] == "课B"


def test_activate_unknown_digits():
    cfg: dict = {"profiles": [], "active_profile": ""}
    assert activate_profile(cfg, "33333")
    assert cfg["classroom_id"] == "33333"
    assert any(p["classroom_id"] == "33333" for p in list_profiles(cfg))


def test_apply_then_upsert_no_dup():
    cfg: dict = {}
    apply_classroom_input(cfg, "44444")
    upsert_profile(cfg, classroom_id="44444", name="X")
    upsert_profile(cfg, classroom_id="44444", name="Y")
    assert len(list_profiles(cfg)) == 1
    assert list_profiles(cfg)[0]["name"] == "Y"
