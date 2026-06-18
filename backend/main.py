from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse

from .models import JobCreate, JobCreateResponse, JobResponse
from .task_manager import DuplicateJobError, JobManager, JobManagerConfig

logger = logging.getLogger(__name__)


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


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
class BackendSettings:
    data_dir: Path
    jmcomic_option_path: Path
    max_concurrent_jobs: int
    job_timeout_seconds: int
    backend_api_token: str | None

    @classmethod
    def from_env(cls) -> "BackendSettings":
        load_dotenv()
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "./data")),
            jmcomic_option_path=Path(os.getenv("JMCOMIC_OPTION_PATH", "./config/jmcomic-option.yml")),
            max_concurrent_jobs=max(1, _env_int("MAX_CONCURRENT_JOBS", 1)),
            job_timeout_seconds=max(1, _env_int("JOB_TIMEOUT_SECONDS", 1800)),
            backend_api_token=os.getenv("BACKEND_API_TOKEN") or None,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = BackendSettings.from_env()
    manager = JobManager(
        JobManagerConfig(
            data_dir=settings.data_dir,
            option_path=settings.jmcomic_option_path,
            max_concurrent_jobs=settings.max_concurrent_jobs,
            job_timeout_seconds=settings.job_timeout_seconds,
        )
    )
    app.state.settings = settings
    app.state.job_manager = manager
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="QQBot JMComic Backend", lifespan=lifespan)


def _manager(request: Request) -> JobManager:
    return request.app.state.job_manager


def _require_api_token(request: Request, authorization: str | None) -> None:
    token = request.app.state.settings.backend_api_token
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/jobs", response_model=JobCreateResponse)
async def create_job(
    payload: JobCreate,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JobCreateResponse:
    _require_api_token(request, authorization)
    try:
        job = await _manager(request).create_job(payload.album_id, payload.group_id, payload.user_id)
    except DuplicateJobError as exc:
        existing = exc.existing_job
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "该 JM 编号已有进行中的任务",
                "job_id": existing["job_id"],
                "status": existing["status"],
            },
        ) from exc
    return JobCreateResponse(job_id=job["job_id"], status=job["status"])


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JobResponse:
    _require_api_token(request, authorization)
    job = _manager(request).get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobResponse(**job)


@app.get("/api/jobs/{job_id}/file")
async def download_file(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    _require_api_token(request, authorization)
    result = _manager(request).get_completed_file(job_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not ready")
    file_path, filename = result
    return FileResponse(path=file_path, filename=filename, media_type="application/pdf")


def main() -> None:
    load_dotenv()
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("BACKEND_HOST", "127.0.0.1"),
        port=_env_int("BACKEND_PORT", 8000),
    )


if __name__ == "__main__":
    main()
