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
    return (
        f"{prefix}Knowledge-graph run complete\n"
        f"  received:  {stats.get('received', 0)}\n"
        f"  ingested:  {stats.get('ingested', 0)}\n"
        f"  skipped:   {stats.get('skipped_other_chat', 0)} (other-chat) "
        f"+ {stats.get('skipped_no_message', 0)} (no-message)\n"
        f"  duration:  {stats.get('duration_s', 0)}s\n"
        f"  target chat configured: {stats.get('target_chat_set', False)}"
    )
