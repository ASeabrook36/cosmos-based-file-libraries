"""Exact application resolution, session boundaries and ordinary API fences."""

import copy
import json

import pytest

from fli_demo.agent import ToolCall, _run_documents
from fli_demo.navigation_refs import NavigationSession, ReferenceError, combine, target_of
from tests.test_granular_navigation import child_named, hierarchy, read, ready

PIN = {"documentId": "document", "generation": 1, "contentRevision": 2}


def session(**kwargs):
    return NavigationSession("reader", ["library", "other"], "target", **kwargs)


def selector(node="p1", **kwargs):
    return {"mode": "selected", "documents": [{**PIN, "nodeIds": [node], **kwargs}]}


def resolve(registry, arguments, **kwargs):
    return registry.resolve_arguments("read", "library", "reader", arguments, target="target", **kwargs)


def test_reference_is_exact_copy_and_never_a_live_source_cache():
    registry = session()
    original = selector()
    reference = registry.register("read", "library", original)
    original["documents"][0]["nodeIds"].append("p2")
    first = resolve(registry, {"reference": reference})
    assert first == selector()
    first["documents"][0]["generation"] = 42
    assert resolve(registry, {"reference": reference}) == selector()
    assert registry.register("read", "library", selector()) == reference
    assert "text" not in json.dumps(registry.entries)


@pytest.mark.parametrize(
    "override",
    [
        {"documents": [PIN]},
        {"mode": "full"},
        {"afterSection": 2},
        {"sectionLimit": 1},
        {"libraryId": "other"},
        {"references": []},
        {"details": "yes"},
    ],
)
def test_reference_cannot_change_scope_or_snapshot(override):
    registry = session()
    reference = registry.register("read", "library", selector())
    with pytest.raises(ReferenceError):
        resolve(registry, {"reference": reference, **override})


@pytest.mark.parametrize("wrong", ["library", "actor", "target", "kind", "closed"])
def test_references_remain_bound_to_platform_context(wrong):
    registry = session()
    reference = registry.register("read", "library", selector())
    if wrong == "closed":
        registry.close()
    with pytest.raises(ReferenceError):
        registry.lookup(
            reference,
            "document" if wrong == "kind" else "read",
            "other" if wrong == "library" else "library",
            "different" if wrong == "actor" else "reader",
            "other-target" if wrong == "target" else "target",
        )


def test_expired_handle_never_aliases_new_session_or_replacement_content():
    previous, restored = session(), session()
    ref = previous.register("read", "library", selector())
    replacement = selector()
    replacement["documents"][0].update(generation=2, contentRevision=1)
    new_ref = restored.register("read", "library", replacement)
    assert new_ref != ref
    previous.close()
    for registry in (previous, restored):
        with pytest.raises(ReferenceError) as caught:
            resolve(registry, {"reference": ref})
        body = caught.value.payload()
        assert "rediscover" in body["nextAction"] and ref not in json.dumps(body)
    assert resolve(restored, {"reference": new_ref}) == replacement


def test_union_preserves_exact_nodes_and_does_not_claim_container_scope():
    registry = session()
    refs = [registry.register("read", "library", selector(node)) for node in ("p1", "p2")]
    result = resolve(registry, {"references": refs, "maxTokens": 100, "includeText": False})
    assert result == {
        "mode": "selected",
        "documents": [{**PIN, "nodeIds": ["p1", "p2"]}],
        "maxTokens": 100,
        "includeText": False,
    }
    assert "cell" not in json.dumps(result)
    assert resolve(registry, {"reference": refs[0], "details": True}) == {**selector(), "details": True}


@pytest.mark.parametrize("change", ["snapshot", "scope", "mode", "field", "paging", "transport"])
def test_incompatible_union_refuses_instead_of_broadening(change):
    left, right = selector(), selector("p2")
    if change == "snapshot":
        right["documents"][0]["generation"] = 2
    elif change == "scope":
        right["documents"][0]["nodeScope"] = "section"
    elif change == "mode":
        right = {"mode": "full", "documents": [PIN]}
    elif change == "field":
        right["documents"][0].pop("nodeIds")
        right["documents"][0]["sectionIds"] = ["unit"]
    elif change == "paging":
        right["afterSection"] = 1
    else:
        right["includeText"] = False
    with pytest.raises(ReferenceError):
        combine([left, right])


def test_document_reference_is_only_an_exact_search_pin():
    registry = session()
    ref = registry.register("document", "library", PIN)
    assert registry.resolve_arguments("search", "library", "reader", {"query": "condition", "documentRef": ref}) == {
        "query": "condition",
        "document": PIN,
    }
    with pytest.raises(ReferenceError):
        registry.resolve_arguments(
            "search",
            "library",
            "reader",
            {"query": "condition", "documentRef": ref, "document": {"documentId": "other"}},
        )
    with pytest.raises(ReferenceError):
        resolve(registry, {"reference": ref})


@pytest.mark.parametrize(
    "field,value",
    [("generation", True), ("generation", 0), ("contentRevision", -1), ("documentId", ""), ("contentRevision", None)],
)
def test_only_version_bound_returned_selectors_can_be_registered(field, value):
    value_to_register = selector()
    value_to_register["documents"][0][field] = value
    with pytest.raises(ReferenceError):
        session().register("read", "library", value_to_register)


def execute(env, registry, reference, *, agent=None, **extras):
    traces = []
    name = "read_documents_library_test"
    actor = agent or {"id": "reader", "libraryIds": [env.lib]}
    content, images = _run_documents(
        ToolCall("call", name, json.dumps({"reference": reference, **extras})),
        env.index,
        actor,
        {name: env.lib},
        {},
        traces,
        lambda: 0,
        200000,
        navigation=registry,
    )
    return json.loads(content), traces, images


@pytest.mark.parametrize(
    "change,category",
    [
        ("revision", "document_changed"),
        ("delete", "document_not_found"),
        ("personal", "library_not_bound_to_agent"),
        ("detached", "navigation_reference_unavailable"),
        ("target", "navigation_reference_unavailable"),
    ],
)
async def test_resolution_uses_normal_access_snapshot_and_lifecycle_fences(change, category):
    env = await ready()
    selection = child_named(env, None, "Operating procedure")["readSelector"]
    registry = NavigationSession("reader", [env.lib], target_of(env.index))
    ref = registry.register("read", env.lib, selection)
    before = copy.deepcopy(registry.entries)
    actor = {"id": "reader", "libraryIds": [env.lib]}
    if change == "revision":
        record = await env.s.documents.get(env.lib, env.doc["id"])
        record["contentRevision"] += 1
        await env.s.documents.replace(record)
    elif change == "delete":
        env.index.delete_document(env.lib, env.doc["id"])
    elif change == "personal":
        library = await env.s.libraries.get(env.lib)
        library.update(kind="personal", boundAgentId="other")
        await env.s.libraries.replace(library)
    elif change == "detached":
        actor["libraryIds"] = []
    else:
        registry.target = "different-target"
    response, traces, images = execute(env, registry, ref, agent=actor)
    assert response["errorCategory"] == category
    assert response["complete"] is False and "documents" not in response
    assert not images and len(traces) == 1 and traces[0].error
    assert registry.entries == before


async def test_multi_paragraph_read_preserves_list_membership_and_separate_qualifier():
    env = await ready()
    _, _, _, cell, _ = hierarchy(env)
    source = read(env, cell["readSelector"])
    registry = NavigationSession("reader", [env.lib], target_of(env.index))
    refs = [registry.register("read", env.lib, row["readSelector"]) for row in source["documents"][0]["sections"]]
    name, traces = "read_documents_library_test", []
    content, _ = _run_documents(
        ToolCall("union", name, json.dumps({"references": refs})),
        env.index,
        {"id": "reader", "libraryIds": [env.lib]},
        {name: env.lib},
        {},
        traces,
        lambda: 0,
        200000,
        navigation=registry,
    )
    result = json.loads(content)
    assert result["selectionComplete"] and not result["documentComplete"]
    # Exact source order, body and list coordinates survive union. Paragraph refs
    # retain paragraph selection semantics, never assert cell completeness.
    actual = result["documents"][0]["sections"]
    expected = source["documents"][0]["sections"]
    assert [(s["text"], s["location"], s["citation"]) for s in actual] == [
        (s["text"], s["location"], s["citation"]) for s in expected
    ]
    assert all(n["kind"] == "paragraph" for n in result["documents"][0]["selectionSummary"]["nodes"])
