from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import downloader
from .models import JobStatus

logger = logging.getLogger(__name__)


class DuplicateJobError(Exception):
    def __init__(self, existing_job: dict[str, Any]) -> None:
        super().__init__("duplicate active job")
        self.existing_job = existing_job


@dataclass(frozen=True)
class JobManagerConfig:
    data_dir: Path
    option_path: Path
    max_concurrent_jobs: int = 1
    job_timeout_seconds: int = 1800


class JobManager:
    def __init__(self, config: JobManagerConfig) -> None:
        self.config = config
        self.data_dir = config.data_dir.resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.db_path = self.data_dir / "jobs.sqlite3"
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []

    def initialize(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    album_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    filename TEXT,
                    file_path TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_album_group
                ON jobs(album_id, group_id)
                WHERE status IN ('queued', 'downloading', 'converting')
                """
            )
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?
                WHERE status IN (?, ?)
                """,
                (
                    JobStatus.QUEUED.value,
                    self._now(),
                    JobStatus.DOWNLOADING.value,
                    JobStatus.CONVERTING.value,
                ),
            )

    async def start(self) -> None:
        self.initialize()
        for job_id in self._queued_job_ids():
            await self._queue.put(job_id)

        worker_count = max(1, self.config.max_concurrent_jobs)
        self._workers = [
            asyncio.create_task(self._worker(worker_id), name=f"job-worker-{worker_id}")
            for worker_id in range(worker_count)
        ]

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def join(self) -> None:
        await self._queue.join()

    async def create_job(self, album_id: str, group_id: str, user_id: str) -> dict[str, Any]:
        existing = self.find_active_job(album_id, group_id)
        if existing is not None:
            raise DuplicateJobError(existing)

        now = self._now()
        job_id = str(uuid.uuid4())
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, album_id, group_id, user_id, status,
                        filename, file_path, error_message, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        job_id,
                        album_id,
                        group_id,
                        user_id,
                        JobStatus.QUEUED.value,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            existing = self.find_active_job(album_id, group_id)
            if existing is not None:
                raise DuplicateJobError(existing) from exc
            raise

        await self._queue.put(job_id)
        created = self.get_job(job_id)
        if created is None:
            raise RuntimeError("created job disappeared")
        return created

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT job_id, album_id, group_id, user_id, status,
                       filename, file_path, error_message, created_at, updated_at
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_completed_file(self, job_id: str) -> tuple[Path, str] | None:
        job = self.get_job(job_id)
        if job is None or job["status"] != JobStatus.COMPLETED.value:
            return None
        file_path = job.get("file_path")
        filename = job.get("filename")
        if not file_path or not filename:
            return None
        path = Path(file_path).resolve()
        if not path.is_file() or not path.is_relative_to(self.jobs_dir.resolve()):
            return None
        return path, filename

    def find_active_job(self, album_id: str, group_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT job_id, album_id, group_id, user_id, status,
                       filename, file_path, error_message, created_at, updated_at
                FROM jobs
                WHERE album_id = ?
                  AND group_id = ?
                  AND status IN (?, ?, ?)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    album_id,
                    group_id,
                    JobStatus.QUEUED.value,
                    JobStatus.DOWNLOADING.value,
                    JobStatus.CONVERTING.value,
                ),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    async def _worker(self, worker_id: int) -> None:
        logger.info("Job worker %s started.", worker_id)
        while True:
            job_id = await self._queue.get()
            try:
                await self._process_job(job_id)
            except Exception:
                logger.exception("Unexpected worker failure for job %s", job_id)
                self._mark_failed(job_id, "任务执行失败，请查看服务日志")
            finally:
                self._queue.task_done()

    async def _process_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job is None:
            logger.warning("Ignoring missing job %s", job_id)
            return
        if job["status"] not in {JobStatus.QUEUED.value, JobStatus.DOWNLOADING.value}:
            return

        album_id = job["album_id"]
        job_dir = self.jobs_dir / job_id
        self._update_status(job_id, JobStatus.DOWNLOADING)

        try:
            pdf_path = await asyncio.wait_for(
                asyncio.to_thread(
                    downloader.download_album_pdf,
                    album_id,
                    self.config.option_path,
                    job_dir,
                ),
                timeout=self.config.job_timeout_seconds,
            )
            self._update_status(job_id, JobStatus.CONVERTING)
            self._mark_completed(job_id, pdf_path)
        except asyncio.TimeoutError:
            logger.exception("Job %s timed out.", job_id)
            self._mark_failed(job_id, "下载超时，请稍后重试")
        except downloader.DownloaderError as exc:
            logger.exception("Job %s failed in downloader.", job_id)
            self._mark_failed(job_id, exc.user_message)
        except Exception:
            logger.exception("Job %s failed unexpectedly.", job_id)
            self._mark_failed(job_id, "下载或转换失败，请查看服务日志")

    def _mark_completed(self, job_id: str, pdf_path: Path) -> None:
        pdf_path = pdf_path.resolve()
        if not pdf_path.is_file() or pdf_path.stat().st_size <= 0:
            self._mark_failed(job_id, "PDF 生成失败：最终文件无效")
            return
        if not pdf_path.is_relative_to(self.jobs_dir.resolve()):
            self._mark_failed(job_id, "PDF 生成失败：输出路径异常")
            return

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, filename = ?, file_path = ?, error_message = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    JobStatus.COMPLETED.value,
                    pdf_path.name,
                    str(pdf_path),
                    self._now(),
                    job_id,
                ),
            )

    def _mark_failed(self, job_id: str, error_message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error_message = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (JobStatus.FAILED.value, error_message, self._now(), job_id),
            )

    def _update_status(self, job_id: str, status: JobStatus) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status.value, self._now(), job_id),
            )

    def _queued_job_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE status = ? ORDER BY created_at ASC",
                (JobStatus.QUEUED.value,),
            ).fetchall()
        return [row["job_id"] for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

