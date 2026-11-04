"""Enclosing-message admission; synthetic fixtures are not answer-quality evidence."""

import copy
import json

import pytest

from fli.chunking import default_tokenizer
from fli.reference_format import model_messages
from fli_demo import agent as a
from fli_demo.context_budget import OUTPUT_RESERVE, SYNTHESIS_PROMPT, EnvelopeContext, refusal_message
from tests.test_demo import FakeIndex, scripted, tool


def budget(*, allowance=64000, images=8192, project=lambda rows: rows, tools=None):
    return EnvelopeContext(default_tokenizer(), tools or [], allowance, images, project)


def test_serialized_envelope_counts_escaped_content_not_only_payload():
    payload = json.dumps({"text": '条件 "quoted"\n' * 2500}, ensure_ascii=False, separators=(",", ":"))
    message = {"role": "tool", "tool_call_id": "call", "content": payload}
    meter = budget()
    body = len(meter.tokenizer.encode(payload))
    whole = meter.local_size([message])
    assert whole > body + 8000  # Unicode/quotes are escaped again in the enclosing representation.
    meter.allowance = whole + OUTPUT_RESERVE - 1
    assert body + OUTPUT_RESERVE < meter.allowance
    assert not meter.fits([message])
    meter.allowance += 1
    assert meter.fits([message])


def test_reference_projection_is_priced_in_the_same_representation_as_the_guard():
    messages = [
        {"role": "tool", "tool_call_id": "c", "content": json.dumps({"results": [{"text": "Exact source words."}]})}
    ]
    meter = budget(project=model_messages, tools=[tool("search_library_bound")])
    expected = len(meter.tokenizer.encode(json.dumps({"messages": model_messages(messages), "tools": meter.tools})))
    assert meter.local_size(messages) == expected


def test_actual_input_supersedes_prior_estimates_without_mutating_meters():
    meter = budget(images=4000)
    first = [{"role": "user", "content": "q"}]
    usage = {"prompt_tokens": 7000, "completion_tokens": 500}
    original = copy.deepcopy(usage)
    meter.observe(meter.local_size(first), usage, 300)
    assert meter.size(first) == 7300
    # With no later input observation, prior opaque reasoning still needs reservation.
    meter.observe(meter.local_size(first), {}, 200)
    assert meter.size(first) == 7500
    visual = first + [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x.invalid"}}]}]
    assert meter.size(visual) >= 11500
    meter.observe(meter.local_size(visual), {"prompt_tokens": 9000}, 100)
    assert meter.size(visual) == 9100  # Image and older reasoning already occur in measured input.
    assert meter.size(first) == 9100  # Removal does not subtract from a provider observation.
    assert usage == original


@pytest.mark.parametrize("prompt", [-1, 0, True, 1.5, "100"])
def test_invalid_provider_input_observation_is_rejected(prompt):
    with pytest.raises(ValueError, match="input-token"):
        budget().observe(20, {"prompt_tokens": prompt}, 0)


@pytest.mark.parametrize("carry", [-1, True, 1.5])
def test_invalid_reasoning_reservation_is_rejected(carry):
    with pytest.raises(ValueError, match="accounting observation"):
        budget().observe(20, {}, carry)


class ReadIndex:
    def tools(self, *args):
        return [tool("read_documents_library_bound")]


AGENT = {"id": "owner", "libraryIds": ["bound"]}


def stub_reads(monkeypatch, bodies, images=None):
    fetched = []

    def read(call, index, agent, owners, names, searches, clock, remaining):
        fetched.append((call.id, remaining))
        payload = {
            "mode": "selected",
            "disposition": "results",
            "complete": True,
            "documents": [
                {
                    "documentId": call.id,
                    "fileName": "source.docx",
                    "generation": 1,
                    "contentRevision": 1,
                    "complete": False,
                    "selectionComplete": True,
                    "sections": [{"sectionId": "s", "text": bodies[call.id]}],
                }
            ],
        }
        searches.append(a.SearchTrace(call.name, "bound", "Bound", "", 1, 5, 1, "results", [], operation="read"))
        return json.dumps(payload), images or []

    monkeypatch.setattr(a, "_run_documents", read)
    return fetched


def checking_chat(calls, *, first_input=24000):
    seen, checker = [], None

    def chat(messages, tools):
        nonlocal checker
        if checker is None:
            checker = budget(tools=tools)
        assert checker.fits(messages), "an admitted transcript overflowed the pre-call guard"
        seen.append(copy.deepcopy(messages))
        if len(seen) == 1:
            usage = {"prompt_tokens": first_input, "completion_tokens": 200}
            checker.observe(checker.local_size(messages), usage, 0)
            return a.Reply(None, calls, usage)
        return a.Reply("Answer from the received sources.", usage={"prompt_tokens": 1000})

    chat.seen = seen
    return chat


@pytest.mark.parametrize("view", ["compact", "references"])
def test_full_candidate_refusal_preserves_other_batched_reply_and_usage(monkeypatch, view):
    bodies = {"large": '条件 "quoted"\n' * 2500, "small": "A small original source fact."}
    stub_reads(monkeypatch, bodies)
    calls = [a.ToolCall(key, "read_documents_library_bound", "{}") for key in bodies]
    chat = checking_chat(calls)
    ledger = []
    index = ReadIndex()
    index.evidence_view = view
    result = a.run_turn(chat, index, AGENT, [], "q", context_accounting="envelope-v2", evidence_ledger=ledger)
    assert len(chat.seen) == 2
    replies = [json.loads(m["content"]) for m in result.messages if m["role"] == "tool"]
    assert replies[0] == json.loads(refusal_message("large")["content"])
    assert replies[1]["documents"][0]["sections"][0]["text"] == bodies["small"]
    assert {entry["documentId"] for entry in ledger} == {"small"}
    assert result.searches[0].result_count == 0 and result.searches[0].complete is False
    assert sum(trace.request_charge for trace in result.searches) == 10
    assert result.model_calls[0]["usage"] == {"prompt_tokens": 24000, "completion_tokens": 200}


@pytest.mark.parametrize("view", ["compact", "references"])
def test_native_images_and_duplicate_delivery_are_priced_with_their_final_metadata(monkeypatch, view):
    image = {"imageId": "image", "caption": "source", "url": "https://x.invalid/image"}
    stub_reads(monkeypatch, {"one": "First source.", "two": "Second source."}, [image])
    index = ReadIndex()
    index.evidence_view = view
    seen = []
    token = default_tokenizer()
    prior_local = prior_input = 0

    def chat(messages, tools):
        nonlocal prior_local, prior_input
        current = len(token.encode(json.dumps({"messages": messages, "tools": tools}))) + sum(
            8192
            for m in messages
            if isinstance(m.get("content"), list)
            for part in m["content"]
            if part.get("type") == "image_url"
        )
        assert (current if not seen else prior_input + max(0, current - prior_local)) + 8000 <= 64000
        seen.append(copy.deepcopy(messages))
        prior_local, prior_input = current, 30000
        if len(seen) <= 2:
            name = "one" if len(seen) == 1 else "two"
            return a.Reply(None, [a.ToolCall(name, "read_documents_library_bound", "{}")], {"prompt_tokens": 30000})
        return a.Reply("Source answer.")

    turn = a.run_turn(chat, index, AGENT, [], "q", context_accounting="envelope-v2")
    replies = [json.loads(m["content"]) for m in turn.messages if m["role"] == "tool"]
    assert replies[0]["nativeImageDelivery"]["supplied"] == ["image"]
    assert replies[1]["nativeImageDelivery"]["alreadySupplied"] == ["image"]
    assert sum(isinstance(m.get("content"), list) for m in seen[-1]) == 1
    assert [t.images_supplied for t in turn.searches] == [1, 0]


def test_image_delivery_metadata_is_inside_candidate_admission(monkeypatch):
    images = [{"imageId": "image-" + str(i) + "x" * 80} for i in range(2000)]
    stub_reads(monkeypatch, {"read": "Short source."}, images)
    chat = checking_chat([a.ToolCall("read", "read_documents_library_bound", "{}")])
    a.run_turn(chat, ReadIndex(), AGENT, [], "q", image_inputs=False, context_accounting="envelope-v2")
    last = json.loads(chat.seen[1][-1]["content"])
    assert last["error"] == "remaining_context_budget_exceeded" and "documents" not in last


@pytest.mark.parametrize("view", ["compact", "references"])
@pytest.mark.parametrize("partial", [False, True])
def test_whole_payload_refusal_keeps_grounding_strength_without_source_credit(monkeypatch, view, partial):
    bodies = {"large": '条件 "quoted"\n' * 2500}
    if partial:
        bodies["small"] = "An original fact from only the small file."
    stub_reads(monkeypatch, bodies)
    index = ReadIndex()
    index.evidence_view = view
    calls = [a.ToolCall(key, "read_documents_library_bound", "{}") for key in bodies]
    replies = [a.Reply(None, calls, {"prompt_tokens": 24000})] + [a.Reply("An unqualified fabricated conclusion.")] * 3
    chat = scripted(*replies)
    ledger = []
    question = "Compare both documents." if partial else "Explain the source conditions."
    result = a.run_turn(chat, index, AGENT, [], question, context_accounting="envelope-v2", evidence_ledger=ledger)
    assert "unqualified fabricated" not in result.answer
    assert ("only part" if partial else "did not receive") in result.answer
    assert {entry["documentId"] for entry in ledger} == ({"small"} if partial else set())
    for messages, _ in chat.seen:
        # Local grounding facts must not become unbudgeted tool replies or source delivery.
        assert all(m.get("tool_call_id") in bodies for m in messages if m["role"] == "tool")


@pytest.mark.parametrize("fresh_reads", [False, True])
def test_resumed_partial_source_history_cannot_claim_a_complete_both_file_comparison(monkeypatch, fresh_reads):
    stub_reads(monkeypatch, {"large": '条件 "quoted"\n' * 2500, "small": "One file's source."})
    calls = [a.ToolCall(key, "read_documents_library_bound", "{}") for key in ("large", "small")]
    first = scripted(
        a.Reply(None, calls, {"prompt_tokens": 24000}),
        *[a.Reply("An unqualified fabricated conclusion.")] * 3,
    )
    turn = a.run_turn(first, ReadIndex(), AGENT, [], "Compare both documents.", context_accounting="envelope-v2")
    stub_reads(monkeypatch, {"large": "A newly returned source.", "small": "The other newly returned source."})
    follow = scripted(
        *([a.Reply(None, calls, {"prompt_tokens": 24000})] if fresh_reads else []),
        a.Reply("Both documents conclusively establish the fabricated claim."),
    )
    resumed = a.run_turn(
        follow, ReadIndex(), AGENT, turn.messages, "Compare both documents.", context_accounting="envelope-v2"
    )
    assert "fabricated claim" not in resumed.answer
    assert "withheld file scope" in resumed.answer and "fresh retrieval session" in resumed.answer
    if fresh_reads:
        assert len(follow.seen) == 2
        fresh = scripted(a.Reply(None, calls), a.Reply("Source-supported comparison."))
        independent = a.run_turn(
            fresh, ReadIndex(), AGENT, [], "Compare both documents.", context_accounting="envelope-v2"
        )
        assert independent.answer == "Source-supported comparison."


def test_image_message_and_question_reminder_are_admitted_atomically(monkeypatch):
    image = {"imageId": "image", "caption": "source", "url": "https://x.invalid/image"}
    stub_reads(monkeypatch, {"read": "Source words."}, [image])
    seen = []

    def chat(messages, tools):
        seen.append(copy.deepcopy(messages))
        return (
            a.Reply(None, [a.ToolCall("read", "read_documents_library_bound", "{}")], {"prompt_tokens": 53000})
            if len(seen) == 1
            else a.Reply("Answer.")
        )

    result = a.run_turn(chat, ReadIndex(), AGENT, [], "q " * 1800, image_tokens=2000, context_accounting="envelope-v2")
    assert len(seen) == 2
    assert not any(isinstance(m.get("content"), list) for m in seen[1])
    delivery = json.loads(seen[1][-1]["content"])["nativeImageDelivery"]
    assert delivery["supplied"] == [] and delivery["withheld"] == ["image"]
    assert result.searches[0].images_withheld == 1


def test_preflight_impossible_reply_fetches_nothing_and_keeps_resumable_protocol(monkeypatch):
    fetched = stub_reads(monkeypatch, {"read": "Must not be fetched."})
    calls = [a.ToolCall("read", "read_documents_library_bound", "{}")]
    first = scripted(a.Reply(None, calls, {"prompt_tokens": 55990}))
    turn = a.run_turn(first, ReadIndex(), AGENT, [], "q", context_accounting="envelope-v2")
    assert fetched == [] and len(first.seen) == 1
    assert turn.messages[-2] == refusal_message("read")
    follow = scripted(a.Reply("I cannot answer without source evidence."))
    a.run_turn(
        follow,
        ReadIndex(),
        AGENT,
        turn.messages,
        "Continue",
        context_token_budget=128000,
        context_accounting="envelope-v2",
    )
    forwarded = follow.seen[0][0]
    assistant = next(i for i, m in enumerate(forwarded) if m.get("tool_calls"))
    assert forwarded[assistant + 1]["tool_call_id"] == "read"


def test_final_round_reserves_terminal_synthesis_message(monkeypatch):
    stub_reads(monkeypatch, {"read": '条件 "quoted"\n' * 2500})
    chat = checking_chat([a.ToolCall("read", "read_documents_library_bound", "{}")])
    a.run_turn(chat, ReadIndex(), AGENT, [], "q", max_rounds=1, context_accounting="envelope-v2")
    assert len(chat.seen) == 2 and chat.seen[-1][-1] == SYNTHESIS_PROMPT


def test_answer_repair_is_not_submitted_when_its_full_envelope_would_overflow():
    first = scripted(a.Reply("Unsupported answer.", usage={"prompt_tokens": 55999}))
    turn = a.run_turn(
        first, FakeIndex(), {"id": "a", "libraryIds": ["lib-a"]}, [], "Show images.", context_accounting="envelope-v2"
    )
    assert len(first.seen) == 1
    assert "insufficient to check" in turn.answer


def test_default_and_explicit_legacy_transcripts_match():
    calls = [a.ToolCall("call", "search_library_finance", '{"query":"q"}')]
    runs = []
    for kwargs in ({}, {"context_accounting": "legacy-v1"}):
        chat = scripted(a.Reply(None, calls), a.Reply("Source answer."))
        result = a.run_turn(chat, FakeIndex(), {"id": "a", "libraryIds": ["lib-a"]}, [], "q", **kwargs)
        runs.append((result.answer, result.messages, chat.seen))
    assert runs[0] == runs[1]


def test_unknown_mode_fails_before_index_access():
    class NoIndex:
        def tools(self, *args):
            raise AssertionError("Index must not be called")

    with pytest.raises(ValueError, match="unknown context accounting"):
        a.run_turn(None, NoIndex(), AGENT, [], "q", context_accounting="future")
