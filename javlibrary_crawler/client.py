from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .errors import (
    JavLibraryBlockedError,
    JavLibraryError,
    JavLibraryFetchError,
    JavLibraryNotFoundError,
    JavLibraryTimeoutError,
)
from .fetcher import CurlCffiFetcher, HttpFetcher
from .models import JavLibrarySearchItem, JavLibraryVideo
from .normalizer import normalize_code
from .providers import ProviderConfig, create_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JavLibraryCrawlerConfig:
    base_url: str = "https://www.javlibrary.com"
    language: str = "cn"
    provider_order: tuple[str, ...] = ("javdb", "javlibrary", "jav321", "javbus")
    javdb_base_url: str = "https://javdb.com"
    javbus_base_url: str = "https://www.javbus.com"
    jav321_base_url: str = "https://www.jav321.com"
    timeout_seconds: float = 8.0
    total_timeout_seconds: float = 15.0
    fetcher: str = "curl"
    user_agent: str | None = None
    cookie: str | None = None
    proxy: str | None = None
    impersonate: str = "random"
    retry_times: int = 1
    browser_profile_dir: str | None = None
    browser_channel: str | None = None
    browser_headless: bool = False
    browser_wait_seconds: float = 60.0


class JavLibraryCrawler:
    def __init__(
        self,
        config: JavLibraryCrawlerConfig | None = None,
        fetcher: HttpFetcher | None = None,
    ) -> None:
        self.config = config or JavLibraryCrawlerConfig()
        self._owns_fetcher = fetcher is None
        raw_fetcher = fetcher or self._create_fetcher()
        self.fetcher = _DeadlineFetcher(raw_fetcher, self.config.total_timeout_seconds)

    def close(self) -> None:
        if self._owns_fetcher:
            self.fetcher.close()

    def lookup(self, raw_code: str) -> JavLibraryVideo:
        code = normalize_code(raw_code)
        errors: list[tuple[str, JavLibraryError]] = []
        provider_config = ProviderConfig(
            javlibrary_base_url=self.config.base_url,
            javlibrary_language=self.config.language,
            javdb_base_url=self.config.javdb_base_url,
            javbus_base_url=self.config.javbus_base_url,
            jav321_base_url=self.config.jav321_base_url,
        )

        for provider_name in self.config.provider_order:
            provider = create_provider(provider_name, self.fetcher, provider_config)
            if provider is None:
                logger.warning("Unknown Jav metadata provider configured: %s", provider_name)
                continue
            try:
                return provider.lookup(code)
            except JavLibraryError as exc:
                logger.info("Jav metadata provider %s failed for %s: %s", provider.source, code, exc.user_message)
                errors.append((provider.source, exc))

        raise _aggregate_lookup_error(code, errors)

    def search_javdb(self, query: str, page: int = 1, limit: int = 10) -> list[JavLibrarySearchItem]:
        provider = self._javdb_provider()
        return provider.search(query.strip(), page=page, limit=limit)

    def search_javdb_actor(self, query: str, page: int = 1, limit: int = 10) -> list[JavLibrarySearchItem]:
        provider = self._javdb_provider()
        return provider.search_actor(query.strip(), page=page, limit=limit)

    def javdb_ranking(self, period: str, page: int = 1, limit: int = 10) -> list[JavLibrarySearchItem]:
        provider = self._javdb_provider()
        return provider.ranking(period, page=page, limit=limit)

    def _javdb_provider(self):
        provider_config = ProviderConfig(
            javlibrary_base_url=self.config.base_url,
            javlibrary_language=self.config.language,
            javdb_base_url=self.config.javdb_base_url,
            javbus_base_url=self.config.javbus_base_url,
            jav321_base_url=self.config.jav321_base_url,
        )
        provider = create_provider("javdb", self.fetcher, provider_config)
        if provider is None:
            raise JavLibraryFetchError("JavDB 数据源不可用")
        return provider

    def _create_fetcher(self) -> object:
        if self.config.fetcher.lower() == "browser":
            from .browser_fetcher import BrowserFetcher

            return BrowserFetcher(
                timeout_seconds=self.config.timeout_seconds,
                user_agent=self.config.user_agent,
                proxy=self.config.proxy,
                profile_dir=self.config.browser_profile_dir,
                channel=self.config.browser_channel,
                headless=self.config.browser_headless,
                wait_seconds=self.config.browser_wait_seconds,
            )

        if self.config.fetcher.lower() in {"curl", "curl_cffi", "curl-cffi"}:
            return CurlCffiFetcher(
                timeout_seconds=self.config.timeout_seconds,
                user_agent=self.config.user_agent,
                cookie=self.config.cookie,
                proxy=self.config.proxy,
                impersonate=self.config.impersonate,
                retry_times=self.config.retry_times,
            )

        return HttpFetcher(
            timeout_seconds=self.config.timeout_seconds,
            user_agent=self.config.user_agent,
            cookie=self.config.cookie,
            proxy=self.config.proxy,
        )


def lookup(raw_code: str, config: JavLibraryCrawlerConfig | None = None) -> JavLibraryVideo:
    crawler = JavLibraryCrawler(config)
    try:
        return crawler.lookup(raw_code)
    finally:
        crawler.close()


def _aggregate_lookup_error(code: str, errors: list[tuple[str, JavLibraryError]]) -> JavLibraryError:
    if not errors:
        return JavLibraryNotFoundError(f"没有找到 {code} 的番号信息")

    sources = "、".join(source for source, _ in errors)
    if all(isinstance(exc, JavLibraryNotFoundError) for _, exc in errors):
        return JavLibraryNotFoundError(f"没有找到 {code} 的番号信息，已尝试：{sources}")
    if all(isinstance(exc, JavLibraryBlockedError) for _, exc in errors):
        return JavLibraryBlockedError(f"番号数据源暂时不可用，已尝试：{sources}")
    if all(isinstance(exc, JavLibraryTimeoutError) for _, exc in errors):
        return JavLibraryTimeoutError(f"番号信息查询超时，已尝试：{sources}")
    return JavLibraryFetchError(f"番号信息查询失败，已尝试：{sources}")


class _DeadlineFetcher:
    def __init__(self, wrapped: object, total_timeout_seconds: float) -> None:
        self._wrapped = wrapped
        self._deadline = time.monotonic() + total_timeout_seconds if total_timeout_seconds > 0 else None

    def close(self) -> None:
        close = getattr(self._wrapped, "close", None)
        if callable(close):
            close()

    def get(self, url: str):
        return self._call("get", url)

    def post(self, url: str, data: dict[str, str] | None = None):
        return self._call("post", url, data)

    def _call(self, method_name: str, *args: object):
        timeout = self._remaining_timeout()
        previous_timeout = getattr(self._wrapped, "timeout_seconds", None)
        if timeout is not None and isinstance(previous_timeout, (int, float)):
            setattr(self._wrapped, "timeout_seconds", max(0.1, min(float(previous_timeout), timeout)))
        try:
            method = getattr(self._wrapped, method_name)
            return method(*args)
        finally:
            if isinstance(previous_timeout, (int, float)):
                setattr(self._wrapped, "timeout_seconds", previous_timeout)

    def _remaining_timeout(self) -> float | None:
        if self._deadline is None:
            return None
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise JavLibraryTimeoutError("番号信息查询总超时，请稍后再试")
        return remaining
