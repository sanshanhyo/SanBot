from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .lang_keys import LangKey

ALBUM_PATTERN = re.compile(r"(?i)\bJM\s*(\d{1,12})\b")
JM_SEARCH_PATTERN = re.compile(r"(?i)^\s*JM\s*(?:搜索|搜|查找)\s*(.*)$", re.S)
AV_SEARCH_PATTERN = re.compile(r"(?i)^\s*(?:AV|DB)\s*(?:搜索|搜|查找)\s*(.*)$", re.S)
ACTOR_SEARCH_PATTERN = re.compile(
    r"(?i)^\s*(?:演员|女优|女優|AV演员|AV女优|AV女優|DB演员|DB女优|DB女優)\s*(?:搜索|搜|查找)?\s*(.*)$",
    re.S,
)
JAV_PATTERN = re.compile(
    r"(?i)^\s*(?:JAV|番号|AV)\s+([A-Z]{2,12}[-_\s]?\d{2,8}[A-Z]?|FC2(?:[-_\s]?PPV)?[-_\s]?\d{3,10})\s*$"
)
JM_RANKING_ALIASES = {
    "JM日榜": "day",
    "JM周榜": "week",
    "JM月榜": "month",
}
DB_RANKING_ALIASES = {
    "DB日榜": "day",
    "DB周榜": "week",
    "DB月榜": "month",
}
TG_BIND_PATTERN = re.compile(r"(?i)^\s*TG\s*(?:绑定|bind)\s+(\S+)\s*$")
TG_LATEST_PATTERN = re.compile(r"(?i)^\s*TG\s*(?:最新|拉取|同步)(?:\s+(\d{1,2}))?\s*$")
CQ_CODE_PATTERN = re.compile(r"\[CQ:([a-zA-Z0-9_]+)((?:,[^\]]*)?)\]")


class ParseAction(StrEnum):
    IGNORE = "ignore"
    HOME = "home"
    HELP = "help"
    FEATURES = "features"
    LLM_RESET = "llm_reset"
    HISTORY = "history"
    GROUP_HISTORY = "group_history"
    USAGE = "usage"
    OK = "ok"
    SEARCH = "search"
    RANKING = "ranking"
    AV_SEARCH = "av_search"
    ACTOR_SEARCH = "actor_search"
    DB_RANKING = "db_ranking"
    JAV = "jav"
    TG_BIND = "tg_bind"
    TG_LIST = "tg_list"
    TG_LATEST = "tg_latest"
    UNKNOWN = "unknown_command"
    ERROR = "error"


@dataclass(frozen=True)
class ParseResult:
    action: ParseAction
    album_id: str | None = None
    search_query: str | None = None
    ranking_period: str | None = None
    db_ranking_period: str | None = None
    jav_code: str | None = None
    tg_channel_ref: str | None = None
    tg_limit: int | None = None
    error_key: str | None = None


def _decode_cq_value(value: str) -> str:
    return (
        value.replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def _parse_cq_data(raw_data: str) -> dict[str, str]:
    data: dict[str, str] = {}
    if not raw_data:
        return data
    for item in raw_data.lstrip(",").split(","):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        data[key] = _decode_cq_value(value)
    return data


def normalize_message_segments(message_segments: Any) -> list[dict[str, Any]]:
    if isinstance(message_segments, list):
        return [segment for segment in message_segments if isinstance(segment, dict)]
    if not isinstance(message_segments, str):
        return []

    segments: list[dict[str, Any]] = []
    cursor = 0
    for match in CQ_CODE_PATTERN.finditer(message_segments):
        if match.start() > cursor:
            text = _decode_cq_value(message_segments[cursor : match.start()])
            if text:
                segments.append({"type": "text", "data": {"text": text}})
        segment_type = match.group(1)
        segments.append({"type": segment_type, "data": _parse_cq_data(match.group(2))})
        cursor = match.end()

    if cursor < len(message_segments):
        text = _decode_cq_value(message_segments[cursor:])
        if text:
            segments.append({"type": "text", "data": {"text": text}})
    return segments


def has_at_bot(message_segments: Any, bot_qq_id: str) -> bool:
    for segment in normalize_message_segments(message_segments):
        if not isinstance(segment, dict):
            continue
        if segment.get("type") != "at":
            continue
        data = segment.get("data") or {}
        if str(data.get("qq")) == str(bot_qq_id):
            return True
    return False


def text_from_segments(message_segments: Any) -> str:
    parts: list[str] = []
    for segment in normalize_message_segments(message_segments):
        if segment.get("type") != "text":
            continue
        data = segment.get("data") or {}
        text = data.get("text")
        if isinstance(text, str):
            parts.append(text)
    return " ".join(parts)


def extract_album_id(message_segments: Any) -> tuple[str | None, str | None]:
    text = text_from_segments(message_segments)
    matches = [match.group(1) for match in ALBUM_PATTERN.finditer(text)]
    if not matches:
        return None, None
    if len(matches) > 1:
        return None, LangKey.MULTIPLE_ALBUM_NUMBERS
    return matches[0], None


def extract_jm_search_query(message_segments: Any) -> tuple[str | None, str | None]:
    text = text_from_segments(message_segments)
    match = JM_SEARCH_PATTERN.match(text)
    if match is None:
        return None, None
    query = re.sub(r"\s+", " ", match.group(1)).strip()
    if not query:
        return None, LangKey.JM_SEARCH_USAGE
    if len(query) > 40:
        return None, LangKey.SEARCH_QUERY_TOO_LONG
    return query, None


def extract_av_search_query(message_segments: Any) -> tuple[str | None, str | None]:
    text = text_from_segments(message_segments)
    match = AV_SEARCH_PATTERN.match(text)
    if match is None:
        return None, None
    query = re.sub(r"\s+", " ", match.group(1)).strip()
    if not query:
        return None, LangKey.AV_SEARCH_USAGE
    if len(query) > 60:
        return None, LangKey.AV_SEARCH_QUERY_TOO_LONG
    return query, None


def extract_actor_search_query(message_segments: Any) -> tuple[str | None, str | None]:
    text = text_from_segments(message_segments)
    match = ACTOR_SEARCH_PATTERN.match(text)
    if match is None:
        return None, None
    query = re.sub(r"\s+", " ", match.group(1)).strip()
    if not query:
        return None, LangKey.ACTOR_SEARCH_USAGE
    if len(query) > 60:
        return None, LangKey.ACTOR_SEARCH_QUERY_TOO_LONG
    return query, None


def extract_jm_ranking_period(message_segments: Any) -> str | None:
    text = re.sub(r"\s+", "", text_from_segments(message_segments)).strip()
    return JM_RANKING_ALIASES.get(text.upper())


def extract_db_ranking_period(message_segments: Any) -> str | None:
    text = re.sub(r"\s+", "", text_from_segments(message_segments)).strip()
    return DB_RANKING_ALIASES.get(text.upper())


def extract_jav_code(message_segments: Any) -> str | None:
    text = text_from_segments(message_segments)
    match = JAV_PATTERN.match(text)
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group(1)).strip().upper()


def extract_tg_bind_ref(message_segments: Any) -> tuple[str | None, str | None]:
    text = text_from_segments(message_segments)
    match = TG_BIND_PATTERN.match(text)
    if match is None:
        if re.match(r"(?i)^\s*TG\s*(?:绑定|bind)\s*$", text):
            return None, LangKey.TG_BIND_USAGE
        return None, None
    return match.group(1).strip(), None


def extract_tg_latest_limit(message_segments: Any) -> tuple[int | None, str | None]:
    text = text_from_segments(message_segments)
    match = TG_LATEST_PATTERN.match(text)
    if match is None:
        return None, None
    raw_limit = match.group(1)
    if raw_limit is None:
        return 5, None
    limit = int(raw_limit)
    if limit < 1 or limit > 10:
        return None, LangKey.TG_LATEST_USAGE
    return limit, None


def is_tg_list_command(message_segments: Any) -> bool:
    text = re.sub(r"\s+", "", text_from_segments(message_segments)).strip()
    return text.upper() in {"TG列表", "TG频道", "TG订阅", "TGCHANNELS"}


def extract_control_action(message_segments: Any) -> ParseAction | None:
    text = re.sub(r"\s+", " ", text_from_segments(message_segments)).strip()
    if not text:
        return ParseAction.HOME

    lowered = text.lower()
    if lowered in {"帮助", "help", "使用说明", "说明"}:
        return ParseAction.HELP
    if lowered in {"功能", "功能列表", "模块", "modules", "features"}:
        return ParseAction.FEATURES
    if lowered in {"重置对话", "重置聊天", "ai重置", "llm重置", "reset chat"}:
        return ParseAction.LLM_RESET
    if lowered in {"历史", "我的任务", "任务历史", "我的历史", "history"}:
        return ParseAction.HISTORY
    if lowered in {"最近任务", "群任务", "群历史", "最近历史", "group history"}:
        return ParseAction.GROUP_HISTORY
    return None


def parse_group_message(event: dict[str, Any], bot_qq_id: str) -> ParseResult:
    if event.get("message_type") != "group":
        return ParseResult(ParseAction.IGNORE)

    if str(event.get("user_id")) == str(bot_qq_id):
        return ParseResult(ParseAction.IGNORE)

    message_segments = event.get("message")
    if not has_at_bot(message_segments, bot_qq_id):
        return ParseResult(ParseAction.IGNORE)

    search_query, search_error = extract_jm_search_query(message_segments)
    if search_error:
        return ParseResult(ParseAction.ERROR, error_key=search_error)
    if search_query is not None:
        return ParseResult(ParseAction.SEARCH, search_query=search_query)

    actor_search_query, actor_search_error = extract_actor_search_query(message_segments)
    if actor_search_error:
        return ParseResult(ParseAction.ERROR, error_key=actor_search_error)
    if actor_search_query is not None:
        return ParseResult(ParseAction.ACTOR_SEARCH, search_query=actor_search_query)

    av_search_query, av_search_error = extract_av_search_query(message_segments)
    if av_search_error:
        return ParseResult(ParseAction.ERROR, error_key=av_search_error)
    if av_search_query is not None:
        return ParseResult(ParseAction.AV_SEARCH, search_query=av_search_query)

    tg_ref, tg_bind_error = extract_tg_bind_ref(message_segments)
    if tg_bind_error:
        return ParseResult(ParseAction.ERROR, error_key=tg_bind_error)
    if tg_ref is not None:
        return ParseResult(ParseAction.TG_BIND, tg_channel_ref=tg_ref)

    if is_tg_list_command(message_segments):
        return ParseResult(ParseAction.TG_LIST)

    tg_limit, tg_latest_error = extract_tg_latest_limit(message_segments)
    if tg_latest_error:
        return ParseResult(ParseAction.ERROR, error_key=tg_latest_error)
    if tg_limit is not None:
        return ParseResult(ParseAction.TG_LATEST, tg_limit=tg_limit)

    ranking_period = extract_jm_ranking_period(message_segments)
    if ranking_period is not None:
        return ParseResult(ParseAction.RANKING, ranking_period=ranking_period)

    db_ranking_period = extract_db_ranking_period(message_segments)
    if db_ranking_period is not None:
        return ParseResult(ParseAction.DB_RANKING, db_ranking_period=db_ranking_period)

    jav_code = extract_jav_code(message_segments)
    if jav_code is not None:
        return ParseResult(ParseAction.JAV, jav_code=jav_code)

    control_action = extract_control_action(message_segments)
    if control_action is not None:
        return ParseResult(control_action)

    album_id, error = extract_album_id(message_segments)
    if error:
        return ParseResult(ParseAction.ERROR, error_key=error)
    if album_id is None:
        return ParseResult(ParseAction.UNKNOWN)
    return ParseResult(ParseAction.OK, album_id=album_id)
