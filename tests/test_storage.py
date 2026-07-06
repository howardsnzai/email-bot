from jason.agent import tools
from jason.identity import Identity


def test_details_roundtrip(store):
    store.put_agent_details("a@b.com", {"email": "a@b.com", "name": "Al"})
    assert store.get_agent_details("a@b.com")["name"] == "Al"


def test_profile_append(store):
    store.append_agent_profile("a@b.com", "- prefers vertical videos")
    store.append_agent_profile("a@b.com", "- always includes open-home CTA")
    profile = store.get_agent_profile("a@b.com")
    assert profile.count("\n") == 2
    assert "vertical" in profile and "open-home" in profile


def test_email_archive_roundtrip(store):
    store.append_email("a@b.com", "t1", {"direction": "inbound", "body": "hi"})
    store.append_email("a@b.com", "t1", {"direction": "outbound", "body": "hello!"})
    entries = store.read_emails("a@b.com", "t1")
    assert [e["direction"] for e in entries] == ["inbound", "outbound"]


def test_video_job_roundtrip(store):
    store.put_video_job("a@b.com", "job1", {"job_id": "job1", "status": "delivered"})
    assert store.list_video_jobs("a@b.com") == ["job1"]
    assert store.get_video_job("a@b.com", "job1")["status"] == "delivered"


def test_write_memory_whitelist(store):
    ctx = tools.ToolContext(
        store=store, mailer=None, thread_id="t1",
        identity=Identity(email="a@b.com", domain="b.com", details={"email": "a@b.com"}),
    )
    out = tools.dispatch(
        ctx, "write_memory",
        {"details": {"name": "Al", "paid": True, "role": "admin"},
         "profile_note": "likes upbeat vibes"},
    )
    assert out["details_saved"] == ["name"]
    assert set(out["details_rejected"]) == {"paid", "role"}
    assert store.get_agent_details("a@b.com").get("paid") is None
    assert "upbeat" in store.get_agent_profile("a@b.com")
