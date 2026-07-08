from __future__ import annotations

import json
from pathlib import Path

from bot.lang_keys import LANG_KEYS


def test_lang_keys_match_default_locale() -> None:
    locale_path = Path(__file__).resolve().parents[1] / "i18n" / "zh_CN.json"
    messages = json.loads(locale_path.read_text(encoding="utf-8"))

    assert set(messages) == LANG_KEYS
