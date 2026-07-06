"""Offline conversation harness: talk to Jason as a pretend real-estate agent
without Gmail. Real OpenRouter, real memory, fake transport.

    python -m jason.devchat --from jane@raywhite.com
    python -m jason.devchat --from jane@raywhite.com --photos ./some_dir

Type a message and hit enter twice to send. Ctrl-D to quit.
Commands: /photos <dir>  attach every image in a directory to the job
          /paid          simulate the Stripe webhook confirming payment
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from jason import jobs, payments, render
from jason.agent import loop as agent_loop
from jason.storage import get_store


class FakeMailer:
    """Prints instead of sending; same surface as GmailMailer."""

    def send_reply(self, thread_id: str, body: str) -> str:
        print("\n─── Jason replies ─────────────────────────────")
        print(body)
        print("───────────────────────────────────────────────\n")
        return "fake-msg-id"

    def send_new(self, to: str, subject: str, body: str) -> str:
        print(f"\n─── New email to {to} — {subject} ───\n{body}\n──────────\n")
        return "fake-msg-id"

    def label_thread(self, thread_id: str, label: str) -> None:
        print(f"[label {label!r} applied to thread {thread_id}]")


def attach_photos(store, thread_id: str, sender: str, photo_dir: str) -> list[str]:
    job = jobs.get_or_create_job(store, thread_id, sender)
    media = store.job_media_dir(sender, job["job_id"])
    saved = []
    for p in sorted(Path(photo_dir).glob("*")):
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            target = media / p.name
            target.write_bytes(p.read_bytes())
            saved.append(str(target))
    job["photos"] = list(dict.fromkeys(job.get("photos", []) + saved))
    store.put_job(thread_id, job)
    return saved


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="sender", default="testagent@example-realty.com")
    ap.add_argument("--thread", default=None, help="reuse a thread id to continue a conversation")
    ap.add_argument("--photos", default=None, help="directory of images to attach up front")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    store = get_store()
    mailer = FakeMailer()
    thread_id = args.thread or f"devchat-{int(time.time())}"
    print(f"devchat: emailing Jason as {args.sender} on thread {thread_id}")

    saved: list[str] = []
    if args.photos:
        saved = attach_photos(store, thread_id, args.sender, args.photos)
        print(f"[attached {len(saved)} photos]")

    while True:
        print("You (blank line to send, Ctrl-D to quit):")
        lines: list[str] = []
        try:
            while True:
                line = input()
                if line == "" and lines:
                    break
                lines.append(line)
        except EOFError:
            print("\nbye")
            return
        body = "\n".join(lines).strip()
        if not body:
            continue

        if body.startswith("/photos "):
            saved = attach_photos(store, thread_id, args.sender, body.split(" ", 1)[1].strip())
            print(f"[attached {len(saved)} photos]")
            continue
        if body == "/paid":
            job = payments.mark_paid(store, thread_id)  # simulates the Stripe webhook
            result = render.trigger_render(store, job)
            print(f"[payment confirmed; render: {result}]")
            agent_loop.notify_payment_received(store, mailer, thread_id, result)
            continue

        message = {
            "thread_id": thread_id,
            "from_email": args.sender,
            "subject": "Video enquiry",
            "body": body,
            "saved_attachments": saved,
        }
        saved = []  # only announce new attachments once
        agent_loop.process_email(store, mailer, message)


if __name__ == "__main__":
    sys.exit(main())
