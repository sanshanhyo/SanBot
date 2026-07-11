from __future__ import annotations

from bot.message_parser import ParseAction, parse_group_message


def _event(message: list[dict] | str, user_id: str = "20001") -> dict:
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


def test_parse_cq_string_message_with_at() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] JM123456"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.OK
    assert result.album_id == "123456"


def test_ignore_cq_string_when_at_other_user() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=99999] JM123456"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.IGNORE


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

    assert result.action == ParseAction.UNKNOWN


def test_empty_at_returns_home() -> None:
    result = parse_group_message(
        _event([{"type": "at", "data": {"qq": "12345"}}]),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.HOME


def test_help_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 帮助"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.HELP


def test_features_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 功能"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.FEATURES


def test_llm_reset_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 重置对话"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.LLM_RESET


def test_history_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 我的任务"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.HISTORY


def test_group_history_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 最近任务"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.GROUP_HISTORY


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

    assert result.action == ParseAction.UNKNOWN


def test_parse_search_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM搜索 戦乙女"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.SEARCH
    assert result.search_query == "戦乙女"


def test_search_command_does_not_become_jm_download() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM搜索 JM123456"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.SEARCH
    assert result.search_query == "JM123456"


def test_search_without_query_returns_usage_error() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM搜索 "}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.ERROR
    assert result.error_key == "jm_search_usage"


def test_parse_day_ranking_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM日榜"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.RANKING
    assert result.ranking_period == "day"


def test_parse_week_ranking_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM周榜"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.RANKING
    assert result.ranking_period == "week"


def test_parse_month_ranking_command_with_at() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] JM月榜"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.RANKING
    assert result.ranking_period == "month"


def test_plain_ranking_command_is_no_longer_jm_ranking() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] 月榜"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.UNKNOWN


def test_parse_av_search_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " AV搜索 中文标题"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.AV_SEARCH
    assert result.search_query == "中文标题"


def test_parse_actor_search_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 演员搜索 三上悠亚"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.ACTOR_SEARCH
    assert result.search_query == "三上悠亚"


def test_actor_search_without_query_returns_usage_error() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 演员搜索 "}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.ERROR
    assert result.error_key == "actor_search_usage"


def test_unknown_command_with_at() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] 来点奇怪的"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.UNKNOWN


def test_parse_db_day_ranking_command_with_at() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] DB日榜"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.DB_RANKING
    assert result.db_ranking_period == "day"


def test_parse_jav_command_with_at() -> None:
    result = parse_group_message(
        _event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.JAV
    assert result.jav_code == "SSIS123"


def test_parse_tg_bind_command_with_at() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] TG绑定 https://t.me/example_channel"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.TG_BIND
    assert result.tg_channel_ref == "https://t.me/example_channel"


def test_parse_tg_list_command_with_at() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] TG列表"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.TG_LIST


def test_parse_tg_latest_command_with_at() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] TG最新 5"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.TG_LATEST
    assert result.tg_limit == 5


def test_parse_fc2_jav_command_with_at() -> None:
    result = parse_group_message(
        _event("[CQ:at,qq=12345] 番号 FC2 PPV 1234567"),
        bot_qq_id="12345",
    )

    assert result.action == ParseAction.JAV
    assert result.jav_code == "FC2PPV1234567"
