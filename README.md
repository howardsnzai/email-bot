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
   operator (`howardsnz.ai@gmail.com`). Because that address is the bot's own inbox, an
   authentic operator message must carry Gmail's `SENT` label (authored by the account)
   and not be one of Jason's own labelled outbound messages. A spoofed From header is
   just text and fails the check.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill it in
```

### 1. OpenRouter
Set `OPENROUTER_API_KEY`. Default model is `anthropic/claude-sonnet-5`
(`OPENROUTER_MODEL` to change).

### 2. Gmail
1. In Google Cloud Console: create a project, enable the **Gmail API**, and create
   **OAuth client ID** credentials of type **Desktop app**.
2. Download the JSON as `credentials.json` in the repo root.
3. First run opens a browser consent flow for `howardsnz.ai@gmail.com` and writes
   `token.json` (auto-refreshed afterwards).

### 3. Stripe
1. Set `STRIPE_API_KEY` (test key first) and the price via `VIDEO_PRICE_CENTS` /
   `VIDEO_CURRENCY`.
2. Point a webhook at `POST /webhooks/stripe` for the `checkout.session.completed`
   event and set `STRIPE_WEBHOOK_SECRET`. Local dev:
   `stripe listen --forward-to localhost:8000/webhooks/stripe`.

### 4. Google amenity + image search
- `GOOGLE_MAPS_API_KEY` — enable **Geocoding API** and **Places API**.
- `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID` — a [Programmable Search Engine](https://programmablesearchengine.google.com)
  with **image search** enabled, plus the Custom Search JSON API key.

### 5. Render pipeline
Set `RENDER_URL` to POST assembled job packages to the pipeline; leave empty to write
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
2. From another mailbox, email `howardsnz.ai@gmail.com` about a listing; converse
   through intake and attach 12+ photos.
3. Pay through the Stripe test link Jason sends → watch the webhook flip the job to
   `paid`, the render package get submitted, and Jason's confirmation email arrive.
4. `curl -X POST localhost:8000/webhooks/render-complete -H 'content-type: application/json' \
   -d '{"thread_id":"<thread>","video_url":"https://howards.ai/v/demo"}'` → delivery email.

## Memory

Local files under `memory/`, laid out to mirror the future Supabase home (structured
fields → columns, freeform notes → text, media → buckets): `agents/<email>/details.json`
+ `profile.md` always load; `videos/<job>/` and `emails/` are read on demand. Moving to
Supabase means writing a second `Store` implementation in `jason/storage.py` — nothing
else changes.

## Deferred (not built yet)

Preview/spec-before-payment, revisions, subscriptions/credits, photo upscaler, the
Supabase `Store` implementation.
