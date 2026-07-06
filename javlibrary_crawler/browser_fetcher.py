from __future__ import annotations

import logging
from pathlib import Path

from .errors import JavLibraryBlockedError, JavLibraryFetchError, JavLibraryTimeoutError
from .fetcher import FetchResponse, looks_blocked

logger = logging.getLogger(__name__)


class BrowserFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        user_agent: str | None = None,
        proxy: str | None = None,
        profile_dir: str | None = None,
        channel: str | None = None,
        headless: bool = False,
        wait_seconds: float = 60.0,
    ) -> None:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise JavLibraryFetchError(
                "浏览器模式需要安装 playwright：pip install '.[browser]' 后再运行 playwright install chromium"
            ) from exc

        self._playwright_timeout_error = PlaywrightTimeoutError
        self._sync_playwright = sync_playwright
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.proxy = proxy
        self.profile_dir = Path(profile_dir or ".javlibrary-browser").expanduser().resolve()
        self.channel = channel
        self.headless = headless
        self.wait_seconds = wait_seconds
        self._playwright = None
        self._context = None

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def get(self, url: str) -> FetchResponse:
        context = self._ensure_context()
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=int(self.timeout_seconds * 1000))
            self._wait_for_challenge_if_needed(page)
            text = page.content()
            if looks_blocked(text):
                raise JavLibraryBlockedError("番号数据源仍在 Cloudflare 验证页，请在打开的浏览器里完成验证后重试")
            return FetchResponse(url=page.url, status_code=200, text=text)
        except self._playwright_timeout_error as exc:
            raise JavLibraryTimeoutError("番号数据源浏览器请求超时，请稍后再试") from exc
        finally:
            page.close()

    def post(self, url: str, data: dict[str, str] | None = None) -> FetchResponse:
        raise JavLibraryFetchError("浏览器模式暂不支持需要 POST 的番号数据源，请使用 curl 模式")

    def _ensure_context(self):
        if self._context is not None:
            return self._context

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = self._sync_playwright().start()
        launch_kwargs: dict[str, object] = {
            "headless": self.headless,
            "viewport": {"width": 1365, "height": 900},
            "locale": "zh-CN",
        }
        if self.proxy:
            launch_kwargs["proxy"] = {"server": self.proxy}
        if self.channel:
            launch_kwargs["channel"] = self.channel
        if self.user_agent:
            launch_kwargs["user_agent"] = self.user_agent
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            **launch_kwargs,
        )
        return self._context

    def _wait_for_challenge_if_needed(self, page) -> None:
        deadline_ms = int(max(1.0, self.wait_seconds) * 1000)
        try:
            page.wait_for_function(
                """
                () => {
                    const text = document.documentElement.innerText || '';
                    const title = document.title || '';
                    return !/just a moment|checking your browser|cloudflare/i.test(title + ' ' + text);
                }
                """,
                timeout=deadline_ms,
            )
        except self._playwright_timeout_error:
            logger.info("Jav metadata browser page is still blocked by Cloudflare: %s", page.url)
