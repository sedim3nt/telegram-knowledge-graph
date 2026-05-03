"""Typed config loaded from the repo-root .env file."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Repo root = three levels above this file (src/config.py -> src -> agent -> repo)
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"
DATA_DIR = REPO_ROOT / "agent" / "data"
LOGS_DIR = REPO_ROOT / "agent" / "logs"
VAULT_DIR = REPO_ROOT / "vault"

# Eager load so the rest of the module sees env values.
load_dotenv(ENV_PATH)


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"FATAL: missing required env var {key} in {ENV_PATH}", file=sys.stderr)
        sys.exit(1)
    return val


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip() or default


@dataclass(frozen=True)
class Config:
    bridg3bot_token: str
    bridg3bot_username: str
    bridg3bot_chat_id: int | None
    owner_chat_id: int | None
    poll_timeout_s: int = 10

    @classmethod
    def load(cls) -> "Config":
        chat_id_raw = _optional("TELEGRAM_BRIDG3BOT_CHAT_ID")
        owner_raw = _optional("TELEGRAM_OWNER_CHAT_ID")
        return cls(
            bridg3bot_token=_require("TELEGRAM_BRIDG3BOT_TOKEN"),
            bridg3bot_username=_optional("TELEGRAM_BRIDG3BOT_USERNAME", ""),
            bridg3bot_chat_id=int(chat_id_raw) if chat_id_raw else None,
            owner_chat_id=int(owner_raw) if owner_raw else None,
        )
