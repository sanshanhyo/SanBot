from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.tg_mirror import TelegramMirrorError, _message_media_type, normalize_channel_ref


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
