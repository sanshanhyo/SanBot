from __future__ import annotations

import json
from pathlib import Path

import yaml

from integrations.astrbot.astrbot_plugin_sanbot_router.meme_knowledge import (
    build_meme_context,
)
from integrations.astrbot.astrbot_plugin_sanbot_router.routing import is_sanbot_command


def test_sanbot_business_commands_are_reserved() -> None:
    commands = [
        "JM123456",
        "JMabc",
        "JM搜索 关键词",
        "JM日榜",
        "JAV SSIS-123",
        "AV搜索 中文标题",
        "演员搜索 三上悠亚",
        "DB月榜",
        "TG绑定 https://t.me/example",
        "TG最新 5",
        "帮助",
        "HELP",
        "状态",
        "取消 JM123456",
        "下载",
        "1",
    ]

    assert all(is_sanbot_command(command) for command in commands)


def test_normal_ai_chat_is_not_reserved_for_sanbot() -> None:
    assert not is_sanbot_command("今晚吃什么？")
    assert not is_sanbot_command("给我讲个笑话")
    assert not is_sanbot_command("SanBot 你在吗")


def test_meme_knowledge_matches_aliases_without_dumping_unrelated_entries() -> None:
    context = build_meme_context("听见你说，外战看滔博")

    assert "突然的陀螺" in context
    assert "反讽" in context
    assert "飞八分钱" not in context


def test_meme_knowledge_recognizes_f8fq_case_insensitively() -> None:
    context = build_meme_context("F8FQ 到底是什么意思")

    assert "张顺飞" in context
    assert "侮辱性" in context


def test_meme_knowledge_ignores_normal_chat() -> None:
    assert build_meme_context("今天晚上吃什么") == ""


def test_astrbot_compose_binds_management_ports_to_localhost() -> None:
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load(
        (root / "integrations" / "astrbot" / "compose.yml").read_text(encoding="utf-8")
    )
    ports = compose["services"]["astrbot"]["ports"]

    assert "127.0.0.1:6185:6185" in ports
    assert "127.0.0.1:6199:6199" in ports
    assert compose["services"]["astrbot"]["restart"] == "unless-stopped"


def test_production_plugin_example_uses_explicit_group_allowlist() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (root / "integrations" / "astrbot" / "plugin-config.example.json").read_text(
            encoding="utf-8"
        )
    )

    assert set(config["allowed_group_ids"]) == {"904942764", "961552805", "706845140"}


def test_astrbot_overlay_leaves_active_reply_to_group_chat_plus() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (root / "integrations" / "astrbot" / "config-overlay.example.json").read_text(
            encoding="utf-8"
        )
    )
    active_reply = config["provider_ltm_settings"]["active_reply"]

    assert config["platform_settings"]["enable_id_white_list"] is True
    assert config["platform_settings"]["ignore_bot_self_message"] is True
    assert config["provider_ltm_settings"]["group_icl_enable"] is True
    assert active_reply["enable"] is False
    assert active_reply["possibility_reply"] == 0.0
    assert set(active_reply["whitelist"]) == {"904942764", "961552805", "706845140"}


def test_group_chat_plus_example_is_allowlisted_and_humanized() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (
            root / "integrations" / "astrbot" / "group-chat-plus-config.example.json"
        ).read_text(encoding="utf-8")
    )

    assert set(config["enabled_groups"]) == {"904942764", "961552805", "706845140"}
    assert config["decision_ai_persona_name"] == "小散"
    assert config["initial_probability"] > 0.1
    assert config["keyword_smart_mode"] is True
    assert config["enable_humanize_mode"] is True
    assert config["enable_reply_density_limit"] is True
    assert config["enable_proactive_chat"] is False
