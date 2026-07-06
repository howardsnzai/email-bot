"""Gmail I/O: OAuth, polling reads, threading-correct sends, labels,
attachment downloads.

Code owns send/receive; the model only ever supplies email bodies.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from jason import config
from jason.identity import parse_address

log = logging.getLogger("jason.gmail")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}


def get_service():
    creds = None
    token_path = Path(config.GMAIL_TOKEN_FILE)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(config.GMAIL_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailMailer:
    """The transport handed to the agent loop and handoff module."""

    def __init__(self, service=None):
        self.service = service or get_service()
        self._label_ids: dict[str, str] = {}
        self._thread_meta: dict[str, dict] = {}  # thread_id -> {to, subject, last_message_id}

    # ---- labels ----
    def label_id(self, name: str) -> str:
        if name in self._label_ids:
            return self._label_ids[name]
        existing = self.service.users().labels().list(userId="me").execute().get("labels", [])
        for lb in existing:
            if lb["name"] == name:
                self._label_ids[name] = lb["id"]
                return lb["id"]
        created = (
            self.service.users().labels()
            .create(userId="me", body={"name": name}).execute()
        )
        self._label_ids[name] = created["id"]
        return created["id"]

    def add_label(self, message_id: str, label_name: str) -> None:
        self.service.users().messages().modify(
            userId="me", id=message_id,
            body={"addLabelIds": [self.label_id(label_name)]},
        ).execute()

    def label_thread(self, thread_id: str, label_name: str) -> None:
        self.service.users().threads().modify(
            userId="me", id=thread_id,
            body={"addLabelIds": [self.label_id(label_name)]},
        ).execute()

    # ---- reading ----
    def list_unprocessed(self) -> list[str]:
        """Message ids in the inbox not yet handled by Jason."""
        query = f"in:inbox -label:{config.LABEL_PROCESSED}"
        resp = (
            self.service.users().messages()
            .list(userId="me", q=query, maxResults=25).execute()
        )
        return [m["id"] for m in resp.get("messages", [])]

    def fetch_message(self, message_id: str, media_dir: Path | None = None) -> dict:
        raw = (
            self.service.users().messages()
            .get(userId="me", id=message_id, format="full").execute()
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in raw.get("payload", {}).get("headers", [])
        }
        label_ids = raw.get("labelIds", [])
        body, attachments = self._walk_parts(raw.get("payload", {}))
        msg = {
            "id": message_id,
            "thread_id": raw.get("threadId"),
            "from": headers.get("from", ""),
            "from_email": parse_address(headers.get("from", "")),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "message_id_header": headers.get("message-id", ""),
            "references": headers.get("references", ""),
            "body": body.strip(),
            "label_ids": label_ids,
            "sent_by_bot": self.label_id_present(label_ids, config.LABEL_SENT_BY_BOT),
            "attachments": attachments,  # [{filename, attachment_id, mime}]
            "saved_attachments": [],
        }
        self._thread_meta[msg["thread_id"]] = {
            "to": msg["from"],
            "subject": msg["subject"],
            "last_message_id": msg["message_id_header"],
            "references": (msg["references"] + " " + msg["message_id_header"]).strip(),
        }
        if media_dir is not None:
            msg["saved_attachments"] = self.download_attachments(msg, media_dir)
        return msg

    def label_id_present(self, label_ids: list[str], label_name: str) -> bool:
        try:
            return self.label_id(label_name) in (label_ids or [])
        except Exception:
            return False

    def _walk_parts(self, payload: dict) -> tuple[str, list[dict]]:
        body_chunks: list[str] = []
        attachments: list[dict] = []

        def walk(part: dict) -> None:
            mime = part.get("mimeType", "")
            filename = part.get("filename") or ""
            data = part.get("body", {}).get("data")
            att_id = part.get("body", {}).get("attachmentId")
            if filename and att_id:
                attachments.append({"filename": filename, "attachment_id": att_id, "mime": mime})
            elif mime == "text/plain" and data:
                body_chunks.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
            for sub in part.get("parts", []) or []:
                walk(sub)

        walk(payload)
        if not body_chunks:  # fall back to html if there was no text/plain
            def walk_html(part: dict) -> None:
                data = part.get("body", {}).get("data")
                if part.get("mimeType") == "text/html" and data:
                    import re
                    html = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
                    body_chunks.append(re.sub(r"<[^>]+>", " ", html))
                for sub in part.get("parts", []) or []:
                    walk_html(sub)
            walk_html(payload)
        return "\n".join(body_chunks), attachments

    def download_attachments(self, msg: dict, media_dir: Path) -> list[str]:
        """Save image attachments to the job's media dir; return saved paths."""
        saved: list[str] = []
        media_dir.mkdir(parents=True, exist_ok=True)
        for att in msg["attachments"]:
            ext = Path(att["filename"]).suffix.lower()
            if ext not in IMAGE_EXTS and not att["mime"].startswith("image/"):
                continue
            data = (
                self.service.users().messages().attachments()
                .get(userId="me", messageId=msg["id"], id=att["attachment_id"]).execute()
            )
            content = base64.urlsafe_b64decode(data["data"])
            target = media_dir / f"{msg['id'][:8]}_{Path(att['filename']).name}"
            target.write_bytes(content)
            saved.append(str(target))
        return saved

    # ---- sending ----
    def send_reply(self, thread_id: str, body: str) -> str:
        meta = self._thread_meta.get(thread_id)
        if meta is None:
            # Rehydrate threading info from the latest message in the thread.
            thread = self.service.users().threads().get(
                userId="me", id=thread_id, format="metadata",
                metadataHeaders=["From", "Subject", "Message-ID", "References"],
            ).execute()
            msgs = thread.get("messages", [])
            headers = {
                h["name"].lower(): h["value"]
                for h in (msgs[-1].get("payload", {}).get("headers", []) if msgs else [])
            }
            sender = headers.get("from", "")
            if parse_address(sender) == config.OPERATOR_EMAIL:
                # last message was ours; find the other party from any message
                for m in msgs:
                    hs = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
                    if parse_address(hs.get("from", "")) != config.OPERATOR_EMAIL:
                        sender = hs.get("from", "")
                        break
            meta = {
                "to": sender,
                "subject": headers.get("subject", ""),
                "last_message_id": headers.get("message-id", ""),
                "references": (headers.get("references", "") + " " + headers.get("message-id", "")).strip(),
            }
        subject = meta["subject"] or "Your Howards video"
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        return self._send(
            to=meta["to"], subject=subject, body=body, thread_id=thread_id,
            in_reply_to=meta.get("last_message_id"), references=meta.get("references"),
        )

    def send_new(self, to: str, subject: str, body: str) -> str:
        return self._send(to=to, subject=subject, body=body)

    def _send(self, to: str, subject: str, body: str, thread_id: str | None = None,
              in_reply_to: str | None = None, references: str | None = None) -> str:
        em = EmailMessage()
        em["To"] = to
        em["Subject"] = subject
        if in_reply_to:
            em["In-Reply-To"] = in_reply_to
        if references:
            em["References"] = references
        em.set_content(body)
        payload: dict = {"raw": base64.urlsafe_b64encode(em.as_bytes()).decode()}
        if thread_id:
            payload["threadId"] = thread_id
        sent = self.service.users().messages().send(userId="me", body=payload).execute()
        # Mark our own outbound so it is never mistaken for an operator command.
        try:
            self.add_label(sent["id"], config.LABEL_SENT_BY_BOT)
        except Exception:
            log.warning("could not label outbound message %s", sent.get("id"))
        log.info("sent message %s (thread %s)", sent.get("id"), sent.get("threadId"))
        return sent["id"]

    def mark_processed(self, message_id: str) -> None:
        self.add_label(message_id, config.LABEL_PROCESSED)


def guess_mime(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
