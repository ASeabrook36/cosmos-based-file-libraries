"""Library endpoints (spec §6.1) and the permission rules (spec §5 rules 2-3, CLAUDE.md rule 7)."""

from __future__ import annotations

import logging

import pytest

from fli.errors import ApiError
from fli.permissions import check_search_allowed
from fli.store import Conflict, document_list_query, library_list_query
from tests.fakes import api


@pytest.fixture
def ctx():
    client, s = api()
    return client, s.libraries, s.queue


def create(client, **body):
    return client.post("/v1/libraries", json={"name": "Finance", "description": "Quarterly reports", **body})


def test_create_shared_library(ctx):
    client, _, _ = ctx
    r = create(client, kind="shared")
    assert r.status_code == 201
    lib = r.json()
    assert lib["id"] == lib["libraryId"] and lib["kind"] == "shared" and "boundAgentId" not in lib
    assert lib["autoAcceptTagProposals"] is False and lib["status"] == "active"
    assert lib["embeddingModel"] == "text-embedding-3-large" and lib["embeddingDimensions"] == 3072
    assert not any(k.startswith("_") for k in lib)  # no Cosmos system fields


def test_create_personal_library_binds_the_agent(ctx):
    client, _, _ = ctx
    lib = create(client, kind="personal", boundAgentId="agent-1").json()
    assert lib["boundAgentId"] == "agent-1" and lib["autoAcceptTagProposals"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "personal"},
        {"kind": "personal", "boundAgentId": "  "},
        {"kind": "shared", "boundAgentId": "agent-1"},
        {"kind": "team"},
        {"kind": "shared", "status": "deleting"},  # unknown fields are refused, not ignored
    ],
)
def test_kind_and_binding_are_checked_at_creation(ctx, body):
    client, _, _ = ctx
    r = create(client, **body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"


def test_validation_errors_never_echo_submitted_values(ctx):
    client, _, _ = ctx
    r = client.post("/v1/libraries", json={"kind": "shared", "name": "x" * 500, "description": "secret words"})
    assert r.status_code == 400 and "secret" not in r.text and "xxxx" not in r.text


@pytest.mark.parametrize("field,value", [("kind", "shared"), ("boundAgentId", "agent-2"), ("boundAgentId", "agent-1")])
def test_patch_touching_kind_or_binding_is_refused_even_with_the_same_value(ctx, field, value):
    client, libraries, _ = ctx
    lib = create(client, kind="personal", boundAgentId="agent-1").json()
    r = client.patch(f"/v1/libraries/{lib['id']}", json={"name": "Renamed", field: value})
    assert r.status_code == 400 and r.json()["error"]["code"] == "immutable_field"
    assert client.get(f"/v1/libraries/{lib['id']}").json()["name"] == "Finance"  # nothing applied


def test_patch_updates_mutable_fields(ctx):
    client, _, _ = ctx
    lib = create(client, kind="shared").json()
    r = client.patch(f"/v1/libraries/{lib['id']}", json={"description": "New", "autoAcceptTagProposals": True})
    assert r.status_code == 200 and r.json()["description"] == "New" and r.json()["autoAcceptTagProposals"] is True
    assert client.patch(f"/v1/libraries/{lib['id']}", json={"autoAcceptTagProposals": "yes"}).status_code == 400
    assert client.patch(f"/v1/libraries/{lib['id']}", json={"stats": {}}).status_code == 400


def test_patch_retries_once_on_a_concurrent_change(ctx, monkeypatch):
    client, libraries, _ = ctx
    lib = create(client, kind="shared").json()
    real, calls = libraries.replace, {"n": 0}

    async def flaky(record):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Conflict(record["id"])
        return await real(record)

    monkeypatch.setattr(libraries, "replace", flaky)
    assert client.patch(f"/v1/libraries/{lib['id']}", json={"name": "B"}).json()["name"] == "B"
    assert calls["n"] == 2


def test_list_defaults_to_active_and_filters_by_kind(ctx):
    client, _, _ = ctx
    shared = create(client, kind="shared").json()
    create(client, kind="personal", boundAgentId="a")
    gone = create(client, kind="shared").json()
    client.delete(f"/v1/libraries/{gone['id']}")
    ids = [lib["id"] for lib in client.get("/v1/libraries", params={"kind": "shared"}).json()["libraries"]]
    assert ids == [shared["id"]]
    assert len(client.get("/v1/libraries").json()["libraries"]) == 2


def test_delete_refuses_at_once_then_purges_in_the_background(ctx):
    client, _, queue = ctx
    lib = create(client, kind="shared").json()
    assert client.delete(f"/v1/libraries/{lib['id']}").status_code == 202
    purge = (lib["id"], {"type": "purgeLibrary", "libraryId": lib["id"]})
    assert queue.sent == [purge]
    for method, path in [("get", ""), ("get", "/tool"), ("patch", "")]:
        r = client.request(method, f"/v1/libraries/{lib['id']}{path}", json={} if method == "patch" else None)
        assert r.status_code == 404 and r.json()["error"]["code"] == "library_not_found"
    # Deleting again queues the purge again: the retry path for a purge that never ran (E28).
    assert client.delete(f"/v1/libraries/{lib['id']}").status_code == 202
    assert queue.sent == [purge, purge]
    assert client.delete("/v1/libraries/nope").json()["error"]["code"] == "library_not_found"


def test_a_queue_failure_on_delete_is_503_and_the_delete_can_be_retried(ctx, monkeypatch):
    client, _, queue = ctx
    lib = create(client, kind="shared").json()
    real = queue.send

    async def broken(message, *, session_id):
        raise RuntimeError("bus down")

    monkeypatch.setattr(queue, "send", broken)
    r = client.delete(f"/v1/libraries/{lib['id']}")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "queue_unavailable")
    assert client.get(f"/v1/libraries/{lib['id']}").status_code == 404  # refused all the same
    monkeypatch.setattr(queue, "send", real)
    assert client.delete(f"/v1/libraries/{lib['id']}").status_code == 202
    assert queue.sent == [(lib["id"], {"type": "purgeLibrary", "libraryId": lib["id"]})]


def test_unknown_library_is_404(ctx):
    client, _, _ = ctx
    assert client.get("/v1/libraries/nope").json()["error"]["code"] == "library_not_found"


def test_every_library_route_needs_the_key(ctx):
    client, _, _ = ctx
    lib = create(client, kind="shared").json()
    from fastapi.testclient import TestClient

    bare = TestClient(client.app)
    for method, path in [("post", ""), ("get", ""), ("get", f"/{lib['id']}"), ("get", f"/{lib['id']}/tool")]:
        assert bare.request(method, f"/v1/libraries{path}").status_code == 401


def test_tool_definition_is_bound_to_the_library_not_a_parameter(ctx):
    client, _, _ = ctx
    lib = create(client, kind="shared", name="Finance — Quarterly Reports!").json()
    tool = client.get(f"/v1/libraries/{lib['id']}/tool").json()
    fn = tool["function"]
    assert fn["name"] == "search_library_finance_quarterly_reports"
    assert fn["description"] == "Quarterly reports"
    assert fn["parameters"]["required"] == ["query"] and "libraryId" not in fn["parameters"]["properties"]


def test_tool_names_fit_function_name_limits():
    from fli.tool import tool_name

    assert len(tool_name("x" * 200)) <= 64 and tool_name("!!!") == "search_library_library"


# The search-time check (spec §5 rule 3), tested directly; the search route calls it.

PERSONAL = {"kind": "personal", "boundAgentId": "agent-1", "status": "active"}
SHARED = {"kind": "shared", "status": "active"}


def test_personal_library_answers_only_its_agent(caplog):
    check_search_allowed("L", PERSONAL, "agent-1")
    with caplog.at_level(logging.WARNING, logger="fli.security"), pytest.raises(ApiError) as err:
        check_search_allowed("L", PERSONAL, "agent-2")
    assert (err.value.status, err.value.code) == (403, "library_not_bound_to_agent")
    assert [r.event for r in caplog.records] == ["library_not_bound_to_agent"]


@pytest.mark.parametrize("agent", ["AGENT-1", "agent-1 ", ""])
def test_binding_is_an_exact_match(agent):
    with pytest.raises(ApiError):
        check_search_allowed("L", PERSONAL, agent)


def test_shared_library_has_no_binding_but_still_needs_an_agent_id():
    check_search_allowed("L", SHARED, "any-agent")
    with pytest.raises(ApiError) as err:
        check_search_allowed("L", SHARED, None)
    assert (err.value.status, err.value.code) == (400, "missing_agent_id")


@pytest.mark.parametrize("library", [None, {**SHARED, "status": "deleting"}])
def test_missing_or_deleting_library_is_404(library):
    with pytest.raises(ApiError) as err:
        check_search_allowed("L", library, "agent-1")
    assert (err.value.status, err.value.code) == (404, "library_not_found")


def test_list_queries_render_parameters_not_values():
    sql, params = library_list_query("shared", "active")
    assert sql == "SELECT * FROM c WHERE c.kind = @kind AND c.status = @status" and len(params) == 2
    sql, params = document_list_query("L", None, "region=EMEA")
    assert sql == "SELECT * FROM c WHERE c.libraryId = @libraryId AND ARRAY_CONTAINS(c.facets, @facet)"
