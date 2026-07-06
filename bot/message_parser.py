from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

ALBUM_PATTERN = re.compile(r"(?i)\bJM\s*(\d{1,12})\b")
SEARCH_PATTERN = re.compile(r"^\s*(?:搜索|搜|查找)\s*(.*)$", re.S)
JAV_PATTERN = re.compile(
    r"(?i)^\s*(?:JAV|番号|AV)\s+([A-Z]{2,12}[-_\s]?\d{2,8}[A-Z]?|FC2(?:[-_\s]?PPV)?[-_\s]?\d{3,10})\s*$"
)
RANKING_ALIASES = {
    "日榜": "day",
    "今日榜": "day",
    "今日排行榜": "day",
    "今天榜": "day",
    "今天排行榜": "day",
    "周榜": "week",
    "本周榜": "week",
    "本周排行榜": "week",
    "周排行榜": "week",
    "月榜": "month",
    "本月榜": "month",
    "本月排行榜": "month",
    "月排行榜": "month",
}
CQ_CODE_PATTERN = re.compile(r"\[CQ:([a-zA-Z0-9_]+)((?:,[^\]]*)?)\]")


class ParseAction(StrEnum):
    IGNORE = "ignore"
    HOME = "home"
    HELP = "help"
    FEATURES = "features"
    HISTORY = "history"
    GROUP_HISTORY = "group_history"
    USAGE = "usage"
    OK = "ok"
    SEARCH = "search"
    RANKING = "ranking"
    JAV = "jav"
    ERROR = "error"


@dataclass(frozen=True)
class ParseResult:
    action: ParseAction
    album_id: str | None = None
    search_query: str | None = None
    ranking_period: str | None = None
    jav_code: str | None = None
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
        return None, "multiple_album_numbers"
    return matches[0], None


def extract_search_query(message_segments: Any) -> tuple[str | None, str | None]:
    text = text_from_segments(message_segments)
    match = SEARCH_PATTERN.match(text)
    if match is None:
        return None, None
    query = re.sub(r"\s+", " ", match.group(1)).strip()
    if not query:
        return None, "search_usage"
    if len(query) > 40:
        return None, "search_query_too_long"
    return query, None


def extract_ranking_period(message_segments: Any) -> str | None:
    text = re.sub(r"\s+", "", text_from_segments(message_segments)).strip()
    lowered = text.lower()
    if lowered in {"dayranking", "dailyranking"}:
        return "day"
    if lowered in {"weekranking", "weeklyranking"}:
        return "week"
    if lowered in {"monthranking", "monthlyranking"}:
        return "month"
    return RANKING_ALIASES.get(text)


def extract_jav_code(message_segments: Any) -> str | None:
    text = text_from_segments(message_segments)
    match = JAV_PATTERN.match(text)
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group(1)).strip()


def extract_control_action(message_segments: Any) -> ParseAction | None:
    text = re.sub(r"\s+", " ", text_from_segments(message_segments)).strip()
    if not text:
        return ParseAction.HOME

    lowered = text.lower()
    if lowered in {"帮助", "help", "使用说明", "说明"}:
        return ParseAction.HELP
    if lowered in {"功能", "功能列表", "模块", "modules", "features"}:
        return ParseAction.FEATURES
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

    search_query, search_error = extract_search_query(message_segments)
    if search_error:
        return ParseResult(ParseAction.ERROR, error_key=search_error)
    if search_query is not None:
        return ParseResult(ParseAction.SEARCH, search_query=search_query)

    ranking_period = extract_ranking_period(message_segments)
    if ranking_period is not None:
        return ParseResult(ParseAction.RANKING, ranking_period=ranking_period)

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
        return ParseResult(ParseAction.USAGE)
    return ParseResult(ParseAction.OK, album_id=album_id)
