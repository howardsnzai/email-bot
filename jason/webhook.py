"""FastAPI webhook server.

POST /webhooks/stripe          — signature-verified; the ONLY path that flips
                                 a job's paid flag, then triggers the render.
POST /webhooks/render-complete — the render pipeline's callback with the
                                 finished video URL; Jason sends delivery.
"""

from __future__ import annotations

import logging

import stripe
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from jason import config, payments, render
from jason.agent import loop as agent_loop
from jason.storage import get_store

log = logging.getLogger("jason.webhook")

app = FastAPI(title="Jason webhooks")

_mailer = None  # set by main.py; stays None in webhook-only/dev runs


def set_mailer(mailer) -> None:
    global _mailer
    _mailer = mailer


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, config.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        thread_id = payments.find_thread_for_event(session)
        if not thread_id:
            log.warning("paid session %s has no thread_id metadata", session.get("id"))
            return {"ok": True, "matched": False}
        store = get_store()
        job = payments.mark_paid(store, thread_id)  # the payment fence's write path
        log.info("payment confirmed for job %s (thread %s)", job["job_id"], thread_id)
        result = render.trigger_render(store, job)
        if _mailer is not None:
            try:
                agent_loop.notify_payment_received(store, _mailer, thread_id, result)
            except Exception:
                log.exception("payment confirmation email failed for thread %s", thread_id)
        return {"ok": True, "job": job["job_id"], "render": result}

    return {"ok": True, "ignored": event["type"]}


class RenderComplete(BaseModel):
    thread_id: str
    video_url: str


@app.post("/webhooks/render-complete")
async def render_complete(body: RenderComplete):
    store = get_store()
    try:
        job = render.render_complete(store, body.thread_id, body.video_url)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown thread_id")
    if _mailer is not None:
        try:
            agent_loop.notify_video_ready(store, _mailer, body.thread_id, body.video_url)
        except Exception:
            log.exception("delivery email failed for thread %s", body.thread_id)
    return {"ok": True, "job": job["job_id"], "status": job["status"]}


@app.get("/healthz")
async def healthz():
    return {"ok": True}
