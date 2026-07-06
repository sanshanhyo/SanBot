from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import JavLibraryBlockedError, JavLibraryFetchError, JavLibraryTimeoutError

logger = logging.getLogger(__name__)

BLOCKED_NEEDLES = (
    "cf-browser-verification",
    "cf-challenge",
    "cloudflare",
    "checking your browser",
    "just a moment",
    "turnstile",
)
DEFAULT_CURL_IMPERSONATES = ("chrome123", "chrome124", "chrome131", "firefox135", "chrome")
RETRY_STATUS_CODES = {408, 429, 504}


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status_code: int
    text: str


class HttpFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        user_agent: str | None = None,
        cookie: str | None = None,
        proxy: str | None = None,
    ) -> None:
        headers = {
            "User-Agent": user_agent
            or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7,ja;q=0.6",
        }
        if cookie:
            headers["Cookie"] = cookie
        self.timeout_seconds = timeout_seconds
        self._client = httpx.Client(
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=True,
            proxy=proxy,
            http2=False,
        )

    def close(self) -> None:
        self._client.close()

    def get(self, url: str) -> FetchResponse:
        return self._request("GET", url)

    def post(self, url: str, data: dict[str, str] | None = None) -> FetchResponse:
        return self._request("POST", url, data=data)

    def _request(self, method: str, url: str, data: dict[str, str] | None = None) -> FetchResponse:
        try:
            response = self._client.request(method, url, data=data, timeout=self.timeout_seconds)
        except httpx.TimeoutException as exc:
            raise JavLibraryTimeoutError("番号数据源请求超时，请稍后再试") from exc
        except httpx.HTTPError as exc:
            raise JavLibraryFetchError("番号数据源请求失败，请稍后再试") from exc

        text = response.text
        if response.status_code in {403, 429, 503} or looks_blocked(text):
            logger.info("Jav metadata source blocked request status=%s url=%s", response.status_code, url)
            raise JavLibraryBlockedError("番号数据源阻断了请求，请稍后再试或检查网络/Cookie")
        if response.status_code >= 400:
            raise JavLibraryFetchError(f"番号数据源请求失败，HTTP {response.status_code}")
        return FetchResponse(url=str(response.url), status_code=response.status_code, text=text)


class CurlCffiFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        user_agent: str | None = None,
        cookie: str | None = None,
        proxy: str | None = None,
        impersonate: str = "random",
        retry_times: int = 1,
    ) -> None:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:
            raise JavLibraryFetchError("番号数据源 curl 模式需要安装 curl-cffi") from exc

        self._headers = browser_like_headers(user_agent)
        if cookie:
            self._headers["Cookie"] = cookie
        self.timeout_seconds = timeout_seconds
        self.proxy = proxy
        self.impersonates = _parse_impersonates(impersonate)
        self.retry_times = max(1, retry_times)
        self._requests = curl_requests
        self._client = self._new_session()

    def close(self) -> None:
        self._client.close()

    def get(self, url: str) -> FetchResponse:
        return self._request("GET", url)

    def post(self, url: str, data: dict[str, str] | None = None) -> FetchResponse:
        return self._request("POST", url, data=data)

    def _request(self, method: str, url: str, data: dict[str, str] | None = None) -> FetchResponse:
        last_timeout: BaseException | None = None
        last_request_error: BaseException | None = None

        for attempt in range(self.retry_times):
            try:
                response = self._client.request(
                    method,
                    url,
                    data=data,
                    allow_redirects=True,
                    timeout=self.timeout_seconds,
                )
            except self._requests.exceptions.Timeout as exc:
                last_timeout = exc
                if attempt < self.retry_times - 1:
                    self._sleep_before_retry(attempt)
                    self._rotate_session()
                    continue
                raise JavLibraryTimeoutError("番号数据源请求超时，请稍后再试") from exc
            except self._requests.exceptions.RequestException as exc:
                last_request_error = exc
                if attempt < self.retry_times - 1:
                    self._sleep_before_retry(attempt)
                    self._rotate_session()
                    continue
                raise JavLibraryFetchError("番号数据源请求失败，请稍后再试") from exc

            if response.status_code in RETRY_STATUS_CODES and attempt < self.retry_times - 1:
                self._sleep_before_retry(attempt)
                self._rotate_session()
                continue
            break
        else:
            if last_timeout is not None:
                raise JavLibraryTimeoutError("番号数据源请求超时，请稍后再试") from last_timeout
            if last_request_error is not None:
                raise JavLibraryFetchError("番号数据源请求失败，请稍后再试") from last_request_error
            raise JavLibraryFetchError("番号数据源请求失败，请稍后再试")

        text = response.text
        if response.status_code in {403, 429, 503} or looks_blocked(text):
            logger.info("Jav metadata source blocked curl request status=%s url=%s", response.status_code, url)
            raise JavLibraryBlockedError("番号数据源阻断了请求，请稍后再试或检查网络/Cookie")
        if response.status_code >= 400:
            raise JavLibraryFetchError(f"番号数据源请求失败，HTTP {response.status_code}")
        return FetchResponse(url=str(response.url), status_code=response.status_code, text=text)

    def _new_session(self):
        session_kwargs: dict[str, Any] = {
            "impersonate": random.choice(self.impersonates),
            "headers": self._headers,
        }
        if self.proxy:
            session_kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        return self._requests.Session(**session_kwargs)

    def _rotate_session(self) -> None:
        self.close()
        self._client = self._new_session()

    @staticmethod
    def _sleep_before_retry(attempt: int) -> None:
        time.sleep(attempt * 2 + 1)


def browser_like_headers(user_agent: str | None = None) -> dict[str, str]:
    return {
        "User-Agent": user_agent
        or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


def _parse_impersonates(value: str | None) -> tuple[str, ...]:
    if value is None or value.strip().lower() in {"", "random"}:
        return DEFAULT_CURL_IMPERSONATES
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or DEFAULT_CURL_IMPERSONATES


def looks_blocked(html: str) -> bool:
    lowered = html[:20000].lower()
    return any(needle in lowered for needle in BLOCKED_NEEDLES)
