# Build Prompt — How Supabase is Organised for Jason

You are organising the **Supabase project** that backs Jason, the Howards email agent.
Supabase holds **everything textual and structured** — the memory system and the
agency/job bookkeeping. It holds **no binary media**: photos, logos, and render
packages live in Cloudflare R2 (see `cloudflare-organisation.md`). If you're about to
put bytes in Supabase, stop — put a URL or an R2 object key in Supabase instead.

## The one guiding rule

> **Structured facts → columns. Freeform judgment → text. Media → R2 (a key, not a blob).**

This mirrors the code split: code reads/writes columns it must trust (payment status,
agency pointers); the model reads and proposes freeform text (profile notes) that code
commits. Never blur the two — a fact the system must rely on does not live inside a
prose note.

## Tables (schema in `migrations/001_memory.sql`)

- **`agents`** — one row per real-estate agent, keyed by **email address** (the
  identity primary key of the whole system).
  - `details jsonb` — structured: name, phone, agency pointer (a domain), branding
    defaults, headshot key. Only whitelisted keys are written (see
    `DETAILS_WHITELIST` in `jason/agent/tools.py`); the model proposes, code commits.
  - `profile text` — freeform, append-only in spirit: durable preferences and
    how-to-act notes, read as context on every email. One-off requests never land here.
- **`agencies`** — one row per **email domain**. Shared branding (colours, standard
  outro, disclaimers, office details) that every agent from that domain inherits via
  their agency pointer. No duplication into agent rows — dereference at read time.
- **`jobs`** — one row per **Gmail thread**; a job is one video order.
  - The full job dict lives in `data jsonb`; `status` and `paid` are ALSO real columns
    so they are queryable and auditable.
  - `paid` is **code-owned**: written only by the Stripe-webhook code path. No model
    write path exists to it, and no human should flip it by hand outside an incident.
- **`video_jobs`** — completed/archived per-job history (style pack, video URL,
  changes requested), keyed `(agent_email, job_id)`. This is what makes "same as my
  last one" cheap.
- **`emails`** — append-only archive of every exchange, `(agent_email, thread_id,
  entry jsonb)`. Read on demand, never loaded wholesale into context.

## Read/write conventions

- **Always loaded per email:** the sender's `agents` row (details + profile) and the
  thread's `jobs` row. Cheap, always relevant.
- **On demand:** `video_jobs` and `emails` — Jason reaches in only when the
  conversation needs history.
- **Write-back at conversation end:** structured facts overwrite `details`; durable
  preferences append to `profile`; completed jobs go to `video_jobs`; every message to
  `emails`. Be conservative promoting preferences; be slower to overwrite a
  long-standing preference on one contrary signal than to record a new fact.

## Security posture

- **RLS is enabled on every table with no policies.** Only the project **secret key**
  (which bypasses RLS) can read or write; Jason's backend is the sole client. There is
  no anon access, no client-side access, no exceptions.
- The secret key lives only in the backend environment (`SUPABASE_KEY`). It is never
  in the repo, never in a prompt, never in an email.
- Nothing in Supabase is trusted as an instruction: rows contain data about agents and
  jobs, and even if an email smuggles text into a profile note, code fences (payment,
  operator auth) do not read prose.

## Migrations

- Schema changes are numbered SQL files in `/migrations`, applied in order via the
  SQL editor or MCP connector. Never mutate schema ad hoc from application code.
- Additive changes preferred (new columns/tables); destructive changes need an
  explicit migration with a stated reason.

## What does NOT go in Supabase

Photos and any binary asset (R2), Stripe secrets or card data (Stripe holds it; we
store only link/session ids in `jobs.data`), Gmail OAuth tokens (backend filesystem /
secret store), and prompts/code (the repo).
