"""SupabaseStore: the production Store implementation.

Text and structured memory live in Supabase Postgres (schema in
migrations/001_memory.sql), accessed through PostgREST with the project's
secret key. Media (photos, logos, render packages) is destined for Cloudflare
R2; until that lands, job_media_dir keeps media on local disk exactly like
LocalFileStore, so the rest of the code is unaffected.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import httpx

from jason import config
from jason.storage import LocalFileStore, Store


class SupabaseStore(Store):
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_KEY", "")
        if not (self.url and self.key):
            raise ValueError("SUPABASE_URL and SUPABASE_KEY are required for SupabaseStore")
        self._client = httpx.Client(
            base_url=f"{self.url}/rest/v1",
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        self._lock = threading.Lock()
        # Media stays local until the Cloudflare R2 media store lands.
        self._media = LocalFileStore(root=Path(config.MEMORY_DIR))

    # ---- REST helpers ----
    def _select(self, table: str, params: dict) -> list[dict]:
        r = self._client.get(f"/{table}", params=params)
        r.raise_for_status()
        return r.json()

    def _one(self, table: str, params: dict) -> dict | None:
        rows = self._select(table, {**params, "limit": 1})
        return rows[0] if rows else None

    def _upsert(self, table: str, row: dict, on_conflict: str) -> None:
        r = self._client.post(
            f"/{table}",
            json=row,
            params={"on_conflict": on_conflict},
            headers={"Prefer": "resolution=merge-duplicates"},
        )
        r.raise_for_status()

    def _insert(self, table: str, row: dict) -> None:
        r = self._client.post(f"/{table}", json=row)
        r.raise_for_status()

    def _ensure_agent_row(self, email: str) -> None:
        if self._one("agents", {"email": f"eq.{email}", "select": "email"}) is None:
            self._upsert("agents", {"email": email}, on_conflict="email")

    # ---- agents ----
    def get_agent_details(self, email: str) -> dict | None:
        row = self._one("agents", {"email": f"eq.{email}", "select": "details"})
        return row["details"] if row else None

    def put_agent_details(self, email: str, details: dict) -> None:
        with self._lock:
            self._upsert("agents", {"email": email, "details": details}, on_conflict="email")

    def get_agent_profile(self, email: str) -> str:
        row = self._one("agents", {"email": f"eq.{email}", "select": "profile"})
        return row["profile"] if row else ""

    def append_agent_profile(self, email: str, note: str) -> None:
        with self._lock:
            existing = self.get_agent_profile(email)
            sep = "\n" if existing and not existing.endswith("\n") else ""
            self._upsert(
                "agents",
                {"email": email, "profile": existing + sep + note.rstrip() + "\n"},
                on_conflict="email",
            )

    # ---- agencies ----
    def get_agency(self, domain: str) -> str | None:
        row = self._one("agencies", {"domain": f"eq.{domain}", "select": "notes"})
        return row["notes"] if row else None

    def put_agency(self, domain: str, notes: str) -> None:
        with self._lock:
            self._upsert("agencies", {"domain": domain, "notes": notes}, on_conflict="domain")

    # ---- jobs ----
    def get_job(self, thread_id: str) -> dict | None:
        row = self._one("jobs", {"thread_id": f"eq.{thread_id}", "select": "data"})
        return row["data"] if row else None

    def put_job(self, thread_id: str, job: dict) -> None:
        with self._lock:
            self._ensure_agent_row(job.get("agent_email", ""))
            self._upsert(
                "jobs",
                {
                    "thread_id": thread_id,
                    "job_id": job.get("job_id", ""),
                    "agent_email": job.get("agent_email", ""),
                    "status": job.get("status", "intake"),
                    "paid": bool(job.get("paid")),
                    "data": job,
                },
                on_conflict="thread_id",
            )

    def list_jobs(self) -> list[dict]:
        return [r["data"] for r in self._select("jobs", {"select": "data", "order": "created_at"})]

    # ---- history ----
    def append_email(self, agent_email: str, thread_id: str, entry: dict) -> None:
        self._insert(
            "emails", {"agent_email": agent_email, "thread_id": thread_id, "entry": entry}
        )

    def read_emails(self, agent_email: str, thread_id: str | None = None) -> list[dict]:
        params = {"agent_email": f"eq.{agent_email}", "select": "entry", "order": "id"}
        if thread_id:
            params["thread_id"] = f"eq.{thread_id}"
        return [r["entry"] for r in self._select("emails", params)]

    def list_video_jobs(self, agent_email: str) -> list[str]:
        rows = self._select(
            "video_jobs",
            {"agent_email": f"eq.{agent_email}", "select": "job_id", "order": "created_at"},
        )
        return [r["job_id"] for r in rows]

    def get_video_job(self, agent_email: str, job_id: str) -> dict | None:
        row = self._one(
            "video_jobs",
            {"agent_email": f"eq.{agent_email}", "job_id": f"eq.{job_id}", "select": "record"},
        )
        return row["record"] if row else None

    def put_video_job(self, agent_email: str, job_id: str, record: dict) -> None:
        with self._lock:
            self._ensure_agent_row(agent_email)
            self._upsert(
                "video_jobs",
                {"agent_email": agent_email, "job_id": job_id, "record": record},
                on_conflict="agent_email,job_id",
            )

    # ---- media (local until Cloudflare R2 lands) ----
    def job_media_dir(self, agent_email: str, job_id: str) -> Path:
        return self._media.job_media_dir(agent_email, job_id)
