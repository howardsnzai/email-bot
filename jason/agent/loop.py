"""The agent loop: one inbound email -> one model invocation rehydrated from
stored state -> reply and/or actions -> persist."""

from __future__ import annotations

import json
import logging
import time

from openai import OpenAI

from jason import config, handoff, jobs
from jason.agent import tools
from jason.agent.prompt import build_system_prompt
from jason.identity import Identity, resolve_identity
from jason.storage import Store

log = logging.getLogger("jason.loop")

MAX_TOOL_ROUNDS = 16


def _client() -> OpenAI:
    return OpenAI(base_url=config.OPENROUTER_BASE_URL, api_key=config.OPENROUTER_API_KEY)


def _context_block(store: Store, identity: Identity, job: dict) -> str:
    """The always-loaded memory + bookkeeping, plus pointers to on-demand history."""
    details = json.dumps(identity.details, indent=2, default=str)
    past_jobs = store.list_video_jobs(identity.email)
    photo_names = [p.rsplit("/", 1)[-1] for p in job.get("photos", [])]
    parts = [
        "# Context for this email (assembled by the system)",
        f"Sender: {identity.email} (agency domain: {identity.domain})",
        "New agent — no record on file." if identity.is_new else "Returning agent.",
        f"\n## Agent details (details.json)\n{details}",
        f"\n## Agent profile notes (profile.md)\n{identity.profile or '(none yet)'}",
        f"\n## Agency notes ({identity.domain})\n{identity.agency_notes or '(none on file)'}",
        "\n## Current job state for this thread\n"
        + json.dumps({k: v for k, v in job.items() if k != "photos"}, indent=2, default=str),
        f"Photos received so far ({len(photo_names)}): {', '.join(photo_names) or 'none'}",
        f"Intake fields still missing: {', '.join(jobs.missing_intake(job)) or 'none'}",
        f"\n## Past jobs on file (read_memory kind='past_job'): {past_jobs or 'none'}",
    ]
    return "\n".join(parts)


def _thread_messages(store: Store, identity: Identity, thread_id: str) -> list[dict]:
    """Replay the stored thread as user/assistant turns."""
    msgs = []
    for entry in store.read_emails(identity.email, thread_id):
        role = "assistant" if entry.get("direction") == "outbound" else "user"
        msgs.append({"role": role, "content": entry.get("body", "")})
    return msgs


def process_email(store: Store, mailer, message: dict) -> dict:
    """Entry point: run Jason on one inbound email. `message` is the parsed
    inbound dict; attachments have already been saved and recorded on the job
    by the caller (poller or devchat)."""
    thread_id = message["thread_id"]
    identity = resolve_identity(store, message["from_email"])
    job = jobs.get_or_create_job(store, thread_id, identity.email)
    job["last_inbound_at"] = time.time()
    store.put_job(thread_id, job)

    store.append_email(
        identity.email, thread_id,
        {
            "direction": "inbound",
            "from": message["from_email"],
            "subject": message.get("subject", ""),
            "body": message.get("body", ""),
            "attachments": [p.rsplit("/", 1)[-1] for p in message.get("saved_attachments", [])],
            "at": time.time(),
        },
    )

    ctx = tools.ToolContext(store=store, mailer=mailer, thread_id=thread_id, identity=identity)

    inbound_text = (
        f"New email from {message['from_email']}"
        f" (subject: {message.get('subject') or '(none)'}):\n\n{message.get('body', '')}"
    )
    if message.get("saved_attachments"):
        names = ", ".join(p.rsplit("/", 1)[-1] for p in message["saved_attachments"])
        inbound_text += f"\n\n[System: {len(message['saved_attachments'])} attachment(s) saved: {names}]"
    if message.get("operator_note"):
        inbound_text += f"\n\n[System: {message['operator_note']}]"

    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "system", "content": _context_block(store, identity, job)},
        *_thread_messages(store, identity, thread_id),
        {"role": "user", "content": inbound_text},
    ]

    client = _client()
    nudged = False
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.chat.completions.create(
            model=config.OPENROUTER_MODEL,
            messages=messages,
            tools=tools.TOOL_SCHEMAS,
        )
        choice = resp.choices[0].message
        messages.append(
            {
                "role": "assistant",
                "content": choice.content or "",
                **({"tool_calls": [tc.model_dump() for tc in choice.tool_calls]}
                   if choice.tool_calls else {}),
            }
        )
        if choice.tool_calls:
            for tc in choice.tool_calls:
                args = tools.parse_tool_args(tc.function.arguments)
                log.info("tool %s(%s)", tc.function.name, tc.function.arguments)
                result = tools.dispatch(ctx, tc.function.name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            continue
        # No tool calls: the model thinks it's done.
        if not ctx.sent_replies and not ctx.escalated and not nudged:
            nudged = True
            messages.append(
                {
                    "role": "user",
                    "content": "[System: you have not sent a reply yet. If this email deserves "
                               "one, call send_email now; otherwise say NO_REPLY.]",
                }
            )
            continue
        break

    for body in ctx.sent_replies:
        store.append_email(
            identity.email, thread_id,
            {"direction": "outbound", "body": body, "at": time.time()},
        )
    return {"replies_sent": len(ctx.sent_replies), "escalated": ctx.escalated}


def notify_payment_received(store: Store, mailer, thread_id: str, render_result: dict) -> None:
    """Invoked by the webhook after payment lands and the render is triggered:
    have Jason send the 'payment received, video underway' email."""
    job = store.get_job(thread_id)
    if job is None:
        return
    message = {
        "thread_id": thread_id,
        "from_email": job["agent_email"],
        "subject": "(system event)",
        "body": "",
        "operator_note": (
            "This is a system event, not an email from the agent. The system has CONFIRMED "
            "payment for this job and handed it to the render pipeline "
            f"({render_result.get('via')}). Send the agent a warm confirmation that payment is "
            "received and their video is underway. Don't promise a specific turnaround time."
        ),
    }
    process_email(store, mailer, message)


def notify_video_ready(store: Store, mailer, thread_id: str, video_url: str) -> None:
    """Invoked by the render-complete webhook: Jason delivers the video link."""
    job = store.get_job(thread_id)
    if job is None:
        return
    message = {
        "thread_id": thread_id,
        "from_email": job["agent_email"],
        "subject": "(system event)",
        "body": "",
        "operator_note": (
            "This is a system event, not an email from the agent. Their video is finished. "
            f"Deliver it warmly with this link to their branded Howards page: {video_url}"
        ),
    }
    process_email(store, mailer, message)
