"""本机环境自检（不连雨课堂真站业务）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def run_doctor(root: Path) -> dict[str, Any]:
    """返回 {ok, checks: [{name, ok, detail}]}。"""
    root = Path(root)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    # Python
    ver = sys.version_info
    py_ok = ver >= (3, 10)
    add(
        "python",
        py_ok,
        f"{ver.major}.{ver.minor}.{ver.micro}" + ("" if py_ok else "（需要 ≥3.10）"),
    )

    # 依赖
    for mod in ("playwright", "yaml", "flask"):
        spec = importlib.util.find_spec(mod if mod != "yaml" else "yaml")
        add(f"import:{mod}", spec is not None, "ok" if spec else "未安装")

    # Playwright chromium 粗检
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            # 不启动浏览器，只检查可执行文件路径是否可解析
            exe = p.chromium.executable_path
            exists = Path(exe).exists() if exe else False
            add(
                "playwright_chromium",
                exists,
                str(exe) if exists else "未找到，请: python -m playwright install chromium",
            )
    except Exception as e:
        add("playwright_chromium", False, f"检测失败: {e}")

    # 路径与隐私
    data = root / "data"
    add("data_dir", True, str(data) + ("（已存在）" if data.is_dir() else "（将自动创建）"))

    cfg = root / "config.yaml"
    add(
        "config.yaml",
        True,
        "存在（本地，勿提交）" if cfg.exists() else "不存在（可用向导生成）",
    )

    storage = data / "storage_state.json"
    add(
        "login_state",
        True,
        "已有 storage_state" if storage.exists() else "尚未登录（首次请有界面）",
    )

    # 绑定安全提示（文档级）
    add(
        "bind_hint",
        True,
        "Web 请保持 127.0.0.1，勿 0.0.0.0",
    )

    # gitignore 关键文件
    gi = root / ".gitignore"
    if gi.exists():
        text = gi.read_text(encoding="utf-8", errors="replace")
        has_cfg = "config.yaml" in text
        has_data = "data/" in text or "data/*" in text
        add(
            "gitignore",
            has_cfg and has_data,
            "ok" if (has_cfg and has_data) else "建议忽略 config.yaml 与 data/*",
        )
    else:
        add("gitignore", False, "缺少 .gitignore")

    ok = all(c["ok"] for c in checks if c["name"].startswith("import:") or c["name"] in (
        "python",
        "playwright_chromium",
    ))
    return {"ok": ok, "checks": checks}


def format_doctor_report(result: dict[str, Any]) -> str:
    lines = ["环境自检 doctor", "-" * 40]
    for c in result.get("checks") or []:
        mark = "OK" if c.get("ok") else "!!"
        lines.append(f"  [{mark}] {c.get('name')}: {c.get('detail')}")
    lines.append("-" * 40)
    lines.append("总体: " + ("通过" if result.get("ok") else "有问题，请按上方提示处理"))
    return "\n".join(lines)
