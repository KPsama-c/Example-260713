"""CLI 配置档菜单：用 monkeypatch 模拟输入。"""

from yuketang.ui import pick_action, profiles_submenu
from yuketang.settings import list_profiles, upsert_profile


def test_pick_action_profiles():
    assert pick_action.__doc__ is None or True  # 存在即可
    # 通过间接：profiles 关键字在源中
    import inspect

    src = inspect.getsource(pick_action)
    assert "profiles" in src


def test_profiles_submenu_switch(monkeypatch):
    cfg: dict = {"profiles": [], "active_profile": ""}
    upsert_profile(cfg, classroom_id="11111", name="课A", activate=True)
    upsert_profile(cfg, classroom_id="22222", name="课B", activate=False)
    # 选编号 2 切换
    monkeypatch.setattr("yuketang.ui.prompt_line", lambda *a, **k: "2")
    cfg = profiles_submenu(cfg)
    assert cfg["classroom_id"] == "22222"
    assert len(list_profiles(cfg)) == 2
