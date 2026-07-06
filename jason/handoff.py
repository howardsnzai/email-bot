"""Human handoff: quiet mode, escalation notification, and wake rules."""

from __future__ import annotations

from jason import config
from jason.identity import is_authenticated_operator
from jason.storage import Store


def escalate_to_human(store: Store, mailer, thread_id: str, reason: str = "") -> dict:
    """Loop in the Howards team and put Jason into quiet mode for the thread."""
    job = store.get_job(thread_id)
    if job is None:
        raise KeyError(f"no job for thread {thread_id}")
    job["quiet_mode"] = True
    job["status"] = "escalated"
    store.put_job(thread_id, job)
    if mailer is not None:
        mailer.label_thread(thread_id, config.LABEL_NEEDS_HUMAN)
        mailer.send_new(
            to=config.OPERATOR_EMAIL,
            subject=f"[JASON — NEEDS HUMAN] thread {thread_id}",
            body=(
                "An agent asked for a human (or Jason escalated).\n\n"
                f"Thread: {thread_id}\n"
                f"Agent: {job.get('agent_email')}\n"
                f"Reason: {reason or 'requested a human'}\n\n"
                "Jason has gone quiet on this thread. Write @jason in the "
                "thread (from this account) to bring him back."
            ),
        )
    return {"escalated": True, "thread_id": thread_id}


def should_invoke_agent(job: dict | None, message: dict) -> bool:
    """Decide whether an inbound message wakes Jason.

    Normal threads: always. Quiet (escalated) threads: only an @jason from
    the AUTHENTICATED operator wakes him — a customer cannot pull him back
    mid-handoff, and a spoofed From header fails the fence in identity.py.
    """
    if job is None or not job.get("quiet_mode"):
        return True
    body = (message.get("body") or "").lower()
    if config.WAKE_TOKEN not in body:
        return False
    return is_authenticated_operator(message)


def wants_human(body: str) -> bool:
    return config.HANDOFF_TRIGGER in (body or "").lower()
