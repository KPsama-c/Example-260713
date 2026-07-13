#!/usr/bin/env python3
"""英华学堂本地助手（Playwright）。

推荐：python main.py
也可：python main.py --login | --list-only | --once

运行即表示已阅读并接受 DISCLAIMER.md。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yinghua import __version__
from yinghua.browser import BrowserSession
from yinghua.jobs import run_automation
from yinghua.login import ensure_login
from yinghua.settings import load_settings, public_settings, save_settings
from yinghua.util import resolve_path

DISCLAIMER_FILE = ROOT / "DISCLAIMER.md"
CONFIG_PATH = ROOT / "config.yaml"


def print_banner() -> None:
    print("=" * 56)
    print(f" 英华学堂本地助手  v{__version__}")
    print(" 非官方工具 · 仅限本人账号自用")
    print(" 范围: 视频学习进度 · 考试默认关闭")
    print(" 风险: 可能违反平台/学校规定，后果自负")
    print(f" 免责: 详见 {DISCLAIMER_FILE.name}（运行即视为同意）")
    print("=" * 56)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="英华学堂本地助手（非官方，风险自负）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--login", action="store_true", help="仅打开浏览器完成登录并保存态")
    ap.add_argument("--list-only", action="store_true", help="只列待办")
    ap.add_argument("--once", action="store_true", help="只处理一节")
    ap.add_argument("--all", action="store_true", help="连续刷待办")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--base-url", default=None, help="覆盖 base_url")
    ap.add_argument("--setup", action="store_true", help="从 example 生成 config.yaml")
    ap.add_argument("--max", type=int, default=None, help="最多 N 节")
    return ap.parse_args()


def do_setup(cfg_path: Path) -> int:
    example = ROOT / "config.example.yaml"
    if cfg_path.exists():
        print(f"[setup] 已存在 {cfg_path}")
        return 0
    if example.exists():
        cfg_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[setup] 已复制 config.example.yaml → {cfg_path}")
        print("[setup] 请编辑 base_url 后运行 python main.py --login")
        return 0
    cfg = load_settings(cfg_path)
    save_settings(cfg_path, cfg)
    print(f"[setup] 已写入默认配置 {cfg_path}")
    return 0


def do_login(cfg: dict) -> int:
    storage = resolve_path(ROOT, cfg.get("storage_state", "data/storage_state.json"))
    headless = bool(cfg.get("headless", False))
    wait_login = int(cfg.get("wait_login_timeout_sec", 300))
    print(f"[login] storage → {storage}")
    with BrowserSession(headless=headless, storage_state=storage) as session:
        page = session.page
        assert page is not None
        ok, url = ensure_login(page, cfg=cfg, timeout_sec=wait_login, log=print)
        if not ok:
            session.screenshot(ROOT / "data" / "login_timeout.png")
            print("[login] 失败")
            return 1
        session.save_state()
        print(f"[login] 已保存登录态 · 当前页 {url}")
    return 0


def main() -> int:
    print_banner()
    args = parse_args()
    cfg_path = Path(args.config)

    if args.setup:
        return do_setup(cfg_path)

    if not cfg_path.exists():
        example = ROOT / "config.example.yaml"
        if example.exists():
            cfg_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[config] 已从 example 生成 {cfg_path.name}，请按需改 base_url")
        else:
            save_settings(cfg_path, load_settings(cfg_path))

    cfg = load_settings(cfg_path)
    if args.base_url:
        cfg["base_url"] = args.base_url.rstrip("/")
    if args.headed:
        cfg["headless"] = False
    if args.headless:
        cfg["headless"] = True
    if args.max is not None:
        cfg["max_videos"] = args.max

    if args.login:
        return do_login(cfg)

    action = "list"
    if args.list_only:
        action = "list"
    elif args.once:
        action = "next"
    elif args.all:
        action = "all"
    else:
        # 简易菜单
        print("\n配置摘要:", public_settings(cfg))
        print("\n  1) 登录并保存态")
        print("  2) 列出待办")
        print("  3) 播放一节")
        print("  4) 连续播放")
        print("  0) 退出")
        choice = input("选择: ").strip()
        if choice == "1":
            return do_login(cfg)
        if choice == "2":
            action = "list"
        elif choice == "3":
            action = "next"
        elif choice == "4":
            action = "all"
        else:
            return 0

    result = run_automation(root=ROOT, cfg=cfg, action=action, log=print)
    if not result.get("ok"):
        print("[main] 结束(有失败或错误):", result.get("error") or result)
        return 1
    print("[main] 完成:", {k: result.get(k) for k in ("action", "done", "fail", "pending_count")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
