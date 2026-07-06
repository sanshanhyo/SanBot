from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from javlibrary_crawler import JavLibraryCrawler, JavLibraryCrawlerConfig
from javlibrary_crawler.errors import JavLibraryError, JavLibraryValidationError
from javlibrary_crawler.models import JavLibrarySearchItem
from javlibrary_crawler.normalizer import normalize_code

logger = logging.getLogger(__name__)
JAV_VIDEO_CACHE_SCHEMA_VERSION = 2

COMMON_ACTOR_ALIASES: dict[str, tuple[str, ...]] = {
    "三上悠亚": ("三上悠亜", "三上悠亞", "Mikami Yua", "Yua Mikami"),
    "桥本有菜": ("橋本ありな", "新ありな", "新有菜", "Hashimoto Arina", "Arina Hashimoto"),
    "河北彩花": ("河北彩伽", "河北彩花", "Kawakita Saika", "Saika Kawakita"),
    "深田咏美": ("深田えいみ", "天海こころ", "Fukada Eimi", "Eimi Fukada"),
    "桃乃木香奈": ("桃乃木かな", "Momogi Kana", "Kana Momogi"),
    "相泽南": ("相沢みなみ", "Aizawa Minami", "Minami Aizawa"),
    "枫花恋": ("楓カレン", "楓ふうあ", "Kaede Karen", "Karen Kaede"),
    "山岸逢花": ("山岸あや花", "Yamagishi Aika", "Aika Yamagishi"),
    "七泽米亚": ("七沢みあ", "Nanasawa Mia", "Mia Nanasawa"),
    "纱仓真菜": ("紗倉まな", "Sakura Mana", "Mana Sakura"),
    "樱空桃": ("桜空もも", "Sakura Momo", "Momo Sakura"),
    "小仓由菜": ("小倉由菜", "Ogura Yuna", "Yuna Ogura"),
    "本庄铃": ("本庄鈴", "Honjo Suzu", "Suzu Honjo"),
    "凉森玲梦": ("涼森れむ", "Suzumori Remu", "Remu Suzumori"),
    "吉高宁宁": ("吉高寧々", "Yoshitaka Nene", "Nene Yoshitaka"),
    "希岛爱理": ("希島あいり", "Kijima Airi", "Airi Kijima"),
    "天使萌": ("天使もえ", "Amatsuka Moe", "Moe Amatsuka"),
    "葵司": ("葵つかさ", "Aoi Tsukasa", "Tsukasa Aoi"),
    "八挂海": ("八掛うみ", "Yatsugake Umi", "Umi Yatsugake"),
}

SCRIPT_VARIANT_TABLE = str.maketrans(
    {
        "亚": "亜",
        "樱": "桜",
        "桥": "橋",
        "泽": "沢",
        "纱": "紗",
        "仓": "倉",
        "凉": "涼",
        "宁": "寧",
        "爱": "愛",
        "岛": "島",
        "绪": "緒",
        "穗": "穂",
        "铃": "鈴",
        "龙": "龍",
        "叶": "葉",
        "宫": "宮",
        "坂": "坂",
        "滨": "浜",
        "遥": "遙",
    }
)

WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"


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
    provider_order: tuple[str, ...] = ("javdb", "javlibrary", "jav321", "javbus")
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
    actor_alias_path: Path | None = None
    actor_alias_online: bool = True
    actor_alias_timeout_seconds: float = 4.0
    actor_alias_candidate_limit: int = 6


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jav_actor_aliases (
                    query_key TEXT NOT NULL,
                    query TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence INTEGER NOT NULL DEFAULT 50,
                    hit_count INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (query_key, alias)
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
            if cached is not None and self._is_current_video_cache(cached):
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
        payload["cache_schema_version"] = JAV_VIDEO_CACHE_SCHEMA_VERSION
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

    def search_actors(self, query: str, *, page: int = 1, limit: int = 5) -> dict[str, Any]:
        query = " ".join(query.split()).strip()
        if not query:
            raise JavLibraryServiceError("演员名称不能为空", "JAV_ACTOR_SEARCH_QUERY_EMPTY")
        candidates = self._actor_search_candidates(query)
        crawler = self._create_crawler()
        try:
            results = self._search_actor_candidates(crawler, query, candidates, page=page, limit=limit)
        except JavLibraryError as exc:
            raise JavLibraryServiceError(exc.user_message, exc.error_code) from exc
        except Exception as exc:
            logger.exception("Unexpected JAV actor search error for %s.", query)
            raise JavLibraryServiceError("演员搜索失败，请稍后再试", "JAV_ACTOR_SEARCH_FAILED") from exc
        finally:
            crawler.close()
        self._learn_actor_aliases_from_results(query, results)
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

    def _actor_search_candidates(self, query: str) -> list[str]:
        values: list[str] = [query]
        script_variants = _script_variants(query)
        file_aliases = self._file_actor_aliases(query)
        common_aliases = _common_actor_aliases(query)
        cached_aliases = self._cached_actor_aliases(query)
        values.extend(script_variants)
        values.extend(file_aliases)
        values.extend(common_aliases)
        values.extend(cached_aliases)
        if self.config.actor_alias_online and not (file_aliases or common_aliases or cached_aliases):
            values.extend(self._resolve_actor_aliases_online(query))
        return _unique_texts(values)[: max(1, self.config.actor_alias_candidate_limit)]

    def _search_actor_candidates(
        self,
        crawler: JavLibraryCrawler,
        query: str,
        candidates: list[str],
        *,
        page: int,
        limit: int,
    ) -> list[JavLibrarySearchItem]:
        merged: dict[str, JavLibrarySearchItem] = {}
        scores: dict[str, int] = {}
        first_error: JavLibraryError | None = None
        per_candidate_limit = min(max(limit * 2, limit), 10)
        for index, candidate in enumerate(candidates):
            try:
                items = crawler.search_javdb_actor(candidate, page=page, limit=per_candidate_limit)
            except JavLibraryError as exc:
                first_error = first_error or exc
                logger.info("Actor candidate search failed for %s via %s: %s", query, candidate, exc.user_message)
                continue
            for item in items:
                key = item.code.upper()
                score = _actor_search_result_score(item, query, candidates, candidate_index=index)
                if score > scores.get(key, -1):
                    merged[key] = item
                    scores[key] = score
                else:
                    scores[key] = max(scores.get(key, 0), score)
        if not merged and first_error is not None:
            raise first_error
        return [
            item
            for _score, item in sorted(
                ((scores.get(key, 0), item) for key, item in merged.items()),
                key=lambda entry: (-entry[0], entry[1].code),
            )
        ][:limit]

    def _cached_actor_aliases(self, query: str) -> list[str]:
        query_key = _actor_query_key(query)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT alias
                FROM jav_actor_aliases
                WHERE query_key = ?
                ORDER BY confidence DESC, hit_count DESC, updated_at DESC
                LIMIT 12
                """,
                (query_key,),
            ).fetchall()
        return [str(row["alias"]) for row in rows if str(row["alias"]).strip()]

    def _file_actor_aliases(self, query: str) -> list[str]:
        path = self.config.actor_alias_path
        if path is None or not path.is_file():
            return []
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            logger.warning("Could not read actor alias file %s: %s", path, exc)
            return []
        except yaml.YAMLError as exc:
            logger.warning("Could not parse actor alias file %s: %s", path, exc)
            return []
        aliases = payload.get("aliases") if isinstance(payload, dict) else None
        if not isinstance(aliases, dict):
            return []
        values = aliases.get(query) or aliases.get(_actor_query_key(query))
        if isinstance(values, str):
            return [values]
        if isinstance(values, list):
            return [str(value) for value in values if str(value).strip()]
        return []

    def _resolve_actor_aliases_online(self, query: str) -> list[str]:
        timeout = max(0.5, self.config.actor_alias_timeout_seconds)
        try:
            with httpx.Client(timeout=timeout, headers={"User-Agent": "SanBot/0.1"}) as client:
                search_response = client.get(
                    WIKIDATA_API_URL,
                    params={
                        "action": "wbsearchentities",
                        "format": "json",
                        "language": "zh",
                        "search": query,
                        "limit": 3,
                    },
                )
                search_response.raise_for_status()
                search_payload = search_response.json()
                ids = [
                    str(item.get("id"))
                    for item in search_payload.get("search", [])
                    if isinstance(item, dict) and item.get("id")
                ][:3]
                if not ids:
                    return []
                entity_response = client.get(
                    WIKIDATA_API_URL,
                    params={
                        "action": "wbgetentities",
                        "format": "json",
                        "ids": "|".join(ids),
                        "props": "labels|aliases|descriptions",
                        "languages": "zh|zh-hans|zh-hant|ja|en",
                    },
                )
                entity_response.raise_for_status()
                entity_payload = entity_response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Online actor alias lookup failed for %s: %s", query, exc)
            return []

        values: list[str] = []
        entities = entity_payload.get("entities", {})
        if not isinstance(entities, dict):
            return []
        for entity in entities.values():
            if not isinstance(entity, dict) or not _looks_like_av_actor_entity(entity):
                continue
            values.extend(_wikidata_entity_names(entity))
        values = [value for value in values if value != query]
        self._store_actor_aliases(query, values, source="wikidata", confidence=70)
        return values

    def _learn_actor_aliases_from_results(self, query: str, results: list[JavLibrarySearchItem]) -> None:
        values: list[str] = []
        for item in results[:8]:
            values.extend(item.actors)
        values = [value for value in values if _actor_query_key(value) != _actor_query_key(query)]
        self._store_actor_aliases(query, values, source="search_result", confidence=60)

    def _store_actor_aliases(self, query: str, aliases: list[str], *, source: str, confidence: int) -> None:
        query_key = _actor_query_key(query)
        cleaned_aliases = [alias for alias in _unique_texts(aliases) if _actor_query_key(alias) != query_key]
        if not cleaned_aliases:
            return
        now = self._now().isoformat()
        with self._connect() as conn:
            for alias in cleaned_aliases[:20]:
                conn.execute(
                    """
                    INSERT INTO jav_actor_aliases (
                        query_key, query, alias, source, confidence, hit_count, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(query_key, alias) DO UPDATE SET
                        query = excluded.query,
                        source = excluded.source,
                        confidence = MAX(jav_actor_aliases.confidence, excluded.confidence),
                        hit_count = jav_actor_aliases.hit_count + 1,
                        updated_at = excluded.updated_at
                    """,
                    (query_key, query, alias, source, confidence, now),
                )

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

    @staticmethod
    def _is_current_video_cache(payload: dict[str, Any]) -> bool:
        try:
            version = int(payload.get("cache_schema_version") or 0)
        except (TypeError, ValueError):
            return False
        return version >= JAV_VIDEO_CACHE_SCHEMA_VERSION

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


def _actor_query_key(value: str) -> str:
    return "".join(value.split()).casefold()


def _unique_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = " ".join(str(value).split()).strip()
        if not text:
            continue
        key = _actor_query_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _common_actor_aliases(query: str) -> list[str]:
    return list(COMMON_ACTOR_ALIASES.get(_actor_query_key(query), ()))


def _script_variants(query: str) -> list[str]:
    translated = query.translate(SCRIPT_VARIANT_TABLE)
    variants = [translated]
    if "亞" in query:
        variants.append(query.replace("亞", "亜"))
    if "亞" in translated:
        variants.append(translated.replace("亞", "亜"))
    return [value for value in _unique_texts(variants) if _actor_query_key(value) != _actor_query_key(query)]


def _actor_search_result_score(
    item: JavLibrarySearchItem,
    query: str,
    candidates: list[str],
    *,
    candidate_index: int,
) -> int:
    score = max(0, 1000 - candidate_index * 60)
    actor_keys = [_actor_query_key(actor) for actor in item.actors]
    query_key = _actor_query_key(query)
    if query_key in actor_keys:
        score += 420
    for index, candidate in enumerate(candidates):
        candidate_key = _actor_query_key(candidate)
        if candidate_key in actor_keys:
            score += max(60, 360 - index * 25)
        elif any(candidate_key and candidate_key in actor_key for actor_key in actor_keys):
            score += max(30, 180 - index * 15)
    title_key = _actor_query_key(item.title)
    if query_key and query_key in title_key:
        score += 30
    return score


def _looks_like_av_actor_entity(entity: dict[str, Any]) -> bool:
    descriptions = entity.get("descriptions")
    if not isinstance(descriptions, dict):
        return False
    text = " ".join(
        str(value.get("value", ""))
        for value in descriptions.values()
        if isinstance(value, dict)
    ).casefold()
    needles = (
        "av",
        "女優",
        "女优",
        "成人",
        "porn",
        "adult",
        "gravure",
        "グラビア",
    )
    return any(needle.casefold() in text for needle in needles)


def _wikidata_entity_names(entity: dict[str, Any]) -> list[str]:
    values: list[str] = []
    labels = entity.get("labels")
    if isinstance(labels, dict):
        for language in ("ja", "zh", "zh-hans", "zh-hant", "en"):
            label = labels.get(language)
            if isinstance(label, dict) and isinstance(label.get("value"), str):
                values.append(label["value"])
    aliases = entity.get("aliases")
    if isinstance(aliases, dict):
        for language in ("ja", "zh", "zh-hans", "zh-hant", "en"):
            for alias in aliases.get(language, []):
                if isinstance(alias, dict) and isinstance(alias.get("value"), str):
                    values.append(alias["value"])
    return _unique_texts(values)
