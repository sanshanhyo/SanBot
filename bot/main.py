from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import re
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

from .backend_client import BackendClient, BackendError, DuplicateJobError, JobLimitError
from .message_parser import (
    ParseAction,
    normalize_message_segments,
    parse_group_message,
    text_from_segments,
)
from .napcat_client import NapCatAPIError, NapCatClient
from .lang import text as lang_text, words as lang_words

logger = logging.getLogger(__name__)

ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DEFAULT_CONFIRM_WORDS = {"下载", "确认", "同意", "是", "要", "y", "yes", "ok"}
DEFAULT_CANCEL_WORDS = {"取消", "取消下载", "取消任务", "不要", "否", "不下", "n", "no"}
DEFAULT_ACTIVE_CANCEL_WORDS = DEFAULT_CANCEL_WORDS | {"停止下载", "停止任务"}
DEFAULT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_FILENAME_BYTES = 180
MAX_UPLOAD_FILENAME_BYTES = 96
MAX_UPLOAD_FALLBACK_DEPTH = 1
DEFAULT_UPLOAD_RETRIES = 5
DEFAULT_SEARCH_RESULT_LIMIT = 5
DEFAULT_RANKING_RESULT_LIMIT = 10
DEFAULT_USER_COMMAND_COOLDOWN_SECONDS = 10
DEFAULT_JAV_ACTION_TIMEOUT_SECONDS = 300
DEFAULT_JAV_TRAILER_CONVERT_TIMEOUT_SECONDS = 180
DEFAULT_JAV_TRAILER_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_JAV_TRAILER_IMPERSONATES = ("chrome123", "chrome124", "chrome131", "firefox135", "chrome")
DEFAULT_JAV_TRAILER_HLS_ASSET_RETRIES = 3
DEFAULT_JAV_STILLS_MAX_COUNT = 3
DEFAULT_JAV_STILLS_PDF_MAX_IMAGES = 0
DEFAULT_JAV_STILLS_PDF_DOWNLOAD_CONCURRENCY = 4
DEFAULT_JAV_STILLS_PDF_DOWNLOAD_TIMEOUT_SECONDS = 60
DEFAULT_JAV_STILLS_MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_JAV_STILLS_MIN_IMAGE_WIDTH = 300
DEFAULT_JAV_STILLS_MIN_IMAGE_HEIGHT = 200
DEFAULT_MISSAV_MAX_GROUP_MEMBERS = 150
COVER_SEND_RETRIES = 1
COVER_DOWNLOAD_TIMEOUT_SECONDS = 20
MAX_COVER_IMAGE_BYTES = 8 * 1024 * 1024
GROUP_ADMIN_ROLES = {"owner", "admin"}


class UploadPreparationError(Exception):
    pass


class UploadCancelledError(Exception):
    pass


class JavStillsPdfError(Exception):
    pass


class JavTrailerError(Exception):
    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class AdminCommand:
    name: str
    target: str | None = None


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s; using %s.", name, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_id_set(name: str) -> set[str]:
    value = os.getenv(name, "")
    ids = {piece.strip() for piece in value.split(",") if piece.strip()}
    return {item for item in ids if item.isdigit()}


@dataclass(frozen=True)
class BotSettings:
    bot_qq_id: str
    napcat_ws_url: str
    napcat_http_url: str
    napcat_access_token: str | None
    backend_url: str
    backend_api_token: str | None
    data_dir: Path
    job_timeout_seconds: int
    poll_interval_seconds: float = 5.0
    progress_notify_seconds: int = 300
    confirm_timeout_seconds: int = 600
    large_album_warning_pages: int = 100
    max_album_pages: int = 300
    napcat_http_timeout_seconds: int = 60
    napcat_upload_timeout_seconds: int = 900
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    max_upload_filename_bytes: int = MAX_UPLOAD_FILENAME_BYTES
    upload_retries: int = DEFAULT_UPLOAD_RETRIES
    enable_search: bool = True
    enable_javlibrary: bool = True
    search_result_limit: int = DEFAULT_SEARCH_RESULT_LIMIT
    search_confirm_timeout_seconds: int = 600
    ranking_result_limit: int = DEFAULT_RANKING_RESULT_LIMIT
    user_command_cooldown_seconds: int = DEFAULT_USER_COMMAND_COOLDOWN_SECONDS
    jav_action_timeout_seconds: int = DEFAULT_JAV_ACTION_TIMEOUT_SECONDS
    enable_jav_resource_page: bool = True
    enable_jav_trailer: bool = True
    jav_trailer_ffmpeg_path: str = "ffmpeg"
    jav_trailer_convert_timeout_seconds: int = DEFAULT_JAV_TRAILER_CONVERT_TIMEOUT_SECONDS
    jav_trailer_max_bytes: int = DEFAULT_JAV_TRAILER_MAX_BYTES
    jav_trailer_cookie: str | None = None
    jav_trailer_impersonate: str = "random"
    enable_jav_stills: bool = False
    jav_stills_max_count: int = DEFAULT_JAV_STILLS_MAX_COUNT
    jav_stills_max_group_members: int = DEFAULT_MISSAV_MAX_GROUP_MEMBERS
    enable_jav_stills_pdf: bool = True
    jav_stills_pdf_max_images: int = DEFAULT_JAV_STILLS_PDF_MAX_IMAGES
    jav_stills_pdf_download_concurrency: int = DEFAULT_JAV_STILLS_PDF_DOWNLOAD_CONCURRENCY
    jav_stills_pdf_download_timeout_seconds: int = DEFAULT_JAV_STILLS_PDF_DOWNLOAD_TIMEOUT_SECONDS
    jav_stills_max_image_bytes: int = DEFAULT_JAV_STILLS_MAX_IMAGE_BYTES
    jav_stills_min_image_width: int = DEFAULT_JAV_STILLS_MIN_IMAGE_WIDTH
    jav_stills_min_image_height: int = DEFAULT_JAV_STILLS_MIN_IMAGE_HEIGHT
    enable_missav_link: bool = False
    missav_base_url: str = "https://missav.live"
    missav_allowed_group_ids: set[str] = field(default_factory=set)
    missav_max_group_members: int = DEFAULT_MISSAV_MAX_GROUP_MEMBERS
    manager_qq_ids: set[str] = field(default_factory=set)
    allowed_group_ids: set[str] = field(default_factory=set)
    health_check_interval_seconds: int = 60
    health_notify_group_ids: set[str] = field(default_factory=set)
    bot_display_name: str = "SanBot"
    manager_name: str = "管理者"
    manager_qq: str = "未配置"

    @classmethod
    def from_env(cls) -> "BotSettings":
        load_dotenv()
        bot_qq_id = os.getenv("BOT_QQ_ID")
        if not bot_qq_id:
            raise RuntimeError("BOT_QQ_ID is required")
        manager_qq_ids = _env_id_set("BOT_MANAGER_QQ_IDS")
        manager_qq = os.getenv("BOT_MANAGER_QQ") or (sorted(manager_qq_ids)[0] if manager_qq_ids else "未配置")
        return cls(
            bot_qq_id=bot_qq_id,
            napcat_ws_url=os.getenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001"),
            napcat_http_url=os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3000"),
            napcat_access_token=os.getenv("NAPCAT_ACCESS_TOKEN") or None,
            backend_url=os.getenv("BACKEND_URL", "http://127.0.0.1:8000"),
            backend_api_token=os.getenv("BACKEND_API_TOKEN") or None,
            data_dir=Path(os.getenv("DATA_DIR", "./data")),
            job_timeout_seconds=max(1, _env_int("JOB_TIMEOUT_SECONDS", 1800)),
            poll_interval_seconds=max(1, _env_int("JOB_POLL_INTERVAL_SECONDS", 5)),
            progress_notify_seconds=max(0, _env_int("JOB_PROGRESS_NOTIFY_SECONDS", 300)),
            confirm_timeout_seconds=max(30, _env_int("JOB_CONFIRM_TIMEOUT_SECONDS", 600)),
            large_album_warning_pages=max(0, _env_int("LARGE_ALBUM_WARNING_PAGES", 100)),
            max_album_pages=max(0, _env_int("MAX_ALBUM_PAGES", 300)),
            napcat_http_timeout_seconds=max(1, _env_int("NAPCAT_HTTP_TIMEOUT_SECONDS", 60)),
            napcat_upload_timeout_seconds=max(60, _env_int("NAPCAT_UPLOAD_TIMEOUT_SECONDS", 900)),
            max_upload_bytes=max(0, _env_int("NAPCAT_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)),
            max_upload_filename_bytes=max(
                32,
                _env_int("NAPCAT_MAX_UPLOAD_FILENAME_BYTES", MAX_UPLOAD_FILENAME_BYTES),
            ),
            upload_retries=max(1, _env_int("NAPCAT_UPLOAD_RETRIES", DEFAULT_UPLOAD_RETRIES)),
            enable_search=_env_bool("ENABLE_SEARCH", True),
            enable_javlibrary=_env_bool("ENABLE_JAVLIBRARY", True),
            search_result_limit=max(1, min(10, _env_int("SEARCH_RESULT_LIMIT", DEFAULT_SEARCH_RESULT_LIMIT))),
            search_confirm_timeout_seconds=max(30, _env_int("SEARCH_CONFIRM_TIMEOUT_SECONDS", 600)),
            ranking_result_limit=max(1, min(20, _env_int("RANKING_RESULT_LIMIT", DEFAULT_RANKING_RESULT_LIMIT))),
            user_command_cooldown_seconds=max(
                0,
                _env_int("USER_COMMAND_COOLDOWN_SECONDS", DEFAULT_USER_COMMAND_COOLDOWN_SECONDS),
            ),
            jav_action_timeout_seconds=max(30, _env_int("JAV_ACTION_TIMEOUT_SECONDS", DEFAULT_JAV_ACTION_TIMEOUT_SECONDS)),
            enable_jav_resource_page=_env_bool("ENABLE_JAV_RESOURCE_PAGE", True),
            enable_jav_trailer=_env_bool("ENABLE_JAV_TRAILER", True),
            jav_trailer_ffmpeg_path=os.getenv("JAV_TRAILER_FFMPEG_PATH") or "ffmpeg",
            jav_trailer_convert_timeout_seconds=max(
                10,
                _env_int("JAV_TRAILER_CONVERT_TIMEOUT_SECONDS", DEFAULT_JAV_TRAILER_CONVERT_TIMEOUT_SECONDS),
            ),
            jav_trailer_max_bytes=max(
                1 * 1024 * 1024,
                _env_int("JAV_TRAILER_MAX_BYTES", DEFAULT_JAV_TRAILER_MAX_BYTES),
            ),
            jav_trailer_cookie=os.getenv("JAV_TRAILER_COOKIE") or os.getenv("JAVLIBRARY_COOKIE") or None,
            jav_trailer_impersonate=(
                os.getenv("JAV_TRAILER_IMPERSONATE") or os.getenv("JAVLIBRARY_IMPERSONATE") or "random"
            ),
            enable_jav_stills=_env_bool("ENABLE_JAV_STILLS", False),
            jav_stills_max_count=max(1, min(6, _env_int("JAV_STILLS_MAX_COUNT", DEFAULT_JAV_STILLS_MAX_COUNT))),
            jav_stills_max_group_members=max(
                0,
                _env_int("JAV_STILLS_MAX_GROUP_MEMBERS", DEFAULT_MISSAV_MAX_GROUP_MEMBERS),
            ),
            enable_jav_stills_pdf=_env_bool("ENABLE_JAV_STILLS_PDF", True),
            jav_stills_pdf_max_images=max(
                0,
                _env_int("JAV_STILLS_PDF_MAX_IMAGES", DEFAULT_JAV_STILLS_PDF_MAX_IMAGES),
            ),
            jav_stills_pdf_download_concurrency=max(
                1,
                min(
                    8,
                    _env_int(
                        "JAV_STILLS_PDF_DOWNLOAD_CONCURRENCY",
                        DEFAULT_JAV_STILLS_PDF_DOWNLOAD_CONCURRENCY,
                    ),
                ),
            ),
            jav_stills_pdf_download_timeout_seconds=max(
                5,
                _env_int(
                    "JAV_STILLS_PDF_DOWNLOAD_TIMEOUT_SECONDS",
                    DEFAULT_JAV_STILLS_PDF_DOWNLOAD_TIMEOUT_SECONDS,
                ),
            ),
            jav_stills_max_image_bytes=max(
                256 * 1024,
                _env_int("JAV_STILLS_MAX_IMAGE_BYTES", DEFAULT_JAV_STILLS_MAX_IMAGE_BYTES),
            ),
            jav_stills_min_image_width=max(
                0,
                _env_int("JAV_STILLS_MIN_IMAGE_WIDTH", DEFAULT_JAV_STILLS_MIN_IMAGE_WIDTH),
            ),
            jav_stills_min_image_height=max(
                0,
                _env_int("JAV_STILLS_MIN_IMAGE_HEIGHT", DEFAULT_JAV_STILLS_MIN_IMAGE_HEIGHT),
            ),
            enable_missav_link=_env_bool("ENABLE_MISSAV_LINK", False),
            missav_base_url=(os.getenv("MISSAV_BASE_URL") or "https://missav.live").rstrip("/"),
            missav_allowed_group_ids=_env_id_set("MISSAV_ALLOWED_GROUP_IDS"),
            missav_max_group_members=max(0, _env_int("MISSAV_MAX_GROUP_MEMBERS", DEFAULT_MISSAV_MAX_GROUP_MEMBERS)),
            manager_qq_ids=manager_qq_ids,
            allowed_group_ids=_env_id_set("ALLOWED_GROUP_IDS") or _env_id_set("BOT_ALLOWED_GROUP_IDS"),
            health_check_interval_seconds=max(0, _env_int("HEALTH_CHECK_INTERVAL_SECONDS", 60)),
            health_notify_group_ids=_env_id_set("HEALTH_NOTIFY_GROUP_IDS"),
            bot_display_name=os.getenv("BOT_DISPLAY_NAME") or "SanBot",
            manager_name=os.getenv("BOT_MANAGER_NAME") or "管理者",
            manager_qq=manager_qq,
        )


@dataclass(frozen=True)
class PendingDownload:
    album_id: str
    title: str
    estimated_text: str
    page_count: int | None
    expires_at: float
    large_warning_sent: bool = False


@dataclass(frozen=True)
class PendingSearch:
    query: str
    results: list[dict[str, Any]]
    expires_at: float


@dataclass(frozen=True)
class PendingJavActions:
    code: str
    title: str
    payload: dict[str, Any]
    actions: set[str]
    expires_at: float


@dataclass(frozen=True)
class UploadingJob:
    job_id: str
    album_id: str
    group_id: str
    user_id: str
    started_at: float


@dataclass
class BotState:
    pending_downloads: dict[tuple[str, str], PendingDownload] = field(default_factory=dict)
    pending_searches: dict[tuple[str, str], PendingSearch] = field(default_factory=dict)
    pending_jav_actions: dict[tuple[str, str], PendingJavActions] = field(default_factory=dict)
    uploading_jobs: dict[str, UploadingJob] = field(default_factory=dict)
    cancelled_uploads: set[str] = field(default_factory=set)
    command_cooldowns: dict[tuple[str, str], float] = field(default_factory=dict)

    def cleanup(self, now: float) -> None:
        expired_downloads = [
            key
            for key, pending in self.pending_downloads.items()
            if pending.expires_at <= now
        ]
        for key in expired_downloads:
            self.pending_downloads.pop(key, None)
        expired_searches = [
            key
            for key, pending in self.pending_searches.items()
            if pending.expires_at <= now
        ]
        for key in expired_searches:
            self.pending_searches.pop(key, None)
        expired_jav_actions = [
            key
            for key, pending in self.pending_jav_actions.items()
            if pending.expires_at <= now
        ]
        for key in expired_jav_actions:
            self.pending_jav_actions.pop(key, None)


def _safe_filename(name: str, fallback: str, max_bytes: int = MAX_FILENAME_BYTES) -> str:
    cleaned = ILLEGAL_FILENAME_CHARS_RE.sub("_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned or fallback
    if len(cleaned.encode("utf-8")) <= max_bytes:
        return cleaned

    suffix = Path(cleaned).suffix
    stem = Path(cleaned).stem if suffix else cleaned
    suffix_bytes = len(suffix.encode("utf-8"))
    stem_budget = max(1, max_bytes - suffix_bytes)
    stem = stem.encode("utf-8")[:stem_budget].decode("utf-8", errors="ignore").strip(" .")
    if stem:
        return f"{stem}{suffix}"
    return fallback


def _confirm_words() -> set[str]:
    return lang_words("confirm_words", DEFAULT_CONFIRM_WORDS)


def _cancel_words() -> set[str]:
    return lang_words("cancel_words", DEFAULT_CANCEL_WORDS)


def _active_cancel_words() -> set[str]:
    return lang_words("active_cancel_words", DEFAULT_ACTIVE_CANCEL_WORDS)


def _parse_admin_command(event: dict[str, Any], bot_qq_id: str) -> AdminCommand | None:
    if not _has_at_bot(event, bot_qq_id):
        return None
    text = text_from_segments(event.get("message")).strip()
    normalized = re.sub(r"\s+", " ", text).strip()
    if normalized in {"状态", "status"}:
        return AdminCommand("status")
    if normalized in {"队列", "queue"}:
        return AdminCommand("queue")
    if normalized in {"审计", "审计日志", "操作日志", "audit"}:
        return AdminCommand("audit")
    if normalized in {"清理缓存", "清除缓存", "cleanup"}:
        return AdminCommand("cleanup")

    cancel_match = re.match(r"^(?:取消|cancel)\s+(.+)$", normalized, flags=re.I)
    if cancel_match:
        target = cancel_match.group(1).strip()
        if target and target not in _active_cancel_words():
            return AdminCommand("cancel", target=target)
    return None


def _has_at_bot(event: dict[str, Any], bot_qq_id: str) -> bool:
    for segment in normalize_message_segments(event.get("message")):
        if segment.get("type") != "at":
            continue
        data = segment.get("data") or {}
        if str(data.get("qq")) == str(bot_qq_id):
            return True
    return False


def _sender_role(event: dict[str, Any]) -> str:
    sender = event.get("sender")
    if not isinstance(sender, dict):
        return ""
    return str(sender.get("role") or "").lower()


def _is_manager(user_id: str, settings: BotSettings) -> bool:
    return str(user_id) in settings.manager_qq_ids


def _is_group_admin(event: dict[str, Any]) -> bool:
    return _sender_role(event) in GROUP_ADMIN_ROLES


def _can_run_admin_command(event: dict[str, Any], user_id: str, settings: BotSettings) -> bool:
    return _is_manager(user_id, settings) or _is_group_admin(event)


def _is_group_allowed(group_id: str, settings: BotSettings) -> bool:
    return not settings.allowed_group_ids or str(group_id) in settings.allowed_group_ids


async def handle_group_message(
    event: dict[str, Any],
    settings: BotSettings,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
    spawn_task: Callable[[Awaitable[None]], None],
) -> None:
    if event.get("message_type") != "group":
        return
    if str(event.get("user_id")) == str(settings.bot_qq_id):
        return

    group_id = str(event.get("group_id") or "")
    user_id = str(event.get("user_id") or "")
    if not group_id or not user_id:
        return

    segments = normalize_message_segments(event.get("message"))
    segment_types = [str(segment.get("type")) for segment in segments[:10]]
    at_targets = [
        str((segment.get("data") or {}).get("qq"))
        for segment in segments
        if segment.get("type") == "at"
    ]
    message_text = text_from_segments(event.get("message"))[:120]
    log_message = logger.info if at_targets or "JM" in message_text.upper() else logger.debug
    log_message(
        "Received group message group_id=%s user_id=%s segment_types=%s at_targets=%s bot_qq_id=%s text=%r",
        group_id,
        user_id,
        segment_types,
        at_targets,
        settings.bot_qq_id,
        message_text,
    )

    if not _is_group_allowed(group_id, settings):
        if str(settings.bot_qq_id) in at_targets:
            logger.warning("Blocked command from non-whitelisted group_id=%s user_id=%s.", group_id, user_id)
            await _record_command_audit(
                backend,
                group_id,
                user_id,
                "blocked_group",
                None,
                "blocked",
                "GROUP_NOT_ALLOWED",
                0,
            )
        return

    now = asyncio.get_running_loop().time()
    state.cleanup(now)

    admin_command = _parse_admin_command(event, settings.bot_qq_id)
    if admin_command is not None:
        started = time.monotonic()
        try:
            handled = await _handle_admin_command(
                event,
                group_id,
                user_id,
                settings,
                state,
                napcat,
                backend,
                admin_command,
            )
        except Exception as exc:
            await _record_command_audit(
                backend,
                group_id,
                user_id,
                f"admin:{admin_command.name}",
                admin_command.target,
                "failed",
                type(exc).__name__,
                _duration_ms(started),
            )
            raise
        await _record_command_audit(
            backend,
            group_id,
            user_id,
            f"admin:{admin_command.name}",
            admin_command.target,
            "handled",
            None,
            _duration_ms(started),
        )
        if handled:
            return

    pending_download = state.pending_downloads.get((group_id, user_id))
    pending_download_target = f"JM{pending_download.album_id}" if pending_download is not None else None
    started = time.monotonic()
    if await _handle_pending_confirmation(event, group_id, user_id, settings, state, napcat, backend, spawn_task):
        await _record_command_audit(
            backend,
            group_id,
            user_id,
            "confirm_download",
            pending_download_target,
            "handled",
            None,
            _duration_ms(started),
        )
        return

    pending_search = state.pending_searches.get((group_id, user_id))
    started = time.monotonic()
    if await _handle_pending_search_selection(event, group_id, user_id, settings, state, napcat, backend):
        await _record_command_audit(
            backend,
            group_id,
            user_id,
            "search_select",
            pending_search.query if pending_search is not None else None,
            "handled",
            None,
            _duration_ms(started),
        )
        return

    pending_jav_action = state.pending_jav_actions.get((group_id, user_id))
    started = time.monotonic()
    if await _handle_pending_jav_action(event, group_id, user_id, settings, state, napcat):
        await _record_command_audit(
            backend,
            group_id,
            user_id,
            "jav_action",
            pending_jav_action.code if pending_jav_action is not None else None,
            "handled",
            None,
            _duration_ms(started),
        )
        return

    started = time.monotonic()
    if await _handle_active_cancel(event, group_id, user_id, napcat, backend):
        await _record_command_audit(
            backend,
            group_id,
            user_id,
            "active_cancel",
            "active",
            "handled",
            None,
            _duration_ms(started),
        )
        return

    parse_result = parse_group_message(event, settings.bot_qq_id)
    if parse_result.action == ParseAction.IGNORE:
        if at_targets:
            logger.info(
                "Ignored group message because it did not match this bot or command: at_targets=%s bot_qq_id=%s text=%r",
                at_targets,
                settings.bot_qq_id,
                message_text,
            )
        return

    logger.info(
        "Parsed group command action=%s album_id=%s search_query=%s ranking_period=%s db_ranking_period=%s jav_code=%s group_id=%s user_id=%s",
        parse_result.action,
        parse_result.album_id,
        parse_result.search_query,
        parse_result.ranking_period,
        parse_result.db_ranking_period,
        parse_result.jav_code,
        group_id,
        user_id,
    )
    await _record_command_audit(
        backend,
        group_id,
        user_id,
        parse_result.action.value,
        _audit_target(parse_result),
        "received",
        None,
        0,
    )

    if parse_result.action == ParseAction.USAGE:
        await _safe_send(napcat, group_id, lang_text("usage"))
        return

    if parse_result.action == ParseAction.UNKNOWN:
        await _safe_send(napcat, group_id, lang_text("unknown_command"))
        return

    if parse_result.action == ParseAction.ERROR:
        await _safe_send(napcat, group_id, lang_text(parse_result.error_key or "usage"))
        return

    if parse_result.action == ParseAction.HOME:
        await _safe_send(napcat, group_id, _format_home_message(settings))
        return

    if parse_result.action == ParseAction.HELP:
        await _safe_send(napcat, group_id, lang_text("help_message"))
        return

    if parse_result.action == ParseAction.FEATURES:
        await _safe_send(napcat, group_id, lang_text("features_message"))
        return

    if parse_result.action == ParseAction.HISTORY:
        await _send_user_history(group_id, user_id, napcat, backend)
        return

    if parse_result.action == ParseAction.GROUP_HISTORY:
        if not _can_run_admin_command(event, user_id, settings):
            await _safe_send(napcat, group_id, lang_text("admin_permission_denied"))
            return
        await _send_group_history(group_id, napcat, backend)
        return

    if parse_result.action in {
        ParseAction.OK,
        ParseAction.SEARCH,
        ParseAction.RANKING,
        ParseAction.AV_SEARCH,
        ParseAction.ACTOR_SEARCH,
        ParseAction.DB_RANKING,
        ParseAction.JAV,
    }:
        remaining = _command_cooldown_remaining(group_id, user_id, settings, state, now)
        if remaining > 0:
            await _safe_send(napcat, group_id, lang_text("command_cooldown", seconds=math.ceil(remaining)))
            return
        _mark_command_cooldown(group_id, user_id, settings, state, now)

    if parse_result.action == ParseAction.SEARCH:
        await _handle_search_command(
            parse_result.search_query or "",
            group_id,
            user_id,
            settings,
            state,
            napcat,
            backend,
        )
        return

    if parse_result.action == ParseAction.AV_SEARCH:
        await _handle_av_search_command(
            parse_result.search_query or "",
            group_id,
            settings,
            napcat,
            backend,
        )
        return

    if parse_result.action == ParseAction.ACTOR_SEARCH:
        await _handle_actor_search_command(
            parse_result.search_query or "",
            group_id,
            settings,
            napcat,
            backend,
        )
        return

    if parse_result.action == ParseAction.RANKING:
        await _handle_ranking_command(
            parse_result.ranking_period or "day",
            group_id,
            settings,
            napcat,
            backend,
        )
        return

    if parse_result.action == ParseAction.DB_RANKING:
        await _handle_javdb_ranking_command(
            parse_result.db_ranking_period or "day",
            group_id,
            settings,
            napcat,
            backend,
        )
        return

    if parse_result.action == ParseAction.JAV:
        await _handle_jav_command(
            parse_result.jav_code or "",
            group_id,
            user_id,
            settings,
            state,
            napcat,
            backend,
        )
        return

    album_id = parse_result.album_id
    if album_id is None:
        await _safe_send(napcat, group_id, lang_text("usage"))
        return

    await _safe_send(napcat, group_id, lang_text("received_fetching", album_id=album_id))

    try:
        active = await backend.get_active_job(group_id, user_id)
    except BackendError as exc:
        logger.exception("Could not query active job for group=%s user=%s.", group_id, user_id)
        await _safe_send(napcat, group_id, lang_text("backend_unavailable", error_code=exc.error_code))
        return

    if active is not None:
        await _safe_send(
            napcat,
            group_id,
            lang_text("active_job_exists", album_id=active.get("album_id")),
        )
        return

    state.pending_searches.pop((group_id, user_id), None)
    await _send_album_preview(album_id, group_id, user_id, settings, state, napcat, backend)


async def _handle_admin_command(
    event: dict[str, Any],
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
    command: AdminCommand | None = None,
) -> bool:
    command = command or _parse_admin_command(event, settings.bot_qq_id)
    if command is None:
        return False

    if command.name == "cleanup":
        if not _is_manager(user_id, settings):
            await _safe_send(napcat, group_id, lang_text("admin_manager_required"))
            return True
    elif not _can_run_admin_command(event, user_id, settings):
        await _safe_send(napcat, group_id, lang_text("admin_permission_denied"))
        return True

    if command.name == "status":
        await _send_admin_status(group_id, state, napcat, backend)
    elif command.name == "queue":
        await _send_admin_queue(group_id, state, napcat, backend)
    elif command.name == "audit":
        await _send_admin_audit(group_id, napcat, backend)
    elif command.name == "cleanup":
        await _run_admin_cleanup(group_id, state, napcat, backend)
    elif command.name == "cancel":
        await _run_admin_cancel(group_id, command.target or "", state, napcat, backend)
    return True


async def _send_admin_status(
    group_id: str,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    try:
        payload = await backend.get_admin_status()
    except BackendError as exc:
        logger.exception("Could not fetch admin status.")
        await _safe_send(napcat, group_id, lang_text("admin_status_failed", error_code=exc.error_code))
        return

    await _safe_send(napcat, group_id, _format_admin_status(payload, len(state.uploading_jobs)))


async def _send_admin_queue(
    group_id: str,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    try:
        payload = await backend.get_admin_queue()
    except BackendError as exc:
        logger.exception("Could not fetch admin queue.")
        await _safe_send(napcat, group_id, lang_text("admin_queue_failed", error_code=exc.error_code))
        return

    jobs = payload.get("jobs")
    safe_jobs = [job for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else []
    await _safe_send(napcat, group_id, _format_admin_queue(_merge_uploading_jobs(safe_jobs, state)))


async def _send_admin_audit(
    group_id: str,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    try:
        payload = await backend.get_admin_audit(group_id=group_id, limit=10)
    except BackendError as exc:
        logger.exception("Could not fetch admin audit log.")
        await _safe_send(napcat, group_id, lang_text("admin_audit_failed", error_code=exc.error_code))
        return

    events = payload.get("events")
    safe_events = [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []
    await _safe_send(napcat, group_id, _format_admin_audit(safe_events))


def _format_home_message(settings: BotSettings) -> str:
    return lang_text(
        "bot_home",
        bot_name=settings.bot_display_name,
        manager_name=settings.manager_name,
        manager_qq=settings.manager_qq,
    )


async def _send_user_history(
    group_id: str,
    user_id: str,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    try:
        payload = await backend.get_user_history(group_id, user_id)
    except BackendError as exc:
        logger.exception("Could not fetch user history.")
        await _safe_send(napcat, group_id, lang_text("history_failed", error_code=exc.error_code))
        return

    jobs = payload.get("jobs") if isinstance(payload, dict) else []
    safe_jobs = [job for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else []
    await _safe_send(napcat, group_id, _format_history_jobs(safe_jobs, group_scope=False))


async def _send_group_history(
    group_id: str,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    try:
        payload = await backend.get_group_history(group_id)
    except BackendError as exc:
        logger.exception("Could not fetch group history.")
        await _safe_send(napcat, group_id, lang_text("history_failed", error_code=exc.error_code))
        return

    jobs = payload.get("jobs") if isinstance(payload, dict) else []
    safe_jobs = [job for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else []
    await _safe_send(napcat, group_id, _format_history_jobs(safe_jobs, group_scope=True))


async def _run_admin_cleanup(
    group_id: str,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    if state.uploading_jobs:
        await _safe_send(napcat, group_id, lang_text("admin_cleanup_busy", count=len(state.uploading_jobs)))
        return

    try:
        payload = await backend.cleanup_cache()
    except BackendError as exc:
        logger.exception("Could not cleanup cache.")
        await _safe_send(napcat, group_id, lang_text("admin_cleanup_failed", error=exc, error_code=exc.error_code))
        return

    stats = payload.get("stats") if isinstance(payload, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    await _safe_send(
        napcat,
        group_id,
        lang_text(
            "admin_cleanup_done",
            freed=_format_bytes(int(payload.get("freed_bytes") or 0)),
            job_dirs=int(stats.get("job_dirs") or 0),
            bot_downloads=int(stats.get("bot_downloads") or 0),
            previews=int(stats.get("previews") or 0),
        ),
    )


async def _run_admin_cancel(
    group_id: str,
    target: str,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    target = _normalize_cancel_target(target)
    if not target:
        await _safe_send(napcat, group_id, lang_text("admin_cancel_usage"))
        return

    uploading_job = _find_uploading_job(target, state)
    if uploading_job is not None:
        state.cancelled_uploads.add(uploading_job.job_id)
        await _safe_send(
            napcat,
            group_id,
            lang_text("admin_cancel_uploading", job_id=_short_job_id(uploading_job.job_id), album_id=uploading_job.album_id),
        )
        return

    try:
        payload = await backend.admin_cancel_job(target)
    except BackendError as exc:
        logger.exception("Could not cancel admin target %s.", target)
        await _safe_send(napcat, group_id, lang_text("admin_cancel_failed", target=target, error=exc, error_code=exc.error_code))
        return

    job = payload.get("job") if isinstance(payload, dict) else None
    if not isinstance(job, dict):
        await _safe_send(
            napcat,
            group_id,
            lang_text(
                "admin_cancel_failed",
                target=target,
                error=lang_text("bad_backend_response"),
                error_code="BAD_RESPONSE",
            ),
        )
        return

    await _safe_send(
        napcat,
        group_id,
        lang_text(
            "admin_cancel_done",
            job_id=_short_job_id(str(job.get("job_id") or target)),
            album_id=job.get("album_id"),
            status=_status_label(str(job.get("status") or "")),
        ),
    )


def _command_cooldown_remaining(
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    now: float,
) -> float:
    cooldown = settings.user_command_cooldown_seconds
    if cooldown <= 0:
        return 0.0
    key = (group_id, user_id)
    last_at = state.command_cooldowns.get(key)
    if last_at is None:
        return 0.0
    return max(0.0, cooldown - (now - last_at))


def _mark_command_cooldown(
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    now: float,
) -> None:
    if settings.user_command_cooldown_seconds <= 0:
        return
    state.command_cooldowns[(group_id, user_id)] = now


async def _handle_pending_confirmation(
    event: dict[str, Any],
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
    spawn_task: Callable[[Awaitable[None]], None],
) -> bool:
    key = (group_id, user_id)
    pending = state.pending_downloads.get(key)
    if pending is None:
        return False

    text = text_from_segments(event.get("message")).strip().lower()
    if not text:
        return False

    if text in _cancel_words():
        state.pending_downloads.pop(key, None)
        await _safe_send(napcat, group_id, lang_text("cancelled_pending", album_id=pending.album_id))
        return True

    if text not in _confirm_words():
        return False

    if _needs_large_album_confirmation(pending.page_count, settings) and not pending.large_warning_sent:
        state.pending_downloads[key] = replace(pending, large_warning_sent=True)
        await _safe_send(
            napcat,
            group_id,
            lang_text(
                "large_album_warning",
                album_id=pending.album_id,
                page_count=pending.page_count,
                limit=settings.large_album_warning_pages,
            ),
        )
        return True

    state.pending_downloads.pop(key, None)
    await _create_job_and_monitor(
        pending.album_id,
        group_id,
        user_id,
        settings,
        napcat,
        backend,
        spawn_task,
        state=state,
        page_count=pending.page_count,
        extra_message=lang_text("estimated_time_line", estimated_text=pending.estimated_text),
    )
    return True


async def _handle_pending_search_selection(
    event: dict[str, Any],
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
) -> bool:
    key = (group_id, user_id)
    pending = state.pending_searches.get(key)
    if pending is None:
        return False

    text = text_from_segments(event.get("message")).strip().lower()
    if not text:
        return False

    if text in _cancel_words():
        state.pending_searches.pop(key, None)
        await _safe_send(napcat, group_id, lang_text("search_cancelled"))
        return True

    if not text.isdigit():
        return False

    index = int(text)
    if index < 1 or index > len(pending.results):
        await _safe_send(napcat, group_id, lang_text("search_invalid_choice", count=len(pending.results)))
        return True

    selected = pending.results[index - 1]
    album_id = str(selected.get("album_id") or "")
    if not album_id.isdigit():
        state.pending_searches.pop(key, None)
        await _safe_send(
            napcat,
            group_id,
            lang_text("search_failed", error=lang_text("bad_search_result"), error_code="SEARCH_BAD_RESULT"),
        )
        return True

    try:
        active = await backend.get_active_job(group_id, user_id)
    except BackendError as exc:
        logger.exception("Could not query active job for group=%s user=%s.", group_id, user_id)
        await _safe_send(napcat, group_id, lang_text("backend_unavailable", error_code=exc.error_code))
        return True

    if active is not None:
        state.pending_searches.pop(key, None)
        await _safe_send(
            napcat,
            group_id,
            lang_text("active_job_exists", album_id=active.get("album_id")),
        )
        return True

    state.pending_searches.pop(key, None)
    await _safe_send(napcat, group_id, lang_text("search_selected", album_id=album_id))
    await _send_album_preview(album_id, group_id, user_id, settings, state, napcat, backend)
    return True


async def _handle_search_command(
    query: str,
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    if not settings.enable_search:
        await _safe_send(napcat, group_id, lang_text("search_disabled"))
        return

    query = re.sub(r"\s+", " ", query).strip()
    if not query:
        await _safe_send(napcat, group_id, lang_text("search_usage"))
        return

    await _safe_send(napcat, group_id, lang_text("searching", query=query))
    try:
        payload = await backend.search_albums(query, page=1, limit=settings.search_result_limit)
    except BackendError as exc:
        logger.exception("Could not search albums for group=%s user=%s.", group_id, user_id)
        await _safe_send(napcat, group_id, lang_text("search_failed", error=exc, error_code=exc.error_code))
        return

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        state.pending_searches.pop((group_id, user_id), None)
        await _safe_send(napcat, group_id, lang_text("search_empty", query=query))
        return

    safe_results = [
        result
        for result in results[: settings.search_result_limit]
        if isinstance(result, dict) and str(result.get("album_id") or "").isdigit()
    ]
    if not safe_results:
        state.pending_searches.pop((group_id, user_id), None)
        await _safe_send(napcat, group_id, lang_text("search_empty", query=query))
        return

    state.pending_downloads.pop((group_id, user_id), None)
    state.pending_searches[(group_id, user_id)] = PendingSearch(
        query=query,
        results=safe_results,
        expires_at=asyncio.get_running_loop().time() + settings.search_confirm_timeout_seconds,
    )
    await _safe_send(napcat, group_id, _format_search_results(query, safe_results))


async def _handle_ranking_command(
    period: str,
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    await _safe_send(napcat, group_id, lang_text("ranking_fetching", period=_ranking_period_label(period)))
    try:
        payload = await backend.get_ranking(period, page=1, limit=settings.ranking_result_limit)
    except BackendError as exc:
        logger.exception("Could not fetch %s ranking for group=%s.", period, group_id)
        await _safe_send(napcat, group_id, lang_text("ranking_failed", error=exc, error_code=exc.error_code))
        return

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        await _safe_send(napcat, group_id, lang_text("ranking_empty", period=_ranking_period_label(period)))
        return

    safe_results = [
        result
        for result in results[: settings.ranking_result_limit]
        if isinstance(result, dict) and str(result.get("album_id") or "").isdigit()
    ]
    if not safe_results:
        await _safe_send(napcat, group_id, lang_text("ranking_empty", period=_ranking_period_label(period)))
        return

    await _safe_send(napcat, group_id, _format_ranking_results(payload, safe_results))


async def _handle_av_search_command(
    query: str,
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    if not settings.enable_javlibrary:
        await _safe_send(napcat, group_id, lang_text("jav_disabled"))
        return

    query = re.sub(r"\s+", " ", query).strip()
    if not query:
        await _safe_send(napcat, group_id, lang_text("av_search_usage"))
        return

    await _safe_send(napcat, group_id, lang_text("av_searching", query=query))
    try:
        payload = await backend.search_jav_videos(query, page=1, limit=settings.search_result_limit)
    except BackendError as exc:
        logger.exception("Could not search JAV metadata for group=%s.", group_id)
        await _safe_send(napcat, group_id, lang_text("av_search_failed", error=exc, error_code=exc.error_code))
        return

    results = payload.get("results")
    safe_results = [result for result in results if isinstance(result, dict)] if isinstance(results, list) else []
    if not safe_results:
        await _safe_send(napcat, group_id, lang_text("av_search_empty", query=query))
        return
    await _safe_send(napcat, group_id, _format_jav_search_results(query, safe_results))


async def _handle_actor_search_command(
    query: str,
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    if not settings.enable_javlibrary:
        await _safe_send(napcat, group_id, lang_text("jav_disabled"))
        return

    query = re.sub(r"\s+", " ", query).strip()
    if not query:
        await _safe_send(napcat, group_id, lang_text("actor_search_usage"))
        return

    await _safe_send(napcat, group_id, lang_text("actor_searching", query=query))
    try:
        payload = await backend.search_jav_actors(query, page=1, limit=settings.search_result_limit)
    except BackendError as exc:
        logger.exception("Could not search JAV actor metadata for group=%s.", group_id)
        await _safe_send(napcat, group_id, lang_text("actor_search_failed", error=exc, error_code=exc.error_code))
        return

    results = payload.get("results")
    safe_results = [result for result in results if isinstance(result, dict)] if isinstance(results, list) else []
    if not safe_results:
        await _safe_send(napcat, group_id, lang_text("actor_search_empty", query=query))
        return
    await _safe_send(napcat, group_id, _format_jav_actor_search_results(query, safe_results))


async def _handle_javdb_ranking_command(
    period: str,
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    if not settings.enable_javlibrary:
        await _safe_send(napcat, group_id, lang_text("jav_disabled"))
        return

    await _safe_send(napcat, group_id, lang_text("db_ranking_fetching", period=_ranking_period_label(period)))
    try:
        payload = await backend.get_javdb_ranking(period, page=1, limit=settings.ranking_result_limit)
    except BackendError as exc:
        logger.exception("Could not fetch JavDB %s ranking for group=%s.", period, group_id)
        await _safe_send(napcat, group_id, lang_text("db_ranking_failed", error=exc, error_code=exc.error_code))
        return

    results = payload.get("results")
    safe_results = [result for result in results if isinstance(result, dict)] if isinstance(results, list) else []
    if not safe_results:
        await _safe_send(napcat, group_id, lang_text("db_ranking_empty", period=_ranking_period_label(period)))
        return
    await _safe_send(napcat, group_id, _format_javdb_ranking_results(payload, safe_results))


async def _handle_jav_command(
    code: str,
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    if not settings.enable_javlibrary:
        await _safe_send(napcat, group_id, lang_text("jav_disabled"))
        return

    await _safe_send(napcat, group_id, lang_text("jav_fetching", code=code.upper()))
    try:
        payload = await backend.get_jav_video(code)
    except BackendError as exc:
        logger.exception("Could not fetch JAV metadata for %s.", code)
        await _safe_send(napcat, group_id, lang_text("jav_failed", error=exc, error_code=exc.error_code))
        return

    await _safe_send(napcat, group_id, _format_jav_video(payload))
    actions = await _available_jav_actions(payload, group_id, settings, napcat)
    if actions:
        state.pending_jav_actions[(group_id, user_id)] = PendingJavActions(
            code=str(payload.get("code") or code),
            title=str(payload.get("title") or payload.get("code") or code),
            payload=payload,
            actions=actions,
            expires_at=asyncio.get_running_loop().time() + settings.jav_action_timeout_seconds,
        )
        await _safe_send(napcat, group_id, _format_jav_action_menu(actions))
    cover_url = payload.get("cover_url")
    if isinstance(cover_url, str) and cover_url:
        asyncio.create_task(
            _send_jav_cover(str(payload.get("code") or code), group_id, cover_url, napcat),
            name=f"jav-cover-{payload.get('code') or code}",
        )


async def _handle_pending_jav_action(
    event: dict[str, Any],
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    napcat: NapCatClient,
) -> bool:
    pending = state.pending_jav_actions.get((group_id, user_id))
    if pending is None:
        return False
    text = re.sub(r"\s+", "", text_from_segments(event.get("message"))).strip().lower()
    if not text:
        return False
    if text in _cancel_words():
        state.pending_jav_actions.pop((group_id, user_id), None)
        await _safe_send(napcat, group_id, lang_text("jav_action_cancelled"))
        return True

    action = _jav_action_from_text(text)
    if action is None:
        return False
    if action not in pending.actions:
        await _safe_send(napcat, group_id, lang_text("jav_action_unavailable"))
        return True

    if action == "resource":
        await _send_jav_resource_page(pending.payload, group_id, napcat)
    elif action == "trailer":
        await _send_jav_trailer(pending.payload, group_id, settings, napcat)
    elif action == "stills":
        await _send_jav_stills(pending.payload, group_id, settings, napcat)
    elif action == "stream":
        await _send_missav_link(pending.payload, group_id, settings, napcat)
    return True


async def _available_jav_actions(
    payload: dict[str, Any],
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
) -> set[str]:
    actions: set[str] = set()
    if settings.enable_jav_trailer and (
        _payload_str(payload, "trailer_url")
        or _payload_str(payload, "trailer_page_url")
        or bool(payload.get("trailer_requires_login"))
    ):
        actions.add("trailer")
    if settings.enable_jav_stills and _payload_list(payload, "preview_image_urls"):
        member_count = await _get_group_member_count(group_id, napcat)
        if (
            settings.jav_stills_max_group_members <= 0
            or (member_count is not None and member_count <= settings.jav_stills_max_group_members)
        ):
            actions.add("stills")
    if settings.enable_jav_resource_page and _jav_resource_page_url(payload):
        actions.add("resource")
    if await _is_missav_allowed(group_id, settings, napcat):
        actions.add("stream")
    return actions


def _format_jav_action_menu(actions: set[str]) -> str:
    lines = [lang_text("jav_action_menu_header")]
    if "trailer" in actions:
        lines.append(lang_text("jav_action_menu_trailer"))
    if "stills" in actions:
        lines.append(lang_text("jav_action_menu_stills"))
    if "resource" in actions:
        lines.append(lang_text("jav_action_menu_resource"))
    if "stream" in actions:
        lines.append(lang_text("jav_action_menu_stream"))
    lines.append(lang_text("jav_action_menu_footer"))
    return "\n".join(lines)


def _jav_action_from_text(text: str) -> str | None:
    if text in {"预告", "预告片", "trailer", "preview"}:
        return "trailer"
    if text in {"剧照", "截图", "样张", "预览图", "图片", "stills", "screenshots"}:
        return "stills"
    if text in {"资源", "资源页", "链接", "查看链接", "番号链接", "javdb", "javdb链接", "查看番号"}:
        return "resource"
    if text in {"在线播放", "播放", "在线", "在线入口", "missav", "missav入口"}:
        return "stream"
    return None


async def _send_jav_resource_page(payload: dict[str, Any], group_id: str, napcat: NapCatClient) -> None:
    url = _jav_resource_page_url(payload)
    if not url:
        await _safe_send(napcat, group_id, lang_text("jav_action_unavailable"))
        return
    await _safe_send(napcat, group_id, lang_text("jav_resource_page", url=url))


async def _send_jav_trailer(
    payload: dict[str, Any],
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
) -> None:
    trailer_url = _payload_str(payload, "trailer_url")
    if trailer_url:
        if _jav_trailer_needs_local_mp4(trailer_url):
            await _send_jav_trailer_as_local_mp4(payload, trailer_url, group_id, settings, napcat)
            return
        try:
            await napcat.send_group_video(group_id, trailer_url)
        except NapCatAPIError:
            logger.warning("Could not send JAV trailer URL directly; trying local MP4.", exc_info=True)
            await _send_jav_trailer_as_local_mp4(payload, trailer_url, group_id, settings, napcat)
        return

    trailer_page_url = _payload_str(payload, "trailer_page_url")
    if trailer_page_url:
        await _safe_send(napcat, group_id, lang_text("jav_trailer_page", url=trailer_page_url))
        return

    if payload.get("trailer_requires_login"):
        await _safe_send(napcat, group_id, lang_text("jav_trailer_requires_login"))
        return

    await _safe_send(napcat, group_id, lang_text("jav_action_unavailable"))


async def _send_jav_trailer_as_local_mp4(
    payload: dict[str, Any],
    trailer_url: str,
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
) -> None:
    code = _jav_payload_code(payload)
    job_id = f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', code).strip('._') or 'JAV'}-{uuid.uuid4().hex[:8]}"
    cache_root = (settings.data_dir.resolve() / "jav_trailers").resolve()
    dest_dir = (cache_root / job_id).resolve()
    if not dest_dir.is_relative_to(cache_root):
        logger.warning("Skip JAV trailer outside cache dir: %s", dest_dir)
        await _safe_send(napcat, group_id, lang_text("jav_trailer_failed", error_code="INVALID_CACHE_PATH"))
        return

    await _safe_send(napcat, group_id, lang_text("jav_trailer_preparing_mp4"))
    try:
        mp4_path = await _prepare_jav_trailer_mp4(payload, trailer_url, dest_dir, settings)
        await napcat.send_group_video(group_id, str(mp4_path.resolve()))
    except NapCatAPIError:
        logger.exception("Could not send JAV trailer MP4 for %s.", code)
        await _safe_send(napcat, group_id, lang_text("jav_trailer_failed", error_code="NAPCAT_VIDEO_UPLOAD_FAILED"))
    except JavTrailerError as exc:
        logger.exception("Could not prepare JAV trailer MP4 for %s.", code)
        await _safe_send(napcat, group_id, lang_text("jav_trailer_failed", error_code=exc.error_code))
    finally:
        await asyncio.to_thread(_cleanup_bot_download_dir, dest_dir, cache_root)


async def _prepare_jav_trailer_mp4(
    payload: dict[str, Any],
    trailer_url: str,
    dest_dir: Path,
    settings: BotSettings,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = (dest_dir / _jav_trailer_filename(payload)).resolve()
    if not mp4_path.is_relative_to(dest_dir):
        raise JavTrailerError("invalid trailer path", "INVALID_TRAILER_PATH")

    if _looks_like_mp4_url(trailer_url):
        await _download_jav_trailer_mp4(trailer_url, mp4_path, payload, settings)
    else:
        await _convert_jav_trailer_to_mp4(trailer_url, mp4_path, payload, settings)

    if not mp4_path.is_file() or mp4_path.stat().st_size <= 0:
        raise JavTrailerError("empty trailer mp4", "TRAILER_MP4_EMPTY")
    if mp4_path.stat().st_size > settings.jav_trailer_max_bytes:
        raise JavTrailerError("trailer mp4 is too large", "TRAILER_MP4_TOO_LARGE")
    return mp4_path


async def _download_jav_trailer_mp4(
    trailer_url: str,
    mp4_path: Path,
    payload: dict[str, Any],
    settings: BotSettings,
) -> None:
    tmp_path = mp4_path.with_name(f"{mp4_path.name}.tmp")
    tmp_path.unlink(missing_ok=True)
    headers = _jav_trailer_request_headers(payload, settings)
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers=headers,
            timeout=httpx.Timeout(settings.jav_trailer_convert_timeout_seconds),
        ) as client:
            async with client.stream("GET", trailer_url) as response:
                response.raise_for_status()
                size = 0
                with tmp_path.open("wb") as file:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > settings.jav_trailer_max_bytes:
                            raise JavTrailerError("trailer mp4 is too large", "TRAILER_MP4_TOO_LARGE")
                        file.write(chunk)
        if tmp_path.stat().st_size <= 0:
            raise JavTrailerError("empty trailer download", "TRAILER_MP4_EMPTY")
        tmp_path.replace(mp4_path)
    except JavTrailerError:
        tmp_path.unlink(missing_ok=True)
        raise
    except httpx.TimeoutException as exc:
        tmp_path.unlink(missing_ok=True)
        raise JavTrailerError("trailer mp4 download timed out", "TRAILER_MP4_TIMEOUT") from exc
    except httpx.HTTPError as exc:
        tmp_path.unlink(missing_ok=True)
        raise JavTrailerError("trailer mp4 download failed", "TRAILER_MP4_DOWNLOAD_FAILED") from exc


async def _convert_jav_trailer_to_mp4(
    trailer_url: str,
    mp4_path: Path,
    payload: dict[str, Any],
    settings: BotSettings,
) -> None:
    tmp_path = mp4_path.with_name(f"{mp4_path.name}.tmp.mp4")
    tmp_path.unlink(missing_ok=True)
    hls_dir = (mp4_path.parent / "hls").resolve()
    local_input = await asyncio.to_thread(
        _materialize_hls_playlist_sync,
        trailer_url,
        hls_dir,
        payload,
        settings,
    )
    copy_command = _ffmpeg_trailer_command(
        settings.jav_trailer_ffmpeg_path,
        str(local_input),
        tmp_path,
        "",
        transcode=False,
    )
    try:
        await _run_ffmpeg(copy_command, settings.jav_trailer_convert_timeout_seconds)
    except JavTrailerError:
        logger.info("Trailer remux failed; retrying with transcode.", exc_info=True)
        tmp_path.unlink(missing_ok=True)
        transcode_command = _ffmpeg_trailer_command(
            settings.jav_trailer_ffmpeg_path,
            str(local_input),
            tmp_path,
            "",
            transcode=True,
        )
        await _run_ffmpeg(transcode_command, settings.jav_trailer_convert_timeout_seconds)

    if not tmp_path.is_file() or tmp_path.stat().st_size <= 0:
        tmp_path.unlink(missing_ok=True)
        raise JavTrailerError("ffmpeg did not produce mp4", "TRAILER_MP4_EMPTY")
    tmp_path.replace(mp4_path)


def _materialize_hls_playlist_sync(
    trailer_url: str,
    hls_dir: Path,
    payload: dict[str, Any],
    settings: BotSettings,
) -> Path:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise JavTrailerError("curl-cffi is not installed", "CURL_CFFI_NOT_FOUND") from exc

    hls_dir = hls_dir.resolve()
    _reset_hls_dir(hls_dir)

    headers = _jav_trailer_request_headers(payload, settings)
    session = curl_requests.Session(
        impersonate=_choose_jav_trailer_impersonate(settings.jav_trailer_impersonate),
        headers=headers,
    )
    try:
        fetcher = _HlsAssetFetcher(
            session=session,
            exceptions=curl_requests.exceptions,
            timeout_seconds=settings.jav_trailer_convert_timeout_seconds,
            max_bytes=settings.jav_trailer_max_bytes,
        )
        playlist_url = trailer_url
        playlist_text = fetcher.fetch_text(playlist_url)
        variant_urls = _hls_variant_urls(playlist_text, playlist_url)
        playlist_candidates = variant_urls or [playlist_url]
        last_error: JavTrailerError | None = None
        for candidate_url in playlist_candidates:
            _reset_hls_dir(hls_dir)
            fetcher.total_bytes = 0
            try:
                candidate_text = fetcher.fetch_text(candidate_url) if variant_urls else playlist_text
                local_playlist = _rewrite_hls_playlist_to_local(
                    candidate_text,
                    candidate_url,
                    hls_dir,
                    fetcher,
                )
                break
            except JavTrailerError as exc:
                if not variant_urls or exc.error_code not in {
                    "TRAILER_HLS_DOWNLOAD_FAILED",
                    "TRAILER_HLS_ASSET_NOT_FOUND",
                    "TRAILER_HLS_EMPTY",
                    "TRAILER_HLS_INVALID",
                    "TRAILER_HLS_SEGMENTS_MISSING",
                }:
                    raise
                last_error = exc
                logger.warning("JAV trailer HLS variant failed; trying the next variant. error_code=%s", exc.error_code)
        else:
            if last_error:
                raise last_error
            raise JavTrailerError("empty HLS playlist", "TRAILER_HLS_EMPTY")
    finally:
        session.close()

    if not local_playlist.is_file() or local_playlist.stat().st_size <= 0:
        raise JavTrailerError("empty HLS playlist", "TRAILER_HLS_EMPTY")
    return local_playlist


def _reset_hls_dir(hls_dir: Path) -> None:
    shutil.rmtree(hls_dir, ignore_errors=True)
    hls_dir.mkdir(parents=True, exist_ok=True)


class _HlsAssetFetcher:
    def __init__(
        self,
        *,
        session: Any,
        exceptions: Any,
        timeout_seconds: int,
        max_bytes: int,
    ) -> None:
        self.session = session
        self.exceptions = exceptions
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.total_bytes = 0

    def fetch_text(self, url: str) -> str:
        content = self.fetch_bytes(url, count_toward_limit=False)
        text = content.decode("utf-8", errors="replace")
        if "#EXTM3U" not in text[:1000]:
            raise JavTrailerError("invalid HLS playlist", "TRAILER_HLS_INVALID")
        return text

    def fetch_bytes(self, url: str, *, count_toward_limit: bool = True) -> bytes:
        last_timeout: BaseException | None = None
        last_request_error: BaseException | None = None
        response = None
        for attempt in range(DEFAULT_JAV_TRAILER_HLS_ASSET_RETRIES):
            try:
                response = self.session.get(url, timeout=self.timeout_seconds, allow_redirects=True)
            except self.exceptions.Timeout as exc:
                last_timeout = exc
                if attempt < DEFAULT_JAV_TRAILER_HLS_ASSET_RETRIES - 1:
                    self._sleep_before_retry(attempt)
                    continue
                raise JavTrailerError("HLS request timed out", "TRAILER_MP4_TIMEOUT") from exc
            except self.exceptions.RequestException as exc:
                last_request_error = exc
                if attempt < DEFAULT_JAV_TRAILER_HLS_ASSET_RETRIES - 1:
                    self._sleep_before_retry(attempt)
                    continue
                raise JavTrailerError("HLS request failed", "TRAILER_HLS_DOWNLOAD_FAILED") from exc

            if response.status_code in {429, 500, 502, 503, 504} and attempt < DEFAULT_JAV_TRAILER_HLS_ASSET_RETRIES - 1:
                self._sleep_before_retry(attempt)
                continue
            break

        if response is None:
            if last_timeout is not None:
                raise JavTrailerError("HLS request timed out", "TRAILER_MP4_TIMEOUT") from last_timeout
            raise JavTrailerError("HLS request failed", "TRAILER_HLS_DOWNLOAD_FAILED") from last_request_error

        if response.status_code == 404:
            logger.warning("JAV trailer HLS asset returned HTTP 404.")
            raise JavTrailerError("HLS asset not found", "TRAILER_HLS_ASSET_NOT_FOUND")
        if response.status_code >= 400:
            logger.warning("JAV trailer HLS request failed with HTTP %s.", response.status_code)
            raise JavTrailerError("HLS request rejected", "TRAILER_HLS_DOWNLOAD_FAILED")

        content = response.content or b""
        if count_toward_limit:
            self.total_bytes += len(content)
            if self.total_bytes > self.max_bytes:
                raise JavTrailerError("trailer HLS assets are too large", "TRAILER_MP4_TOO_LARGE")
        return content

    @staticmethod
    def _sleep_before_retry(attempt: int) -> None:
        time.sleep(0.5 * (attempt + 1))


def _rewrite_hls_playlist_to_local(
    playlist_text: str,
    playlist_url: str,
    hls_dir: Path,
    fetcher: _HlsAssetFetcher,
) -> Path:
    local_playlist = (hls_dir / "playlist.m3u8").resolve()
    if not local_playlist.is_relative_to(hls_dir):
        raise JavTrailerError("invalid HLS path", "INVALID_TRAILER_PATH")

    lines = playlist_text.splitlines()
    rewritten: list[str] = []
    pending_segment_tags: list[str] = []
    seen_segments = 0
    saved_segments = 0
    skipped_segments = 0
    key_index = 0
    map_index = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if pending_segment_tags:
                pending_segment_tags.append(line)
            else:
                rewritten.append(line)
            continue
        if stripped.startswith("#EXT-X-KEY"):
            uri = _extract_hls_uri_attribute(stripped)
            if uri and not uri.lower().startswith("data:"):
                local_name = f"key_{key_index:03d}{_hls_local_extension(uri, '.key')}"
                key_index += 1
                _write_hls_asset(_resolve_hls_asset_url(playlist_url, uri), hls_dir / local_name, hls_dir, fetcher)
                rewritten.append(_replace_hls_uri_attribute(line, local_name))
            else:
                rewritten.append(line)
            continue
        if stripped.startswith("#EXT-X-MAP"):
            uri = _extract_hls_uri_attribute(stripped)
            if uri and not uri.lower().startswith("data:"):
                local_name = f"map_{map_index:03d}{_hls_local_extension(uri, '.mp4')}"
                map_index += 1
                _write_hls_asset(_resolve_hls_asset_url(playlist_url, uri), hls_dir / local_name, hls_dir, fetcher)
                rewritten.append(_replace_hls_uri_attribute(line, local_name))
            else:
                rewritten.append(line)
            continue
        if stripped.startswith("#"):
            if _is_hls_segment_tag(stripped):
                pending_segment_tags.append(line)
            else:
                rewritten.append(line)
            continue

        seen_segments += 1
        local_name = f"segment_{saved_segments:05d}{_hls_local_extension(stripped, '.ts')}"
        try:
            _write_hls_asset(_resolve_hls_asset_url(playlist_url, stripped), hls_dir / local_name, hls_dir, fetcher)
        except JavTrailerError as exc:
            if exc.error_code not in {"TRAILER_HLS_ASSET_NOT_FOUND", "TRAILER_HLS_DOWNLOAD_FAILED"}:
                raise
            skipped_segments += 1
            pending_segment_tags = []
            logger.warning(
                "Skipping unavailable JAV trailer HLS segment. error_code=%s skipped=%s seen=%s",
                exc.error_code,
                skipped_segments,
                seen_segments,
            )
            continue
        rewritten.extend(pending_segment_tags)
        pending_segment_tags = []
        rewritten.append(local_name)
        saved_segments += 1

    if saved_segments == 0:
        raise JavTrailerError("HLS playlist has no segments", "TRAILER_HLS_EMPTY")
    if skipped_segments:
        logger.warning(
            "JAV trailer HLS playlist has unavailable segments. saved=%s skipped=%s seen=%s",
            saved_segments,
            skipped_segments,
            seen_segments,
        )

    local_playlist.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return local_playlist


def _write_hls_asset(asset_url: str, asset_path: Path, hls_dir: Path, fetcher: _HlsAssetFetcher) -> None:
    asset_path = asset_path.resolve()
    if not asset_path.is_relative_to(hls_dir):
        raise JavTrailerError("invalid HLS asset path", "INVALID_TRAILER_PATH")
    asset_path.write_bytes(fetcher.fetch_bytes(asset_url))


def _is_hls_segment_tag(line: str) -> bool:
    return line.startswith(
        (
            "#EXTINF",
            "#EXT-X-BYTERANGE",
            "#EXT-X-PROGRAM-DATE-TIME",
            "#EXT-X-DISCONTINUITY",
            "#EXT-X-GAP",
        )
    )


def _select_hls_variant_url(playlist_text: str, playlist_url: str) -> str | None:
    urls = _hls_variant_urls(playlist_text, playlist_url)
    return urls[0] if urls else None


def _hls_variant_urls(playlist_text: str, playlist_url: str) -> list[str]:
    lines = playlist_text.splitlines()
    candidates: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#EXT-X-STREAM-INF"):
            continue
        bandwidth = _hls_stream_bandwidth(stripped)
        for next_line in lines[index + 1 :]:
            uri = next_line.strip()
            if not uri or uri.startswith("#"):
                continue
            candidates.append((bandwidth, urljoin(playlist_url, uri)))
            break
    if not candidates:
        return []
    return [url for _bandwidth, url in sorted(candidates, key=lambda item: item[0], reverse=True)]


def _resolve_hls_asset_url(playlist_url: str, uri: str) -> str:
    resolved = urljoin(playlist_url, uri)
    base_parts = urlsplit(playlist_url)
    resolved_parts = urlsplit(resolved)
    if (
        base_parts.query
        and not resolved_parts.query
        and resolved_parts.scheme in {"http", "https"}
        and resolved_parts.netloc == base_parts.netloc
    ):
        return urlunsplit(
            (
                resolved_parts.scheme,
                resolved_parts.netloc,
                resolved_parts.path,
                base_parts.query,
                resolved_parts.fragment,
            )
        )
    return resolved


def _hls_stream_bandwidth(line: str) -> int:
    match = re.search(r"\bBANDWIDTH=(\d+)", line)
    if not match:
        return 0
    return int(match.group(1))


def _extract_hls_uri_attribute(line: str) -> str | None:
    match = re.search(r'URI="([^"]+)"', line)
    if match:
        return match.group(1)
    match = re.search(r"URI=([^,]+)", line)
    if match:
        return match.group(1).strip().strip("'\"")
    return None


def _replace_hls_uri_attribute(line: str, local_name: str) -> str:
    if re.search(r'URI="[^"]+"', line):
        return re.sub(r'URI="[^"]+"', f'URI="{local_name}"', line, count=1)
    return re.sub(r"URI=([^,]+)", f'URI="{local_name}"', line, count=1)


def _hls_local_extension(uri: str, fallback: str) -> str:
    suffix = Path(urlsplit(uri).path).suffix.lower()
    if 1 < len(suffix) <= 8 and re.fullmatch(r"\.[a-z0-9]+", suffix):
        return suffix
    return fallback


def _choose_jav_trailer_impersonate(value: str | None) -> str:
    if value is None or value.strip().lower() in {"", "random"}:
        return random.choice(DEFAULT_JAV_TRAILER_IMPERSONATES)
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        return random.choice(DEFAULT_JAV_TRAILER_IMPERSONATES)
    return random.choice(items)


def _ffmpeg_trailer_command(
    ffmpeg_path: str,
    trailer_url: str,
    output_path: Path,
    headers: str,
    *,
    transcode: bool,
) -> list[str]:
    command = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error"]
    if headers:
        command.extend(["-headers", headers])
    command.extend(["-protocol_whitelist", "file,http,https,tcp,tls,crypto", "-allowed_extensions", "ALL"])
    if urlsplit(trailer_url).scheme in {"http", "https"}:
        command.extend(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"])
    command.extend(["-i", trailer_url, "-map", "0:v:0?", "-map", "0:a:0?"])
    if transcode:
        command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k"])
    else:
        command.extend(["-c", "copy"])
    command.extend(["-movflags", "+faststart", str(output_path)])
    return command


async def _run_ffmpeg(command: list[str], timeout_seconds: int) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise JavTrailerError("ffmpeg is not installed", "FFMPEG_NOT_FOUND") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise JavTrailerError("ffmpeg timed out", "TRAILER_MP4_TIMEOUT") from exc

    if process.returncode != 0:
        message = _sanitize_ffmpeg_message((stderr or stdout).decode("utf-8", errors="replace")[-500:])
        logger.warning("ffmpeg failed: %s", message)
        raise JavTrailerError("ffmpeg failed", "FFMPEG_CONVERT_FAILED")


def _sanitize_ffmpeg_message(message: str) -> str:
    return re.sub(r"https?://\S+", "<url>", message)


def _jav_trailer_request_headers(payload: dict[str, Any], settings: BotSettings) -> dict[str, str]:
    referer = _jav_resource_page_url(payload) or _payload_str(payload, "url") or "https://javdb.com/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
        "Origin": "https://javdb.com",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }
    if settings.jav_trailer_cookie:
        headers["Cookie"] = settings.jav_trailer_cookie
    return headers


def _jav_trailer_needs_local_mp4(trailer_url: str) -> bool:
    clean_path = urlsplit(trailer_url).path.lower()
    return clean_path.endswith(".m3u8") or not clean_path.endswith(".mp4")


def _looks_like_mp4_url(trailer_url: str) -> bool:
    return urlsplit(trailer_url).path.lower().endswith(".mp4")


def _jav_trailer_filename(payload: dict[str, Any]) -> str:
    code = _jav_payload_code(payload)
    return _safe_filename(f"[{code}] 预告片.mp4", f"[{code}] trailer.mp4", max_bytes=MAX_UPLOAD_FILENAME_BYTES)


async def _send_jav_stills(
    payload: dict[str, Any],
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
) -> None:
    all_urls = _payload_list(payload, "preview_image_urls")
    preview_urls = all_urls[: settings.jav_stills_max_count]
    if not preview_urls:
        await _safe_send(napcat, group_id, lang_text("jav_action_unavailable"))
        return
    await _safe_send(napcat, group_id, lang_text("jav_stills_sending", count=len(preview_urls)))
    sent_count = 0
    for url in preview_urls:
        try:
            await napcat.send_group_image(group_id, url)
            sent_count += 1
        except NapCatAPIError:
            logger.warning("Could not send JAV still image %s.", url, exc_info=True)
    if sent_count == 0:
        await _safe_send(napcat, group_id, lang_text("jav_stills_failed"))
    if settings.enable_jav_stills_pdf:
        await _send_jav_stills_pdf(payload, all_urls, group_id, settings, napcat)


async def _send_jav_stills_pdf(
    payload: dict[str, Any],
    urls: list[str],
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
) -> None:
    selected_urls = urls if settings.jav_stills_pdf_max_images <= 0 else urls[: settings.jav_stills_pdf_max_images]
    if not selected_urls:
        return

    code = _jav_payload_code(payload)
    job_id = f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', code).strip('._') or 'JAV'}-{uuid.uuid4().hex[:8]}"
    cache_root = (settings.data_dir.resolve() / "jav_stills").resolve()
    dest_dir = (cache_root / job_id).resolve()
    if not dest_dir.is_relative_to(cache_root):
        logger.warning("Skip JAV stills PDF outside cache dir: %s", dest_dir)
        await _safe_send(napcat, group_id, lang_text("jav_stills_pdf_failed", error_code="INVALID_CACHE_PATH"))
        return

    if len(urls) > len(selected_urls):
        await _safe_send(
            napcat,
            group_id,
            lang_text(
                "jav_stills_pdf_preparing_limited",
                count=len(selected_urls),
                total=len(urls),
            ),
        )
    else:
        await _safe_send(napcat, group_id, lang_text("jav_stills_pdf_preparing", count=len(selected_urls)))

    try:
        pdf_path, upload_filename, image_count = await _build_jav_stills_pdf(
            payload,
            selected_urls,
            dest_dir,
            settings,
        )
        upload_files = await asyncio.to_thread(
            _prepare_upload_files,
            pdf_path,
            upload_filename,
            settings.max_upload_bytes,
            settings.max_upload_filename_bytes,
            None,
        )
        if len(upload_files) > 1:
            await _safe_send(
                napcat,
                group_id,
                lang_text("jav_stills_pdf_split", count=len(upload_files)),
            )

        for upload_path, upload_name in upload_files:
            if not await _upload_with_retries(
                napcat,
                group_id,
                upload_path,
                upload_name,
                job_id,
                settings.upload_retries,
            ):
                await _safe_send(napcat, group_id, lang_text("jav_stills_pdf_failed", error_code="NAPCAT_UPLOAD_FAILED"))
                return
        await _safe_send(
            napcat,
            group_id,
            lang_text("jav_stills_pdf_completed", count=image_count, filename=upload_filename),
        )
    except UploadPreparationError:
        logger.exception("Could not prepare JAV stills PDF upload for %s.", code)
        await _safe_send(napcat, group_id, lang_text("jav_stills_pdf_failed", error_code="PDF_UPLOAD_PREPARE_FAILED"))
    except JavStillsPdfError:
        logger.exception("Could not build JAV stills PDF for %s.", code)
        await _safe_send(napcat, group_id, lang_text("jav_stills_pdf_failed", error_code="JAV_STILLS_PDF_FAILED"))
    finally:
        await asyncio.to_thread(_cleanup_bot_download_dir, dest_dir, cache_root)


async def _build_jav_stills_pdf(
    payload: dict[str, Any],
    urls: list[str],
    dest_dir: Path,
    settings: BotSettings,
) -> tuple[Path, str, int]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    images_dir = (dest_dir / "images").resolve()
    if not images_dir.is_relative_to(dest_dir):
        raise JavStillsPdfError("invalid image cache path")
    images_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(settings.jav_stills_pdf_download_concurrency)
    timeout = httpx.Timeout(settings.jav_stills_pdf_download_timeout_seconds)
    limits = httpx.Limits(
        max_connections=settings.jav_stills_pdf_download_concurrency,
        max_keepalive_connections=settings.jav_stills_pdf_download_concurrency,
    )
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_jav_stills_request_headers(payload),
        timeout=timeout,
        limits=limits,
    ) as client:
        image_paths = await asyncio.gather(
            *[
                _download_jav_still_image(
                    client,
                    url,
                    images_dir,
                    index,
                    semaphore,
                    settings.jav_stills_max_image_bytes,
                    settings.jav_stills_min_image_width,
                    settings.jav_stills_min_image_height,
                )
                for index, url in enumerate(urls, start=1)
            ]
        )

    usable_paths = [path for path in image_paths if path is not None]
    if not usable_paths:
        raise JavStillsPdfError("no usable still images")

    pdf_filename = _jav_stills_pdf_filename(payload, MAX_FILENAME_BYTES)
    pdf_path = (dest_dir / pdf_filename).resolve()
    if not pdf_path.is_relative_to(dest_dir):
        raise JavStillsPdfError("invalid pdf path")

    await asyncio.to_thread(_convert_jav_stills_to_pdf, usable_paths, pdf_path)
    if not pdf_path.is_file() or pdf_path.stat().st_size <= 0:
        raise JavStillsPdfError("empty pdf")

    upload_filename = _safe_filename(
        pdf_path.name,
        f"[{_jav_payload_code(payload)}] 剧照.pdf",
        max_bytes=settings.max_upload_filename_bytes,
    )
    return pdf_path, upload_filename, len(usable_paths)


async def _download_jav_still_image(
    client: httpx.AsyncClient,
    image_url: str,
    images_dir: Path,
    index: int,
    semaphore: asyncio.Semaphore,
    max_bytes: int,
    min_width: int,
    min_height: int,
) -> Path | None:
    tmp_path: Path | None = None
    async with semaphore:
        try:
            parsed = urlsplit(image_url)
            if parsed.scheme not in {"http", "https"}:
                raise JavStillsPdfError("unsupported image url scheme")
            async with client.stream("GET", image_url) as response:
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                if content_type and not (
                    content_type.startswith("image/")
                    or content_type in {"application/octet-stream", "binary/octet-stream"}
                ):
                    raise JavStillsPdfError(f"unexpected content type: {content_type}")

                extension = _jav_stills_image_extension(content_type, image_url)
                image_path = (images_dir / f"{index:03d}{extension}").resolve()
                if not image_path.is_relative_to(images_dir):
                    raise JavStillsPdfError("invalid image path")
                tmp_path = image_path.with_name(f"{image_path.name}.tmp")

                size = 0
                with tmp_path.open("wb") as file:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise JavStillsPdfError("still image is too large")
                        file.write(chunk)

            if tmp_path is None or not tmp_path.is_file() or tmp_path.stat().st_size <= 0:
                raise JavStillsPdfError("empty still image")
            if not _is_jav_still_large_enough(tmp_path, min_width, min_height):
                logger.info("Skip tiny JAV still image %s.", image_url)
                tmp_path.unlink(missing_ok=True)
                return None
            tmp_path.replace(image_path)
            return image_path
        except Exception:
            logger.warning("Could not download JAV still image %s.", image_url, exc_info=True)
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            return None


def _is_jav_still_large_enough(image_path: Path, min_width: int, min_height: int) -> bool:
    if min_width <= 0 and min_height <= 0:
        return True
    try:
        from PIL import Image
    except ImportError:
        logger.debug("Pillow is unavailable; skip JAV still dimension check.")
        return True

    try:
        with Image.open(image_path) as image:
            width, height = image.size
    except Exception:
        logger.debug("Could not inspect JAV still image dimensions: %s", image_path, exc_info=True)
        return True

    return (min_width <= 0 or width >= min_width) and (min_height <= 0 or height >= min_height)


def _jav_stills_request_headers(payload: dict[str, Any]) -> dict[str, str]:
    referer = _jav_resource_page_url(payload) or _payload_str(payload, "url") or "https://javdb.com/"
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": referer,
    }


def _jav_stills_image_extension(content_type: str, image_url: str) -> str:
    content_type_extensions = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/tiff": ".tif",
        "image/avif": ".avif",
    }
    if content_type in content_type_extensions:
        return content_type_extensions[content_type]

    suffix = Path(urlsplit(image_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".avif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def _jav_stills_pdf_filename(payload: dict[str, Any], max_bytes: int) -> str:
    code = _jav_payload_code(payload)
    title = str(payload.get("title") or "").strip()
    if title.upper().startswith(code.upper()):
        title = title[len(code):].strip(" -_")
    raw_name = f"[{code}] {title} 剧照.pdf" if title else f"[{code}] 剧照.pdf"
    return _safe_filename(raw_name, f"[{code}] 剧照.pdf", max_bytes=max_bytes)


def _jav_payload_code(payload: dict[str, Any]) -> str:
    code = str(payload.get("code") or "JAV").strip().upper()
    return re.sub(r"[^A-Z0-9_-]+", "_", code).strip("_") or "JAV"


def _convert_jav_stills_to_pdf(image_paths: list[Path], pdf_path: Path) -> None:
    try:
        import img2pdf
    except ImportError as exc:
        raise JavStillsPdfError("missing img2pdf") from exc

    try:
        pdf_path.write_bytes(img2pdf.convert([str(path) for path in image_paths]))
        return
    except Exception:
        logger.info("Direct img2pdf conversion failed for JAV stills; retrying through Pillow.", exc_info=True)

    converted_paths = _convert_jav_stills_with_pillow(image_paths, pdf_path.parent / "converted")
    if not converted_paths:
        raise JavStillsPdfError("no converted still images")
    try:
        pdf_path.write_bytes(img2pdf.convert([str(path) for path in converted_paths]))
    except Exception as exc:
        raise JavStillsPdfError("img2pdf conversion failed") from exc


def _convert_jav_stills_with_pillow(image_paths: list[Path], converted_dir: Path) -> list[Path]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise JavStillsPdfError("missing Pillow") from exc

    converted_dir.mkdir(parents=True, exist_ok=True)
    converted_paths: list[Path] = []
    for index, image_path in enumerate(image_paths, start=1):
        try:
            with Image.open(image_path) as image:
                if getattr(image, "is_animated", False):
                    image.seek(0)
                image = ImageOps.exif_transpose(image)
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")

                converted_path = (converted_dir / f"{index:03d}.jpg").resolve()
                if not converted_path.is_relative_to(converted_dir.resolve()):
                    raise JavStillsPdfError("invalid converted image path")
                image.save(converted_path, "JPEG", quality=92)
                converted_paths.append(converted_path)
        except Exception:
            logger.warning("Could not convert JAV still image %s with Pillow.", image_path, exc_info=True)
    return converted_paths


async def _send_missav_link(
    payload: dict[str, Any],
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
) -> None:
    code = str(payload.get("code") or "").strip().upper()
    if not code:
        await _safe_send(napcat, group_id, lang_text("jav_action_unavailable"))
        return
    url = f"{settings.missav_base_url}/{quote(code)}"
    await _safe_send(napcat, group_id, lang_text("missav_link", code=code, url=url))


async def _is_missav_allowed(group_id: str, settings: BotSettings, napcat: NapCatClient) -> bool:
    if not settings.enable_missav_link:
        return False
    if not settings.missav_allowed_group_ids or str(group_id) not in settings.missav_allowed_group_ids:
        return False
    if settings.missav_max_group_members <= 0:
        return True
    member_count = await _get_group_member_count(group_id, napcat)
    return member_count is not None and member_count <= settings.missav_max_group_members


async def _get_group_member_count(group_id: str, napcat: NapCatClient) -> int | None:
    try:
        payload = await napcat.get_group_info(group_id)
    except NapCatAPIError:
        logger.warning("Could not get group member count for MissAV visibility check.", exc_info=True)
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    count = data.get("member_count")
    try:
        return int(count)
    except (TypeError, ValueError):
        return None


def _jav_resource_page_url(payload: dict[str, Any]) -> str:
    resource_url = _payload_str(payload, "resource_page_url")
    if resource_url:
        return resource_url
    source = str(payload.get("source") or "").lower()
    url = _payload_str(payload, "url")
    return url if source == "javdb" else ""


def _payload_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else ""


def _payload_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


async def _handle_active_cancel(
    event: dict[str, Any],
    group_id: str,
    user_id: str,
    napcat: NapCatClient,
    backend: BackendClient,
) -> bool:
    text = text_from_segments(event.get("message")).strip().lower()
    if text not in _active_cancel_words():
        return False

    try:
        cancelled = await backend.cancel_active_job(group_id, user_id)
    except BackendError as exc:
        logger.exception("Could not cancel active job for group=%s user=%s.", group_id, user_id)
        await _safe_send(napcat, group_id, lang_text("cancel_failed", error_code=exc.error_code))
        return True

    if cancelled is None:
        await _safe_send(napcat, group_id, lang_text("no_active_job"))
        return True

    await _safe_send(napcat, group_id, lang_text("cancelled_active", album_id=cancelled.get("album_id")))
    return True


async def _send_album_preview(
    album_id: str,
    group_id: str,
    user_id: str,
    settings: BotSettings,
    state: BotState,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    try:
        preview = await backend.get_album_preview(album_id)
    except BackendError as exc:
        logger.exception("Could not fetch album preview.")
        await _safe_send(
            napcat,
            group_id,
            lang_text("preview_failed", album_id=album_id, error=exc, error_code=exc.error_code),
        )
        return

    title = str(preview.get("title") or f"JM{album_id}")
    estimated_text = str(preview.get("estimated_text") or lang_text("estimated_unknown"))
    cover_url = preview.get("cover_url")
    page_count = preview.get("page_count")
    page_count_is_estimated = bool(preview.get("page_count_is_estimated"))

    if isinstance(page_count, int) and page_count > 0:
        page_text = lang_text(
            "page_count_estimated" if page_count_is_estimated else "page_count_exact",
            page_count=page_count,
        )
    else:
        page_text = lang_text("page_count_unknown")

    if _is_album_too_large(page_count, settings):
        await _safe_send(
            napcat,
            group_id,
            lang_text(
                "album_too_large",
                album_id=album_id,
                title=title,
                page_text=page_text,
                limit=settings.max_album_pages,
            ),
        )
        return

    extra_warning = ""
    if _needs_large_album_confirmation(page_count, settings):
        extra_warning = lang_text("large_album_hint", limit=settings.large_album_warning_pages)

    await _safe_send(
        napcat,
        group_id,
        lang_text(
            "album_preview",
            album_id=album_id,
            title=title,
            page_text=page_text,
            estimated_text=estimated_text,
            extra_warning=extra_warning,
        ),
    )
    state.pending_downloads[(group_id, user_id)] = PendingDownload(
        album_id=album_id,
        title=title,
        estimated_text=estimated_text,
        page_count=page_count if isinstance(page_count, int) and page_count > 0 else None,
        expires_at=asyncio.get_running_loop().time() + settings.confirm_timeout_seconds,
    )
    if isinstance(cover_url, str) and cover_url:
        asyncio.create_task(
            _send_album_cover(album_id, group_id, cover_url, settings, napcat),
            name=f"album-cover-{album_id}",
        )


def _needs_large_album_confirmation(page_count: object, settings: BotSettings) -> bool:
    return (
        settings.large_album_warning_pages > 0
        and isinstance(page_count, int)
        and page_count > settings.large_album_warning_pages
    )


def _is_album_too_large(page_count: object, settings: BotSettings) -> bool:
    return settings.max_album_pages > 0 and isinstance(page_count, int) and page_count > settings.max_album_pages


def _format_search_results(query: str, results: list[dict[str, Any]]) -> str:
    lines = [lang_text("search_results_header", query=query)]
    for index, result in enumerate(results, start=1):
        album_id = str(result.get("album_id") or "")
        title = _truncate_display_text(str(result.get("title") or f"JM{album_id}"), 46)
        lines.append(lang_text("search_result_line", index=index, album_id=album_id, title=title))
    lines.append(lang_text("search_results_footer", count=len(results)))
    return "\n".join(lines)


def _ranking_period_label(period: str) -> str:
    return {
        "day": lang_text("period_day"),
        "week": lang_text("period_week"),
        "month": lang_text("period_month"),
    }.get(period, lang_text("period_unknown"))


def _format_ranking_results(payload: dict[str, Any], results: list[dict[str, Any]]) -> str:
    period = str(payload.get("period") or "")
    label = str(payload.get("period_label") or _ranking_period_label(period))
    lines = [lang_text("ranking_results_header", period=label)]
    for index, result in enumerate(results, start=1):
        try:
            rank = int(result.get("rank"))
        except (TypeError, ValueError):
            rank = index
        album_id = str(result.get("album_id") or "")
        title = _truncate_display_text(str(result.get("title") or f"JM{album_id}"), 46)
        lines.append(lang_text("ranking_result_line", rank=rank, album_id=album_id, title=title))
    lines.append(lang_text("ranking_results_footer"))
    return "\n".join(lines)


def _format_jav_search_results(query: str, results: list[dict[str, Any]]) -> str:
    lines = [lang_text("av_search_results_header", query=query)]
    for index, result in enumerate(results[:10], start=1):
        lines.append(_format_jav_list_line(index, result, rank=index))
    lines.append(lang_text("av_search_results_footer"))
    return "\n".join(lines)


def _format_jav_actor_search_results(query: str, results: list[dict[str, Any]]) -> str:
    lines = [lang_text("actor_search_results_header", query=query)]
    for index, result in enumerate(results[:10], start=1):
        lines.append(_format_jav_list_line(index, result, rank=index))
    lines.append(lang_text("actor_search_results_footer"))
    return "\n".join(lines)


def _format_javdb_ranking_results(payload: dict[str, Any], results: list[dict[str, Any]]) -> str:
    period = str(payload.get("period") or "")
    label = str(payload.get("period_label") or _ranking_period_label(period))
    lines = [lang_text("db_ranking_results_header", period=label)]
    for index, result in enumerate(results[:20], start=1):
        rank = _int_or_default(result.get("rank"), index)
        lines.append(_format_jav_list_line(index, result, rank=rank))
    lines.append(lang_text("db_ranking_results_footer"))
    return "\n".join(lines)


def _format_jav_list_line(index: int, result: dict[str, Any], *, rank: int) -> str:
    code = str(result.get("code") or "?")
    title = _truncate_display_text(str(result.get("title") or code), 42)
    source = str(result.get("source") or "javdb")
    actors = _format_name_list(result.get("actors"), limit=3)
    actors_suffix = lang_text("av_list_actors_suffix", actors=actors) if actors else ""
    return lang_text(
        "av_list_line",
        index=index,
        rank=rank,
        code=code,
        title=title,
        source=source,
        actors=actors_suffix,
    )


def _int_or_default(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_jav_video(payload: dict[str, Any]) -> str:
    code = str(payload.get("code") or "?")
    title = _truncate_display_text(str(payload.get("title") or code), 80)
    lines = [lang_text("jav_result_header", code=code), lang_text("jav_title_line", title=title)]
    source = payload.get("source")
    if source:
        lines.append(lang_text("jav_source_line", source=str(source)))

    release_date = payload.get("release_date")
    if release_date:
        lines.append(lang_text("jav_release_date_line", release_date=release_date))

    runtime = payload.get("runtime_minutes")
    if isinstance(runtime, int) and runtime > 0:
        lines.append(lang_text("jav_runtime_line", runtime=runtime))

    for key, label_key in [
        ("studio", "jav_studio_line"),
        ("publisher", "jav_publisher_line"),
        ("series", "jav_series_line"),
        ("director", "jav_director_line"),
    ]:
        value = payload.get(key)
        if value:
            lines.append(lang_text(label_key, value=_truncate_display_text(str(value), 40)))

    rating = payload.get("rating")
    if isinstance(rating, (int, float)):
        lines.append(lang_text("jav_rating_line", rating=f"{float(rating):.1f}"))

    actors = _format_name_list(payload.get("actors"), limit=8)
    if actors:
        lines.append(lang_text("jav_actors_line", actors=actors))

    genres = _format_name_list(payload.get("genres"), limit=10)
    if genres:
        lines.append(lang_text("jav_genres_line", genres=genres))

    url = payload.get("url")
    if url:
        lines.append(lang_text("jav_url_line", url=url))
    return "\n".join(lines)


def _format_name_list(value: object, *, limit: int) -> str:
    if not isinstance(value, list):
        return ""
    names = [_truncate_display_text(str(item), 18) for item in value[:limit] if str(item).strip()]
    return " / ".join(names)


def _format_admin_status(payload: dict[str, Any], uploading_count: int) -> str:
    memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else None
    disk = payload.get("disk") if isinstance(payload.get("disk"), dict) else {}
    cache = payload.get("cache") if isinstance(payload.get("cache"), dict) else {}
    network = payload.get("network") if isinstance(payload.get("network"), dict) else {}
    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), dict) else {}

    if memory:
        memory_text = f"{_format_bytes(int(memory.get('used') or 0))} / {_format_bytes(int(memory.get('total') or 0))}"
    else:
        memory_text = lang_text("unknown")

    cpu = payload.get("cpu_percent")
    cpu_text = f"{float(cpu):.1f}%" if isinstance(cpu, (int, float)) else lang_text("unknown")

    lines = [
        lang_text("admin_status_header"),
        lang_text("admin_status_cpu", cpu=cpu_text),
        lang_text("admin_status_memory", memory=memory_text),
        lang_text(
            "admin_status_disk",
            used=_format_bytes(int(disk.get("used") or 0)),
            total=_format_bytes(int(disk.get("total") or 0)),
            free=_format_bytes(int(disk.get("free") or 0)),
        ),
        lang_text(
            "admin_status_cache",
            data=_format_bytes(int(cache.get("data") or 0)),
            jobs=_format_bytes(int(cache.get("jobs") or 0)),
            bot=_format_bytes(int(cache.get("bot_downloads") or 0)),
        ),
        lang_text(
            "admin_status_network",
            tx=_format_rate(network.get("tx_bytes_per_second")),
            rx=_format_rate(network.get("rx_bytes_per_second")),
        ),
        lang_text(
            "admin_status_queue",
            downloading=int(jobs.get("downloading") or 0),
            queued=int(jobs.get("queued") or 0),
            converting=int(jobs.get("converting") or 0),
            uploading=uploading_count,
        ),
    ]
    return "\n".join(lines)


def _format_admin_queue(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return lang_text("admin_queue_empty")

    lines = [lang_text("admin_queue_header")]
    for index, job in enumerate(jobs[:20], start=1):
        job_id = _short_job_id(str(job.get("job_id") or ""))
        album_id = str(job.get("album_id") or "?")
        group_id = str(job.get("group_id") or "?")
        status_value = str(job.get("status") or "")
        progress = _job_progress_text(job)
        lines.append(
            lang_text(
                "admin_queue_line",
                index=index,
                job_id=job_id,
                album_id=album_id,
                status=progress or _status_label(status_value),
                group_id=group_id,
            )
        )
    return "\n".join(lines)


def _format_admin_audit(events: list[dict[str, Any]]) -> str:
    if not events:
        return lang_text("admin_audit_empty")

    lines = [lang_text("admin_audit_header")]
    for index, event in enumerate(events[:10], start=1):
        target = str(event.get("target") or "")
        error_code = str(event.get("error_code") or "")
        lines.append(
            lang_text(
                "admin_audit_line",
                index=index,
                time=_format_history_time(str(event.get("created_at") or "")),
                command=_audit_command_label(str(event.get("command") or "")),
                status=_audit_status_label(str(event.get("status") or "")),
                user_id=str(event.get("user_id") or "?"),
                target=lang_text("admin_audit_target_suffix", target=target) if target else "",
                error=lang_text("admin_audit_error_suffix", error_code=error_code) if error_code else "",
            )
        )
    return "\n".join(lines)


def _format_history_jobs(jobs: list[dict[str, Any]], group_scope: bool) -> str:
    if not jobs:
        return lang_text("group_history_empty" if group_scope else "user_history_empty")

    lines = [lang_text("group_history_header" if group_scope else "user_history_header")]
    for index, job in enumerate(jobs[:10], start=1):
        album_id = str(job.get("album_id") or "?")
        status = _job_progress_text(job) or _status_label(str(job.get("status") or ""))
        time_text = _format_history_time(str(job.get("updated_at") or job.get("created_at") or ""))
        extra = ""
        if group_scope:
            extra = lang_text("history_user_suffix", user_id=str(job.get("user_id") or "?"))
        lines.append(
            lang_text(
                "history_line",
                index=index,
                album_id=album_id,
                status=status,
                time=time_text,
                extra=extra,
            )
        )
    return "\n".join(lines)


def _format_history_time(value: str) -> str:
    if not value:
        return lang_text("time_unknown")
    return value.replace("T", " ")[:16]


def _audit_command_label(command: str) -> str:
    key_by_command = {
        "admin:status": "audit_command_admin_status",
        "admin:queue": "audit_command_admin_queue",
        "admin:audit": "audit_command_admin_audit",
        "admin:cleanup": "audit_command_admin_cleanup",
        "admin:cancel": "audit_command_admin_cancel",
        "confirm_download": "audit_command_confirm_download",
        "search_select": "audit_command_search_select",
        "jav_action": "audit_command_jav_action",
        "active_cancel": "audit_command_active_cancel",
        "blocked_group": "audit_command_blocked_group",
        "home": "audit_command_home",
        "help": "audit_command_help",
        "features": "audit_command_features",
        "history": "audit_command_history",
        "group_history": "audit_command_group_history",
        "usage": "audit_command_usage",
        "ok": "audit_command_ok",
        "search": "audit_command_search",
        "ranking": "audit_command_ranking",
        "av_search": "audit_command_av_search",
        "actor_search": "audit_command_actor_search",
        "db_ranking": "audit_command_db_ranking",
        "jav": "audit_command_jav",
        "unknown_command": "audit_command_unknown",
        "error": "audit_command_error",
    }
    key = key_by_command.get(command)
    return lang_text(key) if key else (command or lang_text("unknown"))


def _audit_status_label(status_value: str) -> str:
    key_by_status = {
        "received": "audit_status_received",
        "handled": "audit_status_handled",
        "failed": "audit_status_failed",
        "blocked": "audit_status_blocked",
    }
    key = key_by_status.get(status_value)
    return lang_text(key) if key else (status_value or lang_text("unknown"))


def _audit_target(parse_result: object) -> str | None:
    if not hasattr(parse_result, "action"):
        return None
    action = parse_result.action
    if action == ParseAction.OK:
        return f"JM{parse_result.album_id}" if parse_result.album_id else None
    if action == ParseAction.SEARCH:
        return parse_result.search_query
    if action == ParseAction.RANKING:
        return parse_result.ranking_period
    if action == ParseAction.AV_SEARCH:
        return parse_result.search_query
    if action == ParseAction.ACTOR_SEARCH:
        return parse_result.search_query
    if action == ParseAction.DB_RANKING:
        return parse_result.db_ranking_period
    if action == ParseAction.JAV:
        return parse_result.jav_code
    if action == ParseAction.ERROR:
        return parse_result.error_key
    return None


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


async def _record_command_audit(
    backend: BackendClient,
    group_id: str,
    user_id: str,
    command: str,
    target: str | None,
    status_value: str,
    error_code: str | None,
    duration_ms: int,
) -> None:
    try:
        await backend.create_audit_event(
            {
                "group_id": group_id,
                "user_id": user_id,
                "command": command,
                "target": target,
                "status": status_value,
                "error_code": error_code,
                "duration_ms": duration_ms,
            }
        )
    except Exception:
        logger.debug("Could not record command audit event.", exc_info=True)


def _merge_uploading_jobs(jobs: list[dict[str, Any]], state: BotState) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {str(job.get("job_id")): dict(job) for job in jobs if job.get("job_id")}
    for uploading in state.uploading_jobs.values():
        merged[uploading.job_id] = {
            "job_id": uploading.job_id,
            "album_id": uploading.album_id,
            "group_id": uploading.group_id,
            "user_id": uploading.user_id,
            "status": "uploading",
            "downloaded_files": 0,
            "total_files": 0,
            "progress_message": lang_text("status_uploading"),
        }
    return list(merged.values())


def _job_progress_text(job: dict[str, Any]) -> str:
    status_value = str(job.get("status") or "")
    if status_value == "failed":
        error_code = job.get("error_code")
        return lang_text("status_error_with_code", error_code=error_code or "UNKNOWN")
    if status_value == "uploading":
        return lang_text("status_uploading")

    total_files = int(job.get("total_files") or 0)
    downloaded_files = int(job.get("downloaded_files") or 0)
    label = _status_label(status_value)
    if total_files > 0 and status_value in {"downloading", "completed"}:
        ratio = min(100.0, max(0.0, downloaded_files * 100 / total_files))
        if status_value == "completed":
            ratio = 100.0
        return f"{label}（{ratio:.0f}%）"
    return label


def _status_label(status_value: str) -> str:
    key_by_status = {
        "queued": "status_queued",
        "downloading": "status_downloading",
        "converting": "status_converting",
        "completed": "status_completed",
        "failed": "status_failed",
        "uploading": "status_uploading",
    }
    key = key_by_status.get(status_value)
    return lang_text(key) if key else (status_value or lang_text("unknown"))


def _short_job_id(job_id: str) -> str:
    return job_id.split("-", 1)[0] if job_id else "?"


def _normalize_cancel_target(target: str) -> str:
    target = target.strip()
    match = re.search(r"(?i)\bJM\s*(\d{1,12})\b", target)
    if match:
        return match.group(1)
    return target


def _find_uploading_job(target: str, state: BotState) -> UploadingJob | None:
    for job in state.uploading_jobs.values():
        if job.job_id == target or job.job_id.startswith(target) or job.album_id == target:
            return job
    return None


def _truncate_display_text(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + "…"


async def _send_album_cover(
    album_id: str,
    group_id: str,
    cover_url: str,
    settings: BotSettings,
    napcat: NapCatClient,
) -> None:
    try:
        await napcat.send_group_image(group_id, cover_url)
        return
    except NapCatAPIError:
        logger.warning("Could not send album cover by URL for JM%s; trying local cache.", album_id, exc_info=True)

    try:
        cover_path = await _download_cover_image(
            cover_url,
            settings.data_dir.resolve() / "cover_cache",
            album_id,
        )
    except Exception:
        logger.exception("Could not download album cover for JM%s.", album_id)
        return

    for attempt in range(1, COVER_SEND_RETRIES + 1):
        try:
            await napcat.send_group_image(group_id, str(cover_path))
            return
        except NapCatAPIError as exc:
            if attempt < COVER_SEND_RETRIES:
                logger.warning(
                    "Local cover send attempt %s failed for JM%s: %s",
                    attempt,
                    album_id,
                    exc,
                )
                await asyncio.sleep(min(10, 2 * attempt))
            else:
                logger.exception("Could not send cached album cover for JM%s.", album_id)


async def _send_jav_cover(code: str, group_id: str, cover_url: str, napcat: NapCatClient) -> None:
    try:
        await napcat.send_group_image(group_id, cover_url)
    except NapCatAPIError:
        logger.warning("Could not send JAV metadata cover for %s.", code, exc_info=True)


async def _download_cover_image(cover_url: str, cache_dir: Path, album_id: str) -> Path:
    cache_dir = cache_dir.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(COVER_DOWNLOAD_TIMEOUT_SECONDS),
            headers={"User-Agent": "sanbot/0.1"},
        ) as client:
            async with client.stream("GET", cover_url) as response:
                response.raise_for_status()
                extension = _cover_image_extension(response.headers.get("content-type"), cover_url)
                safe_album_id = re.sub(r"\D+", "", album_id)[:12] or "unknown"
                cover_path = (cache_dir / f"JM{safe_album_id}{extension}").resolve()
                if not cover_path.is_relative_to(cache_dir):
                    raise ValueError("Invalid cover cache path")

                tmp_path = cover_path.with_name(f"{cover_path.name}.tmp")
                size = 0
                with tmp_path.open("wb") as file:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MAX_COVER_IMAGE_BYTES:
                            raise ValueError("Cover image is too large")
                        file.write(chunk)

        if tmp_path is None or not tmp_path.is_file() or tmp_path.stat().st_size <= 0:
            raise ValueError("Cover image is empty")
        tmp_path.replace(cover_path)
        return cover_path
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _cover_image_extension(content_type: str | None, cover_url: str) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    content_type_extensions = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if normalized in content_type_extensions:
        return content_type_extensions[normalized]

    suffix = Path(urlsplit(cover_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


async def _create_job_and_monitor(
    album_id: str,
    group_id: str,
    user_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
    spawn_task: Callable[[Awaitable[None]], None],
    state: BotState | None = None,
    page_count: int | None = None,
    extra_message: str | None = None,
) -> None:
    try:
        created = await backend.create_job(album_id, group_id, user_id, page_count=page_count)
    except DuplicateJobError as exc:
        suffix = f"：{exc.job_id}" if exc.job_id else ""
        await _safe_send(
            napcat,
            group_id,
            lang_text("duplicate_job", album_id=album_id, suffix=suffix, error_code=exc.error_code),
        )
        return
    except JobLimitError as exc:
        logger.info("Job limit rejected JM%s: %s", album_id, exc)
        await _safe_send(napcat, group_id, lang_text("job_limit_reached", error=exc, error_code=exc.error_code))
        return
    except BackendError as exc:
        logger.exception("Could not create backend job.")
        if exc.error_code == "BACKEND_UNAVAILABLE":
            await _safe_send(napcat, group_id, lang_text("backend_unavailable", error_code=exc.error_code))
        else:
            await _safe_send(napcat, group_id, lang_text("job_create_failed", error=exc, error_code=exc.error_code))
        return

    job_id = str(created["job_id"])
    message = lang_text("job_accepted", album_id=album_id, job_id=job_id)
    if extra_message:
        message = f"{message}\n{extra_message}"
    await _safe_send(napcat, group_id, message)
    spawn_task(monitor_job(job_id, album_id, group_id, settings, napcat, backend, state=state))


async def monitor_job(
    job_id: str,
    album_id: str,
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
    state: BotState | None = None,
) -> None:
    last_progress_at = asyncio.get_running_loop().time()
    last_progress_key: tuple[str | None, str | None, int] | None = None

    while True:
        try:
            job = await backend.get_job(job_id)
        except BackendError as exc:
            logger.exception("Could not query job %s.", job_id)
            await asyncio.sleep(settings.poll_interval_seconds)
            continue

        status = job.get("status")
        if status == "failed":
            error_message = job.get("error_message") or lang_text("generic_job_failed")
            error_code = job.get("error_code") or "UNKNOWN"
            await _safe_send(
                napcat,
                group_id,
                lang_text("job_failed", album_id=album_id, error_message=error_message, error_code=error_code),
            )
            return

        if status == "completed":
            await _download_and_upload(job, album_id, group_id, settings, napcat, backend, state=state)
            return

        progress_message = job.get("progress_message")
        downloaded_files = int(job.get("downloaded_files") or 0)
        progress_key = (status, progress_message, downloaded_files)
        now = asyncio.get_running_loop().time()
        if (
            settings.progress_notify_seconds > 0
            and status != "downloading"
            and progress_message
            and progress_key != last_progress_key
            and now - last_progress_at >= settings.progress_notify_seconds
        ):
            await _safe_send(
                napcat,
                group_id,
                lang_text("job_progress", album_id=album_id, progress_message=progress_message),
            )
            last_progress_at = now
            last_progress_key = progress_key

        await asyncio.sleep(settings.poll_interval_seconds)


async def _download_and_upload(
    job: dict[str, Any],
    album_id: str,
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
    state: BotState | None = None,
) -> None:
    job_id = str(job["job_id"])
    raw_filename = str(job.get("filename") or f"[JM{album_id}].pdf")
    filename = _safe_filename(
        raw_filename,
        f"[JM{album_id}].pdf",
    )
    upload_filename = _upload_display_filename(
        raw_filename,
        f"[JM{album_id}].pdf",
        album_id,
        settings.max_upload_filename_bytes,
    )
    dest_dir = settings.data_dir.resolve() / "bot_downloads" / job_id
    dest_path = dest_dir / filename
    if state is not None:
        state.uploading_jobs[job_id] = UploadingJob(
            job_id=job_id,
            album_id=album_id,
            group_id=group_id,
            user_id=str(job.get("user_id") or ""),
            started_at=asyncio.get_running_loop().time(),
        )

    try:
        try:
            pdf_path = await backend.download_file(job_id, dest_path)
        except BackendError as exc:
            logger.exception("Could not download PDF for job %s.", job_id)
            await _safe_send(napcat, group_id, lang_text("pdf_download_failed", album_id=album_id, error_code=exc.error_code))
            return

        try:
            upload_files = await asyncio.to_thread(
                _prepare_upload_files,
                pdf_path,
                upload_filename,
                settings.max_upload_bytes,
                settings.max_upload_filename_bytes,
                album_id,
            )
        except UploadPreparationError as exc:
            logger.exception("Could not prepare upload files for job %s.", job_id)
            await _safe_send(napcat, group_id, lang_text("upload_prepare_failed", album_id=album_id, error=exc))
            return

        if len(upload_files) > 1:
            await _safe_send(
                napcat,
                group_id,
                lang_text(
                    "large_pdf_split",
                    album_id=album_id,
                    size=_format_bytes(pdf_path.stat().st_size),
                    count=len(upload_files),
                ),
            )

        try:
            for index, (upload_path, upload_name) in enumerate(upload_files, start=1):
                if _upload_cancel_requested(state, job_id):
                    raise UploadCancelledError
                if not await _upload_item_with_fallback(
                    napcat,
                    group_id,
                    upload_path,
                    upload_name,
                    dest_dir,
                    job_id,
                    album_id,
                    settings.max_upload_filename_bytes,
                    settings.upload_retries,
                    label=f"upload_{index:02d}",
                    cancel_requested=lambda: _upload_cancel_requested(state, job_id),
                ):
                    await _safe_send(
                        napcat,
                        group_id,
                        lang_text("upload_part_failed", album_id=album_id, index=index, count=len(upload_files)),
                    )
                    return
        except UploadCancelledError:
            await _safe_send(napcat, group_id, lang_text("upload_cancelled_by_admin", album_id=album_id))
            return

        if len(upload_files) == 1:
            await _safe_send(napcat, group_id, lang_text("upload_completed", album_id=album_id, filename=filename))
        else:
            await _safe_send(napcat, group_id, lang_text("upload_completed_parts", album_id=album_id))
        await asyncio.to_thread(_cleanup_bot_download_dir, dest_dir, settings.data_dir.resolve() / "bot_downloads")
    finally:
        if state is not None:
            state.uploading_jobs.pop(job_id, None)
            state.cancelled_uploads.discard(job_id)


def _upload_cancel_requested(state: BotState | None, job_id: str) -> bool:
    return state is not None and job_id in state.cancelled_uploads


async def _upload_item_with_fallback(
    napcat: NapCatClient,
    group_id: str,
    file_path: Path,
    filename: str,
    dest_dir: Path,
    job_id: str,
    album_id: str,
    max_filename_bytes: int,
    upload_retries: int,
    label: str,
    cancel_requested: Callable[[], bool] | None = None,
    depth: int = 0,
) -> bool:
    if cancel_requested is not None and cancel_requested():
        raise UploadCancelledError
    staged_path = await asyncio.to_thread(_stage_upload_file, file_path, dest_dir, label)
    if await _upload_with_retries(
        napcat,
        group_id,
        staged_path,
        filename,
        job_id,
        upload_retries,
        cancel_requested=cancel_requested,
    ):
        return True

    compact_filename = _compact_upload_filename(album_id, filename)
    if compact_filename != filename and await _upload_with_retries(
        napcat,
        group_id,
        staged_path,
        compact_filename,
        job_id,
        upload_retries,
        cancel_requested=cancel_requested,
    ):
        return True

    if depth >= MAX_UPLOAD_FALLBACK_DEPTH:
        return False

    if file_path.stat().st_size < int(DEFAULT_MAX_UPLOAD_BYTES * 0.8):
        return False

    try:
        fallback_files = await asyncio.to_thread(
            _split_pdf_for_retry,
            staged_path,
            compact_filename,
            max_filename_bytes,
            album_id,
        )
    except UploadPreparationError:
        logger.exception("Could not split failed upload part for job %s.", job_id)
        return False

    if len(fallback_files) <= 1:
        return False

    await _safe_send(
        napcat,
        group_id,
        lang_text("upload_retry_split", album_id=album_id, count=len(fallback_files)),
    )
    for sub_index, (sub_path, sub_name) in enumerate(fallback_files, start=1):
        if cancel_requested is not None and cancel_requested():
            raise UploadCancelledError
        if not await _upload_item_with_fallback(
            napcat,
            group_id,
            sub_path,
            sub_name,
            dest_dir,
            job_id,
            album_id,
            max_filename_bytes,
            upload_retries,
            label=f"{label}_{sub_index:02d}",
            cancel_requested=cancel_requested,
            depth=depth + 1,
        ):
            return False
    return True


async def _upload_with_retries(
    napcat: NapCatClient,
    group_id: str,
    file_path: Path,
    filename: str,
    job_id: str,
    attempts: int = DEFAULT_UPLOAD_RETRIES,
    cancel_requested: Callable[[], bool] | None = None,
) -> bool:
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        if cancel_requested is not None and cancel_requested():
            raise UploadCancelledError
        try:
            await napcat.upload_group_file(group_id, file_path, filename)
            return True
        except NapCatAPIError as exc:
            if attempt < attempts:
                logger.warning("Upload attempt %s failed for job %s: %s", attempt, job_id, exc)
                delay = min(60, 5 * attempt)
                if cancel_requested is None:
                    await asyncio.sleep(delay)
                else:
                    deadline = asyncio.get_running_loop().time() + delay
                    while asyncio.get_running_loop().time() < deadline:
                        if cancel_requested():
                            raise UploadCancelledError
                        await asyncio.sleep(min(1, deadline - asyncio.get_running_loop().time()))
            else:
                logger.exception("Upload attempt %s failed for job %s.", attempt, job_id)
    return False


def _prepare_upload_files(
    pdf_path: Path,
    filename: str,
    max_upload_bytes: int,
    max_filename_bytes: int = MAX_UPLOAD_FILENAME_BYTES,
    album_id: str | None = None,
) -> list[tuple[Path, str]]:
    pdf_path = pdf_path.resolve()
    if max_upload_bytes <= 0 or pdf_path.stat().st_size <= max_upload_bytes:
        return [(pdf_path, filename)]
    return _split_pdf_for_upload(pdf_path, filename, max_upload_bytes, max_filename_bytes, album_id)


def _split_pdf_for_upload(
    pdf_path: Path,
    filename: str,
    max_upload_bytes: int,
    max_filename_bytes: int = MAX_UPLOAD_FILENAME_BYTES,
    album_id: str | None = None,
) -> list[tuple[Path, str]]:
    try:
        import pikepdf
    except ImportError as exc:
        raise UploadPreparationError(lang_text("upload_error_missing_pikepdf")) from exc

    split_dir = pdf_path.parent / f"{pdf_path.stem}_parts"
    parent = pdf_path.parent.resolve()
    split_dir = split_dir.resolve()
    if not split_dir.is_relative_to(parent):
        raise UploadPreparationError(lang_text("upload_error_split_dir"))
    if split_dir.exists():
        shutil.rmtree(split_dir)
    split_dir.mkdir(parents=True, exist_ok=True)

    try:
        with pikepdf.Pdf.open(pdf_path) as source_pdf:
            page_count = len(source_pdf.pages)
            if page_count <= 1:
                return [(pdf_path, filename)]

            target_bytes = max(1, int(max_upload_bytes * 0.85))
            part_count = min(page_count, max(2, math.ceil(pdf_path.stat().st_size / target_bytes)))
            while part_count <= page_count:
                shutil.rmtree(split_dir)
                split_dir.mkdir(parents=True, exist_ok=True)
                pages_per_part = max(1, math.ceil(page_count / part_count))
                parts = _write_pdf_parts(
                    source_pdf,
                    page_count,
                    pages_per_part,
                    split_dir,
                    filename,
                    max_filename_bytes,
                    album_id,
                )
                oversized = [path for path, _name in parts if path.stat().st_size > max_upload_bytes]
                if not oversized:
                    return parts
                if pages_per_part == 1:
                    raise UploadPreparationError(lang_text("upload_error_part_too_large"))
                part_count = min(page_count, max(part_count + 1, math.ceil(part_count * 1.5)))
    except UploadPreparationError:
        raise
    except Exception as exc:
        raise UploadPreparationError(lang_text("upload_error_split_failed")) from exc

    raise UploadPreparationError(lang_text("upload_error_split_failed"))


def _write_pdf_parts(
    source_pdf: Any,
    page_count: int,
    pages_per_part: int,
    split_dir: Path,
    filename: str,
    max_filename_bytes: int,
    album_id: str | None = None,
) -> list[tuple[Path, str]]:
    parts: list[tuple[Path, str]] = []
    total = math.ceil(page_count / pages_per_part)
    start = 0
    index = 1
    while start < page_count:
        end = min(page_count, start + pages_per_part)
        part_pdf = None
        part_path: Path | None = None
        try:
            import pikepdf

            part_pdf = pikepdf.Pdf.new()
            for page_index in range(start, end):
                part_pdf.pages.append(source_pdf.pages[page_index])

            part_name = _part_filename(filename, index, total, max_filename_bytes, album_id)
            part_path = split_dir / part_name
            part_pdf.save(part_path)
        finally:
            if part_pdf is not None:
                part_pdf.close()

        if part_path is None or not part_path.is_file() or part_path.stat().st_size <= 0:
            raise UploadPreparationError(lang_text("upload_error_invalid_part"))
        parts.append((part_path, part_name))
        start = end
        index += 1
    return parts


def _split_pdf_for_retry(
    pdf_path: Path,
    filename: str,
    max_filename_bytes: int,
    album_id: str | None = None,
) -> list[tuple[Path, str]]:
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file() or pdf_path.stat().st_size <= 0:
        raise UploadPreparationError(lang_text("upload_error_invalid_part"))
    retry_max_upload_bytes = max(1, int(pdf_path.stat().st_size * 0.65))
    return _split_pdf_for_upload(pdf_path, filename, retry_max_upload_bytes, max_filename_bytes, album_id)


def _stage_upload_file(source_path: Path, dest_dir: Path, label: str) -> Path:
    source_path = source_path.resolve()
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        raise UploadPreparationError(lang_text("upload_error_invalid_part"))

    dest_dir = dest_dir.resolve()
    stage_dir = (dest_dir / "_upload").resolve()
    if not stage_dir.is_relative_to(dest_dir):
        raise UploadPreparationError(lang_text("upload_error_split_dir"))
    stage_dir.mkdir(parents=True, exist_ok=True)

    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("._") or "upload"
    staged_path = stage_dir / f"{safe_label}.pdf"
    if staged_path.exists():
        staged_path.unlink()
    shutil.copy2(source_path, staged_path)
    if staged_path.stat().st_size != source_path.stat().st_size:
        staged_path.unlink(missing_ok=True)
        raise UploadPreparationError(lang_text("upload_error_invalid_part"))
    return staged_path


def _cleanup_bot_download_dir(dest_dir: Path, bot_downloads_dir: Path) -> None:
    dest_dir = dest_dir.resolve()
    bot_downloads_dir = bot_downloads_dir.resolve()
    if dest_dir == bot_downloads_dir or not dest_dir.is_relative_to(bot_downloads_dir):
        logger.warning("Skip bot download cleanup outside cache dir: %s", dest_dir)
        return
    if not dest_dir.exists():
        return
    try:
        shutil.rmtree(dest_dir)
    except OSError:
        logger.exception("Could not cleanup bot download cache: %s", dest_dir)


def _part_filename(
    filename: str,
    index: int,
    total: int,
    max_filename_bytes: int = MAX_UPLOAD_FILENAME_BYTES,
    album_id: str | None = None,
) -> str:
    album = album_id or _album_id_from_filename(filename)
    if album:
        return f"JM{album}_part{index:02d}-of{total:02d}.pdf"

    safe = _safe_filename(filename, "upload.pdf", max_bytes=max_filename_bytes)
    stem = Path(safe).stem.strip(" .")
    if len(stem) > 120:
        stem = stem[:120].strip(" .")
    stem = stem or "upload"
    return _safe_filename(
        f"part{index:02d}-of{total:02d}_{stem}.pdf",
        f"part{index:02d}-of{total:02d}.pdf",
        max_bytes=max_filename_bytes,
    )


def _album_id_from_filename(filename: str) -> str | None:
    match = re.search(r"(?i)JM\s*(\d{1,12})", filename)
    return match.group(1) if match else None


def _compact_upload_filename(album_id: str, filename: str) -> str:
    match = re.match(r"(?:JM\d+_)?part(\d+)-of(\d+)", filename)
    if match:
        return f"JM{album_id}_part{int(match.group(1)):02d}-of{int(match.group(2)):02d}.pdf"
    return f"JM{album_id}.pdf"


def _upload_display_filename(filename: str, fallback: str, album_id: str, max_bytes: int) -> str:
    _safe_filename(filename, fallback, max_bytes=max_bytes)
    return _compact_upload_filename(album_id, filename)


def _format_bytes(size: int) -> str:
    if size <= 0:
        return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)}B"
    if value >= 10:
        return f"{value:.0f}{units[unit_index]}"
    return f"{value:.1f}{units[unit_index]}"


def _format_rate(value: object) -> str:
    if not isinstance(value, (int, float)):
        return lang_text("unknown")
    return f"{_format_bytes(int(value))}/s"


async def _safe_send(napcat: NapCatClient, group_id: str, message: str) -> None:
    try:
        await napcat.send_group_msg(group_id, message)
    except NapCatAPIError:
        logger.exception("Could not send group message.")


async def monitor_health(
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
) -> None:
    interval = max(5, settings.health_check_interval_seconds)
    was_healthy = True
    while True:
        await asyncio.sleep(interval)
        try:
            payload = await backend.health()
            if not isinstance(payload, dict) or payload.get("status") != "ok":
                raise BackendError("后端健康检查返回异常", "BACKEND_HEALTH_BAD_STATUS")
        except Exception:
            logger.warning("Backend health check failed.", exc_info=True)
            if was_healthy:
                await _notify_health_groups(settings, napcat, lang_text("health_failed"))
            was_healthy = False
            continue

        if not was_healthy:
            await _notify_health_groups(settings, napcat, lang_text("health_recovered"))
        was_healthy = True


async def _notify_health_groups(settings: BotSettings, napcat: NapCatClient, message: str) -> None:
    notify_groups = settings.health_notify_group_ids or settings.allowed_group_ids
    for group_id in sorted(notify_groups):
        await _safe_send(napcat, group_id, message)


def _spawn_task(pending_tasks: set[asyncio.Task[None]], awaitable: Awaitable[None]) -> None:
    task = asyncio.create_task(awaitable)
    pending_tasks.add(task)

    def _done(done_task: asyncio.Task[None]) -> None:
        pending_tasks.discard(done_task)
        try:
            done_task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Background bot task failed.")

    task.add_done_callback(_done)


async def run_bot() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = BotSettings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    pending_tasks: set[asyncio.Task[None]] = set()
    state = BotState()
    async with NapCatClient(
        settings.napcat_ws_url,
        settings.napcat_http_url,
        settings.napcat_access_token,
        request_timeout_seconds=settings.napcat_http_timeout_seconds,
        upload_timeout_seconds=settings.napcat_upload_timeout_seconds,
    ) as napcat, BackendClient(
        settings.backend_url,
        settings.backend_api_token,
    ) as backend:
        if settings.health_check_interval_seconds > 0:
            _spawn_task(pending_tasks, monitor_health(settings, napcat, backend))
        try:
            async for event in napcat.iter_events():
                _spawn_task(
                    pending_tasks,
                    handle_group_message(
                        event,
                        settings,
                        state,
                        napcat,
                        backend,
                        lambda awaitable: _spawn_task(pending_tasks, awaitable),
                    ),
                )
        finally:
            tasks = list(pending_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")


if __name__ == "__main__":
    main()
