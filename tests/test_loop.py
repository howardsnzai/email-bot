"""Agent-loop machinery with a stubbed model: tool dispatch, reply sending
(with the code-enforced handoff line), and email archiving."""

import json
from types import SimpleNamespace

from jason import config
from jason.agent import loop


class FakeMailer:
    def __init__(self):
        self.replies = []

    def send_reply(self, thread_id, body):
        self.replies.append((thread_id, body))
        return "mid"

    def send_new(self, to, subject, body):
        return "mid"

    def label_thread(self, thread_id, label):
        pass


def _tc(name, args, tc_id="tc1"):
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        model_dump=lambda: {
            "id": tc_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        },
    )


class FakeClient:
    """First turn: record intake + send the reply. Second turn: done."""

    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            msg = SimpleNamespace(
                content=None,
                tool_calls=[
                    _tc("set_job_state", {"intake": {"property_address": "1 Beach Rd"}}, "a"),
                    _tc("send_email", {"body": "Hi Jane,\n\nLovely!\n\nKind regards, Jason — Howards"}, "b"),
                ],
            )
        else:
            msg = SimpleNamespace(content="done", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_process_email_end_to_end(store, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "_client", lambda: fake)
    mailer = FakeMailer()

    result = loop.process_email(
        store, mailer,
        {"thread_id": "t9", "from_email": "jane@raywhite.com",
         "subject": "Video please", "body": "Hi, I'd like a video for 1 Beach Rd."},
    )

    assert result["replies_sent"] == 1
    thread_id, body = mailer.replies[0]
    assert thread_id == "t9"
    assert body.endswith(config.HANDOFF_LINE)  # standing line enforced in code

    job = store.get_job("t9")
    assert job["intake"]["property_address"] == "1 Beach Rd"
    assert job["paid"] is False

    archived = store.read_emails("jane@raywhite.com", "t9")
    directions = [e["direction"] for e in archived]
    assert directions == ["inbound", "outbound"]
