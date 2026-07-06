# Build Prompt — How Cloudflare is Organised for Jason

You are organising the **Cloudflare side** of Jason, the Howards email agent.
Cloudflare holds **all binary assets** in **R2** (S3-compatible object storage) and —
because howards.ai fronts through Cloudflare — serves the **branded delivery pages**.
All text and structured data lives in Supabase (see `supabase-organisation.md`); R2
objects are referenced from Supabase by **object key**, never duplicated as blobs.

## The one guiding rule

> **R2 stores bytes; Supabase stores the truth about them.** An object's existence,
> ownership, and meaning are recorded in Supabase; R2 keys are content-addressed-ish,
> predictable, and never encode business state (no `paid/` prefixes, no status in
> paths — status lives in the `jobs` table).

## Buckets

- **`jason-media`** (private) — everything working: listing photos, agent headshots,
  agency logos, amenity imagery, assembled render packages.
- **`howards-delivery`** (public via custom domain / Cloudflare CDN) — only finished,
  paid-for deliverables: final videos and the assets their branded pages need.

Private by default; nothing in `jason-media` is ever publicly reachable. Promotion to
`howards-delivery` happens only after the render pipeline returns a finished video for
a **paid** job — the same payment fence that gates rendering gates publication.

## Key layout (mirrors the memory layout)

```
jason-media/
  agencies/<domain>/logo.png
  agencies/<domain>/assets/<name>
  agents/<email>/headshot.<ext>
  agents/<email>/jobs/<job_id>/photos/<msg8>_<filename>   # intake photos, as received
  agents/<email>/jobs/<job_id>/amenities/<feature>/<n>.jpg
  agents/<email>/jobs/<job_id>/package.json               # render handoff package

howards-delivery/
  v/<job_id>/video.mp4
  v/<job_id>/poster.jpg
  v/<job_id>/page-assets/...
```

Keys are lowercase; emails/domains sanitised exactly as `jason/storage.py::_safe` does,
so local, Supabase, and R2 layouts always line up.

## Access patterns

- The bot writes/reads R2 with **S3 API credentials** (account ID + access key +
  secret in env: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
  `R2_BUCKET_MEDIA`, `R2_BUCKET_DELIVERY`). Scope the token to just these buckets.
- The **render pipeline pulls photos directly** from R2 via short-lived **presigned
  URLs** included in the render package — the bot never proxies bytes downstream.
- Agents (customers) never see raw R2 URLs. They receive one link: the **branded
  delivery page** on howards.ai.

## The delivery page

Delivery is a branded page, never a raw file link. `howards.ai/v/<job_id>` (Cloudflare
Pages/Worker) renders the video from `howards-delivery` with Howards branding plus the
agent's/agency's branding pulled at build time. The page URL is what Jason emails on
render-complete, and it is stored on the job in Supabase (`video_url`).

## Code integration

- Implement a small `MediaStore` used by `Store.job_media_dir`'s replacement: `put(key,
  bytes)`, `get(key)`, `presign(key, ttl)`, `list(prefix)` — `boto3`/S3-compatible
  against the R2 endpoint `https://<account_id>.r2.cloudflarestorage.com`.
- Local dev keeps writing under `memory/` with the same relative keys; switching to R2
  is a backend swap, not a layout change.

## Lifecycle & hygiene

- Intake photos for jobs marked **unfinished** (ghosted) may be cleaned up after 90
  days via R2 lifecycle rules; delivered jobs keep their media (it's the reorder moat).
- Never delete anything referenced by a `video_jobs` record without archiving the
  record's reference first.
- No secrets in R2, ever; no user-supplied filenames used raw (always sanitised and
  prefixed as above).
