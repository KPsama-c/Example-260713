#!/usr/bin/env python3
"""从 ScriptCat/Monaco 导出的 HTML 或 .user.js 中抽取可读逻辑摘要（去掉 base64 大图）。

用法:
  python scripts/extract_userscript.py path/to/file.user.js
  python scripts/extract_userscript.py path/to/editor.html
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def strip_data_urls(text: str) -> str:
    text = re.sub(
        r"(data:image/[^;]+;base64,)[A-Za-z0-9+/=\s]{200,}",
        r"\1/*stripped*/",
        text,
    )
    return text


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"not found: {src}")
        return 1
    raw = src.read_text(encoding="utf-8", errors="replace")
    # 若是 HTML 壳，尝试抽 script 体
    m = re.search(
        r"(//\s*==UserScript==[\s\S]*?//\s*==/UserScript==[\s\S]*)",
        raw,
    )
    body = m.group(1) if m else raw
    body = strip_data_urls(body)
    out = Path(__file__).resolve().parent.parent / "vendor" / "yinghua-helper.summary.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"wrote {out} ({len(body)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
