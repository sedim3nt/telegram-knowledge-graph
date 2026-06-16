# Conflu3nce: an agent that turns a Telegram channel into a knowledge website

**What it is**

A self-hostable agent that reads *every* message in a Telegram channel and turns the
firehose into a navigable, versioned, password-gated knowledge site. Instead of
scrolling endless chat history, you get clean concept pages ("where things stand" on
each topic, with version lineage and anti-patterns), contributor profiles, and a
force-directed graph of how the channel's consensus evolves over time. It rebuilds
itself nightly.

**What you'll see**

- 🔗 **Live site:** https://clawryderz.spirittree.dev (gated; you'll be sent a username and password separately).
- A floating 🐯 **"Ask Conflu3nce"** chat panel on every page: ask a plain-English
  question and it answers *only* from the channel's vault.

**How it's made** (the nightly pipeline)

```
Telegram channel
  → Conflu3nce (read-only poller; Telethon for one-time historical backfill)
  → Claude Haiku classifies each message (tags, entities, is-question, supersession)
  → Sonnet clusters ~1000 topic slugs into ~50 canonical concepts
  → chronological synthesis → versioned concept + people pages (Markdown vault)
  → Sonnet "where things stand" summaries (threshold-cached: ~0-3 calls/night)
  → Quartz 4 static site + Cloudflare Pages (cookie-auth middleware)
  → Ask Conflu3nce Q&A (Opus, vault-grounded) via a local FastAPI server + cloudflared tunnel
```

Stack: a **Python** agent (orchestrated by macOS `launchd` cron), **Quartz 4** for the
site, **Cloudflare Pages** for hosting, and **Claude via the local `claude` CLI** (uses
a Claude Max subscription; no API key required). The Markdown vault is the source of
truth; everything regenerates from immutable atomic message notes.

**Deploy your own clone** (yes, it's built for this)

Clone the **public template** and self-host it for *any* channel or topic (mycology,
governance, indie game dev, whatever):

```bash
git clone https://github.com/sedim3nt/telegram-knowledge-graph.git my-channel-vault
cd my-channel-vault
claude
```

The repo ships a `CLAUDE.md` setup playbook that Claude Code reads automatically and
**walks you through every step**: BotFather, Telegram API credentials, wiping the
example data, historical backfill, building the vault, GitHub + Cloudflare Pages, and
the nightly cron. (A manual, no-Claude-Code path is in the same file.)

**You'll need:** a Telegram bot (via @BotFather), a Telegram user account (for the
one-time historical scrape), a GitHub account, a Cloudflare account (free tier is
fine), and a Claude Max subscription.

---

### Repos at a glance

| Repo | Visibility | Use |
|---|---|---|
| [`sedim3nt/telegram-knowledge-graph`](https://github.com/sedim3nt/telegram-knowledge-graph) | public | **Clone this** to deploy your own |
| `sedim3nt/clawryderz` | private | The live ClawRyderz deployment (real channel content) |

*See `README.md` for the full quickstart and `CLAUDE.md` for the interactive setup playbook.*
