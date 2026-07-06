"""Storage layer.

A `Store` interface with a local-file implementation mirroring the layout
that will later live in Supabase (structured fields -> columns, freeform
notes -> a text column, media -> storage buckets):

    /memory/
      /agencies/<domain>/
        agency.md              # freeform shared branding notes
        logo.png               # (media)
      /agents/<email>/
        profile.md             # freeform preferences / how-to-act notes
        details.json           # structured: name, emails, phone, agency pointer, defaults
        /videos/<job>/         # per-job history + media
        /emails/               # archived threads
      /jobs/<thread_id>.json   # per-thread job state (code-owned bookkeeping)
"""

from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from jason import config


def _safe(name: str) -> str:
    """Filesystem-safe version of an email/domain/id."""
    return re.sub(r"[^A-Za-z0-9._@-]", "_", name.strip().lower())


class Store(ABC):
    """Persistence interface. Implement against Supabase later; the rest of
    the codebase only talks to this."""

    # -- agents --
    @abstractmethod
    def get_agent_details(self, email: str) -> dict | None: ...

    @abstractmethod
    def put_agent_details(self, email: str, details: dict) -> None: ...

    @abstractmethod
    def get_agent_profile(self, email: str) -> str: ...

    @abstractmethod
    def append_agent_profile(self, email: str, note: str) -> None: ...

    # -- agencies --
    @abstractmethod
    def get_agency(self, domain: str) -> str | None: ...

    @abstractmethod
    def put_agency(self, domain: str, notes: str) -> None: ...

    # -- jobs (per-thread bookkeeping) --
    @abstractmethod
    def get_job(self, thread_id: str) -> dict | None: ...

    @abstractmethod
    def put_job(self, thread_id: str, job: dict) -> None: ...

    @abstractmethod
    def list_jobs(self) -> list[dict]: ...

    # -- history --
    @abstractmethod
    def append_email(self, agent_email: str, thread_id: str, entry: dict) -> None: ...

    @abstractmethod
    def read_emails(self, agent_email: str, thread_id: str | None = None) -> list[dict]: ...

    @abstractmethod
    def list_video_jobs(self, agent_email: str) -> list[str]: ...

    @abstractmethod
    def get_video_job(self, agent_email: str, job_id: str) -> dict | None: ...

    @abstractmethod
    def put_video_job(self, agent_email: str, job_id: str, record: dict) -> None: ...

    # -- media --
    @abstractmethod
    def job_media_dir(self, agent_email: str, job_id: str) -> Path: ...


class LocalFileStore(Store):
    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.MEMORY_DIR)
        self._lock = threading.Lock()

    # ---- paths ----
    def _agent_dir(self, email: str) -> Path:
        return self.root / "agents" / _safe(email)

    def _agency_dir(self, domain: str) -> Path:
        return self.root / "agencies" / _safe(domain)

    def _job_file(self, thread_id: str) -> Path:
        return self.root / "jobs" / f"{_safe(thread_id)}.json"

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        return json.loads(path.read_text())

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(path)

    # ---- agents ----
    def get_agent_details(self, email: str) -> dict | None:
        return self._read_json(self._agent_dir(email) / "details.json")

    def put_agent_details(self, email: str, details: dict) -> None:
        with self._lock:
            self._write_json(self._agent_dir(email) / "details.json", details)

    def get_agent_profile(self, email: str) -> str:
        p = self._agent_dir(email) / "profile.md"
        return p.read_text() if p.exists() else ""

    def append_agent_profile(self, email: str, note: str) -> None:
        with self._lock:
            p = self._agent_dir(email) / "profile.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            existing = p.read_text() if p.exists() else ""
            sep = "\n" if existing and not existing.endswith("\n") else ""
            p.write_text(existing + sep + note.rstrip() + "\n")

    # ---- agencies ----
    def get_agency(self, domain: str) -> str | None:
        p = self._agency_dir(domain) / "agency.md"
        return p.read_text() if p.exists() else None

    def put_agency(self, domain: str, notes: str) -> None:
        with self._lock:
            p = self._agency_dir(domain) / "agency.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(notes)

    # ---- jobs ----
    def get_job(self, thread_id: str) -> dict | None:
        return self._read_json(self._job_file(thread_id))

    def put_job(self, thread_id: str, job: dict) -> None:
        with self._lock:
            self._write_json(self._job_file(thread_id), job)

    def list_jobs(self) -> list[dict]:
        jobs_dir = self.root / "jobs"
        if not jobs_dir.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(jobs_dir.glob("*.json"))]

    # ---- history ----
    def append_email(self, agent_email: str, thread_id: str, entry: dict) -> None:
        with self._lock:
            p = self._agent_dir(agent_email) / "emails" / f"{_safe(thread_id)}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")

    def read_emails(self, agent_email: str, thread_id: str | None = None) -> list[dict]:
        d = self._agent_dir(agent_email) / "emails"
        if not d.exists():
            return []
        files = [d / f"{_safe(thread_id)}.jsonl"] if thread_id else sorted(d.glob("*.jsonl"))
        out: list[dict] = []
        for p in files:
            if p.exists():
                out.extend(json.loads(line) for line in p.read_text().splitlines() if line.strip())
        return out

    def list_video_jobs(self, agent_email: str) -> list[str]:
        d = self._agent_dir(agent_email) / "videos"
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    def get_video_job(self, agent_email: str, job_id: str) -> dict | None:
        return self._read_json(self._agent_dir(agent_email) / "videos" / _safe(job_id) / "job.json")

    def put_video_job(self, agent_email: str, job_id: str, record: dict) -> None:
        with self._lock:
            self._write_json(
                self._agent_dir(agent_email) / "videos" / _safe(job_id) / "job.json", record
            )

    # ---- media ----
    def job_media_dir(self, agent_email: str, job_id: str) -> Path:
        d = self._agent_dir(agent_email) / "videos" / _safe(job_id) / "photos"
        d.mkdir(parents=True, exist_ok=True)
        return d


_default_store: Store | None = None


def get_store() -> Store:
    """Backend selection: STORE_BACKEND=local|supabase, defaulting to Supabase
    when SUPABASE_URL/SUPABASE_KEY are present, else local files."""
    global _default_store
    if _default_store is None:
        import os

        backend = os.environ.get("STORE_BACKEND", "").lower()
        has_supabase = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))
        if backend == "supabase" or (not backend and has_supabase):
            from jason.supabase_store import SupabaseStore

            _default_store = SupabaseStore()
        else:
            _default_store = LocalFileStore()
    return _default_store
