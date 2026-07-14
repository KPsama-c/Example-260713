"""最小冒烟：版本与可导入（无私钥、不连真站）。"""

from __future__ import annotations


def test_version_string():
    from yinghua import __version__

    assert isinstance(__version__, str)
    assert __version__
    parts = __version__.split(".")
    assert len(parts) >= 2


def test_import_core_modules():
    import yinghua.browser  # noqa: F401
    import yinghua.jobs  # noqa: F401
    import yinghua.progress  # noqa: F401
    import yinghua.settings  # noqa: F401
