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


class AlbumPreviewResponse(BaseModel):
    album_id: str
    title: str
    cover_url: str | None = None
    page_count: int | None = None
    estimated_seconds: int | None = None
    estimated_text: str
