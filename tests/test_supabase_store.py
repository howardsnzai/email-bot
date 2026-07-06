"""SupabaseStore contract test against a minimal in-process PostgREST fake —
same behavioural assertions as the LocalFileStore tests, no network."""

import json

import httpx
import pytest

from jason.supabase_store import SupabaseStore

PKS = {"agents": ["email"], "agencies": ["domain"], "jobs": ["thread_id"],
       "video_jobs": ["agent_email", "job_id"]}


class FakePostgrest:
    def __init__(self):
        self.tables = {t: [] for t in [*PKS, "emails"]}
        self._serial = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        rows = self.tables[table]
        params = dict(request.url.params)
        if request.method == "GET":
            out = rows
            for k, v in params.items():
                if isinstance(v, str) and v.startswith("eq."):
                    out = [r for r in out if str(r.get(k)) == v[3:]]
            if "order" in params:
                out = sorted(out, key=lambda r: r.get(params["order"].split(".")[0]) or 0)
            if "limit" in params:
                out = out[: int(params["limit"])]
            if "select" in params and params["select"] != "*":
                cols = params["select"].split(",")
                out = [{c: r.get(c) for c in cols} for r in out]
            return httpx.Response(200, json=out)
        if request.method == "POST":
            row = json.loads(request.content)
            if table == "emails":
                self._serial += 1
                rows.append({**row, "id": self._serial})
            else:
                pk = PKS[table]
                existing = next(
                    (r for r in rows if all(r[k] == row.get(k) for k in pk)), None
                )
                if existing is not None:
                    if "merge-duplicates" not in request.headers.get("Prefer", ""):
                        return httpx.Response(409, json={"message": "duplicate key"})
                    existing.update(row)
                else:
                    defaults = {"details": {}, "profile": "", "notes": "", "data": {},
                                "record": {}, "status": "intake", "paid": False}
                    rows.append({**defaults, **row})
            return httpx.Response(201, json=[row])
        return httpx.Response(405)


@pytest.fixture
def sb_store():
    store = SupabaseStore(url="https://fake.supabase.co", key="sb_secret_fake")
    fake = FakePostgrest()
    store._client = httpx.Client(
        base_url="https://fake.supabase.co/rest/v1",
        transport=httpx.MockTransport(fake.handler),
    )
    return store


def test_agent_details_and_profile(sb_store):
    assert sb_store.get_agent_details("a@b.com") is None
    sb_store.put_agent_details("a@b.com", {"email": "a@b.com", "name": "Al"})
    assert sb_store.get_agent_details("a@b.com")["name"] == "Al"
    sb_store.append_agent_profile("a@b.com", "- vertical videos")
    sb_store.append_agent_profile("a@b.com", "- upbeat vibe")
    profile = sb_store.get_agent_profile("a@b.com")
    assert "vertical" in profile and "upbeat" in profile
    # profile writes must not clobber details
    assert sb_store.get_agent_details("a@b.com")["name"] == "Al"


def test_agency_roundtrip(sb_store):
    assert sb_store.get_agency("raywhite.com") is None
    sb_store.put_agency("raywhite.com", "yellow/white branding")
    assert "yellow" in sb_store.get_agency("raywhite.com")


def test_job_roundtrip_and_paid_column(sb_store):
    job = {"job_id": "j1", "thread_id": "t1", "agent_email": "a@b.com",
           "status": "intake", "paid": False, "intake": {"format": "vertical"}}
    sb_store.put_job("t1", job)
    got = sb_store.get_job("t1")
    assert got["intake"]["format"] == "vertical"
    job["paid"] = True
    job["status"] = "paid"
    sb_store.put_job("t1", job)
    assert sb_store.get_job("t1")["paid"] is True
    assert len(sb_store.list_jobs()) == 1


def test_emails_and_video_jobs(sb_store):
    sb_store.append_email("a@b.com", "t1", {"direction": "inbound", "body": "hi"})
    sb_store.append_email("a@b.com", "t1", {"direction": "outbound", "body": "hello"})
    sb_store.append_email("a@b.com", "t2", {"direction": "inbound", "body": "other"})
    assert [e["direction"] for e in sb_store.read_emails("a@b.com", "t1")] == ["inbound", "outbound"]
    assert len(sb_store.read_emails("a@b.com")) == 3

    sb_store.put_video_job("a@b.com", "j1", {"job_id": "j1", "status": "delivered"})
    assert sb_store.list_video_jobs("a@b.com") == ["j1"]
    assert sb_store.get_video_job("a@b.com", "j1")["status"] == "delivered"
