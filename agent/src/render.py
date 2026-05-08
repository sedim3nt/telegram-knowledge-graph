"""JSON → Markdown renderer for vault/concepts/*.json and vault/people/*.json.

Output is Quartz-friendly: YAML frontmatter + body with sections.

Atomic deep-links go straight to t.me — atoms themselves are never published.

Also emits vault/_meta/vault-bundle.json — a compact, ask_server-friendly
representation of the whole knowledge base. The Ask Bridg3 server reads this
once per request and prepends it to the model prompt so prompt caching works.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, VAULT_DIR

LOG = logging.getLogger("render")

ATOMIC_DIR = DATA_DIR / "atomic"
CONCEPTS_DIR = VAULT_DIR / "concepts"
PEOPLE_DIR = VAULT_DIR / "people"
META_DIR = VAULT_DIR / "_meta"
VAULT_BUNDLE_PATH = META_DIR / "vault-bundle.json"


def _atom_lookup() -> dict[str, dict]:
    """Map atom_id → atom dict. Used to resolve citations."""
    out: dict[str, dict] = {}
    for p in ATOMIC_DIR.glob("*.json"):
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
            out[a["id"]] = a
        except json.JSONDecodeError:
            continue
    return out


def _short_date(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return iso[:10]


def _cite(atom: dict | None) -> str:
    """Format a one-line citation as a raw HTML anchor.

    Uses target="topw" so all Telegram links open in the SAME named window
    (a single companion tab) instead of spawning a new tab per click.
    rel="noopener noreferrer" hardens against the linked page accessing the
    opener (security baseline for any cross-origin link).
    """
    if not atom:
        return "_(unknown atom)_"
    author = atom.get("author_username") or atom.get("author_display_name") or "anon"
    date = _short_date(atom.get("date_iso"))
    link = atom.get("deep_link") or "#"
    return f'<a href="{link}" target="topw" rel="noopener noreferrer">@{author} on {date}</a>'


def _quote(atom: dict | None, *, max_len: int = 240) -> str:
    if not atom:
        return ""
    text = (atom.get("text") or "").strip().replace("\n", " ")
    if not text:
        return ""
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return f"> {text}\n> — {_cite(atom)}"


def render_concept(concept: dict, atoms: dict[str, dict]) -> str:
    """Render a single concept JSON as a Markdown page."""
    cid = concept["concept_id"]
    title = concept["title"]
    category = concept.get("category", "other")
    status = concept.get("status", "active")
    summary = concept.get("summary", "")
    versions = concept.get("versions", [])
    current_v = concept.get("current_version") or "v1"

    # YAML-safe quoting for any field that might contain special chars
    def _yaml_str(s: str | None) -> str:
        if s is None:
            return '""'
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

    # Frontmatter — Quartz reads this for graph + index
    frontmatter_lines = [
        "---",
        f"title: {_yaml_str(title)}",
        f"concept_id: {cid}",
        f"category: {category}",
        f"status: {status}",
        f"current_version: {current_v}",
        f"first_seen: {_short_date(concept.get('first_seen'))}",
        f"last_updated: {_short_date(concept.get('last_updated'))}",
        f"atom_count: {concept.get('atom_count', 0)}",
    ]
    related = concept.get("related", [])
    if related:
        frontmatter_lines.append("related:")
        for r in related:
            frontmatter_lines.append(f"  - {r}")
    contributors = concept.get("contributors", [])
    if contributors:
        handles = [c["handle"] for c in contributors[:10]]
        frontmatter_lines.append(f"contributors: [{', '.join(handles)}]")
    frontmatter_lines.append("---")
    parts = ["\n".join(frontmatter_lines), ""]

    parts.append(f"# {title}")
    parts.append("")
    if summary:
        parts.append(f"> {summary}")
        parts.append("")

    # Status badge line
    badge = f"**Status:** `{status}` · **Version:** `{current_v}` · **{concept.get('atom_count', 0)} message{'s' if concept.get('atom_count', 0) != 1 else ''}** · {len(versions)} version{'s' if len(versions) != 1 else ''}"
    parts.append(badge)
    parts.append("")

    # Sonnet-generated consensus summary (cached by hash). Displayed as an
    # Obsidian-style callout so it pops between the status line and the
    # full lineage below.
    consensus_summary = concept.get("consensus_summary")
    if consensus_summary:
        parts.append("> [!info] Where things stand")
        for line in consensus_summary.splitlines():
            parts.append(f"> {line}" if line.strip() else ">")
        parts.append("")

    # Current version — establishing quote + 3 most recent quotes (full text)
    # + up to 10 earlier mentions as links + count of any beyond.
    cur = next((v for v in versions if v.get("v") == current_v), versions[-1] if versions else None)
    if cur:
        established_date = _short_date(cur.get("established"))
        parts.append(f"## Current ({current_v}, since {established_date})")
        parts.append("")
        est_atom = atoms.get(cur.get("established_by_atom"))
        q = _quote(est_atom)
        if q:
            parts.append(q)
            parts.append("")

        consensus = cur.get("consensus_messages", []) or []
        if consensus:
            # consensus_messages is appended chronologically (oldest → newest)
            # during synthesis. Reverse to get newest-first ordering.
            newest_first = consensus[::-1]
            spelled_out = newest_first[:3]      # top 3 full quotes
            earlier_links = newest_first[3:13]  # next 10 as links
            remaining = len(newest_first) - len(spelled_out) - len(earlier_links)

            if spelled_out:
                parts.append(f"**Most recent discussion** ({len(consensus)} message{'s' if len(consensus) != 1 else ''} total):")
                parts.append("")
                for atom_id in spelled_out:
                    atom = atoms.get(atom_id)
                    if atom:
                        full_quote = _quote(atom)
                        if full_quote:
                            parts.append(full_quote)
                            parts.append("")

            if earlier_links:
                parts.append("**Earlier mentions:**")
                parts.append("")
                for atom_id in earlier_links:
                    atom = atoms.get(atom_id)
                    if atom:
                        parts.append(f"- {_cite(atom)}")
                if remaining > 0:
                    parts.append(f"- _… and {remaining} more_")
                parts.append("")

    # Lineage
    if len(versions) > 1:
        parts.append("## Lineage")
        parts.append("")
        for v in versions:
            v_label = v.get("v")
            estab = _short_date(v.get("established"))
            depr = _short_date(v.get("deprecated")) if v.get("deprecated") else "present"
            cur_marker = " — _current_" if v.get("current") else ""
            parts.append(f"### {v_label} ({estab} → {depr}){cur_marker}")
            parts.append("")
            est_atom = atoms.get(v.get("established_by_atom"))
            if est_atom:
                parts.append(_quote(est_atom, max_len=180))
                parts.append("")
            if v.get("superseded_by"):
                reason = v.get("deprecated_reason", "")
                if reason:
                    parts.append(f"**Superseded by `{v['superseded_by']}`** — {reason[:200]}")
                else:
                    parts.append(f"**Superseded by `{v['superseded_by']}`.**")
                parts.append("")
            consensus_n = len(v.get("consensus_messages", []) or [])
            if consensus_n:
                parts.append(f"_Built on by {consensus_n} message{'s' if consensus_n != 1 else ''}._")
                parts.append("")

    # Anti-patterns
    aps = concept.get("anti_patterns", [])
    if aps:
        parts.append(f"## Anti-Patterns ({len(aps)})")
        parts.append("")
        parts.append("> Things tried in this space that the channel flagged as failures or warnings.")
        parts.append("")
        for ap in aps[:15]:
            atom = atoms.get(ap.get("atom_id"))
            claim = ap.get("claim", "").replace("\n", " ").strip()
            parts.append(f"- **{claim}** — {_cite(atom)}")
        if len(aps) > 15:
            parts.append(f"- _… and {len(aps) - 15} more_")
        parts.append("")

    # Contributors
    if contributors:
        parts.append("## Contributors")
        parts.append("")
        for c in contributors[:12]:
            handle = c["handle"]
            n = c.get("msg_count", 0)
            role = c.get("role", "")
            parts.append(f"- [[people/{handle}|@{handle}]] — {role}, {n} message{'s' if n != 1 else ''}")
        parts.append("")

    # Related concepts
    if related:
        parts.append("## Related Concepts")
        parts.append("")
        for r in related:
            parts.append(f"- [[concepts/{r}]]")
        parts.append("")

    return "\n".join(parts)


def render_person(person: dict) -> str:
    """Render a single person JSON as a Markdown page."""
    username = person.get("username") or "unknown"
    display = person.get("display_name") or username
    is_bot = person.get("is_bot") or person.get("is_bot_persona")
    external = person.get("external")
    aliases = person.get("aliases", []) or []
    linked_bots = person.get("linked_bots", []) or []
    by_kind = person.get("by_kind", {}) or {}
    concepts = person.get("concepts", []) or []
    total = person.get("total_messages", 0)

    # YAML-safe escape: quote any string that might confuse the YAML parser.
    def _yaml_str(s: str | None) -> str:
        if s is None:
            return '""'
        # Always wrap in double quotes; escape any embedded double quotes.
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

    parts = [
        "---",
        f"title: {_yaml_str('@' + username)}",
        f"username: {_yaml_str(username)}",
        f"display_name: {_yaml_str(display)}",
        f"is_bot: {str(bool(is_bot)).lower()}",
        f"external: {str(bool(external)).lower()}",
        f"total_messages: {total}",
        f"first_seen: {_short_date(person.get('first_message_at'))}",
        f"last_seen: {_short_date(person.get('last_message_at'))}",
        "---",
        "",
    ]
    parts.append(f"# {display} (@{username})")
    parts.append("")

    if external:
        parts.append("**External reference** — mentioned in the channel but not a member.")
        parts.append("")
    elif is_bot:
        parts.append("**Bot account.**")
        parts.append("")

    if total > 0:
        parts.append(f"{total} message{'s' if total != 1 else ''} from {_short_date(person.get('first_message_at'))} to {_short_date(person.get('last_message_at'))}.")
        parts.append("")

    # Sonnet-generated activity summary (cached by hash). Displayed as a
    # callout so it surfaces above the activity profile.
    activity_summary = person.get("activity_summary")
    if activity_summary:
        parts.append("> [!info] Profile")
        for line in activity_summary.splitlines():
            parts.append(f"> {line}" if line.strip() else ">")
        parts.append("")

    if aliases:
        parts.append(f"_Other names used to refer to this person: {', '.join(aliases)}_")
        parts.append("")

    if linked_bots:
        parts.append("## Linked Bots")
        parts.append("")
        for lb in linked_bots:
            parts.append(f"- @{lb.get('username')} (user_id={lb.get('user_id')})")
        parts.append("")

    if by_kind:
        parts.append("## Activity Profile")
        parts.append("")
        for kind, n in sorted(by_kind.items(), key=lambda x: -x[1]):
            parts.append(f"- {n} {kind}")
        parts.append("")

    # Originated vs contributed-to concepts
    originated = [c for c in concepts if c.get("role") == "originator"]
    contributed = [c for c in concepts if c.get("role") != "originator"]

    if originated:
        parts.append(f"## Concepts Originated ({len(originated)})")
        parts.append("")
        for c in originated[:25]:
            parts.append(f"- [[concepts/{c['concept_id']}]] — {c['msg_count']} message{'s' if c['msg_count'] != 1 else ''}")
        parts.append("")

    if contributed:
        parts.append(f"## Concepts Contributed To ({len(contributed)})")
        parts.append("")
        for c in contributed[:25]:
            parts.append(f"- [[concepts/{c['concept_id']}]] — {c['msg_count']} message{'s' if c['msg_count'] != 1 else ''}")
        parts.append("")

    return "\n".join(parts)


def _bundle_concept(concept: dict, atoms: dict[str, dict]) -> dict:
    """Compact concept dict for the ask bundle — summaries + a few quotes."""
    cur = next(
        (v for v in concept.get("versions", []) if v.get("v") == concept.get("current_version")),
        None,
    )
    quotes: list[dict] = []
    if cur:
        est_atom = atoms.get(cur.get("established_by_atom"))
        if est_atom and est_atom.get("text"):
            quotes.append({
                "kind": "establishing",
                "author": est_atom.get("author_username"),
                "date": _short_date(est_atom.get("date_iso")),
                "text": (est_atom.get("text") or "").strip()[:280],
                "link": est_atom.get("deep_link"),
            })
        # Most recent 3 discussion messages
        consensus = (cur.get("consensus_messages") or [])[-3:]
        for atom_id in consensus:
            a = atoms.get(atom_id)
            if a and a.get("text"):
                quotes.append({
                    "kind": "discussion",
                    "author": a.get("author_username"),
                    "date": _short_date(a.get("date_iso")),
                    "text": (a.get("text") or "").strip()[:240],
                    "link": a.get("deep_link"),
                })

    anti = []
    for ap in (concept.get("anti_patterns") or [])[:5]:
        a = atoms.get(ap.get("atom_id"))
        anti.append({
            "claim": (ap.get("claim") or "").strip()[:200],
            "author": a.get("author_username") if a else None,
        })

    return {
        "id": concept["concept_id"],
        "title": concept.get("title"),
        "category": concept.get("category"),
        "status": concept.get("status"),
        "summary": (concept.get("summary") or "").strip(),
        "consensus_summary": (concept.get("consensus_summary") or "").strip(),
        "atom_count": concept.get("atom_count", 0),
        "first_seen": _short_date(concept.get("first_seen")),
        "last_updated": _short_date(concept.get("last_updated")),
        "related": concept.get("related", []),
        "contributors": [c.get("handle") for c in (concept.get("contributors") or [])[:8]],
        "quotes": quotes,
        "anti_patterns": anti,
    }


def _bundle_person(person: dict) -> dict | None:
    """Compact person dict for the ask bundle. Skips externals + zero-message rows."""
    if person.get("external") or person.get("total_messages", 0) == 0:
        return None
    return {
        "username": person.get("username"),
        "display_name": person.get("display_name"),
        "is_bot": bool(person.get("is_bot") or person.get("is_bot_persona")),
        "total_messages": person.get("total_messages", 0),
        "first_seen": _short_date(person.get("first_message_at")),
        "last_seen": _short_date(person.get("last_message_at")),
        "activity_summary": (person.get("activity_summary") or "").strip(),
        "top_concepts": [
            {
                "id": c["concept_id"],
                "msg_count": c.get("msg_count", 0),
                "role": c.get("role"),
            }
            for c in (person.get("concepts") or [])[:8]
        ],
    }


def build_vault_bundle() -> dict:
    """Build the JSON document Ask Bridg3 reads as its world model."""
    atoms = _atom_lookup()

    concepts: list[dict] = []
    for p in sorted(CONCEPTS_DIR.glob("*.json")):
        try:
            concepts.append(_bundle_concept(json.loads(p.read_text(encoding="utf-8")), atoms))
        except json.JSONDecodeError:
            continue

    people: list[dict] = []
    for p in sorted(PEOPLE_DIR.glob("*.json")):
        try:
            entry = _bundle_person(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
        if entry is not None:
            people.append(entry)

    return {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "concept_count": len(concepts),
        "person_count": len(people),
        "concepts": concepts,
        "people": people,
    }


def write_vault_bundle() -> Path:
    META_DIR.mkdir(parents=True, exist_ok=True)
    bundle = build_vault_bundle()
    VAULT_BUNDLE_PATH.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    size_kb = VAULT_BUNDLE_PATH.stat().st_size / 1024
    LOG.info(
        "vault bundle: %d concepts + %d people (%.1f KB) → %s",
        bundle["concept_count"], bundle["person_count"], size_kb, VAULT_BUNDLE_PATH,
    )
    return VAULT_BUNDLE_PATH


def render_all() -> dict:
    """Render every concept and person JSON to .md siblings + vault bundle."""
    atoms = _atom_lookup()
    LOG.info("loaded %d atoms for citation lookup", len(atoms))

    concept_count = person_count = 0
    for p in CONCEPTS_DIR.glob("*.json"):
        try:
            concept = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        md = render_concept(concept, atoms)
        (p.with_suffix(".md")).write_text(md, encoding="utf-8")
        concept_count += 1

    for p in PEOPLE_DIR.glob("*.json"):
        try:
            person = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        md = render_person(person)
        (p.with_suffix(".md")).write_text(md, encoding="utf-8")
        person_count += 1

    write_vault_bundle()

    LOG.info("rendered %d concepts + %d people", concept_count, person_count)
    return {"concepts_rendered": concept_count, "people_rendered": person_count}


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    print(render_all())
