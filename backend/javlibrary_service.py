from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from javlibrary_crawler import JavLibraryCrawler, JavLibraryCrawlerConfig
from javlibrary_crawler.errors import JavLibraryError, JavLibraryValidationError
from javlibrary_crawler.normalizer import normalize_code

logger = logging.getLogger(__name__)


class JavLibraryServiceError(Exception):
    def __init__(self, user_message: str, error_code: str = "JAVLIBRARY_ERROR") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.error_code = error_code


@dataclass(frozen=True)
class JavLibraryServiceConfig:
    data_dir: Path
    base_url: str = "https://www.javlibrary.com"
    language: str = "cn"
    provider_order: tuple[str, ...] = ("javlibrary", "jav321", "javdb", "javbus")
    javdb_base_url: str = "https://javdb.com"
    javbus_base_url: str = "https://www.javbus.com"
    jav321_base_url: str = "https://www.jav321.com"
    timeout_seconds: int = 8
    total_timeout_seconds: int = 15
    cache_ttl_seconds: int = 604800
    failure_cache_ttl_seconds: int = 600
    not_found_cache_ttl_seconds: int = 86400
    blocked_cache_ttl_seconds: int = 120
    timeout_cache_ttl_seconds: int = 60
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


class JavLibraryService:
    def __init__(self, config: JavLibraryServiceConfig) -> None:
        self.config = config
        self.db_path = config.data_dir / "javlibrary.sqlite3"

    def initialize(self) -> None:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jav_videos (
                    code TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )

    def lookup_video(self, raw_code: str, *, force_refresh: bool = False) -> dict[str, Any]:
        try:
            code = normalize_code(raw_code)
        except JavLibraryValidationError as exc:
            raise JavLibraryServiceError(exc.user_message, exc.error_code) from exc

        if not force_refresh:
            cached = self._get_cached(code)
            if cached is not None:
                return cached

        crawler = self._create_crawler()
        try:
            video = crawler.lookup(code)
        except JavLibraryError as exc:
            self._store_error(code, exc.error_code, exc.user_message)
            raise JavLibraryServiceError(exc.user_message, exc.error_code) from exc
        except Exception as exc:
            logger.exception("Unexpected JAV metadata lookup error for %s.", code)
            message = "番号信息查询失败，请稍后再试"
            self._store_error(code, "JAVLIBRARY_ERROR", message)
            raise JavLibraryServiceError(message, "JAVLIBRARY_ERROR") from exc
        finally:
            crawler.close()

        payload = video.to_dict()
        self._store_success(code, payload)
        return payload

    def search_videos(self, query: str, *, page: int = 1, limit: int = 5) -> dict[str, Any]:
        query = " ".join(query.split()).strip()
        if not query:
            raise JavLibraryServiceError("搜索关键词不能为空", "JAV_SEARCH_QUERY_EMPTY")
        crawler = self._create_crawler()
        try:
            results = crawler.search_javdb(query, page=page, limit=limit)
        except JavLibraryError as exc:
            raise JavLibraryServiceError(exc.user_message, exc.error_code) from exc
        except Exception as exc:
            logger.exception("Unexpected JAV metadata search error for %s.", query)
            raise JavLibraryServiceError("番号搜索失败，请稍后再试", "JAV_SEARCH_FAILED") from exc
        finally:
            crawler.close()
        return {
            "query": query,
            "page": page,
            "total": len(results),
            "results": [item.to_dict() for item in results],
        }

    def get_javdb_ranking(self, period: str, *, page: int = 1, limit: int = 10) -> dict[str, Any]:
        labels = {"day": "日榜", "week": "周榜", "month": "月榜"}
        if period not in labels:
            raise JavLibraryServiceError("JavDB 排行榜类型无效", "JAV_RANKING_PERIOD_INVALID")
        crawler = self._create_crawler()
        try:
            results = crawler.javdb_ranking(period, page=page, limit=limit)
        except JavLibraryError as exc:
            raise JavLibraryServiceError(exc.user_message, exc.error_code) from exc
        except Exception as exc:
            logger.exception("Unexpected JavDB ranking error for %s.", period)
            raise JavLibraryServiceError("JavDB 排行榜获取失败，请稍后再试", "JAV_RANKING_FAILED") from exc
        finally:
            crawler.close()
        return {
            "period": period,
            "period_label": labels[period],
            "page": page,
            "total": len(results),
            "results": [item.to_dict() for item in results],
        }

    def _create_crawler(self) -> JavLibraryCrawler:
        return JavLibraryCrawler(
            JavLibraryCrawlerConfig(
                base_url=self.config.base_url,
                language=self.config.language,
                provider_order=self.config.provider_order,
                javdb_base_url=self.config.javdb_base_url,
                javbus_base_url=self.config.javbus_base_url,
                jav321_base_url=self.config.jav321_base_url,
                timeout_seconds=self.config.timeout_seconds,
                total_timeout_seconds=self.config.total_timeout_seconds,
                fetcher=self.config.fetcher,
                user_agent=self.config.user_agent,
                cookie=self.config.cookie,
                proxy=self.config.proxy,
                impersonate=self.config.impersonate,
                retry_times=self.config.retry_times,
                browser_profile_dir=self.config.browser_profile_dir,
                browser_channel=self.config.browser_channel,
                browser_headless=self.config.browser_headless,
                browser_wait_seconds=self.config.browser_wait_seconds,
            )
        )

    def _get_cached(self, code: str) -> dict[str, Any] | None:
        now = self._now()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status, payload_json, error_code, error_message, expires_at
                FROM jav_videos
                WHERE code = ?
                """,
                (code,),
            ).fetchone()
        if row is None:
            return None
        if self._parse_time(str(row["expires_at"])) <= now:
            return None

        if row["status"] == "ok":
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                return None
            if isinstance(payload, dict):
                payload["cache_hit"] = True
                return payload
            return None

        raise JavLibraryServiceError(
            str(row["error_message"] or "番号信息查询失败，请稍后再试"),
            str(row["error_code"] or "JAVLIBRARY_ERROR"),
        )

    def _store_success(self, code: str, payload: dict[str, Any]) -> None:
        now = self._now()
        expires_at = now + timedelta(seconds=max(0, self.config.cache_ttl_seconds))
        self._upsert(
            code=code,
            status="ok",
            payload_json=json.dumps(payload, ensure_ascii=False),
            error_code=None,
            error_message=None,
            fetched_at=now,
            expires_at=expires_at,
        )

    def _store_error(self, code: str, error_code: str, error_message: str) -> None:
        ttl = self._error_cache_ttl(error_code)
        if ttl <= 0:
            return
        now = self._now()
        self._upsert(
            code=code,
            status="error",
            payload_json=None,
            error_code=error_code,
            error_message=error_message,
            fetched_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )

    def _error_cache_ttl(self, error_code: str) -> int:
        if error_code == "JAV_NOT_FOUND":
            return max(0, self.config.not_found_cache_ttl_seconds)
        if error_code == "JAV_SOURCE_BLOCKED":
            return max(0, self.config.blocked_cache_ttl_seconds)
        if error_code == "JAV_FETCH_TIMEOUT":
            return max(0, self.config.timeout_cache_ttl_seconds)
        if error_code in {"JAV_FETCH_FAILED", "JAVLIBRARY_ERROR"}:
            return min(max(0, self.config.failure_cache_ttl_seconds), 60)
        return max(0, self.config.failure_cache_ttl_seconds)

    def _upsert(
        self,
        *,
        code: str,
        status: str,
        payload_json: str | None,
        error_code: str | None,
        error_message: str | None,
        fetched_at: datetime,
        expires_at: datetime,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jav_videos (
                    code, status, payload_json, error_code, error_message, fetched_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                """,
                (
                    code,
                    status,
                    payload_json,
                    error_code,
                    error_message,
                    fetched_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
