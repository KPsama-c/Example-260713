#!/usr/bin/env python3
"""雨课堂「智·汇大讲堂」直播回放助手（Playwright）。

通用入口：用户在 config.yaml 中填写自己的 classroom_id 后使用。
运行即表示已阅读并接受 DISCLAIMER.md。

用法:
  python main.py --list-only
  python main.py --once --headed
  python main.py
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yuketang import __version__
from yuketang.browser import BrowserSession
from yuketang.login import ensure_login
from yuketang.logs import list_pending_replays
from yuketang.progress import FailedStore, ProgressStore
from yuketang.rate import PRESETS, rate_help_text, resolve_playback_rate
from yuketang.replay import watch_replay
from yuketang.urls import expand_course_urls, resolve_classroom_id

DISCLAIMER_FILE = ROOT / "DISCLAIMER.md"


def load_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"配置文件格式错误: {path}")
    return data


def resolve_path(base: Path, p: str | Path) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def origin_of(url: str) -> str:
    p = urlparse(url)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return "https://www.yuketang.cn"


def print_banner() -> None:
    print("=" * 56)
    print(f" 雨课堂 · 智·汇大讲堂 直播回放助手  v{__version__}")
    print(" 非官方工具 · 仅限本人账号自用")
    print(" 范围: 仅观看回放 · 不签到 · 不答题")
    print(" 风险: 可能违反平台/学校规定，后果自负")
    print(f" 免责: 详见 {DISCLAIMER_FILE.name}（运行即视为同意）")
    print("=" * 56)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="雨课堂「智·汇大讲堂」直播回放助手（非官方，风险自负）",
        epilog=(
            "使用前请阅读 DISCLAIMER.md。仅限本人账号、合法合规使用。\n"
            + rate_help_text()
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--once", action="store_true", help="只处理一节")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--list-only", action="store_true", help="只列出未观看回放")
    ap.add_argument(
        "--rate",
        "--speed",
        dest="rate",
        default=None,
        metavar="RATE",
        help="自定义播放倍速，如 1.25 / 1.5x / 2 / normal（覆盖配置；--speed 同义）",
    )
    ap.add_argument(
        "--list-rates",
        action="store_true",
        help="列出倍速预设并退出",
    )
    ap.add_argument("--max", type=int, default=None, help="最多 N 节")
    ap.add_argument(
        "--i-accept-risk",
        action="store_true",
        help="确认已阅读 DISCLAIMER.md（可选；不传也会在横幅提示）",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    print_banner()
    if args.list_rates:
        print(rate_help_text())
        print("预设明细:")
        for k, v in PRESETS.items():
            print(f"  {k:10s} -> {v}x")
        return 0
    if args.i_accept_risk:
        print("[main] 已确认接受免责声明 (--i-accept-risk)")

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"[!] 缺少配置: {cfg_path}")
        print("    请复制 config.example.yaml 为 config.yaml，并填写你的 classroom_id")
        return 2

    cfg = load_config(cfg_path)
    course_url_raw = (cfg.get("course_url") or "").strip()
    if not course_url_raw or "请替换" in course_url_raw:
        print("[!] 请在 config.yaml 中设置你自己的 course_url 或 classroom_id")
        print("    示例见 config.example.yaml；如何获取 ID 见 README.md")
        return 2

    prefer_desktop = bool(cfg.get("prefer_desktop", True))
    url_candidates = expand_course_urls(course_url_raw, prefer_desktop=prefer_desktop)
    course_url = url_candidates[0]
    classroom_id = resolve_classroom_id(
        course_url_raw, cfg.get("classroom_id")
    ) or resolve_classroom_id(course_url)

    if not classroom_id:
        print("[!] 无法解析 classroom_id，请在 config.yaml 设置 classroom_id")
        return 2

    print(f"[main] course_url    : {course_url_raw}")
    print(f"[main] classroom_id  : {classroom_id}")
    print(f"[main] 候选入口页:")
    for i, u in enumerate(url_candidates[:6], 1):
        print(f"       {i}. {u}")

    headless = bool(cfg.get("headless", False))
    if args.headed:
        headless = False
    if args.headless:
        headless = True

    # 自定义倍速：CLI > config > 默认；支持 1.5x / 预设名
    rate = resolve_playback_rate(cli_rate=args.rate, cfg=cfg, log=print)
    max_videos = int(args.max if args.max is not None else cfg.get("max_videos", 0))
    if args.once:
        max_videos = 1

    storage = resolve_path(ROOT, cfg.get("storage_state", "data/storage_state.json"))
    progress_path = resolve_path(ROOT, cfg.get("progress_file", "data/progress.json"))
    failed_path = resolve_path(ROOT, cfg.get("failed_file", "data/failed.json"))
    wait_login = int(cfg.get("wait_login_timeout_sec", 180))
    max_watch = int(cfg.get("max_watch_sec", 7200))
    complete_ratio = float(cfg.get("complete_ratio", 0.65))

    shot_on_err = bool(cfg.get("screenshot_on_error", True))
    pause_cfg = cfg.get("pause_between_sec", [2, 6])
    if isinstance(pause_cfg, (list, tuple)) and len(pause_cfg) >= 2:
        pause_lo, pause_hi = float(pause_cfg[0]), float(pause_cfg[1])
    else:
        pause_lo, pause_hi = 2.0, 6.0

    progress = ProgressStore.load(progress_path)
    failed = FailedStore(failed_path)
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    print("-" * 56)
    print(f" headless     : {headless}")
    print(f" rate         : {rate}x")
    print(f" complete≥    : {complete_ratio*100:.0f}% 总时长")
    print(f" max_lessons  : {max_videos or '∞'}")
    print(f" storage      : {storage}")
    print("-" * 56)


    done_count = 0
    fail_count = 0

    with BrowserSession(headless=headless, storage_state=storage) as session:
        page = session.page
        assert page is not None

        ok_login, course_url = ensure_login(
            page,
            course_url=course_url,
            timeout_sec=wait_login,
            log=print,
            candidate_urls=url_candidates,
        )
        if not ok_login:
            if shot_on_err:
                session.screenshot(data_dir / "login_timeout.png")
            return 1

        session.save_state()
        origin = origin_of(page.url or course_url)
        print(f"[main] 已保存登录态 -> {storage}")
        print(f"[main] origin: {origin}")
        print(f"[main] 当前页: {page.url}")

        # 确保能调 API：先停在有效日志页
        try:
            page.goto(
                f"{origin}/v2/web/studentLog/{classroom_id}",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(1500)
        except Exception as e:
            print(f"[main] 打开日志页警告: {e}")

        pending = list_pending_replays(
            page,
            classroom_id,
            progress_keys=set(progress.completed),
            origin=origin,
            log=print,
        )

        if not pending:
            print("[main] 没有待观看回放的课堂")
            return 0

        print("[main] 待处理（未观看回放）:")
        for i, item in enumerate(pending, 1):
            attend = "已签到" if item.attend_status else "缺勤"
            print(f"  {i:3d}. [{item.lesson_id}] {item.title}  ({attend})")

        if args.list_only:
            return 0

        limit = max_videos if max_videos > 0 else len(pending)
        targets = pending[:limit]

        for idx, item in enumerate(targets, 1):
            print("-" * 56)
            print(f"[main] ({idx}/{len(targets)}) {item.title}")
            print(f"[main] lesson_id={item.lesson_id} activity_id={item.activity_id}")

            ok = watch_replay(
                page,
                classroom_id=classroom_id,
                lesson_id=item.lesson_id,
                origin=origin,
                rate=rate,
                complete_ratio=complete_ratio,
                max_watch_sec=max_watch,
                log=print,
            )
            if ok:
                progress.mark_done(item.key, item.title)
                done_count += 1
                session.save_state()
                print("[main] 本节完成，已写入断点")
            else:
                fail_count += 1
                failed.add(item.key, item.title, "watch_replay failed")
                if shot_on_err:
                    session.screenshot(data_dir / f"fail_replay_{item.lesson_id}.png")
                print("[main] 本节失败，已记录")

            if idx < len(targets):
                delay = random.uniform(pause_lo, pause_hi)
                print(f"[main] 休息 {delay:.1f}s …")
                time.sleep(delay)

        session.save_state()

    print("=" * 56)
    print(f"[main] 结束: 成功 {done_count}, 失败 {fail_count}")
    print(f"  断点: {progress_path}")
    if fail_count:
        print(f"  失败: {failed_path}")
        print("  下一步: 看 data/fail_replay_*.png，必要时改 selectors/replay")
    print("=" * 56)
    return 0 if fail_count == 0 else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[main] 用户中断，进度在 data/progress.json")
        raise SystemExit(130)
