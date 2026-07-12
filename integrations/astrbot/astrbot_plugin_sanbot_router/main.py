from __future__ import annotations

from sys import maxsize

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .meme_knowledge import build_meme_context
from .routing import is_sanbot_command, normalize_text


class SanBotRouter(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=maxsize)
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
        # Ordinary messages continue into the configured AstrBot group-chat engine.

    @filter.on_llm_request(priority=maxsize - 100)
    async def inject_meme_knowledge(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
    ) -> None:
        group_id = str(event.get_group_id() or "")
        if not group_id or group_id not in self._allowed_groups():
            return

        context = build_meme_context(event.message_str)
        if context:
            request.system_prompt = f"{request.system_prompt}\n\n{context}".strip()
            logger.debug("Injected Xiaosan meme knowledge for group %s", group_id)

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
