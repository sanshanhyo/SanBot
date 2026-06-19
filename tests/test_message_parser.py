from __future__ import annotations

from bot.message_parser import ParseAction, parse_group_message


def _event(message: list[dict], user_id: str = "20001") -> dict:
    return {
        "message_type": "group",
        "group_id": "10001",
        "user_id": user_id,
        "message": message,
    }


def test_parse_jm_number_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM123456"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.OK
    assert result.album_id == "123456"


def test_ignore_when_not_at_bot() -> None:
    result = parse_group_message(
        _event([{"type": "text", "data": {"text": "JM123456"}}]),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.IGNORE


def test_usage_when_no_number() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " hello"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.USAGE


def test_plain_number_requires_jm_prefix() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 123456"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.USAGE
