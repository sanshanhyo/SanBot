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

logger = logging.getLogger(__name__)

TG_REF_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,64}$")
ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class TelegramMirrorError(Exception):
    def __init__(self, user_message: str, error_code: str = "TG_MIRROR_ERROR") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.error_code = error_code


@dataclass(frozen=True)
class TelegramMirrorConfig:
    data_dir: Path
    enabled: bool = False
    api_id: int | None = None
    api_hash: str | None = None
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

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def bind_channel(self, group_id: str, channel_ref: str) -> dict[str, Any]:
        self._require_enabled()
        normalized_ref = normalize_channel_ref(channel_ref)
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

    async def fetch_latest(self, group_id: str, limit: int) -> dict[str, Any]:
        self._require_enabled()
        await self.cleanup_media_cache()
        channels = self.list_channels(group_id)
        if not channels:
            return {"items": [], "channels": [], "skipped": 0}

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


def _message_datetime(message: Any) -> str | None:
    value = getattr(message, "date", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return None


def _message_caption(message: Any) -> str | None:
    text = getattr(message, "raw_text", None) or getattr(message, "message", None)
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip()
    return text[:500] if text else None


def _message_url(channel: dict[str, Any], message_id: int) -> str | None:
    ref = str(channel.get("channel_ref") or "")
    if TG_REF_PATTERN.fullmatch(ref):
        return f"https://t.me/{ref}/{message_id}"
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
