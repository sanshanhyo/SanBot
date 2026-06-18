from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend import downloader
from backend.models import JobStatus
from backend.task_manager import JobManager, JobManagerConfig


@pytest.mark.asyncio
async def test_download_failure_marks_job_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_download(_album_id: str, _option_path: Path, _job_dir: Path) -> Path:
        raise downloader.DownloadError("下载失败，请稍后重试")

    monkeypatch.setattr(downloader, "download_album_pdf", fail_download)
    manager = JobManager(
        JobManagerConfig(
            data_dir=tmp_path / "data",
            option_path=tmp_path / "jmcomic-option.yml",
            max_concurrent_jobs=1,
            job_timeout_seconds=5,
        )
    )

    await manager.start()
    try:
        job = await manager.create_job("123456", "10001", "20001")
        await asyncio.wait_for(manager.join(), timeout=1)
        stored = manager.get_job(job["job_id"])
    finally:
        await manager.stop()

    assert stored is not None
    assert stored["status"] == JobStatus.FAILED.value
    assert stored["error_message"] == "下载失败，请稍后重试"


def test_pdf_not_generated_raises(tmp_path: Path) -> None:
    with pytest.raises(downloader.PdfGenerationError, match="未找到输出文件"):
        downloader._finalize_single_pdf("123456", tmp_path)

