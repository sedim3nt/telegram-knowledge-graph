"""Telegram bot poller: one-shot getUpdates → SQLite + JSONL.

Telegram Bot API: https://core.telegram.org/bots/api#getupdates
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import atomic
from .config import Config, DATA_DIR

LOG = logging.getLogger("poll")
DB_PATH = DATA_DIR / "state.db"
INGEST_DIR = DATA_DIR / "ingest"
TG_API = "https://api.telegram.org"


def _open_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            update_id   INTEGER PRIMARY KEY,
            chat_id     INTEGER NOT NULL,
            message_id  INTEGER NOT NULL,
            date        INTEGER NOT NULL,
            raw_json    TEXT NOT NULL,
            ingested_at INTEGER NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg ON messages(chat_id, message_id)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    return conn


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _ingest_path() -> Path:
    INGEST_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return INGEST_DIR / f"{today}.jsonl"


def fetch_updates(cfg: Config, *, dry_run: bool = False) -> dict:
    """Single-shot getUpdates pull. Persists to SQLite + JSONL.

    Returns a stats dict:
      received, ingested, skipped_other_chat, skipped_no_message,
      discovered_chats {chat_id: title}, target_chat_set
    """
    conn = _open_db()
    last = _meta_get(conn, "last_update_id")
    offset = (int(last) + 1) if last else None

    url = f"{TG_API}/bot{cfg.bridg3bot_token}/getUpdates"
    # Long-poll up to poll_timeout_s seconds; allowed_updates filters server-side.
    params: dict[str, object] = {
        "timeout": cfg.poll_timeout_s,
        "allowed_updates": json.dumps(["message", "channel_post", "edited_message"]),
    }
    if offset is not None:
        params["offset"] = offset

    LOG.info("getUpdates offset=%s timeout=%ss", offset, cfg.poll_timeout_s)
    with httpx.Client(timeout=cfg.poll_timeout_s + 5) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        body = r.json()

    if not body.get("ok"):
        raise RuntimeError(f"Telegram returned not-ok: {body}")

    updates = body.get("result", [])
    LOG.info("received %d updates", len(updates))

    target_chat = cfg.bridg3bot_chat_id
    new_rows: list[tuple] = []
    skipped_other_chat = 0
    skipped_no_message = 0
    atomic_written = 0
    discovered_chats: dict[int, str] = {}

    for upd in updates:
        upd_id = int(upd["update_id"])
        msg = upd.get("message") or upd.get("channel_post") or upd.get("edited_message")
        if not msg:
            skipped_no_message += 1
            continue
        chat = msg.get("chat", {})
        chat_id = int(chat.get("id"))
        chat_title = chat.get("title") or chat.get("username") or ""
        discovered_chats.setdefault(chat_id, chat_title)
        if target_chat and chat_id != target_chat:
            skipped_other_chat += 1
            continue
        new_rows.append(
            (
                upd_id,
                chat_id,
                int(msg["message_id"]),
                int(msg["date"]),
                json.dumps(upd, ensure_ascii=False),
                int(time.time()),
            )
        )
        if not dry_run:
            try:
                a = atomic.from_bot_update(upd)
                if atomic.write(a) is not None:
                    atomic_written += 1
            except Exception as e:  # noqa: BLE001
                LOG.warning("atomic write failed for update %s: %s", upd_id, e)

    if dry_run:
        LOG.info("dry-run: would insert %d rows; skipping commit", len(new_rows))
    else:
        if new_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO messages "
                "(update_id, chat_id, message_id, date, raw_json, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                new_rows,
            )
            with _ingest_path().open("a", encoding="utf-8") as fh:
                for row in new_rows:
                    fh.write(row[4] + "\n")
        if updates:
            highest = max(int(u["update_id"]) for u in updates)
            _meta_set(conn, "last_update_id", str(highest))
        _meta_set(conn, "last_run_at", datetime.now(timezone.utc).isoformat())
        conn.commit()

    conn.close()

    return {
        "received": len(updates),
        "ingested": len(new_rows),
        "atomic_written": atomic_written,
        "skipped_other_chat": skipped_other_chat,
        "skipped_no_message": skipped_no_message,
        "discovered_chats": discovered_chats,
        "target_chat_set": target_chat is not None,
    }
