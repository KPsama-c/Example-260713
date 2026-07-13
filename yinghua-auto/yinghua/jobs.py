"""后台任务：list / next(once) / all（CLI 与 Web / nfctl 共用）。"""

from __future__ import annotations

import random
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from yinghua.browser import BrowserSession
from yinghua.course import Section, list_pending
from yinghua.login import ensure_login
from yinghua.player import watch_current
from yinghua.progress import FailedStore, ProgressStore
from yinghua.settings import base_url_of, load_settings
from yinghua.util import origin_of, progress_key, resolve_path

LogFn = Callable[[str], None]


@dataclass
class JobState:
    running: bool = False
    action: str = ""
    message: str = "空闲"
    ok: bool | None = None
    pending_preview: list[dict[str, Any]] = field(default_factory=list)
    done: int = 0
    fail: int = 0
    progress: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=800))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)
        try:
            print(line)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "gbk"
            print(str(line).encode(enc, errors="replace").decode(enc, errors="replace"))

    def request_cancel(self) -> bool:
        with self._lock:
            if not self.running:
                return False
            self.cancel_requested = True
            self.message = "正在停止…"
            return True

    def is_cancel_requested(self) -> bool:
        with self._lock:
            return self.cancel_requested

    def set_progress(self, info: dict[str, Any]) -> None:
        with self._lock:
            self.progress = dict(info or {})

    def snapshot(self, *, since: int = 0) -> dict[str, Any]:
        with self._lock:
            all_logs = list(self.logs)
            total = len(all_logs)
            since = max(0, min(int(since or 0), total))
            return {
                "running": self.running,
                "action": self.action,
                "message": self.message,
                "ok": self.ok,
                "pending_preview": list(self.pending_preview),
                "done": self.done,
                "fail": self.fail,
                "progress": dict(self.progress),
                "cancel_requested": self.cancel_requested,
                "log_len": total,
                "log_since": since,
                "logs": all_logs[since:],
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
    """同步执行。action: list | next | once | all。"""
    log = log or print
    action = (action or "list").strip().lower()
    if action in ("once", "next"):
        action = "next"
    if action not in ("list", "next", "all"):
        return {"ok": False, "error": f"未知动作: {action}"}

    headless = bool(cfg.get("headless", False))
    storage = resolve_path(root, cfg.get("storage_state", "data/storage_state.json"))
    progress_path = resolve_path(root, cfg.get("progress_file", "data/progress.json"))
    failed_path = resolve_path(root, cfg.get("failed_file", "data/failed.json"))
    wait_login = int(cfg.get("wait_login_timeout_sec", 300))
    max_watch = int(cfg.get("max_watch_sec", 7200))
    complete_ratio = float(cfg.get("complete_ratio", 0.95))
    rate = float(cfg.get("playback_rate", 1.5))
    max_videos = int(cfg.get("max_videos") or 0)
    shot_on_err = bool(cfg.get("screenshot_on_error", True))
    pause_cfg = cfg.get("pause_between_sec", [2, 6])
    if isinstance(pause_cfg, (list, tuple)) and len(pause_cfg) >= 2:
        pause_lo, pause_hi = float(pause_cfg[0]), float(pause_cfg[1])
    else:
        pause_lo, pause_hi = 2.0, 6.0

    course_id = str(cfg.get("course_id") or base_url_of(cfg))
    progress = ProgressStore.load(progress_path, course_id=course_id)
    failed = FailedStore(failed_path)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    done_count = 0
    fail_count = 0
    pending_preview: list[dict[str, Any]] = []

    log(f"[job] action={action} base={base_url_of(cfg)} rate={rate}x headless={headless}")

    with BrowserSession(headless=headless, storage_state=storage) as session:
        page = session.page
        assert page is not None
        ok_login, _url = ensure_login(
            page, cfg=cfg, timeout_sec=wait_login, log=log
        )
        if not ok_login:
            if shot_on_err:
                session.screenshot(data_dir / "login_timeout.png")
            return {"ok": False, "error": "登录超时", "done": 0, "fail": 0}

        session.save_state()
        course_id = str(cfg.get("course_id") or origin_of(page.url, base_url_of(cfg)))
        progress.course_id = course_id

        pending = list_pending(page, cfg, progress, log=log)
        pending_preview = [s.to_dict() for s in pending[:50]]
        STATE.pending_preview = pending_preview

        if action == "list":
            for i, s in enumerate(pending[:30], 1):
                log(f"  {i:02d}. [{s.node_id}] {s.title[:60]}")
            if len(pending) > 30:
                log(f"  … 另有 {len(pending) - 30} 项")
            return {
                "ok": True,
                "action": "list",
                "pending": pending_preview,
                "pending_count": len(pending),
                "done": 0,
                "fail": 0,
            }

        if not pending:
            log("[job] 无待办视频")
            return {
                "ok": True,
                "action": action,
                "pending": [],
                "pending_count": 0,
                "done": 0,
                "fail": 0,
                "message": "无待办",
            }

        targets: list[Section]
        if action == "next":
            targets = pending[:1]
        else:
            targets = pending
            if max_videos > 0:
                targets = targets[:max_videos]

        for idx, sec in enumerate(targets):
            if STATE.is_cancel_requested():
                log("[job] 用户取消")
                break
            log(f"[job] ({idx+1}/{len(targets)}) {sec.title[:80]}")
            STATE.message = f"播放: {sec.title[:40]}"
            STATE.set_progress({"title": sec.title, "phase": "opening", "href": sec.href})
            try:
                page.goto(sec.href, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                page.wait_for_timeout(600)
                result = watch_current(
                    page,
                    rate=rate,
                    complete_ratio=complete_ratio,
                    max_watch_sec=max_watch,
                    cfg=cfg,
                    log=log,
                    should_cancel=STATE.is_cancel_requested,
                )
                cid = getattr(sec, "course_id", None) or course_id
                key = progress_key(cid, sec.node_id)
                if result.ok:
                    progress.mark_done(
                        key, sec.title, course_id=cid, node_id=sec.node_id
                    )
                    done_count += 1
                    log(f"[job] 完成并写断点: {sec.title[:60]}")
                    STATE.set_progress(
                        {
                            "title": sec.title,
                            "phase": "done",
                            "ratio": result.ratio,
                        }
                    )
                else:
                    fail_count += 1
                    failed.add(key, sec.title, result.reason)
                    log(f"[job] 失败: {result.reason} · {sec.title[:60]}")
                    if shot_on_err:
                        session.screenshot(
                            data_dir / f"fail_{sec.node_id}.png"
                        )
            except Exception as e:
                fail_count += 1
                failed.add(progress_key(course_id, sec.node_id), sec.title, str(e))
                log(f"[job] 异常: {e}")
                log(traceback.format_exc())
                if shot_on_err:
                    try:
                        session.screenshot(data_dir / f"err_{sec.node_id}.png")
                    except Exception:
                        pass

            session.save_state()
            if idx < len(targets) - 1 and not STATE.is_cancel_requested():
                delay = random.uniform(pause_lo, pause_hi)
                log(f"[job] 间隔 {delay:.1f}s")
                time.sleep(delay)

        STATE.done = done_count
        STATE.fail = fail_count
        return {
            "ok": fail_count == 0,
            "action": action,
            "done": done_count,
            "fail": fail_count,
            "pending_count": max(0, len(pending) - done_count),
            "pending": pending_preview,
        }


def start_job_async(
    *,
    root: Path,
    cfg: dict[str, Any],
    action: str,
) -> tuple[bool, str]:
    global _worker
    if STATE.running:
        return False, "已有任务在运行"
    action = (action or "list").strip().lower()
    if action in ("once",):
        action = "next"
    if action not in ("list", "next", "all", "stop"):
        return False, "action 必须是 list/next/all/stop"
    if action == "stop":
        ok = STATE.request_cancel()
        return (True, "已请求停止") if ok else (False, "当前无运行任务")

    def _run() -> None:
        STATE.running = True
        STATE.action = action
        STATE.ok = None
        STATE.done = 0
        STATE.fail = 0
        STATE.cancel_requested = False
        STATE.message = f"运行中: {action}"
        try:
            result = run_automation(root=root, cfg=cfg, action=action, log=STATE.log)
            STATE.ok = bool(result.get("ok"))
            STATE.done = int(result.get("done") or 0)
            STATE.fail = int(result.get("fail") or 0)
            STATE.message = result.get("message") or (
                "完成" if STATE.ok else result.get("error") or "结束"
            )
            if result.get("pending") is not None:
                STATE.pending_preview = list(result.get("pending") or [])
        except Exception as e:
            STATE.ok = False
            STATE.message = f"异常: {e}"
            STATE.log(traceback.format_exc())
        finally:
            STATE.running = False
            STATE.cancel_requested = False

    _worker = threading.Thread(target=_run, daemon=True)
    _worker.start()
    return True, f"已启动: {action}"


def clear_progress_store(root: Path, cfg: dict[str, Any]) -> int:
    path = resolve_path(root, cfg.get("progress_file", "data/progress.json"))
    store = ProgressStore.load(path)
    return store.clear()


def clear_failed_store(root: Path, cfg: dict[str, Any]) -> int:
    path = resolve_path(root, cfg.get("failed_file", "data/failed.json"))
    return FailedStore(path).clear()
