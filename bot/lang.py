from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOCALE = "zh_CN"


def _lang_dir() -> Path:
    configured = os.getenv("BOT_I18N_DIR")
    if configured:
        return Path(configured)
    project_root = Path(__file__).resolve().parents[1]
    i18n_dir = project_root / "i18n"
    if i18n_dir.is_dir():
        return i18n_dir
    return project_root / "lang"


@lru_cache(maxsize=8)
def _load_messages(locale: str) -> dict[str, Any]:
    path = _lang_dir() / f"{locale}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if locale != DEFAULT_LOCALE:
            return _load_messages(DEFAULT_LOCALE)
        logger.warning("Language file not found: %s", path)
        return {}
    except (json.JSONDecodeError, OSError):
        logger.exception("Could not load language file: %s", path)
        return {}

    return data if isinstance(data, dict) else {}


def _locale() -> str:
    return os.getenv("BOT_LANG", DEFAULT_LOCALE) or DEFAULT_LOCALE


def text(key: str, **kwargs: object) -> str:
    value = _load_messages(_locale()).get(key, key)
    if not isinstance(value, str):
        return key
    try:
        return value.format(**kwargs)
    except Exception:
        logger.exception("Could not format language key: %s", key)
        return value


def words(key: str, default: set[str]) -> set[str]:
    value = _load_messages(_locale()).get(key)
    if isinstance(value, list):
        return {str(item).lower() for item in value}
    return default
