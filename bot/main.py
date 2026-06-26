from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .backend_client import BackendClient, BackendError, DuplicateJobError
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


class UploadPreparationError(Exception):
    pass


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
    confirm_timeout_seconds: int = 300
    large_album_warning_pages: int = 100
    napcat_http_timeout_seconds: int = 60
    napcat_upload_timeout_seconds: int = 900
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES

    @classmethod
    def from_env(cls) -> "BotSettings":
        load_dotenv()
        bot_qq_id = os.getenv("BOT_QQ_ID")
        if not bot_qq_id:
            raise RuntimeError("BOT_QQ_ID is required")
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
            confirm_timeout_seconds=max(30, _env_int("JOB_CONFIRM_TIMEOUT_SECONDS", 300)),
            large_album_warning_pages=max(0, _env_int("LARGE_ALBUM_WARNING_PAGES", 100)),
            napcat_http_timeout_seconds=max(1, _env_int("NAPCAT_HTTP_TIMEOUT_SECONDS", 60)),
            napcat_upload_timeout_seconds=max(60, _env_int("NAPCAT_UPLOAD_TIMEOUT_SECONDS", 900)),
            max_upload_bytes=max(0, _env_int("NAPCAT_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)),
        )


@dataclass(frozen=True)
class PendingDownload:
    album_id: str
    title: str
    estimated_text: str
    page_count: int | None
    expires_at: float
    large_warning_sent: bool = False


@dataclass
class BotState:
    pending_downloads: dict[tuple[str, str], PendingDownload] = field(default_factory=dict)

    def cleanup(self, now: float) -> None:
        expired = [
            key
            for key, pending in self.pending_downloads.items()
            if pending.expires_at <= now
        ]
        for key in expired:
            self.pending_downloads.pop(key, None)


def _safe_filename(name: str, fallback: str) -> str:
    cleaned = ILLEGAL_FILENAME_CHARS_RE.sub("_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or fallback


def _confirm_words() -> set[str]:
    return lang_words("confirm_words", DEFAULT_CONFIRM_WORDS)


def _cancel_words() -> set[str]:
    return lang_words("cancel_words", DEFAULT_CANCEL_WORDS)


def _active_cancel_words() -> set[str]:
    return lang_words("active_cancel_words", DEFAULT_ACTIVE_CANCEL_WORDS)


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

    now = asyncio.get_running_loop().time()
    state.cleanup(now)
    if await _handle_pending_confirmation(event, group_id, user_id, settings, state, napcat, backend, spawn_task):
        return
    if await _handle_active_cancel(event, group_id, user_id, napcat, backend):
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
        "Parsed group command action=%s album_id=%s group_id=%s user_id=%s",
        parse_result.action,
        parse_result.album_id,
        group_id,
        user_id,
    )

    if parse_result.action == ParseAction.USAGE:
        await _safe_send(napcat, group_id, lang_text("usage"))
        return

    if parse_result.action == ParseAction.ERROR:
        await _safe_send(napcat, group_id, lang_text(parse_result.error_key or "usage"))
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

    await _send_album_preview(album_id, group_id, user_id, settings, state, napcat, backend)


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
        page_count=pending.page_count,
        extra_message=lang_text("estimated_time_line", estimated_text=pending.estimated_text),
    )
    return True


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

    if isinstance(cover_url, str) and cover_url:
        try:
            await napcat.send_group_image(group_id, cover_url)
        except NapCatAPIError:
            logger.exception("Could not send album cover.")

    if isinstance(page_count, int) and page_count > 0:
        page_text = lang_text(
            "page_count_estimated" if page_count_is_estimated else "page_count_exact",
            page_count=page_count,
        )
    else:
        page_text = lang_text("page_count_unknown")
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


def _needs_large_album_confirmation(page_count: object, settings: BotSettings) -> bool:
    return (
        settings.large_album_warning_pages > 0
        and isinstance(page_count, int)
        and page_count > settings.large_album_warning_pages
    )


async def _create_job_and_monitor(
    album_id: str,
    group_id: str,
    user_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
    spawn_task: Callable[[Awaitable[None]], None],
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
    except BackendError as exc:
        logger.exception("Could not create backend job.")
        await _safe_send(napcat, group_id, lang_text("backend_unavailable", error_code=exc.error_code))
        return

    job_id = str(created["job_id"])
    message = lang_text("job_accepted", album_id=album_id, job_id=job_id)
    if extra_message:
        message = f"{message}\n{extra_message}"
    await _safe_send(napcat, group_id, message)
    spawn_task(monitor_job(job_id, album_id, group_id, settings, napcat, backend))


async def monitor_job(
    job_id: str,
    album_id: str,
    group_id: str,
    settings: BotSettings,
    napcat: NapCatClient,
    backend: BackendClient,
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
            await _download_and_upload(job, album_id, group_id, settings, napcat, backend)
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
) -> None:
    job_id = str(job["job_id"])
    filename = _safe_filename(str(job.get("filename") or f"[JM{album_id}].pdf"), f"[JM{album_id}].pdf")
    dest_dir = settings.data_dir.resolve() / "bot_downloads" / job_id
    dest_path = dest_dir / filename

    try:
        pdf_path = await backend.download_file(job_id, dest_path)
    except BackendError as exc:
        logger.exception("Could not download PDF for job %s.", job_id)
        await _safe_send(napcat, group_id, lang_text("pdf_download_failed", album_id=album_id, error_code=exc.error_code))
        return

    try:
        upload_files = await asyncio.to_thread(_prepare_upload_files, pdf_path, filename, settings.max_upload_bytes)
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

    for index, (upload_path, upload_name) in enumerate(upload_files, start=1):
        if not await _upload_with_retries(napcat, group_id, upload_path, upload_name, job_id):
            await _safe_send(
                napcat,
                group_id,
                lang_text("upload_part_failed", album_id=album_id, index=index, count=len(upload_files)),
            )
            return

    if len(upload_files) == 1:
        await _safe_send(napcat, group_id, lang_text("upload_completed", album_id=album_id, filename=filename))
    else:
        await _safe_send(napcat, group_id, lang_text("upload_completed_parts", album_id=album_id))


async def _upload_with_retries(
    napcat: NapCatClient,
    group_id: str,
    file_path: Path,
    filename: str,
    job_id: str,
) -> bool:
    for attempt in range(1, 4):
        try:
            await napcat.upload_group_file(group_id, file_path, filename)
            return True
        except NapCatAPIError as exc:
            if attempt < 3:
                logger.warning("Upload attempt %s failed for job %s: %s", attempt, job_id, exc)
                await asyncio.sleep(attempt * 2)
            else:
                logger.exception("Upload attempt %s failed for job %s.", attempt, job_id)
    return False


def _prepare_upload_files(pdf_path: Path, filename: str, max_upload_bytes: int) -> list[tuple[Path, str]]:
    pdf_path = pdf_path.resolve()
    if max_upload_bytes <= 0 or pdf_path.stat().st_size <= max_upload_bytes:
        return [(pdf_path, filename)]
    return _split_pdf_for_upload(pdf_path, filename, max_upload_bytes)


def _split_pdf_for_upload(pdf_path: Path, filename: str, max_upload_bytes: int) -> list[tuple[Path, str]]:
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
            part_count = max(2, math.ceil(pdf_path.stat().st_size / target_bytes))
            pages_per_part = max(1, math.ceil(page_count / part_count))
            parts: list[tuple[Path, str]] = []

            start = 0
            index = 1
            while start < page_count:
                end = min(page_count, start + pages_per_part)
                part_pdf = pikepdf.Pdf.new()
                for page_index in range(start, end):
                    part_pdf.pages.append(source_pdf.pages[page_index])

                part_name = _part_filename(filename, index, math.ceil(page_count / pages_per_part))
                part_path = split_dir / part_name
                part_pdf.save(part_path)
                part_pdf.close()
                if not part_path.is_file() or part_path.stat().st_size <= 0:
                    raise UploadPreparationError(lang_text("upload_error_invalid_part"))
                parts.append((part_path, part_name))
                start = end
                index += 1
    except UploadPreparationError:
        raise
    except Exception as exc:
        raise UploadPreparationError(lang_text("upload_error_split_failed")) from exc

    return parts or [(pdf_path, filename)]


def _part_filename(filename: str, index: int, total: int) -> str:
    safe = _safe_filename(filename, "upload.pdf")
    stem = Path(safe).stem.strip(" .")
    if len(stem) > 120:
        stem = stem[:120].strip(" .")
    stem = stem or "upload"
    return f"part{index:02d}-of{total:02d}_{stem}.pdf"


def _format_bytes(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))}KB"
    return f"{size / 1024 / 1024:.1f}MB"


async def _safe_send(napcat: NapCatClient, group_id: str, message: str) -> None:
    try:
        await napcat.send_group_msg(group_id, message)
    except NapCatAPIError:
        logger.exception("Could not send group message.")


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
