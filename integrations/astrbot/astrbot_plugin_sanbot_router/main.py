from __future__ import annotations

import time
from datetime import date
from sys import maxsize

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .routing import ActiveReplyLimiter, is_sanbot_command, normalize_text


class SanBotRouter(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.active_reply_limiter = ActiveReplyLimiter()

    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE, priority=maxsize - 100
    )
    async def route_group_message(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id() or "")
        if not group_id or group_id not in self._allowed_groups():
            event.stop_event()
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            event.stop_event()
            return

        text = normalize_text(event.message_str)
        original_text = self._original_plain_text(event)

        # Slash-prefixed commands belong to AstrBot itself, such as /help and /reset.
        if original_text.lstrip().startswith("/"):
            return
        if is_sanbot_command(text):
            logger.debug(
                "SanBot router delegated business command to SanBot: %s", text[:80]
            )
            event.stop_event()
            return
        if event.is_at_or_wake_command:
            return
        # Ordinary messages continue into AstrBot's built-in GroupChatContext.
        # Its active_reply feature owns context collection and probabilistic replies.

    @filter.on_llm_request(priority=maxsize - 100)
    async def guard_active_reply(
        self,
        event: AstrMessageEvent,
        _request: ProviderRequest,
    ) -> None:
        if event.is_at_or_wake_command:
            return

        group_id = str(event.get_group_id() or "")
        if not group_id or group_id not in self._allowed_groups():
            event.stop_event()
            return

        allowed = self.active_reply_limiter.allow(
            group_id,
            now=time.time(),
            day=date.today().isoformat(),
            cooldown_seconds=max(
                0, int(self.config.get("active_reply_cooldown_seconds", 600))
            ),
            daily_limit=max(0, int(self.config.get("active_reply_daily_limit", 30))),
        )
        if not allowed:
            logger.debug("AstrBot active reply suppressed by group limit: %s", group_id)
            event.stop_event()

    def _allowed_groups(self) -> set[str]:
        values = self.config.get("allowed_group_ids", [])
        if not isinstance(values, list):
            return set()
        return {str(value).strip() for value in values if str(value).strip().isdigit()}

    @staticmethod
    def _original_plain_text(event: AstrMessageEvent) -> str:
        return " ".join(
            component.text
            for component in event.get_messages()
            if isinstance(component, Plain) and isinstance(component.text, str)
        ).strip()
