# Claude Code instructions for telegram-knowledge-graph

> **Read this file first.** You are being asked to either help set up a fresh
> clone (most likely if `vault/concepts/` is empty and `.env` is missing), OR to
> assist with ongoing operations (if the clone is already configured for the
> user's own Telegram channel).

---

## What this repo is

A self-hostable agent that turns a Telegram dev/community channel into a
living, password-gated knowledge website. The pipeline:

```
Telegram channel → bot poller → Claude classifier → concept synthesis
                  → Sonnet summarizer → Quartz site → Cloudflare Pages
```

The user is deploying this for **their own channel** about **any topic**
(mycology, DAO governance, indie game dev, AI tooling, whatever). This repo
ships **empty** — no inherited data, no example vault. Everything will be
populated by the first ingestion + synthesis pass for the user's own channel.

> A live reference instance built from this same code (the `ClawRyderz` channel)
> runs at `https://clawryderz.spirittree.dev` — that's the visual + structural
> target for what the user's site will look like once data flows in.

---

## STEP 0 — Detect the situation

Before doing anything, run this assessment silently and decide which playbook
to execute:

| Check | If true → |
|---|---|
| `.env` doesn't exist, OR `.env` has no `TELEGRAM_BRIDG3BOT_TOKEN` value | **Fresh clone** — go to "Fresh clone setup" below |
| `agent/.venv/` missing | **Fresh clone** even if `.env` exists — needs deps first |
| `vault/concepts/` is empty AND `.env` is fully populated | Setup is partway done — find which Phase the user stopped at and continue |
| `vault/concepts/` has content AND launchd job is loaded | **Operational mode** — answer questions, debug, ship UI tweaks, etc. |

Quick recon commands (read-only, run silently before greeting the user):

```bash
test -f .env && echo "env: present" || echo "env: missing"
grep -E "^TELEGRAM_BRIDG3BOT_TOKEN=." .env 2>/dev/null | head -1 | cut -d= -f1
test -d agent/.venv && echo "venv: present" || echo "venv: missing"
ls vault/concepts/ 2>/dev/null | head -3
ls agent/data/atomic/ 2>/dev/null | wc -l
git remote get-url origin 2>/dev/null
launchctl list 2>/dev/null | grep ai.tkg
```

Greet the user with a one-line summary of the detected state and propose the
next action. Do NOT dump the full setup playbook into chat — execute it
step-by-step, asking for input only when needed.

---

## Fresh clone setup playbook

If this is a fresh clone, walk the user through these phases in order. Be
proactive: ask the next question or run the next command without making them
think about it. Pause to verify between phases.

### Phase A — Personalize identity

Ask for:
- **Their channel's name + topic** (1-2 sentences) — used in the homepage
  intro and to seed the classifier's domain context if it differs significantly
  from "AI agents/LLMs/coding tools" (see `agent/src/classify.py` SYSTEM_PROMPT)
- **Their bot's desired name** — they'll create it in @BotFather next; default
  pattern is `<channel-name-short>bot` (e.g. `MycoBot` for a mycology channel)
- **Their Telegram username** (without @) — for owner-DM status pings
- **Site username + password** — generate password with `openssl rand -base64 24`
  if they don't have a preference. Username can be anything readable.
- **A custom subdomain** (optional) — if they have a domain on Cloudflare DNS,
  e.g. `vault.theirdomain.com`. Otherwise the auto-generated `*.pages.dev` URL
  works fine.

### Phase B — Create the venv + install deps

```bash
python3 -m venv agent/.venv
agent/.venv/bin/pip install -e agent --quiet
```

If they want historical backfill (almost always yes), also:
```bash
agent/.venv/bin/pip install -e 'agent[backfill]' --quiet
```

### Phase C — BotFather

Walk the user through creating their bot:
1. Open Telegram, search **@BotFather**
2. `/newbot` → reply with a display name → reply with a username (must end
   in `bot`)
3. Save the token BotFather hands back (looks like `1234567890:ABCdef…`)
4. **`/setprivacy → @theirbot → Disable`** — critical, must happen
   BEFORE adding the bot to the channel (Telegram only re-applies privacy
   mode at join time)
5. Optional but recommended: `/setdescription`, `/setabouttext`, `/setuserpic`
6. **DO NOT register any commands with `/setcommands`** — the bot is read-only
   and shouldn't accept commands. Run `/empty` if anything's there.

Verify the token works (replace `<TOKEN>`):
```bash
curl -sS "https://api.telegram.org/bot<TOKEN>/getMe"
```
Look for `"can_read_all_group_messages":true` — that confirms privacy mode is
disabled.

### Phase D — Telethon API credentials (for historical backfill)

Required ONLY if the user wants to ingest existing channel history (almost
always yes — otherwise the vault is empty until the bot has been polling for a
while).

1. They visit https://my.telegram.org → log in with their phone (their user
   account, NOT the bot's)
2. **API Development Tools** → **Create application** (any title, "Other"
   platform)
3. Copy `api_id` (numeric) and `api_hash` (32-char hex)

### Phase E — Write `.env`

```bash
cp .env.example .env
```

Then edit it with what you've gathered. The fields:

| Variable | Source |
|---|---|
| `TELEGRAM_BRIDG3BOT_TOKEN` | from BotFather (Phase C). Var name historical, not branded. |
| `TELEGRAM_BRIDG3BOT_USERNAME` | their bot's username (without @) |
| `TELEGRAM_API_ID` | from my.telegram.org (Phase D) |
| `TELEGRAM_API_HASH` | same |
| `TELEGRAM_OWNER_CHAT_ID` | TBD — captured in Phase F |
| `TELEGRAM_BRIDG3BOT_CHAT_ID` | TBD — captured in Phase F |
| `SITE_USERNAME` | what they chose in Phase A |
| `SITE_PASSWORD` | the generated/chosen password |
| `GITHUB_USER` / `GITHUB_EMAIL` | for git commit identity |
| `CLOUDFLARE_API_TOKEN` / `_ACCOUNT_ID` / `_ZONE_ID` | optional; only needed if scripting CF later |

Use the Edit tool to populate it. Don't echo `.env` contents to chat after
populating (secrets).

### Phase F — Authenticate Telethon, list chats, capture chat_id

```bash
agent/.venv/bin/python agent/scripts/backfill_telethon.py --list-chats
```

First run is interactive: prompts for the user's phone number → SMS code →
2FA password if set. Session is cached at `agent/data/telethon.session` so
later runs don't re-prompt.

It'll print every chat the user account is in. Find the channel they want to
scrape, copy its `chat_id`, write to `.env` as `TELEGRAM_BRIDG3BOT_CHAT_ID`.

Also: have the user send `/start` to their new bot once (in a DM). This is what
enables the bot to DM them with status pings later. Capture their own user_id
(it'll show up in `--list-chats` output as a `user` row near the top) and
write to `.env` as `TELEGRAM_OWNER_CHAT_ID`.

### Phase G — Add the bot to the channel

The user adds `@theirbot` to the channel as a regular member (no admin
needed). The channel owner needs to ensure **"Chat History For New Members"
is set to Visible** (Group settings → that toggle) — Telethon needs this for
the historical scrape to work.

### Phase H — Customize classifier domain (if needed)

If the channel is about something far from "AI agents / LLMs / coding tools"
(e.g. mycology, governance, art), open `agent/src/classify.py` and adjust:

- `SYSTEM_PROMPT` — change the domain phrasing in the first paragraph and the
  example entity kinds (under "Field guidance")
- `CONCEPT_SYSTEM_PROMPT` and `PERSON_SYSTEM_PROMPT` in `agent/src/summarize.py`
  — same domain adjustment

If the channel is technical/dev-adjacent, the prompts probably work as-is.
Skip this phase.

### Phase I — Historical backfill

Test on a small slice first:
```bash
env -u CLAUDECODE agent/.venv/bin/python agent/scripts/backfill_telethon.py --limit 50
```

Eyeball a few `agent/data/atomic/clr-*.json` files to confirm fields look
right (text, author, date, deep_link). Then full pull:
```bash
env -u CLAUDECODE agent/.venv/bin/python agent/scripts/backfill_telethon.py
```

For a 1-3 month-old channel this takes 1-5 minutes.

### Phase J — Classify the corpus

```bash
env -u CLAUDECODE agent/.venv/bin/python -c "from src import classify; print(classify.classify_pending())"
```
(Run from `agent/`. Workers default to 2 — see `CLAWRYDERZ_CLASSIFY_WORKERS`
in `.env` to tune. Var name is historical; it controls classify-pass workers.)

For 1000+ atoms this takes 15-45 minutes via Claude Max subscription. Threshold
errors retry inline; persistent rate-limit hits leave atoms for the next run.

### Phase K — Canonicalize topics + resolve persons (one-shot, Sonnet)

Once classify is done:
```bash
env -u CLAUDECODE agent/.venv/bin/python agent/scripts/canonicalize_topics.py
env -u CLAUDECODE agent/.venv/bin/python agent/scripts/resolve_persons.py
```

These are 2 single Sonnet calls. Each takes ~1-2 min and clusters 1000+
classifier-generated topic slugs into ~50 canonical concepts, and ~100+ person
mentions into actual humans. Show the user the summary output so they can
sanity-check before continuing.

### Phase L — Synthesize + render + summarize

```bash
env -u CLAUDECODE agent/.venv/bin/python agent/scripts/synthesize_vault.py
env -u CLAUDECODE agent/.venv/bin/python agent/scripts/refresh_summaries.py --force
```

This builds `vault/concepts/`, `vault/people/`, `vault/_meta/graph.json`, and
runs the first-time Sonnet summarization for every concept + person.

### Phase M — Local Quartz preview

```bash
cd site && npm install && npx quartz build && npx quartz serve
```

Open `http://localhost:8080`. Verify it looks right.

### Phase N — GitHub repo (PRIVATE)

The user MUST create a **private** GitHub repo for their channel. Public means
anyone can see the channel content (vault contains real members' messages and
profile pages). Then:
```bash
git remote set-url origin https://github.com/<their-user>/<their-repo>.git
git push -u origin main
```

### Phase O — Cloudflare Pages

Walk through `CLOUDFLARE_DEPLOY.md`. The dashboard step is unavoidable — point
them at it. Once configured, every nightly orchestrator git push triggers a
Pages rebuild.

### Phase P — Install nightly cron

```bash
agent/.venv/bin/python agent/scripts/setup_wizard.py
```

The wizard renders `agent/deploy/launchd.plist.template` with the actual repo
path + a per-fork `Label` (`ai.tkg.<dirname>`), copies it to
`~/Library/LaunchAgents/`, and loads it with `launchctl`. Cron fires daily at
04:00 local.

### Phase Q — First end-to-end orchestrator run

```bash
env -u CLAUDECODE agent/.venv/bin/python -m src.orchestrator
```

Should: poll, classify any new messages, run the threshold-gated summarize
(skips most), render markdown, compute graph, commit + push. Cloudflare Pages
auto-deploys within a minute. Site is live.

### Phase R — Customize the homepage

Edit `vault/index.md` to describe THEIR channel and link to their concepts
(use the Phase L output to pick the most-discussed). Commit + push.

### Phase S — Optional: drop a brand image

Drop a square image (any aspect ratio; will be cropped) at
`site/quartz/static/brand.jpg`. Then in `site/quartz/styles/custom.scss`
**uncomment** the `.left .page-title::before` rule near the top of the file.
Rebuild. The image appears above the page title in the left rail.

---

## Operational mode (already-configured fork)

If the fork is set up and ingestion has been running, the user is probably
asking about:

- **UI tweaks** — `site/quartz/styles/custom.scss` is the place. Build with
  `cd site && npx quartz build`. Push.
- **Concept page format** — `agent/src/render.py` controls Markdown output.
  Re-run `agent.scripts.synthesize_vault` after edits to regenerate.
- **Adding a new concept manually** — edit `vault/concepts/<slug>.md` directly
  (it'll be overwritten on next render unless you also add a corresponding
  entry to `agent/data/canonical_topics.json`'s `slug_to_concept` map).
- **Re-running the full pipeline** — see Phase L above.
- **Force-refreshing all summaries** (after prompt change or model upgrade) —
  `agent/scripts/refresh_summaries.py --force`
- **Changing cron time** — edit `agent/deploy/launchd.plist.template` and
  re-run `setup_wizard.py`.
- **Site is broken** — check the most recent Cloudflare Pages deploy log
  (manual via dashboard) and the local `agent/logs/launchd.out.log`.

---

## What to NOT do

- Don't commit `.env` (it's gitignored — verify before any `git add .`)
- Don't make the user's GitHub fork public unless they explicitly say so
  (the vault contains real channel members' messages)
- Don't push without checking `git status` first
- Don't run `init_fork.py` on the canonical `sedim3nt/clawryderz` instance —
  the script refuses there for safety. It's safe on any other clone.
- Don't bypass the auth middleware (`site/functions/_middleware.ts`) — the
  vault content is private even if the repo is private
- Don't downgrade summarization to Haiku without telling the user; the
  consensus-summary quality difference is significant

---

## Reference docs

- `README.md` — short overview + quickstart
- `KG_SPEC.md` — atomic / classify / concept / person / graph schemas
- `BOT_STRATEGY.md` — why one bot per channel, prompt-injection safety model
- `CLOUDFLARE_DEPLOY.md` — the one manual dashboard step
- `agent/src/*.py` — pipeline modules; each has a docstring
