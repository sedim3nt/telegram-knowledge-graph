# Cool apps that should be useful

SpiritTree apps that each solve a real problem. Most are live and clickable, and every
one now has an AI chat you can use on its topic, powered by the local Claude CLI (Claude
Max, no API keys).

---

## SafeSpace Lite

**What it is:** A tenant/landlord safety database. Look up an address or a landlord
before you sign, see the history and any red flags, and learn your rights as a renter.

**AI features:** An AI "Know Your Rights" assistant that answers tenant questions in
plain language (LLM-backed).

**Status:** Live, ~85%. Accounts, address lookup, and rights guidance work; data
coverage grows over time.

**Live:** https://safespace.spirittree.dev

---

## Clean Slate

**What it is:** An addiction-recovery dashboard. Sobriety counter, daily check-ins
(mood, cravings, gratitude), milestones, crisis resources, and a private journal.
Anonymous by default.

**AI features:** An AI recovery companion in the journal, plus a floating recovery-support
chat (local Claude CLI) with built-in crisis guardrails (988 / SAMHSA). Supportive, not a
substitute for professional help.

**Status:** Live, ~80%. Tracking, journal, and the AI companion all work; paid tiers
and community features are post-MVP.

**Live:** https://cleanslate.spirittree.dev

---

## MycoMaps ("Weedmaps for shrooms")

**What it is:** A functional-mushroom intelligence map. Find retailers and products on
a map, browse a species directory and foraging guide, and check legal status by
location.

**AI features:** An AI Mycologist chat that answers species, foraging, and usage
questions, grounded in the app's own data and wrapped in safety guardrails.

**Status:** Live, ~90%. Map, 55+ retailer profiles, species/legal/guide pages, and the
AI chat all work.

**Live:** https://mycomaps.spirittree.dev

---

## GeoLayers

**What it is:** A "what is the geology under me" lookup. Drop a point and get the
surface rock units, a plain-language ground read, and curated regional stack stories.

**AI features:** An "Ask the Geologist" chat (local Claude CLI) that answers questions
about your local geology, grounded in the looked-up Macrostrat data. The core ground-read
narrative stays deterministic (straight from Macrostrat), so the facts are reproducible
while the chat handles free-form questions.

**Status:** Live, ~85%. Live geology lookup, narrative, and regional stories work.

**Live:** https://geolayers.spirittree.dev

---

## Relational Layers

**What it is:** A relationship-decision tool. Pick the context, the layer, and the
direction, set a few readiness sliders, and get a clear, structured recommendation with
guardrails and out-of-scope flags.

**AI features:** A "Talk it through" chat (local Claude CLI) that helps you reflect on the
decision and interpret your result. The recommendation engine itself stays deterministic
and transparent (same inputs always give the same answer); the chat supplements it, never
overrides it.

**Status:** Live, ~85%.

**Live:** https://relational.spirittree.dev

---

## Health Records Vault

**What it is:** A local-first Personal Health Record + Pet Health Vault. Import or
folder-scan medical records into a private local vault, organize them on a case-file
timeline, build shareable record packets, and track requests to providers. Your data
stays on your own machine.

**AI features:** Four, all powered by the local Claude CLI: extract structured fields from
a scanned/pasted record, summarize a history in plain language, auto-draft a
records-request letter (citing your right of access), and a chat that answers questions
about your records and how to use the app (not medical advice).

**Status:** Desktop (Electron) alpha, plus a live browser web demo with fictional data
(Riley Demo and Mochi the cat).

**Live:** https://health.spirittree.dev (web demo, dummy data only)

**Repo:** https://github.com/sedim3nt/health-records
