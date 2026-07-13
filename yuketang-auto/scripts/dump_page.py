#!/usr/bin/env python3
"""导出当前课程页 HTML + 截图，便于改选择器。

  python scripts/dump_page.py --config config.yaml
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yuketang.browser import BrowserSession
from yuketang.login import ensure_login
from yuketang.urls import expand_course_urls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--url", default=None, help="覆盖 course_url")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"缺少 {cfg_path}")
        return 2
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    raw = args.url or cfg.get("course_url")
    prefer = bool(cfg.get("prefer_desktop", True))
    candidates = expand_course_urls(raw, prefer_desktop=prefer)
    storage = ROOT / cfg.get("storage_state", "data/storage_state.json")
    out_dir = ROOT / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with BrowserSession(headless=False, storage_state=storage) as session:
        page = session.page
        assert page is not None
        ok, final_url = ensure_login(
            page,
            course_url=candidates[0],
            timeout_sec=int(cfg.get("wait_login_timeout_sec", 180)),
            candidate_urls=candidates,
        )
        if not ok:
            return 1
        session.save_state()
        page.wait_for_timeout(2000)
        html_path = out_dir / f"page_{stamp}.html"
        png_path = out_dir / f"page_{stamp}.png"
        html_path.write_text(page.content(), encoding="utf-8")
        session.screenshot(png_path, full_page=True)
        print(f"HTML -> {html_path}")
        print(f"PNG  -> {png_path}")
        print(f"URL  -> {page.url} (resolved: {final_url})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
