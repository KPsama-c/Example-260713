"""浏览器启动、截图、storage_state。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright


class BrowserSession:
    def __init__(
        self,
        *,
        headless: bool = False,
        storage_state: str | Path | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.headless = headless
        self.storage_state = Path(storage_state) if storage_state else None
        self.user_agent = user_agent
        self._pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def start(self) -> Page:
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        opts: dict[str, Any] = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if self.user_agent:
            opts["user_agent"] = self.user_agent
        if self.storage_state and self.storage_state.exists():
            opts["storage_state"] = str(self.storage_state)

        self.context = self.browser.new_context(**opts)
        self.context.set_default_timeout(30_000)
        self.page = self.context.new_page()
        return self.page

    def save_state(self) -> None:
        if not self.context or not self.storage_state:
            return
        self.storage_state.parent.mkdir(parents=True, exist_ok=True)
        self.context.storage_state(path=str(self.storage_state))

    def screenshot(self, path: str | Path, *, full_page: bool = False) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        assert self.page is not None
        self.page.screenshot(path=str(p), full_page=full_page)
        return p

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self.page = None
        self.context = None
        self.browser = None
        self._pw = None
