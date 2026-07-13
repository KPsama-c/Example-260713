#!/usr/bin/env python3
"""有界面打开 base_url，导出 HTML / 链接，便于改 selectors。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yinghua.browser import BrowserSession
from yinghua.login import ensure_login
from yinghua.settings import load_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--url", default=None)
    args = ap.parse_args()
    cfg = load_settings(Path(args.config))
    url = args.url or str(cfg.get("course_url") or cfg.get("base_url"))
    out_dir = ROOT / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    storage = ROOT / str(cfg.get("storage_state") or "data/storage_state.json")
    with BrowserSession(headless=False, storage_state=storage) as session:
        page = session.page
        assert page is not None
        ensure_login(page, cfg=cfg, log=print)
        if args.url:
            page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        html_path = out_dir / f"page_{stamp}.html"
        html_path.write_text(page.content(), encoding="utf-8")
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(a => ({href: a.href, text: (a.innerText||'').trim().slice(0,80)}))",
        )
        import json

        (out_dir / f"links_{stamp}.json").write_text(
            json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        session.screenshot(out_dir / f"shot_{stamp}.png", full_page=True)
        session.save_state()
        print(f"[dump] html={html_path}")
        print(f"[dump] links={len(links)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
