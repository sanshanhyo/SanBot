from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from .client import JavLibraryCrawlerConfig


def create_option_by_file(path: str | Path) -> JavLibraryCrawlerConfig:
    option_path = Path(path).expanduser().resolve()
    data = _read_option_file(option_path)
    return create_option_by_mapping(data, base_dir=option_path.parent)


def create_option_by_env(prefix: str = "JAVLIBRARY_") -> JavLibraryCrawlerConfig:
    return create_option_by_mapping(
        {
            "base_url": os.getenv(f"{prefix}BASE_URL"),
            "language": os.getenv(f"{prefix}LANGUAGE"),
            "provider_order": os.getenv(f"{prefix}PROVIDER_ORDER"),
            "javdb_base_url": os.getenv("JAVDB_BASE_URL") or os.getenv(f"{prefix}JAVDB_BASE_URL"),
            "javbus_base_url": os.getenv("JAVBUS_BASE_URL") or os.getenv(f"{prefix}JAVBUS_BASE_URL"),
            "jav321_base_url": os.getenv("JAV321_BASE_URL") or os.getenv(f"{prefix}JAV321_BASE_URL"),
            "timeout_seconds": os.getenv(f"{prefix}TIMEOUT_SECONDS"),
            "total_timeout_seconds": os.getenv(f"{prefix}TOTAL_TIMEOUT_SECONDS"),
            "fetcher": os.getenv(f"{prefix}FETCHER"),
            "user_agent": os.getenv(f"{prefix}USER_AGENT"),
            "cookie": os.getenv(f"{prefix}COOKIE"),
            "proxy": os.getenv(f"{prefix}PROXY"),
            "impersonate": os.getenv(f"{prefix}IMPERSONATE"),
            "retry_times": os.getenv(f"{prefix}RETRY_TIMES"),
            "browser_profile_dir": os.getenv(f"{prefix}BROWSER_PROFILE_DIR"),
            "browser_channel": os.getenv(f"{prefix}BROWSER_CHANNEL"),
            "browser_headless": os.getenv(f"{prefix}BROWSER_HEADLESS"),
            "browser_wait_seconds": os.getenv(f"{prefix}BROWSER_WAIT_SECONDS"),
        }
    )


def create_option_by_mapping(data: dict[str, Any], *, base_dir: Path | None = None) -> JavLibraryCrawlerConfig:
    request = data.get("request")
    if not isinstance(request, dict):
        request = {}
    timeout_seconds = _float_value(
        data.get("timeout_seconds")
        or data.get("timeout")
        or request.get("timeout_seconds")
        or request.get("timeout"),
        8.0,
    )
    browser = data.get("browser")
    if not isinstance(browser, dict):
        browser = {}
    return JavLibraryCrawlerConfig(
        base_url=str(data.get("base_url") or "https://www.javlibrary.com"),
        language=str(data.get("language") or "cn"),
        provider_order=_provider_order(data.get("provider_order") or data.get("providers")),
        javdb_base_url=str(data.get("javdb_base_url") or "https://javdb.com"),
        javbus_base_url=str(data.get("javbus_base_url") or "https://www.javbus.com"),
        jav321_base_url=str(data.get("jav321_base_url") or "https://www.jav321.com"),
        timeout_seconds=timeout_seconds,
        total_timeout_seconds=_float_value(data.get("total_timeout_seconds") or data.get("total_timeout"), 15.0),
        fetcher=str(data.get("fetcher") or "curl"),
        user_agent=_optional_str(data.get("user_agent") or request.get("user_agent")),
        cookie=_optional_str(data.get("cookie") or request.get("cookie")),
        proxy=_optional_str(data.get("proxy") or request.get("proxy")),
        impersonate=_optional_str(data.get("impersonate") or request.get("impersonate")) or "random",
        retry_times=_int_value(data.get("retry_times") or request.get("retry_times"), 1),
        browser_profile_dir=_resolve_path(
            _optional_str(data.get("browser_profile_dir") or browser.get("profile_dir")),
            base_dir,
        ),
        browser_channel=_optional_str(data.get("browser_channel") or browser.get("channel")),
        browser_headless=_bool_value(data.get("browser_headless") or browser.get("headless"), False),
        browser_wait_seconds=_float_value(data.get("browser_wait_seconds") or browser.get("wait_seconds"), 60.0),
    )


def merge_options(
    base: JavLibraryCrawlerConfig,
    override: JavLibraryCrawlerConfig,
    *,
    prefer_override: bool = True,
) -> JavLibraryCrawlerConfig:
    if not prefer_override:
        base, override = override, base
    return JavLibraryCrawlerConfig(
        base_url=override.base_url or base.base_url,
        language=override.language or base.language,
        provider_order=override.provider_order or base.provider_order,
        javdb_base_url=override.javdb_base_url or base.javdb_base_url,
        javbus_base_url=override.javbus_base_url or base.javbus_base_url,
        jav321_base_url=override.jav321_base_url or base.jav321_base_url,
        timeout_seconds=override.timeout_seconds or base.timeout_seconds,
        total_timeout_seconds=override.total_timeout_seconds or base.total_timeout_seconds,
        fetcher=override.fetcher or base.fetcher,
        user_agent=override.user_agent or base.user_agent,
        cookie=override.cookie or base.cookie,
        proxy=override.proxy or base.proxy,
        impersonate=override.impersonate or base.impersonate,
        retry_times=override.retry_times or base.retry_times,
        browser_profile_dir=override.browser_profile_dir or base.browser_profile_dir,
        browser_channel=override.browser_channel or base.browser_channel,
        browser_headless=override.browser_headless if override.browser_headless else base.browser_headless,
        browser_wait_seconds=override.browser_wait_seconds or base.browser_wait_seconds,
    )


def _read_option_file(path: Path) -> dict[str, Any]:
    option_path = path.expanduser().resolve()
    if not option_path.is_file():
        raise FileNotFoundError(f"option file not found: {option_path}")
    suffix = option_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(option_path.read_text(encoding="utf-8"))
    elif suffix == ".toml":
        data = tomllib.loads(option_path.read_text(encoding="utf-8"))
    else:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("YAML option files require PyYAML") from exc
        data = yaml.safe_load(option_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("option file must contain a mapping")
    return data


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_path(value: str | None, base_dir: Path | None) -> str | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _provider_order(value: object) -> tuple[str, ...]:
    default = ("javdb", "javlibrary", "jav321", "javbus")
    if value is None:
        return default
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        return default
    order = tuple(item for item in items if item)
    return order or default


def _float_value(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _int_value(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _bool_value(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
