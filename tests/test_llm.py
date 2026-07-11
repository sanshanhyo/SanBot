from __future__ import annotations

import asyncio

import httpx
import pytest

from bot.llm_client import LLMError, OpenAIChatClient
from bot.llm_service import LLMChatConfig, LLMChatService, LLMStore, ambient_reply_probability


class FakeChatClient:
    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = list(replies or ["你好呀"])
        self.requests: list[list[dict[str, str]]] = []
        self.closed = False

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.requests.append(messages)
        return self.replies.pop(0)

    async def close(self) -> None:
        self.closed = True


class BlockingChatClient(FakeChatClient):
    def __init__(self) -> None:
        super().__init__(["完成"])
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.requests.append(messages)
        self.started.set()
        await self.release.wait()
        return self.replies.pop(0)


@pytest.mark.asyncio
async def test_openai_client_calls_compatible_endpoint() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "模型回复"}}]})

    client = OpenAIChatClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="example-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.chat([{"role": "user", "content": "你好"}])
    finally:
        await client.close()

    assert result == "模型回复"
    assert captured["path"] == "/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret"
    assert '"model":"example-model"' in captured["body"]


@pytest.mark.asyncio
async def test_openai_client_maps_http_error_without_response_body() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="sensitive provider detail")

    client = OpenAIChatClient(
        base_url="https://llm.example/v1",
        api_key=None,
        model="example-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(LLMError) as raised:
            await client.chat([{"role": "user", "content": "你好"}])
    finally:
        await client.close()

    assert raised.value.error_code == "LLM_HTTP_429"
    assert "sensitive" not in str(raised.value)


@pytest.mark.asyncio
async def test_explicit_chat_persists_context_and_can_reset(tmp_path) -> None:
    client = FakeChatClient(["第一条回复", "第二条回复"])
    store = LLMStore(tmp_path / "llm.sqlite3")
    service = LLMChatService(
        LLMChatConfig(system_prompt="system", user_cooldown_seconds=0),
        client,
        store,
    )
    await service.initialize()

    first = await service.explicit_reply("10001", "20001", "第一条问题")
    second = await service.explicit_reply("10001", "20001", "第二条问题")

    assert first.text == "第一条回复"
    assert second.text == "第二条回复"
    assert client.requests[1][1:] == [
        {"role": "user", "content": "第一条问题"},
        {"role": "assistant", "content": "第一条回复"},
        {"role": "user", "content": "第二条问题"},
    ]
    assert await service.reset("10001", "20001") == 4
    assert await store.context("10001", "20001", limit=10, ttl_seconds=3600) == []


@pytest.mark.asyncio
async def test_same_user_cannot_start_overlapping_llm_requests(tmp_path) -> None:
    client = BlockingChatClient()
    service = LLMChatService(
        LLMChatConfig(system_prompt="system", user_cooldown_seconds=0, max_concurrent_requests=2),
        client,
        LLMStore(tmp_path / "llm.sqlite3"),
    )
    await service.initialize()

    first_task = asyncio.create_task(service.explicit_reply("10001", "20001", "第一条"))
    await client.started.wait()
    second = await service.explicit_reply("10001", "20001", "第二条")
    client.release.set()
    first = await first_task

    assert first.status == "ok"
    assert second.status == "busy"
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_ambient_chat_uses_local_trigger_and_recent_context(tmp_path) -> None:
    client = FakeChatClient(["我也觉得可以"])
    service = LLMChatService(
        LLMChatConfig(
            system_prompt="system",
            ambient_probability=1.0,
            ambient_group_cooldown_seconds=0,
            ambient_min_delay_seconds=0,
            ambient_max_delay_seconds=0,
        ),
        client,
        LLMStore(tmp_path / "llm.sqlite3"),
        random_value=lambda: 0.0,
        random_uniform=lambda _start, _end: 0.0,
    )
    await service.initialize()

    result = await service.ambient_reply(
        group_id="10001",
        user_id="20001",
        sender_name="小明",
        text="你们觉得今晚吃火锅怎么样？",
        has_at=False,
    )

    assert result is not None
    assert result.text == "我也觉得可以"
    assert "小明: 你们觉得今晚吃火锅怎么样？" in client.requests[0][-1]["content"]


@pytest.mark.asyncio
async def test_ambient_chat_ignores_mentions_and_commands(tmp_path) -> None:
    client = FakeChatClient()
    service = LLMChatService(
        LLMChatConfig(system_prompt="system", ambient_probability=1.0),
        client,
        LLMStore(tmp_path / "llm.sqlite3"),
        random_value=lambda: 0.0,
    )
    await service.initialize()

    mentioned = await service.ambient_reply(
        group_id="10001", user_id="20001", sender_name="小明", text="你觉得呢", has_at=True
    )
    command = await service.ambient_reply(
        group_id="10001", user_id="20001", sender_name="小明", text="JM123456", has_at=False
    )

    assert mentioned is None
    assert command is None
    assert client.requests == []


@pytest.mark.asyncio
async def test_daily_limit_is_atomic_per_group_and_bucket(tmp_path) -> None:
    store = LLMStore(tmp_path / "llm.sqlite3")
    await store.initialize()

    assert await store.consume_daily("10001", bucket="ambient", limit=1)
    assert not await store.consume_daily("10001", bucket="ambient", limit=1)
    assert await store.consume_daily("10001", bucket="explicit", limit=1)
    assert await store.consume_daily("10002", bucket="ambient", limit=1)


def test_ambient_probability_favors_name_and_questions() -> None:
    assert ambient_reply_probability("普通聊天", "SanBot", 0.03) == 0.03
    assert ambient_reply_probability("SanBot 你在吗", "SanBot", 0.03) == 0.85
    assert ambient_reply_probability("你们觉得怎么样？", "SanBot", 0.03) == 0.45
