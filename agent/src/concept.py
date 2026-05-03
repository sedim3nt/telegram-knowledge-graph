"""Concept synthesizer — turns atomic + classify outputs into versioned concept.json files.

Spec: KG_SPEC.md (concept layer)

Reads:
  - agent/data/atomic/*.json        (raw messages, chronologically ordered by message_id)
  - agent/data/classify/*.json      (Haiku-generated metadata)
  - agent/data/canonical_topics.json (slug → canonical concept_id map)
  - agent/data/person_aliases.json  (alias → canonical author user_id map)

Writes:
  - vault/concepts/<concept_id>.json    (one per canonical concept; later rendered to .md)

Algorithm: process atoms oldest-first ("originals first" rule). For each atom:

  1. Resolve its classify topics → canonical concept_ids
  2. For each touched concept_id:
       - First time → spawn concept with v1 (this atom is establishing_messages)
       - Else → append to current version's consensus_messages
  3. If is_supersession with resolvable supersedes_topics → bump those concepts
       to a new version, mark prior version deprecated, this atom establishes the new
  4. If is_anti_pattern → record in concept's anti_patterns list (with citation)

Status assigned at end:
  - "active" if last message in last 14 days
  - "stable" otherwise
  - "deprecated" reserved for whole-concept supersession (v2 feature)
  - "contested" reserved for disagreement detection (v2 feature)
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR, VAULT_DIR

LOG = logging.getLogger("concept")

ATOMIC_DIR = DATA_DIR / "atomic"
CLASSIFY_DIR = DATA_DIR / "classify"
CANONICAL_TOPICS_PATH = DATA_DIR / "canonical_topics.json"
PERSON_ALIASES_PATH = DATA_DIR / "person_aliases.json"
CONCEPTS_DIR = VAULT_DIR / "concepts"

ACTIVE_WINDOW_DAYS = 14
SCHEMA = "concept.v1"


def _load_jsons(d: Path) -> dict[str, dict]:
    """Load all *.json files in d, keyed by stem (basename without .json)."""
    out: dict[str, dict] = {}
    if not d.exists():
        return out
    for p in d.glob("*.json"):
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            LOG.warning("skip malformed json %s: %s", p, e)
    return out


def _resolve_canonical_id(slug: str, slug_to_concept: dict[str, str]) -> str | None:
    return slug_to_concept.get(slug)


def _resolve_author(atomic: dict, alias_to_user_id: dict[str, int]) -> dict:
    """Return canonical author info {user_id, username, display_name}."""
    return {
        "user_id": atomic.get("author_id"),
        "username": atomic.get("author_username"),
        "display_name": atomic.get("author_display_name"),
        "is_bot": atomic.get("author_is_bot", False),
    }


def _new_concept(canonical: dict) -> dict:
    """Build an empty concept skeleton from canonical_topics.json entry."""
    return {
        "$schema": SCHEMA,
        "concept_id": canonical["id"],
        "title": canonical["title"],
        "category": canonical.get("category", "other"),
        "summary": canonical.get("summary", ""),
        "kind": "best-practice",
        "status": "active",
        "first_seen": None,
        "last_updated": None,
        "current_version": None,
        "versions": [],
        "anti_patterns": [],
        "_atoms": set(),  # internal, removed before write
    }


def _new_version(label: str, atom: dict) -> dict:
    return {
        "v": label,
        "established": atom["date_iso"],
        "established_by_atom": atom["id"],
        "establishing_messages": [atom["id"]],
        "consensus_messages": [],
        "deprecated": None,
        "deprecated_reason": None,
        "superseded_by": None,
        "current": True,
    }


def _bump_version(concept: dict, atom: dict, reason: str | None = None) -> None:
    """Mark current version deprecated and create the next."""
    cur = concept["versions"][-1] if concept["versions"] else None
    if cur is not None:
        cur["current"] = False
        cur["deprecated"] = atom["date_iso"]
        cur["deprecated_reason"] = reason or "(superseded; no explicit reason captured)"
        next_label = f"v{int(cur['v'][1:]) + 1}"
        cur["superseded_by"] = next_label
    else:
        next_label = "v1"
    concept["versions"].append(_new_version(next_label, atom))
    concept["current_version"] = next_label
    concept["last_updated"] = atom["date_iso"]


def synthesize() -> dict:
    """Run the full concept synthesis pass. Returns stats."""
    LOG.info("loading inputs...")
    atomics_by_stem = _load_jsons(ATOMIC_DIR)
    classifies_by_stem = _load_jsons(CLASSIFY_DIR)

    if not CANONICAL_TOPICS_PATH.exists():
        raise FileNotFoundError(f"canonical_topics.json missing — run agent/scripts/canonicalize_topics.py first")
    canonical = json.loads(CANONICAL_TOPICS_PATH.read_text(encoding="utf-8"))
    slug_to_concept: dict[str, str] = canonical["slug_to_concept"]
    concept_meta: dict[str, dict] = {c["id"]: c for c in canonical["canonical_concepts"]}

    alias_to_uid: dict[str, int] = {}
    if PERSON_ALIASES_PATH.exists():
        person_data = json.loads(PERSON_ALIASES_PATH.read_text(encoding="utf-8"))
        alias_to_uid = person_data.get("alias_to_user_id", {})

    # Sort atoms chronologically by message_id (== Telegram order; oldest first)
    atomics_sorted = sorted(
        atomics_by_stem.values(),
        key=lambda a: int(a.get("message_id", 0)),
    )
    LOG.info("processing %d atoms in chronological order", len(atomics_sorted))

    concepts: dict[str, dict] = {}
    contributors: dict[str, Counter] = {}  # concept_id -> Counter(author_handle: msg_count)
    related_pairs: dict[str, Counter] = {}  # concept_id -> Counter(other_concept_id: cooccur_count)

    for atom in atomics_sorted:
        aid = atom["id"]
        c = classifies_by_stem.get(aid)
        if c is None:
            continue  # no classification — skip
        if c.get("model") == "skipped-empty":
            continue  # empty atoms add no signal

        # Resolve all topic slugs to canonical concept ids; dedupe
        touched_ids: list[str] = []
        for slug in c.get("topics", []) or []:
            cid = _resolve_canonical_id(slug, slug_to_concept)
            if cid and cid not in touched_ids:
                touched_ids.append(cid)
        if not touched_ids:
            continue

        author = _resolve_author(atom, alias_to_uid)
        author_key = author.get("username") or f"id-{author.get('user_id')}"

        # Track concept ↔ concept co-occurrence (related edges)
        if len(touched_ids) > 1:
            for a_id in touched_ids:
                for b_id in touched_ids:
                    if a_id == b_id:
                        continue
                    related_pairs.setdefault(a_id, Counter())[b_id] += 1

        for cid in touched_ids:
            # Spawn concept on first sight
            if cid not in concepts:
                meta = concept_meta.get(cid)
                if meta is None:
                    LOG.warning("classify referenced unknown canonical id %s", cid)
                    continue
                concepts[cid] = _new_concept(meta)
                concepts[cid]["first_seen"] = atom["date_iso"]
                contributors.setdefault(cid, Counter())

            concept = concepts[cid]
            concept["_atoms"].add(aid)
            contributors[cid][author_key] += 1
            concept["last_updated"] = atom["date_iso"]

            # Decide whether this atom triggers a version bump for THIS concept.
            # is_supersession with supersedes_topics resolving to this concept = bump.
            is_super = bool(c.get("is_supersession"))
            superseded_canonical = set()
            for s_slug in c.get("supersedes_topics", []) or []:
                s_cid = _resolve_canonical_id(s_slug, slug_to_concept)
                if s_cid:
                    superseded_canonical.add(s_cid)

            if is_super and cid in superseded_canonical:
                # This concept itself is being superseded by this atom — bump
                reason = (atom.get("text") or "")[:240].strip()
                _bump_version(concept, atom, reason=reason)
            elif not concept["versions"]:
                # First atom establishing this concept → v1
                concept["versions"].append(_new_version("v1", atom))
                concept["current_version"] = "v1"
            else:
                # Just append to current version's consensus_messages
                concept["versions"][-1]["consensus_messages"].append(aid)

            # Anti-pattern record (cite atom + brief)
            if c.get("is_anti_pattern"):
                concept["anti_patterns"].append({
                    "claim": (atom.get("text") or "")[:200].strip(),
                    "atom_id": aid,
                    "author": author_key,
                    "date": atom["date_iso"],
                    "deep_link": atom.get("deep_link"),
                })

    # Compute status for each concept
    cutoff = datetime.now(timezone.utc) - timedelta(days=ACTIVE_WINDOW_DAYS)
    for concept in concepts.values():
        last = concept.get("last_updated")
        if last:
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                concept["status"] = "active" if last_dt >= cutoff else "stable"
            except ValueError:
                concept["status"] = "stable"

    # Finalize contributors (top-N) and related concepts
    for cid, concept in concepts.items():
        atom_count = len(concept["_atoms"])
        concept["atom_count"] = atom_count

        # Contributors: rank by message count, mark originator
        ctr = contributors.get(cid, Counter())
        first_atom_id = None
        if concept["versions"]:
            first_atom_id = concept["versions"][0].get("established_by_atom")
        first_atom = atomics_by_stem.get(first_atom_id, {}) if first_atom_id else {}
        originator_handle = (first_atom.get("author_username") or
                             f"id-{first_atom.get('author_id')}" if first_atom.get("author_id") else None)
        concept["contributors"] = []
        for handle, n in ctr.most_common(15):
            role = "originator" if handle == originator_handle else "consensus-builder"
            concept["contributors"].append({"handle": handle, "msg_count": n, "role": role})

        # Related: top co-occurring concepts
        rel = related_pairs.get(cid, Counter())
        concept["related"] = [other for other, _ in rel.most_common(8)]

        # Drop the internal _atoms set
        concept.pop("_atoms", None)

    # Write
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for concept in concepts.values():
        out = CONCEPTS_DIR / f"{concept['concept_id']}.json"
        out.write_text(
            json.dumps(concept, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        written += 1

    LOG.info("synthesized %d concepts → %s", written, CONCEPTS_DIR)
    return {
        "atoms_processed": len(atomics_sorted),
        "concepts_written": written,
        "concept_dir": str(CONCEPTS_DIR),
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    print(synthesize())
