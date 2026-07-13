"""load_pending / duration_map 为共享入口（不启浏览器，仅签名与纯逻辑）。"""

from yuketang.jobs import enrich_duration_map, load_pending_for_classroom, select_soft_targets


def test_exports_exist():
    assert callable(load_pending_for_classroom)
    assert callable(enrich_duration_map)
    assert callable(select_soft_targets)


def test_enrich_duration_map_empty():
    class _Page:
        def wait_for_timeout(self, _ms):
            return None

    assert enrich_duration_map(_Page(), [], origin="https://www.yuketang.cn") == {}
