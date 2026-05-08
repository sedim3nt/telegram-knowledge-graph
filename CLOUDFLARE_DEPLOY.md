# Cloudflare Pages Deployment

This is the one manual step that has to happen in a browser — Cloudflare's
GitHub OAuth flow can't be scripted with a normal API token. After the initial
setup (~5 minutes), every `git push` from the orchestrator triggers an
automatic redeploy.

## Why this can't be automated end-to-end

The Cloudflare API token in `.env` doesn't carry `Account.Cloudflare Pages:Edit`
permission, and even if it did, linking the project to GitHub for auto-deploys
requires the GitHub OAuth dance which is dashboard-only. Re-permissioning the
token is also dashboard-only, so the manual step is unavoidable. Once.

## One-time setup (5 min)

### 1. Create the Pages project

1. Visit https://dash.cloudflare.com/?to=/:account/pages
2. **Create a project → Connect to Git**
3. Authorize the Cloudflare GitHub App (if not already) and grant access to `sedim3nt/clawryderz`
4. Pick the `clawryderz` repo

### 2. Build configuration

| Field | Value |
|---|---|
| Project name | `clawryderz` |
| Production branch | `main` |
| Framework preset | `None` |
| **Root directory** | `site` |
| Build command | `npm install && npx quartz build` |
| Build output directory | `public` |

**Why root = `site`:** so Cloudflare auto-detects `site/functions/_middleware.ts` (path: `functions/_middleware.ts` relative to that root) for the basic-auth gate.

### 3. Environment variables

Under **Settings → Environment variables → Production**, add:

| Variable | Value |
|---|---|
| `SITE_USERNAME` | `ryder` |
| `SITE_PASSWORD` | _(value of `SITE_PASSWORD` from your local `.env`)_ |
| `NODE_VERSION` | `22` |

The middleware reads `SITE_USERNAME` (defaults to `ryder` if unset) and `SITE_PASSWORD` (required) from these vars.

### 4. Trigger first deploy

Click **Save and Deploy**. First build takes 1-3 minutes. You'll get a `*.pages.dev` URL — visit it and you should see the HTTP basic auth prompt.

Test login:
- Username: `ryder`
- Password: from `.env`

### 5. Custom domain — `clawryderz.spirittree.dev`

1. In the Pages project: **Custom domains → Set up a custom domain**
2. Enter `clawryderz.spirittree.dev`
3. Cloudflare auto-creates the CNAME (since `spirittree.dev` is in your CF account already — `CLOUDFLARE_ZONE_ID` in `.env` confirms this)
4. SSL provisions in ~30 seconds

Site is now live at `https://clawryderz.spirittree.dev`, gated.

## Ongoing operation

After the one-time setup, every commit the orchestrator makes to `vault/` (nightly at 01:00) triggers an automatic Cloudflare Pages rebuild. No further manual action.

You can manually trigger a redeploy any time via:
- Cloudflare dashboard → Pages → clawryderz → Deployments → Retry / Manual deploy
- Or push a no-op commit: `git commit --allow-empty -m "force redeploy" && git push`

## Verifying the build is healthy

After a deploy, the dashboard shows status. Common gotchas:

- **`npm install` slow / fails** — the build runs `npm install` in `site/`. Check the build log; usually a Node version mismatch (set `NODE_VERSION=22` per above).
- **404 on a known concept page** — the orchestrator may have skipped synthesis (check `agent/logs/run-YYYY-MM-DD.log`).
- **401 on every page** — `SITE_PASSWORD` env var is missing. Add it via dashboard.
- **Site loads but graph view is empty** — `vault/_meta/graph.json` not committed; force a re-run.

## Optional: tighten auth later

The current setup is single shared password. Two upgrade paths:

- **Cloudflare Access** (free for ≤50 users) — replaces basic auth with email-based magic links. Requires Cloudflare Zero Trust setup; no code changes (the middleware can stay as a fallback or be removed).
- **Per-user passwords** — change the middleware to read a comma-separated `SITE_USERS=ryder:passA,alice:passB` env var.
