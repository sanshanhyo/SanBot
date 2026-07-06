from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .client import JavLibraryCrawlerConfig, lookup
from .errors import JavLibraryError
from .models import JavLibraryVideo
from .option import create_option_by_env, create_option_by_file, merge_options


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        option = create_option_by_env()
        if args.option:
            option = merge_options(option, create_option_by_file(_resolve_option_path(args.option)))
    except FileNotFoundError as exc:
        print(f"JAV_OPTION_NOT_FOUND: {exc}", file=sys.stderr)
        return _exit_code("JAV_OPTION_NOT_FOUND")
    except (RuntimeError, ValueError) as exc:
        print(f"JAV_OPTION_INVALID: {exc}", file=sys.stderr)
        return _exit_code("JAV_OPTION_INVALID")

    cli_option = JavLibraryCrawlerConfig(
        base_url=args.base_url or option.base_url,
        language=args.language or option.language,
        provider_order=_split_providers(args.providers) or option.provider_order,
        javdb_base_url=args.javdb_base_url or option.javdb_base_url,
        javbus_base_url=args.javbus_base_url or option.javbus_base_url,
        jav321_base_url=args.jav321_base_url or option.jav321_base_url,
        timeout_seconds=args.timeout or option.timeout_seconds,
        total_timeout_seconds=args.total_timeout or option.total_timeout_seconds,
        fetcher=args.fetcher or option.fetcher,
        user_agent=args.user_agent or option.user_agent,
        cookie=args.cookie or option.cookie,
        proxy=args.proxy or option.proxy,
        impersonate=args.impersonate or option.impersonate,
        retry_times=args.retry or option.retry_times,
        browser_profile_dir=args.browser_profile_dir or option.browser_profile_dir,
        browser_channel=args.browser_channel or option.browser_channel,
        browser_headless=args.browser_headless or option.browser_headless,
        browser_wait_seconds=args.browser_wait or option.browser_wait_seconds,
    )

    try:
        video = lookup(args.code, cli_option)
    except JavLibraryError as exc:
        print(f"{exc.error_code}: {exc.user_message}", file=sys.stderr)
        return _exit_code(exc.error_code)
    except Exception as exc:
        print(f"JAVLIBRARY_ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(video.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_video(video))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="javlibrary",
        description="Lookup JAV metadata by video code.",
    )
    parser.add_argument("code", help="Video code, for example SSIS-123 or FC2-PPV-1234567.")
    parser.add_argument("-o", "--option", help="Option file path, supports YAML, JSON, or TOML.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--base-url", help="Javlibrary base URL.")
    parser.add_argument("--language", help="Javlibrary language path, default: cn.")
    parser.add_argument("--providers", help="Comma-separated provider order, default: javdb,javlibrary,jav321,javbus.")
    parser.add_argument("--javdb-base-url", help="JavDB base URL.")
    parser.add_argument("--javbus-base-url", help="JavBus base URL.")
    parser.add_argument("--jav321-base-url", help="Jav321 base URL.")
    parser.add_argument("--timeout", type=float, help="Request timeout seconds.")
    parser.add_argument("--total-timeout", type=float, help="Overall lookup timeout seconds, default: 15.")
    parser.add_argument("--user-agent", help="HTTP User-Agent.")
    parser.add_argument("--cookie", help="HTTP Cookie. Prefer an option file or environment variable.")
    parser.add_argument("--proxy", help="HTTP proxy URL.")
    parser.add_argument("--fetcher", choices=["curl", "http", "browser"], help="Fetcher mode, default: curl.")
    parser.add_argument("--impersonate", help="curl_cffi impersonation target for curl mode, default: random.")
    parser.add_argument("--retry", type=int, help="Retry times for transient Javlibrary request errors.")
    parser.add_argument("--browser-profile-dir", help="Persistent browser profile directory for browser mode.")
    parser.add_argument("--browser-channel", help="Browser channel for browser mode, for example chrome or msedge.")
    parser.add_argument("--browser-headless", action="store_true", help="Run browser mode without a visible window.")
    parser.add_argument("--browser-wait", type=float, help="Seconds to wait for manual browser verification.")
    return parser


def _resolve_option_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_file() or path.is_absolute():
        return path
    project_candidate = Path(__file__).resolve().parents[1] / path
    if project_candidate.is_file():
        return project_candidate
    return path


def _split_providers(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    providers = tuple(item.strip() for item in value.split(",") if item.strip())
    return providers or None


def _format_video(video: JavLibraryVideo) -> str:
    lines = [
        f"Code: {video.code}",
        f"Source: {video.source}",
        f"Title: {video.title}",
    ]
    if video.release_date:
        lines.append(f"Release date: {video.release_date}")
    if video.runtime_minutes:
        lines.append(f"Runtime: {video.runtime_minutes} minutes")
    if video.studio:
        lines.append(f"Studio: {video.studio}")
    if video.publisher:
        lines.append(f"Publisher: {video.publisher}")
    if video.series:
        lines.append(f"Series: {video.series}")
    if video.director:
        lines.append(f"Director: {video.director}")
    if video.rating is not None:
        lines.append(f"Rating: {video.rating:.1f}")
    if video.actors:
        lines.append(f"Actors: {' / '.join(video.actors)}")
    if video.genres:
        lines.append(f"Genres: {' / '.join(video.genres)}")
    if video.cover_url:
        lines.append(f"Cover: {video.cover_url}")
    lines.append(f"URL: {video.url}")
    return "\n".join(lines)


def _exit_code(error_code: str) -> int:
    return {
        "JAV_CODE_INVALID": 2,
        "JAV_NOT_FOUND": 3,
        "JAV_SOURCE_BLOCKED": 4,
        "JAV_FETCH_TIMEOUT": 5,
        "JAV_FETCH_FAILED": 6,
        "JAV_PARSE_FAILED": 7,
        "JAV_OPTION_NOT_FOUND": 8,
        "JAV_OPTION_INVALID": 9,
    }.get(error_code, 1)


if __name__ == "__main__":
    raise SystemExit(main())
