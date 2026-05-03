# telegram-knowledge-graph

> Self-hostable agent that turns any Telegram channel into a versioned, password-gated knowledge website. Read every message, classify it, build a graph of evolving consensus, publish it nightly.

This is the **public template**. Clone it, point it at your channel, deploy. A live reference instance (the original `ClawRyderz` channel) runs at `https://clawryderz.spirittree.dev` (gated) — the homepage, navigation, concept-page format, and graph view there are exactly what this template builds.

## How it works

```
Telegram channel
   ↓ (read-only Telegram bot — daily poll, oldest-first historical scrape via Telethon)
agent/data/atomic/*.json     raw immutable messages (gitignored)
   ↓ Claude Haiku via local CLI
agent/data/classify/*.json   tags · entities · is-question / supersession / anti-pattern
   ↓ one-shot Sonnet clustering
agent/data/canonical_topics.json    1000+ slugs → ~50 concepts
agent/data/person_aliases.json      mention strings → telegram user_ids
   ↓ chronological synthesis (oldest → newest, originals first)
vault/concepts/*.json + .md   versioned concept pages w/ lineage + anti-patterns
vault/people/*.json + .md     contributor profiles
   ↓ Sonnet "where things stand" callouts (hash + threshold cached)
vault/_meta/graph.json        force-directed graph
   ↓ Quartz 4 + custom Cloudflare Pages middleware (cookie session, HMAC-signed)
your-subdomain.example.com (gated)
```

The bot is **read-only** — no message handlers, no command responses, no posting in the channel. It DMs the operator with daily status pings only. See `BOT_STRATEGY.md` for the security/prompt-injection model.

## Setup (recommended path: open the cloned repo in Claude Code)

This repo ships with a `CLAUDE.md` at the root containing a complete interactive setup playbook. Just:

```bash
git clone https://github.com/sedim3nt/telegram-knowledge-graph.git my-channel-vault
cd my-channel-vault
claude
```

Claude Code reads `CLAUDE.md` automatically and walks you through every phase — channel personalization, BotFather, Telethon credentials, `.env`, historical backfill, classifier domain customization, canonicalization, synthesis, Quartz preview, GitHub repo, Cloudflare Pages, and the nightly cron. It asks questions when it needs input and runs the right command at each step.

## Setup (manual path, no Claude Code)

Same phases, you drive them yourself. Open `CLAUDE.md` and follow it top-to-bottom — the commands and decision points are all there. Brief outline:

1. **Create your venv + install deps**
   ```bash
   python3 -m venv agent/.venv && agent/.venv/bin/pip install -e agent --quiet
   ```
2. **Create your bot** in @BotFather (privacy mode disabled BEFORE adding to channel; no commands registered)
3. **Telegram MTProto credentials** from `https://my.telegram.org` (for historical backfill)
4. **Populate `.env`** from `.env.example` (`SITE_PASSWORD` should be high-entropy: `openssl rand -base64 24`)
5. **Discover chat_id** + first auth: `agent/.venv/bin/python agent/scripts/backfill_telethon.py --list-chats`
6. **Customize classifier domain prompts** in `agent/src/classify.py` and `agent/src/summarize.py` if your channel isn't dev/AI-adjacent
7. **Backfill history**: `agent/.venv/bin/python agent/scripts/backfill_telethon.py`
8. **Classify**: `env -u CLAUDECODE agent/.venv/bin/python -c "from src import classify; print(classify.classify_pending())"` (run from `agent/`)
9. **Canonicalize**: `agent/.venv/bin/python agent/scripts/canonicalize_topics.py` and `resolve_persons.py`
10. **Synthesize + summarize**: `agent/.venv/bin/python agent/scripts/synthesize_vault.py` then `refresh_summaries.py --force`
11. **Push to your private GitHub repo**, **set up Cloudflare Pages** (see `CLOUDFLARE_DEPLOY.md`)
12. **Install nightly cron**: `agent/.venv/bin/python agent/scripts/setup_wizard.py`

> **Important — keep your fork private.** The vault contains real channel members' messages (verbatim text in `agent/data/`, summaries in `vault/`). Make your fork's GitHub repo **private**. The Cloudflare Pages site is gated by username + password.

## Requirements

- macOS (for `launchd`; Linux can swap to cron — same orchestrator)
- Python 3.11+
- Node.js 22+ (for Quartz)
- A Telegram bot via [@BotFather](https://t.me/BotFather)
- A Telegram user account (for the one-time historical scrape via Telethon)
- A Claude Code subscription (Max recommended — Pro hits rate limits on the initial classify pass for 1000+ messages)
- A GitHub account
- A Cloudflare account (free tier is fine)
- Optional: a domain on Cloudflare DNS for a custom subdomain (or use the auto-generated `*.pages.dev`)

## Models + cost

The pipeline uses Claude through your **local `claude` CLI** (i.e. your Claude Code subscription) — there's no Anthropic API key requirement. With Claude Max ($200/mo):

- **Haiku** classifies ~10-30 atoms/night → effectively free
- **Sonnet** summarizes only changed concepts (threshold-gated: delta=3 atoms for concepts, 5 for people, plus 14-day staleness fallback) → typically 0-3 calls/night
- One-shot **canonicalization** on first deploy: 2 Sonnet calls
- One-shot **first-time summarization** for ~50 concepts + ~25 people: ~75 Sonnet calls in ~5 min

## Repo layout

```
telegram-knowledge-graph/
├── CLAUDE.md                       # the interactive setup playbook (read first)
├── README.md
├── KG_SPEC.md                      # data layer schemas (atomic, classify, concept, person, graph)
├── BOT_STRATEGY.md                 # bot security model
├── CLOUDFLARE_DEPLOY.md            # the one manual dashboard step
├── .env.example                    # env template (copy to .env, never commit .env)
├── .gitignore
│
├── agent/
│   ├── pyproject.toml
│   ├── src/
│   │   ├── config.py               # typed env loader
│   │   ├── poll.py                 # Bot getUpdates → atomic.json + SQLite
│   │   ├── atomic.py               # atomic schema, builds from Bot API + Telethon shapes
│   │   ├── classify.py             # Claude CLI tags every atom
│   │   ├── concept.py              # versioned concept synthesis (chronological)
│   │   ├── person.py               # contributor aggregation
│   │   ├── summarize.py            # Sonnet "where things stand" + threshold cache
│   │   ├── render.py               # JSON → Markdown for Quartz
│   │   ├── graph.py                # vault/_meta/graph.json
│   │   ├── notify.py               # owner-DM status pings
│   │   └── orchestrator.py         # nightly entry point: poll → classify → synthesize → render → graph → push
│   ├── data/                       # gitignored — atomic.json, classify.json, state.db, telethon.session
│   ├── logs/                       # gitignored
│   ├── scripts/
│   │   ├── setup_wizard.py         # venv + deps + render plist + load launchd
│   │   ├── init_fork.py            # safety wipe + fresh-state init (refuses on canonical sedim3nt/clawryderz remote)
│   │   ├── backfill_telethon.py    # one-shot historical scrape (use --list-chats first)
│   │   ├── canonicalize_topics.py  # cluster slugs into ~50 concepts (Sonnet)
│   │   ├── resolve_persons.py      # alias clustering (Sonnet)
│   │   ├── synthesize_vault.py     # concept + person + render + graph in one go
│   │   ├── refresh_summaries.py    # `--force` to rebuild all summaries (use after prompt changes)
│   │   └── fetch_topics.py         # forum-topic title fetch (only useful for forum-mode supergroups)
│   └── deploy/
│       └── launchd.plist.template  # rendered + installed by setup_wizard.py
│
├── vault/                          # the published knowledge base (committed in your fork)
│   ├── index.md                    # homepage (customize for your channel)
│   ├── concepts/<slug>.json + .md  # populated by your first synthesis run
│   ├── people/<handle>.json + .md  # populated by your first synthesis run
│   └── _meta/graph.json            # populated by your first synthesis run
│
└── site/                           # Quartz 4 (content/ symlinks to ../vault)
    ├── package.json
    ├── quartz.config.ts            # palette, fonts, build config
    ├── quartz.layout.ts            # left/right column components
    ├── quartz/styles/custom.scss   # cyberpunk dark overlay (blues + terminal green)
    └── functions/_middleware.ts    # Cloudflare Pages HMAC-cookie auth
```

## Operations

### Manual orchestrator run

```bash
env -u CLAUDECODE agent/.venv/bin/python -m src.orchestrator             # live
env -u CLAUDECODE agent/.venv/bin/python -m src.orchestrator --dry-run   # parse only, no writes/notify
```

`env -u CLAUDECODE` is required because the orchestrator shells out to the `claude` CLI for classify/summarize, and that CLI refuses to nest inside an active Claude Code session.

### Inspect the cron job

```bash
launchctl list | grep ai.tkg
tail -f agent/logs/launchd.out.log
tail -f "agent/logs/run-$(date -u +%Y-%m-%d).log"
```

### Force-rebuild all summaries (after prompt or model change)

```bash
agent/.venv/bin/python agent/scripts/refresh_summaries.py --force
```

### Reload the LaunchAgent after editing the plist template

```bash
agent/.venv/bin/python agent/scripts/setup_wizard.py    # re-renders + reloads
```

## Architecture decisions (TL;DR)

- **One bot per channel** (not a shared bot) — see `BOT_STRATEGY.md` for the rationale
- **Markdown vault as source of truth + Quartz for rendering** — Obsidian-compatible, diffable, future-proof
- **Atomic notes are immutable + gitignored**; everything downstream regenerates from them
- **Concept versioning is causal** — chronological synthesis (oldest first) so v1 always exists before something supersedes it to v2
- **Summaries are hash + threshold cached** — daily runs typically make 0-3 LLM calls instead of re-summarizing everything
- **Auth is HMAC-signed cookie session, not HTTP basic** — so the login screen is brand-themable and re-auth doesn't require closing the tab
- **Telegram links open in a single named window** (`target="topw"`) — clicking 10 citations reuses one tab instead of spawning 10

## License

MIT.
