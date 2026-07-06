"""Handoff to the (already existing) AI video render pipeline.

trigger_render is guarded by the payment fence: it refuses any job whose
code-owned paid flag is not True, regardless of what the model or any email
claims. With RENDER_URL set the package is POSTed to the pipeline; otherwise
it is written to the job folder (stub mode for development).
"""

from __future__ import annotations

import json
import time

import httpx

from jason import config
from jason.storage import Store


class PaymentFenceError(Exception):
    """Raised when a render is attempted on an unpaid job."""


def assemble_package(store: Store, job: dict) -> dict:
    agent_email = job["agent_email"]
    details = store.get_agent_details(agent_email) or {}
    agency_notes = store.get_agency(details.get("agency", "")) or ""
    return {
        "job_id": job["job_id"],
        "thread_id": job["thread_id"],
        "agent": {
            "email": agent_email,
            "name": details.get("name"),
            "phone": details.get("phone"),
        },
        "agency_branding": agency_notes,
        "intake": job.get("intake", {}),
        "photos": job.get("photos", []),
        "amenities": job.get("amenities", []),
        "created_at": time.time(),
    }


def trigger_render(store: Store, job: dict) -> dict:
    # THE PAYMENT FENCE. Code-owned flag only; never model- or email-asserted.
    if job.get("paid") is not True:
        raise PaymentFenceError(
            f"render refused: job {job.get('job_id')} is not paid "
            "(payment status comes from the Stripe webhook, not from messages)"
        )
    package = assemble_package(store, job)
    if config.RENDER_URL:
        resp = httpx.post(config.RENDER_URL, json=package, timeout=30)
        resp.raise_for_status()
        result = {"submitted": True, "via": "http", "response": resp.status_code}
    else:
        media_dir = store.job_media_dir(job["agent_email"], job["job_id"])
        out = media_dir.parent / "package.json"
        out.write_text(json.dumps(package, indent=2, default=str))
        result = {"submitted": True, "via": "stub", "package_file": str(out)}

    job["status"] = "rendering"
    store.put_job(job["thread_id"], job)
    store.put_video_job(job["agent_email"], job["job_id"], {**job, "package": package})
    return result


def render_complete(store: Store, thread_id: str, video_url: str) -> dict:
    """Called by the pipeline's callback webhook when the video is ready."""
    job = store.get_job(thread_id)
    if job is None:
        raise KeyError(f"no job for thread {thread_id}")
    job["status"] = "delivered"
    job["video_url"] = video_url
    store.put_job(thread_id, job)
    record = store.get_video_job(job["agent_email"], job["job_id"]) or dict(job)
    record.update({"status": "delivered", "video_url": video_url})
    store.put_video_job(job["agent_email"], job["job_id"], record)
    return job
