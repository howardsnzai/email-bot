"""Per-thread job state (bookkeeping, not a script) and the photo gate.

A job is one video order, keyed by Gmail thread id. State records facts
between emails — it never dictates what Jason says.

The `paid` flag is code-owned: only payments.mark_paid() may set it, and the
model-facing set_job_state tool blocklists it (see agent/tools.py).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from PIL import Image

from jason import config
from jason.storage import Store

# States: intake -> awaiting_payment -> paid -> rendering -> delivered
# plus: unfinished (ghosted), escalated (human handoff / quiet mode)

INTAKE_FIELDS = [
    "property_address",
    "listing_details",
    "listing_status",
    "format",
    "vibe",
    "call_to_action",
    "branding",
]

# Fields the model may never write via set_job_state (code-owned).
PROTECTED_FIELDS = {
    "paid", "payment", "payment_confirmed", "paid_at",
    "stripe_payment_link_id", "quiet_mode", "thread_id", "job_id",
    "agent_email", "status",
}


def new_job(thread_id: str, agent_email: str) -> dict:
    return {
        "job_id": uuid.uuid4().hex[:12],
        "thread_id": thread_id,
        "agent_email": agent_email,
        "status": "intake",
        "paid": False,
        "quiet_mode": False,
        "created_at": time.time(),
        "last_inbound_at": time.time(),
        "intake": {},
        "photos": [],
        "amenities": [],
        "notes": "",
    }


def get_or_create_job(store: Store, thread_id: str, agent_email: str) -> dict:
    job = store.get_job(thread_id)
    if job is None:
        job = new_job(thread_id, agent_email)
        store.put_job(thread_id, job)
    return job


def update_job(store: Store, thread_id: str, updates: dict) -> dict:
    """Code-side update (no field restrictions — the model goes through the
    tool dispatcher, which filters PROTECTED_FIELDS before calling this)."""
    job = store.get_job(thread_id)
    if job is None:
        raise KeyError(f"no job for thread {thread_id}")
    for k, v in updates.items():
        if k == "intake" and isinstance(v, dict):
            job.setdefault("intake", {}).update(v)
        else:
            job[k] = v
    store.put_job(thread_id, job)
    return job


def missing_intake(job: dict) -> list[str]:
    intake = job.get("intake", {})
    return [f for f in INTAKE_FIELDS if not intake.get(f)]


def check_photo_file(path: str) -> dict:
    """Quality-gate one photo: openable + minimum resolution."""
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception as e:
        return {"path": path, "usable": False, "reason": f"unreadable image ({e})"}
    lo, hi = sorted((w, h))
    min_lo, min_hi = sorted((config.MIN_PHOTO_WIDTH, config.MIN_PHOTO_HEIGHT))
    if hi < min_hi or lo < min_lo:
        return {
            "path": path,
            "usable": False,
            "reason": f"low resolution ({w}x{h}; need at least "
                      f"{config.MIN_PHOTO_WIDTH}x{config.MIN_PHOTO_HEIGHT})",
            "size": [w, h],
        }
    return {"path": path, "usable": True, "size": [w, h]}


def check_photos(job: dict) -> dict:
    """Count and quality-gate the job's uploaded photos."""
    results = [check_photo_file(p) for p in job.get("photos", [])]
    usable = [r for r in results if r["usable"]]
    weak = [r for r in results if not r["usable"]]
    return {
        "total": len(results),
        "usable": len(usable),
        "required": config.MIN_PHOTOS,
        "enough": len(usable) >= config.MIN_PHOTOS,
        "weak_photos": [{"file": Path(r["path"]).name, "reason": r["reason"]} for r in weak],
    }


def intake_complete(job: dict) -> tuple[bool, str]:
    """The pre-payment gate: full intake AND enough usable photos.

    Enforced in the tool dispatcher before create_payment_link, so
    'paid but photos unusable' cannot occur.
    """
    missing = missing_intake(job)
    if missing:
        return False, "intake incomplete; missing: " + ", ".join(missing)
    photos = check_photos(job)
    if not photos["enough"]:
        return False, (
            f"only {photos['usable']} usable photos of {photos['required']} required"
            + (f"; weak: {photos['weak_photos']}" if photos["weak_photos"] else "")
        )
    return True, "ready"
