"""Regression cases from the isolated large-chunk and metadata-only replays."""

import json

import pytest

from fli.chunking import default_tokenizer
from fli_demo.agent import Reply, ToolCall, _run_search, grounding_state, run_turn, source_documents
from fli_demo.client import IndexError_
from tests.test_demo import scripted, tool

AGENT = {"id": "owner", "libraryIds": ["bound"]}


def envelope(evidence, charge=1):
    return {
        "contract": "fli.compact.v1",
        "evidence": {"library": {"name": "Reports"}, **evidence},
        "attachments": [],
        "metadata": {"usage": {"requestCharge": charge}, "budget": {"requiredTokens": 100}},
    }


class RecoveryIndex:
    supports_search_options = True

    def __init__(self):
        self.requests = []

    def tools(self, *args):
        return [
            tool("read_documents_library_reports"),
            tool("search_library_reports"),
            tool("find_documents_library_reports"),
        ]

    def read_documents(self, library, agent, **args):
        self.requests.append(("read", library, agent, args))
        return envelope(
            {
                "mode": args.get("mode", "full"),
                "disposition": "budget_exceeded",
                "complete": False,
                "documents": [
                    {
                        "documentId": d["documentId"],
                        "fileName": d["documentId"] + ".docx",
                        "generation": 3,
                        "contentRevision": 4,
                        "documentTokens": 20000,
                        "description": "A generated description must not become evidence.",
                    }
                    for d in args["documents"]
                ],
            }
        )

    def search(self, library, agent, query, **args):
        self.requests.append(("search", library, agent, args))
        if args["top"] > 1:
            return envelope({"disposition": "budget_exceeded", "results": [], "coverage": {"resultCount": 4}}, 2)
        scope = args.get("document") or {"documentId": "d"}
        return envelope(
            {
                "disposition": "results",
                "results": [
                    {
                        **scope,
                        "fileName": scope["documentId"] + ".docx",
                        "location": {},
                        "text": "Original supporting passage.",
                        "textSource": "extracted_source_text",
                    }
                ],
            },
            3,
        )

    def discover_documents(self, *args, **kwargs):
        return envelope(
            {
                "mode": "catalogue",
                "documents": [
                    {"documentId": "d", "fileName": "d.docx", "description": "A generated claim of a billion saved."}
                ],
            }
        )


def test_search_budget_refusal_reduces_whole_passage_count_and_accounts_for_every_request():
    index, traces = RecoveryIndex(), []
    call = ToolCall(
        "s",
        "search_library_reports",
        json.dumps({"query": "amount", "document": {"documentId": "d"}, "authors": ["A"]}),
    )
    content, _ = _run_search(call, index, AGENT, {call.name: "bound"}, {}, traces, lambda: 0, 2000, default_tokenizer())
    assert [r[3]["top"] for r in index.requests] == [8, 4, 2, 1]
    assert all(r[1:3] == ("bound", "owner") for r in index.requests)
    assert all(
        r[3]["document"] == {"documentId": "d"} and r[3]["filters"] == {"authors": ["A"]} for r in index.requests
    )
    assert all(not r[3]["include_neighbors"] for r in index.requests[1:])
    assert sum(t.request_charge for t in traces) == 9
    assert json.loads(content)["results"][0]["text"] == "Original supporting passage."


def test_oversized_whole_file_batch_recovers_both_scoped_sources_without_claiming_complete_reads():
    index = RecoveryIndex()
    chat = scripted(
        Reply(
            None,
            [
                ToolCall(
                    "r",
                    "read_documents_library_reports",
                    json.dumps({"mode": "full", "documents": [{"documentId": "a"}, {"documentId": "b"}]}),
                )
            ],
        ),
        Reply("Supported partial comparison."),
    )
    turn = run_turn(chat, index, AGENT, [], "Compare both files.", context_token_budget=24000)
    reply = json.loads(next(m["content"] for m in turn.messages if m["role"] == "tool"))
    assert not reply["complete"] and not reply["documentComplete"] and not reply["selectionComplete"]
    assert {r["documentId"] for r in reply["results"]} == {"a", "b"}
    scopes = [r[3]["document"] for r in index.requests if r[0] == "search"]
    assert all(s["generation"] == 3 and s["contentRevision"] == 4 for s in scopes)
    assert len(turn.searches) == 5 and sum(t.request_charge for t in turn.searches) == 11
    assert "generated description" not in json.dumps(reply)
    assert "whole-file coverage" in reply["nextAction"]


def test_metadata_only_substantive_answer_is_blocked_even_at_terminal_round():
    chat = scripted(Reply(None, [ToolCall("f", "find_documents_library_reports", "{}")]), Reply("A billion saved."))
    turn = run_turn(chat, RecoveryIndex(), AGENT, [], "Explain the savings.", max_rounds=1)
    assert "A billion saved" not in turn.answer
    assert "did not receive" in turn.answer


@pytest.mark.parametrize(
    "question",
    [
        "List the files.",
        "Please list the files.",
        "Can you list the files?",
        "Could you please list all attached documents?",
        "Show me the available documents.",
        "Please show the files.",
    ],
)
def test_catalogue_question_can_be_answered_from_metadata_without_body_recovery(question):
    chat = scripted(Reply(None, [ToolCall("f", "find_documents_library_reports", "{}")]), Reply("d.docx"))
    turn = run_turn(chat, RecoveryIndex(), AGENT, [], question)
    assert turn.answer == "d.docx" and len(turn.model_calls) == 2


@pytest.mark.parametrize(
    "question",
    [
        "Please list findings in the documents.",
        "Which documents explain why costs grow?",
        "Can you list the files and summarize their conclusions?",
    ],
)
def test_polite_or_inventory_wording_does_not_exempt_substantive_claims(question):
    chat = scripted(Reply(None, [ToolCall("f", "find_documents_library_reports", "{}")]), Reply("A billion saved."))
    turn = run_turn(chat, RecoveryIndex(), AGENT, [], question, max_rounds=1)
    assert "A billion saved" not in turn.answer
    assert "did not receive" in turn.answer


def test_catalogue_question_with_explicit_summary_exclusion_remains_metadata_only():
    chat = scripted(Reply(None, [ToolCall("f", "find_documents_library_reports", "{}")]), Reply("d.docx is readable."))
    turn = run_turn(
        chat,
        RecoveryIndex(),
        AGENT,
        [],
        "List every file tagged corpus=matched-docx in this library and say whether each is readable. "
        "Do not summarize their contents.",
    )
    assert turn.answer == "d.docx is readable." and len(turn.model_calls) == 2


@pytest.mark.parametrize("document", ["", {}, [], {"documentId": " "}])
def test_invalid_explicit_document_scope_is_never_dropped(document):
    index, traces = RecoveryIndex(), []
    call = ToolCall("s", "search_library_reports", json.dumps({"query": "amount", "document": document}))
    content, _ = _run_search(call, index, AGENT, {call.name: "bound"}, {}, traces, lambda: 0)
    assert not index.requests
    assert "nonblank documentId" in json.loads(content)["error"]
    assert len(traces) == 1 and traces[0].request_charge is None


def test_scalar_document_scope_repair_keeps_library_owner_and_filters():
    index, traces = RecoveryIndex(), []
    call = ToolCall("s", "search_library_reports", json.dumps({"query": "amount", "document": "d", "authors": ["A"]}))
    _run_search(call, index, AGENT, {call.name: "bound"}, {}, traces, lambda: 0, 2000, default_tokenizer())
    assert all(r[1:3] == ("bound", "owner") for r in index.requests)
    assert all(r[3]["document"] == {"documentId": "d"} for r in index.requests)
    assert all(r[3]["filters"] == {"authors": ["A"]} for r in index.requests)


def test_metadata_and_generated_descriptions_do_not_count_but_source_neighbors_do():
    row = {"documentId": "a", "text": "Generated interpretation", "textSource": "generated_image_description"}
    assert source_documents({"results": [row]}) == set()
    for mode in ("catalogue", "semantic_candidates", "outline"):
        assert source_documents({"mode": mode, "results": [{"documentId": "a", "text": "Metadata"}]}) == set()
    assert (
        source_documents(
            {"disposition": "budget_exceeded", "documents": [{"documentId": "a", "sections": [{"text": "Refused"}]}]}
        )
        == set()
    )
    assert source_documents({"results": [{**row, "neighbors": [{"text": "Original source next to the figure."}]}]}) == {
        "a"
    }
    assert source_documents(
        {
            "mode": "outline",
            "disposition": "budget_exceeded",
            "automaticRecovery": "document_scoped_passages",
            "results": [{"documentId": "a", "text": "Delivered recovered source."}],
        }
    ) == {"a"}


def test_both_file_answer_needs_source_evidence_from_each_identified_file():
    metadata = {"documents": [{"documentId": d, "description": "Generated"} for d in ("a", "b")]}
    one_source = {"results": [{"documentId": "a", "text": "Original evidence"}]}
    messages = [{"role": "tool", "content": json.dumps(p)} for p in (metadata, one_source)]
    assert grounding_state("Compare both files.", messages) == (True, {"a"}, {"b"})
    assert grounding_state("Compare both options in source-1.docx.", messages) == (True, {"a"}, set())
    assert grounding_state("Explain each recommendation in source-1.docx.", messages) == (True, {"a"}, set())
    messages.append(
        {"role": "tool", "content": json.dumps({"results": [{"documentId": "b", "text": "Second source"}]})}
    )
    assert grounding_state("Compare both files.", messages) == (True, {"a", "b"}, set())


def test_inspected_native_image_counts_as_source_but_candidate_and_generated_text_do_not():
    payload = {
        "documents": [{"documentId": "d", "description": "Generated"}],
        "results": [{"documentId": "d", "imageId": "i", "contentKind": "image", "text": "Generated chart"}],
        "nativeImageDelivery": {"displayAvailable": ["i"], "supplied": []},
    }
    messages = [{"role": "tool", "content": json.dumps(payload)}]
    assert grounding_state("Explain this chart.", messages) == (True, set(), set())
    payload["nativeImageDelivery"]["supplied"] = ["i"]
    messages[0]["content"] = json.dumps(payload)
    assert grounding_state("Explain this chart.", messages) == (True, {"d"}, set())


def test_no_source_limitation_does_not_trigger_repeated_model_calls():
    chat = scripted(
        Reply(
            None, [ToolCall("r", "read_documents_library_reports", '{"documents":[{"documentId":"a"}],"maxTokens":1}')]
        ),
        Reply("The full text exceeds the specified budget; it has not been read."),
    )
    index = RecoveryIndex()
    turn = run_turn(chat, index, AGENT, [], "Read it all.")
    assert len(turn.model_calls) == 2
    assert [r[0] for r in index.requests] == ["read"]
    assert "cannot give a source-grounded answer" in turn.answer


def test_read_recovery_retains_zero_content_revision_on_every_search_attempt():
    class ZeroRevisionIndex(RecoveryIndex):
        def read_documents(self, library, agent, **args):
            response = super().read_documents(library, agent, **args)
            response["evidence"]["documents"][0]["contentRevision"] = 0
            return response

    index = ZeroRevisionIndex()
    chat = scripted(
        Reply(None, [ToolCall("r", "read_documents_library_reports", '{"documents":[{"documentId":"a"}]}')]),
        Reply("Supported partial answer."),
    )
    turn = run_turn(chat, index, AGENT, [], "Explain the evidence.", context_token_budget=24000)
    searches = [r for r in index.requests if r[0] == "search"]
    assert len(searches) == 2
    assert all(r[1:3] == ("bound", "owner") for r in searches)
    assert all(r[3]["document"] == {"documentId": "a", "generation": 3, "contentRevision": 0} for r in searches)
    reply = json.loads(next(m["content"] for m in turn.messages if m["role"] == "tool"))
    assert reply["results"][0]["contentRevision"] == 0 and not reply["complete"]


@pytest.mark.parametrize("status,code", [(403, "library_not_bound_to_agent"), (409, "document_changed")])
def test_denied_or_stale_read_does_not_trigger_automatic_search(status, code):
    class RefusingIndex(RecoveryIndex):
        def read_documents(self, library, agent, **args):
            self.requests.append(("read", library, agent, args))
            raise IndexError_(status, code, "Read refused")

    index = RefusingIndex()
    chat = scripted(
        Reply(None, [ToolCall("r", "read_documents_library_reports", '{"documents":[{"documentId":"a"}]}')]),
        Reply("I could not read the requested source."),
    )
    turn = run_turn(chat, index, AGENT, [], "Explain the evidence.", max_rounds=1)
    assert [r[:3] for r in index.requests] == [("read", "bound", "owner")]
    assert len(turn.searches) == 1 and turn.searches[0].request_charge is None
    assert f"{status} {code}" in turn.searches[0].error
    reply = json.loads(next(m["content"] for m in turn.messages if m["role"] == "tool"))
    assert not reply["complete"] and "automaticRecovery" not in reply and not reply.get("results")


def test_terminal_synthesis_is_skipped_when_reported_prompt_leaves_insufficient_reserve():
    # Provider-reported use can exceed the local estimate. Tool protocol overhead
    # after this reply consumes the remaining answer reserve even without source text.
    chat = scripted(
        Reply(
            None,
            [ToolCall("r", "read_documents_library_reports", '{"documents":[{"documentId":"a"}]}')],
            usage={"prompt_tokens": 16000},
        )
    )
    index = RecoveryIndex()
    turn = run_turn(chat, index, AGENT, [], "Explain the evidence.", max_rounds=1, context_token_budget=24000)
    assert len(chat.seen) == 1 and chat.seen[0][1]
    assert len(turn.model_calls) == 1 and turn.model_calls[0]["purpose"] == "tool_request"
    assert not index.requests
    assert "insufficient to synthesize" in turn.answer
