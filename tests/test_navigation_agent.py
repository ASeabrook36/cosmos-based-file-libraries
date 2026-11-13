"""Normal-agent delivery, continuity, delayed scopes and fail-closed restoration."""

import copy
import json
from types import SimpleNamespace

import pytest

from fli_demo.agent import Reply, ToolCall, _run_documents, build_tools, run_turn
from fli_demo.navigation_refs import NavigationSession, target_of
from fli_demo.responses import azure_responses_chat
from tests.test_granular_navigation import OPENING, child_named, read, ready, source_docx
from tests.test_worker_handlers import drain


def actor(env):
    return {"id": "reader", "libraryIds": [env.lib]}


def read_name(tools):
    return next(t["function"]["name"] for t in tools if t["function"]["name"].startswith("read_documents_"))


async def test_actual_model_view_canonical_evidence_and_named_id_diagnostics():
    env = await ready()
    seen, diagnostics, selected = [], {}, {}

    def chat(messages, tools):
        seen.append(copy.deepcopy(messages))
        name = read_name(tools)
        if len(seen) == 1:
            return Reply(None, [ToolCall("outline", name, json.dumps({"mode": "outline", "documents": [env.pin]}))])
        payload = json.loads(messages[-1]["content"])
        if len(seen) == 2:
            assert payload["presentation"] == "fli.navigation.v1"
            doc = payload["documents"][0]
            assert doc["fileName"] == "procedure.docx" and doc["documentInfo"]
            node = next(n for n in doc["outline"] if n.get("heading") == "Operating procedure")
            assert "navigationNodeId" not in node
            selected["ref"] = node["actions"]["read"]
            return Reply(None, [ToolCall("focused", name, json.dumps({"reference": selected["ref"]}))])
        if len(seen) == 3:
            doc = payload["documents"][0]
            assert doc["fileName"] == "procedure.docx" and "documentInfo" not in doc
            assert payload["selectionComplete"] and not payload["documentComplete"]
            assert OPENING in json.dumps(doc["sections"])
            assert all("sectionId" not in row for row in doc["sections"])
            return Reply(None, [ToolCall("details", name, json.dumps({"reference": selected["ref"], "details": True}))])
        assert len(seen) == 4
        assert "presentation" not in payload
        assert all(row["sectionId"] and row["containingSectionId"] for row in payload["documents"][0]["sections"])
        return Reply("The procedure starts with a source review. (procedure.docx, source paragraph 2)")

    turn = run_turn(
        chat,
        env.index,
        actor(env),
        [],
        "Read the opening rule and show its exact source identifiers.",
        presentation="navigation",
        presentation_diagnostics=diagnostics,
        image_inputs=False,
        context_token_budget=200000,
    )
    assert diagnostics["closed"] and diagnostics["referenceCount"] > 0
    assert len(diagnostics["resolutions"]) == 3
    focused, details = diagnostics["resolutions"][1:]
    assert {k: v for k, v in focused["resolvedArguments"].items() if k != "details"} == {
        k: v for k, v in details["resolvedArguments"].items() if k != "details"
    }
    assert details["resolvedArguments"]["details"] is True
    canonical = [json.loads(m["content"]) for m in turn.messages if m["role"] == "tool"]
    assert canonical[1]["documents"][0]["sections"][0]["sectionId"]
    # Later requests never rewrite a delivered wire prefix to new handles/info.
    for earlier, later in zip(seen, seen[1:], strict=False):
        assert later[: len(earlier)] == earlier


async def test_responses_transport_retains_exact_opaque_reasoning_for_registry_rounds():
    env = await ready()
    requests = []

    def create(**request):
        requests.append(copy.deepcopy(request))
        n = len(requests)
        name = next(t["name"] for t in request["tools"] if t["name"].startswith("read_documents_"))
        if n == 1:
            args = {"mode": "outline", "documents": [env.pin]}
        elif n == 2:
            output = next(x["output"] for x in reversed(request["input"]) if x.get("type") == "function_call_output")
            payload = json.loads(output)
            node = next(x for x in payload["documents"][0]["outline"] if x.get("heading") == "Operating procedure")
            args = {"reference": node["actions"]["read"]}
        output = [{"type": "reasoning", "id": f"reason-{n}", "encrypted_content": f"opaque-exact-{n}"}]
        if n < 3:
            output.append(
                {"type": "function_call", "call_id": f"call-{n}", "name": name, "arguments": json.dumps(args)}
            )
        else:
            output.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "The procedure starts with a source review. (procedure.docx, source paragraph 2)",
                        }
                    ],
                }
            )
        return {
            "status": "completed",
            "model": "pinned",
            "output": output,
            "usage": {"input_tokens": 3000 * n, "output_tokens": 80, "output_tokens_details": {"reasoning_tokens": 20}},
        }

    chat = azure_responses_chat(
        "https://model.invalid", "pinned", client=SimpleNamespace(responses=SimpleNamespace(create=create))
    )
    turn = run_turn(
        chat,
        env.index,
        actor(env),
        [],
        "Read the opening rule and cite it.",
        presentation="navigation",
        image_inputs=False,
        context_token_budget=200000,
    )
    assert len(requests) == 3 and OPENING in turn.answer
    assert [x["encrypted_content"] for x in requests[2]["input"] if x.get("type") == "reasoning"] == [
        "opaque-exact-1",
        "opaque-exact-2",
    ]


async def test_shortened_history_refreshes_labels_but_resolves_app_held_handle():
    env = await ready()
    tools, owners = build_tools(env.index, [env.lib], "reader")
    session = NavigationSession("reader", [env.lib], target_of(env.index))
    session.tools(tools, owners)
    name = read_name(tools)
    selection = child_named(env, None, "Operating procedure")["readSelector"]
    raw = read(env, selection)
    session.record_request("first", "read", env.lib, selection, selection)
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "first", "function": {"name": name, "arguments": json.dumps(selection)}}],
        },
        {"role": "tool", "tool_call_id": "first", "content": json.dumps(raw)},
    ]
    first = json.loads(session.render(messages, owners)[-1]["content"])
    ref = first["documents"][0]["actions"]["outline"]
    # No dictionary, original request or earlier response remains visible. The
    # application still holds the exact registry and owner ledger for this turn.
    shortened = session.render(messages[-1:], owners)
    refreshed = json.loads(shortened[0]["content"])
    assert refreshed["documents"][0]["documentInfo"] == first["documents"][0]["documentInfo"]
    assert refreshed["documents"][0]["fileName"] == "procedure.docx"
    resolved = session.resolve_arguments("read", env.lib, "reader", {"reference": ref})
    assert resolved == raw["documents"][0]["outlineSelector"]
    assert env.index.read_documents(env.lib, "reader", **resolved)["evidence"]["documents"][0]["outline"]


async def test_delayed_reference_survives_pending_replacement_but_not_new_publication():
    env = await ready()
    selection = child_named(env, None, "Operating procedure")["readSelector"]
    registry = NavigationSession("reader", [env.lib], target_of(env.index))
    ref = registry.register("read", env.lib, selection)
    name = "read_documents_library_fixture"

    def invoke():
        content, _ = _run_documents(
            ToolCall("read", name, json.dumps({"reference": ref})),
            env.index,
            actor(env),
            {name: env.lib},
            {},
            [],
            lambda: 0,
            200000,
            navigation=registry,
        )
        return json.loads(content)

    # Delay/other navigation does not rebind the action to a newer current file.
    read(env, {"mode": "outline", "documents": [env.pin]})
    replacement = env.index.replace_document(
        env.lib, env.doc["id"], "procedure.docx", source_docx(qualifier="A changed qualifier.")
    )
    assert replacement
    assert OPENING in json.dumps(invoke())  # active generation remains visible until publication
    await drain(env.s, env.worker)
    stale = invoke()
    assert stale["errorCategory"] == "document_changed" and "documents" not in stale
    assert registry.lookup(ref, "read", env.lib, "reader", target_of(env.index)) == selection


async def test_restored_prose_history_expires_old_refs_and_requires_new_authorized_read():
    env = await ready()
    old = NavigationSession("reader", [env.lib], target_of(env.index))
    expired = old.register("read", env.lib, child_named(env, None, "Operating procedure")["readSelector"])
    old.close()
    calls, diagnostics = [], {}

    def chat(messages, tools):
        calls.append(copy.deepcopy(messages))
        name = read_name(tools)
        if len(calls) == 1:
            return Reply(None, [ToolCall("old", name, json.dumps({"reference": expired}))])
        payload = json.loads(messages[-1]["content"])
        if len(calls) == 2:
            assert payload["errorCategory"] == "navigation_reference_unavailable"
            return Reply(None, [ToolCall("refresh", name, json.dumps({"mode": "outline", "documents": [env.pin]}))])
        if len(calls) == 3:
            node = next(n for n in payload["documents"][0]["outline"] if n.get("heading") == "Operating procedure")
            assert node["actions"]["read"] != expired
            return Reply(None, [ToolCall("fresh", name, json.dumps({"reference": node["actions"]["read"]}))])
        assert OPENING in json.dumps(payload)
        return Reply("The procedure starts with a source review. (procedure.docx, source paragraph 2)")

    turn = run_turn(
        chat,
        env.index,
        actor(env),
        [{"role": "assistant", "content": "Earlier source: procedure.docx."}],
        "Please revisit the opening rule.",
        presentation="navigation",
        presentation_diagnostics=diagnostics,
        context_token_budget=200000,
        image_inputs=False,
    )
    assert len(calls) == 4 and diagnostics["closed"]
    assert len(diagnostics["resolutions"]) == 2  # expired call never reached the index
    assert turn.searches[0].error
    with pytest.raises(ValueError, match="Restored navigation"):
        run_turn(chat, env.index, actor(env), turn.messages, "Continue", presentation="navigation")


async def test_registry_closes_on_model_failure_without_changing_default_turn_schema():
    env = await ready()
    diagnostics = {}

    def fail(*args):
        raise RuntimeError("simulated provider failure")

    with pytest.raises(RuntimeError, match="simulated"):
        run_turn(
            fail,
            env.index,
            actor(env),
            [],
            "Read a rule",
            presentation="navigation",
            presentation_diagnostics=diagnostics,
        )
    assert diagnostics["closed"]


async def test_exact_union_budget_refusal_is_atomic_and_never_registers_withheld_bodies():
    env = await ready()
    registry = NavigationSession("reader", [env.lib], target_of(env.index))
    selection = child_named(env, None, "Operating procedure")["readSelector"]
    ref = registry.register("read", env.lib, selection)
    name = "read_documents_library_fixture"
    content, images = _run_documents(
        ToolCall("limited", name, json.dumps({"reference": ref, "maxTokens": 1})),
        env.index,
        actor(env),
        {name: env.lib},
        {},
        [],
        lambda: 0,
        200000,
        navigation=registry,
    )
    payload = json.loads(content)
    assert not payload["selectionComplete"] and payload["disposition"] == "budget_exceeded"
    assert all("sections" not in doc and "outline" not in doc for doc in payload["documents"])
    assert not images
    assert len(registry.entries) == 1
