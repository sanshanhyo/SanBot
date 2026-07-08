from __future__ import annotations

import argparse
import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or validate a Telethon session for SanBot Telegram mirror.",
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env file, default: .env")
    parser.add_argument("--api-id", type=int, default=None, help="Telegram API ID. Defaults to TG_API_ID.")
    parser.add_argument("--api-hash", default=None, help="Telegram API hash. Defaults to TG_API_HASH.")
    parser.add_argument(
        "--session-path",
        default=None,
        help="Output Telethon .session path. Defaults to TG_SESSION_PATH or ./data/telegram.session.",
    )
    parser.add_argument(
        "--string",
        action="store_true",
        help="Print a StringSession instead of writing a .session file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(args.env_file)

    api_id = args.api_id or _env_int("TG_API_ID")
    api_hash = args.api_hash or os.getenv("TG_API_HASH")
    if not api_id or not api_hash:
        print("TG_SESSION_CONFIG_MISSING: please set TG_API_ID and TG_API_HASH or pass --api-id/--api-hash.")
        return 2

    try:
        from telethon.sync import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("TG_DEPENDENCY_MISSING: install dependencies first, e.g. ./.venv/bin/python -m pip install -e .")
        return 3

    if args.string:
        session = StringSession()
        session_hint = "StringSession"
    else:
        session_path = Path(args.session_path or os.getenv("TG_SESSION_PATH") or "./data/telegram.session")
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session = str(session_path)
        session_hint = str(session_path)

    print("Telethon will ask for your phone number, login code, and 2FA password if needed.")
    print("Only use your own account. Keep session files and strings private.")
    with TelegramClient(session, api_id, api_hash) as client:
        me = client.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else str(getattr(me, "id", "unknown"))
        print(f"TG_SESSION_OK: logged in as {username}")
        if args.string:
            print("TG_SESSION_STRING_BEGIN")
            print(client.session.save())
            print("TG_SESSION_STRING_END")
        else:
            print(f"TG_SESSION_PATH_OK: {session_hint}")
    return 0


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
