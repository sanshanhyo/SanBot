from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class JavLibrarySearchItem:
    code: str
    title: str
    url: str
    cover_url: str | None = None
    source: str = "javlibrary"
    rank: int | None = None
    release_date: str | None = None
    actors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JavLibraryVideo:
    code: str
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
    actors: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    rating: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
