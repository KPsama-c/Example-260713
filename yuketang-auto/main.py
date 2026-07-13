#!/usr/bin/env python3
"""雨课堂「智·汇大讲堂」直播回放助手（Playwright）。

推荐：直接 python main.py（向导 + 菜单，无需手写配置）
也可：python main.py --id <classroom_id> --list-only

运行即表示已阅读并接受 DISCLAIMER.md。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yuketang import __version__
from yuketang.browser import BrowserSession
from yuketang.classrooms import resolve_classroom_id as resolve_joined_classroom
from yuketang.doctor import format_doctor_report, run_doctor
from yuketang.jobs import (
    filter_skip_local_complete,
    load_pending_for_classroom,
    reconcile_progress_with_platform,
    run_automation,
    select_soft_targets,
    watch_lesson_batch,
)
from yuketang.login import ensure_login
from yuketang.logs import LogsApiError, normalize_attend_filter
from yuketang.progress import FailedStore, PartialStore, ProgressStore, SoftStore
from yuketang.rate import PRESETS, rate_help_text, resolve_playback_rate
from yuketang.settings import (
    activate_profile,
    apply_classroom_input,
    has_classroom,
    load_settings,
    resolve_runtime,
    save_settings,
)
from yuketang.ui import (
    capture_classroom_from_page,
    is_tty,
    pick_action,
    pick_attend_filter,
    print_main_menu,
    profiles_submenu,
    prompt_yes_no,
    settings_submenu,
    wizard_first_run,
)
from yuketang.util import origin_of, resolve_path

DISCLAIMER_FILE = ROOT / "DISCLAIMER.md"


def print_banner() -> None:
    print("=" * 56)
    print(f" 雨课堂 · 智·汇大讲堂 直播回放助手  v{__version__}")
    print(" 非官方工具 · 仅限本人账号自用")
    print(" 范围: 仅观看回放 · 不签到 · 不答题")
    print(" 风险: 可能违反平台/学校规定，后果自负")
    print(f" 免责: 详见 {DISCLAIMER_FILE.name}（运行即视为同意）")
    print(" 提示: 直接运行进入菜单；无需手写 config")
    print("=" * 56)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="雨课堂「智·汇大讲堂」直播回放助手（非官方，风险自负）",
        epilog=(
            "示例:\n"
            "  python main.py\n"
            "  python main.py --id 1234567890 --list-only\n"
            "  python main.py --url https://www.yuketang.cn/v2/web/studentLog/xxx --once\n"
            "  python main.py --setup\n\n"
            + rate_help_text()
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument(
        "--id",
        "--classroom-id",
        dest="classroom_id",
        default=None,
        help="classroom_id（可不用 config.yaml）",
    )
    ap.add_argument("--url", default=None, help="学习日志 URL（可不用 config.yaml）")
    ap.add_argument("--once", action="store_true", help="只处理一节")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--list-only", action="store_true", help="只列出未观看回放")
    ap.add_argument(
        "--filter",
        dest="attend_filter",
        default=None,
        choices=["all", "absent", "present"],
        help="筛选: all=不限签到, absent=仅缺勤, present=仅已签到",
    )
    ap.add_argument(
        "--absent-only",
        action="store_true",
        help="仅缺勤（等同 --filter absent）",
    )
    ap.add_argument(
        "--rate",
        "--speed",
        dest="rate",
        default=None,
        metavar="RATE",
        help="倍速，如 1.25 / 1.5x / normal",
    )
    ap.add_argument("--list-rates", action="store_true", help="列出倍速预设")
    ap.add_argument("--max", type=int, default=None, help="最多 N 节")
    ap.add_argument("--menu", action="store_true", help="强制进入交互菜单")
    ap.add_argument(
        "--no-menu",
        action="store_true",
        help="不进菜单：按 once/max/list 直接跑",
    )
    ap.add_argument("--setup", action="store_true", help="只运行首次向导并保存配置")
    ap.add_argument(
        "--no-save",
        action="store_true",
        help="向导/设置后不写 config.yaml",
    )
    ap.add_argument(
        "--i-accept-risk",
        action="store_true",
        help="确认已阅读 DISCLAIMER.md",
    )
    ap.add_argument(
        "--profile",
        default=None,
        metavar="NAME_OR_ID",
        help="激活配置档（name 或 classroom_id）后执行",
    )
    ap.add_argument(
        "--soft-only",
        action="store_true",
        help="仅重试 SOFT（本地达标未平台确认）的节",
    )
    ap.add_argument(
        "--doctor",
        action="store_true",
        help="本机环境自检后退出（不连业务）",
    )
    return ap.parse_args()


def print_pending(pending: list, *, soft: SoftStore | None = None, classroom_id: str = "") -> None:
    if not pending:
        print("[main] 没有待观看回放")
        return
    soft_ids: set[str] = set()
    if soft is not None and classroom_id:
        soft_ids = {s.lesson_id for s in soft.for_classroom(str(classroom_id))}
    print(f"[main] 待处理 {len(pending)} 节（未观看回放）:")
    for i, item in enumerate(pending, 1):
        attend = "已签到" if item.attend_status else "缺勤"
        soft_tag = " SOFT" if item.lesson_id in soft_ids else ""
        print(f"  {i:3d}. [{attend}{soft_tag}] {item.title}")
        print(f"       lesson={item.lesson_id}")


def ensure_resolved_classroom(
    page,
    classroom_id: str,
    *,
    cfg: dict[str, Any],
    cfg_path: Path,
    origin: str,
    auto_save: bool,
) -> str | None:
    """登录后解析/纠正 classroom_id；失败返回 None。"""
    resolved, _rooms, msg = resolve_joined_classroom(page, str(classroom_id), log=print)
    if not resolved:
        print(f"[!] {msg}")
        return None
    if resolved != str(classroom_id):
        print(f"[main] classroom_id: {classroom_id} → {resolved}")
        cfg["classroom_id"] = resolved
        cfg["course_url"] = f"{origin.rstrip('/')}/v2/web/studentLog/{resolved}"
        if auto_save:
            save_settings(cfg_path, cfg)
            print(f"[main] 已写回正确 classroom_id -> {cfg_path}")
    else:
        print(f"[main] {msg}")
    return resolved


def main() -> int:
    args = parse_args()
    print_banner()
    if args.list_rates:
        print(rate_help_text())
        for k, v in PRESETS.items():
            print(f"  {k:10s} -> {v}x")
        return 0
    if args.doctor:
        report = run_doctor(ROOT)
        print(format_doctor_report(report))
        return 0 if report.get("ok") else 2
    if args.i_accept_risk:
        print("[main] 已确认接受免责声明")

    cfg_path = Path(args.config)
    cfg = load_settings(cfg_path)

    # 先切配置档，再允许 --id/--url 覆盖
    if args.profile:
        if activate_profile(cfg, str(args.profile)):
            print(f"[main] 已激活配置档: {cfg.get('active_profile')} "
                  f"(classroom={cfg.get('classroom_id')})")
            if not args.no_save:
                save_settings(cfg_path, cfg)
        else:
            print(f"[!] 未找到配置档: {args.profile}")
            return 2

    # CLI 覆盖课堂
    if args.url:
        apply_classroom_input(cfg, args.url)
    if args.classroom_id:
        apply_classroom_input(cfg, str(args.classroom_id))

    # 有界面参数
    if args.headed:
        cfg["headless"] = False
    if args.headless:
        cfg["headless"] = True

    auto_save = not args.no_save

    # 首次向导
    need_wizard = not has_classroom(cfg)
    if args.setup or need_wizard:
        if not is_tty() and need_wizard:
            print("[!] 缺少 classroom_id，且当前非交互终端。")
            print("    请使用: python main.py --id <classroom_id>")
            print("    或:     python main.py --url <学习日志URL>")
            print("    或在终端运行: python main.py --setup")
            return 2
        if need_wizard or args.setup:
            if not is_tty():
                print("[!] --setup 需要交互终端")
                return 2
            cfg = wizard_first_run(cfg)
            if auto_save and has_classroom(cfg):
                save_settings(cfg_path, cfg)
                print(f"[main] 已保存配置 -> {cfg_path}")
            if args.setup:
                return 0 if has_classroom(cfg) else 2

    if not has_classroom(cfg):
        print("[!] 仍无有效 classroom_id")
        return 2

    # 是否直接动作（不进菜单）
    direct = bool(
        args.list_only
        or args.once
        or args.soft_only
        or (args.max is not None)
        or args.no_menu
        or not is_tty()
    )
    if args.menu:
        direct = False

    rate = resolve_playback_rate(cli_rate=args.rate, cfg=cfg, log=print)
    cfg["playback_rate"] = rate

    if args.absent_only:
        cfg["attend_filter"] = "absent"
    elif args.attend_filter:
        cfg["attend_filter"] = normalize_attend_filter(args.attend_filter)

    course_url, classroom_id, url_candidates = resolve_runtime(cfg)
    if not classroom_id:
        print("[!] 无法解析 classroom_id")
        return 2

    headless = bool(cfg.get("headless", False))
    max_videos = int(args.max if args.max is not None else cfg.get("max_videos", 0))
    if args.once:
        max_videos = 1

    storage = resolve_path(ROOT, cfg.get("storage_state", "data/storage_state.json"))
    progress_path = resolve_path(ROOT, cfg.get("progress_file", "data/progress.json"))
    failed_path = resolve_path(ROOT, cfg.get("failed_file", "data/failed.json"))
    soft_path = resolve_path(ROOT, cfg.get("soft_file", "data/soft.json"))
    partial_path = resolve_path(ROOT, cfg.get("partial_file", "data/partial.json"))
    wait_login = int(cfg.get("wait_login_timeout_sec", 180))
    max_watch = int(cfg.get("max_watch_sec", 7200))
    complete_ratio = float(cfg.get("complete_ratio", 0.65))
    confirm_grace_sec = int(cfg.get("confirm_grace_sec", 120))
    soft_boost = float(cfg.get("soft_boost", 0.10))
    require_platform = bool(cfg.get("require_platform_confirm", True))
    shot_on_err = bool(cfg.get("screenshot_on_error", True))
    skip_local_on_all = bool(cfg.get("skip_local_complete_on_all", True))
    resume_partial = bool(cfg.get("resume_partial", True))
    pause_cfg = cfg.get("pause_between_sec", [2, 6])
    if isinstance(pause_cfg, (list, tuple)) and len(pause_cfg) >= 2:
        pause_lo, pause_hi = float(pause_cfg[0]), float(pause_cfg[1])
    else:
        pause_lo, pause_hi = 2.0, 6.0

    progress = ProgressStore.load(progress_path, classroom_id=str(classroom_id))
    failed = FailedStore(failed_path)
    soft = SoftStore(soft_path)
    partial = PartialStore(partial_path)
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    print("-" * 56)
    print(f" classroom_id : {classroom_id}")
    print(f" course_url   : {course_url}")
    print(f" headless     : {headless}")
    print(f" rate         : {rate}x")
    print(f" complete≥    : {complete_ratio*100:.0f}%")
    print("-" * 56)

    # 直接模式：委托 jobs.run_automation（与 Web 同一可靠核心）
    if direct:
        if args.list_only:
            action = "list"
        elif args.soft_only:
            action = "soft"
        elif args.once or max_videos == 1:
            action = "once"
        else:
            action = "all"
        result = run_automation(
            root=ROOT,
            cfg=cfg,
            action=action,
            attend_filter=normalize_attend_filter(cfg.get("attend_filter", "all")),
            log=print,
        )
        if result.get("error"):
            print(f"[!] {result['error']}")
            return 2
        print("=" * 56)
        print(f"[main] {result.get('message')}")
        print("=" * 56)
        return 0 if result.get("ok") else 3

    # 菜单模式：浏览器会话保持，循环操作
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
        print(f"[main] 已登录，origin={origin}")
        fixed = ensure_resolved_classroom(
            page,
            classroom_id,
            cfg=cfg,
            cfg_path=cfg_path,
            origin=origin,
            auto_save=auto_save,
        )
        if fixed:
            classroom_id = fixed

        total_done = 0
        total_fail = 0

        while True:
            # 刷新运行时参数
            rate = float(cfg.get("playback_rate") or 1.25)
            complete_ratio = float(cfg.get("complete_ratio") or 0.65)
            course_url, classroom_id, url_candidates = resolve_runtime(cfg)
            if not classroom_id:
                print("[!] classroom_id 无效，请在设置中重新填写")
                cfg = settings_submenu(cfg)
                continue
            # 设置改过 ID 时再解析一次
            fixed = ensure_resolved_classroom(
                page,
                classroom_id,
                cfg=cfg,
                cfg_path=cfg_path,
                origin=origin,
                auto_save=auto_save,
            )
            if not fixed:
                cfg = settings_submenu(cfg)
                continue
            classroom_id = fixed

            print_main_menu(cfg, rate)
            action = pick_action()

            if action == "quit":
                break

            if action == "settings":
                cfg = settings_submenu(cfg)
                if auto_save and prompt_yes_no("保存到 config.yaml？", default=True):
                    save_settings(cfg_path, cfg)
                    print(f"[main] 已保存 -> {cfg_path}")
                # 倍速可能已改
                rate = resolve_playback_rate(
                    cli_rate=None, cfg=cfg, log=print
                )
                cfg["playback_rate"] = rate
                continue

            if action == "filter":
                cfg["attend_filter"] = pick_attend_filter(
                    str(cfg.get("attend_filter") or "all")
                )
                print(f"[main] 筛选 = {cfg['attend_filter']}")
                if auto_save:
                    save_settings(cfg_path, cfg)
                continue

            if action == "browser_id":
                cfg = capture_classroom_from_page(page, cfg, log=print)
                course_url, classroom_id, url_candidates = resolve_runtime(cfg)
                if auto_save and prompt_yes_no("保存到 config.yaml？", default=True):
                    save_settings(cfg_path, cfg)
                continue

            if action == "profiles":
                cfg = profiles_submenu(cfg)
                course_url, classroom_id, url_candidates = resolve_runtime(cfg)
                if auto_save and prompt_yes_no("保存到 config.yaml？", default=True):
                    save_settings(cfg_path, cfg)
                    print(f"[main] 已保存 -> {cfg_path}")
                continue

            # all_absent: 强制仅缺勤 + 全部观看
            force_af = None
            if action == "all_absent":
                action = "all"
                force_af = "absent"

            if action not in ("list", "once", "all", "soft"):
                print("  无效选项，请重新选择。")
                continue

            af = force_af or normalize_attend_filter(cfg.get("attend_filter", "all"))
            try:
                pending = load_pending_for_classroom(
                    page,
                    str(classroom_id),
                    origin=origin,
                    progress=progress,
                    soft=soft,
                    attend_filter=af,
                    log=print,
                    reconcile=True,
                )
            except LogsApiError as e:
                print(f"[!] {e}")
                continue
            print_pending(pending, soft=soft, classroom_id=str(classroom_id))

            if action == "list":
                continue
            if not pending:
                continue

            if action == "soft":
                pending = select_soft_targets(pending, soft, str(classroom_id))
                if not pending:
                    print("[main] 无 SOFT 待重试（或已全部转正）")
                    continue
                print(f"[main] 仅 SOFT 再跑：{len(pending)} 节")

            if action == "once":
                targets = pending[:1]
            elif action == "soft":
                targets = list(pending)
            else:
                targets, skipped_local = filter_skip_local_complete(
                    pending,
                    classroom_id=str(classroom_id),
                    complete_ratio=complete_ratio,
                    soft=soft,
                    partial_ratios=partial.local_ratio_map(str(classroom_id)),
                    enabled=skip_local_on_all,
                )
                if skipped_local:
                    print(
                        f"[main] 全部：跳过 SOFT 已达 ≥{complete_ratio*100:.0f}% 的 "
                        f"{len(skipped_local)} 节（确定本地 0→阈值；[9] 补平台）"
                    )
                if not targets:
                    if skipped_local:
                        print("[main] 待办均已 SOFT 达线，已跳过；用 [9] 补刷平台确认")
                    continue
            batch = watch_lesson_batch(
                page,
                session,
                classroom_id=str(classroom_id),
                origin=origin,
                targets=targets,
                rate=rate,
                complete_ratio=complete_ratio,
                max_watch=max_watch,
                progress=progress,
                failed=failed,
                soft=soft,
                data_dir=data_dir,
                pause_lo=pause_lo,
                pause_hi=pause_hi,
                confirm_grace_sec=confirm_grace_sec,
                soft_boost=soft_boost,
                require_platform=require_platform,
                retry_per_lesson=int(cfg.get("retry_per_lesson", 1)),
                shot_on_err=shot_on_err,
                log=print,
                should_cancel=None,
                update_state=False,
                partial=partial,
                resume_partial=resume_partial,
            )
            done = int(batch.get("done") or 0)
            fail = int(batch.get("fail") or 0)
            soft_n = int(batch.get("soft_done") or 0)
            total_done += done
            total_fail += fail
            print(
                f"[main] 本轮 确认 {done} / SOFT {soft_n} / 失败 {fail}"
                f"（累计确认 {total_done}/失败 {total_fail}）"
            )
            # 与 jobs 一致：观看后再对账一次
            try:
                reconcile_progress_with_platform(
                    page,
                    str(classroom_id),
                    progress,
                    origin=origin,
                    log=print,
                    soft=soft,
                )
            except Exception as e:
                print(f"[main] 结束对账跳过: {e}")

        session.save_state()
        if auto_save and has_classroom(cfg):
            save_settings(cfg_path, cfg)

    print("=" * 56)
    print(f"[main] 退出。断点: {progress_path}")
    if total_fail:
        print(f"  失败记录: {failed_path}")
    print("=" * 56)
    return 0 if total_fail == 0 else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[main] 用户中断，进度在 data/progress.json")
        raise SystemExit(130)
