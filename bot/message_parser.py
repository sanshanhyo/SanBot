from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

ALBUM_PATTERN = re.compile(r"(?i)(?:JM\s*)?(\d{1,12})")


class ParseAction(StrEnum):
    IGNORE = "ignore"
    USAGE = "usage"
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True)
class ParseResult:
    action: ParseAction
    album_id: str | None = None
    error_message: str | None = None


def has_at_bot(message_segments: Any, bot_qq_id: str) -> bool:
    if not isinstance(message_segments, list):
        return False
    for segment in message_segments:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") != "at":
            continue
        data = segment.get("data") or {}
        if str(data.get("qq")) == str(bot_qq_id):
            return True
    return False


def _text_from_segments(message_segments: Any) -> str:
    if not isinstance(message_segments, list):
        return ""

    parts: list[str] = []
    for segment in message_segments:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") != "text":
            continue
        data = segment.get("data") or {}
        text = data.get("text")
        if isinstance(text, str):
            parts.append(text)
    return " ".join(parts)


def extract_album_id(message_segments: Any) -> tuple[str | None, str | None]:
    text = _text_from_segments(message_segments)
    matches = [match.group(1) for match in ALBUM_PATTERN.finditer(text)]
    if not matches:
        return None, None
    if len(matches) > 1:
        return None, "一条消息只能包含一个 JM 编号"
    return matches[0], None


def parse_group_message(event: dict[str, Any], bot_qq_id: str) -> ParseResult:
    if event.get("message_type") != "group":
        return ParseResult(ParseAction.IGNORE)

    if str(event.get("user_id")) == str(bot_qq_id):
        return ParseResult(ParseAction.IGNORE)

    message_segments = event.get("message")
    if not has_at_bot(message_segments, bot_qq_id):
        return ParseResult(ParseAction.IGNORE)

    album_id, error = extract_album_id(message_segments)
    if error:
        return ParseResult(ParseAction.ERROR, error_message=error)
    if album_id is None:
        return ParseResult(ParseAction.USAGE)
    return ParseResult(ParseAction.OK, album_id=album_id)

