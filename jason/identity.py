"""Sender identity resolution and the operator-authentication fence.

Identity: primary key is the sender's email address; the domain identifies
their agency. A first-time agent from a known agency still gets the agency's
shared branding via the agency pointer.

Operator auth (HARD FENCE — code, never prompt): privileged @jason commands
are honoured only for the authenticated operator. Because the operator
address IS the bot's own inbox, an authentic operator message is one the
Gmail account itself authored (it carries the SENT label) and that the bot
did not send itself (bot outbound is labelled jason-sent). A raw From header
claiming the operator address is just text and proves nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jason import config
from jason.storage import Store


@dataclass
class Identity:
    email: str
    domain: str
    details: dict = field(default_factory=dict)
    profile: str = ""
    agency_notes: str | None = None
    is_new: bool = False


def parse_address(raw_from: str) -> str:
    """Extract the bare address from a From header like 'Jane <jane@x.com>'."""
    m = re.search(r"<([^>]+)>", raw_from)
    addr = m.group(1) if m else raw_from
    return addr.strip().lower()


def resolve_identity(store: Store, from_address: str) -> Identity:
    email = parse_address(from_address)
    domain = email.split("@", 1)[1] if "@" in email else ""
    details = store.get_agent_details(email)
    is_new = details is None
    if is_new:
        details = {"email": email, "agency": domain}
        store.put_agent_details(email, details)
    return Identity(
        email=email,
        domain=domain,
        details=details,
        profile=store.get_agent_profile(email),
        agency_notes=store.get_agency(details.get("agency") or domain),
        is_new=is_new,
    )


def is_authenticated_operator(message: dict) -> bool:
    """The operator-auth fence.

    `message` is our parsed inbound-message dict (see gmail_client.parse_message):
      - label_ids: Gmail labels on the message
      - from_email: parsed From address
      - sent_by_bot: True if this message id is one the bot sent (jason-sent label)

    Authentic operator = authored by our own account (SENT label present),
    from the operator address, and not one of the bot's own outbound messages.
    """
    if message.get("from_email", "").lower() != config.OPERATOR_EMAIL:
        return False
    labels = set(message.get("label_ids") or [])
    if "SENT" not in labels:
        return False  # external mail spoofing the From header lands here
    if message.get("sent_by_bot"):
        return False  # Jason's own outbound is not an operator command
    return True
