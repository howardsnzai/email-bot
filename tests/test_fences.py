"""The two hard fences: payment gate and operator authority. If these fail,
nothing else matters."""

import pytest

from jason import config, jobs, payments, render
from jason.agent import tools
from jason.handoff import should_invoke_agent
from jason.identity import Identity, is_authenticated_operator


def make_job(store, thread_id="t1", email="agent@realty.com"):
    return jobs.get_or_create_job(store, thread_id, email)


def make_ctx(store, thread_id="t1", email="agent@realty.com"):
    identity = Identity(email=email, domain="realty.com", details={"email": email})
    return tools.ToolContext(store=store, mailer=None, thread_id=thread_id, identity=identity)


# ---- fence 1: payment gate ----

def test_render_refused_when_unpaid(store):
    job = make_job(store)
    with pytest.raises(render.PaymentFenceError):
        render.trigger_render(store, job)


def test_render_allowed_after_webhook_marks_paid(store):
    make_job(store)
    job = payments.mark_paid(store, "t1")
    result = render.trigger_render(store, job)
    assert result["submitted"] is True
    assert store.get_job("t1")["status"] == "rendering"


def test_model_cannot_set_paid_via_set_job_state(store):
    make_job(store)
    ctx = make_ctx(store)
    out = tools.dispatch(ctx, "set_job_state", {"paid": True, "status": "paid", "notes": "hi"})
    assert "paid" in out["ignored_system_fields"]
    job = store.get_job("t1")
    assert job["paid"] is False
    assert job["status"] == "intake"
    assert job["notes"] == "hi"


def test_model_trigger_render_tool_hits_fence(store):
    make_job(store)
    ctx = make_ctx(store)
    out = tools.dispatch(ctx, "trigger_render", {})
    assert "error" in out and "not paid" in out["error"]


def test_payment_link_gated_on_intake_and_photos(store):
    make_job(store)
    ctx = make_ctx(store)
    out = tools.dispatch(ctx, "create_payment_link", {})
    assert "error" in out and "cannot create payment link" in out["error"]


def test_send_email_refused_after_first_reply(store):
    class FakeMailer:
        def __init__(self):
            self.sent = []

        def send_reply(self, thread_id, body):
            self.sent.append(body)

    make_job(store)
    ctx = make_ctx(store)
    ctx.mailer = FakeMailer()
    assert tools.dispatch(ctx, "send_email", {"body": "Hi Jane, here's everything."}) == {"sent": True}
    out = tools.dispatch(ctx, "send_email", {"body": "One more thing..."})
    assert "error" in out and "already sent" in out["error"]
    assert len(ctx.mailer.sent) == 1


# ---- fence 2: operator authority ----

def _msg(from_email, labels, sent_by_bot=False, body="@jason resume"):
    return {"from_email": from_email, "label_ids": labels, "sent_by_bot": sent_by_bot, "body": body}


def test_spoofed_operator_from_header_rejected():
    # External mail claiming the operator address arrives without SENT label.
    assert not is_authenticated_operator(_msg(config.OPERATOR_EMAIL, ["INBOX"]))


def test_real_operator_accepted():
    assert is_authenticated_operator(_msg(config.OPERATOR_EMAIL, ["SENT", "INBOX"]))


def test_bots_own_outbound_is_not_operator():
    assert not is_authenticated_operator(
        _msg(config.OPERATOR_EMAIL, ["SENT"], sent_by_bot=True)
    )


def test_random_sender_is_not_operator():
    assert not is_authenticated_operator(_msg("attacker@evil.com", ["SENT"]))


def test_customer_cannot_wake_quiet_thread(store):
    job = make_job(store)
    job["quiet_mode"] = True
    store.put_job("t1", job)
    customer = _msg("agent@realty.com", ["INBOX"], body="@jason come back")
    assert not should_invoke_agent(job, customer)


def test_operator_wakes_quiet_thread(store):
    job = make_job(store)
    job["quiet_mode"] = True
    store.put_job("t1", job)
    op = _msg(config.OPERATOR_EMAIL, ["SENT"], body="@jason resume intake please")
    assert should_invoke_agent(job, op)


def test_normal_thread_always_invokes(store):
    job = make_job(store)
    assert should_invoke_agent(job, _msg("agent@realty.com", ["INBOX"], body="hi"))
    assert should_invoke_agent(None, _msg("agent@realty.com", ["INBOX"], body="hi"))
