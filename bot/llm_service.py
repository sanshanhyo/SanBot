from __future__ import annotations

import asyncio
import logging
import random
import re
import sqlite3
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .llm_client import LLMError, OpenAIChatClient

logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    async def chat(self, messages: list[dict[str, str]]) -> str: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class LLMChatConfig:
    system_prompt: str
    bot_name: str = "SanBot"
    context_messages: int = 12
    context_ttl_seconds: int = 3600
    user_cooldown_seconds: int = 10
    daily_group_limit: int = 100
    max_concurrent_requests: int = 2
    max_input_chars: int = 2000
    max_reply_chars: int = 3000
    ambient_probability: float = 0.03
    ambient_group_cooldown_seconds: int = 600
    ambient_daily_limit: int = 30
    ambient_min_delay_seconds: float = 2
    ambient_max_delay_seconds: float = 8


@dataclass(frozen=True)
class LLMResult:
    status: str
    text: str | None = None
    error_code: str | None = None
    retry_after: int | None = None


@dataclass(frozen=True)
class AmbientMessage:
    created_at: float
    user_id: str
    sender_name: str
    text: str


class LLMStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def context(self, group_id: str, user_id: str, *, limit: int, ttl_seconds: int) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._context, group_id, user_id, limit, ttl_seconds)

    async def append_exchange(self, group_id: str, user_id: str, user_text: str, assistant_text: str) -> None:
        await asyncio.to_thread(self._append_exchange, group_id, user_id, user_text, assistant_text)

    async def reset(self, group_id: str, user_id: str) -> int:
        return await asyncio.to_thread(self._reset, group_id, user_id)

    async def consume_daily(self, group_id: str, *, bucket: str, limit: int) -> bool:
        if limit <= 0:
            return True
        return await asyncio.to_thread(self._consume_daily, group_id, bucket, limit)

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS llm_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_llm_messages_session
                    ON llm_messages(group_id, user_id, id);
                CREATE TABLE IF NOT EXISTS llm_daily_usage (
                    day TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY(day, group_id, bucket)
                );
                """
            )

    def _context(self, group_id: str, user_id: str, limit: int, ttl_seconds: int) -> list[dict[str, str]]:
        cutoff = time.time() - max(1, ttl_seconds)
        with self._connect() as conn:
            conn.execute("DELETE FROM llm_messages WHERE created_at < ?", (cutoff,))
            rows = conn.execute(
                """
                SELECT role, content
                FROM llm_messages
                WHERE group_id = ? AND user_id = ? AND created_at >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (group_id, user_id, cutoff, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def _append_exchange(self, group_id: str, user_id: str, user_text: str, assistant_text: str) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO llm_messages(created_at, group_id, user_id, role, content) VALUES (?, ?, ?, ?, ?)",
                [
                    (now, group_id, user_id, "user", user_text),
                    (now, group_id, user_id, "assistant", assistant_text),
                ],
            )

    def _reset(self, group_id: str, user_id: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM llm_messages WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            )
            return int(cursor.rowcount or 0)

    def _consume_daily(self, group_id: str, bucket: str, limit: int) -> bool:
        day = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT request_count FROM llm_daily_usage WHERE day = ? AND group_id = ? AND bucket = ?",
                (day, group_id, bucket),
            ).fetchone()
            count = int(row[0]) if row else 0
            if count >= limit:
                return False
            conn.execute(
                """
                INSERT INTO llm_daily_usage(day, group_id, bucket, request_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(day, group_id, bucket)
                DO UPDATE SET request_count = request_count + 1
                """,
                (day, group_id, bucket),
            )
        return True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


class LLMChatService:
    def __init__(
        self,
        config: LLMChatConfig,
        client: ChatClient,
        store: LLMStore,
        *,
        random_value: Callable[[], float] = random.random,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self._semaphore = asyncio.Semaphore(max(1, config.max_concurrent_requests))
        self._random_value = random_value
        self._random_uniform = random_uniform
        self._direct_cooldowns: dict[tuple[str, str], float] = {}
        self._ambient_cooldowns: dict[str, float] = {}
        self._active_sessions: set[tuple[str, str]] = set()
        self._active_ambient_groups: set[str] = set()
        self._ambient_context: dict[str, deque[AmbientMessage]] = defaultdict(deque)

    async def initialize(self) -> None:
        await self.store.initialize()

    async def close(self) -> None:
        await self.client.close()

    async def reset(self, group_id: str, user_id: str) -> int:
        self._direct_cooldowns.pop((group_id, user_id), None)
        return await self.store.reset(group_id, user_id)

    async def explicit_reply(self, group_id: str, user_id: str, text: str) -> LLMResult:
        text = _clean_text(text, self.config.max_input_chars)
        if not text:
            return LLMResult("empty", error_code="LLM_EMPTY_INPUT")

        now = time.monotonic()
        key = (group_id, user_id)
        retry_after = self._remaining(self._direct_cooldowns.get(key), now, self.config.user_cooldown_seconds)
        if retry_after > 0:
            return LLMResult("cooldown", retry_after=retry_after)
        if key in self._active_sessions:
            return LLMResult("busy", error_code="LLM_BUSY")
        self._active_sessions.add(key)
        acquired = await self._acquire()
        if not acquired:
            self._active_sessions.discard(key)
            return LLMResult("busy", error_code="LLM_BUSY")
        try:
            if not await self.store.consume_daily(group_id, bucket="explicit", limit=self.config.daily_group_limit):
                return LLMResult("daily_limit", error_code="LLM_DAILY_LIMIT")
            self._direct_cooldowns[key] = now
            context = await self.store.context(
                group_id,
                user_id,
                limit=self.config.context_messages,
                ttl_seconds=self.config.context_ttl_seconds,
            )
            messages = [{"role": "system", "content": self.config.system_prompt}, *context]
            messages.append({"role": "user", "content": text})
            answer = _clean_text(await self.client.chat(messages), self.config.max_reply_chars)
            if not answer:
                raise LLMError("LLM provider returned an empty response", "LLM_EMPTY_RESPONSE")
            await self.store.append_exchange(group_id, user_id, text, answer)
            return LLMResult("ok", text=answer)
        except LLMError as exc:
            return LLMResult("error", error_code=exc.error_code)
        except Exception:
            logger.exception("Unexpected failure while processing explicit LLM chat.")
            return LLMResult("error", error_code="LLM_INTERNAL_ERROR")
        finally:
            self._active_sessions.discard(key)
            self._semaphore.release()

    async def ambient_reply(
        self,
        *,
        group_id: str,
        user_id: str,
        sender_name: str,
        text: str,
        has_at: bool,
    ) -> LLMResult | None:
        text = _clean_text(text, self.config.max_input_chars)
        if not self._ambient_candidate(text, has_at=has_at):
            return None

        now = time.monotonic()
        context = self._ambient_messages(group_id, now)
        context.append(AmbientMessage(now, user_id, sender_name, text))
        if self._remaining(
            self._ambient_cooldowns.get(group_id),
            now,
            self.config.ambient_group_cooldown_seconds,
        ) > 0:
            return None
        if group_id in self._active_ambient_groups:
            return None

        probability = ambient_reply_probability(text, self.config.bot_name, self.config.ambient_probability)
        if self._random_value() >= probability:
            return None
        self._active_ambient_groups.add(group_id)
        acquired = await self._acquire()
        if not acquired:
            self._active_ambient_groups.discard(group_id)
            return None
        try:
            if not await self.store.consume_daily(group_id, bucket="ambient", limit=self.config.ambient_daily_limit):
                return None
            self._ambient_cooldowns[group_id] = now
            delay = self._random_uniform(
                self.config.ambient_min_delay_seconds,
                self.config.ambient_max_delay_seconds,
            )
            if delay > 0:
                await asyncio.sleep(delay)
            recent = list(context)[-self.config.context_messages :]
            transcript = "\n".join(f"{item.sender_name}: {item.text}" for item in recent)
            messages = [
                {"role": "system", "content": self.config.system_prompt},
                {
                    "role": "user",
                    "content": (
                        "下面是群聊中最近的消息。请仅在自然、有帮助且不会打断聊天时简短回应最后一条；"
                        "不要声称自己看到了未提供的内容。\n\n" + transcript
                    ),
                },
            ]
            answer = _clean_text(await self.client.chat(messages), self.config.max_reply_chars)
            if not answer:
                raise LLMError("LLM provider returned an empty response", "LLM_EMPTY_RESPONSE")
            context.append(AmbientMessage(time.monotonic(), "bot", self.config.bot_name, answer))
            return LLMResult("ok", text=answer)
        except LLMError as exc:
            return LLMResult("error", error_code=exc.error_code)
        except Exception:
            logger.exception("Unexpected failure while processing ambient LLM chat.")
            return LLMResult("error", error_code="LLM_INTERNAL_ERROR")
        finally:
            self._active_ambient_groups.discard(group_id)
            self._semaphore.release()

    async def _acquire(self) -> bool:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=0.1)
            return True
        except TimeoutError:
            return False

    def _ambient_messages(self, group_id: str, now: float) -> deque[AmbientMessage]:
        messages = self._ambient_context[group_id]
        cutoff = now - max(1, self.config.context_ttl_seconds)
        while messages and messages[0].created_at < cutoff:
            messages.popleft()
        max_items = max(4, self.config.context_messages * 2)
        while len(messages) >= max_items:
            messages.popleft()
        return messages

    @staticmethod
    def _remaining(last_at: float | None, now: float, cooldown: int) -> int:
        if last_at is None or cooldown <= 0:
            return 0
        return max(0, int(last_at + cooldown - now + 0.999))

    @staticmethod
    def _ambient_candidate(text: str, *, has_at: bool) -> bool:
        if has_at or len(text) < 2:
            return False
        if text.startswith(("/", "!", "#")) or re.match(r"(?i)^(?:JM|JAV|AV|DB|TG)\s*\S+", text):
            return False
        if re.fullmatch(r"[\W_]+", text, flags=re.UNICODE):
            return False
        return True


def ambient_reply_probability(text: str, bot_name: str, base_probability: float) -> float:
    probability = min(1.0, max(0.0, base_probability))
    normalized = text.casefold()
    if bot_name and bot_name.casefold() in normalized:
        probability = max(probability, 0.85)
    if any(word in text for word in ("有人吗", "你们觉得", "怎么办", "有没有人", "谁知道", "为什么")):
        probability = max(probability, 0.45)
    elif text.rstrip().endswith(("?", "？")) or any(word in text for word in ("吗", "呢", "怎么")):
        probability = max(probability, 0.25)
    return min(1.0, probability)


def _clean_text(value: str, max_chars: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[: max(1, max_chars)]


def create_llm_service(
    *,
    config: LLMChatConfig,
    db_path: Path,
    base_url: str,
    api_key: str | None,
    model: str,
    timeout_seconds: int,
    max_output_tokens: int,
    temperature: float,
) -> LLMChatService:
    client = OpenAIChatClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )
    return LLMChatService(config, client, LLMStore(db_path))
