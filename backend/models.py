from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    COMPLETED = "completed"
    FAILED = "failed"


class JobCreate(BaseModel):
    album_id: str = Field(pattern=r"^\d{1,12}$")
    group_id: str = Field(pattern=r"^\d+$")
    user_id: str = Field(pattern=r"^\d+$")
    page_count: int | None = Field(default=None, ge=1)


class JobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobResponse(BaseModel):
    job_id: str
    album_id: str
    group_id: str
    user_id: str
    status: JobStatus
    filename: str | None = None
    error_message: str | None = None
    error_code: str | None = None
    downloaded_files: int = 0
    total_files: int = 0
    progress_message: str | None = None


class CommandAuditCreate(BaseModel):
    group_id: str = Field(pattern=r"^\d+$")
    user_id: str = Field(pattern=r"^\d+$")
    command: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=128)
    status: str = Field(min_length=1, max_length=32)
    error_code: str | None = Field(default=None, max_length=64)
    duration_ms: int = Field(default=0, ge=0)


class AlbumPreviewResponse(BaseModel):
    album_id: str
    title: str
    cover_url: str | None = None
    page_count: int | None = None
    page_count_is_estimated: bool = False
    estimated_seconds: int | None = None
    estimated_text: str


class AlbumSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=40)
    page: int = Field(default=1, ge=1, le=5)
    limit: int = Field(default=5, ge=1, le=10)


class AlbumSearchItem(BaseModel):
    album_id: str = Field(pattern=r"^\d{1,12}$")
    title: str
    tags: list[str] = Field(default_factory=list)


class AlbumSearchResponse(BaseModel):
    query: str
    page: int
    total: int
    results: list[AlbumSearchItem]


class AlbumRankingItem(BaseModel):
    rank: int = Field(ge=1)
    album_id: str = Field(pattern=r"^\d{1,12}$")
    title: str
    tags: list[str] = Field(default_factory=list)


class AlbumRankingResponse(BaseModel):
    period: str = Field(pattern=r"^(day|week|month)$")
    period_label: str
    page: int
    total: int
    results: list[AlbumRankingItem]


class JavSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=60)
    page: int = Field(default=1, ge=1, le=5)
    limit: int = Field(default=5, ge=1, le=10)


class JavSearchItem(BaseModel):
    code: str
    title: str
    url: str
    source: str = "javdb"
    cover_url: str | None = None
    rank: int | None = None
    release_date: str | None = None
    actors: list[str] = Field(default_factory=list)


class JavSearchResponse(BaseModel):
    query: str
    page: int
    total: int
    results: list[JavSearchItem]


class JavRankingResponse(BaseModel):
    period: str = Field(pattern=r"^(day|week|month)$")
    period_label: str
    page: int
    total: int
    results: list[JavSearchItem]


class JavVideoResponse(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+){1,3}$")
    title: str
    url: str
    source: str = "javlibrary"
    cover_url: str | None = None
    release_date: str | None = None
    runtime_minutes: int | None = None
    director: str | None = None
    studio: str | None = None
    publisher: str | None = None
    series: str | None = None
    actors: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    rating: float | None = None
    trailer_url: str | None = None
    trailer_page_url: str | None = None
    trailer_requires_login: bool = False
    preview_image_urls: list[str] = Field(default_factory=list)
    resource_page_url: str | None = None
    cache_hit: bool = False
