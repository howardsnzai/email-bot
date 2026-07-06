from jason.identity import parse_address, resolve_identity


def test_parse_address():
    assert parse_address("Jane Smith <Jane@RayWhite.com>") == "jane@raywhite.com"
    assert parse_address("jane@raywhite.com") == "jane@raywhite.com"


def test_new_agent_created_with_agency_pointer(store):
    ident = resolve_identity(store, "Jane <jane@raywhite.com>")
    assert ident.is_new
    assert ident.email == "jane@raywhite.com"
    assert ident.details["agency"] == "raywhite.com"


def test_returning_agent_keeps_details(store):
    resolve_identity(store, "jane@raywhite.com")
    details = store.get_agent_details("jane@raywhite.com")
    details["name"] = "Jane Smith"
    store.put_agent_details("jane@raywhite.com", details)

    ident = resolve_identity(store, "jane@raywhite.com")
    assert not ident.is_new
    assert ident.details["name"] == "Jane Smith"


def test_known_agency_branding_reaches_new_agent(store):
    store.put_agency("raywhite.com", "Brand colours: yellow/white. Outro: standard RW.")
    ident = resolve_identity(store, "newagent@raywhite.com")
    assert ident.is_new
    assert "yellow/white" in ident.agency_notes
