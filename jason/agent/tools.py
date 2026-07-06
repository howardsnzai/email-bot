"""Tool schemas and the dispatcher.

Each tool is deterministic code; the decision to call it is the model's.
The two hard fences live here and in the modules they guard:
  - payment fields are stripped from anything the model writes, and
    trigger_render refuses unpaid jobs (render.PaymentFenceError);
  - operator authority is enforced before the model runs (poller/handoff),
    so no tool here can grant it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from jason import amenities, config, handoff, jobs, payments, render
from jason.identity import Identity
from jason.storage import Store

# details.json fields the model may write via write_memory.
DETAILS_WHITELIST = {
    "name", "phone", "mobile", "title", "website", "headshot",
    "agency", "defaults", "brand_colours", "contact_display",
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send your reply to the agent on this thread. This is the only way "
                           "your words reach them. Write a complete email (greeting, body, "
                           "sign-off); the standing 'talk to a human' line is appended for you.",
            "parameters": {
                "type": "object",
                "properties": {"body": {"type": "string", "description": "The full email body."}},
                "required": ["body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_memory",
            "description": "Read stored memory: a past job record, this agent's archived email "
                           "threads, or their agency's branding notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["past_job", "emails", "agency"]},
                    "key": {
                        "type": "string",
                        "description": "past_job: a job id from the context list; emails: a thread "
                                       "id (or empty for all); agency: a domain.",
                    },
                },
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_memory",
            "description": "Save durable facts about this agent. Structured fields overwrite "
                           "details; freeform durable preferences append to their profile notes. "
                           "Never store one-off, single-order requests here.",
            "parameters": {
                "type": "object",
                "properties": {
                    "details": {
                        "type": "object",
                        "description": f"Structured fields to set. Allowed keys: {sorted(DETAILS_WHITELIST)}.",
                    },
                    "profile_note": {
                        "type": "string",
                        "description": "A short durable note about how this agent likes to work.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_job_state",
            "description": "Read the current job state for this thread.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_job_state",
            "description": "Record job bookkeeping for this thread: intake fields as you learn "
                           "them, confirmed amenities, order notes. Payment and status fields "
                           "are system-owned and will be ignored.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intake": {
                        "type": "object",
                        "description": "Any of: property_address, listing_details, listing_status, "
                                       "format, vibe, call_to_action, branding.",
                    },
                    "amenities": {
                        "type": "array", "items": {"type": "object"},
                        "description": "Amenities the agent has confirmed, with chosen imagery URLs.",
                    },
                    "notes": {"type": "string", "description": "One-off requests for this order only."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_photos",
            "description": "Count and quality-check the photos uploaded for this job "
                           "(at least 12 usable are required).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_amenities",
            "description": "Find marketable features near the property address (parks, schools, "
                           "beaches...). Present results to the agent as suggestions to confirm.",
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": "string"}},
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_amenity_images",
            "description": "Google image search for a confirmed amenity near the property.",
            "parameters": {
                "type": "object",
                "properties": {
                    "feature": {"type": "string", "description": "e.g. 'Takapuna Beach'"},
                    "address": {"type": "string"},
                },
                "required": ["feature", "address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_payment_link",
            "description": "Generate the Stripe payment link for this job. Only works once intake "
                           "is complete and at least 12 usable photos are in.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "payment_confirmed",
            "description": "Check whether the system has confirmed payment for this job. "
                           "Read-only; this is the only source of truth on payment.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_render",
            "description": "Hand the assembled job package to the video render pipeline. "
                           "Refused unless the system has confirmed payment. (Normally the "
                           "system does this itself when payment lands.)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Loop in the Howards team and go quiet on this thread. Use when the "
                           "agent asks for a human or when you're out of your depth.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        },
    },
]


@dataclass
class ToolContext:
    store: Store
    mailer: object  # anything with send_reply/send_new/label_thread
    thread_id: str
    identity: Identity
    sent_replies: list = field(default_factory=list)
    escalated: bool = False


def _job(ctx: ToolContext) -> dict:
    return jobs.get_or_create_job(ctx.store, ctx.thread_id, ctx.identity.email)


def dispatch(ctx: ToolContext, name: str, args: dict) -> dict:
    try:
        return _dispatch(ctx, name, args)
    except render.PaymentFenceError as e:
        return {"error": str(e)}
    except Exception as e:  # tool failures go back to the model, not the user
        return {"error": f"{type(e).__name__}: {e}"}


def _dispatch(ctx: ToolContext, name: str, args: dict) -> dict:
    store, thread_id = ctx.store, ctx.thread_id

    if name == "send_email":
        body = (args.get("body") or "").rstrip()
        if not body:
            return {"error": "empty body"}
        full = body + "\n\n" + config.HANDOFF_LINE  # standing line enforced in code
        ctx.mailer.send_reply(thread_id, full)
        ctx.sent_replies.append(full)
        return {"sent": True}

    if name == "read_memory":
        kind, key = args.get("kind"), args.get("key") or ""
        if kind == "past_job":
            rec = store.get_video_job(ctx.identity.email, key)
            return rec or {"error": f"no past job {key!r}"}
        if kind == "emails":
            entries = store.read_emails(ctx.identity.email, key or None)
            return {"emails": entries[-40:]}
        if kind == "agency":
            notes = store.get_agency(key or ctx.identity.domain)
            return {"agency": key or ctx.identity.domain, "notes": notes or "(no notes on file)"}
        return {"error": f"unknown kind {kind!r}"}

    if name == "write_memory":
        result: dict = {}
        details_updates = args.get("details") or {}
        allowed = {k: v for k, v in details_updates.items() if k in DETAILS_WHITELIST}
        rejected = sorted(set(details_updates) - set(allowed))
        if allowed:
            details = store.get_agent_details(ctx.identity.email) or {"email": ctx.identity.email}
            details.update(allowed)
            store.put_agent_details(ctx.identity.email, details)
            ctx.identity.details = details
            result["details_saved"] = sorted(allowed)
        if rejected:
            result["details_rejected"] = rejected
        note = (args.get("profile_note") or "").strip()
        if note:
            store.append_agent_profile(ctx.identity.email, f"- {note}")
            result["profile_note_saved"] = True
        return result or {"error": "nothing to save"}

    if name == "get_job_state":
        return _job(ctx)

    if name == "set_job_state":
        updates = {k: v for k, v in args.items() if k not in jobs.PROTECTED_FIELDS}
        ignored = sorted(set(args) - set(updates))
        _job(ctx)  # ensure it exists
        job = jobs.update_job(store, thread_id, updates)
        out = {"ok": True, "intake_missing": jobs.missing_intake(job)}
        if ignored:
            out["ignored_system_fields"] = ignored
        return out

    if name == "check_photos":
        return jobs.check_photos(_job(ctx))

    if name == "lookup_amenities":
        return amenities.lookup_amenities(args["address"])

    if name == "search_amenity_images":
        return amenities.search_amenity_images(args["feature"], args["address"])

    if name == "create_payment_link":
        job = _job(ctx)
        ready, why = jobs.intake_complete(job)
        if not ready:  # the pre-payment gate: no link until intake + photos pass
            return {"error": f"cannot create payment link yet: {why}"}
        url = payments.create_payment_link(store, job)
        return {"payment_link": url}

    if name == "payment_confirmed":
        job = _job(ctx)
        return {"paid": bool(job.get("paid")), "status": job.get("status")}

    if name == "trigger_render":
        return render.trigger_render(store, _job(ctx))  # payment fence inside

    if name == "escalate_to_human":
        _job(ctx)
        ctx.escalated = True
        return handoff.escalate_to_human(store, ctx.mailer, thread_id, args.get("reason", ""))

    return {"error": f"unknown tool {name!r}"}


def parse_tool_args(raw: str) -> dict:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
