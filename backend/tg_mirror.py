from __future__ import annotations

import logging
import mimetypes
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

TG_REF_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,64}$")
ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
BOT_API_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


class TelegramMirrorError(Exception):
    def __init__(self, user_message: str, error_code: str = "TG_MIRROR_ERROR") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.error_code = error_code


@dataclass(frozen=True)
class TelegramMirrorConfig:
    data_dir: Path
    enabled: bool = False
    mode: str = "telethon"
    api_id: int | None = None
    api_hash: str | None = None
    bot_token: str | None = None
    session_string: str | None = None
    session_path: Path | None = None
    max_file_bytes: int = 100 * 1024 * 1024
    default_fetch_limit: int = 5
    max_fetch_limit: int = 10
    scan_limit: int = 30
    media_cache_ttl_seconds: int = 86400


class TelegramMirrorService:
    def __init__(self, config: TelegramMirrorConfig) -> None:
        self.config = config
        self.db_path = config.data_dir / "telegram_mirror.sqlite3"
        self.media_dir = (config.data_dir / "tg_media").resolve()
        self._client: Any | None = None

    def initialize(self) -> None:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tg_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    channel_ref TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    channel_title TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(group_id, channel_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tg_channels_group ON tg_channels(group_id, enabled)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tg_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(group_id, channel_id, message_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tg_messages_group ON tg_messages(group_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tg_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def bind_channel(self, group_id: str, channel_ref: str) -> dict[str, Any]:
        self._require_enabled()
        normalized_ref = normalize_channel_ref(channel_ref)
        if self._mode == "bot":
            return self._bind_bot_channel(group_id, normalized_ref)

        client = await self._get_client()
        try:
            entity = await client.get_entity(normalized_ref)
        except Exception as exc:
            logger.exception("Could not resolve Telegram channel.")
            raise TelegramMirrorError("Telegram 频道解析失败，请确认账号已加入该频道", "TG_CHANNEL_RESOLVE_FAILED") from exc

        channel_id = self._entity_id(entity)
        title = self._entity_title(entity, normalized_ref)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tg_channels (
                    group_id, channel_ref, channel_id, channel_title, enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(group_id, channel_id) DO UPDATE SET
                    channel_ref=excluded.channel_ref,
                    channel_title=excluded.channel_title,
                    enabled=1,
                    updated_at=excluded.updated_at
                """,
                (str(group_id), normalized_ref, channel_id, title, now, now),
            )
            row = conn.execute(
                "SELECT * FROM tg_channels WHERE group_id = ? AND channel_id = ?",
                (str(group_id), channel_id),
            ).fetchone()
        return _channel_row_to_dict(row)

    def _bind_bot_channel(self, group_id: str, normalized_ref: str) -> dict[str, Any]:
        now = _utc_now()
        placeholder_channel_id = f"bot:{normalized_ref.lower()}"
        title = normalized_ref
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tg_channels
                WHERE group_id = ? AND channel_ref = ?
                """,
                (str(group_id), normalized_ref),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO tg_channels (
                        group_id, channel_ref, channel_id, channel_title, enabled, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (str(group_id), normalized_ref, placeholder_channel_id, title, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE tg_channels
                    SET channel_title = ?, enabled = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (row["channel_title"] or title, now, row["id"]),
                )
            row = conn.execute(
                """
                SELECT * FROM tg_channels
                WHERE group_id = ? AND channel_ref = ?
                """,
                (str(group_id), normalized_ref),
            ).fetchone()
        return _channel_row_to_dict(row)

    def list_channels(self, group_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tg_channels
                WHERE group_id = ? AND enabled = 1
                ORDER BY id DESC
                """,
                (str(group_id),),
            ).fetchall()
        return [_channel_row_to_dict(row) for row in rows]

    def list_group_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT group_id FROM tg_channels
                WHERE enabled = 1
                ORDER BY group_id
                """
            ).fetchall()
        return [str(row["group_id"]) for row in rows if str(row["group_id"]).isdigit()]

    async def fetch_latest(self, group_id: str, limit: int) -> dict[str, Any]:
        self._require_enabled()
        await self.cleanup_media_cache()
        channels = self.list_channels(group_id)
        if not channels:
            return {"items": [], "channels": [], "skipped": 0}
        if self._mode == "bot":
            return await self._fetch_latest_bot(group_id, limit, channels)

        client = await self._get_client()
        remaining = max(1, min(limit, self.config.max_fetch_limit))
        items: list[dict[str, Any]] = []
        skipped = 0
        for channel in channels:
            if remaining <= 0:
                break
            try:
                entity = await client.get_entity(channel["channel_ref"])
                async for message in client.iter_messages(entity, limit=self.config.scan_limit):
                    if remaining <= 0:
                        break
                    media_type = _message_media_type(message)
                    if media_type is None:
                        continue
                    message_id = int(getattr(message, "id", 0) or 0)
                    if message_id <= 0 or self._message_seen(group_id, channel["channel_id"], message_id):
                        continue
                    file_size = _message_file_size(message)
                    if file_size is not None and file_size > self.config.max_file_bytes:
                        skipped += 1
                        continue
                    item = await self._download_message_media(client, channel, message, media_type)
                    if item is None:
                        skipped += 1
                        continue
                    items.append(item)
                    remaining -= 1
            except TelegramMirrorError:
                raise
            except Exception as exc:
                logger.exception("Could not fetch Telegram media for channel_id=%s.", channel.get("channel_id"))
                raise TelegramMirrorError("Telegram 频道内容拉取失败，请稍后再试", "TG_FETCH_FAILED") from exc
        return {"items": items, "channels": channels, "skipped": skipped}

    async def cleanup_media_cache(self) -> None:
        ttl = max(0, self.config.media_cache_ttl_seconds)
        if ttl <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl)
        cutoff_text = cutoff.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, file_path FROM tg_messages WHERE fetched_at < ?",
                (cutoff_text,),
            ).fetchall()
            for row in rows:
                path = Path(str(row["file_path"])).resolve()
                if path.is_file() and path.is_relative_to(self.media_dir):
                    path.unlink(missing_ok=True)
            conn.execute("DELETE FROM tg_messages WHERE fetched_at < ?", (cutoff_text,))

    async def _fetch_latest_bot(
        self,
        group_id: str,
        limit: int,
        requested_channels: list[dict[str, Any]],
    ) -> dict[str, Any]:
        all_channels = self._list_all_channels()
        if not all_channels:
            return {"items": [], "channels": requested_channels, "skipped": 0}

        remaining = max(1, min(limit, self.config.max_fetch_limit))
        items: list[dict[str, Any]] = []
        skipped = 0
        offset = self._get_state_int("bot_update_offset")
        updates = await self._bot_api_json("getUpdates", params={"offset": offset, "limit": 100, "timeout": 0})
        result = updates.get("result")
        if not isinstance(result, list):
            raise TelegramMirrorError("Telegram Bot API 返回结果无效", "TG_BOT_BAD_RESPONSE")

        last_consumed_update_id: int | None = None
        for update in result:
            if not isinstance(update, dict):
                continue
            update_id = _safe_int(update.get("update_id"))
            channel_post = update.get("channel_post")
            if not isinstance(channel_post, dict):
                if update_id is not None:
                    last_consumed_update_id = update_id
                continue

            chat = channel_post.get("chat")
            if not isinstance(chat, dict):
                if update_id is not None:
                    last_consumed_update_id = update_id
                continue

            matched_channels = self._matching_bot_channels(all_channels, chat)
            if not matched_channels:
                if update_id is not None:
                    last_consumed_update_id = update_id
                continue

            media = _bot_message_media(channel_post)
            if media is None:
                if update_id is not None:
                    last_consumed_update_id = update_id
                continue

            message_id = _safe_int(channel_post.get("message_id"))
            if message_id is None:
                if update_id is not None:
                    last_consumed_update_id = update_id
                continue

            requested_group_hit = any(str(channel["group_id"]) == str(group_id) for channel in matched_channels)
            if requested_group_hit and len(items) >= remaining:
                break

            for channel in matched_channels:
                real_channel = self._update_bot_channel_from_chat(channel, chat)
                if self._message_seen(real_channel["group_id"], real_channel["channel_id"], message_id):
                    continue
                if media["file_size"] and int(media["file_size"]) > self._bot_max_file_bytes:
                    self._record_skipped_message(real_channel, message_id, media["media_type"], int(media["file_size"]), _bot_message_datetime(channel_post))
                    if str(real_channel["group_id"]) == str(group_id):
                        skipped += 1
                    continue
                try:
                    item = await self._download_bot_media(real_channel, channel_post, media)
                except TelegramMirrorError:
                    raise
                except Exception:
                    logger.exception("Could not download Telegram Bot API media.")
                    raise TelegramMirrorError("Telegram Bot API 媒体下载失败", "TG_BOT_DOWNLOAD_FAILED") from None
                if item is None:
                    if str(real_channel["group_id"]) == str(group_id):
                        skipped += 1
                    continue
                if str(real_channel["group_id"]) == str(group_id) and len(items) < remaining:
                    items.append(item)

            if update_id is not None:
                last_consumed_update_id = update_id

        if last_consumed_update_id is not None:
            self._set_state("bot_update_offset", str(last_consumed_update_id + 1))
        return {"items": items, "channels": self.list_channels(group_id), "skipped": skipped}

    async def _download_bot_media(
        self,
        channel: dict[str, Any],
        message: dict[str, Any],
        media: dict[str, Any],
    ) -> dict[str, Any] | None:
        message_id = int(message["message_id"])
        media_type = str(media["media_type"])
        ext = _bot_media_extension(media)
        channel_dir = (self.media_dir / str(channel["group_id"]) / _safe_path_piece(str(channel["channel_id"]))).resolve()
        if not channel_dir.is_relative_to(self.media_dir):
            raise TelegramMirrorError("Telegram 媒体缓存路径异常", "TG_INVALID_CACHE_PATH")
        channel_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(f"TG_{channel['channel_title']}_{message_id}{ext}", f"TG_{message_id}{ext}")
        target_path = (channel_dir / f"{message_id}_{uuid.uuid4().hex[:8]}{ext}").resolve()
        if not target_path.is_relative_to(channel_dir):
            raise TelegramMirrorError("Telegram 媒体缓存路径异常", "TG_INVALID_CACHE_PATH")

        file_info = await self._bot_api_json("getFile", params={"file_id": media["file_id"]})
        file_result = file_info.get("result")
        if not isinstance(file_result, dict) or not file_result.get("file_path"):
            return None
        actual_size = _safe_int(file_result.get("file_size")) or _safe_int(media.get("file_size")) or 0
        if actual_size > self._bot_max_file_bytes:
            self._record_skipped_message(channel, message_id, media_type, actual_size, _bot_message_datetime(message))
            return None

        file_url = self._bot_file_url(str(file_result["file_path"]))
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", file_url) as response:
                    response.raise_for_status()
                    size = 0
                    with target_path.open("wb") as file:
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > self._bot_max_file_bytes:
                                target_path.unlink(missing_ok=True)
                                self._record_skipped_message(
                                    channel,
                                    message_id,
                                    media_type,
                                    size,
                                    _bot_message_datetime(message),
                                )
                                return None
                            file.write(chunk)
        except httpx.HTTPError as exc:
            target_path.unlink(missing_ok=True)
            raise TelegramMirrorError("Telegram Bot API 媒体下载失败", "TG_BOT_DOWNLOAD_FAILED") from None

        if not target_path.is_file() or target_path.stat().st_size <= 0:
            target_path.unlink(missing_ok=True)
            return None

        file_size = target_path.stat().st_size
        created_at = _bot_message_datetime(message)
        fetched_at = _utc_now()
        caption = _bot_message_caption(message)
        message_url = _message_url(channel, message_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO tg_messages (
                    group_id, channel_id, message_id, media_type, file_path, file_size, status, created_at, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'downloaded', ?, ?)
                """,
                (
                    str(channel["group_id"]),
                    str(channel["channel_id"]),
                    message_id,
                    media_type,
                    str(target_path),
                    file_size,
                    created_at,
                    fetched_at,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM tg_messages
                WHERE group_id = ? AND channel_id = ? AND message_id = ?
                """,
                (str(channel["group_id"]), str(channel["channel_id"]), message_id),
            ).fetchone()

        return {
            "id": int(row["id"]) if row else 0,
            "channel_id": str(channel["channel_id"]),
            "channel_title": str(channel["channel_title"]),
            "message_id": message_id,
            "media_type": media_type,
            "file_path": str(target_path),
            "filename": filename,
            "file_size": file_size,
            "caption": caption,
            "message_url": message_url,
            "created_at": created_at,
        }

    async def _download_message_media(
        self,
        client: Any,
        channel: dict[str, Any],
        message: Any,
        media_type: str,
    ) -> dict[str, Any] | None:
        message_id = int(getattr(message, "id", 0) or 0)
        ext = _message_extension(message, media_type)
        channel_dir = (self.media_dir / str(channel["group_id"]) / _safe_path_piece(str(channel["channel_id"]))).resolve()
        if not channel_dir.is_relative_to(self.media_dir):
            raise TelegramMirrorError("Telegram 媒体缓存路径异常", "TG_INVALID_CACHE_PATH")
        channel_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(f"TG_{channel['channel_title']}_{message_id}{ext}", f"TG_{message_id}{ext}")
        target_path = (channel_dir / f"{message_id}_{uuid.uuid4().hex[:8]}{ext}").resolve()
        if not target_path.is_relative_to(channel_dir):
            raise TelegramMirrorError("Telegram 媒体缓存路径异常", "TG_INVALID_CACHE_PATH")

        downloaded = await client.download_media(message, file=str(target_path))
        if not downloaded:
            target_path.unlink(missing_ok=True)
            return None
        file_path = Path(str(downloaded)).resolve()
        if not file_path.is_file() or not file_path.is_relative_to(channel_dir):
            file_path.unlink(missing_ok=True)
            return None
        file_size = file_path.stat().st_size
        if file_size <= 0 or file_size > self.config.max_file_bytes:
            file_path.unlink(missing_ok=True)
            return None

        created_at = _message_datetime(message)
        fetched_at = _utc_now()
        message_url = _message_url(channel, message_id)
        caption = _message_caption(message)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO tg_messages (
                    group_id, channel_id, message_id, media_type, file_path, file_size, status, created_at, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'downloaded', ?, ?)
                """,
                (
                    str(channel["group_id"]),
                    str(channel["channel_id"]),
                    message_id,
                    media_type,
                    str(file_path),
                    file_size,
                    created_at,
                    fetched_at,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM tg_messages
                WHERE group_id = ? AND channel_id = ? AND message_id = ?
                """,
                (str(channel["group_id"]), str(channel["channel_id"]), message_id),
            ).fetchone()

        return {
            "id": int(row["id"]) if row else 0,
            "channel_id": str(channel["channel_id"]),
            "channel_title": str(channel["channel_title"]),
            "message_id": message_id,
            "media_type": media_type,
            "file_path": str(file_path),
            "filename": filename,
            "file_size": file_size,
            "caption": caption,
            "message_url": message_url,
            "created_at": created_at,
        }

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise TelegramMirrorError("Telegram 镜像功能未启用", "TG_DISABLED")
        if self._mode == "bot":
            if not self.config.bot_token:
                raise TelegramMirrorError("Telegram Bot Token 未配置", "TG_BOT_TOKEN_MISSING")
            return
        if not self.config.api_id or not self.config.api_hash:
            raise TelegramMirrorError("Telegram API 凭据未配置", "TG_NOT_CONFIGURED")
        if not self.config.session_string and not self.config.session_path:
            raise TelegramMirrorError("Telegram 会话未配置", "TG_SESSION_NOT_CONFIGURED")

    async def _get_client(self) -> Any:
        self._require_enabled()
        if self._client is None:
            try:
                from telethon import TelegramClient, utils
                from telethon.sessions import StringSession
            except ImportError as exc:
                raise TelegramMirrorError("缺少 Telethon 依赖", "TG_DEPENDENCY_MISSING") from exc

            self._utils = utils
            if self.config.session_string:
                session: Any = StringSession(self.config.session_string)
            else:
                session_path = (self.config.session_path or (self.config.data_dir / "telegram.session")).resolve()
                session_path.parent.mkdir(parents=True, exist_ok=True)
                session = str(session_path)
            self._client = TelegramClient(session, self.config.api_id, self.config.api_hash)
            await self._client.connect()
        if not await self._client.is_user_authorized():
            raise TelegramMirrorError("Telegram 会话未登录或已失效", "TG_SESSION_UNAUTHORIZED")
        return self._client

    def _entity_id(self, entity: Any) -> str:
        utils = getattr(self, "_utils", None)
        if utils is not None:
            try:
                return str(utils.get_peer_id(entity))
            except Exception:
                pass
        return str(getattr(entity, "id", ""))

    @staticmethod
    def _entity_title(entity: Any, fallback: str) -> str:
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or fallback
        return " ".join(str(title).split())[:120] or fallback

    def _message_seen(self, group_id: str, channel_id: str, message_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM tg_messages
                WHERE group_id = ? AND channel_id = ? AND message_id = ?
                """,
                (str(group_id), str(channel_id), int(message_id)),
            ).fetchone()
        return row is not None

    def _record_skipped_message(
        self,
        channel: dict[str, Any],
        message_id: int,
        media_type: str,
        file_size: int,
        created_at: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO tg_messages (
                    group_id, channel_id, message_id, media_type, file_path, file_size, status, created_at, fetched_at
                )
                VALUES (?, ?, ?, ?, '', ?, 'skipped', ?, ?)
                """,
                (
                    str(channel["group_id"]),
                    str(channel["channel_id"]),
                    int(message_id),
                    media_type,
                    int(file_size),
                    created_at,
                    _utc_now(),
                ),
            )

    def _list_all_channels(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tg_channels
                WHERE enabled = 1
                ORDER BY id ASC
                """
            ).fetchall()
        return [_channel_row_to_dict(row) for row in rows]

    def _matching_bot_channels(self, channels: list[dict[str, Any]], chat: dict[str, Any]) -> list[dict[str, Any]]:
        chat_id = str(chat.get("id") or "")
        username = str(chat.get("username") or "").lower()
        matched: list[dict[str, Any]] = []
        for channel in channels:
            channel_ref = str(channel.get("channel_ref") or "").lower()
            channel_id = str(channel.get("channel_id") or "")
            if channel_id == chat_id or (username and channel_ref == username):
                matched.append(channel)
        return matched

    def _update_bot_channel_from_chat(self, channel: dict[str, Any], chat: dict[str, Any]) -> dict[str, Any]:
        chat_id = str(chat.get("id") or channel.get("channel_id") or "")
        title = " ".join(str(chat.get("title") or channel.get("channel_title") or channel.get("channel_ref") or "").split())
        username = str(chat.get("username") or channel.get("channel_ref") or "").strip() or str(channel.get("channel_ref"))
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tg_channels
                SET channel_id = ?, channel_title = ?, channel_ref = ?, updated_at = ?
                WHERE id = ?
                """,
                (chat_id, title[:120] or username, username, now, int(channel["id"])),
            )
            row = conn.execute("SELECT * FROM tg_channels WHERE id = ?", (int(channel["id"]),)).fetchone()
        return _channel_row_to_dict(row)

    async def _bot_api_json(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.config.bot_token:
            raise TelegramMirrorError("Telegram Bot Token 未配置", "TG_BOT_TOKEN_MISSING")
        url = f"https://api.telegram.org/bot{self.config.bot_token}/{method}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.get(url, params=params or {})
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError:
                raise TelegramMirrorError("Telegram Bot API 请求失败", "TG_BOT_API_FAILED") from None
            except ValueError:
                raise TelegramMirrorError("Telegram Bot API 返回结果无效", "TG_BOT_BAD_RESPONSE") from None
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise TelegramMirrorError("Telegram Bot API 返回失败", "TG_BOT_API_FAILED")
        return payload

    def _bot_file_url(self, file_path: str) -> str:
        if not self.config.bot_token:
            raise TelegramMirrorError("Telegram Bot Token 未配置", "TG_BOT_TOKEN_MISSING")
        return f"https://api.telegram.org/file/bot{self.config.bot_token}/{file_path.lstrip('/')}"

    @property
    def _mode(self) -> str:
        mode = (self.config.mode or "telethon").strip().lower()
        return "bot" if mode in {"bot", "bot_api", "bot-api"} else "telethon"

    @property
    def _bot_max_file_bytes(self) -> int:
        return min(max(1, self.config.max_file_bytes), BOT_API_MAX_DOWNLOAD_BYTES)

    def _get_state_int(self, key: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM tg_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return 0
        try:
            return max(0, int(row["value"]))
        except (TypeError, ValueError):
            return 0

    def _set_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tg_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def normalize_channel_ref(value: str) -> str:
    ref = " ".join(str(value or "").strip().split())
    if not ref:
        raise TelegramMirrorError("Telegram 频道地址不能为空", "TG_CHANNEL_REF_INVALID")
    if ref.startswith("@"):
        ref = ref[1:]
    if ref.startswith(("http://", "https://")):
        parts = urlsplit(ref)
        if parts.netloc.lower() not in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
            raise TelegramMirrorError("只支持 t.me / telegram.me 频道链接", "TG_CHANNEL_REF_INVALID")
        pieces = [piece for piece in parts.path.split("/") if piece]
        if not pieces:
            raise TelegramMirrorError("Telegram 频道地址无效", "TG_CHANNEL_REF_INVALID")
        ref = pieces[0]
    if not TG_REF_PATTERN.fullmatch(ref):
        raise TelegramMirrorError("Telegram 频道名格式无效", "TG_CHANNEL_REF_INVALID")
    return ref


def _channel_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": int(row["id"]),
        "group_id": str(row["group_id"]),
        "channel_ref": str(row["channel_ref"]),
        "channel_id": str(row["channel_id"]),
        "channel_title": str(row["channel_title"]),
        "enabled": bool(row["enabled"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _message_media_type(message: Any) -> str | None:
    file_obj = getattr(message, "file", None)
    mime_type = str(getattr(file_obj, "mime_type", "") or "")
    if getattr(message, "photo", None) is not None or mime_type.startswith("image/"):
        return "image"
    if getattr(message, "video", None) is not None or mime_type.startswith("video/"):
        return "video"
    return None


def _message_file_size(message: Any) -> int | None:
    file_obj = getattr(message, "file", None)
    size = getattr(file_obj, "size", None)
    try:
        return int(size) if size is not None else None
    except (TypeError, ValueError):
        return None


def _message_extension(message: Any, media_type: str) -> str:
    file_obj = getattr(message, "file", None)
    ext = str(getattr(file_obj, "ext", "") or "")
    if ext and re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext):
        return ext.lower()
    mime_type = str(getattr(file_obj, "mime_type", "") or "")
    guessed = mimetypes.guess_extension(mime_type) if mime_type else None
    if guessed and re.fullmatch(r"\.[A-Za-z0-9]{1,8}", guessed):
        return guessed.lower()
    return ".jpg" if media_type == "image" else ".mp4"


def _bot_message_media(message: dict[str, Any]) -> dict[str, Any] | None:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        candidates = [photo for photo in photos if isinstance(photo, dict) and photo.get("file_id")]
        if candidates:
            photo = max(candidates, key=lambda item: int(item.get("file_size") or 0))
            return {
                "media_type": "image",
                "file_id": str(photo["file_id"]),
                "file_size": int(photo.get("file_size") or 0),
                "mime_type": "image/jpeg",
                "file_name": None,
            }

    video = message.get("video")
    if isinstance(video, dict) and video.get("file_id"):
        return {
            "media_type": "video",
            "file_id": str(video["file_id"]),
            "file_size": int(video.get("file_size") or 0),
            "mime_type": str(video.get("mime_type") or "video/mp4"),
            "file_name": video.get("file_name"),
        }
    return None


def _bot_media_extension(media: dict[str, Any]) -> str:
    file_name = media.get("file_name")
    if isinstance(file_name, str):
        suffix = Path(file_name).suffix.lower()
        if suffix and re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix):
            return suffix
    mime_type = str(media.get("mime_type") or "")
    guessed = mimetypes.guess_extension(mime_type) if mime_type else None
    if guessed and re.fullmatch(r"\.[A-Za-z0-9]{1,8}", guessed):
        return guessed.lower()
    return ".jpg" if media.get("media_type") == "image" else ".mp4"


def _message_datetime(message: Any) -> str | None:
    value = getattr(message, "date", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return None


def _bot_message_datetime(message: dict[str, Any]) -> str | None:
    value = _safe_int(message.get("date"))
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _message_caption(message: Any) -> str | None:
    text = getattr(message, "raw_text", None) or getattr(message, "message", None)
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip()
    return text[:500] if text else None


def _bot_message_caption(message: dict[str, Any]) -> str | None:
    text = message.get("caption") or message.get("text")
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip()
    return text[:500] if text else None


def _message_url(channel: dict[str, Any], message_id: int) -> str | None:
    ref = str(channel.get("channel_ref") or "")
    if TG_REF_PATTERN.fullmatch(ref):
        return f"https://t.me/{ref}/{message_id}"
    return None


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _safe_path_piece(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned[:80] or "channel"


def _safe_filename(name: str, fallback: str, max_bytes: int = 96) -> str:
    cleaned = ILLEGAL_FILENAME_CHARS_RE.sub("_", name).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned) or fallback
    while len(cleaned.encode("utf-8")) > max_bytes and len(cleaned) > 1:
        stem = Path(cleaned).stem
        suffix = Path(cleaned).suffix
        cleaned = f"{stem[:-1]}{suffix}"
    return cleaned or fallback


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
