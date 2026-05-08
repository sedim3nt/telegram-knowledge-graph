"""Person aggregator — turns atomic + classify outputs into person.json files,
one per resolved human cluster.

Reads:
  - agent/data/atomic/*.json
  - agent/data/classify/*.json
  - agent/data/person_aliases.json   (canonical clusters)
  - vault/concepts/*.json            (to compute per-person concept contributions)

Writes:
  - vault/people/<canonical_username>.json

The aggregation:
  - Aliases mapped to a Telegram user_id get full author records (real msg counts,
    by_kind histogram, first/last seen, concepts contributed to).
  - External humans (canonical_user_id is null — referenced but not in channel)
    get a slim record so the graph can still link to them.
  - Bot personas attributed to a human are folded into the human's record as
    `linked_bots[]`; the bot's own messages get a separate person record marked
    `is_bot_persona: true` for filtering.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

from .config import DATA_DIR, VAULT_DIR

LOG = logging.getLogger("person")

ATOMIC_DIR = DATA_DIR / "atomic"
CLASSIFY_DIR = DATA_DIR / "classify"
PERSON_ALIASES_PATH = DATA_DIR / "person_aliases.json"
CONCEPTS_DIR = VAULT_DIR / "concepts"
PEOPLE_DIR = VAULT_DIR / "people"

SCHEMA = "person.v1"
SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(s: str | None) -> str:
    if not s:
        return "unknown"
    return SLUG_RE.sub("-", s.lower()).strip("-") or "unknown"


def _load_jsons(d: Path) -> list[dict]:
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            LOG.warning("skip malformed json %s: %s", p, e)
    return out


def synthesize() -> dict:
    LOG.info("loading inputs...")
    atomics = _load_jsons(ATOMIC_DIR)
    classifies = {c["atomic_id"]: c for c in _load_jsons(CLASSIFY_DIR)}
    if not PERSON_ALIASES_PATH.exists():
        raise FileNotFoundError("person_aliases.json missing — run agent/scripts/resolve_persons.py first")
    person_data = json.loads(PERSON_ALIASES_PATH.read_text(encoding="utf-8"))
    humans: list[dict] = person_data["humans"]

    concepts = _load_jsons(CONCEPTS_DIR)

    # Build a uid → human-cluster lookup for fast reverse mapping.
    # Also expose linked-bot user_ids → host human (so bot-authored msgs attribute to host).
    host_by_uid: dict[int, dict] = {}
    bot_uids: set[int] = set()
    for h in humans:
        cuid = h.get("canonical_user_id")
        if cuid:
            host_by_uid[cuid] = h
        for lb in h.get("linked_bots", []) or []:
            buid = lb.get("user_id")
            if buid:
                bot_uids.add(buid)
                host_by_uid[buid] = h  # author=bot's uid → resolves to host human

    # For external humans (canonical_user_id is null), still create records so
    # they can be cited in concepts/graph. Keyed by canonical_username.
    external = [h for h in humans if not h.get("canonical_user_id")]

    # Aggregate stats per host human (by canonical_user_id)
    person_records: dict[str, dict] = {}  # username → record

    for h in humans:
        cuid = h.get("canonical_user_id")
        username = h.get("canonical_username") or _slug(h.get("display_name"))
        if h.get("is_bot_persona"):
            # Bot personas get their own slim record so atom counts stay correct
            rec = _new_record(h, is_bot=True)
        else:
            rec = _new_record(h, is_bot=False)
        person_records[username] = rec

    # Walk atoms — accumulate per resolved human
    for atom in atomics:
        author_uid = atom.get("author_id")
        if author_uid is None:
            continue
        host = host_by_uid.get(author_uid)
        if host is None:
            # Author not in our human map — usually because all their messages
            # had no person mentions to cluster against. Make a stub record.
            username = atom.get("author_username") or f"id-{author_uid}"
            if username not in person_records:
                person_records[username] = {
                    "$schema": SCHEMA,
                    "username": username,
                    "user_id": author_uid,
                    "display_name": atom.get("author_display_name"),
                    "aliases": [],
                    "is_bot": atom.get("author_is_bot", False),
                    "is_bot_persona": False,
                    "external": False,
                    "linked_bots": [],
                    "linked_to_host": None,
                    "first_message_at": None,
                    "last_message_at": None,
                    "total_messages": 0,
                    "by_kind": {},
                    "concepts": [],
                    "atom_count": 0,
                }
            rec = person_records[username]
        else:
            rec = person_records[host["canonical_username"]]
            # If this atom was sent by a bot whose host is a human, we still
            # count it but flag it; the host's record will get the credit.
            if author_uid in bot_uids and not host.get("is_bot_persona"):
                # noop here, but we add a counter below
                pass

        rec["total_messages"] += 1
        date_iso = atom.get("date_iso")
        if date_iso:
            if rec["first_message_at"] is None or date_iso < rec["first_message_at"]:
                rec["first_message_at"] = date_iso
            if rec["last_message_at"] is None or date_iso > rec["last_message_at"]:
                rec["last_message_at"] = date_iso

        # Kind histogram from classify
        c = classifies.get(atom["id"])
        if c:
            kind = c.get("kind", "unknown")
            rec["by_kind"][kind] = rec["by_kind"].get(kind, 0) + 1

    # Compute per-person concept contributions by walking concept records
    # (concept.contributors[].handle is keyed by username/id-fallback)
    concept_contribs_by_handle: dict[str, list] = {}
    for concept in concepts:
        for contrib in concept.get("contributors", []):
            handle = contrib.get("handle")
            if not handle:
                continue
            concept_contribs_by_handle.setdefault(handle, []).append({
                "concept_id": concept["concept_id"],
                "title": concept["title"],
                "category": concept.get("category"),
                "msg_count": contrib.get("msg_count", 0),
                "role": contrib.get("role"),
            })

    for username, rec in person_records.items():
        contribs = concept_contribs_by_handle.get(username, [])
        contribs.sort(key=lambda c: -c["msg_count"])
        rec["concepts"] = contribs

    # Add external humans (no atoms; they're just referenced)
    for h in external:
        username = h.get("canonical_username") or _slug(h.get("display_name"))
        if username in person_records:
            continue  # already exists somehow
        person_records[username] = _new_record(h, is_bot=False, external=True)

    # Drop empty bot-persona records that never had a host (orphans)
    person_records = {k: v for k, v in person_records.items() if v["total_messages"] > 0 or v.get("external")}

    # Write. Carry forward Sonnet-generated summary fields from any prior file
    # so summarize.py's hash-cache survives the nightly rebuild.
    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_FIELDS = (
        "activity_summary",
        "summary_hash",
        "summary_atom_count",
        "summary_generated_at",
        "summary_model",
    )
    written = 0
    for username, rec in person_records.items():
        out = PEOPLE_DIR / f"{_slug(username)}.json"
        if out.exists():
            try:
                prior = json.loads(out.read_text(encoding="utf-8"))
                for f in SUMMARY_FIELDS:
                    if f in prior:
                        rec[f] = prior[f]
            except json.JSONDecodeError:
                pass
        out.write_text(
            json.dumps(rec, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        written += 1

    LOG.info("synthesized %d people → %s", written, PEOPLE_DIR)
    return {"people_written": written, "people_dir": str(PEOPLE_DIR)}


def _new_record(h: dict, *, is_bot: bool = False, external: bool = False) -> dict:
    return {
        "$schema": SCHEMA,
        "username": h.get("canonical_username"),
        "user_id": h.get("canonical_user_id"),
        "display_name": h.get("display_name"),
        "aliases": h.get("aliases", []),
        "is_bot": is_bot,
        "is_bot_persona": h.get("is_bot_persona", False),
        "external": external,
        "linked_bots": h.get("linked_bots", []),
        "linked_to_host": None,
        "confidence": h.get("confidence"),
        "notes": h.get("notes", ""),
        "first_message_at": None,
        "last_message_at": None,
        "total_messages": 0,
        "by_kind": {},
        "concepts": [],
        "atom_count": 0,
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    print(synthesize())
