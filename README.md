# Jason — the Howards Email Agent

Jason is an AI email agent for [Howards](https://howards.ai). Real-estate agents email the
Howards inbox; Jason converses with them, collects the photos and details needed for a
marketing video, takes payment via Stripe, hands the paid job to the AI render pipeline,
and delivers the finished video. He remembers agents between orders.

**Architecture in one line:** the model decides; code executes and enforces. Jason is a
tool-calling LLM (via OpenRouter) in an agent loop — free conversation is the default —
with exactly two hard fences in code: the **payment gate** and **operator authority**.

## Layout

```
jason/
  main.py          entrypoint: Gmail poller (30s) + webhook server in one process
  poller.py        inbox polling, attachment intake, quiet-mode gating, ghost sweep
  gmail_client.py  Gmail OAuth, threading-correct sends, labels, attachments
  agent/
    prompt.py      Jason's runtime system prompt (soft behaviour: voice, intake, memory)
    tools.py       tool schemas + dispatcher (fences enforced here)
    loop.py        per-email agent loop: rehydrate state -> model -> tools -> persist
  identity.py      sender -> agent, domain -> agency; operator-auth fence
  storage.py       Store interface + local-file implementation (Supabase-ready)
  jobs.py          per-thread job state + photo quality gate (>=12 usable)
  payments.py      Stripe payment links; mark_paid is the ONLY write path to `paid`
  render.py        payment-fenced handoff to the render pipeline (+ stub mode)
  amenities.py     Google Places lookup + Google image search for amenities
  handoff.py       "let me talk to a human": escalation, quiet mode, @jason wake
  webhook.py       POST /webhooks/stripe, POST /webhooks/render-complete
  devchat.py       offline CLI: talk to Jason without Gmail
memory/            runtime data (gitignored); mirrors the future Supabase schema
```

## The two hard fences (code, never prompt)

1. **Payment gate** — a job's `paid` flag is set only by `payments.mark_paid()`, called
   exclusively from the signature-verified Stripe webhook. The model's `set_job_state`
   tool blocklists payment fields, and `trigger_render` refuses unpaid jobs. No email —
   however persuasive — can start a render.
2. **Operator authority** — privileged `@jason` commands work only for the authenticated
   operator (`jasonfromhowards1@gmail.com`). Because that address is the bot's own inbox, an
   authentic operator message must carry Gmail's `SENT` label (authored by the account)
   and not be one of Jason's own labelled outbound messages. A spoofed From header is
   just text and fails the check.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill it in
```

Configuration is split the same way as Main-Pipeline: secrets (API keys) go in
`.env`; everything else (Gmail paths, poll interval, model, prices, webhook
host/port, behaviour knobs) lives in `config/email-bot.json`.

### 1. OpenRouter
Set `OPENROUTER_API_KEY`. Default model is `qwen/qwen3.6-flash`
(`openrouterModel` in `config/email-bot.json` to change).

### 2. Gmail
1. In Google Cloud Console: create a project, enable the **Gmail API**, and create
   **OAuth client ID** credentials of type **Desktop app**.
2. Download the JSON as `credentials.json` in the repo root (path configurable via
   `gmailCredentialsPath` in `config/email-bot.json`).
3. First run opens a browser consent flow for `jasonfromhowards1@gmail.com` and writes
   `token.json` (auto-refreshed afterwards). The consent covers Gmail plus Google
   Drive (`drive.readonly`, `drive.file`) — the same scope set Main-Pipeline uses —
   and a cached token missing any of these scopes triggers a fresh consent flow.

### 3. Stripe
1. Set `STRIPE_API_KEY` (test key first) and the price via `videoPriceCents` /
   `videoCurrency` in `config/email-bot.json`.
2. Point a webhook at `POST /webhooks/stripe` for the `checkout.session.completed`
   event and set `STRIPE_WEBHOOK_SECRET`. Local dev:
   `stripe listen --forward-to localhost:8000/webhooks/stripe`.

### 4. Google amenity + image search
- `GOOGLE_MAPS_API_KEY` — enable **Geocoding API** and **Places API**.
- `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID` — a [Programmable Search Engine](https://programmablesearchengine.google.com)
  with **image search** enabled, plus the Custom Search JSON API key.

### 5. Render pipeline
Set `renderUrl` in `config/email-bot.json` to POST assembled job packages to the
pipeline; leave empty to write
`package.json` into the job folder (stub mode). The pipeline calls back
`POST /webhooks/render-complete {"thread_id": ..., "video_url": ...}` when the video
(and its branded howards.ai page) is ready, and Jason sends the delivery email.

## Run

```bash
python -m jason.main        # poller + webhook server together
```

## Develop / test

```bash
python -m pytest                      # fences, photo gate, identity, storage, loop
python -m jason.devchat --from jane@raywhite.com   # chat with Jason offline (real LLM, fake email)
```

devchat commands: `/photos <dir>` attaches a folder of images to the job; `/paid`
simulates the Stripe webhook (flips the flag, triggers the render, Jason confirms).

### End-to-end smoke test (live)
1. Start `python -m jason.main` with a filled `.env` and `stripe listen` forwarding.
2. From another mailbox, email `jasonfromhowards1@gmail.com` about a listing; converse
   through intake and attach 12+ photos.
3. Pay through the Stripe test link Jason sends → watch the webhook flip the job to
   `paid`, the render package get submitted, and Jason's confirmation email arrive.
4. `curl -X POST localhost:8000/webhooks/render-complete -H 'content-type: application/json' \
   -d '{"thread_id":"<thread>","video_url":"https://howards.ai/v/demo"}'` → delivery email.

## Memory

Two interchangeable backends behind the `Store` interface (`STORE_BACKEND=supabase|local`;
Supabase is the default when `SUPABASE_URL`/`SUPABASE_KEY` are set):

- **Supabase** (`jason/supabase_store.py`) — apply `migrations/001_memory.sql` in the
  Supabase SQL editor first. One table, `jason_memory.customers`: one row per
  customer with `personal_details` (jsonb), `preferences` (text), `past_jobs` (jsonb
  array of job dicts) and `conversations` (jsonb array of email entries); agency
  notes reuse the table in a row keyed by the email domain. RLS is enabled with no
  policies, so only the secret key can reach the data.
- **Local files** under `memory/`, same layout, for development.

The production split:

- **Supabase (Postgres)** — all text and structured memory: `details.json` →
  `personal_details`, `profile.md`/`agency.md` → `preferences`, job state and email
  archives → `past_jobs`/`conversations`.
- **Cloudflare R2** — all binary assets: listing photos, logos, amenity imagery,
  render packages (S3-compatible; the render pipeline can pull photos directly).

`agents/<email>/details.json` + `profile.md` always load; `videos/<job>/` and
`emails/` are read on demand. Migrating means writing a second `Store` implementation
in `jason/storage.py` (Supabase for the data methods, R2 for the media methods) —
nothing else changes.

## Deferred (not built yet)

Preview/spec-before-payment, revisions, subscriptions/credits, photo upscaler, the
Cloudflare R2 media store.
