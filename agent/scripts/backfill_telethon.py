"""Historical-message backfill via Telethon (MTProto, user-account session).

Authenticates with your Telegram user account using api_id/api_hash from
my.telegram.org. Iterates the channel's history oldest-first and writes
atomic.json files matching the same schema as Bridg3bot's forward poll.

Run from the repo root:
    agent/.venv/bin/python agent/scripts/backfill_telethon.py
    agent/.venv/bin/python agent/scripts/backfill_telethon.py --limit 50    # smoke test
    agent/.venv/bin/python agent/scripts/backfill_telethon.py --overwrite   # re-ingest

Required .env keys:
    TELEGRAM_API_ID
    TELEGRAM_API_HASH
    TELEGRAM_BRIDG3BOT_CHAT_ID    (also accepted: --chat-id <int>)

First run prompts for phone number + SMS code (+ 2FA password if set). The
session token is saved to agent/data/telethon.session and re-used after that.

Telethon dep: install with `pip install -e 'agent[backfill]'`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add agent/src to path so we can import the package without installing it
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agent"))

from src import atomic  # noqa: E402
from src.config import DATA_DIR  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

try:
    from telethon.sync import TelegramClient
except ImportError:
    print(
        "ERR Telethon not installed. Run:\n"
        "    agent/.venv/bin/pip install -e 'agent[backfill]'",
        file=sys.stderr,
    )
    sys.exit(2)


SESSION_PATH = DATA_DIR / "telethon.session"


def main() -> int:
    parser = argparse.ArgumentParser(prog="backfill_telethon")
    parser.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="Channel chat_id (else read from TELEGRAM_BRIDG3BOT_CHAT_ID).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N messages (smoke-test).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-write atomic.json files even if they exist.",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=None,
        help="Resume from a specific message_id (skip everything older).",
    )
    parser.add_argument(
        "--list-chats",
        action="store_true",
        help="Print all chats your account is a member of (chat_id + title) and exit. "
             "Useful for finding the chat_id of ClawRyderz.",
    )
    args = parser.parse_args()

    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        print(
            "ERR TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env.\n"
            "   Get them at https://my.telegram.org -> API Development Tools.",
            file=sys.stderr,
        )
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.list_chats:
        print("== Listing chats your account is a member of ==\n")
        with TelegramClient(str(SESSION_PATH), int(api_id), api_hash) as client:
            for dialog in client.iter_dialogs():
                kind = "channel" if dialog.is_channel else ("group" if dialog.is_group else "user")
                print(f"  {dialog.id:>20}  [{kind:<8}]  {dialog.name}")
        print("\nCopy the id for ClawRyderz and pass it via --chat-id <id>")
        print("(or set TELEGRAM_BRIDG3BOT_CHAT_ID in .env so future runs pick it up).")
        return 0

    chat_id = args.chat_id
    if chat_id is None:
        env_chat = os.environ.get("TELEGRAM_BRIDG3BOT_CHAT_ID", "").strip()
        if not env_chat:
            print(
                "ERR no chat_id. Pass --chat-id <int>, or set TELEGRAM_BRIDG3BOT_CHAT_ID,\n"
                "    or run with --list-chats to discover it.",
                file=sys.stderr,
            )
            return 1
        chat_id = int(env_chat)

    print(f"== Telethon backfill ==")
    print(f"   chat_id:     {chat_id}")
    print(f"   session:     {SESSION_PATH}")
    print(f"   limit:       {args.limit or 'no limit (full history)'}")
    print(f"   overwrite:   {args.overwrite}")
    if args.start_from:
        print(f"   start-from:  message_id={args.start_from}")
    print()

    with TelegramClient(str(SESSION_PATH), int(api_id), api_hash) as client:
        print("Connecting...")
        try:
            entity = client.get_entity(chat_id)
        except Exception as e:  # noqa: BLE001
            print(f"ERR cannot resolve chat {chat_id}: {e}", file=sys.stderr)
            return 1

        chat_title = getattr(entity, "title", None) or getattr(entity, "username", None)
        print(f"Channel: {chat_title}")
        print()
        print("Iterating history oldest-first...")
        print()

        ingested = 0
        skipped = 0
        errored = 0

        # reverse=True == oldest-first (Telethon's default is newest-first)
        # min_id allows resuming
        kwargs = {"reverse": True}
        if args.start_from:
            kwargs["min_id"] = args.start_from

        for i, msg in enumerate(client.iter_messages(entity, **kwargs)):
            if args.limit and i >= args.limit:
                print(f"\n--limit {args.limit} reached, stopping.")
                break
            try:
                a = atomic.from_telethon_message(msg, chat_title=chat_title)
                path = atomic.write(a, overwrite=args.overwrite)
                if path is None:
                    skipped += 1
                else:
                    ingested += 1
            except Exception as e:  # noqa: BLE001
                errored += 1
                print(f"  ERR msg_id={getattr(msg, 'id', '?')}: {e}", file=sys.stderr)

            if (ingested + skipped + errored) % 50 == 0 and (ingested + skipped + errored) > 0:
                print(
                    f"  progress: ingested={ingested} skipped={skipped} errored={errored} "
                    f"(latest msg_id={getattr(msg, 'id', '?')}, "
                    f"date={getattr(msg, 'date', '?')})"
                )

        print()
        print(f"DONE. ingested={ingested} skipped={skipped} errored={errored}")
        print(f"     atomics in {atomic.ATOMIC_DIR}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
