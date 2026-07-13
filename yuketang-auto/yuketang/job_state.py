"""任务运行时状态（Web / 异步任务共享单例）。"""

from __future__ import annotations

import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

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
    soft_done: int = 0  # 本地达标但平台未确认
    progress: dict[str, Any] = field(default_factory=dict)
    batch: dict[str, Any] = field(default_factory=dict)
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
            title = str(info.get("title") or "")[:36]
            pct = info.get("pct")
            eta = info.get("eta_text") or ""
            phase = info.get("phase") or ""
            if phase == "playing" and pct is not None:
                self.message = f"播放中 {pct}% 约{eta}达线" + (f" {title}" if title else "")
            elif phase == "opening":
                self.message = f"打开回放… {title}".strip()
            elif phase == "done":
                conf = info.get("platform_confirmed")
                tag = "平台已确认" if conf else "本地达标"
                self.message = f"本节{tag} {title}".strip()
            elif phase == "cancelled":
                self.message = "已取消"

    def clear_display_logs(self) -> None:
        """清空界面用日志缓冲（不删磁盘文件）。"""
        with self._lock:
            self.logs.clear()

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
                "soft_done": self.soft_done,
                "progress": dict(self.progress),
                "batch": dict(self.batch),
                "cancel_requested": self.cancel_requested,
                "log_len": total,
                "log_since": since,
                "logs": all_logs[since:],
            }


STATE = JobState()
