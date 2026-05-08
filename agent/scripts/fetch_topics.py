"""Fetch forum-topic titles for the channel and write to agent/data/topics.json.

For supergroups with topics enabled, Telegram exposes named sub-discussions
("topics") via the channels.GetForumTopics MTProto method. Each topic has an id
that matches the `topic_id` field on individual messages. With this map we can
attach human-given names ("Memory config", "MCP setup", etc.) to clusters of
messages — a huge head-start for concept synthesis.

Output format (agent/data/topics.json):
    {
      "fetched_at": "2026-04-30T...",
      "chat_id": -1003744226857,
      "chat_title": "ClawRyderz",
      "topics": [
        {"id": 19, "title": "Memory config", "icon_color": 11129336,
         "from_id": 12345, "date_iso": "...", "closed": false, "pinned": false},
        ...
      ]
    }

Run:
    agent/.venv/bin/python agent/scripts/fetch_topics.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agent"))

from src.config import DATA_DIR  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

try:
    from telethon.sync import TelegramClient
    from telethon.tl.functions.messages import GetForumTopicsRequest
except ImportError as e:
    print(f"ERR Telethon import failed: {e}", file=sys.stderr)
    print("    Try: agent/.venv/bin/pip install -e 'agent[backfill]'", file=sys.stderr)
    sys.exit(2)


SESSION_PATH = DATA_DIR / "telethon.session"
OUT_PATH = DATA_DIR / "topics.json"


def main() -> int:
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    chat_id = os.environ.get("TELEGRAM_BRIDG3BOT_CHAT_ID", "").strip()

    if not api_id or not api_hash or not chat_id:
        print(
            "ERR missing TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_BRIDG3BOT_CHAT_ID in .env",
            file=sys.stderr,
        )
        return 1

    chat_id_int = int(chat_id)

    with TelegramClient(str(SESSION_PATH), int(api_id), api_hash) as client:
        channel = client.get_entity(chat_id_int)
        chat_title = getattr(channel, "title", None) or getattr(channel, "username", None)
        print(f"Fetching forum topics for: {chat_title} ({chat_id_int})")

        topics: list[dict] = []
        offset_date = None
        offset_id = 0
        offset_topic = 0
        page = 0

        while True:
            page += 1
            result = client(GetForumTopicsRequest(
                peer=channel,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=100,
            ))
            batch = getattr(result, "topics", []) or []
            print(f"  page {page}: {len(batch)} topics")

            if not batch:
                break

            for t in batch:
                topics.append({
                    "id": getattr(t, "id", None),
                    "title": getattr(t, "title", None),
                    "icon_color": getattr(t, "icon_color", None),
                    "icon_emoji_id": getattr(t, "icon_emoji_id", None),
                    "from_id": _user_id(getattr(t, "from_id", None)),
                    "date_iso": t.date.isoformat() if getattr(t, "date", None) else None,
                    "closed": bool(getattr(t, "closed", False)),
                    "pinned": bool(getattr(t, "pinned", False)),
                    "hidden": bool(getattr(t, "hidden", False)),
                    "top_message": getattr(t, "top_message", None),
                })

            # If we got fewer than the limit, no more pages
            if len(batch) < 100:
                break
            # Otherwise advance the cursor
            last = batch[-1]
            offset_topic = getattr(last, "id", 0)
            offset_id = getattr(last, "top_message", 0) or 0
            offset_date = last.date if getattr(last, "date", None) else None

        # Dedupe by id
        seen = {}
        for t in topics:
            if t["id"] is not None:
                seen[t["id"]] = t
        topics_unique = sorted(seen.values(), key=lambda t: t["id"])

        out = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "chat_id": chat_id_int,
            "chat_title": chat_title,
            "topic_count": len(topics_unique),
            "topics": topics_unique,
        }

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nOK wrote {OUT_PATH} ({len(topics_unique)} unique topics)")

    return 0


def _user_id(peer) -> int | None:
    if peer is None:
        return None
    return getattr(peer, "user_id", None) or getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)


if __name__ == "__main__":
    sys.exit(main())
