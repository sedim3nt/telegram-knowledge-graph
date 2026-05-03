"""Atomic note schema + writer.

Spec: KG_SPEC.md (atomic.v1)

Atomic files are the immutable, canonical record of every Telegram message we've
ever ingested. They live in `agent/data/atomic/<id>.json` (gitignored, local-only).

Two ingestion sources produce identical schemas:
  - bridg3bot-poll       (Bot API getUpdates payload)
  - telethon-backfill    (MTProto Message via Telethon)

Idempotent: re-ingesting the same message overwrites with identical content.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

LOG = logging.getLogger("atomic")
ATOMIC_DIR = DATA_DIR / "atomic"
SCHEMA = "atomic.v1"


def atomic_id(message_id: int) -> str:
    return f"clr-{message_id}"


def atomic_path(aid: str) -> Path:
    ATOMIC_DIR.mkdir(parents=True, exist_ok=True)
    return ATOMIC_DIR / f"{aid}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_deep_link(chat_id: int, message_id: int) -> str:
    """Construct a t.me deep link for the message.

    For private supergroups/channels, Telegram uses the chat_id without the -100 prefix.
    For public channels with a username, t.me/<username>/<msg_id> works too — but we
    don't have the username here, so always use the c/ form.
    """
    public_id = abs(chat_id)
    if public_id > 1_000_000_000_000:  # supergroup/channel: -100xxxxx
        public_id -= 1_000_000_000_000
    return f"https://t.me/c/{public_id}/{message_id}"


def _media_kind_from_bot(msg: dict) -> str | None:
    for k in ("photo", "video", "document", "voice", "sticker", "audio", "animation"):
        if k in msg:
            return k
    return None


# ---------------------------------------------------------------------------
# Bot API → atomic
# ---------------------------------------------------------------------------

def from_bot_update(update: dict) -> dict:
    """Build atomic dict from a single Telegram Bot API update payload.

    `update` is the dict returned by getUpdates (one element of `result`).
    Handles `message`, `channel_post`, `edited_message`.
    """
    msg = update.get("message") or update.get("channel_post") or update.get("edited_message")
    if not msg:
        raise ValueError("update has no message-like field")

    chat = msg.get("chat", {}) or {}
    sender = msg.get("from") or {}  # for channel posts, may be absent

    chat_id = int(chat["id"])
    message_id = int(msg["message_id"])
    aid = atomic_id(message_id)

    return {
        "$schema": SCHEMA,
        "id": aid,
        "ingested_at": _now_iso(),
        "source": "bridg3bot-poll",
        "chat_id": chat_id,
        "chat_title": chat.get("title") or chat.get("username"),
        "message_id": message_id,
        "thread_root_id": msg.get("message_thread_id"),
        "reply_to_message_id": (msg.get("reply_to_message") or {}).get("message_id"),
        "topic_id": msg.get("message_thread_id") if msg.get("is_topic_message") else None,
        "deep_link": _build_deep_link(chat_id, message_id),
        "date_iso": (
            datetime.fromtimestamp(int(msg["date"]), tz=timezone.utc).isoformat()
            if msg.get("date") else None
        ),
        "edit_date_iso": (
            datetime.fromtimestamp(int(msg["edit_date"]), tz=timezone.utc).isoformat()
            if msg.get("edit_date") else None
        ),
        "author_id": sender.get("id"),
        "author_username": sender.get("username"),
        "author_display_name": " ".join(
            x for x in [sender.get("first_name"), sender.get("last_name")] if x
        ) or sender.get("username") or None,
        "author_is_bot": bool(sender.get("is_bot")) if sender else False,
        "text": msg.get("text") or msg.get("caption") or "",
        "media_kind": _media_kind_from_bot(msg),
        "media_caption": msg.get("caption") if _media_kind_from_bot(msg) else None,
        "forward": _forward_from_bot(msg),
        "raw_source": update,
    }


def _forward_from_bot(msg: dict) -> dict | None:
    fwd_from = msg.get("forward_from") or msg.get("forward_from_chat")
    if not fwd_from:
        return None
    return {
        "from": fwd_from.get("title") or fwd_from.get("username") or fwd_from.get("first_name"),
        "from_id": fwd_from.get("id"),
        "date_iso": (
            datetime.fromtimestamp(int(msg["forward_date"]), tz=timezone.utc).isoformat()
            if msg.get("forward_date") else None
        ),
    }


# ---------------------------------------------------------------------------
# Telethon → atomic
# ---------------------------------------------------------------------------

def from_telethon_message(msg: Any, chat_title: str | None = None) -> dict:
    """Build atomic dict from a Telethon Message object.

    `msg` is a `telethon.tl.types.Message`. We avoid importing telethon types here
    so this module stays import-cheap; callers pass the live object.
    """
    chat_id = int(getattr(msg, "chat_id", 0)) or int(msg.peer_id.channel_id) * -1 - 1_000_000_000_000
    message_id = int(msg.id)
    aid = atomic_id(message_id)

    sender = getattr(msg, "sender", None)
    sender_id = getattr(sender, "id", None) if sender else None
    sender_username = getattr(sender, "username", None) if sender else None
    sender_first = getattr(sender, "first_name", None) if sender else None
    sender_last = getattr(sender, "last_name", None) if sender else None
    sender_is_bot = bool(getattr(sender, "bot", False)) if sender else False

    reply = getattr(msg, "reply_to", None)
    reply_to_msg_id = getattr(reply, "reply_to_msg_id", None) if reply else None
    reply_to_top_id = getattr(reply, "reply_to_top_id", None) if reply else None

    media = getattr(msg, "media", None)
    media_kind = _media_kind_from_telethon(media)

    fwd = getattr(msg, "fwd_from", None)

    # raw_source: Telethon Message has .to_dict() which produces a JSON-friendly nested dict
    try:
        raw = msg.to_dict()
    except Exception:  # noqa: BLE001
        raw = {"_telethon_repr": repr(msg)}

    return {
        "$schema": SCHEMA,
        "id": aid,
        "ingested_at": _now_iso(),
        "source": "telethon-backfill",
        "chat_id": chat_id,
        "chat_title": chat_title,
        "message_id": message_id,
        "thread_root_id": reply_to_top_id,
        "reply_to_message_id": reply_to_msg_id,
        "topic_id": reply_to_top_id,  # forum topic = reply_to_top_id when message is in topic
        "deep_link": _build_deep_link(chat_id, message_id),
        "date_iso": msg.date.isoformat() if msg.date else None,
        "edit_date_iso": msg.edit_date.isoformat() if getattr(msg, "edit_date", None) else None,
        "author_id": sender_id,
        "author_username": sender_username,
        "author_display_name": " ".join(x for x in [sender_first, sender_last] if x) or sender_username or None,
        "author_is_bot": sender_is_bot,
        "text": (getattr(msg, "message", "") or "").strip(),
        "media_kind": media_kind,
        "media_caption": getattr(msg, "message", None) if media_kind else None,
        "forward": _forward_from_telethon(fwd),
        "raw_source": raw,
    }


def _media_kind_from_telethon(media: Any) -> str | None:
    if not media:
        return None
    cls = type(media).__name__
    mapping = {
        "MessageMediaPhoto": "photo",
        "MessageMediaDocument": "document",
        "MessageMediaWebPage": None,  # treat link previews as not-media
    }
    if cls in mapping:
        return mapping[cls]
    # Fallbacks based on document MIME
    doc = getattr(media, "document", None)
    if doc:
        mime = getattr(doc, "mime_type", "") or ""
        if mime.startswith("audio/"):
            return "voice" if "ogg" in mime else "audio"
        if mime.startswith("video/"):
            return "video"
    return cls.replace("MessageMedia", "").lower() or None


def _forward_from_telethon(fwd: Any) -> dict | None:
    if not fwd:
        return None
    return {
        "from": getattr(fwd, "from_name", None),
        "from_id": _try_int(getattr(getattr(fwd, "from_id", None), "user_id", None)
                            or getattr(getattr(fwd, "from_id", None), "channel_id", None)),
        "date_iso": fwd.date.isoformat() if getattr(fwd, "date", None) else None,
    }


def _try_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write(atomic: dict, *, overwrite: bool = False, dry_run: bool = False) -> Path | None:
    """Persist atomic to disk. Returns the path written, or None if skipped.

    Idempotent: existing files are skipped unless overwrite=True.
    """
    aid = atomic["id"]
    path = atomic_path(aid)
    if path.exists() and not overwrite:
        return None
    if dry_run:
        return path
    # default=str lets datetime/date/Decimal objects (esp. from Telethon's msg.to_dict())
    # serialize as their str() form rather than crashing.
    path.write_text(
        json.dumps(atomic, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path
