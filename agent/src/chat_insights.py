"""Read Ask Bridg3 question logs (agent/logs/ask-YYYY-MM-DD.jsonl) and turn
them into a per-concept question signal that other phases can use:

  vault/_meta/chat-insights.json   — machine-readable; summarize.py reads it
  vault/_meta/chat-insights.md     — human-readable; committed via the nightly
                                      git push so the owner sees the trend

We deliberately read the *questions only* (never the answers). The point of
this loop is to learn what visitors actually want to know — not to feed
Bridg3's own answers back into the next consensus summary, which would make
the model grade its own homework.

Activate the summary injection by calling
`chat_insights.signal_for_concept(concept_id)` inside summarize.py — see
that module for the exact integration.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import LOGS_DIR, VAULT_DIR

LOG = logging.getLogger("chat_insights")

META_DIR = VAULT_DIR / "_meta"
INSIGHTS_JSON = META_DIR / "chat-insights.json"
INSIGHTS_MD = META_DIR / "chat-insights.md"
CONCEPTS_DIR = VAULT_DIR / "concepts"

WINDOW_DAYS = 7
MAX_SAMPLE_QUESTIONS = 5
MAX_TOP_PAGES = 10


def _load_recent_jsonl(window_days: int = WINDOW_DAYS) -> list[dict]:
    """Read all ask-*.jsonl entries from the last N days. Tolerant of missing files."""
    if not LOGS_DIR.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows: list[dict] = []
    for p in sorted(LOGS_DIR.glob("ask-*.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("ts")
                if not ts:
                    continue
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when < cutoff:
                    continue
                rows.append(row)
        except OSError:
            continue
    return rows


def _load_concept_titles() -> dict[str, str]:
    """Map concept_id → title for slug-to-name lookup."""
    out: dict[str, str] = {}
    if not CONCEPTS_DIR.exists():
        return out
    for p in CONCEPTS_DIR.glob("*.json"):
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            out[c["concept_id"]] = c.get("title", c["concept_id"])
        except Exception:  # noqa: BLE001
            continue
    return out


def _tokenize(text: str) -> set[str]:
    """Lowercase word set, used for fuzzy concept matching."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _slug_tokens(slug: str) -> set[str]:
    return set(slug.lower().replace("-", " ").split())


def _match_concept(question: str, concepts: dict[str, str]) -> set[str]:
    """Return concept_ids whose title or slug appears (loosely) in the question.

    We match a concept if EITHER:
      - its full slug (with hyphens) appears in the lowercased question, OR
      - its multi-word title appears as a substring (lowercased), OR
      - all distinctive tokens of the slug appear in the question's word set
    """
    q_lower = question.lower()
    q_tokens = _tokenize(question)
    matched: set[str] = set()
    for cid, title in concepts.items():
        slug_lc = cid.lower()
        title_lc = title.lower()
        if slug_lc in q_lower:
            matched.add(cid)
            continue
        if len(title_lc) > 4 and title_lc in q_lower:
            matched.add(cid)
            continue
        slug_words = _slug_tokens(cid) - _COMMON_WORDS
        if slug_words and slug_words.issubset(q_tokens):
            matched.add(cid)
    return matched


_COMMON_WORDS = {
    "and", "or", "the", "a", "an", "of", "for", "to", "in", "on", "with",
    "is", "are", "was", "were", "be", "by", "as",
}


def compute(window_days: int = WINDOW_DAYS) -> dict:
    """Compute insights JSON and write both JSON + MD reports.

    Returns a dict suitable for inclusion in the orchestrator stats and the
    owner's nightly notification footer.
    """
    META_DIR.mkdir(parents=True, exist_ok=True)
    rows = _load_recent_jsonl(window_days)

    if not rows:
        # Fresh forks won't have ask logs yet — write an empty file so the
        # MD page renders, but bail early.
        empty = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "window_days": window_days,
            "total_questions": 0,
            "error_rate": 0.0,
            "per_concept": {},
            "top_pages": [],
            "top_questions": [],
        }
        INSIGHTS_JSON.write_text(json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        INSIGHTS_MD.write_text(
            f"# Chat insights\n\n_No Ask Bridg3 questions in the last {window_days} days._\n",
            encoding="utf-8",
        )
        LOG.info("chat_insights: 0 rows in last %dd", window_days)
        return empty

    concepts = _load_concept_titles()
    per_concept_counts: Counter[str] = Counter()
    per_concept_samples: dict[str, list[str]] = defaultdict(list)
    page_counts: Counter[str] = Counter()
    error_count = 0

    for r in rows:
        q = (r.get("question") or "").strip()
        if not q:
            continue
        if r.get("error"):
            error_count += 1
        if r.get("current_page"):
            page_counts[r["current_page"]] += 1
        for cid in _match_concept(q, concepts):
            per_concept_counts[cid] += 1
            if len(per_concept_samples[cid]) < MAX_SAMPLE_QUESTIONS:
                per_concept_samples[cid].append(q[:280])

    total = len(rows)
    err_rate = round(error_count / total, 3) if total else 0.0

    per_concept = {
        cid: {
            "count": cnt,
            "title": concepts.get(cid, cid),
            "sample_questions": per_concept_samples[cid],
        }
        for cid, cnt in per_concept_counts.most_common()
    }

    top_pages = [
        {"page": page, "count": cnt}
        for page, cnt in page_counts.most_common(MAX_TOP_PAGES)
    ]

    # Surface a few representative questions overall (newest first, deduped).
    seen: set[str] = set()
    top_questions: list[dict] = []
    for r in sorted(rows, key=lambda x: x.get("ts", ""), reverse=True):
        q = (r.get("question") or "").strip()
        if not q or q in seen:
            continue
        seen.add(q)
        top_questions.append({
            "ts": r.get("ts"),
            "current_page": r.get("current_page"),
            "question": q[:300],
        })
        if len(top_questions) >= 15:
            break

    insights = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "total_questions": total,
        "error_count": error_count,
        "error_rate": err_rate,
        "per_concept": per_concept,
        "top_pages": top_pages,
        "top_questions": top_questions,
    }

    INSIGHTS_JSON.write_text(json.dumps(insights, ensure_ascii=False, indent=2), encoding="utf-8")
    INSIGHTS_MD.write_text(_render_md(insights), encoding="utf-8")
    LOG.info(
        "chat_insights: %d questions / %d concepts touched / %.1f%% error rate",
        total, len(per_concept), err_rate * 100,
    )
    return insights


def _render_md(insights: dict) -> str:
    parts: list[str] = ["# Chat insights", ""]
    parts.append(
        f"_Last {insights['window_days']} days · {insights['total_questions']} "
        f"question{'s' if insights['total_questions'] != 1 else ''} · "
        f"{insights['error_count']} errors_"
    )
    parts.append("")

    per_concept = insights.get("per_concept", {}) or {}
    if per_concept:
        parts.append("## Concepts visitors asked about")
        parts.append("")
        for cid, data in list(per_concept.items())[:20]:
            parts.append(f"### [[concepts/{cid}|{data['title']}]] — {data['count']}")
            for q in data["sample_questions"][:3]:
                parts.append(f"- _{q}_")
            parts.append("")

    top_questions = insights.get("top_questions", []) or []
    if top_questions:
        parts.append("## Recent questions")
        parts.append("")
        for tq in top_questions:
            page_hint = f" _(on {tq['current_page']})_" if tq.get("current_page") else ""
            parts.append(f"- {tq['question']}{page_hint}")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Helpers for summarize.py — surface per-concept signal without invalidating
# the summary cache. summarize.py reads this lazily during prompt construction.
# ---------------------------------------------------------------------------

_INSIGHTS_CACHE: dict[str, dict] | None = None


def signal_for_concept(concept_id: str) -> dict | None:
    """Return {count, title, sample_questions} for `concept_id` or None.

    Lazy-loads chat-insights.json on first call. Returns None if the file
    doesn't exist or the concept isn't represented (fresh forks, quiet weeks).
    """
    global _INSIGHTS_CACHE
    if _INSIGHTS_CACHE is None:
        if not INSIGHTS_JSON.exists():
            _INSIGHTS_CACHE = {}
            return None
        try:
            data = json.loads(INSIGHTS_JSON.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _INSIGHTS_CACHE = {}
            return None
        _INSIGHTS_CACHE = data.get("per_concept") or {}
    return _INSIGHTS_CACHE.get(concept_id)


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    out = compute()
    print(json.dumps({k: v for k, v in out.items() if k != "per_concept"}, indent=2, default=str))
