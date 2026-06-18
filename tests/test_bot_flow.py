from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable

import pytest

from bot.main import BotSettings, _download_and_upload, handle_group_message
from bot.napcat_client import NapCatAPIError


class FakeNapCat:
    def __init__(self, upload_failures: int = 0) -> None:
        self.sent: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, Path, str]] = []
        self.upload_attempts = 0
        self.upload_failures = upload_failures

    async def send_group_msg(self, group_id: str, message: str) -> dict:
        self.sent.append((group_id, message))
        return {"status": "ok", "retcode": 0}

    async def upload_group_file(self, group_id: str, file_path: str | Path, name: str) -> dict:
        self.upload_attempts += 1
        if self.upload_attempts <= self.upload_failures:
            raise NapCatAPIError("upload failed")
        self.uploads.append((group_id, Path(file_path), name))
        return {"status": "ok", "retcode": 0}


class FakeCreateBackend:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []

    async def create_job(self, album_id: str, group_id: str, user_id: str) -> dict:
        self.created.append((album_id, group_id, user_id))
        return {"job_id": "job-123", "status": "queued"}


class FakeDownloadBackend:
    async def download_file(self, job_id: str, dest_path: str | Path) -> Path:
        path = Path(dest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n")
        return path


class TaskCollector:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, awaitable: Awaitable[None]) -> None:
        self.count += 1
        awaitable.close()


def _settings(tmp_path: Path) -> BotSettings:
    return BotSettings(
        bot_qq_id="12345",
        napcat_ws_url="ws://127.0.0.1:3001",
        napcat_http_url="http://127.0.0.1:3000",
        napcat_access_token=None,
        backend_url="http://127.0.0.1:8000",
        backend_api_token=None,
        data_dir=tmp_path,
        job_timeout_seconds=30,
        poll_interval_seconds=0.01,
    )


def _group_event(message: list[dict]) -> dict:
    return {
        "message_type": "group",
        "group_id": "10001",
        "user_id": "20001",
        "message": message,
    }


@pytest.mark.asyncio
async def test_handle_group_message_sends_usage_without_number(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event([{"type": "at", "data": {"qq": "12345"}}]),
        _settings(tmp_path),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent == [("10001", "用法：@机器人 JM123456")]
    assert backend.created == []


@pytest.mark.asyncio
async def test_handle_group_message_creates_job(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    tasks = TaskCollector()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " jm123456"}},
            ]
        ),
        _settings(tmp_path),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    assert backend.created == [("123456", "10001", "20001")]
    assert napcat.sent == [("10001", "已接收 JM123456，任务编号：job-123")]
    assert tasks.count == 1


@pytest.mark.asyncio
async def test_upload_success(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeDownloadBackend()

    await _download_and_upload(
        {"job_id": "job-123", "filename": "[JM123456]title.pdf"},
        "123456",
        "10001",
        _settings(tmp_path),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
    )

    assert napcat.upload_attempts == 1
    assert napcat.uploads[0][0] == "10001"
    assert napcat.uploads[0][2] == "[JM123456]title.pdf"
    assert napcat.sent[-1] == ("10001", "JM123456 已完成，PDF 已上传：[JM123456]title.pdf")


@pytest.mark.asyncio
async def test_upload_retries_until_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    napcat = FakeNapCat(upload_failures=2)
    backend = FakeDownloadBackend()

    await _download_and_upload(
        {"job_id": "job-123", "filename": "[JM123456]title.pdf"},
        "123456",
        "10001",
        _settings(tmp_path),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
    )

    assert napcat.upload_attempts == 3
    assert len(napcat.uploads) == 1
    assert napcat.sent[-1][1].startswith("JM123456 已完成")

