"""P1 preserves source structure and all access/lifecycle boundaries."""

import copy
import json

import pytest

from fli.chunking import default_tokenizer
from fli.presentation import evidence_json
from fli.reference_format import CONTRACT, model_messages, pack, unpack
from fli.selector_hints import add_hints, error_details
from fli_demo.client import IndexError_
from tests.test_reading import ctx as reading_ctx
from tests.test_reading import drain, upload

ctx = reading_ctx


def test_roundtrip_preserves_order_blank_cells_metadata_scope_and_exact_unicode():
    evidence = {
        "mode": "selected",
        "complete": False,
        "documentComplete": False,
        "selectionComplete": True,
        "documents": [
            {
                "documentId": "document",
                "generation": 2,
                "contentRevision": 4,
                "fileName": "Répørt.docx",
                "sections": [
                    {
                        "heading": "Repeated heading " * 5,
                        "text": "A\t\tB\nα  é\u0301",
                        "location": {"paragraph": 8},
                        "citation": "Répørt.docx (paragraph 8)",
                    },
                    {
                        "heading": "Repeated heading " * 5,
                        "text": "<table><tr><td></td><td>value</td></tr></table>",
                        "location": {"paragraph": 9},
                        "citation": "Répørt.docx (paragraph 9)",
                    },
                ],
            }
        ],
        "metadataUnknown": {"$ref": "literal-source-key", "$columns": ["literal"]},
    }
    before = copy.deepcopy(evidence)
    packed = pack(evidence)
    assert packed["contract"] == CONTRACT and packed["dictionary"]
    assert unpack(packed) == evidence == before
    assert isinstance(packed["evidence"]["documents"][0]["sections"], dict)
    # Reconstruction objects cannot mutate a shared dictionary or another row.
    decoded = unpack(packed)
    decoded["documents"][0]["sections"][0]["location"]["paragraph"] = 99
    assert unpack(packed) == before


@pytest.mark.parametrize(
    "bad",
    [
        {"$ref": -1},
        {"$ref": True},
        {"$ref": 0, "text": "ambiguous"},
        {"$columns": ["text", "text"], "$rows": [[1, 2]]},
        {"$columns": ["text"], "$rows": [[1, 2]]},
        {"$literal": []},
    ],
)
def test_malformed_references_fail_closed(bad):
    with pytest.raises(ValueError):
        unpack({"contract": CONTRACT, "dictionary": ["value"], "evidence": bad})


def test_model_transform_changes_only_tool_wire_format_and_is_reversible():
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "assistant", "tool_calls": []},
        {"role": "tool", "tool_call_id": "one", "content": json.dumps({"text": "source"})},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "private"}}]},
    ]
    result = model_messages(messages)
    assert result[:2] == messages[:2] and result[3] == messages[3]
    assert unpack(json.loads(result[2]["content"])) == json.loads(messages[2]["content"])
    assert result[2]["tool_call_id"] == "one"


def post(client, library, operation, body, **params):
    return client.post(f"/v1/libraries/{library}/{operation}", params={"view": "references", **params}, json=body)


async def test_reference_contract_api_equivalence_exact_budget_and_atomic_refusal(ctx):
    client, services, library, worker = ctx
    ids = [
        upload(client, library, name=f"source-{i}.md", text="# One\n\nExact source.\n\n# Two\n\nSecond section.")
        for i in range(2)
    ]
    await drain(services, worker)
    body = {"documents": [{"documentId": d} for d in ids], "maxTokens": 200000}
    base = client.post(f"/v1/libraries/{library}/documents:read", params={"view": "compact"}, json=body).json()
    response = post(client, library, "documents:read", body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["contract"] == "fli.references.v1"
    assert unpack(result["evidence"]) == base["evidence"]
    required = result["metadata"]["budget"]["requiredTokens"]
    assert required == len(default_tokenizer().encode(evidence_json(result["evidence"])))
    assert post(client, library, "documents:read", {**body, "maxTokens": required}).json()["metadata"]["budget"]["fits"]
    refused = post(client, library, "documents:read", {**body, "maxTokens": required - 1}).json()
    evidence = unpack(refused["evidence"])
    assert not evidence["complete"] and evidence["disposition"] == "budget_exceeded"
    assert len(evidence["documents"]) == 2 and not refused["attachments"]
    assert all("sections" not in d and "outline" not in d for d in evidence["documents"])
    assert not unpack(post(client, library, "documents:read", {**body, "includeText": False}).json()["evidence"])[
        "complete"
    ]


async def test_selectors_optin_usable_outline_and_stale_revision_rejected(ctx):
    client, services, library, worker = ctx
    document = upload(client, library, text="# First\n\nExact first.\n\n# Next\n\nExact next.")
    await drain(services, worker)
    body = {"mode": "outline", "documents": [{"documentId": document}]}
    base = client.post(f"/v1/libraries/{library}/documents:read", params={"view": "compact"}, json=body).json()
    assert "selectorGuidance" not in base["evidence"]
    hints = unpack(post(client, library, "documents:read", body, selectors="handles").json()["evidence"])
    selection = hints["documents"][0]["outline"][0]["readSelector"]
    assert selection["documents"][0]["documentId"] == document
    assert "generation" in selection["documents"][0] and "contentRevision" in selection["documents"][0]
    read = unpack(post(client, library, "documents:read", selection, selectors="handles").json()["evidence"])
    assert read["selectionComplete"] and not read["documentComplete"]
    assert read["documents"][0]["sections"][0]["text"] == "Exact first."
    record = await services.documents.get(library, document)
    record["contentRevision"] += 1
    await services.documents.replace(record)
    stale = post(client, library, "documents:read", selection, selectors="handles")
    assert stale.status_code == 409
    assert "evidence" not in stale.json()
    invalid = post(client, library, "search", {"query": "first", "document": document}, selectors="handles")
    assert invalid.status_code == 400 and "evidence" not in invalid.json()


async def test_reference_and_selector_views_keep_personal_and_deletion_fences(ctx):
    client, services, library, worker = ctx
    document = upload(client, library)
    await drain(services, worker)
    record = await services.libraries.get(library)
    record["kind"], record["boundAgentId"] = "personal", "some-other-agent"
    await services.libraries.replace(record)
    for operation, body in [
        ("search", {"query": "test"}),
        ("documents:discover", {}),
        ("documents:read", {"documents": [{"documentId": document}]}),
    ]:
        denied = post(client, library, operation, body, selectors="handles")
        assert denied.status_code == 403 and "evidence" not in denied.json()
    record = await services.libraries.get(library)
    record["kind"] = "shared"
    record.pop("boundAgentId")
    await services.libraries.replace(record)
    client.delete(f"/v1/libraries/{library}/documents/{document}")
    denied = post(client, library, "documents:read", {"documents": [{"documentId": document}]}, selectors="handles")
    assert denied.status_code == 404 and "evidence" not in denied.json()


def test_empty_provider_source_scope_advertises_outline_not_false_expansion():
    evidence = {
        "results": [
            {
                "documentId": "doc",
                "generation": 1,
                "contentRevision": 1,
                "evidenceId": "ev1.snapshot.chunk.0",
                "sourceScope": {},
                "representation": "extracted_sections",
            }
        ]
    }
    result = add_hints(evidence)["results"][0]
    assert result["readCapabilities"]["evidenceExpansion"] == "outline_required"
    assert "readSelector" not in result
    assert result["outlineSelector"]["documents"] == [result["documentSelector"]]
    for code in ("source_scope_unavailable", "evidence_changed", "invalid_request"):
        out = error_details(IndexError_(409, code, "private-invalid-selector"))
        assert out["errorCategory"] == code and "private-invalid-selector" not in json.dumps(out)


def test_agent_sends_reference_wire_but_retains_canonical_source_ledger():
    from fli_demo.agent import Reply, ToolCall, run_turn
    from tests.test_demo import scripted
    from tests.test_retrieval_followups import EvidenceIndex

    index = EvidenceIndex()
    index.evidence_view = "references"
    chat = scripted(
        Reply(None, [ToolCall("read", "read_documents_library_evidence", '{"documents":[{"documentId":"d"}]}')]),
        Reply("The evidence is documented in evidence.pdf, page 1."),
    )
    result = run_turn(
        chat,
        index,
        {"id": "agent", "libraryIds": ["authorized"]},
        [],
        "Read evidence.pdf and report its finding.",
        context_token_budget=200000,
        image_inputs=False,
    )
    canonical = [json.loads(m["content"]) for m in result.messages if m["role"] == "tool"]
    wire = [json.loads(m["content"]) for m in chat.seen[-1][0] if m["role"] == "tool"]
    assert canonical and wire and unpack(wire[0]) == canonical[0]
    assert canonical[0]["documents"][0]["sections"][0]["text"] == "Evidence. " * 4000
    assert wire[0]["contract"] == CONTRACT


def test_selector_adapter_rejects_bare_id_without_unpinning_or_calling_search():
    import time

    from fli_demo.agent import ToolCall, _run_search

    class Index:
        selector_mode = "handles"

        def search(self, *args, **kwargs):
            pytest.fail("invalid selector must not become an unpinned search")

    traces = []
    call = ToolCall("id", "search_library_bound", '{"query":"question","document":"returned-but-unpinned"}')
    content, _ = _run_search(call, Index(), {"id": "agent"}, {call.name: "bound"}, {}, traces, time.perf_counter)
    result = json.loads(content)
    assert result["errorCategory"] == "invalid_selector" and not result["complete"]
    assert "returned-but-unpinned" not in content


async def test_raw_selector_hints_count_toward_atomic_read_budget(ctx):
    client, services, library, worker = ctx
    document = upload(client, library)
    await drain(services, worker)
    endpoint = f"/v1/libraries/{library}/documents:read"
    body = {"documents": [{"documentId": document}], "maxTokens": 200000}
    raw = client.post(endpoint, json=body).json()
    raw_limit = raw["estimatedTokens"] + 1
    hints = client.post(endpoint, params={"selectors": "handles"}, json=body).json()
    assert hints["complete"] and hints["estimatedTokens"] > raw_limit
    assert len(default_tokenizer().encode(json.dumps(hints, ensure_ascii=False))) <= hints["estimatedTokens"]
    refused = client.post(endpoint, params={"selectors": "handles"}, json={**body, "maxTokens": raw_limit}).json()
    assert refused["disposition"] == "budget_exceeded" and not refused["complete"]
    assert refused["estimatedTokens"] > raw_limit
    assert all("sections" not in d and "outline" not in d and "candidateImages" not in d for d in refused["documents"])
    # The stored-section shortcut remains a refusal, with usable pinned outline hints.
    early = client.post(endpoint, params={"selectors": "handles"}, json={**body, "maxTokens": 1}).json()
    assert not early["complete"] and "outlineSelector" in early["documents"][0]
    assert "sections" not in early["documents"][0]
