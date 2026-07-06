"""Stripe payments and the payment fence.

HARD FENCE: the `paid` flag on a job may be set ONLY by mark_paid(), which is
called exclusively from the signature-verified Stripe webhook handler
(webhook.py). The model can read the flag (payment_confirmed tool) but has no
write path to it — set_job_state blocklists payment fields.
"""

from __future__ import annotations

import time

import stripe

from jason import config
from jason.storage import Store


def _client() -> None:
    stripe.api_key = config.STRIPE_API_KEY


def create_payment_link(store: Store, job: dict) -> str:
    """Create a Stripe Payment Link for this job. Returns the URL."""
    _client()
    price = stripe.Price.create(
        unit_amount=config.VIDEO_PRICE_CENTS,
        currency=config.VIDEO_CURRENCY,
        product_data={"name": config.VIDEO_PRODUCT_NAME},
    )
    link = stripe.PaymentLink.create(
        line_items=[{"price": price.id, "quantity": 1}],
        metadata={
            "job_id": job["job_id"],
            "thread_id": job["thread_id"],
            "agent_email": job["agent_email"],
        },
    )
    job["stripe_payment_link_id"] = link.id
    job["payment_link_url"] = link.url
    job["status"] = "awaiting_payment"
    store.put_job(job["thread_id"], job)
    return link.url


def mark_paid(store: Store, thread_id: str) -> dict:
    """The ONLY code path that flips the paid flag. Called from the Stripe
    webhook handler after signature verification — never from the model."""
    job = store.get_job(thread_id)
    if job is None:
        raise KeyError(f"no job for thread {thread_id}")
    job["paid"] = True
    job["paid_at"] = time.time()
    job["status"] = "paid"
    store.put_job(thread_id, job)
    return job


def find_thread_for_event(event_object) -> str | None:
    """Extract our thread id from a Stripe event's metadata. StripeObject is
    not a plain dict in recent SDKs, so only item access is safe."""
    try:
        md = event_object["metadata"] or {}
        return md["thread_id"]
    except (KeyError, TypeError):
        return None
