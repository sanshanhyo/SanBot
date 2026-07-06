from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from .audit_log import AuditLog, AuditLogConfig
from .models import (
    AlbumPreviewResponse,
    AlbumRankingResponse,
    AlbumSearchRequest,
    AlbumSearchResponse,
    CommandAuditCreate,
    JavRankingResponse,
    JavSearchRequest,
    JavSearchResponse,
    JavVideoResponse,
    JobCreate,
    JobCreateResponse,
    JobResponse,
)
from .javlibrary_service import JavLibraryService, JavLibraryServiceConfig, JavLibraryServiceError
from .task_manager import ActiveJobLimitError, DuplicateJobError, JobManager, JobManagerConfig

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


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s; using %s.", name, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


@dataclass(frozen=True)
class BackendSettings:
    data_dir: Path
    jmcomic_option_path: Path
    max_concurrent_jobs: int
    job_timeout_seconds: int
    preview_timeout_seconds: int
    job_stall_timeout_seconds: int
    job_progress_check_seconds: float
    cache_cleanup_interval_seconds: int
    job_cache_ttl_seconds: int
    bot_download_cache_ttl_seconds: int
    preview_cache_ttl_seconds: int
    audit_retention_days: int
    backend_api_token: str | None
    enable_search: bool
    search_timeout_seconds: int
    search_result_limit: int
    ranking_timeout_seconds: int
    ranking_result_limit: int
    enable_javlibrary: bool
    javlibrary_timeout_seconds: int
    javlibrary_total_timeout_seconds: int
    javlibrary_cache_ttl_seconds: int
    javlibrary_failure_cache_ttl_seconds: int
    javlibrary_not_found_cache_ttl_seconds: int
    javlibrary_blocked_cache_ttl_seconds: int
    javlibrary_timeout_cache_ttl_seconds: int
    javlibrary_base_url: str
    javlibrary_language: str
    javlibrary_provider_order: tuple[str, ...]
    javdb_base_url: str
    javbus_base_url: str
    jav321_base_url: str
    javlibrary_fetcher: str
    javlibrary_user_agent: str | None
    javlibrary_cookie: str | None
    javlibrary_proxy: str | None
    javlibrary_impersonate: str
    javlibrary_retry_times: int
    javlibrary_browser_profile_dir: str | None
    javlibrary_browser_channel: str | None
    javlibrary_browser_headless: bool
    javlibrary_browser_wait_seconds: float
    jav_actor_alias_path: Path | None
    jav_actor_alias_online: bool
    jav_actor_alias_timeout_seconds: float
    jav_actor_alias_candidate_limit: int
    max_active_jobs_per_group: int
    max_active_jobs_per_user: int
    max_album_pages: int

    @classmethod
    def from_env(cls) -> "BackendSettings":
        load_dotenv()
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "./data")),
            jmcomic_option_path=Path(os.getenv("JMCOMIC_OPTION_PATH", "./config/jmcomic-option.yml")),
            max_concurrent_jobs=max(1, _env_int("MAX_CONCURRENT_JOBS", 1)),
            job_timeout_seconds=max(1, _env_int("JOB_TIMEOUT_SECONDS", 1800)),
            preview_timeout_seconds=max(1, _env_int("PREVIEW_TIMEOUT_SECONDS", 30)),
            job_stall_timeout_seconds=max(0, _env_int("JOB_STALL_TIMEOUT_SECONDS", 300)),
            job_progress_check_seconds=max(1.0, _env_float("JOB_PROGRESS_CHECK_SECONDS", 10.0)),
            cache_cleanup_interval_seconds=max(0, _env_int("CACHE_CLEANUP_INTERVAL_SECONDS", 3600)),
            job_cache_ttl_seconds=max(0, _env_int("JOB_CACHE_TTL_SECONDS", 259200)),
            bot_download_cache_ttl_seconds=max(0, _env_int("BOT_DOWNLOAD_CACHE_TTL_SECONDS", 259200)),
            preview_cache_ttl_seconds=max(0, _env_int("PREVIEW_CACHE_TTL_SECONDS", 86400)),
            audit_retention_days=max(0, _env_int("AUDIT_RETENTION_DAYS", 30)),
            backend_api_token=os.getenv("BACKEND_API_TOKEN") or None,
            enable_search=_env_bool("ENABLE_SEARCH", True),
            search_timeout_seconds=max(1, _env_int("SEARCH_TIMEOUT_SECONDS", 20)),
            search_result_limit=max(1, min(10, _env_int("SEARCH_RESULT_LIMIT", 5))),
            ranking_timeout_seconds=max(1, _env_int("RANKING_TIMEOUT_SECONDS", 20)),
            ranking_result_limit=max(1, min(20, _env_int("RANKING_RESULT_LIMIT", 10))),
            enable_javlibrary=_env_bool("ENABLE_JAVLIBRARY", True),
            javlibrary_timeout_seconds=max(1, _env_int("JAVLIBRARY_TIMEOUT_SECONDS", 8)),
            javlibrary_total_timeout_seconds=max(1, _env_int("JAVLIBRARY_TOTAL_TIMEOUT_SECONDS", 15)),
            javlibrary_cache_ttl_seconds=max(0, _env_int("JAVLIBRARY_CACHE_TTL_SECONDS", 604800)),
            javlibrary_failure_cache_ttl_seconds=max(0, _env_int("JAVLIBRARY_FAILURE_CACHE_TTL_SECONDS", 60)),
            javlibrary_not_found_cache_ttl_seconds=max(0, _env_int("JAVLIBRARY_NOT_FOUND_CACHE_TTL_SECONDS", 86400)),
            javlibrary_blocked_cache_ttl_seconds=max(0, _env_int("JAVLIBRARY_BLOCKED_CACHE_TTL_SECONDS", 120)),
            javlibrary_timeout_cache_ttl_seconds=max(0, _env_int("JAVLIBRARY_TIMEOUT_CACHE_TTL_SECONDS", 60)),
            javlibrary_base_url=os.getenv("JAVLIBRARY_BASE_URL", "https://www.javlibrary.com"),
            javlibrary_language=os.getenv("JAVLIBRARY_LANGUAGE", "cn"),
            javlibrary_provider_order=_env_csv(
                "JAVLIBRARY_PROVIDER_ORDER",
                ("javlibrary", "jav321", "javdb", "javbus"),
            ),
            javdb_base_url=os.getenv("JAVDB_BASE_URL", "https://javdb.com"),
            javbus_base_url=os.getenv("JAVBUS_BASE_URL", "https://www.javbus.com"),
            jav321_base_url=os.getenv("JAV321_BASE_URL", "https://www.jav321.com"),
            javlibrary_fetcher=os.getenv("JAVLIBRARY_FETCHER", "curl"),
            javlibrary_user_agent=os.getenv("JAVLIBRARY_USER_AGENT") or None,
            javlibrary_cookie=os.getenv("JAVLIBRARY_COOKIE") or None,
            javlibrary_proxy=os.getenv("JAVLIBRARY_PROXY") or None,
            javlibrary_impersonate=os.getenv("JAVLIBRARY_IMPERSONATE", "random"),
            javlibrary_retry_times=max(1, _env_int("JAVLIBRARY_RETRY_TIMES", 1)),
            javlibrary_browser_profile_dir=os.getenv("JAVLIBRARY_BROWSER_PROFILE_DIR") or None,
            javlibrary_browser_channel=os.getenv("JAVLIBRARY_BROWSER_CHANNEL") or None,
            javlibrary_browser_headless=_env_bool("JAVLIBRARY_BROWSER_HEADLESS", False),
            javlibrary_browser_wait_seconds=max(1.0, _env_float("JAVLIBRARY_BROWSER_WAIT_SECONDS", 60.0)),
            jav_actor_alias_path=Path(os.getenv("JAV_ACTOR_ALIAS_PATH", "./config/actor-aliases.yml")),
            jav_actor_alias_online=_env_bool("JAV_ACTOR_ALIAS_ONLINE", True),
            jav_actor_alias_timeout_seconds=max(0.5, _env_float("JAV_ACTOR_ALIAS_TIMEOUT_SECONDS", 4.0)),
            jav_actor_alias_candidate_limit=max(1, min(12, _env_int("JAV_ACTOR_ALIAS_CANDIDATE_LIMIT", 6))),
            max_active_jobs_per_group=max(0, _env_int("MAX_ACTIVE_JOBS_PER_GROUP", 3)),
            max_active_jobs_per_user=max(0, _env_int("MAX_ACTIVE_JOBS_PER_USER", 1)),
            max_album_pages=max(0, _env_int("MAX_ALBUM_PAGES", 300)),
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
            job_stall_timeout_seconds=settings.job_stall_timeout_seconds,
            progress_interval_seconds=settings.job_progress_check_seconds,
            cache_cleanup_interval_seconds=settings.cache_cleanup_interval_seconds,
            job_cache_ttl_seconds=settings.job_cache_ttl_seconds,
            bot_download_cache_ttl_seconds=settings.bot_download_cache_ttl_seconds,
            preview_cache_ttl_seconds=settings.preview_cache_ttl_seconds,
            max_active_jobs_per_group=settings.max_active_jobs_per_group,
            max_active_jobs_per_user=settings.max_active_jobs_per_user,
        )
    )
    app.state.settings = settings
    app.state.job_manager = manager
    audit_log = AuditLog(AuditLogConfig(data_dir=settings.data_dir, retention_days=settings.audit_retention_days))
    audit_log.initialize()
    app.state.audit_log = audit_log
    javlibrary_service = JavLibraryService(
        JavLibraryServiceConfig(
            data_dir=settings.data_dir,
            base_url=settings.javlibrary_base_url,
            language=settings.javlibrary_language,
            provider_order=settings.javlibrary_provider_order,
            javdb_base_url=settings.javdb_base_url,
            javbus_base_url=settings.javbus_base_url,
            jav321_base_url=settings.jav321_base_url,
            timeout_seconds=settings.javlibrary_timeout_seconds,
            total_timeout_seconds=settings.javlibrary_total_timeout_seconds,
            cache_ttl_seconds=settings.javlibrary_cache_ttl_seconds,
            failure_cache_ttl_seconds=settings.javlibrary_failure_cache_ttl_seconds,
            not_found_cache_ttl_seconds=settings.javlibrary_not_found_cache_ttl_seconds,
            blocked_cache_ttl_seconds=settings.javlibrary_blocked_cache_ttl_seconds,
            timeout_cache_ttl_seconds=settings.javlibrary_timeout_cache_ttl_seconds,
            fetcher=settings.javlibrary_fetcher,
            user_agent=settings.javlibrary_user_agent,
            cookie=settings.javlibrary_cookie,
            proxy=settings.javlibrary_proxy,
            impersonate=settings.javlibrary_impersonate,
            retry_times=settings.javlibrary_retry_times,
            browser_profile_dir=settings.javlibrary_browser_profile_dir,
            browser_channel=settings.javlibrary_browser_channel,
            browser_headless=settings.javlibrary_browser_headless,
            browser_wait_seconds=settings.javlibrary_browser_wait_seconds,
            actor_alias_path=settings.jav_actor_alias_path,
            actor_alias_online=settings.jav_actor_alias_online,
            actor_alias_timeout_seconds=settings.jav_actor_alias_timeout_seconds,
            actor_alias_candidate_limit=settings.jav_actor_alias_candidate_limit,
        )
    )
    javlibrary_service.initialize()
    app.state.javlibrary_service = javlibrary_service
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="SanBot Backend", lifespan=lifespan)


def _manager(request: Request) -> JobManager:
    return request.app.state.job_manager


def _javlibrary_service(request: Request) -> JavLibraryService:
    return request.app.state.javlibrary_service


def _audit_log(request: Request) -> AuditLog:
    return request.app.state.audit_log


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
    settings: BackendSettings = request.app.state.settings
    if (
        settings.max_album_pages > 0
        and payload.page_count is not None
        and payload.page_count > settings.max_album_pages
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"JM{payload.album_id} 页数超过上限 {settings.max_album_pages}，已拒绝加入队列",
                "error_code": "ALBUM_TOO_LARGE",
                "page_count": payload.page_count,
                "limit": settings.max_album_pages,
            },
        )
    try:
        job = await _manager(request).create_job(payload.album_id, payload.group_id, payload.user_id, payload.page_count)
    except DuplicateJobError as exc:
        existing = exc.existing_job
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "你已有进行中的任务，或该 JM 编号已在本群下载中",
                "error_code": "DUPLICATE_ACTIVE_JOB",
                "job_id": existing["job_id"],
                "status": existing["status"],
            },
        ) from exc
    except ActiveJobLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": exc.user_message,
                "error_code": exc.error_code,
            },
        ) from exc
    return JobCreateResponse(job_id=job["job_id"], status=job["status"])


class PreviewWorkerError(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class SearchWorkerError(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class RankingWorkerError(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


@app.get("/api/albums/{album_id}/preview", response_model=AlbumPreviewResponse)
async def get_album_preview(
    album_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> AlbumPreviewResponse:
    _require_api_token(request, authorization)
    if not album_id.isdigit() or len(album_id) > 12:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid album_id")

    settings: BackendSettings = request.app.state.settings
    result_path = settings.data_dir.resolve() / "previews" / f"{uuid.uuid4()}.json"
    try:
        preview = await _run_preview_worker(
            album_id,
            settings.jmcomic_option_path,
            result_path,
            settings.preview_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="获取漫画信息超时，请稍后重试",
        ) from exc
    except PreviewWorkerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.user_message) from exc
    finally:
        result_path.unlink(missing_ok=True)

    return AlbumPreviewResponse(**preview)


@app.post("/api/search", response_model=AlbumSearchResponse)
async def search_albums(
    payload: AlbumSearchRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> AlbumSearchResponse:
    _require_api_token(request, authorization)
    settings: BackendSettings = request.app.state.settings
    if not settings.enable_search:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="搜索功能未启用")

    result_path = settings.data_dir.resolve() / "searches" / f"{uuid.uuid4()}.json"
    limit = min(payload.limit, settings.search_result_limit)
    try:
        result = await _run_search_worker(
            payload.query,
            payload.page,
            limit,
            settings.jmcomic_option_path,
            result_path,
            settings.search_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="搜索超时，请稍后重试",
        ) from exc
    except SearchWorkerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.user_message) from exc
    finally:
        result_path.unlink(missing_ok=True)

    return AlbumSearchResponse(**result)


@app.get("/api/rankings/{period}", response_model=AlbumRankingResponse)
async def get_album_ranking(
    period: str,
    request: Request,
    page: int = Query(default=1, ge=1, le=5),
    limit: int = Query(default=10, ge=1, le=20),
    authorization: str | None = Header(default=None),
) -> AlbumRankingResponse:
    _require_api_token(request, authorization)
    if period not in {"day", "week", "month"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid ranking period")

    settings: BackendSettings = request.app.state.settings
    result_path = settings.data_dir.resolve() / "rankings" / f"{uuid.uuid4()}.json"
    limit = min(limit, settings.ranking_result_limit)
    try:
        result = await _run_ranking_worker(
            period,
            page,
            limit,
            settings.jmcomic_option_path,
            result_path,
            settings.ranking_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="排行榜获取超时，请稍后重试",
        ) from exc
    except RankingWorkerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.user_message) from exc
    finally:
        result_path.unlink(missing_ok=True)

    return AlbumRankingResponse(**result)


@app.get("/api/jav/videos/{code}", response_model=JavVideoResponse)
async def get_jav_video(
    code: str,
    request: Request,
    force_refresh: bool = Query(default=False),
    authorization: str | None = Header(default=None),
) -> JavVideoResponse:
    _require_api_token(request, authorization)
    settings: BackendSettings = request.app.state.settings
    if not settings.enable_javlibrary:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="番号信息查询功能未启用")

    try:
        payload = await asyncio.to_thread(
            _javlibrary_service(request).lookup_video,
            code,
            force_refresh=force_refresh,
        )
    except JavLibraryServiceError as exc:
        status_code = status.HTTP_502_BAD_GATEWAY
        if exc.error_code == "JAV_CODE_INVALID":
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        elif exc.error_code == "JAV_NOT_FOUND":
            status_code = status.HTTP_404_NOT_FOUND
        elif exc.error_code == "JAV_FETCH_TIMEOUT":
            status_code = status.HTTP_504_GATEWAY_TIMEOUT
        raise HTTPException(
            status_code=status_code,
            detail={"message": exc.user_message, "error_code": exc.error_code},
        ) from exc

    return JavVideoResponse(**payload)


@app.post("/api/jav/search", response_model=JavSearchResponse)
async def search_jav_videos(
    payload: JavSearchRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JavSearchResponse:
    _require_api_token(request, authorization)
    settings: BackendSettings = request.app.state.settings
    if not settings.enable_javlibrary:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="番号信息查询功能未启用")

    limit = min(payload.limit, settings.search_result_limit)
    try:
        result = await asyncio.to_thread(
            _javlibrary_service(request).search_videos,
            payload.query,
            page=payload.page,
            limit=limit,
        )
    except JavLibraryServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": exc.user_message, "error_code": exc.error_code},
        ) from exc
    return JavSearchResponse(**result)


@app.post("/api/jav/actors/search", response_model=JavSearchResponse)
async def search_jav_actors(
    payload: JavSearchRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JavSearchResponse:
    _require_api_token(request, authorization)
    settings: BackendSettings = request.app.state.settings
    if not settings.enable_javlibrary:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="番号信息查询功能未启用")

    limit = min(payload.limit, settings.search_result_limit)
    try:
        result = await asyncio.to_thread(
            _javlibrary_service(request).search_actors,
            payload.query,
            page=payload.page,
            limit=limit,
        )
    except JavLibraryServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": exc.user_message, "error_code": exc.error_code},
        ) from exc
    return JavSearchResponse(**result)


@app.get("/api/javdb/rankings/{period}", response_model=JavRankingResponse)
async def get_javdb_ranking(
    period: str,
    request: Request,
    page: int = Query(default=1, ge=1, le=5),
    limit: int = Query(default=10, ge=1, le=20),
    authorization: str | None = Header(default=None),
) -> JavRankingResponse:
    _require_api_token(request, authorization)
    settings: BackendSettings = request.app.state.settings
    if not settings.enable_javlibrary:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="番号信息查询功能未启用")
    if period not in {"day", "week", "month"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid ranking period")

    limit = min(limit, settings.ranking_result_limit)
    try:
        result = await asyncio.to_thread(
            _javlibrary_service(request).get_javdb_ranking,
            period,
            page=page,
            limit=limit,
        )
    except JavLibraryServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": exc.user_message, "error_code": exc.error_code},
        ) from exc
    return JavRankingResponse(**result)


async def _run_preview_worker(
    album_id: str,
    option_path: Path,
    result_path: Path,
    timeout_seconds: int,
) -> dict:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "backend.preview_worker",
        "--album-id",
        album_id,
        "--option-path",
        str(option_path),
        "--result-path",
        str(result_path),
    ]
    kwargs: dict[str, object] = {
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = await asyncio.create_subprocess_exec(*command, **kwargs)
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        await _terminate_process(process)
        raise

    if not result_path.is_file():
        raise PreviewWorkerError(f"获取漫画信息失败，退出码：{process.returncode}")

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PreviewWorkerError("获取漫画信息结果无效") from exc

    if not result.get("ok"):
        raise PreviewWorkerError(result.get("user_message") or "获取漫画信息失败，请稍后重试")
    preview = result.get("preview")
    if not isinstance(preview, dict):
        raise PreviewWorkerError("获取漫画信息结果无效")
    return preview


async def _run_search_worker(
    query: str,
    page: int,
    limit: int,
    option_path: Path,
    result_path: Path,
    timeout_seconds: int,
) -> dict:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "backend.search_worker",
        "--query",
        query,
        "--page",
        str(page),
        "--limit",
        str(limit),
        "--option-path",
        str(option_path),
        "--result-path",
        str(result_path),
    ]
    kwargs: dict[str, object] = {
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = await asyncio.create_subprocess_exec(*command, **kwargs)
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        await _terminate_process(process)
        raise

    if not result_path.is_file():
        raise SearchWorkerError(f"搜索失败，退出码：{process.returncode}")

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SearchWorkerError("搜索结果无效") from exc

    if not result.get("ok"):
        raise SearchWorkerError(result.get("user_message") or "搜索失败，请稍后重试")
    search_result = result.get("result")
    if not isinstance(search_result, dict):
        raise SearchWorkerError("搜索结果无效")
    return search_result


async def _run_ranking_worker(
    period: str,
    page: int,
    limit: int,
    option_path: Path,
    result_path: Path,
    timeout_seconds: int,
) -> dict:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "backend.ranking_worker",
        "--period",
        period,
        "--page",
        str(page),
        "--limit",
        str(limit),
        "--option-path",
        str(option_path),
        "--result-path",
        str(result_path),
    ]
    kwargs: dict[str, object] = {
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = await asyncio.create_subprocess_exec(*command, **kwargs)
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        await _terminate_process(process)
        raise

    if not result_path.is_file():
        raise RankingWorkerError(f"排行榜获取失败，退出码：{process.returncode}")

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RankingWorkerError("排行榜结果无效") from exc

    if not result.get("ok"):
        raise RankingWorkerError(result.get("user_message") or "排行榜获取失败，请稍后重试")
    ranking_result = result.get("result")
    if not isinstance(ranking_result, dict):
        raise RankingWorkerError("排行榜结果无效")
    return ranking_result


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(process.wait(), timeout=5)
        return
    except asyncio.TimeoutError:
        pass

    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    await process.wait()


@app.get("/api/admin/status")
async def admin_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_api_token(request, authorization)
    settings: BackendSettings = request.app.state.settings
    manager = _manager(request)
    snapshot = await _collect_admin_status(settings.data_dir, manager)
    return snapshot


@app.get("/api/admin/queue")
async def admin_queue(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = 20,
) -> dict:
    _require_api_token(request, authorization)
    jobs = await asyncio.to_thread(_manager(request).list_admin_jobs, limit)
    return {"jobs": jobs}


@app.get("/api/admin/history")
async def admin_group_history(
    group_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = 10,
) -> dict:
    _require_api_token(request, authorization)
    if not group_id.isdigit():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id")
    jobs = await asyncio.to_thread(_manager(request).list_group_history, group_id, limit)
    return {"jobs": jobs}


@app.post("/api/audit/events")
async def create_audit_event(
    payload: CommandAuditCreate,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_api_token(request, authorization)
    event = await asyncio.to_thread(_audit_log(request).record_event, **payload.model_dump())
    return {"event": event}


@app.get("/api/admin/audit")
async def admin_audit(
    request: Request,
    authorization: str | None = Header(default=None),
    group_id: str | None = None,
    limit: int = 20,
) -> dict:
    _require_api_token(request, authorization)
    if group_id is not None and not group_id.isdigit():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id")
    events = await asyncio.to_thread(_audit_log(request).list_events, group_id=group_id, limit=limit)
    return {"events": events}


@app.post("/api/admin/cache/cleanup")
async def admin_cleanup_cache(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_api_token(request, authorization)
    manager = _manager(request)
    active_count = await asyncio.to_thread(manager.count_active_jobs)
    if active_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "当前还有下载或转换任务运行，暂不清理缓存",
                "error_code": "ACTIVE_JOBS_RUNNING",
                "active_count": active_count,
            },
        )

    before = await asyncio.to_thread(_directory_size, manager.data_dir)
    stats = await manager.cleanup_cache_once()
    after = await asyncio.to_thread(_directory_size, manager.data_dir)
    return {"stats": stats, "freed_bytes": max(0, before - after)}


@app.post("/api/admin/jobs/{target}/cancel")
async def admin_cancel_job(
    target: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_api_token(request, authorization)
    manager = _manager(request)
    job = await asyncio.to_thread(manager.find_job_by_prefix, target)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found or ambiguous")

    cancelled = await manager.cancel_job(str(job["job_id"]), "任务已由管理员取消")
    if cancelled is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return {"job": cancelled}


async def _collect_admin_status(data_dir: Path, manager: JobManager) -> dict:
    cpu_task = asyncio.create_task(_cpu_percent())
    net_before = await asyncio.to_thread(_read_network_bytes)
    await asyncio.sleep(1)
    net_after = await asyncio.to_thread(_read_network_bytes)
    cpu_percent = await cpu_task

    system_status = await asyncio.to_thread(_collect_admin_status_sync, data_dir, manager)
    system_status["cpu_percent"] = cpu_percent
    system_status["network"] = _network_speed(net_before, net_after)
    return system_status


def _collect_admin_status_sync(data_dir: Path, manager: JobManager) -> dict:
    disk = shutil.disk_usage(data_dir.resolve())
    data_dir = data_dir.resolve()
    jobs = manager.list_admin_jobs(50)
    counts = {
        "queued": 0,
        "downloading": 0,
        "converting": 0,
        "failed": 0,
    }
    for job in jobs:
        status_value = str(job.get("status") or "")
        if status_value in counts:
            counts[status_value] += 1

    return {
        "memory": _memory_status(),
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
        },
        "cache": {
            "data": _directory_size(data_dir),
            "jobs": _directory_size(data_dir / "jobs"),
            "bot_downloads": _directory_size(data_dir / "bot_downloads"),
            "previews": _directory_size(data_dir / "previews"),
            "cover_cache": _directory_size(data_dir / "cover_cache"),
        },
        "jobs": {
            "active": manager.count_active_jobs(),
            **counts,
        },
    }


async def _cpu_percent() -> float | None:
    first = await asyncio.to_thread(_read_cpu_times)
    if first is None:
        return None
    await asyncio.sleep(1)
    second = await asyncio.to_thread(_read_cpu_times)
    if second is None:
        return None
    idle_delta = second["idle"] - first["idle"]
    total_delta = second["total"] - first["total"]
    if total_delta <= 0:
        return None
    busy_delta = max(0, total_delta - idle_delta)
    return round(busy_delta * 100 / total_delta, 1)


def _read_cpu_times() -> dict[str, int] | None:
    stat_path = Path("/proc/stat")
    if not stat_path.is_file():
        return None
    try:
        first_line = stat_path.read_text(encoding="utf-8").splitlines()[0]
        parts = [int(part) for part in first_line.split()[1:]]
    except (IndexError, OSError, ValueError):
        return None
    if len(parts) < 5:
        return None
    idle = parts[3] + parts[4]
    return {"idle": idle, "total": sum(parts)}


def _memory_status() -> dict[str, int] | None:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.is_file():
        return None
    values: dict[str, int] = {}
    try:
        for line in meminfo_path.read_text(encoding="utf-8").splitlines():
            key, raw_value = line.split(":", 1)
            number = int(raw_value.strip().split()[0]) * 1024
            values[key] = number
    except (OSError, ValueError, IndexError):
        return None
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return None
    return {"total": total, "available": available, "used": max(0, total - available)}


def _read_network_bytes() -> dict[str, int] | None:
    dev_path = Path("/proc/net/dev")
    if not dev_path.is_file():
        return None
    rx_total = 0
    tx_total = 0
    try:
        for line in dev_path.read_text(encoding="utf-8").splitlines()[2:]:
            if ":" not in line:
                continue
            interface, raw_data = line.split(":", 1)
            if interface.strip() == "lo":
                continue
            parts = raw_data.split()
            rx_total += int(parts[0])
            tx_total += int(parts[8])
    except (OSError, ValueError, IndexError):
        return None
    return {"rx": rx_total, "tx": tx_total}


def _network_speed(before: dict[str, int] | None, after: dict[str, int] | None) -> dict[str, float | None]:
    if before is None or after is None:
        return {"rx_bytes_per_second": None, "tx_bytes_per_second": None}
    return {
        "rx_bytes_per_second": max(0, after["rx"] - before["rx"]),
        "tx_bytes_per_second": max(0, after["tx"] - before["tx"]),
    }


def _directory_size(path: Path) -> int:
    path = path.resolve()
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0

    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


@app.get("/api/jobs/active", response_model=JobResponse)
async def get_active_job(
    group_id: str,
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JobResponse:
    _require_api_token(request, authorization)
    if not group_id.isdigit() or not user_id.isdigit():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id or user_id")
    job = _manager(request).find_active_job_for_user(group_id, user_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="active job not found")
    return JobResponse(**job)


@app.post("/api/jobs/active/cancel", response_model=JobResponse)
async def cancel_active_job(
    group_id: str,
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JobResponse:
    _require_api_token(request, authorization)
    if not group_id.isdigit() or not user_id.isdigit():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id or user_id")
    job = await _manager(request).cancel_active_job_for_user(group_id, user_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="active job not found")
    return JobResponse(**job)


@app.get("/api/jobs/history")
async def get_user_job_history(
    group_id: str,
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = 5,
) -> dict:
    _require_api_token(request, authorization)
    if not group_id.isdigit() or not user_id.isdigit():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid group_id or user_id")
    jobs = await asyncio.to_thread(_manager(request).list_user_history, group_id, user_id, limit)
    return {"jobs": jobs}


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


@app.post("/api/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JobResponse:
    _require_api_token(request, authorization)
    job = await _manager(request).cancel_job(job_id)
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
