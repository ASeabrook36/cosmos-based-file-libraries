"""Application source bookmarks survive trusted chat state, never bypass rereads."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from fli_demo.agent import Reply, ToolCall, _run_documents, build_tools, run_turn
from fli_demo.navigation_refs import NavigationSession, target_of
from fli_demo.source_bookmarks import SourceBookmarkError, SourceBookmarkStore
from tests.test_granular_navigation import OPENING, QUALIFIER, child_named, hierarchy, read, ready, source_docx
from tests.test_modular_ingestion import upload
from tests.test_worker_handlers import drain

KEY = b"application-owned-key-not-in-model" * 2


def owner(env, **kwargs):
    return SourceBookmarkStore("conversation-one", "reader", target_of(env.index), authority_key=KEY, **kwargs)


def actor(env):
    return {"id": "reader", "libraryIds": [env.lib]}


def schema(tools):
    return next(t["function"] for t in tools if t["function"]["name"].startswith("read_documents_"))


def source_turn(env, store, arguments, *, fail_delivery=False, history=(), allow_grounding_retry=False):
    captures = []

    def chat(messages, tools):
        captures.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        if len(captures) == 1:
            Draft202012Validator(schema(tools)["parameters"]).validate(arguments)
            return Reply(None, [ToolCall("read", schema(tools)["name"], json.dumps(arguments))])
        if fail_delivery:
            raise RuntimeError("provider did not complete this source-bearing input")
        return Reply("The requested source region was read.")

    turn = run_turn(
        chat,
        env.index,
        actor(env),
        list(history),
        "Read the specified original source region.",
        presentation="navigation",
        bookmark_store=store,
        conversation_id="conversation-one",
        context_token_budget=200000,
        image_inputs=False,
    )
    if allow_grounding_retry:
        assert 2 <= len(captures) <= 4  # Empty evidence retains the normal grounding recovery loop.
    else:
        assert len(captures) == 2
    payload = json.loads(next(m["content"] for m in reversed(captures[-1]["messages"]) if m.get("role") == "tool"))
    return turn, captures, payload


def registry(env, store):
    tools, owners = build_tools(env.index, [env.lib], "reader")
    session = NavigationSession(
        "reader", [env.lib], target_of(env.index), bookmark_store=store, conversation_id="conversation-one"
    )
    return session, session.tools(tools, owners), owners


def execute(env, store, arguments):
    session, tools, owners = registry(env, store)
    content, images = _run_documents(
        ToolCall("read", schema(tools)["name"], json.dumps(arguments)),
        env.index,
        actor(env),
        owners,
        {},
        [],
        lambda: 0,
        200000,
        navigation=session,
    )
    return json.loads(content), images, tools


async def test_real_source_commit_ack_checkpoint_restore_and_shortened_history_reuse_same_complete_cell():
    env = await ready()
    store = owner(env)
    _, _, _, cell, _ = hierarchy(env)
    first, _, delivered = source_turn(env, store, {**cell["readSelector"], "maxTokens": 12000})
    doc = delivered["documents"][0]
    bookmark = doc["sourceBookmark"]
    assert bookmark["scope"]["sourceAtomCount"] == 4 and "tableCell" in bookmark["scope"]["description"]
    assert "Operating procedure / Eligibility" in bookmark["label"]
    assert bookmark["tool"].startswith("read_documents_")
    checkpoint = store.checkpoint()
    assert len(checkpoint["payload"]["entries"]) == 1
    assert QUALIFIER not in json.dumps(checkpoint) and OPENING not in json.dumps(checkpoint)
    assert "authority" not in json.dumps(checkpoint) and "signature" in checkpoint
    entry = checkpoint["payload"]["entries"][0]
    assert entry["request"] == {**cell["readSelector"], "maxTokens": 12000}
    ack_inputs = []

    def ack(messages, tools):
        ack_inputs.append(copy.deepcopy(messages))
        return Reply("Acknowledged.")

    run_turn(
        ack,
        env.index,
        actor(env),
        [{"role": "assistant", "content": first.answer}],
        "Acknowledge briefly.",
        presentation="navigation",
        bookmark_store=store,
        conversation_id="conversation-one",
    )
    assert store.checkpoint() == checkpoint and bookmark["read"]["bookmark"] in ack_inputs[0][0]["content"]
    store.close()
    restored = SourceBookmarkStore.restore(
        checkpoint,
        authority_key=KEY,
        conversation_id="conversation-one",
        actor_id="reader",
        target=target_of(env.index),
    )
    assert restored.expires_at == checkpoint["payload"]["expiresAt"]
    _, _, again = source_turn(env, restored, {**bookmark["read"], "maxTokens": 16000}, history=[])
    after = again["documents"][0]
    assert after["sourceBookmark"]["read"] == bookmark["read"]
    assert [(s["text"], s["citation"], s["location"]) for s in after["sections"]] == [
        (s["text"], s["citation"], s["location"]) for s in doc["sections"]
    ]
    assert after["selectionComplete"] and not after["documentComplete"]
    assert "<list-item " in after["sections"][1]["text"] and "<list-item " not in after["sections"][-1]["text"]
    assert QUALIFIER in after["sections"][-1]["text"]
    assert restored.checkpoint()["payload"]["entries"][0]["request"] == entry["request"]


@pytest.mark.parametrize("damage", ["signature", "content", "key", "conversation", "actor", "target", "expired"])
async def test_untrusted_or_foreign_checkpoint_never_restores_authority_or_extends_expiry(damage):
    env = await ready()
    clock = [100.0]
    store = owner(env, clock=lambda: clock[0], ttl_seconds=10)
    source_turn(env, store, child_named(env, None, "Operating procedure")["readSelector"])
    checkpoint = store.checkpoint()
    args = dict(
        authority_key=KEY,
        conversation_id="conversation-one",
        actor_id="reader",
        target=target_of(env.index),
        clock=lambda: clock[0],
    )
    if damage == "signature":
        checkpoint["signature"] = "0" * 64
    elif damage == "content":
        checkpoint["payload"]["entries"][0]["request"]["documents"][0]["generation"] += 1
    elif damage == "key":
        args["authority_key"] = b"different-protected-application-key"
    elif damage == "conversation":
        args["conversation_id"] = "other-conversation"
    elif damage == "actor":
        args["actor_id"] = "other-actor"
    elif damage == "target":
        args["target"] = "other-api"
    else:
        clock[0] = 111.0
    with pytest.raises(SourceBookmarkError):
        SourceBookmarkStore.restore(checkpoint, **args)
    assert store.expires_at == 110.0
    with pytest.raises(ValueError, match="trusted application owner"):
        run_turn(
            None,
            env.index,
            actor(env),
            [],
            "Read",
            presentation="navigation",
            bookmark_store=checkpoint,
            conversation_id="conversation-one",
        )


async def test_old_action_expires_but_saved_bookmark_uses_normal_api_and_small_budget_stays_atomic():
    env = await ready()
    store = owner(env)
    _, _, first = source_turn(env, store, child_named(env, None, "Operating procedure")["readSelector"])
    doc = first["documents"][0]
    response, _, _ = execute(env, store, {"reference": doc["sections"][0]["actions"]["read"]})
    assert response["errorCategory"] == "navigation_reference_unavailable" and "documents" not in response
    response, _, _ = execute(env, store, {**doc["sourceBookmark"]["read"], "maxTokens": 1})
    assert response["disposition"] == "budget_exceeded" and not response["selectionComplete"]
    assert all("sections" not in d for d in response["documents"])
    assert len(store.checkpoint()["payload"]["entries"]) == 1


@pytest.mark.parametrize(
    "change,category",
    [
        ("revision", "document_changed"),
        ("replacement", "document_changed"),
        ("delete", "document_not_found"),
        ("personal", "library_not_bound_to_agent"),
        ("detached", "source_bookmark_unavailable"),
        ("hash", "document_changed"),
    ],
)
async def test_lifecycle_access_and_hash_changes_never_rebind_or_leak_source(change, category):
    env = await ready()
    store = owner(env)
    _, _, first = source_turn(env, store, child_named(env, None, "Operating procedure")["readSelector"])
    bookmark = first["documents"][0]["sourceBookmark"]["read"]
    saved = copy.deepcopy(store.checkpoint()["payload"]["entries"])
    if change == "revision":
        document = await env.s.documents.get(env.lib, env.doc["id"])
        document["contentRevision"] += 1
        await env.s.documents.replace(document)
    elif change == "replacement":
        env.index.replace_document(
            env.lib, env.doc["id"], "procedure.docx", source_docx(qualifier="Changed qualifier.")
        )
        await drain(env.s, env.worker)
    elif change == "delete":
        env.index.delete_document(env.lib, env.doc["id"])
    elif change == "personal":
        library = await env.s.libraries.get(env.lib)
        library.update(kind="personal", boundAgentId="different-reader")
        await env.s.libraries.replace(library)
        # Directly exercise ordinary API access with a registry initialized before revocation.
        session = NavigationSession(
            "reader", [env.lib], target_of(env.index), bookmark_store=store, conversation_id="conversation-one"
        )
        name = "read_documents_library_test"
        response, _ = _run_documents(
            ToolCall("read", name, json.dumps(bookmark)),
            env.index,
            actor(env),
            {name: env.lib},
            {},
            [],
            lambda: 0,
            200000,
            navigation=session,
        )
        assert json.loads(response)["errorCategory"] == category
        return
    elif change == "detached":
        assert store.catalogue([]) == []
        with pytest.raises(SourceBookmarkError):
            store.resolve(
                bookmark["bookmark"],
                conversation_id="conversation-one",
                actor_id="reader",
                target=target_of(env.index),
                library_id=env.lib,
                library_ids=[],
            )
        return
    else:
        original = env.index.read_documents

        def altered(*args, **kwargs):
            result = original(*args, **kwargs)
            result["evidence"]["documents"][0]["sourceSha256"] = "0" * 64
            return result

        env.index.read_documents = altered
    response, _, _ = execute(env, store, bookmark)
    assert response["errorCategory"] == category and not response["complete"]
    assert "documents" not in response and OPENING not in json.dumps(response)
    assert "read" not in response.get("recovery", {})
    assert store.checkpoint()["payload"]["entries"] == saved


async def test_unsuccessful_delivery_outline_and_budget_refusal_do_not_create_saved_source():
    env = await ready()
    store = owner(env)
    selection = child_named(env, None, "Operating procedure")["readSelector"]
    with pytest.raises(RuntimeError, match="did not complete"):
        source_turn(env, store, selection, fail_delivery=True)
    assert store.catalogue([env.lib]) == []
    _, _, outline = source_turn(env, store, {"mode": "outline", "documents": [env.pin]}, allow_grounding_retry=True)
    assert "sourceBookmark" not in outline["documents"][0] and not store.catalogue([env.lib])
    _, _, refusal = source_turn(env, store, {**selection, "maxTokens": 1}, allow_grounding_retry=True)
    assert refusal["disposition"] == "budget_exceeded" and not store.catalogue([env.lib])


@pytest.mark.parametrize("duplicate", [False, True])
async def test_pending_capacity_and_identity_are_checked_before_tokens_are_advertised(duplicate):
    env = await ready()
    store = owner(env, max_entries=1)
    session, tools, owners = registry(env, store)
    first = child_named(env, None, "Operating procedure")["readSelector"]
    other = hierarchy(env)[1]["readSelector"]
    messages = []
    for i, request in enumerate([first, first if duplicate else other]):
        call_id = str(i)
        session.record_request(call_id, "read", env.lib, request, request)
        messages += [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": call_id, "function": {"name": schema(tools)["name"], "arguments": json.dumps(request)}}
                ],
            },
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(read(env, request))},
        ]
    wire = session.render(messages, owners)
    a, b = [json.loads(m["content"]) for m in wire if m["role"] == "tool"]
    assert not store.catalogue([env.lib])
    assert a["documents"][0]["sourceBookmark"]
    if duplicate:
        assert b["documents"][0]["sourceBookmark"]["read"] == a["documents"][0]["sourceBookmark"]["read"]
    else:
        assert b["sourceBookmarkUnavailable"] == "conversation_bookmark_limit_reached"
        assert "sourceBookmark" not in b["documents"][0] and b["documents"][0]["sections"]
    session.delivered(wire)
    assert len(store.checkpoint()["payload"]["entries"]) == 1
    assert session.render(messages, owners) == wire


@pytest.mark.parametrize("conflict", ["mode", "documents"])
async def test_known_malformed_bookmark_has_executable_same_scope_recovery_without_auto_read(conflict):
    env = await ready()
    store = owner(env)
    _, _, first = source_turn(env, store, child_named(env, None, "Operating procedure")["readSelector"])
    bookmark = first["documents"][0]["sourceBookmark"]["read"]
    arguments = {**bookmark, "maxTokens": 12000, conflict: "full" if conflict == "mode" else [env.pin]}
    original = env.index.read_documents
    called = []

    def counted(*args, **kwargs):
        called.append(kwargs)
        return original(*args, **kwargs)

    env.index.read_documents = counted
    response, _, tools = execute(env, store, arguments)
    assert not Draft202012Validator(schema(tools)["parameters"]).is_valid(arguments)
    assert not called and response["errorCategory"] == "source_bookmark_unavailable"
    example = response["recovery"]["read"]
    assert example["arguments"] == {**bookmark, "maxTokens": 12000}
    assert example["scope"]["mode"] == "selected" and "not applied" in example["note"]
    Draft202012Validator(schema(tools)["parameters"]).validate(example["arguments"])
    source, _, _ = execute(env, store, example["arguments"])
    assert len(called) == 1 and OPENING in json.dumps(source) and source["selectionComplete"]


async def test_multi_document_source_stays_complete_without_an_ambiguous_bookmark():
    env = await ready()
    second_id = upload(env.client, env.lib, source_docx(), "second.docx")
    await drain(env.s, env.worker)
    second = await env.s.documents.get(env.lib, second_id)
    pin = {
        "documentId": second_id,
        "generation": second["activeGeneration"],
        "contentRevision": second["contentRevision"],
    }
    store = owner(env)
    _, _, payload = source_turn(env, store, {"mode": "full", "documents": [env.pin, pin]})
    assert payload["selectionComplete"] and payload["documentComplete"]
    assert len(payload["documents"]) == 2 and all(d["sections"] for d in payload["documents"])
    assert all("sourceBookmark" not in d for d in payload["documents"])
    assert payload["sourceBookmarkUnavailable"] == "multi_document_bookmarks_unavailable"
    assert not store.catalogue([env.lib])


async def test_saved_scope_catalogue_routes_each_library_to_its_named_read_tool_and_filters_detached():
    env = await ready()
    second_lib = env.client.post(
        "/v1/libraries", json={"kind": "shared", "name": "Other", "ingestionProfile": "custom-granular-v1"}
    ).json()["id"]
    second_id = upload(env.client, second_lib, source_docx(), "other.docx")
    await drain(env.s, env.worker)
    second = await env.s.documents.get(second_lib, second_id)
    pin = {
        "documentId": second_id,
        "generation": second["activeGeneration"],
        "contentRevision": second["contentRevision"],
    }
    store = owner(env)
    source_turn(env, store, {"mode": "full", "documents": [env.pin]})
    request = {"mode": "full", "documents": [pin]}
    payload = env.index.read_documents(second_lib, "reader", **request)["evidence"]
    entry, reason = store.prepare(second_lib, request, payload)
    assert reason is None
    store.commit([entry])
    tools, owners = build_tools(env.index, [env.lib, second_lib], "reader")
    session = NavigationSession(
        "reader", [env.lib, second_lib], target_of(env.index), bookmark_store=store, conversation_id="conversation-one"
    )
    session.tools(tools, owners)
    hints = session.bookmark_hints()
    catalog = store.catalogue(
        [env.lib, second_lib], read_tools={lib: names["read"] for lib, names in session.tool_names.items()}
    )
    assert len(catalog) == 2 and len({item["tool"] for item in catalog}) == 2
    for item in catalog:
        assert item["tool"] in hints and item["fileName"] in hints
    filtered = store.catalogue([env.lib])
    assert len(filtered) == 1 and filtered[0]["fileName"] == "procedure.docx"
    with pytest.raises(SourceBookmarkError):
        store.resolve(
            entry["token"],
            conversation_id="conversation-one",
            actor_id="reader",
            target=target_of(env.index),
            library_id=env.lib,
            library_ids=[env.lib, second_lib],
        )
