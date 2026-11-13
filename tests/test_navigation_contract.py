"""Actual schema, dispatcher and error examples agree on exact reference use."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from fli_demo.agent import Reply, ToolCall, _run_documents, build_tools, run_turn
from fli_demo.navigation_refs import NavigationSession, ReferenceError, target_of
from tests.test_granular_guidance import tools as profile_tools
from tests.test_granular_navigation import OPENING, ready


def prepared(env):
    raw, owners = build_tools(env.index, [env.lib], "reader")
    session = NavigationSession("reader", [env.lib], target_of(env.index))
    revised = session.tools(raw, owners)
    return session, revised, owners


def definition(tools, prefix):
    return next(t["function"] for t in tools if t["function"]["name"].startswith(prefix))


def validator(tools, prefix="read_documents_"):
    return Draft202012Validator(definition(tools, prefix)["parameters"])


def call(name, arguments, number):
    return Reply(None, [ToolCall(str(number), name, json.dumps(arguments))])


def resolve(env, session, arguments, operation="read"):
    return session.resolve_arguments(operation, env.lib, "reader", arguments, target=target_of(env.index))


async def test_closed_schema_and_runtime_reject_old_trace_mixed_forms_and_execute_the_two_valid_shapes():
    env = await ready()
    session, tools, _ = prepared(env)
    bootstrap = {"mode": "outline", "documents": [env.pin]}
    ref = session.register("read", env.lib, bootstrap)
    docref = session.register("document", env.lib, env.pin)
    valid = [bootstrap, {"reference": ref}, {"reference": ref, "includeText": False, "maxTokens": 1, "details": True}]
    for arguments in valid:
        validator(tools).validate(arguments)
        assert resolve(env, session, arguments)["documents"] == [env.pin]
    invalid = [
        {"reference": ref, "mode": "selected"},
        {"reference": ref, "documents": [env.pin]},
        {"reference": ref, "afterSection": 1},
        {"reference": ref, "references": [ref]},
        {"reference": ref, "includeText": "false"},
        {"mode": "outline", "documents": [{"documentId": env.pin["documentId"]}]},
        {"mode": "selected", "documents": [{**env.pin, "outlineNodeId": ref}]},
        {"mode": "selected", "documents": [{**env.pin, "nodeIds": [ref]}]},
        {"mode": "selected", "documents": [{"documentRef": docref, "outlineNodeId": ref}]},
    ]
    for arguments in invalid:
        assert not validator(tools).is_valid(arguments), arguments
        with pytest.raises(ReferenceError):
            resolve(env, session, arguments)
    search = validator(tools, "search_library_")
    search.validate({"query": "source rule", "documentRef": docref})
    assert resolve(env, session, {"query": "source rule", "documentRef": docref}, "search")["document"] == env.pin
    mixed = {"query": "source rule", "documentRef": docref, "document": env.pin}
    assert not search.is_valid(mixed)
    with pytest.raises(ReferenceError):
        resolve(env, session, mixed, "search")
    # Reserved action syntax in locator fields does not reserve arbitrary source document IDs.
    arbitrary = {"mode": "outline", "documents": [{**env.pin, "documentId": "navarbitrary-91"}]}
    validator(tools).validate(arbitrary)
    assert resolve(env, session, arbitrary) == arbitrary
    assert "Copy from outlineSelector" not in json.dumps(tools)
    assert "documentRef is for search only" in definition(tools, "read_documents_")["description"]


@pytest.mark.parametrize("malformation", ["outline-field", "node-field", "nested-document-ref", "mode-override"])
async def test_actual_agent_follows_executable_known_action_recovery_then_receives_complete_source(malformation):
    env = await ready()
    captures, api_requests, wanted = [], [], {}
    original_read = env.index.read_documents

    def read(library, agent, **arguments):
        api_requests.append(copy.deepcopy(arguments))
        return original_read(library, agent, **arguments)

    env.index.read_documents = read

    def chat(messages, tools):
        captures.append(copy.deepcopy(messages))
        n = len(captures)
        name = definition(tools, "read_documents_")["name"]
        if n == 1:
            return call(name, {"mode": "outline", "documents": [env.pin]}, n)
        payload = json.loads(messages[-1]["content"])
        if n == 2:
            document = payload["documents"][0]
            row = next(row for row in document["outline"] if row["title"] == "Operating procedure")
            wanted["reference"] = row["actions"]["outline"]
            if malformation == "mode-override":
                arguments = {"reference": wanted["reference"], "mode": "selected", "maxTokens": 12000}
            else:
                selection = {**env.pin}
                if malformation == "nested-document-ref":
                    selection = {"documentRef": document["documentRef"]}
                selection["nodeIds" if malformation == "node-field" else "outlineNodeId"] = (
                    [wanted["reference"]] if malformation == "node-field" else wanted["reference"]
                )
                arguments = {"mode": "selected", "documents": [selection], "maxTokens": 12000}
            assert not validator(tools).is_valid(arguments)
            return call(name, arguments, n)
        if n == 3:
            assert len(api_requests) == 1  # rejected before any source read; no autocorrection
            assert "documents" not in payload and not payload["complete"]
            example = payload["recovery"]["read"]
            assert example["scope"] == "outline" and "were not applied" in example["note"]
            assert example["arguments"] == {"reference": wanted["reference"], "maxTokens": 12000}
            validator(tools).validate(example["arguments"])
            return call(example["tool"], example["arguments"], n)
        if n == 4:
            assert api_requests[-1]["mode"] == "outline" and api_requests[-1]["maxTokens"] == 12000
            row = next(
                row
                for row in payload["documents"][0]["outline"]
                if row.get("navigationPreview", {}).get("prefix") == OPENING
            )
            return call(name, {"reference": row["actions"]["read"]}, n)
        assert n == 5 and payload["selectionComplete"] and not payload["documentComplete"]
        assert payload["documents"][0]["sections"][0]["text"] == "<p>" + OPENING + "</p>"
        return Reply(OPENING + " (procedure.docx, source paragraph 2)")

    turn = run_turn(
        chat,
        env.index,
        {"id": "reader", "libraryIds": [env.lib]},
        [],
        "Read the opening rule.",
        presentation="navigation",
        context_token_budget=200000,
        image_inputs=False,
    )
    assert OPENING in turn.answer and len(api_requests) == 3
    assert all(request["mode"] != "full" for request in api_requests)
    assert all(request["documents"][0][key] == value for request in api_requests for key, value in env.pin.items())


async def test_expired_reference_without_old_dictionary_uses_real_discovery_example_and_new_pinned_outline():
    env = await ready()
    captures = []
    expired = "navpreviousTurn-1"

    def chat(messages, tools):
        captures.append(copy.deepcopy(messages))
        n = len(captures)
        name = definition(tools, "read_documents_")["name"]
        if n == 1:
            return call(name, {"reference": expired}, n)
        payload = json.loads(messages[-1]["content"])
        if n == 2:
            assert expired not in json.dumps(payload) and "read" not in payload["recovery"]
            example = payload["recovery"]["discover"]
            discovery = next(t["function"] for t in tools if t["function"]["name"] == example["tool"])
            Draft202012Validator(discovery["parameters"]).validate(example["arguments"])
            return call(example["tool"], example["arguments"], n)
        if n == 3:
            document = next(doc for doc in payload["documents"] if doc["fileName"] == "procedure.docx")
            validator(tools).validate(document["outlineSelector"])
            return call(name, document["outlineSelector"], n)
        if n == 4:
            document = payload["documents"][0]
            assert document["fileName"] == "procedure.docx" and document["documentInfo"]
            row = next(row for row in document["outline"] if row["title"] == "Operating procedure")
            assert row["actions"]["read"] != expired
            return call(name, {"reference": row["actions"]["read"]}, n)
        assert n == 5 and OPENING in json.dumps(payload)
        return Reply(OPENING + " (procedure.docx, source paragraph 2)")

    run_turn(
        chat,
        env.index,
        {"id": "reader", "libraryIds": [env.lib]},
        [{"role": "assistant", "content": "Earlier source was procedure.docx; previous actions expired."}],
        "Revisit the opening rule.",
        presentation="navigation",
        context_token_budget=200000,
        image_inputs=False,
    )
    assert len(captures) == 5


async def test_real_source_scope_error_is_preserved_without_repeating_a_correctly_shaped_failed_reference():
    env = await ready()
    session, tools, owners = prepared(env)
    ref = session.register("read", env.lib, {"mode": "selected", "documents": [{**env.pin, "nodeIds": ["missing"]}]})
    name = definition(tools, "read_documents_")["name"]
    content, _ = _run_documents(
        ToolCall("scope", name, json.dumps({"reference": ref})),
        env.index,
        {"id": "reader", "libraryIds": [env.lib]},
        owners,
        {},
        [],
        lambda: 0,
        200000,
        navigation=session,
    )
    payload = json.loads(content)
    assert payload["errorCategory"] == "source_scope_unavailable" and "read" not in payload["recovery"]
    assert payload["recovery"]["discover"]["arguments"] == {} and "documents" not in payload
    assert session.lookup(ref, "read", env.lib, "reader", target_of(env.index))["documents"][0]["nodeIds"] == [
        "missing"
    ]


@pytest.mark.parametrize("profile", ["current-v1", "custom-structured-v1", "microsoft-indexer-layout-v1"])
def test_non_granular_tool_contracts_remain_byte_identical(profile):
    tools = profile_tools(profile)
    owners = {tool["function"]["name"]: "library" for tool in tools}
    session = NavigationSession("reader", ["library"], "target")
    assert session.tools(tools, owners) == tools
