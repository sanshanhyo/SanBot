from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.tg_mirror import (
    BOT_API_MAX_DOWNLOAD_BYTES,
    TelegramMirrorConfig,
    TelegramMirrorError,
    TelegramMirrorService,
    _bot_media_extension,
    _bot_message_media,
    _message_media_type,
    normalize_channel_ref,
)


def test_normalize_tg_channel_ref_from_url_and_username() -> None:
    assert normalize_channel_ref("https://t.me/example_channel") == "example_channel"
    assert normalize_channel_ref("@example_channel") == "example_channel"
    assert normalize_channel_ref("example_channel") == "example_channel"


def test_normalize_tg_channel_ref_rejects_other_hosts() -> None:
    with pytest.raises(TelegramMirrorError) as exc_info:
        normalize_channel_ref("https://example.test/example_channel")

    assert exc_info.value.error_code == "TG_CHANNEL_REF_INVALID"


def test_tg_message_media_type_detects_images_and_videos() -> None:
    assert _message_media_type(SimpleNamespace(photo=object(), video=None, file=None)) == "image"
    assert _message_media_type(SimpleNamespace(photo=None, video=object(), file=None)) == "video"
    assert _message_media_type(SimpleNamespace(photo=None, video=None, file=SimpleNamespace(mime_type="image/jpeg"))) == "image"
    assert _message_media_type(SimpleNamespace(photo=None, video=None, file=SimpleNamespace(mime_type="video/mp4"))) == "video"
    assert _message_media_type(SimpleNamespace(photo=None, video=None, file=SimpleNamespace(mime_type="application/pdf"))) is None


@pytest.mark.asyncio
async def test_bot_mode_bind_channel_without_api_id(tmp_path) -> None:
    service = TelegramMirrorService(
        TelegramMirrorConfig(
            data_dir=tmp_path,
            enabled=True,
            mode="bot",
            bot_token="123456:secret",
            max_file_bytes=100 * 1024 * 1024,
        )
    )
    service.initialize()

    channel = await service.bind_channel("10001", "https://t.me/example_channel")

    assert channel["channel_ref"] == "example_channel"
    assert channel["channel_id"] == "bot:example_channel"
    assert service._bot_max_file_bytes == BOT_API_MAX_DOWNLOAD_BYTES
    assert service.list_channels("10001")[0]["channel_title"] == "example_channel"


@pytest.mark.asyncio
async def test_bot_mode_fetch_latest_for_groups_records_same_message_per_group(tmp_path, monkeypatch) -> None:
    service = TelegramMirrorService(
        TelegramMirrorConfig(
            data_dir=tmp_path,
            enabled=True,
            mode="bot",
            bot_token="123456:secret",
            max_file_bytes=100 * 1024 * 1024,
        )
    )
    service.initialize()
    await service.bind_channel("10001", "example_channel")
    await service.bind_channel("10002", "example_channel")

    async def fake_bot_api_json(method, params=None):
        assert method == "getUpdates"
        return {
            "ok": True,
            "result": [
                {
                    "update_id": 11,
                    "channel_post": {
                        "message_id": 42,
                        "date": 1783330950,
                        "caption": "hello",
                        "chat": {
                            "id": -100123456,
                            "username": "example_channel",
                            "title": "Example Channel",
                        },
                        "photo": [{"file_id": "photo-file-id", "file_size": 12}],
                    },
                }
            ],
        }

    async def fake_download_bot_media(channel, message, media):
        target_path = tmp_path / f"{channel['group_id']}-{message['message_id']}.jpg"
        target_path.write_bytes(b"fake image")
        with service._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO tg_messages (
                    group_id, channel_id, message_id, media_type, file_path, file_size, status, created_at, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'downloaded', ?, ?)
                """,
                (
                    str(channel["group_id"]),
                    str(channel["channel_id"]),
                    int(message["message_id"]),
                    str(media["media_type"]),
                    str(target_path),
                    target_path.stat().st_size,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM tg_messages
                WHERE group_id = ? AND channel_id = ? AND message_id = ?
                """,
                (str(channel["group_id"]), str(channel["channel_id"]), int(message["message_id"])),
            ).fetchone()
        return {
            "id": int(row["id"]) if row else 0,
            "channel_id": str(channel["channel_id"]),
            "channel_title": str(channel["channel_title"]),
            "message_id": int(message["message_id"]),
            "media_type": str(media["media_type"]),
            "file_path": str(target_path),
            "filename": target_path.name,
            "file_size": target_path.stat().st_size,
            "caption": "hello",
            "message_url": "https://t.me/example_channel/42",
            "created_at": "2026-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(service, "_bot_api_json", fake_bot_api_json)
    monkeypatch.setattr(service, "_download_bot_media", fake_download_bot_media)

    result = await service.fetch_latest_for_groups(["10001", "10002"], 5)

    by_group = {group["group_id"]: group for group in result["groups"]}
    assert [item["message_id"] for item in by_group["10001"]["items"]] == [42]
    assert [item["message_id"] for item in by_group["10002"]["items"]] == [42]
    assert by_group["10001"]["channels"][0]["channel_id"] == "-100123456"
    assert by_group["10002"]["channels"][0]["channel_id"] == "-100123456"
    assert service._message_seen("10001", "-100123456", 42)
    assert service._message_seen("10002", "-100123456", 42)
    assert service._get_state_int("bot_update_offset") == 12


def test_bot_message_media_selects_largest_photo_and_video_extension() -> None:
    media = _bot_message_media(
        {
            "photo": [
                {"file_id": "small", "file_size": 100},
                {"file_id": "large", "file_size": 200},
            ]
        }
    )
    assert media is not None
    assert media["media_type"] == "image"
    assert media["file_id"] == "large"
    assert _bot_media_extension(media) == ".jpg"

    video = _bot_message_media(
        {
            "video": {
                "file_id": "video-id",
                "file_name": "sample.mp4",
                "mime_type": "video/mp4",
                "file_size": 1024,
            }
        }
    )
    assert video is not None
    assert video["media_type"] == "video"
    assert _bot_media_extension(video) == ".mp4"
