"""Send status pings to the bot owner (private DM)."""
from __future__ import annotations

import logging
from typing import Mapping

import httpx

from .config import Config

LOG = logging.getLogger("notify")
TG_API = "https://api.telegram.org"


def send_owner(cfg: Config, text: str) -> None:
    """Send a DM to TELEGRAM_OWNER_CHAT_ID. Silent no-op if owner not configured.

    Note: Telegram bots cannot DM a user who has not interacted with them at least
    once. The owner must have sent the bot a message (any message — /start works
    even with no command handler). If unsent, this returns 403; we log + ignore.
    """
    if not cfg.owner_chat_id:
        LOG.info("owner_chat_id not set; skipping notify: %s", text[:80])
        return
    url = f"{TG_API}/bot{cfg.bridg3bot_token}/sendMessage"
    payload = {
        "chat_id": cfg.owner_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            LOG.warning(
                "owner notify 403: send '/start' to @%s once to enable owner DMs",
                cfg.bridg3bot_username,
            )
        else:
            LOG.warning("owner notify HTTP %s: %s", e.response.status_code, e)
    except httpx.HTTPError as e:
        LOG.warning("owner notify failed: %s", e)


def format_run_summary(stats: Mapping[str, object], *, dry_run: bool = False) -> str:
    prefix = "[DRY-RUN] " if dry_run else ""
    out = [
        f"{prefix}Knowledge-graph run complete",
        f"  received:  {stats.get('received', 0)}",
        f"  ingested:  {stats.get('ingested', 0)}",
        f"  skipped:   {stats.get('skipped_other_chat', 0)} (other-chat) "
        f"+ {stats.get('skipped_no_message', 0)} (no-message)",
        f"  duration:  {stats.get('duration_s', 0)}s",
        f"  target chat configured: {stats.get('target_chat_set', False)}",
    ]

    chat = stats.get("chat_insights") if isinstance(stats.get("chat_insights"), dict) else None
    if chat and not chat.get("error"):
        total = chat.get("total_questions", 0)
        if total:
            err = chat.get("error_count", 0)
            top = list((chat.get("per_concept") or {}).items())[:3]
            top_str = ", ".join(f"{cid}({d['count']})" for cid, d in top)
            out.append(f"  ask:       {total} questions / {err} errors / top: {top_str or '—'}")

    digest_stats = stats.get("digest") if isinstance(stats.get("digest"), dict) else None
    if digest_stats:
        if digest_stats.get("posted"):
            out.append(f"  digest:    posted ({digest_stats.get('chars', 0)} chars, msg_id={digest_stats.get('message_id')})")
        elif digest_stats.get("reason") and digest_stats["reason"] not in ("disabled",):
            out.append(f"  digest:    skipped ({digest_stats['reason']})")
        elif digest_stats.get("error"):
            out.append(f"  digest:    ERROR {digest_stats['error']}")

    return "\n".join(out)
