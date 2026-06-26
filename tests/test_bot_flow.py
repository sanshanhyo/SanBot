from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable

import pytest

import bot.main as bot_main
from bot.main import BotSettings, BotState, _download_and_upload, handle_group_message, monitor_job
from bot.napcat_client import NapCatAPIError


class FakeNapCat:
    def __init__(self, upload_failures: int = 0) -> None:
        self.sent: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, Path, str]] = []
        self.upload_attempts = 0
        self.upload_failures = upload_failures

    async def send_group_msg(self, group_id: str, message: str | list[dict]) -> dict:
        self.sent.append((group_id, message))
        return {"status": "ok", "retcode": 0}

    async def send_group_image(self, group_id: str, image_url: str) -> dict:
        self.sent.append((group_id, f"IMAGE:{image_url}"))
        return {"status": "ok", "retcode": 0}

    async def upload_group_file(self, group_id: str, file_path: str | Path, name: str) -> dict:
        self.upload_attempts += 1
        if self.upload_attempts <= self.upload_failures:
            raise NapCatAPIError("upload failed")
        self.uploads.append((group_id, Path(file_path), name))
        return {"status": "ok", "retcode": 0}


class FakeCreateBackend:
    def __init__(self, page_count: int | None = 80) -> None:
        self.created: list[tuple[str, str, str, int | None]] = []
        self.previewed: list[str] = []
        self.cancelled: list[str] = []
        self.active_queries: list[tuple[str, str]] = []
        self.cancelled_active: list[tuple[str, str]] = []
        self.active_job: dict | None = None
        self.page_count = page_count

    async def get_active_job(self, group_id: str, user_id: str) -> dict | None:
        self.active_queries.append((group_id, user_id))
        return self.active_job

    async def cancel_active_job(self, group_id: str, user_id: str) -> dict | None:
        self.cancelled_active.append((group_id, user_id))
        return self.active_job

    async def get_album_preview(self, album_id: str) -> dict:
        self.previewed.append(album_id)
        return {
            "album_id": album_id,
            "title": "A Test Album",
            "cover_url": "https://example.test/cover.jpg",
            "page_count": self.page_count,
            "estimated_seconds": 300,
            "estimated_text": "预计约 5-8 分钟",
        }

    async def create_job(
        self,
        album_id: str,
        group_id: str,
        user_id: str,
        page_count: int | None = None,
    ) -> dict:
        self.created.append((album_id, group_id, user_id, page_count))
        return {"job_id": "job-123", "status": "queued"}

    async def cancel_job(self, job_id: str) -> dict:
        self.cancelled.append(job_id)
        return {"job_id": job_id, "status": "failed"}


class FakeDownloadBackend:
    async def download_file(self, job_id: str, dest_path: str | Path) -> Path:
        path = Path(dest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n")
        return path


class FakeFailedJobBackend:
    async def get_job(self, _job_id: str) -> dict:
        return {
            "job_id": "job-123",
            "status": "failed",
            "error_message": "下载失败，请稍后重试",
            "error_code": "JM_DOWNLOAD_FAILED",
        }


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


def test_split_pdf_for_upload_creates_valid_parts(tmp_path: Path) -> None:
    import img2pdf
    import pikepdf
    from PIL import Image

    image_paths: list[Path] = []
    for index in range(3):
        image_path = tmp_path / f"{index}.jpg"
        Image.new("RGB", (32, 32), "white").save(image_path)
        image_paths.append(image_path)

    pdf_path = tmp_path / "album.pdf"
    pdf_path.write_bytes(img2pdf.convert([str(path) for path in image_paths]))

    parts = bot_main._split_pdf_for_upload(pdf_path, "album.pdf", max_upload_bytes=100)

    assert len(parts) == 3
    assert [name for _path, name in parts] == [
        "part01-of03_album.pdf",
        "part02-of03_album.pdf",
        "part03-of03_album.pdf",
    ]
    for part_path, _part_name in parts:
        with pikepdf.Pdf.open(part_path) as part_pdf:
            assert len(part_pdf.pages) == 1


def test_part_filename_is_truncated_by_utf8_bytes() -> None:
    filename = "[JM434803]" + ("譚雅奉旨生子之事" * 30) + ".pdf"

    part_name = bot_main._part_filename(filename, 1, 3)

    assert part_name.startswith("part01-of03_")
    assert part_name.endswith(".pdf")
    assert len(part_name.encode("utf-8")) <= bot_main.MAX_FILENAME_BYTES


@pytest.mark.asyncio
async def test_handle_group_message_sends_usage_without_number(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event([{"type": "at", "data": {"qq": "12345"}}]),
        _settings(tmp_path),
        BotState(pending_downloads={}),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent == [("10001", "用法：@我 JM123456")]
    assert backend.created == []


@pytest.mark.asyncio
async def test_handle_group_message_sends_preview_without_creating_job(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    tasks = TaskCollector()
    state = BotState(pending_downloads={})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " jm123456"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    assert backend.previewed == ["123456"]
    assert backend.created == []
    assert tasks.count == 0
    assert napcat.sent[0] == ("10001", "我已经接收到 JM123456，正在用全力获取信息中(绝对没有偷懒！)...")
    assert napcat.sent[1] == ("10001", "IMAGE:https://example.test/cover.jpg")
    assert "标题是A Test Album" in napcat.sent[2][1]
    assert ("10001", "20001") in state.pending_downloads


@pytest.mark.asyncio
async def test_confirm_download_creates_job(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    tasks = TaskCollector()
    state = BotState(pending_downloads={})
    settings = _settings(tmp_path)

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM123456"}},
            ]
        ),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "下载"}}]),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    assert backend.created == [("123456", "10001", "20001", 80)]
    assert napcat.sent[-1] == (
        "10001",
        "我已经接收到 JM123456 啦，任务编号是 job-123\n我预计时间是 预计约 5-8 分钟，请你稍等片刻啦",
    )
    assert tasks.count == 1
    assert state.pending_downloads == {}


@pytest.mark.asyncio
async def test_large_album_requires_second_confirmation(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend(page_count=120)
    tasks = TaskCollector()
    state = BotState(pending_downloads={})
    settings = _settings(tmp_path)

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM123456"}},
            ]
        ),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "下载"}}]),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    assert backend.created == []
    assert tasks.count == 0
    assert "超过 100 页" in napcat.sent[-1][1]
    assert state.pending_downloads[("10001", "20001")].large_warning_sent is True

    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "下载"}}]),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    assert backend.created == [("123456", "10001", "20001", 120)]
    assert tasks.count == 1
    assert state.pending_downloads == {}


@pytest.mark.asyncio
async def test_new_jm_is_rejected_when_user_has_active_download(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.active_job = {"job_id": "job-123", "album_id": "123456", "status": "downloading"}
    state = BotState()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM654321"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.previewed == []
    assert napcat.sent[-1] == (
        "10001",
        "JM123456 已经正在下载或排队中啦！回复“取消下载”可以停止当前任务。",
    )


@pytest.mark.asyncio
async def test_active_download_can_be_cancelled(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.active_job = {"job_id": "job-123", "album_id": "123456", "status": "downloading"}
    state = BotState()

    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "取消下载"}}]),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.cancelled_active == [("10001", "20001")]
    assert napcat.sent[-1] == ("10001", "已取消 JM123456 任务。")


@pytest.mark.asyncio
async def test_failed_job_message_includes_error_code(tmp_path: Path) -> None:
    napcat = FakeNapCat()

    await monitor_job(
        "job-123",
        "123456",
        "10001",
        _settings(tmp_path),
        napcat,  # type: ignore[arg-type]
        FakeFailedJobBackend(),  # type: ignore[arg-type]
    )

    assert napcat.sent[-1] == (
        "10001",
        "JM123456 任务失败｡ﾟヽ(ﾟ´Д`)ﾉﾟ｡\n下载失败，请稍后重试\n报错码：JM_DOWNLOAD_FAILED",
    )


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
    assert napcat.sent[-1] == ("10001", "锵锵！JM123456 已完成啦ʕง•ᴥ•ʔ，请你查收⸜(* ॑꒳ ॑* )⸝")


@pytest.mark.asyncio
async def test_large_upload_uses_split_parts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_prepare_upload_files(pdf_path: Path, _filename: str, _max_upload_bytes: int) -> list[tuple[Path, str]]:
        part1 = pdf_path.parent / "part1.pdf"
        part2 = pdf_path.parent / "part2.pdf"
        part1.write_bytes(b"%PDF-1.4\npart1")
        part2.write_bytes(b"%PDF-1.4\npart2")
        return [(part1, "part1.pdf"), (part2, "part2.pdf")]

    monkeypatch.setattr(bot_main, "_prepare_upload_files", fake_prepare_upload_files)
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

    assert [upload[2] for upload in napcat.uploads] == ["part1.pdf", "part2.pdf"]
    assert "已拆分为 2 个文件上传" in napcat.sent[-2][1]
    assert napcat.sent[-1] == (
        "10001",
        "锵锵！JM123456 已完成啦ʕง•ᴥ•ʔ，由于文件过大，PDF进行了分卷，请你查收⸜(* ॑꒳ ॑* )⸝",
    )


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
    assert "JM123456 已完成" in napcat.sent[-1][1]
