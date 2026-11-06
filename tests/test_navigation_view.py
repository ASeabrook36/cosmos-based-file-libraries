"""Exact-reference presentation over real local API responses; no model calls."""

import copy
import json

import pytest

from fli_demo.agent import source_documents
from fli_demo.navigation_view import CONTRACT, INSTRUCTIONS, present
from tests.test_granular_inspection import api_read
from tests.test_granular_navigation import QUALIFIER, child_named, hierarchy, read, ready


class Registry:
    def __init__(self):
        self.values = {}
        self.keys = {}

    def register(self, kind, library, value):
        signature = json.dumps([kind, library, value], sort_keys=True)
        if signature not in self.keys:
            ref = ("d" if kind == "document" else "r") + str(len(self.keys) + 1)
            self.keys[signature] = ref
            self.values[ref] = (kind, library, copy.deepcopy(value))
        return self.keys[signature]

    def request(self, ref, library):
        kind, owner, value = self.values[ref]
        assert kind == "read" and owner == library
        return copy.deepcopy(value)


def render(env, evidence, registry=None, introduced=None, request=None):
    registry = registry or Registry()
    introduced = {} if introduced is None else introduced
    return present(evidence, library_id=env.lib, register=registry.register, introduced=introduced, request=request)


async def test_cell_sources_citations_relationships_and_exact_actions_survive_readable_projection():
    env = await ready()
    _, _, _, cell, _ = hierarchy(env)
    source = read(env, cell["readSelector"])
    original = copy.deepcopy(source)
    registry = Registry()
    view = render(env, source, registry)
    assert source == original and view["presentation"] == CONTRACT
    document = view["documents"][0]
    assert document["fileName"] == "procedure.docx" and document["documentRef"]
    assert document["selectionComplete"] and not document["documentComplete"]
    assert document["processingCoverage"] == source["documents"][0]["processingCoverage"]
    assert document["selectionSummary"]["nodes"][0]["kind"] == "tableCell"
    assert document["selectionSummary"]["nodes"][0]["authoredPath"] == [
        {"title": "Operating procedure"},
        {"title": "Eligibility"},
    ]
    assert len(document["sections"]) == 4
    for row, before in zip(document["sections"], source["documents"][0]["sections"], strict=True):
        assert row["text"] == before["text"] and row["citation"] == before["citation"]
        assert row["heading"] == "Eligibility" and row["readScope"] == before["readScope"]
        assert row["location"]["originalCoordinate"] == before["location"]["originalCoordinate"]
        assert row["location"]["paragraph"] == before["location"]["paragraph"]
        for field in ("sectionId", "containingSectionId", "navigationNodeId", "parentNodeId"):
            assert field not in row
        assert not {"sourceAtomId", "parentNodeId", "nodeId"} & row["location"].keys()
        assert registry.request(row["actions"]["read"], env.lib) == before["readSelector"]
        assert registry.request(row["actions"]["parent"], env.lib) == before["parentSelector"]
        assert registry.request(row["actions"]["section"], env.lib) == before["containingSectionSelector"]
    assert "<list-item " in document["sections"][1]["text"]
    assert "<list-item " not in document["sections"][-1]["text"]
    assert QUALIFIER in document["sections"][-1]["text"]
    reread = read(env, registry.request(document["sections"][-1]["actions"]["parent"], env.lib))
    assert reread["documents"][0]["sections"] == source["documents"][0]["sections"]


async def test_info_once_refresh_after_shortening_and_human_scope_on_every_response():
    env = await ready()
    _, section, _, _, qualifier = hierarchy(env)
    first = read(env, section["outlineSelector"])
    second = read(env, qualifier["readSelector"])
    registry, introduced = Registry(), {}
    first_view = render(env, first, registry, introduced)
    second_view = render(env, second, registry, introduced)
    a, b = first_view["documents"][0], second_view["documents"][0]
    assert "documentInfo" in a and "documentInfo" not in b
    assert a["documentRef"] == b["documentRef"] and a["fileName"] == b["fileName"] == "procedure.docx"
    assert b["sections"][0]["heading"] == "Eligibility"
    assert b["sections"][0]["citation"].startswith("procedure.docx (source table ")
    assert b["processingCoverage"]["sourceStructureLimitations"]
    assert render(env, first, registry, {}) == first_view
    refreshed = render(env, second, registry, {})
    assert refreshed["documents"][0]["documentInfo"] == a["documentInfo"]
    changed = copy.deepcopy(second)
    changed["documents"][0]["author"] = "Another source author"
    assert (
        render(env, changed, registry, introduced)["documents"][0]["documentInfo"]["author"] == "Another source author"
    )


async def test_diagnostic_details_preserve_canonical_ids_and_do_not_register_or_suppress_info():
    env = await ready()
    source = read(env)
    registry, introduced = Registry(), {}
    result = render(env, source, registry, introduced, {"details": True})
    assert result == source and result is not source and not registry.values and not introduced
    assert result["documents"][0]["sections"][0]["sectionId"]


async def test_outline_keeps_exact_labeled_preview_and_current_human_path_without_becoming_source():
    env = await ready()
    parent = child_named(env, None, "Operating procedure")
    source = read(env, parent["outlineSelector"])
    view = render(env, source)
    document = view["documents"][0]
    assert document["outlineContext"]["title"] == "Operating procedure"
    assert document["outlineContext"]["authoredPath"] == [{"title": "Operating procedure"}]
    assert source_documents(view) == set()
    for row, before in zip(document["outline"], source["documents"][0]["outline"], strict=True):
        assert row["title"] == before["title"] and row["navigationKind"] == before["navigationKind"]
        assert row.get("navigationPreview") == before.get("navigationPreview")
        assert row["location"] == before["location"]
        assert "text" not in row and row["actions"]["read"]
    assert "navigationPreview" in INSTRUCTIONS and "even when truncated=false" in INSTRUCTIONS


async def test_unknown_source_fields_and_limitations_are_retained_conservatively():
    env = await ready()
    source = read(env)
    source["notes"].extend(["A newly reported source limitation.", {"future": "structured warning"}])
    source["documents"][0]["unknownDocumentFact"] = {"nodeId": "not-a-known-diagnostic-position"}
    source["documents"][0]["sections"][0]["futureStructure"] = {"paragraphs": ["retain exactly"]}
    source["progress"] = {"recoveryRequired": True, "nextAction": "Choose another topic."}
    source["nativeDelivery"] = {"requested": [], "supplied": [], "withheld": []}
    view = render(env, source)
    assert view["notes"] == ["A newly reported source limitation.", {"future": "structured warning"}]
    assert view["documents"][0]["unknownDocumentFact"] == source["documents"][0]["unknownDocumentFact"]
    assert view["documents"][0]["sections"][0]["futureStructure"] == {"paragraphs": ["retain exactly"]}
    assert view["progress"] == source["progress"] and view["nativeDelivery"] == source["nativeDelivery"]


async def test_row_authored_path_hides_only_known_machine_id_and_keeps_human_relationship():
    env = await ready()
    source = read(env)
    path = [{"nodeId": "h1", "title": "Operating procedure", "future": {"nodeId": "preserve-unknown"}}]
    source["documents"][0]["sections"][0]["authoredPath"] = copy.deepcopy(path)
    view = render(env, source)
    assert view["documents"][0]["sections"][0]["authoredPath"] == [
        {"title": "Operating procedure", "future": {"nodeId": "preserve-unknown"}}
    ]
    assert source["documents"][0]["sections"][0]["authoredPath"] == path


@pytest.mark.parametrize(
    "damage",
    [
        "profile",
        "format",
        "media",
        "mixed",
        "cross-snapshot",
        "bad-selector",
        "collision",
        "legacy",
        "future-selector",
        "too-many-selectors",
    ],
)
async def test_unsupported_payload_falls_back_without_registration_or_info_state_changes(damage):
    env = await ready()
    source = read(env)
    document = source["documents"][0]
    if damage == "profile":
        document["processingCoverage"]["sourceStructure"] = "different-source.v1"
    elif damage == "format":
        document["fileType"] = "pdf"
    elif damage == "media":
        document["candidateImages"] = [{"imageId": "image-one"}]
    elif damage == "mixed":
        source["documents"].append({"documentId": "another-format"})
    elif damage == "cross-snapshot":
        document["outlineSelector"]["documents"][0]["generation"] += 1
    elif damage == "bad-selector":
        document["documentSelector"] = None
    elif damage == "collision":
        document["actions"] = {"read": "untrusted"}
    elif damage == "legacy":
        document.pop("fullSelector")
    elif damage == "future-selector":
        document["outlineSelector"]["documents"][0]["futureScope"] = "not-supported-yet"
    else:
        document["outlineSelector"]["documents"] *= 21
    registry, introduced = Registry(), {}
    assert render(env, source, registry, introduced) == source
    assert not registry.values and not introduced


async def test_next_outline_action_uses_current_scope_pins_page_size_and_server_boundary():
    env = await ready()
    parent = child_named(env, None, "Operating procedure")
    request = {**parent["outlineSelector"], "sectionLimit": 1, "afterSection": 0, "maxTokens": 18000}
    source = read(env, request)
    registry = Registry()
    view = render(env, source, registry, request=request)
    selector = registry.request(view["documents"][0]["actions"]["nextOutline"], env.lib)
    assert selector == {**request, "afterSection": source["documents"][0]["nextAfterSection"]}
    next_page = read(env, selector)
    assert len(next_page["documents"][0]["outline"]) == 1
    assert next_page["documents"][0]["outline"] != source["documents"][0]["outline"]
    no_context = Registry()
    assert render(env, source, no_context) == source and not no_context.values
    wrong_scope = {**request, "documents": [{**env.pin, "outlineNodeId": "another-section"}]}
    assert render(env, source, no_context, request=wrong_scope) == source and not no_context.values


@pytest.mark.parametrize("inspection", [True, False])
async def test_inspection_and_budget_refusal_remain_body_free_and_budget_is_never_rewritten(inspection):
    env = await ready()
    request = {"mode": "outline", "documents": [env.pin], "includeText": not inspection, "maxTokens": 1}
    source = api_read(env, request).json()["evidence"]
    registry = Registry()
    view = render(env, source, registry, request=request)
    assert not view["complete"] and not view["selectionComplete"] and not view["documentComplete"]
    assert QUALIFIER not in json.dumps(view)
    assert all("sections" not in d and "outline" not in d for d in view["documents"])
    if inspection:
        recovery = registry.request(view["actions"]["read"], env.lib)
        assert recovery["maxTokens"] == 1 and recovery["includeText"] is True
        assert "actions.read reference" in view["nextAction"] and "readSelector" not in view["nextAction"]
    else:
        assert view["disposition"] == "budget_exceeded"
