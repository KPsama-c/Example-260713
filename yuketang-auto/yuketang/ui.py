"""终端交互：首次向导、主菜单、设置。"""

from __future__ import annotations

import sys
from typing import Any, Callable

from yuketang.rate import PRESETS, parse_rate_value
from yuketang.settings import apply_classroom_input, has_classroom
from yuketang.urls import resolve_classroom_id


def is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def prompt_line(msg: str, default: str | None = None) -> str:
    if default is not None and default != "":
        suffix = f" [{default}]"
    else:
        suffix = ""
    try:
        raw = input(f"{msg}{suffix}: ").strip()
    except EOFError:
        return default or ""
    if not raw and default is not None:
        return default
    return raw


def prompt_yes_no(msg: str, *, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = prompt_line(f"{msg} ({hint})", "y" if default else "n").lower()
    if not raw:
        return default
    return raw in ("y", "yes", "是", "1", "true")


def prompt_classroom(cfg: dict[str, Any]) -> dict[str, Any]:
    print()
    print("请提供课堂信息（二选一）：")
    print("  · 粘贴学习日志完整 URL")
    print("    例: https://www.yuketang.cn/v2/web/studentLog/1234567890")
    print("  · 或只输入 classroom_id 数字")
    print("  移动端 URL 请用第二段 ID，不要用 course_id")
    print()
    while True:
        raw = prompt_line("URL 或 classroom_id")
        if not raw:
            print("  不能为空，请再试一次。")
            continue
        apply_classroom_input(cfg, raw)
        if has_classroom(cfg):
            print(f"  ✓ classroom_id = {cfg.get('classroom_id')}")
            return cfg
        print("  无法识别，请检查后重试。")


def prompt_rate_value(current: float = 1.25) -> float:
    print(f"  常用: 1.0 / 1.25 / 1.5  或预设 {', '.join(list(PRESETS)[:6])}…")
    raw = prompt_line("播放倍速", str(current))
    parsed = parse_rate_value(raw if raw else current)
    if parsed is None:
        print(f"  无效，保持 {current}x")
        return float(current)
    return float(parsed)


def wizard_first_run(cfg: dict[str, Any]) -> dict[str, Any]:
    print()
    print("=" * 56)
    print(" 首次使用向导（无需手写 config.yaml）")
    print("=" * 56)
    cfg = prompt_classroom(cfg)
    cfg["playback_rate"] = prompt_rate_value(float(cfg.get("playback_rate") or 1.25))
    headed = prompt_yes_no("使用有界面浏览器（首次登录建议是）", default=True)
    cfg["headless"] = not headed
    ratio_raw = prompt_line("有效进度比例 0~1", str(cfg.get("complete_ratio") or 0.65))
    try:
        r = float(ratio_raw)
        if 0.5 <= r <= 1.0:
            cfg["complete_ratio"] = r
    except ValueError:
        pass
    print()
    print("向导完成。")
    return cfg


def print_main_menu(cfg: dict[str, Any], rate: float) -> None:
    cid = cfg.get("classroom_id") or "?"
    print()
    print("=" * 56)
    print(" 主菜单")
    print(f"  课堂 ID : {cid}")
    print(f"  倍速    : {rate}x")
    print(f"  有效线  : {float(cfg.get('complete_ratio', 0.65))*100:.0f}%")
    print(f"  界面    : {'无头' if cfg.get('headless') else '有界面'}")
    af = str(cfg.get("attend_filter") or "all")
    af_label = {"all": "不限签到", "absent": "仅缺勤", "present": "仅已签到"}.get(af, af)
    print(f"  筛选    : {af_label}")
    print("-" * 56)
    print("  [1] 查看待办列表（当前筛选）")
    print("  [2] 观看下一节（1 节）")
    print("  [3] 全部观看（当前筛选 / 不限签到可先选 6）")
    print("  [4] 仅缺勤 · 全部观看")
    print("  [5] 设置（倍速 / 阈值 / 换课 / 筛选 / 界面）")
    print("  [6] 切换筛选：不限签到 / 仅缺勤 / 仅已签到")
    print("  [7] 从浏览器当前页识别课堂 ID")
    print("  [0] 退出")
    print("=" * 56)


def pick_action() -> str:
    raw = prompt_line("请选择", "1").strip().lower()
    if raw in ("0", "q", "quit", "exit", "退出"):
        return "quit"
    if raw in ("1", "l", "list"):
        return "list"
    if raw in ("2", "n", "next", "once"):
        return "once"
    if raw in ("3", "a", "all"):
        return "all"
    if raw in ("4", "aa", "absent_all", "缺勤"):
        return "all_absent"
    if raw in ("5", "s", "set", "settings"):
        return "settings"
    if raw in ("6", "f", "filter"):
        return "filter"
    if raw in ("7", "b", "browser"):
        return "browser_id"
    return raw


def pick_attend_filter(current: str = "all") -> str:
    print("  筛选：")
    print("  [1] 全部未看回放（无论是否签到）")
    print("  [2] 仅缺勤且未看回放")
    print("  [3] 仅已签到且未看回放")
    cur = {"all": "1", "absent": "2", "present": "3"}.get(
        (current or "all").lower(), "1"
    )
    raw = prompt_line("选择筛选", cur).strip()
    if raw in ("2", "absent", "缺勤"):
        return "absent"
    if raw in ("3", "present", "签到"):
        return "present"
    return "all"


def settings_submenu(cfg: dict[str, Any]) -> dict[str, Any]:
    print()
    print("--- 设置 ---")
    print("  [1] 改倍速")
    print("  [2] 改有效进度比例")
    print("  [3] 换课（URL / ID）")
    print("  [4] 切换 有界面/无头")
    print("  [5] 观看筛选（全部 / 仅缺勤 / 仅已签到）")
    print("  [0] 返回")
    choice = prompt_line("设置项", "0")
    if choice == "1":
        cfg["playback_rate"] = prompt_rate_value(float(cfg.get("playback_rate") or 1.25))
    elif choice == "2":
        raw = prompt_line("complete_ratio", str(cfg.get("complete_ratio") or 0.65))
        try:
            r = float(raw)
            if 0.5 <= r <= 1.0:
                cfg["complete_ratio"] = r
                print(f"  ✓ 有效线 = {r*100:.0f}%")
        except ValueError:
            print("  无效数字")
    elif choice == "3":
        cfg = prompt_classroom(cfg)
    elif choice == "4":
        cfg["headless"] = not prompt_yes_no(
            "使用有界面浏览器", default=not bool(cfg.get("headless"))
        )
        print(f"  ✓ headless = {cfg['headless']}")
    elif choice == "5":
        cfg["attend_filter"] = pick_attend_filter(str(cfg.get("attend_filter") or "all"))
        print(f"  ✓ attend_filter = {cfg['attend_filter']}")
    return cfg


def capture_classroom_from_page(page: Any, cfg: dict[str, Any], log: Callable[[str], None] = print) -> dict[str, Any]:
    """提示用户在已打开的浏览器里进入学习日志，再从 page.url 解析。"""
    log("")
    log("请在浏览器中打开该课的「学习日志」页面，然后回到终端。")
    log("地址应类似: .../v2/web/studentLog/<classroom_id>")
    prompt_line("进入学习日志后按回车继续", "")
    try:
        url = page.url or ""
    except Exception:
        url = ""
    log(f"  当前页: {url}")
    cid = resolve_classroom_id(url, None)
    if cid:
        apply_classroom_input(cfg, url)
        log(f"  ✓ 已识别 classroom_id = {cid}")
    else:
        log("  未能从当前页解析，请改用菜单 [4] 手动输入。")
    return cfg
