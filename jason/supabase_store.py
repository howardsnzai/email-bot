"""SupabaseStore: the production Store implementation.

All text and structured memory lives in ONE Supabase table
(jason_memory.customers, see migrations/001_memory.sql) — one row per
customer, keyed by email:

    email            text primary key
    preferences      text   -- freeform durable preferences (profile notes)
    personal_details jsonb  -- structured details: name, phone, agency pointer...
    past_jobs        jsonb  -- array of job dicts, one item per job_id; active
                            -- thread state and per-job history merge into the
                            -- same item (a job dict carries thread_id + job_id)
    conversations    jsonb  -- array of {"thread_id": ..., "entry": {...}}

Agency notes reuse the same table: one row per email DOMAIN (stored in the
email column) with the shared branding notes in `preferences`.

Media (photos, logos, render packages) is destined for Cloudflare R2; until
that lands, job_media_dir keeps media on local disk exactly like
LocalFileStore, so the rest of the code is unaffected.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import httpx

from jason import config
from jason.storage import LocalFileStore, Store


class SupabaseStore(Store):
    def __init__(self, url: str | None = None, key: str | None = None, schema: str | None = None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_KEY", "")
        self.schema = schema or os.environ.get("SUPABASE_SCHEMA", config.SUPABASE_SCHEMA)
        self.customers_table = os.environ.get(
            "SUPABASE_CUSTOMERS_TABLE", config.SUPABASE_CUSTOMERS_TABLE
        )
        if not (self.url and self.key):
            raise ValueError("SUPABASE_URL and SUPABASE_KEY are required for SupabaseStore")
        self._client = httpx.Client(
            base_url=f"{self.url}/rest/v1",
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "User-Agent": "jason-email-bot/1.0",
                "Accept-Profile": self.schema,
                "Content-Profile": self.schema,
            },
            timeout=20,
        )
        self._lock = threading.Lock()
        # Media stays local until the Cloudflare R2 media store lands.
        self._media = LocalFileStore(root=Path(config.MEMORY_DIR))

    # ---- REST helpers ----
    def _select(self, params: dict) -> list[dict]:
        r = self._client.get(f"/{self.customers_table}", params=params)
        r.raise_for_status()
        return r.json()

    def _one(self, params: dict) -> dict | None:
        rows = self._select({**params, "limit": 1})
        return rows[0] if rows else None

    def _row(self, email: str, select: str) -> dict | None:
        return self._one({"email": f"eq.{email}", "select": select})

    def _upsert_row(self, row: dict) -> None:
        r = self._client.post(
            f"/{self.customers_table}",
            json=row,
            params={"on_conflict": "email"},
            headers={"Prefer": "resolution=merge-duplicates", "Content-Profile": self.schema},
        )
        r.raise_for_status()

    def _past_jobs(self, email: str) -> list[dict]:
        row = self._row(email, "past_jobs")
        return row["past_jobs"] if row else []

    def _merge_past_job(self, email: str, key: str, value: str, item: dict) -> None:
        """Upsert one job item in the row's past_jobs array, matching on
        `key` (thread_id or job_id) and merging so keys written by the other
        write path (e.g. render package) survive."""
        jobs = self._past_jobs(email)
        for i, j in enumerate(jobs):
            if j.get(key) == value:
                jobs[i] = {**j, **item}
                break
        else:
            jobs.append(item)
        self._upsert_row({"email": email, "past_jobs": jobs})

    # ---- agents ----
    def get_agent_details(self, email: str) -> dict | None:
        row = self._row(email, "personal_details")
        return row["personal_details"] if row else None

    def put_agent_details(self, email: str, details: dict) -> None:
        with self._lock:
            self._upsert_row({"email": email, "personal_details": details})

    def get_agent_profile(self, email: str) -> str:
        row = self._row(email, "preferences")
        return row["preferences"] if row else ""

    def append_agent_profile(self, email: str, note: str) -> None:
        with self._lock:
            existing = self.get_agent_profile(email)
            sep = "\n" if existing and not existing.endswith("\n") else ""
            self._upsert_row({"email": email, "preferences": existing + sep + note.rstrip() + "\n"})

    # ---- agencies (a row keyed by domain in the same table) ----
    def get_agency(self, domain: str) -> str | None:
        row = self._row(domain, "preferences")
        return row["preferences"] if row else None

    def put_agency(self, domain: str, notes: str) -> None:
        with self._lock:
            self._upsert_row({"email": domain, "preferences": notes})

    # ---- jobs (items in past_jobs, matched by thread_id) ----
    def get_job(self, thread_id: str) -> dict | None:
        row = self._one(
            {
                "past_jobs": "cs." + json.dumps([{"thread_id": thread_id}]),
                "select": "past_jobs",
            }
        )
        if row is None:
            return None
        return next((j for j in row["past_jobs"] if j.get("thread_id") == thread_id), None)

    def put_job(self, thread_id: str, job: dict) -> None:
        email = job.get("agent_email", "")
        if not email:
            raise ValueError(f"job for thread {thread_id} has no agent_email")
        with self._lock:
            self._merge_past_job(email, "thread_id", thread_id, {**job, "thread_id": thread_id})

    def list_jobs(self) -> list[dict]:
        rows = self._select({"select": "past_jobs"})
        jobs = [j for r in rows for j in r["past_jobs"] if j.get("thread_id")]
        return sorted(jobs, key=lambda j: j.get("created_at") or 0)

    # ---- history (items in conversations) ----
    def append_email(self, agent_email: str, thread_id: str, entry: dict) -> None:
        with self._lock:
            row = self._row(agent_email, "conversations")
            conv = row["conversations"] if row else []
            conv.append({"thread_id": thread_id, "entry": entry})
            self._upsert_row({"email": agent_email, "conversations": conv})

    def read_emails(self, agent_email: str, thread_id: str | None = None) -> list[dict]:
        row = self._row(agent_email, "conversations")
        conv = row["conversations"] if row else []
        if thread_id:
            conv = [c for c in conv if c.get("thread_id") == thread_id]
        return [c["entry"] for c in conv]

    # ---- video jobs (the same past_jobs items, matched by job_id) ----
    def list_video_jobs(self, agent_email: str) -> list[str]:
        return [j["job_id"] for j in self._past_jobs(agent_email) if j.get("job_id")]

    def get_video_job(self, agent_email: str, job_id: str) -> dict | None:
        return next(
            (j for j in self._past_jobs(agent_email) if j.get("job_id") == job_id), None
        )

    def put_video_job(self, agent_email: str, job_id: str, record: dict) -> None:
        with self._lock:
            self._merge_past_job(agent_email, "job_id", job_id, {**record, "job_id": job_id})

    # ---- media (local until Cloudflare R2 lands) ----
    def job_media_dir(self, agent_email: str, job_id: str) -> Path:
        return self._media.job_media_dir(agent_email, job_id)
