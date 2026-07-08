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
