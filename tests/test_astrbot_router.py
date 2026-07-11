from __future__ import annotations

import json
from pathlib import Path

import yaml

from integrations.astrbot.astrbot_plugin_sanbot_router.routing import (
    ActiveReplyLimiter,
    is_sanbot_command,
)


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


def test_active_reply_limiter_enforces_cooldown_and_daily_limit() -> None:
    limiter = ActiveReplyLimiter()

    assert limiter.allow(
        "10001", now=1000, day="2026-07-11", cooldown_seconds=600, daily_limit=2
    )
    assert not limiter.allow(
        "10001", now=1200, day="2026-07-11", cooldown_seconds=600, daily_limit=2
    )
    assert limiter.allow(
        "10001", now=1600, day="2026-07-11", cooldown_seconds=600, daily_limit=2
    )
    assert not limiter.allow(
        "10001", now=2200, day="2026-07-11", cooldown_seconds=600, daily_limit=2
    )
    assert limiter.allow(
        "10001", now=2200, day="2026-07-12", cooldown_seconds=600, daily_limit=2
    )


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
    assert config["active_reply_cooldown_seconds"] == 600
    assert config["active_reply_daily_limit"] == 30


def test_astrbot_overlay_enables_builtin_active_reply_for_allowlisted_groups() -> None:
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
    assert active_reply["enable"] is True
    assert active_reply["possibility_reply"] == 0.03
    assert set(active_reply["whitelist"]) == {"904942764", "961552805", "706845140"}
