"""One-shot: cluster person mentions in classify outputs to actual Telegram
authors via Claude Sonnet.

Why this exists: people are referenced multiple ways across messages —
"Jamie", "@thezigelbot", "thezigelbot", "zigelbaum" might all be the same
human; "trach" / "@tracheopteryx" might be a different person. We need a
mapping from soft alias → canonical Telegram user_id so concept synthesis
can correctly attribute contributions.

Authors are well-defined (Telegram exposes a stable user_id and username on
every message). Person ENTITIES in the classify output are soft mentions
that may or may not resolve to a known author.

Output: agent/data/person_aliases.json with shape:

  {
    "generated_at": "...",
    "model": "...",
    "humans": [
      {
        "canonical_username": "zigelbaum",
        "canonical_user_id": 12345678,
        "display_name": "Jamie Zigelbaum",
        "aliases": ["Jamie", "@thezigelbot", "thezigelbot", "zigelbaum"],
        "is_bot_persona": false,
        "linked_bots": [{"username": "thezigelbot", "user_id": 8456959780}],
        "confidence": "high",
        "notes": ""
      }
    ],
    "alias_to_user_id": {
      "Jamie": 12345678,
      "@thezigelbot": 12345678,
      ...
    },
    "unresolved": ["mention1", "mention2"]
  }

Run:
    agent/.venv/bin/python agent/scripts/resolve_persons.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agent"))

ATOMIC_DIR = REPO_ROOT / "agent" / "data" / "atomic"
CLASSIFY_DIR = REPO_ROOT / "agent" / "data" / "classify"
OUT_PATH = REPO_ROOT / "agent" / "data" / "person_aliases.json"

MODEL = "sonnet"
EFFORT = "low"
CLI_TIMEOUT_S = 1500

SYSTEM_PROMPT = """You resolve person aliases for a Telegram knowledge graph.

You will receive two lists:

1. AUTHORS — the actual Telegram users who have posted in the channel, with stable user_id, username, and display_name. These are ground truth.

2. MENTIONS — names, handles, and references extracted from message text by a classifier (e.g. "Jamie", "@thezigelbot", "trach"). Many are different ways to refer to the same human author. Some refer to bots. Some may be famous people outside the channel.

Your job: cluster the mentions into HUMANS, mapping each cluster to an author when possible.

Output ONLY a single JSON object — no prose, no markdown fences:

{
  "humans": [
    {
      "canonical_username": "zigelbaum",                  // pick the best author username if mapped, else the most stable mention
      "canonical_user_id": 12345678,                      // null if not mapped to an author
      "display_name": "Jamie Zigelbaum",                  // human readable
      "aliases": ["Jamie", "@thezigelbot", "thezigelbot", "zigelbaum"],
      "is_bot_persona": false,                            // true if this cluster is primarily a bot account, not a human
      "linked_bots": [                                    // bots operated by or representing this human
        {"username": "thezigelbot", "user_id": 8456959780}
      ],
      "confidence": "high" | "medium" | "low",
      "notes": ""                                          // any caveats; e.g. "trach intentionally separate from zigelbaum"
    }
  ],
  "unresolved_mentions": ["..."]                           // mentions you can't confidently cluster
}

Rules:
- An author MAY have zero mentions (they post but are never referenced by name).
- A mention may not match any author (could be an external person — e.g. "Steve Yegge", "Andrej Karpathy"). Cluster these into humans too with `canonical_user_id: null`.
- If a bot's account is operated by a known human author, list the bot in `linked_bots` of the human's cluster, NOT as a separate human. The user-id of the bot still matters for filtering bot-authored messages, hence `linked_bots`.
- If you're uncertain whether two mentions refer to the same person, KEEP THEM SEPARATE and set `confidence: low`. Better to under-merge than over-merge.
- Strip leading "@" from aliases when matching to author usernames (which don't carry the @).
- Resolve case-insensitively (Sedim3nt = sedim3nt).
- Famous people from outside the channel ARE valid humans — include them with `canonical_user_id: null`.
- Aim for high precision over high recall — `unresolved_mentions` is a fine landing place for ambiguous tokens."""

USER_TEMPLATE = """AUTHORS (ground truth — every Telegram account that has posted in the channel):

{authors_block}

MENTIONS (extracted by classifier from message text, with frequencies):

{mentions_block}

Cluster the mentions into humans per the schema. Output JSON only."""


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    return env


def _parse_json_lenient(s: str) -> dict:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def collect_authors() -> list[dict]:
    """Return list of unique authors from atomic.json files."""
    authors: dict[int, dict] = {}
    for p in sorted(ATOMIC_DIR.glob("*.json")):
        a = json.loads(p.read_text(encoding="utf-8"))
        uid = a.get("author_id")
        if uid is None:
            continue
        if uid not in authors:
            authors[uid] = {
                "user_id": uid,
                "username": a.get("author_username"),
                "display_name": a.get("author_display_name"),
                "is_bot": a.get("author_is_bot", False),
                "msg_count": 0,
            }
        authors[uid]["msg_count"] += 1
    return sorted(authors.values(), key=lambda r: -r["msg_count"])


def collect_person_mentions() -> Counter:
    """Return Counter of person-kind entity mentions across classify outputs."""
    counter: Counter = Counter()
    for p in sorted(CLASSIFY_DIR.glob("*.json")):
        c = json.loads(p.read_text(encoding="utf-8"))
        for e in c.get("entities", []):
            if e.get("kind") == "person":
                t = (e.get("text") or "").strip()
                if t:
                    counter[t] += 1
    return counter


def main() -> int:
    authors = collect_authors()
    mentions = collect_person_mentions()
    if not authors:
        print("ERR no atomic data", file=sys.stderr)
        return 1
    if not mentions:
        print("ERR no person mentions in classify data", file=sys.stderr)
        return 1

    print(f"== Resolving aliases: {len(authors)} authors, {len(mentions)} mention strings ==")

    authors_block = "\n".join(
        f"- user_id={a['user_id']}  username={a['username']!r}  display={a['display_name']!r}  "
        f"is_bot={a['is_bot']}  msgs={a['msg_count']}"
        for a in authors
    )
    mentions_block = "\n".join(f"{c}\t{m}" for m, c in mentions.most_common())

    user_prompt = USER_TEMPLATE.format(
        authors_block=authors_block,
        mentions_block=mentions_block,
    )

    cmd = [
        "claude", "-p",
        "--model", MODEL,
        "--effort", EFFORT,
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",
        "--disable-slash-commands",
        "--system-prompt", SYSTEM_PROMPT,
    ]
    print(f"Calling claude --model {MODEL} --effort {EFFORT} (may take 30-90s)...")
    proc = subprocess.run(
        cmd, input=user_prompt, capture_output=True, text=True,
        timeout=CLI_TIMEOUT_S, env=_subprocess_env(),
    )
    if proc.returncode != 0:
        print(f"ERR claude exit {proc.returncode}: {proc.stderr[:500]}", file=sys.stderr)
        return 1

    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        print(f"ERR claude reported error: {envelope.get('result')}", file=sys.stderr)
        return 1

    raw = _parse_json_lenient(envelope["result"])
    humans = raw.get("humans", [])
    unresolved = raw.get("unresolved_mentions", [])
    if not humans:
        print(f"ERR no humans in output: {raw}", file=sys.stderr)
        return 1

    # Build alias → user_id reverse map (skip None user_ids)
    alias_to_user_id: dict[str, int] = {}
    for h in humans:
        uid = h.get("canonical_user_id")
        if uid is None:
            continue
        for alias in h.get("aliases", []):
            alias_to_user_id[alias] = uid

    out = {
        "$schema": "person_aliases.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "input_author_count": len(authors),
        "input_mention_count": len(mentions),
        "human_clusters": len(humans),
        "unresolved_mention_count": len(unresolved),
        "humans": humans,
        "alias_to_user_id": alias_to_user_id,
        "unresolved_mentions": unresolved,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nOK wrote {OUT_PATH}")
    print(f"   {len(humans)} human clusters")
    print(f"   {len(alias_to_user_id)} alias mappings")
    print(f"   {len(unresolved)} unresolved mentions")
    mapped = sum(1 for h in humans if h.get("canonical_user_id"))
    external = sum(1 for h in humans if not h.get("canonical_user_id"))
    bots = sum(1 for h in humans if h.get("is_bot_persona"))
    print(f"\nBreakdown: {mapped} mapped to authors, {external} external/unmapped, {bots} bot-persona clusters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
