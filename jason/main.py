"""Entrypoint: run the Gmail poller and the webhook server in one process.

    python -m jason.main
"""

from __future__ import annotations

import logging
import threading

import uvicorn

from jason import config, poller, webhook
from jason.gmail_client import GmailMailer


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mailer = GmailMailer()  # first run opens the OAuth consent flow
    webhook.set_mailer(mailer)

    poll_thread = threading.Thread(target=poller.run_forever, name="poller", daemon=True)
    poll_thread.start()

    uvicorn.run(webhook.app, host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT)


if __name__ == "__main__":
    main()
