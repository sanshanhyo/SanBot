from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

from . import downloader


def _write_result(result_path: Path, payload: dict) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch one JMComic album preview.")
    parser.add_argument("--album-id", required=True)
    parser.add_argument("--option-path", required=True)
    parser.add_argument("--result-path", required=True)
    args = parser.parse_args()

    result_path = Path(args.result_path).resolve()
    error_log_path = result_path.with_suffix(".error.log")

    try:
        preview = downloader.fetch_album_preview(args.album_id, Path(args.option_path))
    except downloader.DownloaderError as exc:
        error_log_path.write_text(
            downloader._redact_sensitive_log(traceback.format_exc()),
            encoding="utf-8",
        )
        _write_result(
            result_path,
            {
                "ok": False,
                "error_type": exc.__class__.__name__,
                "user_message": exc.user_message,
            },
        )
        return 2
    except Exception:
        error_log_path.write_text(
            downloader._redact_sensitive_log(traceback.format_exc()),
            encoding="utf-8",
        )
        _write_result(
            result_path,
            {
                "ok": False,
                "error_type": "UnexpectedError",
                "user_message": "获取漫画信息失败，请查看服务日志",
            },
        )
        return 1

    _write_result(result_path, {"ok": True, "preview": preview})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
