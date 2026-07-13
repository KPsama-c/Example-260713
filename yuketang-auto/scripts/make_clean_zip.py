#!/usr/bin/env python3
"""生成不含个人配置/登录态的纯净 zip，输出到项目根目录。"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    import sys

    sys.path.insert(0, str(ROOT))
    from yuketang import __version__

    name = f"yuketang-auto-v{__version__}-clean"
    out = ROOT / f"{name}.zip"
    include_files = [
        "main.py",
        "webapp.py",
        "requirements.txt",
        "DISCLAIMER.md",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        "start_web.bat",
        ".gitignore",
        "config.example.yaml",
        "pyproject.toml",
    ]
    include_globs = [
        "yuketang/*.py",
        "webui/templates/*.html",
        "scripts/dump_page.py",
        "scripts/make_clean_zip.py",
        "tests/*.py",
    ]

    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / name
        (base / "data").mkdir(parents=True)
        (base / "data" / ".gitkeep").write_text("", encoding="utf-8")
        (base / "yuketang").mkdir()
        (base / "webui" / "templates").mkdir(parents=True)
        (base / "scripts").mkdir()
        (base / "tests").mkdir()

        for rel in include_files:
            src = ROOT / rel
            if src.exists():
                text = src.read_text(encoding="utf-8")
                if rel in ("README.md", "webui/templates/index.html"):
                    text = re.sub(r"\b27586609\b", "YOUR_CLASSROOM_ID", text)
                    text = re.sub(r"\b5348693\b", "YOUR_COURSE_ID", text)
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
                (base / rel).write_text(text, encoding="utf-8")

        for pattern in include_globs:
            for src in ROOT.glob(pattern):
                if "__pycache__" in str(src):
                    continue
                rel = src.relative_to(ROOT)
                dst = base / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                text = src.read_text(encoding="utf-8")
                if src.suffix in (".md", ".html"):
                    text = re.sub(r"\b27586609\b", "YOUR_CLASSROOM_ID", text)
                    text = re.sub(r"\b5348693\b", "YOUR_COURSE_ID", text)
                dst.write_text(text, encoding="utf-8")

        (base / "PACKAGING.md").write_text(
            "# 纯净包\n\n不含 config.yaml / data Cookie / 断点。\n"
            "pip install -r requirements.txt && python -m playwright install chromium\n"
            "python webapp.py\n",
            encoding="utf-8",
        )

        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in base.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(base.parent).as_posix())

        # 检漏：隐私文件名 + 正文敏感片段
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            bad_names = [
                n
                for n in names
                if n.endswith("config.yaml")
                or "storage_state.json" in n
                or re.search(r"(^|/)progress\.json$", n)
                or re.search(r"(^|/)(soft|failed)\.json$", n)
                or "__pycache__" in n
                or ".pytest_cache" in n
            ]
            bad_content: list[str] = []
            for n in names:
                # 仅检用户可见文档与模板中的真实 ID 残留（打包脚本自身含脱敏字面量）
                if not n.endswith((".md", ".html", ".yaml", ".yml", ".json")):
                    continue
                if n.endswith("config.example.yaml"):
                    continue
                raw = zf.read(n)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                base = n.rsplit("/", 1)[-1]
                if base in ("storage_state.json", "progress.json", "soft.json", "failed.json"):
                    bad_content.append(n)
                if re.search(r"\b27586609\b", text) or re.search(r"\b5348693\b", text):
                    bad_content.append(f"{n}:real_id")
        if bad_names or bad_content:
            print("LEAK names:", bad_names)
            print("LEAK content:", bad_content)
            return 1
        print(f"OK {out} ({out.stat().st_size} bytes, {len(names)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
