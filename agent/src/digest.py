"""Bridg3's daily channel digest — narrative, not a data dump.

Picks the most-discussed concepts of the past day (and the most-asked Ask
Bridg3 questions) and asks Sonnet to write a 3-paragraph dispatch in Bridg3's
voice, then posts it to the Telegram channel via sendMessage.

Hard-default OFF — the orchestrator only invokes this when
`TELEGRAM_DIGEST_ENABLED=1` is set in .env. Use `--preview` to dry-run from
the CLI without ever hitting Telegram, and review tone before flipping the
env flag.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import REPO_ROOT, VAULT_DIR

LOG = logging.getLogger("digest")

CONCEPTS_DIR = VAULT_DIR / "concepts"
META_DIR = VAULT_DIR / "_meta"
INSIGHTS_JSON = META_DIR / "chat-insights.json"
SOUL_PATH = REPO_ROOT / "SOUL.md"

DEFAULT_MODEL = os.environ.get("CLAWRYDERZ_DIGEST_MODEL", "sonnet").strip() or "sonnet"
CLI_TIMEOUT_S = 180
TELEGRAM_API = "https://api.telegram.org"

DIGEST_SYSTEM_PROMPT = """You are Bridg3bot writing a short daily dispatch back to the channel about what's been happening. Voice: warm, friendly, occasionally clever — match the SOUL.md persona. Output should read like a friend recapping the day, not an analytics report.

Hard rules:
- 2-3 short paragraphs. Never headers. Never bullet lists.
- Lead with the most interesting thing that happened, not a chronological summary.
- Name people by their handle (@username) when their take is what made the moment interesting.
- If visitors asked Bridg3 questions on the website, mention 1-2 — what visitors are curious about is itself news.
- End with a single line in Bridg3's voice — a small observation or sign-off, never a summary of what you just said.
- Plain text only. No markdown formatting (Telegram doesn't render MD reliably). Use @handles, plain prose. The 🐯 tiger is OK once at the end.
- 600 chars max total."""


def _short_date(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return iso[:10]


def _recent_concepts(window_days: int) -> list[dict]:
    """Concepts whose last_updated falls within the window. Sorted by atom_count desc."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    out: list[dict] = []
    if not CONCEPTS_DIR.exists():
        return out
    for p in sorted(CONCEPTS_DIR.glob("*.json")):
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        last = c.get("last_updated")
        if not last:
            continue
        try:
            when = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= cutoff:
            out.append(c)
    out.sort(key=lambda c: c.get("atom_count", 0), reverse=True)
    return out


def _load_chat_signal() -> dict:
    if not INSIGHTS_JSON.exists():
        return {}
    try:
        return json.loads(INSIGHTS_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _build_user_prompt(window_days: int) -> str | None:
    """Assemble the 'what happened' brief Bridg3 narrates from. None if nothing happened."""
    concepts = _recent_concepts(window_days)
    chat = _load_chat_signal()

    if not concepts and not chat.get("total_questions"):
        return None

    parts: list[str] = [f"Window: last {window_days} day(s).", ""]

    if concepts:
        parts.append("Concepts that saw activity:")
        for c in concepts[:8]:
            cs = (c.get("consensus_summary") or "").strip()
            parts.append(
                f"- {c.get('title')} (id={c['concept_id']}, "
                f"{c.get('atom_count', 0)} total messages, "
                f"updated {_short_date(c.get('last_updated'))}): {cs[:280]}"
            )
        parts.append("")

    top_qs = (chat.get("top_questions") or [])[:5]
    if top_qs:
        parts.append("Recent questions visitors asked Bridg3 on the website:")
        for q in top_qs:
            page = f" (on {q['current_page']})" if q.get("current_page") else ""
            parts.append(f"- {q['question']}{page}")
        parts.append("")

    parts.append(
        "Write the 2-3 paragraph dispatch. Lead with the single most "
        "interesting thread, not a chronology. End with one short line."
    )
    return "\n".join(parts)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    return env


def _claude_call(system_prompt: str, user_prompt: str, model: str) -> str:
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",
        "--disable-slash-commands",
        "--system-prompt", system_prompt,
    ]
    proc = subprocess.run(
        cmd, input=user_prompt, capture_output=True, text=True,
        timeout=CLI_TIMEOUT_S, env=_subprocess_env(),
    )
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude error: {envelope.get('result', '')[:300]}")
    return (envelope.get("result") or "").strip()


def _post_to_telegram(token: str, chat_id: int, text: str) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    body = urlencode({
        "chat_id": str(chat_id),
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = Request(url, data=body, method="POST",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=20) as resp:  # noqa: S310
        return json.loads(resp.read())


def compose(window_days: int = 1) -> str | None:
    """Generate the digest text. Returns None if there's nothing worth posting."""
    if not SOUL_PATH.exists():
        raise RuntimeError(f"SOUL.md missing at {SOUL_PATH}")
    user_prompt = _build_user_prompt(window_days)
    if not user_prompt:
        LOG.info("digest: nothing to report in last %dd; skipping", window_days)
        return None
    persona = SOUL_PATH.read_text(encoding="utf-8")
    system_prompt = persona + "\n\n---\n\n" + DIGEST_SYSTEM_PROMPT
    return _claude_call(system_prompt, user_prompt, DEFAULT_MODEL)


def run(*, preview: bool = False, window_days: int = 1, target_chat_id: int | None = None) -> dict:
    """Compose and (optionally) post the digest. Returns a stats dict."""
    enabled = os.environ.get("TELEGRAM_DIGEST_ENABLED", "").strip() == "1"
    if not enabled and not preview:
        LOG.info("digest: TELEGRAM_DIGEST_ENABLED!=1 and not --preview; skipping")
        return {"posted": False, "reason": "disabled"}

    text = compose(window_days)
    if not text:
        return {"posted": False, "reason": "nothing to post"}

    if preview:
        print("\n--- Digest preview ---\n")
        print(text)
        print("\n--- end ---\n")
        return {"posted": False, "reason": "preview", "text": text, "chars": len(text)}

    token = os.environ.get("TELEGRAM_BRIDG3BOT_TOKEN", "").strip()
    if not token:
        return {"posted": False, "error": "TELEGRAM_BRIDG3BOT_TOKEN missing"}
    chat_raw = os.environ.get("TELEGRAM_DIGEST_TARGET", "").strip() or os.environ.get(
        "TELEGRAM_BRIDG3BOT_CHAT_ID", ""
    ).strip()
    if target_chat_id is not None:
        chat_raw = str(target_chat_id)
    if not chat_raw:
        return {"posted": False, "error": "no target chat id (TELEGRAM_DIGEST_TARGET / TELEGRAM_BRIDG3BOT_CHAT_ID unset)"}
    try:
        chat_id = int(chat_raw)
    except ValueError:
        return {"posted": False, "error": f"invalid chat_id: {chat_raw}"}

    LOG.info("digest: posting %d chars to chat_id=%s", len(text), chat_id)
    try:
        body = _post_to_telegram(token, chat_id, text)
    except Exception as e:  # noqa: BLE001
        return {"posted": False, "error": str(e)[:200]}
    if not body.get("ok"):
        return {"posted": False, "error": str(body)[:200]}
    return {"posted": True, "chat_id": chat_id, "chars": len(text), "message_id": body["result"].get("message_id")}


def main() -> int:
    parser = argparse.ArgumentParser(prog="clawryderz-digest")
    parser.add_argument("--preview", action="store_true",
                        help="Print the digest to stdout instead of posting.")
    parser.add_argument("--window-days", type=int, default=1,
                        help="Look back this many days when picking material (default 1).")
    parser.add_argument("--target", type=int, default=None,
                        help="Override target chat_id (e.g. send to your owner DM for testing).")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    result = run(preview=args.preview, window_days=args.window_days, target_chat_id=args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("posted") or args.preview or result.get("reason") in ("nothing to post", "disabled") else 1


if __name__ == "__main__":
    sys.exit(main())
