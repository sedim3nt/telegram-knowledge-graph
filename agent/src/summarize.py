"""Concept + person consensus summaries — Claude Sonnet via the local CLI.

Produces a 2-3 sentence "where things stand" paragraph for each vault entry,
cached by content-hash so nightly runs only re-summarize entries whose source
material actually changed.

Output is written back into the same JSON file under fields:
  - concept.consensus_summary, concept.summary_hash
  - person.activity_summary, person.summary_hash

Render.py reads those fields and renders them as a callout above "## Current".
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, VAULT_DIR

LOG = logging.getLogger("summarize")

ATOMIC_DIR = DATA_DIR / "atomic"
CONCEPTS_DIR = VAULT_DIR / "concepts"
PEOPLE_DIR = VAULT_DIR / "people"

DEFAULT_MODEL = "sonnet"
ENV_MODEL_KEY = "CLAWRYDERZ_SUMMARIZE_MODEL"
ENV_WORKERS_KEY = "CLAWRYDERZ_SUMMARIZE_WORKERS"
CLI_TIMEOUT_S = 180
RATE_LIMIT_BACKOFF_S = (5, 15, 45)

# "Fresh enough" thresholds. A summary is regenerated when EITHER condition fires:
#   1. Concept gained ≥ DELTA_THRESHOLD new atoms since its last summary
#   2. ≥ STALE_DAYS days have passed AND any new atoms have been added
# Otherwise the existing summary is reused. Keeps nightly runs near-free.
CONCEPT_DELTA_THRESHOLD = 3
PERSON_DELTA_THRESHOLD = 5     # person summaries shift less per-message than concepts
STALE_DAYS = 14

CONCEPT_SYSTEM_PROMPT = """You write 2-3 sentence summaries describing the current state of a concept in a developer-community Telegram channel about AI agents, LLMs, coding tools, memory systems, and frameworks.

You will receive: the concept's title, a short canonical description, the current version's establishing message, several recent discussion messages, and any anti-patterns flagged. Your job is to synthesize a single succinct paragraph (2-3 sentences) that tells a reader visiting this page WHAT THE CURRENT CONSENSUS IS — what the community currently agrees on or recommends, and any active disagreements worth noting.

Rules:
- 2-3 sentences. Specific and concrete. Reference tools, models, files by name where they appear.
- Lead with the position the channel has converged on. Mention active debates only if they're prominent.
- Don't preamble ("The community thinks...") — state the position directly.
- Don't list contributors. Don't editorialize.
- Output ONLY the summary paragraph. No quotes, no markdown, no headers."""

PERSON_SYSTEM_PROMPT = """You write 2-sentence profiles describing the role of a contributor in a developer-community Telegram channel about AI agents, LLMs, coding tools, memory systems, and frameworks.

You will receive: their handle, total message count, kind histogram (claims/questions/answers/meta/etc.), and the top concepts they've contributed to. Your job is to summarize WHO THIS PERSON IS in the channel — their specialization and stance.

Rules:
- 2 sentences. Specific. Reference what they specialize in by name.
- Lead with their primary contribution mode (originator, debater, question-asker, builder, etc.).
- Don't preamble ("This person is...") — describe directly.
- Output ONLY the summary. No formatting."""


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    return env


def _hash_inputs(items: list[str]) -> str:
    canonical = json.dumps(sorted(items), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _is_stale(generated_at: str | None, days: int) -> bool:
    """True if the summary is older than `days` (or has no timestamp)."""
    if not generated_at:
        return True
    try:
        gen_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - gen_dt).days >= days


def _should_regenerate(
    *,
    has_summary: bool,
    new_hash: str,
    cached_hash: str | None,
    cur_input_count: int,
    cached_input_count: int | None,
    generated_at: str | None,
    delta_threshold: int,
    stale_days: int,
    force: bool,
) -> tuple[bool, str]:
    """Three-tier 'fresh enough' decision. Returns (regen?, reason)."""
    if force:
        return (True, "force")
    if not has_summary:
        return (True, "no prior summary")
    if cached_hash is None or cached_input_count is None:
        return (True, "missing cache metadata")
    # Same atoms → cached, no work
    if new_hash == cached_hash:
        return (False, "no atom changes")
    # Atoms changed — apply thresholds
    delta = cur_input_count - cached_input_count
    if delta >= delta_threshold:
        return (True, f"delta={delta} >= {delta_threshold}")
    if _is_stale(generated_at, stale_days):
        return (True, f"stale (>{stale_days}d) + has changes")
    return (False, f"delta={delta} below threshold and not stale")


def _claude_call(system_prompt: str, user_prompt: str, model: str) -> str:
    """Call claude CLI with retry on rate-limit. Returns the model's text response."""
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",
        "--disable-slash-commands",
        "--system-prompt", system_prompt,
    ]
    last_err: Exception | None = None
    for attempt in range(len(RATE_LIMIT_BACKOFF_S) + 1):
        proc = subprocess.run(
            cmd, input=user_prompt, capture_output=True, text=True,
            timeout=CLI_TIMEOUT_S, env=_subprocess_env(),
        )
        if proc.returncode != 0 and not proc.stdout:
            raise RuntimeError(f"claude CLI exit {proc.returncode}: stderr={proc.stderr.strip()[:200]}")
        envelope = json.loads(proc.stdout)
        if not envelope.get("is_error"):
            text = envelope.get("result", "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            return text.strip()
        result = envelope.get("result", "")
        is_rate_limit = "credit balance" in result.lower() or "too low" in result.lower()
        if is_rate_limit and attempt < len(RATE_LIMIT_BACKOFF_S):
            delay = RATE_LIMIT_BACKOFF_S[attempt]
            LOG.info("summarize: rate-limited (attempt %d), backing off %ds", attempt + 1, delay)
            import time as _time
            _time.sleep(delay)
            continue
        if is_rate_limit:
            raise RuntimeError(f"rate-limited: {result}")
        raise RuntimeError(f"claude error: {result}")
    if last_err:
        raise last_err
    raise RuntimeError("unreachable")


def _atom_lookup() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not ATOMIC_DIR.exists():
        return out
    for p in ATOMIC_DIR.glob("*.json"):
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
            out[a["id"]] = a
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------------------
# Concept summary
# ---------------------------------------------------------------------------

def _summarize_concept_one(
    path: Path, atoms: dict[str, dict], model: str, *, force: bool = False,
) -> tuple[str, bool, str | None]:
    """Returns (concept_id, was_updated, error_or_none)."""
    try:
        concept = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return (path.stem, False, f"read error: {e}")

    cur = next(
        (v for v in concept.get("versions", []) if v.get("v") == concept.get("current_version")),
        None,
    )
    if not cur:
        return (concept.get("concept_id", path.stem), False, "no current version")

    establishing = cur.get("establishing_messages", []) or []
    consensus = cur.get("consensus_messages", []) or []
    anti_pattern_ids = [ap.get("atom_id") for ap in concept.get("anti_patterns", []) if ap.get("atom_id")]
    input_ids = establishing + consensus + anti_pattern_ids
    if not input_ids:
        return (concept["concept_id"], False, "no input atoms")

    new_hash = _hash_inputs(input_ids)
    regen, reason = _should_regenerate(
        has_summary=bool(concept.get("consensus_summary")),
        new_hash=new_hash,
        cached_hash=concept.get("summary_hash"),
        cur_input_count=len(input_ids),
        cached_input_count=concept.get("summary_atom_count"),
        generated_at=concept.get("summary_generated_at"),
        delta_threshold=CONCEPT_DELTA_THRESHOLD,
        stale_days=STALE_DAYS,
        force=force,
    )
    if not regen:
        return (concept["concept_id"], False, None)
    LOG.info("regenerating concept %s (%s)", concept["concept_id"], reason)

    title = concept.get("title", concept["concept_id"])
    short_desc = concept.get("summary", "")

    quote_blocks: list[str] = []
    for atom_id in establishing[:1]:
        atom = atoms.get(atom_id)
        if atom and atom.get("text"):
            quote_blocks.append(
                f"ESTABLISHING ({atom.get('author_username') or '?'}, "
                f"{(atom.get('date_iso') or '')[:10]}): {atom['text'][:400]}"
            )
    for atom_id in consensus[-8:]:
        atom = atoms.get(atom_id)
        if atom and atom.get("text"):
            quote_blocks.append(
                f"({atom.get('author_username') or '?'}, "
                f"{(atom.get('date_iso') or '')[:10]}): {atom['text'][:300]}"
            )

    anti_chunks = [f"- {ap.get('claim','')[:200]}" for ap in concept.get("anti_patterns", [])[:5]]

    user_prompt = (
        f"Concept: {title}\n"
        f"Canonical description: {short_desc}\n\n"
        f"Recent messages on this topic (oldest establishing first, then most-recent discussion):\n"
        f"{chr(10).join(quote_blocks)}\n\n"
        f"Anti-patterns flagged:\n"
        f"{chr(10).join(anti_chunks) if anti_chunks else '(none)'}\n\n"
        "Write the 2-3 sentence current-consensus summary."
    )

    try:
        summary = _claude_call(CONCEPT_SYSTEM_PROMPT, user_prompt, model)
    except Exception as e:  # noqa: BLE001
        return (concept["concept_id"], False, str(e)[:200])

    if not summary:
        return (concept["concept_id"], False, "empty summary returned")

    concept["consensus_summary"] = summary
    concept["summary_hash"] = new_hash
    concept["summary_atom_count"] = len(input_ids)
    concept["summary_generated_at"] = datetime.now(timezone.utc).isoformat()
    concept["summary_model"] = model

    path.write_text(
        json.dumps(concept, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return (concept["concept_id"], True, None)


# ---------------------------------------------------------------------------
# Person summary
# ---------------------------------------------------------------------------

def _summarize_person_one(path: Path, model: str, *, force: bool = False) -> tuple[str, bool, str | None]:
    try:
        person = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return (path.stem, False, f"read error: {e}")

    if person.get("external") or person.get("total_messages", 0) == 0:
        return (person.get("username", path.stem), False, "external/no-msgs")

    by_kind = person.get("by_kind", {})
    concepts = person.get("concepts", []) or []

    input_keys = (
        sorted(c["concept_id"] for c in concepts)
        + [f"{k}:{v}" for k, v in sorted(by_kind.items())]
        + [f"total:{person.get('total_messages', 0)}"]
    )
    new_hash = _hash_inputs(input_keys)
    regen, reason = _should_regenerate(
        has_summary=bool(person.get("activity_summary")),
        new_hash=new_hash,
        cached_hash=person.get("summary_hash"),
        cur_input_count=person.get("total_messages", 0),
        cached_input_count=person.get("summary_atom_count"),
        generated_at=person.get("summary_generated_at"),
        delta_threshold=PERSON_DELTA_THRESHOLD,
        stale_days=STALE_DAYS,
        force=force,
    )
    if not regen:
        return (person.get("username", path.stem), False, None)
    LOG.info("regenerating person @%s (%s)", person.get("username"), reason)

    name = person.get("display_name") or person.get("username")
    handle = person.get("username")
    total = person.get("total_messages", 0)
    kind_str = ", ".join(
        f"{n} {k}"
        for k, n in sorted(by_kind.items(), key=lambda x: -x[1])[:6]
    )
    top_concepts_str = ", ".join(
        f"{c['title']} ({c['msg_count']})" for c in concepts[:8]
    )

    user_prompt = (
        f"Handle: @{handle}\n"
        f"Display name: {name}\n"
        f"Total messages: {total}\n"
        f"Activity profile: {kind_str}\n"
        f"Top concepts contributed to: {top_concepts_str or '(none)'}\n\n"
        "Write the 2-sentence profile."
    )

    try:
        summary = _claude_call(PERSON_SYSTEM_PROMPT, user_prompt, model)
    except Exception as e:  # noqa: BLE001
        return (handle, False, str(e)[:200])

    if not summary:
        return (handle, False, "empty summary returned")

    person["activity_summary"] = summary
    person["summary_hash"] = new_hash
    person["summary_atom_count"] = person.get("total_messages", 0)
    person["summary_generated_at"] = datetime.now(timezone.utc).isoformat()
    person["summary_model"] = model

    path.write_text(
        json.dumps(person, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return (handle, True, None)


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------

def synthesize(
    *,
    model: str | None = None,
    max_workers: int | None = None,
    force: bool = False,
) -> dict:
    chosen_model = model or os.environ.get(ENV_MODEL_KEY, DEFAULT_MODEL).strip()
    workers = max_workers or int(os.environ.get(ENV_WORKERS_KEY, "2"))
    workers = max(1, workers)

    atoms = _atom_lookup()
    concept_paths = sorted(CONCEPTS_DIR.glob("*.json"))
    person_paths = sorted(PEOPLE_DIR.glob("*.json"))

    LOG.info(
        "summarize: %d concepts + %d people (model=%s, workers=%d, force=%s, "
        "thresholds: concepts=%d new atoms / people=%d / stale=%dd)",
        len(concept_paths), len(person_paths), chosen_model, workers, force,
        CONCEPT_DELTA_THRESHOLD, PERSON_DELTA_THRESHOLD, STALE_DAYS,
    )

    stats = {
        "concept_updated": 0, "concept_cached": 0, "concept_failed": 0,
        "person_updated": 0, "person_cached": 0, "person_failed": 0,
        "model": chosen_model, "workers": workers, "force": force,
    }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        c_futs = [
            executor.submit(_summarize_concept_one, p, atoms, chosen_model, force=force)
            for p in concept_paths
        ]
        for fut in as_completed(c_futs):
            cid, updated, err = fut.result()
            if err:
                if err.startswith("rate-limited"):
                    LOG.warning("concept rate-limited: %s", cid)
                    stats["concept_failed"] += 1
                elif err in ("no current version", "no input atoms"):
                    pass
                else:
                    LOG.warning("concept summary failed for %s: %s", cid, err)
                    stats["concept_failed"] += 1
            elif updated:
                stats["concept_updated"] += 1
            else:
                stats["concept_cached"] += 1

        p_futs = [executor.submit(_summarize_person_one, p, chosen_model, force=force) for p in person_paths]
        for fut in as_completed(p_futs):
            handle, updated, err = fut.result()
            if err:
                if err.startswith("rate-limited"):
                    LOG.warning("person rate-limited: %s", handle)
                    stats["person_failed"] += 1
                elif err == "external/no-msgs":
                    pass
                else:
                    LOG.warning("person summary failed for %s: %s", handle, err)
                    stats["person_failed"] += 1
            elif updated:
                stats["person_updated"] += 1
            else:
                stats["person_cached"] += 1

    LOG.info("summarize done: %s", stats)
    return stats


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    print(synthesize())
