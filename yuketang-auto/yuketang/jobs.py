"""后台任务：列表 / 观看一节 / 全部（供 CLI 与 Web UI 共用）。"""

from __future__ import annotations

import random
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from yuketang.browser import BrowserSession
from yuketang.classrooms import resolve_classroom_id as resolve_joined_classroom
from yuketang.login import ensure_login
from yuketang.logs import LogsApiError, list_pending_replays
from yuketang.progress import FailedStore, ProgressStore
from yuketang.rate import resolve_playback_rate
from yuketang.replay import watch_replay
from yuketang.settings import has_classroom, resolve_runtime, save_settings

LogFn = Callable[[str], None]


def _origin_of(url: str) -> str:
    p = urlparse(url)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return "https://www.yuketang.cn"


def _resolve_path(base: Path, p: str | Path) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (base / path).resolve()


@dataclass
class JobState:
    running: bool = False
    action: str = ""
    message: str = "空闲"
    ok: bool | None = None
    pending_preview: list[dict[str, Any]] = field(default_factory=list)
    done: int = 0
    fail: int = 0
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=500))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)
        print(line)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "action": self.action,
                "message": self.message,
                "ok": self.ok,
                "pending_preview": list(self.pending_preview),
                "done": self.done,
                "fail": self.fail,
                "logs": list(self.logs)[-200:],
            }


STATE = JobState()
_worker: threading.Thread | None = None


def run_automation(
    *,
    root: Path,
    cfg: dict[str, Any],
    action: str,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """同步执行。action: list | once | all。"""
    log = log or print
    action = (action or "list").strip().lower()
    if action not in ("list", "once", "all"):
        return {"ok": False, "error": f"未知动作: {action}"}

    if not has_classroom(cfg):
        return {"ok": False, "error": "请先填写 classroom_id 或学习日志 URL"}

    rate = resolve_playback_rate(cli_rate=None, cfg=cfg, log=log)
    course_url, classroom_id, url_candidates = resolve_runtime(cfg)
    if not classroom_id:
        return {"ok": False, "error": "无法解析 classroom_id"}

    headless = bool(cfg.get("headless", False))
    storage = _resolve_path(root, cfg.get("storage_state", "data/storage_state.json"))
    progress_path = _resolve_path(root, cfg.get("progress_file", "data/progress.json"))
    failed_path = _resolve_path(root, cfg.get("failed_file", "data/failed.json"))
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
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    done_count = 0
    fail_count = 0
    pending_preview: list[dict[str, Any]] = []

    log(f"[job] action={action} classroom={classroom_id} rate={rate}x headless={headless}")

    with BrowserSession(headless=headless, storage_state=storage) as session:
        page = session.page
        assert page is not None
        ok_login, course_url = ensure_login(
            page,
            course_url=course_url,
            timeout_sec=wait_login,
            log=log,
            candidate_urls=url_candidates,
        )
        if not ok_login:
            if shot_on_err:
                session.screenshot(data_dir / "login_timeout.png")
            return {"ok": False, "error": "登录超时", "done": 0, "fail": 0}

        session.save_state()
        origin = _origin_of(page.url or course_url)

        # course_id → classroom_id 自动纠正（常见误填）
        resolved, _rooms, resolve_msg = resolve_joined_classroom(
            page, str(classroom_id), log=log
        )
        if not resolved:
            return {
                "ok": False,
                "error": resolve_msg or "无法解析 classroom_id",
                "done": 0,
                "fail": 0,
            }
        if resolved != str(classroom_id):
            log(f"[job] classroom_id: {classroom_id} → {resolved}")
            classroom_id = resolved
            cfg["classroom_id"] = resolved
            cfg["course_url"] = f"{origin}/v2/web/studentLog/{resolved}"
            try:
                save_settings(root / "config.yaml", cfg)
                log("[job] 已写回正确 classroom_id 到 config.yaml")
            except Exception as e:
                log(f"[job] 写回配置失败（可忽略）: {e}")
        else:
            log(f"[job] {resolve_msg}")

        try:
            page.goto(
                f"{origin}/v2/web/studentLog/{classroom_id}",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(1500)
        except Exception as e:
            log(f"[job] 打开日志页警告: {e}")

        try:
            pending = list_pending_replays(
                page,
                classroom_id,
                progress_keys=set(progress.completed),
                origin=origin,
                log=log,
            )
        except LogsApiError as e:
            return {
                "ok": False,
                "error": str(e),
                "done": 0,
                "fail": 0,
                "classroom_id": classroom_id,
            }
        pending_preview = [
            {
                "title": it.title,
                "lesson_id": it.lesson_id,
                "attend": bool(it.attend_status),
            }
            for it in pending
        ]
        log(f"[job] 待观看 {len(pending)} 节")
        for i, it in enumerate(pending, 1):
            log(f"  {i}. {it.title}")

        if action == "list" or not pending:
            session.save_state()
            return {
                "ok": True,
                "done": 0,
                "fail": 0,
                "pending": pending_preview,
                "classroom_id": classroom_id,
                "message": "无待办" if not pending else f"共 {len(pending)} 节待观看",
            }

        limit = 1 if action == "once" else len(pending)
        targets = pending[:limit]
        for idx, item in enumerate(targets, 1):
            log("-" * 40)
            log(f"[job] ({idx}/{len(targets)}) {item.title}")
            ok = watch_replay(
                page,
                classroom_id=classroom_id,
                lesson_id=item.lesson_id,
                origin=origin,
                rate=rate,
                complete_ratio=complete_ratio,
                max_watch_sec=max_watch,
                log=log,
            )
            if ok:
                progress.mark_done(item.key, item.title)
                done_count += 1
                session.save_state()
                log("[job] ✓ 本节完成")
            else:
                fail_count += 1
                failed.add(item.key, item.title, "watch_replay failed")
                if shot_on_err:
                    session.screenshot(data_dir / f"fail_replay_{item.lesson_id}.png")
                log("[job] ✗ 本节失败")
            if idx < len(targets):
                delay = random.uniform(pause_lo, pause_hi)
                log(f"[job] 休息 {delay:.1f}s")
                time.sleep(delay)

        session.save_state()

    return {
        "ok": fail_count == 0,
        "done": done_count,
        "fail": fail_count,
        "pending": pending_preview,
        "classroom_id": classroom_id,
        "message": f"成功 {done_count}, 失败 {fail_count}",
    }


def start_job_async(*, root: Path, cfg: dict[str, Any], action: str) -> tuple[bool, str]:
    """在后台线程启动任务。若已有任务在跑则拒绝。"""
    global _worker
    if STATE.running:
        return False, "已有任务在运行，请等待结束"

    def _run() -> None:
        STATE.running = True
        STATE.action = action
        STATE.message = "运行中…"
        STATE.ok = None
        STATE.done = 0
        STATE.fail = 0
        STATE.pending_preview = []
        try:
            result = run_automation(root=root, cfg=cfg, action=action, log=STATE.log)
            STATE.ok = bool(result.get("ok"))
            STATE.done = int(result.get("done") or 0)
            STATE.fail = int(result.get("fail") or 0)
            STATE.pending_preview = list(result.get("pending") or [])
            STATE.message = str(result.get("message") or result.get("error") or "完成")
            if result.get("error"):
                STATE.log(f"[job] 错误: {result['error']}")
                STATE.ok = False
        except Exception as e:
            STATE.ok = False
            STATE.message = f"异常: {e}"
            STATE.log(traceback.format_exc())
        finally:
            STATE.running = False

    _worker = threading.Thread(target=_run, name="yuketang-job", daemon=True)
    _worker.start()
    return True, "任务已启动"
