"""The 30-second inbox poll: ingest, gate, invoke the agent, persist.

Also runs the ghost sweep: intake jobs silent past GHOST_DAYS are marked
unfinished and archived — no chasing.
"""

from __future__ import annotations

import logging
import time

from jason import config, handoff, jobs
from jason.agent import loop as agent_loop
from jason.gmail_client import GmailMailer
from jason.identity import is_authenticated_operator, parse_address
from jason.storage import Store, get_store

log = logging.getLogger("jason.poller")


def _ignored_sender(msg: dict) -> bool:
    sender = f"{msg.get('from', '')} {msg.get('from_email', '')}".lower()
    return any(pattern in sender for pattern in config.GMAIL_IGNORED_SENDER_PATTERNS)


def process_message(store: Store, mailer: GmailMailer, message_id: str) -> None:
    msg = mailer.fetch_message(message_id)

    # Never react to our own outbound.
    if msg.get("sent_by_bot"):
        mailer.mark_processed(message_id)
        return
    if _ignored_sender(msg):
        log.info("ignored automated sender on message %s: %s", message_id, msg.get("from", ""))
        mailer.mark_processed(message_id)
        return

    thread_id = msg["thread_id"]
    sender = msg["from_email"]
    job = store.get_job(thread_id)

    # Operator messages: the human replying in a handed-off thread stays
    # human-owned; @jason (authenticated only) wakes Jason back up.
    if sender == config.OPERATOR_EMAIL:
        if not is_authenticated_operator(msg):
            log.warning("spoofed operator From on message %s — ignoring as untrusted", message_id)
            mailer.mark_processed(message_id)
            return
        if config.WAKE_TOKEN not in (msg["body"] or "").lower():
            mailer.mark_processed(message_id)  # human talking to the customer; stay quiet
            return
        if job is not None and job.get("quiet_mode"):
            job["quiet_mode"] = False
            job["status"] = "intake" if not job.get("paid") else job["status"]
            store.put_job(thread_id, job)
        msg["operator_note"] = (
            "This message is from the authenticated Howards operator (your own team), "
            "waking you on this thread with instructions. Follow them."
        )
        # The reply must go to the customer on the thread, which send_reply handles.
        agent_loop.process_email(store, mailer, msg)
        mailer.mark_processed(message_id)
        return

    # Customer message on a quiet (escalated) thread: archive, don't respond.
    if not handoff.should_invoke_agent(job, msg):
        store.append_email(
            sender, thread_id,
            {"direction": "inbound", "from": sender, "subject": msg.get("subject", ""),
             "body": msg.get("body", ""), "at": time.time(), "quiet": True},
        )
        mailer.mark_processed(message_id)
        return

    # Normal flow: save photo attachments onto the job before the model runs.
    job = jobs.get_or_create_job(store, thread_id, sender)
    media_dir = store.job_media_dir(sender, job["job_id"])
    msg["saved_attachments"] = mailer.download_attachments(msg, media_dir)
    if msg["saved_attachments"]:
        job["photos"] = list(dict.fromkeys(job.get("photos", []) + msg["saved_attachments"]))
        store.put_job(thread_id, job)

    # "let me talk to a human" is honoured in code as well as by the model.
    if handoff.wants_human(msg["body"]):
        msg["operator_note"] = (
            "The agent has asked for a human. Call escalate_to_human, and send a short warm "
            "note letting them know a real person from Howards will pick this up."
        )

    agent_loop.process_email(store, mailer, msg)
    mailer.mark_processed(message_id)


def ghost_sweep(store: Store) -> None:
    cutoff = time.time() - config.GHOST_DAYS * 86400
    for job in store.list_jobs():
        if job.get("status") in ("intake", "awaiting_payment") and job.get("last_inbound_at", 0) < cutoff:
            job["status"] = "unfinished"
            store.put_job(job["thread_id"], job)
            store.append_email(
                job["agent_email"], job["thread_id"],
                {"direction": "system", "body": f"Job {job['job_id']} marked unfinished "
                 f"(no reply for {config.GHOST_DAYS}+ days during intake).", "at": time.time()},
            )
            store.put_video_job(job["agent_email"], job["job_id"], job)
            log.info("ghost sweep: job %s -> unfinished", job["job_id"])


def run_forever(store: Store | None = None, mailer: GmailMailer | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    store = store or get_store()
    mailer = mailer or GmailMailer()
    log.info("Jason polling %s every %ss", config.OPERATOR_EMAIL, config.POLL_INTERVAL_SECONDS)
    last_sweep = 0.0
    while True:
        try:
            for message_id in mailer.list_unprocessed():
                try:
                    process_message(store, mailer, message_id)
                except Exception:
                    log.exception("failed processing message %s", message_id)
            if time.time() - last_sweep > 3600:
                ghost_sweep(store)
                last_sweep = time.time()
        except Exception:
            log.exception("poll cycle failed")
        time.sleep(config.POLL_INTERVAL_SECONDS)
