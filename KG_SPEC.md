# Knowledge-Graph Spec

> Data layer is canonical. Rendering is downstream.
> Atomic is the source of truth and the only file produced by ingestion.
> Everything else is regenerable from atomics + synthesis logic.

## Files & dependency tree

```
atomic.json (immutable, ingestion-only)
  ↓
classify.json (regenerable, cheap LLM pass — Claude Haiku 4.5)
  ↓
concept.json (regenerable, expensive LLM pass — Claude Sonnet 4.6)
person.json  (regenerable, aggregation)
  ↓
graph.json          (regenerable, computed)
open-questions.json (regenerable, filtered)
category.json       (regenerable, derived from concept tags)
```

Atomic files **never edited** after first ingest. If we change the synthesis logic we wipe `concepts/`, `people/`, `_meta/` and re-derive. No data loss, no re-ingest.

## Privacy boundary

Atoms contain verbatim channel messages. They live in `agent/data/atomic/` (gitignored, local-only). The committed `vault/` contains only summaries + citations as deep-links to t.me. The site is built from `vault/` — atoms never reach Cloudflare Pages. This means a leaked repo doesn't leak the channel archive.

## Two ingestion sources

| Source | Method | What it captures |
|---|---|---|
| `bridg3bot-poll` | Telegram Bot API `getUpdates` | Messages from the moment the bot joined the channel forward |
| `telethon-backfill` | MTProto user session (your Telegram user account) | Historical messages back to where the user can scroll |

Both produce the **same** `atomic.json` schema. The `source` field records which path produced the file. Concept synthesis treats them identically.

---

## Schema 1: `atomic.json` — LOCKED for v1

One file per Telegram message. Path: `agent/data/atomic/<id>.json`.

```json
{
  "$schema": "atomic.v1",
  "id": "clr-12345",
  "ingested_at": "2026-04-30T13:49:22Z",
  "source": "bridg3bot-poll",

  "chat_id": -1001234567890,
  "chat_title": "Your Channel Name",
  "message_id": 12345,
  "thread_root_id": 12340,
  "reply_to_message_id": 12343,
  "topic_id": null,
  "deep_link": "https://t.me/c/1234567890/12345",

  "date_iso": "2026-04-19T15:32:11Z",
  "edit_date_iso": null,

  "author_id": 12345678,
  "author_username": "alice",
  "author_display_name": "Alice Chen",
  "author_is_bot": false,

  "text": "verbatim message body",
  "media_kind": null,
  "media_caption": null,
  "forward": null,

  "raw_source": { /* verbatim source-specific payload for forensics */ }
}
```

### Field rules

- `id`: deterministic from `message_id`. Format: `clr-<message_id>`. Same message ingested by the bot poll or Telethon produces the same id (idempotent). Prefix is historical and stable across all atoms.
- `source`: `"bridg3bot-poll"` | `"telethon-backfill"`. Used to track which path produced this atom. Useful for debugging and for re-running source-specific extraction. The `bridg3bot-poll` literal is a stable schema value, not a brand reference — change it and you fork the data format.
- `thread_root_id` / `reply_to_message_id`: nullable. Threading is reconstructed at concept-synthesis time, not at ingest.
- `topic_id`: Telegram forum topic id, if the chat is a forum. Null otherwise.
- `media_kind`: `null` | `"photo"` | `"video"` | `"document"` | `"voice"` | `"sticker"` | `"audio"`. Caption (if any) goes in `media_caption`; the file itself is **not** downloaded in v1.
- `forward`: `null` or `{"from": "...", "from_id": int|null, "date_iso": "..."}`.
- `text`: empty string for media-only messages without captions.
- `raw_source`: full source payload (Bot API update dict or Telethon message dict). Bloat is fine; it's local-only and gitignored.

### Idempotency

Re-ingesting the same message produces an identical file. Writers use `if path.exists(): skip` semantics by default, with an `--overwrite` flag for explicit reprocessing.

---

## Schema 2: `classify.json` — LOCKED for v1, fields may be ADDED in v2

One file per atomic. Path: `agent/data/classify/<id>.json`. Regenerable — wipe and re-run with a newer model anytime.

```json
{
  "$schema": "classify.v1",
  "atomic_id": "clr-12345",
  "classified_at": "2026-04-30T13:50:00Z",
  "model": "claude-haiku-4-5",

  "kind": "claim",
  "topics": ["memory-config"],
  "entities": [
    {"text": "SOUL.md", "kind": "file"},
    {"text": "@alice", "kind": "person"},
    {"text": "Claude Sonnet 4.6", "kind": "model"},
    {"text": "openclaw", "kind": "tool"},
    {"text": "MuninnDB", "kind": "system"}
  ],
  "links_categorized": [
    {"url": "https://github.com/x/y", "domain": "github.com", "kind": "repo"}
  ],
  "code_blocks": [
    {"lang": "python", "lines": 15}
  ],
  "language": "en",

  "is_question": false,
  "is_supersession": true,
  "supersedes_topics": ["memory-config"],
  "is_anti_pattern": false,

  "confidence": 0.82
}
```

### Field rules

- `kind`: `"claim"` | `"question"` | `"answer"` | `"link-share"` | `"code-snippet"` | `"meta"` | `"greeting"` | `"off-topic"`. Single best label.
- `topics`: candidate concept slugs in kebab-case. Synthesizer treats these as suggestions, not commitments.
- `entities`: `kind` is one of `"file"` | `"person"` | `"model"` | `"tool"` | `"system"` | `"concept"` | `"library"` | `"other"`. The `kind` taxonomy is what powers the **funnel** — entities of the same kind cluster together (all `model` entities form the LLMs category, all `tool` entities form the Tools category).
- `is_supersession` + `supersedes_topics`: this is the signal that a message replaces a prior best-practice version. Used directly by concept synthesis to bump a concept from v1 to v2.
- `is_anti_pattern`: this message describes something that *failed*. Preserved as negative knowledge in concept lineage.
- `confidence`: model's self-reported confidence 0-1.

### Schema versioning

If we discover during corpus inspection that we need a new field (e.g. `is_breaking_change`), we add it as `classify.v2` and re-run the classifier. Old `classify.v1` files remain valid; readers tolerate both.

---

## Schemas 3-7 — STUBBED, finalize after corpus inspection

These are deliberately not locked. We'll design them after running Telethon backfill and seeing the actual data. Below is the rough shape so we know what we're aiming for.

### `concept.json` (TBD-after-data)

The navigable layer. One file per evolving topic. Two known kinds:

- `kind: "best-practice"` — evolves through versions over time (memory-config v1 → v2 → v3)
- `kind: "comparison"` — multiple options with shifting consensus (GPT vs Claude vs Kimi; openclaw vs claude-code vs codex)

Maybe a third kind we'll discover:
- `kind: "reference"` — stable definition (e.g. "what is SOUL.md")

The "feature current, preserve history" requirement is solved by:
- `current_version` field at top
- `versions[]` array carrying the lineage
- Each version records `establishing_messages`, `consensus_messages`, `superseded_by`, `deprecation_reason`

### `person.json` (TBD-after-data)

One per channel member. Aggregated from atomics by `author_id`. Records contributions, topics they originated, topics they superseded, total message count, recency.

### `graph.json` (TBD-after-data)

Two node types: `concept` (large) and `person` (small). Atomics are NOT nodes — they'd swamp the graph. They're available on drill-down.

Edges:
- `concept → concept`: `references` | `supersedes` | `parent-of` | `compared-with`
- `person → concept`: `originated` | `consensus-builder` | `superseded` | `objected`

Sized by citation density and recency.

### `open-questions.json` (TBD-after-data)

Auto-curated list of unresolved tensions. Created when classifier marks `is_question: true` and synthesizer fails to find a converged answer in the channel. Closes when a later message resolves it.

### `category.json` (TBD-after-data)

Top-level taxonomy. The "funnel" the user named:

- `LLMs` (members: gpt-5, claude-4-6, kimi-k2, ...)
- `Coding-agents` (openclaw, claude-code, codex, ...)
- `Memory-systems` (...)
- `Frameworks` (...)
- `Tools` (...)
- `Concepts & techniques` (...)

Members are concept_ids. Concepts can belong to multiple categories. Categories drive the homepage lattice and the comparison-concept sidebar.

---

## Build order (the chronological-first rule)

Telethon backfill processes messages **oldest-first** (`reverse=True` in `iter_messages`). This is load-bearing for two reasons:

1. **Origin gets v1.** When concept synthesis sees a message about `memory-config` for the first time, it spawns the concept with that message as `versions[0].establishing_messages`. Originals first.
2. **Supersession is causal.** A later message that says "use 1536-dim embeddings instead" can only meaningfully reference v1 if v1 already exists. Reverse-order processing breaks this.

The bot's forward poll naturally produces oldest-first too (Telegram returns updates ordered by `update_id` ascending).

## Synthesis pipeline (run after backfill or nightly)

```
for each atomic in chronological order:
    classify_pending(atomic) → write classify.json
    synthesize(atomic, classify):
        if classify.is_supersession and supersedes_topics:
            for each topic in supersedes_topics:
                bump concept[topic] to next version
        elif classify.topics:
            for each topic in classify.topics:
                if concept[topic] exists: append to current version's consensus_messages
                else: spawn concept[topic] with this as v1
        if classify.is_question and not resolved:
            create open_question entry
        update person[author].contributions
```

This is the v1 algorithm. We'll refine after seeing the data.

## Re-rendering

`vault/concepts/<slug>.md`, `vault/people/<handle>.md`, etc. are markdown projections of the JSON. Generated by `agent/src/render.py` (TBD). Anyone reading the markdown sees a clean page; anyone reading the JSON sees the full data.

Cloudflare Pages builds the site from `vault/`. It never reads `agent/data/`.

---

## Open design questions (track but don't block)

- Concept slugs: auto-generated kebab-case from canonical title? Human-curated? Hybrid (auto-generate, allow rename)?
- Multiple-language messages: detect at classify time, route to translation pre-summary? Skip for v1.
- Code-block extraction: store full code in classify.json, or just metadata + line count? Storage-cheap to keep full; deferred until corpus inspection.
- Edited messages: rewrite atomic.json or version it? V1: rewrite on next ingest, since edits are rare.
- Deleted messages: Telegram doesn't notify on delete. We never know. Atomic stays. Document this in operator notes.
