from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Awaitable

import pytest

import bot.main as bot_main
from bot.main import BotSettings, BotState, _download_and_upload, handle_group_message, monitor_job
from bot.napcat_client import NapCatAPIError


class FakeNapCat:
    def __init__(self, upload_failures: int = 0, image_failures: int = 0) -> None:
        self.sent: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, Path, str]] = []
        self.videos: list[tuple[str, str]] = []
        self.group_member_count = 80
        self.upload_attempts = 0
        self.upload_failures = upload_failures
        self.image_attempts = 0
        self.image_failures = image_failures

    async def send_group_msg(self, group_id: str, message: str | list[dict]) -> dict:
        self.sent.append((group_id, message))
        return {"status": "ok", "retcode": 0}

    async def send_group_image(self, group_id: str, image_url: str) -> dict:
        self.image_attempts += 1
        if self.image_attempts <= self.image_failures:
            raise NapCatAPIError("image failed")
        self.sent.append((group_id, f"IMAGE:{image_url}"))
        return {"status": "ok", "retcode": 0}

    async def send_group_video(self, group_id: str, video_url: str) -> dict:
        self.videos.append((group_id, video_url))
        self.sent.append((group_id, f"VIDEO:{video_url}"))
        return {"status": "ok", "retcode": 0}

    async def get_group_info(self, group_id: str) -> dict:
        return {"status": "ok", "retcode": 0, "data": {"group_id": group_id, "member_count": self.group_member_count}}

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
        self.searches: list[tuple[str, int, int]] = []
        self.rankings: list[tuple[str, int, int]] = []
        self.av_searches: list[tuple[str, int, int]] = []
        self.actor_searches: list[tuple[str, int, int]] = []
        self.db_rankings: list[tuple[str, int, int]] = []
        self.jav_queries: list[str] = []
        self.jav_force_refreshes: list[bool] = []
        self.tg_bound_refs: list[tuple[str, str]] = []
        self.tg_fetches: list[tuple[str, int]] = []
        self.tg_group_fetches: list[tuple[list[str], int]] = []
        self.tg_groups: list[str] = ["10001"]
        self.tg_channels: list[dict] = [
            {
                "id": 1,
                "group_id": "10001",
                "channel_ref": "example_channel",
                "channel_id": "-100123456",
                "channel_title": "Example Channel",
                "enabled": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        self.tg_items: list[dict] = []
        self.tg_items_by_group: dict[str, list[dict]] = {}
        self.cancelled: list[str] = []
        self.admin_cancellations: list[str] = []
        self.active_queries: list[tuple[str, str]] = []
        self.cancelled_active: list[tuple[str, str]] = []
        self.audit_events: list[dict] = []
        self.active_job: dict | None = None
        self.page_count = page_count
        self.trailer_url: str | None = "https://javdb.com/trailers/SSIS-123.mp4"
        self.trailer_page_url: str | None = None
        self.trailer_requires_login = False

    async def health(self) -> dict:
        return {"status": "ok"}

    async def create_audit_event(self, payload: dict) -> dict:
        self.audit_events.append(payload)
        return {"event": payload}

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

    async def search_albums(self, query: str, page: int = 1, limit: int = 5) -> dict:
        self.searches.append((query, page, limit))
        return {
            "query": query,
            "page": page,
            "total": 2,
            "results": [
                {"album_id": "111111", "title": "First Search Hit", "tags": ["tag1"]},
                {"album_id": "222222", "title": "Second Search Hit", "tags": ["tag2"]},
            ],
        }

    async def get_ranking(self, period: str, page: int = 1, limit: int = 10) -> dict:
        self.rankings.append((period, page, limit))
        return {
            "period": period,
            "period_label": {"day": "日榜", "week": "周榜", "month": "月榜"}[period],
            "page": page,
            "total": 2,
            "results": [
                {"rank": 1, "album_id": "111111", "title": "First Ranking Hit", "tags": []},
                {"rank": 2, "album_id": "222222", "title": "Second Ranking Hit", "tags": []},
            ],
        }

    async def search_jav_videos(self, query: str, page: int = 1, limit: int = 5) -> dict:
        self.av_searches.append((query, page, limit))
        return {
            "query": query,
            "page": page,
            "total": 2,
            "results": [
                {
                    "code": "SSIS-123",
                    "title": "A Chinese Title",
                    "url": "https://javdb.com/v/abc",
                    "source": "javdb",
                    "actors": ["三上悠亚"],
                },
                {
                    "code": "ABP-456",
                    "title": "Another Chinese Title",
                    "url": "https://javdb.com/v/def",
                    "source": "javdb",
                    "actors": [],
                },
            ],
        }

    async def search_jav_actors(self, query: str, page: int = 1, limit: int = 5) -> dict:
        self.actor_searches.append((query, page, limit))
        return {
            "query": query,
            "page": page,
            "total": 2,
            "results": [
                {
                    "code": "SSIS-123",
                    "title": "Actor Search Hit",
                    "url": "https://javdb.com/v/abc",
                    "source": "javdb",
                    "actors": ["三上悠亚"],
                },
                {
                    "code": "ABP-456",
                    "title": "Another Actor Hit",
                    "url": "https://javdb.com/v/def",
                    "source": "javdb",
                    "actors": ["三上悠亚", "Alice"],
                },
            ],
        }

    async def get_javdb_ranking(self, period: str, page: int = 1, limit: int = 10) -> dict:
        self.db_rankings.append((period, page, limit))
        return {
            "period": period,
            "period_label": {"day": "日榜", "week": "周榜", "month": "月榜"}[period],
            "page": page,
            "total": 2,
            "results": [
                {"rank": 1, "code": "SSIS-123", "title": "First DB Hit", "source": "javdb", "actors": ["三上悠亚"]},
                {"rank": 2, "code": "ABP-456", "title": "Second DB Hit", "source": "javdb", "actors": []},
            ],
        }

    async def get_jav_video(self, code: str, *, force_refresh: bool = False) -> dict:
        self.jav_queries.append(code)
        self.jav_force_refreshes.append(force_refresh)
        return {
            "code": "SSIS-123",
            "title": "SSIS-123 A Sample Title",
            "url": "https://www.javlibrary.com/cn/?v=abc123",
            "cover_url": "https://example.test/jav-cover.jpg",
            "release_date": "2026-01-02",
            "runtime_minutes": 123,
            "studio": "A Studio",
            "publisher": "A Publisher",
            "series": "A Series",
            "director": "A Director",
            "rating": 4.5,
            "actors": ["Alice", "Bob"],
            "genres": ["Drama", "HD"],
            "trailer_url": self.trailer_url,
            "trailer_page_url": self.trailer_page_url,
            "trailer_requires_login": self.trailer_requires_login,
            "preview_image_urls": ["https://javdb.com/samples/1.jpg", "https://javdb.com/samples/2.jpg"],
            "resource_page_url": "https://javdb.com/v/abc123",
        }

    async def bind_tg_channel(self, group_id: str, channel_ref: str) -> dict:
        self.tg_bound_refs.append((group_id, channel_ref))
        channel = dict(self.tg_channels[0])
        channel["group_id"] = group_id
        channel["channel_ref"] = channel_ref.replace("https://t.me/", "")
        return channel

    async def list_tg_channels(self, group_id: str) -> dict:
        return {"channels": self.tg_channels}

    async def list_tg_groups(self) -> dict:
        return {"groups": self.tg_groups}

    async def fetch_tg_latest(self, group_id: str, limit: int = 5) -> dict:
        self.tg_fetches.append((group_id, limit))
        return {"channels": self.tg_channels, "items": self.tg_items, "skipped": 0}

    async def fetch_tg_latest_groups(self, group_ids: list[str], limit: int = 5) -> dict:
        group_id_list = [str(group_id) for group_id in group_ids]
        self.tg_group_fetches.append((group_id_list, limit))
        groups = []
        for group_id in group_id_list:
            channels = [
                channel
                for channel in self.tg_channels
                if str(channel.get("group_id") or group_id) == group_id
            ]
            if not channels and self.tg_channels:
                channels = [dict(self.tg_channels[0], group_id=group_id)]
            items = self.tg_items_by_group.get(group_id, self.tg_items)
            groups.append({"group_id": group_id, "channels": channels, "items": items, "skipped": 0})
        return {"groups": groups}

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

    async def get_admin_status(self) -> dict:
        return {
            "cpu_percent": 12.5,
            "memory": {"used": 512 * 1024 * 1024, "total": 2 * 1024 * 1024 * 1024},
            "disk": {
                "used": 10 * 1024 * 1024 * 1024,
                "total": 40 * 1024 * 1024 * 1024,
                "free": 30 * 1024 * 1024 * 1024,
            },
            "cache": {"data": 1000, "jobs": 200, "bot_downloads": 300},
            "network": {"tx_bytes_per_second": 1024, "rx_bytes_per_second": 2048},
            "jobs": {"downloading": 1, "queued": 2, "converting": 0},
        }

    async def get_admin_queue(self, limit: int = 20) -> dict:
        return {
            "jobs": [
                {
                    "job_id": "abcdef1234567890",
                    "album_id": "123456",
                    "group_id": "10001",
                    "user_id": "20001",
                    "status": "downloading",
                    "downloaded_files": 50,
                    "total_files": 100,
                }
            ]
        }

    async def get_admin_audit(self, group_id: str | None = None, limit: int = 20) -> dict:
        return {
            "events": [
                {
                    "id": 1,
                    "created_at": "2026-07-06T12:00:00+00:00",
                    "group_id": group_id or "10001",
                    "user_id": "20001",
                    "command": "ok",
                    "target": "JM123456",
                    "status": "received",
                    "error_code": None,
                    "duration_ms": 0,
                }
            ]
        }

    async def get_group_history(self, group_id: str, limit: int = 10) -> dict:
        return {
            "jobs": [
                {
                    "job_id": "group-history-job",
                    "album_id": "333333",
                    "group_id": group_id,
                    "user_id": "20002",
                    "status": "completed",
                    "updated_at": "2026-06-27T12:00:00+00:00",
                }
            ]
        }

    async def get_user_history(self, group_id: str, user_id: str, limit: int = 5) -> dict:
        return {
            "jobs": [
                {
                    "job_id": "user-history-job",
                    "album_id": "123456",
                    "group_id": group_id,
                    "user_id": user_id,
                    "status": "completed",
                    "updated_at": "2026-06-27T12:00:00+00:00",
                },
                {
                    "job_id": "failed-history-job",
                    "album_id": "222222",
                    "group_id": group_id,
                    "user_id": user_id,
                    "status": "failed",
                    "error_code": "JOB_TIMEOUT",
                    "updated_at": "2026-06-27T12:10:00+00:00",
                },
            ]
        }

    async def cleanup_cache(self) -> dict:
        return {"freed_bytes": 2048, "stats": {"job_dirs": 1, "bot_downloads": 2, "previews": 3}}

    async def admin_cancel_job(self, target: str) -> dict:
        self.admin_cancellations.append(target)
        return {"job": {"job_id": "abcdef1234567890", "album_id": "123456", "status": "failed"}}


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


def _settings(tmp_path: Path, enable_search: bool = True) -> BotSettings:
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
        enable_search=enable_search,
        enable_tg_mirror=True,
        search_confirm_timeout_seconds=60,
    )


def _group_event(message: list[dict], user_id: str = "20001", role: str = "member") -> dict:
    return {
        "message_type": "group",
        "group_id": "10001",
        "user_id": user_id,
        "sender": {"role": role},
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

    max_upload_bytes = 2000
    parts = bot_main._split_pdf_for_upload(pdf_path, "album.pdf", max_upload_bytes=max_upload_bytes)

    assert len(parts) == 3
    assert [name for _path, name in parts] == [
        "part01-of03_album.pdf",
        "part02-of03_album.pdf",
        "part03-of03_album.pdf",
    ]
    for part_path, _part_name in parts:
        assert part_path.stat().st_size <= max_upload_bytes
        with pikepdf.Pdf.open(part_path) as part_pdf:
            assert len(part_pdf.pages) == 1


def test_jav_still_dimension_filter_skips_tiny_images(tmp_path: Path) -> None:
    from PIL import Image

    tiny_path = tmp_path / "tiny.jpg"
    large_path = tmp_path / "large.jpg"
    Image.new("RGB", (120, 90), "white").save(tiny_path)
    Image.new("RGB", (640, 360), "white").save(large_path)

    assert bot_main._is_jav_still_large_enough(tiny_path, 300, 200) is False
    assert bot_main._is_jav_still_large_enough(large_path, 300, 200) is True


def test_jav_trailer_headers_include_cookie_and_logs_are_sanitized(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), jav_trailer_cookie="session=secret")
    payload = {"url": "https://javdb.com/v/abc123"}

    headers = bot_main._jav_trailer_request_headers(payload, settings)

    assert headers["Cookie"] == "session=secret"
    assert bot_main._sanitize_ffmpeg_message("open https://example.test/a.m3u8?sign=secret failed") == "open <url> failed"


def test_jav_trailer_hls_variant_selects_highest_bandwidth() -> None:
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720
high/index.m3u8
"""

    assert (
        bot_main._select_hls_variant_url(playlist, "https://media.example.test/master.m3u8")
        == "https://media.example.test/high/index.m3u8"
    )
    assert bot_main._hls_variant_urls(playlist, "https://media.example.test/master.m3u8") == [
        "https://media.example.test/high/index.m3u8",
        "https://media.example.test/low/index.m3u8",
    ]


def test_jav_trailer_hls_uri_rewrite_and_extensions() -> None:
    assert (
        bot_main._replace_hls_uri_attribute('#EXT-X-KEY:METHOD=AES-128,URI="../key.bin",IV=0x1', "key_000.key")
        == '#EXT-X-KEY:METHOD=AES-128,URI="key_000.key",IV=0x1'
    )
    assert bot_main._hls_local_extension("https://media.example.test/segment.m4s?token=secret", ".ts") == ".m4s"
    assert bot_main._hls_local_extension("https://media.example.test/segment?token=secret", ".ts") == ".ts"


def test_jav_trailer_ffmpeg_reconnect_only_for_remote_inputs(tmp_path: Path) -> None:
    local_command = bot_main._ffmpeg_trailer_command("ffmpeg", str(tmp_path / "playlist.m3u8"), tmp_path / "out.mp4", "", transcode=False)
    remote_command = bot_main._ffmpeg_trailer_command(
        "ffmpeg",
        "https://media.example.test/playlist.m3u8",
        tmp_path / "out.mp4",
        "",
        transcode=False,
    )

    assert "-reconnect" not in local_command
    assert "-reconnect" in remote_command


def test_jav_trailer_hls_relative_assets_inherit_playlist_query() -> None:
    playlist_url = "https://media.example.test/hls/index.m3u8?token=secret"

    assert bot_main._resolve_hls_asset_url(playlist_url, "seg-001.ts") == (
        "https://media.example.test/hls/seg-001.ts?token=secret"
    )
    assert bot_main._resolve_hls_asset_url(playlist_url, "seg-001.ts?part=1") == (
        "https://media.example.test/hls/seg-001.ts?part=1"
    )
    assert bot_main._resolve_hls_asset_url(playlist_url, "https://cdn.example.test/seg-001.ts") == (
        "https://cdn.example.test/seg-001.ts"
    )


def test_jav_trailer_hls_materializes_local_playlist(tmp_path: Path) -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def fetch_bytes(self, url: str) -> bytes:
            self.urls.append(url)
            return f"asset:{url}".encode()

    playlist = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="../key.bin"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:1.0,
seg-000.ts?token=secret
#EXTINF:1.0,
https://cdn.example.test/video/seg-001.ts
#EXT-X-ENDLIST
"""
    fetcher = FakeFetcher()

    local_playlist = bot_main._rewrite_hls_playlist_to_local(
        playlist,
        "https://media.example.test/path/index.m3u8",
        tmp_path,
        fetcher,  # type: ignore[arg-type]
    )

    assert local_playlist.read_text(encoding="utf-8") == """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="key_000.bin"
#EXT-X-MAP:URI="map_000.mp4"
#EXTINF:1.0,
segment_00000.ts
#EXTINF:1.0,
segment_00001.ts
#EXT-X-ENDLIST
"""
    assert (tmp_path / "key_000.bin").is_file()
    assert (tmp_path / "map_000.mp4").is_file()
    assert (tmp_path / "segment_00000.ts").is_file()
    assert (tmp_path / "segment_00001.ts").is_file()
    assert fetcher.urls == [
        "https://media.example.test/key.bin",
        "https://media.example.test/path/init.mp4",
        "https://media.example.test/path/seg-000.ts?token=secret",
        "https://cdn.example.test/video/seg-001.ts",
    ]


def test_jav_trailer_hls_skips_one_missing_segment(tmp_path: Path) -> None:
    class FakeFetcher:
        def fetch_bytes(self, url: str) -> bytes:
            if "seg-001" in url:
                raise bot_main.JavTrailerError("missing", "TRAILER_HLS_ASSET_NOT_FOUND")
            return b"asset"

    playlist = """#EXTM3U
#EXTINF:1.0,
seg-000.ts
#EXTINF:1.0,
seg-001.ts
#EXTINF:1.0,
seg-002.ts
#EXTINF:1.0,
seg-003.ts
#EXT-X-ENDLIST
"""

    local_playlist = bot_main._rewrite_hls_playlist_to_local(
        playlist,
        "https://media.example.test/path/index.m3u8",
        tmp_path,
        FakeFetcher(),  # type: ignore[arg-type]
    )

    content = local_playlist.read_text(encoding="utf-8")
    assert "segment_00000.ts" in content
    assert "segment_00001.ts" in content
    assert "segment_00002.ts" in content
    assert content.count("#EXTINF") == 3
    assert len(list(tmp_path.glob("segment_*.ts"))) == 3


def test_part_filename_is_truncated_by_utf8_bytes() -> None:
    filename = "[JM434803]" + ("譚雅奉旨生子之事" * 30) + ".pdf"

    part_name = bot_main._part_filename(filename, 1, 3)

    assert part_name == "JM434803_part01-of03.pdf"
    assert len(part_name.encode("utf-8")) <= bot_main.MAX_FILENAME_BYTES


@pytest.mark.asyncio
async def test_handle_group_message_sends_unknown_for_unknown_command(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " hello"}},
            ]
        ),
        _settings(tmp_path),
        BotState(pending_downloads={}),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent == [("10001", "我看不懂你在输入什么(つд⊂)！输入‘帮助’获取命令列表")]
    assert backend.created == []


@pytest.mark.asyncio
async def test_astrbot_coexist_delegates_unknown_at_message_silently(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    tasks = TaskCollector()
    settings = replace(
        _settings(tmp_path),
        enable_astrbot_coexist=True,
        astrbot_coexist_group_ids={"10001"},
    )

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 今晚吃什么？"}},
            ]
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    assert napcat.sent == []
    assert tasks.count == 1


@pytest.mark.asyncio
async def test_astrbot_coexist_delegates_empty_at_silently(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(
        _settings(tmp_path),
        enable_astrbot_coexist=True,
        astrbot_coexist_group_ids={"10001"},
    )

    await handle_group_message(
        _group_event([{"type": "at", "data": {"qq": "12345"}}]),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent == []


@pytest.mark.asyncio
async def test_astrbot_coexist_does_not_affect_unlisted_group(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(
        _settings(tmp_path),
        enable_astrbot_coexist=True,
        astrbot_coexist_group_ids={"99999"},
    )

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " hello"}},
            ]
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent == [("10001", "我看不懂你在输入什么(つд⊂)！输入‘帮助’获取命令列表")]


@pytest.mark.asyncio
async def test_astrbot_coexist_keeps_invalid_sanbot_like_command(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(
        _settings(tmp_path),
        enable_astrbot_coexist=True,
        astrbot_coexist_group_ids={"10001"},
    )

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JMabc"}},
            ]
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent == [("10001", "我看不懂你在输入什么(つд⊂)！输入‘帮助’获取命令列表")]


@pytest.mark.asyncio
async def test_empty_at_sends_home_message(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(
        _settings(tmp_path),
        bot_display_name="测试机器人",
        manager_name="散山肆水HyO",
        manager_qq="2456014618",
    )

    await handle_group_message(
        _group_event([{"type": "at", "data": {"qq": "12345"}}]),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "这里是「测试机器人」" in napcat.sent[-1][1]
    assert "散山肆水HyO（QQ：2456014618）" in napcat.sent[-1][1]
    assert "https://github.com/sanshanhyo/SanBot" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_group_whitelist_blocks_unlisted_group(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), allowed_group_ids={"99999"})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM123456"}},
            ]
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent == []
    assert backend.previewed == []
    assert backend.audit_events[-1]["command"] == "blocked_group"
    assert backend.audit_events[-1]["error_code"] == "GROUP_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_feature_whitelist_blocks_jm_download(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), jm_download_allowed_group_ids={"99999"})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM123456"}},
            ]
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.previewed == []
    assert any("暂时没有在本群开放" in message for _group, message in napcat.sent)
    assert backend.audit_events[-1]["status"] == "blocked"
    assert backend.audit_events[-1]["error_code"] == "FEATURE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_feature_whitelist_blocks_tg_commands(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), tg_mirror_allowed_group_ids={"99999"})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " TG绑定 https://t.me/example_channel"}},
            ],
            role="admin",
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.tg_bound_refs == []
    assert any("暂时没有在本群开放" in message for _group, message in napcat.sent)
    assert backend.audit_events[-1]["error_code"] == "FEATURE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_jav_action_whitelist_hides_trailer_entry(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), jav_trailer_allowed_group_ids={"99999"})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV SSIS-123"}},
            ]
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    messages = "\n".join(message for _group, message in napcat.sent)
    assert "预告片" not in messages


@pytest.mark.asyncio
async def test_help_and_features_commands(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    state = BotState()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 帮助"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 功能"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    help_message = napcat.sent[-2][1]
    assert help_message.startswith("IMAGE:")
    help_image = Path(help_message.removeprefix("IMAGE:"))
    assert help_image == (tmp_path / "assets" / "main.png").resolve()
    assert help_image.stat().st_size == bot_main.HELP_IMAGE_PATH.stat().st_size
    assert "当前功能" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_help_falls_back_to_text_when_image_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    napcat = FakeNapCat()
    monkeypatch.setattr(bot_main, "HELP_IMAGE_PATH", tmp_path / "missing.png")

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 帮助"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        FakeCreateBackend(),  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "SanBot 帮助" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_user_history_command(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 我的任务"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "你的最近任务" in napcat.sent[-1][1]
    assert "JM123456 已完成" in napcat.sent[-1][1]
    assert "JM222222 错误：JOB_TIMEOUT" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_group_history_requires_admin(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 最近任务"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent[-1] == ("10001", "这个命令需要群主、群管理员或机器人管理者执行。")


@pytest.mark.asyncio
async def test_group_admin_can_query_group_history(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 最近任务"}},
            ],
            role="admin",
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "本群最近任务" in napcat.sent[-1][1]
    assert "JM333333 已完成" in napcat.sent[-1][1]
    assert "用户：20002" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_member_cannot_bind_tg_channel(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " TG绑定 https://t.me/example_channel"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent[-1] == ("10001", "这个命令需要群主、群管理员或机器人管理者执行。")
    assert backend.tg_bound_refs == []


@pytest.mark.asyncio
async def test_group_admin_can_bind_and_list_tg_channels(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    state = BotState()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " TG绑定 https://t.me/example_channel"}},
            ],
            role="admin",
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " TG列表"}},
            ],
            role="admin",
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.tg_bound_refs == [("10001", "https://t.me/example_channel")]
    assert "TG 频道已绑定" in napcat.sent[-2][1]
    assert "Example Channel" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_group_admin_can_fetch_latest_tg_media(tmp_path: Path) -> None:
    media_path = tmp_path / "tg.jpg"
    media_path.write_bytes(b"fake image")
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.tg_items = [
        {
            "id": 7,
            "channel_id": "-100123456",
            "channel_title": "Example Channel",
            "message_id": 42,
            "media_type": "image",
            "file_path": str(media_path),
            "filename": "tg.jpg",
            "file_size": media_path.stat().st_size,
            "caption": "hello",
            "message_url": "https://t.me/example_channel/42",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " TG最新 1"}},
            ],
            role="admin",
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.tg_fetches == [("10001", 1)]
    sent_text = "\n".join(message for _group, message in napcat.sent)
    assert "hello" in sent_text
    assert "来自 TG" not in sent_text
    assert "类型：" not in sent_text
    assert "大小：" not in sent_text
    assert "https://t.me/example_channel/42" not in sent_text
    assert ("10001", f"IMAGE:{media_path.resolve()}") in napcat.sent


@pytest.mark.asyncio
async def test_tg_media_without_caption_sends_no_extra_description(tmp_path: Path) -> None:
    media_path = tmp_path / "tg.jpg"
    media_path.write_bytes(b"fake image")
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.tg_items = [
        {
            "id": 7,
            "channel_id": "-100123456",
            "channel_title": "Example Channel",
            "message_id": 42,
            "media_type": "image",
            "file_path": str(media_path),
            "filename": "tg.jpg",
            "file_size": media_path.stat().st_size,
            "caption": "",
            "message_url": "https://t.me/example_channel/42",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " TG最新 1"}},
            ],
            role="admin",
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert [message for _group, message in napcat.sent] == [
        "正在拉取 TG 最近 1 条图片/视频，稍等一下下(｀・ω・´)",
        f"IMAGE:{media_path.resolve()}",
        "TG 拉取完成：已发送 1 个，失败 0 个。",
    ]


@pytest.mark.asyncio
async def test_tg_auto_fetch_sends_new_media_silently(tmp_path: Path) -> None:
    media_path = tmp_path / "tg.jpg"
    media_path.write_bytes(b"fake image")
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.tg_items = [
        {
            "id": 7,
            "channel_id": "-100123456",
            "channel_title": "Example Channel",
            "message_id": 42,
            "media_type": "image",
            "file_path": str(media_path),
            "filename": "tg.jpg",
            "file_size": media_path.stat().st_size,
            "caption": "hello",
            "message_url": "https://t.me/example_channel/42",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    settings = replace(
        _settings(tmp_path),
        enable_tg_auto_fetch=True,
        tg_auto_fetch_limit=3,
    )

    result = await bot_main._run_tg_auto_fetch_once(
        settings,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
    )

    assert backend.tg_fetches == []
    assert backend.tg_group_fetches == [(["10001"], 3)]
    assert result == {"groups": 1, "sent": 1, "failed": 0}
    assert [message for _group, message in napcat.sent] == ["hello", f"IMAGE:{media_path.resolve()}"]


@pytest.mark.asyncio
async def test_tg_auto_fetch_no_media_is_silent(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), enable_tg_auto_fetch=True)

    result = await bot_main._run_tg_auto_fetch_once(
        settings,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
    )

    assert backend.tg_fetches == []
    assert backend.tg_group_fetches == [(["10001"], 5)]
    assert result == {"groups": 1, "sent": 0, "failed": 0}
    assert napcat.sent == []


@pytest.mark.asyncio
async def test_tg_auto_fetch_sends_same_channel_media_to_every_bound_group(tmp_path: Path) -> None:
    media_path = tmp_path / "tg.jpg"
    media_path.write_bytes(b"fake image")
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.tg_groups = ["10001", "10002", "10003"]
    backend.tg_channels = [
        {
            "id": index,
            "group_id": group_id,
            "channel_ref": "example_channel",
            "channel_id": "-100123456",
            "channel_title": "Example Channel",
            "enabled": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        for index, group_id in enumerate(backend.tg_groups, start=1)
    ]
    backend.tg_items_by_group = {
        group_id: [
            {
                "id": index,
                "channel_id": "-100123456",
                "channel_title": "Example Channel",
                "message_id": 42,
                "media_type": "image",
                "file_path": str(media_path),
                "filename": "tg.jpg",
                "file_size": media_path.stat().st_size,
                "caption": f"hello {group_id}",
                "message_url": "https://t.me/example_channel/42",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        for index, group_id in enumerate(backend.tg_groups, start=1)
    }
    settings = replace(
        _settings(tmp_path),
        allowed_group_ids=set(backend.tg_groups),
        tg_mirror_allowed_group_ids=set(backend.tg_groups),
        enable_tg_auto_fetch=True,
        tg_auto_fetch_limit=3,
    )

    result = await bot_main._run_tg_auto_fetch_once(
        settings,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
    )

    assert backend.tg_group_fetches == [(["10001", "10002", "10003"], 3)]
    assert result == {"groups": 3, "sent": 3, "failed": 0}
    for group_id in backend.tg_groups:
        assert (group_id, f"hello {group_id}") in napcat.sent
        assert (group_id, f"IMAGE:{media_path.resolve()}") in napcat.sent


@pytest.mark.asyncio
async def test_ranking_command_sends_ranking_results(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM日榜"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.rankings == [("day", 1, 10)]
    assert napcat.sent[0] == ("10001", "正在获取 JM 日榜，榜单搬运中(｡･ω･｡)ﾉ")
    assert "JM 日榜 Top 榜" in napcat.sent[1][1]
    assert "1. JM111111 First Ranking Hit" in napcat.sent[1][1]
    assert "2. JM222222 Second Ranking Hit" in napcat.sent[1][1]


@pytest.mark.asyncio
async def test_jav_command_sends_video_metadata(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.jav_queries == ["SSIS123"]
    assert napcat.sent[0] == ("10001", "正在查询 SSIS123 的番号信息，小本本翻页中(｡･ω･｡)ﾉ")
    assert "番号信息：SSIS-123" in napcat.sent[1][1]
    assert "标题：SSIS-123 A Sample Title" in napcat.sent[1][1]
    assert "演员：Alice / Bob" in napcat.sent[1][1]
    assert "可选操作来啦" in napcat.sent[2][1]
    assert "回复“预告片”" in napcat.sent[2][1]
    assert "回复“资源页”" in napcat.sent[2][1]
    assert "在线播放" not in napcat.sent[2][1]
    await asyncio.sleep(0)
    assert napcat.sent[-1] == ("10001", "IMAGE:https://example.test/jav-cover.jpg")


@pytest.mark.asyncio
async def test_jav_trailer_action_sends_video(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    state = BotState()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "预告片"}}]),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert ("10001", "https://javdb.com/trailers/SSIS-123.mp4") in napcat.videos


@pytest.mark.asyncio
async def test_jav_trailer_action_converts_m3u8_to_local_mp4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.trailer_url = "https://javdb.com/trailers/SSIS-123.m3u8"
    state = BotState()

    async def fake_prepare_jav_trailer_mp4(
        payload: dict,
        trailer_url: str,
        dest_dir: Path,
        settings: BotSettings,
    ) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        mp4_path = dest_dir / "[SSIS-123] 预告片.mp4"
        mp4_path.write_bytes(b"fake mp4")
        return mp4_path

    monkeypatch.setattr(bot_main, "_prepare_jav_trailer_mp4", fake_prepare_jav_trailer_mp4)

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "预告片"}}]),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert any("转成 MP4" in message for _group, message in napcat.sent)
    assert napcat.videos
    assert napcat.videos[-1][1].endswith(".mp4")
    assert ".m3u8" not in napcat.videos[-1][1]


@pytest.mark.asyncio
async def test_jav_trailer_action_refreshes_metadata_before_sending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.trailer_url = "https://javdb.com/trailers/OLD.m3u8"
    state = BotState()
    used_trailer_urls: list[str] = []

    async def fake_prepare_jav_trailer_mp4(
        payload: dict,
        trailer_url: str,
        dest_dir: Path,
        settings: BotSettings,
    ) -> Path:
        used_trailer_urls.append(trailer_url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        mp4_path = dest_dir / "[SSIS-123] 预告片.mp4"
        mp4_path.write_bytes(b"fake mp4")
        return mp4_path

    monkeypatch.setattr(bot_main, "_prepare_jav_trailer_mp4", fake_prepare_jav_trailer_mp4)

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    backend.trailer_url = "https://javdb.com/trailers/NEW.m3u8"
    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "预告片"}}]),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.jav_force_refreshes == [False, True]
    assert used_trailer_urls == ["https://javdb.com/trailers/NEW.m3u8"]


@pytest.mark.asyncio
async def test_jav_trailer_action_explains_login_requirement(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    backend.trailer_url = None
    backend.trailer_requires_login = True
    state = BotState()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "预告片"}}]),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert any("回复“预告片”" in message for _group, message in napcat.sent)
    assert any("需要 JavDB 登录" in message for _group, message in napcat.sent)


@pytest.mark.asyncio
async def test_jav_resource_action_sends_javdb_page(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    state = BotState()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "资源页"}}]),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert any("JavDB 外部页面：https://javdb.com/v/abc123" in message for _group, message in napcat.sent)


@pytest.mark.asyncio
async def test_missav_action_requires_whitelisted_small_group(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    state = BotState()
    settings = replace(
        _settings(tmp_path),
        enable_missav_link=True,
        missav_allowed_group_ids={"10001"},
        missav_max_group_members=150,
    )

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    assert any("回复“在线播放”" in message for _group, message in napcat.sent)

    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "在线播放"}}]),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    assert any("https://missav.live/SSIS-123" in message for _group, message in napcat.sent)

    big_group_napcat = FakeNapCat()
    big_group_napcat.group_member_count = 151
    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        settings,
        BotState(),
        big_group_napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    assert not any("回复“在线播放”" in message for _group, message in big_group_napcat.sent)


@pytest.mark.asyncio
async def test_jav_stills_action_is_opt_in(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    state = BotState()
    settings = replace(
        _settings(tmp_path),
        enable_jav_stills=True,
        jav_stills_max_count=1,
        enable_jav_stills_pdf=False,
    )

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "剧照"}}]),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert any("先给你投喂 1 张剧照预览" in message for _group, message in napcat.sent)
    assert ("10001", "IMAGE:https://javdb.com/samples/1.jpg") in napcat.sent


@pytest.mark.asyncio
async def test_jav_stills_action_uploads_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    state = BotState()
    settings = replace(_settings(tmp_path), enable_jav_stills=True, enable_jav_stills_pdf=True)

    async def fake_build_jav_stills_pdf(
        payload: dict,
        urls: list[str],
        dest_dir: Path,
        settings: BotSettings,
    ) -> tuple[Path, str, int]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = dest_dir / "[SSIS-123] 剧照.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")
        return pdf_path, pdf_path.name, len(urls)

    monkeypatch.setattr(bot_main, "_build_jav_stills_pdf", fake_build_jav_stills_pdf)

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JAV ssis123"}},
            ]
        ),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "剧照"}}]),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert any("正在把 2 张剧照打包成 PDF" in message for _group, message in napcat.sent)
    assert napcat.uploads
    assert napcat.uploads[0][2] == "[SSIS-123] 剧照.pdf"
    assert any("剧照 PDF 已上传" in message for _group, message in napcat.sent)


@pytest.mark.asyncio
async def test_av_search_command_sends_javdb_results(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " AV搜索 中文标题"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.av_searches == [("中文标题", 1, 5)]
    assert napcat.sent[0] == ("10001", "正在搜索 AV “中文标题”，检索小马达启动(｀・ω・´)")
    assert "AV 搜索结果：中文标题" in napcat.sent[1][1]
    assert "SSIS-123 A Chinese Title 演员：三上悠亚 来源：javdb" in napcat.sent[1][1]


@pytest.mark.asyncio
async def test_actor_search_command_sends_javdb_results(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 演员搜索 三上悠亚"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.actor_searches == [("三上悠亚", 1, 5)]
    assert napcat.sent[0] == ("10001", "正在翻小本本找演员“三上悠亚”(｡･ω･｡)ﾉ")
    assert "演员搜索结果：三上悠亚" in napcat.sent[1][1]
    assert "SSIS-123 Actor Search Hit 演员：三上悠亚 来源：javdb" in napcat.sent[1][1]


@pytest.mark.asyncio
async def test_db_ranking_command_sends_javdb_ranking(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " DB日榜"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.db_rankings == [("day", 1, 10)]
    assert napcat.sent[0] == ("10001", "正在获取 JavDB 日榜，榜单搬运中(｡･ω･｡)ﾉ")
    assert "JavDB 日榜" in napcat.sent[1][1]
    assert "1. SSIS-123 First DB Hit 演员：三上悠亚 来源：javdb" in napcat.sent[1][1]


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
    assert "标题是A Test Album" in napcat.sent[1][1]
    assert ("10001", "20001") in state.pending_downloads
    await asyncio.sleep(0)
    assert napcat.sent[2] == ("10001", "IMAGE:https://example.test/cover.jpg")


@pytest.mark.asyncio
async def test_oversized_album_is_rejected_before_pending_download(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend(page_count=301)
    tasks = TaskCollector()
    state = BotState(pending_downloads={})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM123456"}},
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
    assert state.pending_downloads == {}
    assert "已自动拒绝加入队列" in napcat.sent[-1][1]
    assert "当前上限：300 页" in napcat.sent[-1][1]
    await asyncio.sleep(0)
    assert all(message != "IMAGE:https://example.test/cover.jpg" for _group, message in napcat.sent)


@pytest.mark.asyncio
async def test_cover_url_failure_sends_cached_cover(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_download_cover_image(cover_url: str, cache_dir: Path, album_id: str) -> Path:
        assert cover_url == "https://example.test/cover.jpg"
        cover_path = cache_dir / f"JM{album_id}.jpg"
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.write_bytes(b"fake image")
        return cover_path.resolve()

    monkeypatch.setattr(bot_main, "_download_cover_image", fake_download_cover_image)
    napcat = FakeNapCat(image_failures=1)
    backend = FakeCreateBackend()
    state = BotState(pending_downloads={})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM123456"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    for _ in range(5):
        await asyncio.sleep(0)

    assert napcat.image_attempts == 2
    expected_cover_path = tmp_path.resolve() / "cover_cache" / "JM123456.jpg"
    assert "标题是A Test Album" in napcat.sent[1][1]
    assert napcat.sent[2] == ("10001", f"IMAGE:{expected_cover_path}")


@pytest.mark.asyncio
async def test_slow_cover_send_does_not_block_preview(tmp_path: Path) -> None:
    class SlowImageNapCat(FakeNapCat):
        def __init__(self) -> None:
            super().__init__()
            self.release_image = asyncio.Event()

        async def send_group_image(self, group_id: str, image_url: str) -> dict:
            self.image_attempts += 1
            await self.release_image.wait()
            self.sent.append((group_id, f"IMAGE:{image_url}"))
            return {"status": "ok", "retcode": 0}

    napcat = SlowImageNapCat()
    backend = FakeCreateBackend()
    state = BotState(pending_downloads={})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM123456"}},
            ]
        ),
        _settings(tmp_path),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )
    await asyncio.sleep(0)

    assert napcat.image_attempts == 1
    assert "标题是A Test Album" in napcat.sent[1][1]
    assert len(napcat.sent) == 2

    napcat.release_image.set()
    await asyncio.sleep(0)
    assert napcat.sent[-1] == ("10001", "IMAGE:https://example.test/cover.jpg")


@pytest.mark.asyncio
async def test_search_command_can_be_disabled_by_config(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    state = BotState(pending_downloads={})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM搜索 戦乙女"}},
            ]
        ),
        _settings(tmp_path, enable_search=False),
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.searches == []
    assert napcat.sent[-1] == ("10001", "搜索功能还没有开启，联系我的管理者开启吧QAQ")
    assert state.pending_searches == {}


@pytest.mark.asyncio
async def test_search_result_selection_sends_album_preview(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    tasks = TaskCollector()
    state = BotState(pending_downloads={})
    settings = _settings(tmp_path, enable_search=True)

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM搜索 戦乙女"}},
            ]
        ),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    assert backend.searches == [("戦乙女", 1, 5)]
    assert ("10001", "20001") in state.pending_searches
    assert napcat.sent[-1] == (
        "10001",
        "搜索结果：戦乙女\n1. JM111111 First Search Hit\n2. JM222222 Second Search Hit\n回复 1-2 选择，回复“取消”放弃。",
    )

    await handle_group_message(
        _group_event([{"type": "text", "data": {"text": "1"}}]),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tasks,
    )

    assert backend.previewed == ["111111"]
    assert state.pending_searches == {}
    assert ("10001", "20001") in state.pending_downloads
    assert napcat.sent[-2] == ("10001", "已选择 JM111111，我先获取封面和页数给你确认。")
    assert "标题是A Test Album" in napcat.sent[-1][1]
    assert tasks.count == 0
    await asyncio.sleep(0)
    assert napcat.sent[-1] == ("10001", "IMAGE:https://example.test/cover.jpg")


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
async def test_manager_can_query_admin_status(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), manager_qq_ids={"2456014618"})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 状态"}},
            ],
            user_id="2456014618",
            role="member",
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "服务器状态" in napcat.sent[-1][1]
    assert "CPU：12.5%" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_group_admin_can_query_queue(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 队列"}},
            ],
            role="admin",
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "当前队列" in napcat.sent[-1][1]
    assert "JM123456 下载中（50%）" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_group_admin_can_query_audit_log(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 审计"}},
            ],
            role="admin",
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "本群最近命令审计" in napcat.sent[-1][1]
    assert "JM 预览 已接收 用户：20001 目标：JM123456" in napcat.sent[-1][1]
    assert backend.audit_events[-1]["command"] == "admin:audit"


@pytest.mark.asyncio
async def test_member_cannot_run_admin_status(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 状态"}},
            ]
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent[-1] == ("10001", "这个命令需要群主、群管理员或机器人管理者执行。")


@pytest.mark.asyncio
async def test_cleanup_cache_requires_manager(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 清理缓存"}},
            ],
            role="owner",
        ),
        _settings(tmp_path),
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert napcat.sent[-1] == ("10001", "清理缓存属于维护操作，只允许机器人管理者执行。")


@pytest.mark.asyncio
async def test_manager_can_cleanup_cache(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), manager_qq_ids={"2456014618"})

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 清理缓存"}},
            ],
            user_id="2456014618",
        ),
        settings,
        BotState(),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "缓存清理完成，释放 2.0KB" in napcat.sent[-1][1]


@pytest.mark.asyncio
async def test_cooldown_blocks_repeated_new_command(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), user_command_cooldown_seconds=60)
    state = BotState()

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
        TaskCollector(),
    )
    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " JM654321"}},
            ]
        ),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert backend.previewed == ["123456"]
    assert napcat.sent[-1] == ("10001", "别急别急，60 秒后再发新任务或搜索吧。")


@pytest.mark.asyncio
async def test_admin_cancel_uploading_job_marks_cancelled(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeCreateBackend()
    settings = replace(_settings(tmp_path), manager_qq_ids={"2456014618"})
    state = BotState(
        uploading_jobs={
            "abcdef1234567890": bot_main.UploadingJob(
                job_id="abcdef1234567890",
                album_id="123456",
                group_id="10001",
                user_id="20001",
                started_at=0,
            )
        }
    )

    await handle_group_message(
        _group_event(
            [
                {"type": "at", "data": {"qq": "12345"}},
                {"type": "text", "data": {"text": " 取消 abcdef12"}},
            ],
            user_id="2456014618",
        ),
        settings,
        state,
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        TaskCollector(),
    )

    assert "abcdef1234567890" in state.cancelled_uploads
    assert backend.admin_cancellations == []
    assert "已请求取消上传" in napcat.sent[-1][1]


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
    assert napcat.uploads[0][2] == "JM123456.pdf"
    assert napcat.uploads[0][1].parent.name == "_upload"
    assert napcat.uploads[0][1].name == "upload_01.pdf"
    assert napcat.sent[-1] == ("10001", "锵锵！JM123456 已完成啦ʕง•ᴥ•ʔ，请你查收⸜(* ॑꒳ ॑* )⸝")
    assert not (tmp_path / "bot_downloads" / "job-123").exists()


@pytest.mark.asyncio
async def test_upload_can_be_cancelled_by_admin_state(tmp_path: Path) -> None:
    napcat = FakeNapCat()
    backend = FakeDownloadBackend()
    state = BotState(cancelled_uploads={"job-123"})

    await _download_and_upload(
        {"job_id": "job-123", "filename": "[JM123456]title.pdf", "user_id": "20001"},
        "123456",
        "10001",
        _settings(tmp_path),
        napcat,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        state=state,
    )

    assert napcat.upload_attempts == 0
    assert napcat.sent[-1] == ("10001", "JM123456 上传已由管理员取消。")
    assert state.uploading_jobs == {}
    assert state.cancelled_uploads == set()


@pytest.mark.asyncio
async def test_large_upload_uses_split_parts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_prepare_upload_files(
        pdf_path: Path,
        _filename: str,
        _max_upload_bytes: int,
        _max_filename_bytes: int,
        _album_id: str | None,
    ) -> list[tuple[Path, str]]:
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


@pytest.mark.asyncio
async def test_large_failed_upload_splits_once_after_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    def fake_split_for_retry(
        _pdf_path: Path,
        _filename: str,
        _max_filename_bytes: int,
        _album_id: str | None,
    ) -> list[tuple[Path, str]]:
        part1 = tmp_path / "retry-part1.pdf"
        part2 = tmp_path / "retry-part2.pdf"
        part1.write_bytes(b"%PDF-1.4\nretry1")
        part2.write_bytes(b"%PDF-1.4\nretry2")
        return [(part1, "JM123456_part01-of02.pdf"), (part2, "JM123456_part02-of02.pdf")]

    source_pdf = tmp_path / "large.pdf"
    with source_pdf.open("wb") as file:
        file.seek(int(bot_main.DEFAULT_MAX_UPLOAD_BYTES * 0.8))
        file.write(b"\0")

    monkeypatch.setattr(bot_main, "_split_pdf_for_retry", fake_split_for_retry)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    napcat = FakeNapCat(upload_failures=bot_main.DEFAULT_UPLOAD_RETRIES)

    ok = await bot_main._upload_item_with_fallback(
        napcat,  # type: ignore[arg-type]
        "10001",
        source_pdf,
        "JM123456.pdf",
        tmp_path,
        "job-123",
        "123456",
        bot_main.MAX_UPLOAD_FILENAME_BYTES,
        bot_main.DEFAULT_UPLOAD_RETRIES,
        label="upload_01",
    )

    assert ok is True
    assert napcat.upload_attempts > bot_main.DEFAULT_UPLOAD_RETRIES
    assert [upload[2] for upload in napcat.uploads] == [
        "JM123456_part01-of02.pdf",
        "JM123456_part02-of02.pdf",
    ]
    assert any("拆得更细" in str(message) for _group_id, message in napcat.sent)
