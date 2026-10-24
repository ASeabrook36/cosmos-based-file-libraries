"""Proposals through the worker and the API, with a fake chat model (spec §6.2, §7.2, §9.1)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from fli.chunking import ApproxTokenizer, Chunker
from fli.proposals import ChatError
from fli.worker.handlers import Worker
from tests.fakes import FakeChat, FakeEmbedder, api
from tests.test_worker_handlers import TEXT, doc, drain, replace, upload

ANSWER = {
    "tags": [{"key": "region", "value": "EMEA"}, {"key": "topic", "value": "margin"}],
    "period": {"start": "2025-10-01", "end": "2025-12-31"},
    "author": "Jane Doe",
}
DESCRIPTION = {"description": "Quarterly finance reports for EMEA, 2025. Not for HR questions."}


def setup(*answers, kind="shared", proposer=True):
    client, s = api()
    body = {"kind": kind, "name": "Finance"} | ({"boundAgentId": "agent-1"} if kind == "personal" else {})
    lib = client.post("/v1/libraries", json=body).json()["id"]
    chat = FakeChat(*answers) if proposer else None
    worker = Worker(
        s.libraries, s.documents, s.chunks, s.blobs, s.queue, FakeEmbedder(), Chunker(ApproxTokenizer()), proposer=chat
    )
    worker.stale_read_pause = 0
    return client, s, lib, worker, chat


def chunks_of(s, doc_id):
    return [c for (_, d, _), c in s.chunks.items.items() if d == doc_id]


def patch(client, lib, doc_id, **body):
    return client.patch(f"/v1/libraries/{lib}/documents/{doc_id}", json=body)


# -- ingestion ------------------------------------------------------------------------------


async def test_a_shared_library_keeps_proposals_waiting_and_chunks_unchanged():
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION)
    doc_id = upload(client, lib)
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert d["status"] == "ready"
    assert d["tagProposals"] == [{"key": "region", "value": "EMEA"}, {"key": "topic", "value": "margin"}]
    assert d["periodProposal"] == {"start": "2025-10-01", "end": "2025-12-31"}
    assert d["authorProposal"] == "Jane Doe"
    assert d["tags"] == [] and d["periodStart"] is None and d["author"] is None
    assert all(c["facets"] == [] and c["periodStart"] is None for c in chunks_of(s, doc_id))
    assert TEXT[:40] in chat.calls[0][1]["content"]  # the opening text went to the model


async def test_a_personal_library_accepts_proposals_at_once_into_the_record_and_the_chunks():
    client, s, lib, worker, _ = setup(ANSWER, DESCRIPTION, kind="personal")
    doc_id = upload(client, lib, metadata={"tags": [{"key": "region", "value": "EMEA"}]})
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert d["tags"] == [
        {"key": "region", "value": "EMEA", "source": "user"},
        {"key": "topic", "value": "margin", "source": "accepted-proposal"},
    ]
    assert (d["periodStart"], d["periodEnd"], d["author"]) == ("2025-10-01", "2025-12-31", "Jane Doe")
    assert d["tagProposals"] == [] and d["periodProposal"] is None and d["authorProposal"] is None
    for c in chunks_of(s, doc_id):  # written with the accepted metadata: no retag needed
        assert c["facets"] == ["region=EMEA", "topic=margin"]
        assert (c["periodStart"], c["author"]) == ("2025-10-01", "Jane Doe")
    assert not any(m["type"] == "retag" for _, m in s.queue.sent)
    lib_record = client.get(f"/v1/libraries/{lib}").json()
    assert lib_record["tagVocabulary"] == {"region": {"EMEA": 1}, "topic": {"margin": 1}}


async def test_auto_accept_never_replaces_what_the_uploader_set():
    client, s, lib, worker, _ = setup(ANSWER, DESCRIPTION, kind="personal")
    meta = {"author": "Sam Lee", "periodStart": "2024-01-01", "periodEnd": "2024-12-31"}
    doc_id = upload(client, lib, metadata=meta)
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert (d["author"], d["periodStart"]) == ("Sam Lee", "2024-01-01")
    assert d["authorProposal"] == "Jane Doe" and d["periodProposal"]["start"] == "2025-10-01"
    assert all(c["author"] == "Sam Lee" for c in chunks_of(s, doc_id))


async def test_a_patch_made_during_an_auto_accepting_ingest_is_kept_and_its_retag_follows(monkeypatch):
    client, s, lib, worker, _ = setup(ANSWER, DESCRIPTION, kind="personal")
    doc_id = upload(client, lib)
    real = s.chunks.upsert

    async def patch_midway(chunk):
        await real(chunk)
        if chunk["chunkIndex"] == 0:
            r = patch(client, lib, doc_id, author="Sam Lee", tags=[{"key": "type", "value": "memo"}])
            assert r.status_code == 200

    monkeypatch.setattr(s.chunks, "upsert", patch_midway)
    _, message = s.queue.sent.pop(0)
    await worker.handle(message)
    monkeypatch.setattr(s.chunks, "upsert", real)
    await drain(s, worker)  # the PATCH's retag, after the ingest in session order
    d = doc(client, lib, doc_id)
    assert d["author"] == "Sam Lee" and d["authorProposal"] == "Jane Doe"
    assert d["facets"] == ["region=EMEA", "topic=margin", "type=memo"]
    for c in chunks_of(s, doc_id):
        assert c["author"] == "Sam Lee" and c["facets"] == d["facets"]


async def test_the_prompt_offers_the_librarys_vocabulary_for_reuse():
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION, kind="personal")
    upload(client, lib)
    await drain(s, worker)
    upload(client, lib, name="q3.txt")
    await drain(s, worker)
    second = [m for m in chat.calls if "Opening text" in m[1]["content"]][1][1]["content"]
    assert '"region": ["EMEA"]' in second and '"Jane Doe"' in second


@pytest.mark.parametrize("failure", [ChatError("down"), RuntimeError("bug"), {"tags": "garbage"}])
async def test_a_failed_or_useless_proposal_never_fails_the_document(failure):
    client, s, lib, worker, _ = setup(failure, DESCRIPTION)
    doc_id = upload(client, lib)
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert d["status"] == "ready" and d["activeGeneration"] == 1
    assert d["tagProposals"] == [] and d["authorProposal"] is None


async def test_without_a_chat_deployment_there_are_no_proposals_and_no_description_messages():
    client, s, lib, worker, _ = setup(proposer=False)
    doc_id = upload(client, lib)
    await drain(s, worker)
    assert doc(client, lib, doc_id)["tagProposals"] == []
    s.queue.sent.clear()
    await worker.refresh_library(lib)
    assert s.queue.sent == []


async def test_a_replacement_proposes_again_and_keeps_accepted_tags():
    second = {"tags": [{"key": "topic", "value": "capex"}], "period": None, "author": None}
    client, s, lib, worker, _ = setup(ANSWER, DESCRIPTION, second, DESCRIPTION)
    doc_id = upload(client, lib)
    await drain(s, worker)
    assert patch(client, lib, doc_id, acceptProposals=["region=EMEA"]).status_code == 200
    await drain(s, worker)
    replace(client, lib, doc_id)
    await drain(s, worker)
    d = doc(client, lib, doc_id)
    assert d["activeGeneration"] == 2
    assert d["tags"] == [{"key": "region", "value": "EMEA", "source": "accepted-proposal"}]
    assert d["tagProposals"] == [{"key": "topic", "value": "capex"}] and d["authorProposal"] is None
    assert all(c["facets"] == ["region=EMEA"] for c in chunks_of(s, doc_id))


async def test_logs_carry_counts_never_document_or_model_text(caplog):
    marker = "ZXQ-PRIVATE-NOTE"
    answer = {"tags": [{"key": "topic", "value": marker}], "author": marker}
    client, s, lib, worker, _ = setup(answer, {"description": marker})
    caplog.set_level(logging.DEBUG)
    upload(client, lib, name=f"{marker}.txt", data=(f"{marker} " * 400).encode())
    await drain(s, worker)
    await worker.propose_description(lib, requested=True)
    made = [r for r in caplog.records if r.getMessage() == "proposals made"]
    assert made and made[0].tags == 1 and made[0].promptTokens == 100
    assert any(r.getMessage() == "description proposed" for r in caplog.records)
    for record in caplog.records:
        assert marker not in record.getMessage() and marker not in str(vars(record))


# -- accepting and dismissing ---------------------------------------------------------------


async def proposed():
    client, s, lib, worker, _ = setup(ANSWER, DESCRIPTION)
    doc_id = upload(client, lib, metadata={"tags": [{"key": "type", "value": "report"}]})
    await drain(s, worker)
    s.queue.sent.clear()
    return client, s, lib, worker, doc_id


async def test_accepting_moves_proposals_into_the_record_and_retag_reaches_the_chunks():
    client, s, lib, worker, doc_id = await proposed()
    r = patch(client, lib, doc_id, acceptProposals=["topic=margin", "period", "author"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["tags"] == [
        {"key": "type", "value": "report", "source": "user"},
        {"key": "topic", "value": "margin", "source": "accepted-proposal"},
    ]
    assert (d["periodStart"], d["periodEnd"], d["author"]) == ("2025-10-01", "2025-12-31", "Jane Doe")
    assert d["tagProposals"] == [{"key": "region", "value": "EMEA"}]
    assert d["periodProposal"] is None and d["authorProposal"] is None
    assert d["metadataVersion"] == 1
    assert s.queue.sent == [(lib, {"type": "retag", "libraryId": lib, "documentId": doc_id, "metadataVersion": 1})]
    await drain(s, worker)
    for c in chunks_of(s, doc_id):
        assert c["facets"] == ["topic=margin", "type=report"] and c["author"] == "Jane Doe"


async def test_dismissing_alone_drops_proposals_without_a_retag():
    client, s, lib, _, doc_id = await proposed()
    d = patch(client, lib, doc_id, dismissProposals=["region=EMEA", "author"]).json()
    assert d["tagProposals"] == [{"key": "topic", "value": "margin"}] and d["authorProposal"] is None
    assert d["tags"] == [{"key": "type", "value": "report", "source": "user"}]
    assert d["metadataVersion"] == 0 and s.queue.sent == []


async def test_an_edit_is_an_explicit_field_plus_a_dismissal():
    client, _, lib, _, doc_id = await proposed()
    body = {
        "tags": [{"key": "type", "value": "report"}, {"key": "region", "value": "Europe"}],
        "dismissProposals": ["region=EMEA"],
        "acceptProposals": ["topic=margin"],
    }
    d = patch(client, lib, doc_id, **body).json()
    assert d["tags"] == [
        {"key": "type", "value": "report", "source": "user"},
        {"key": "region", "value": "Europe", "source": "user"},
        {"key": "topic", "value": "margin", "source": "accepted-proposal"},
    ]
    assert d["facets"] == ["region=Europe", "topic=margin", "type=report"] and d["tagProposals"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"acceptProposals": ["region=APAC"]},  # never proposed
        {"dismissProposals": ["type=report"]},  # a tag, not a proposal
        {"acceptProposals": ["region=EMEA"], "dismissProposals": ["region=EMEA"]},
        {"acceptProposals": ["period"], "periodStart": "2025-01-01", "periodEnd": "2025-03-31"},
        {"acceptProposals": ["author"], "author": "Sam"},
        {"acceptProposals": "author"},
        {"acceptProposals": [""]},
    ],
)
async def test_an_accept_or_dismiss_that_does_not_fit_is_refused_and_changes_nothing(body):
    client, s, lib, _, doc_id = await proposed()
    r = patch(client, lib, doc_id, **body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert "APAC" not in r.json()["error"]["message"]
    d = doc(client, lib, doc_id)
    assert d["metadataVersion"] == 0 and len(d["tagProposals"]) == 2 and s.queue.sent == []


async def test_a_resolved_proposal_cannot_be_accepted_again():
    client, _, lib, _, doc_id = await proposed()
    assert patch(client, lib, doc_id, acceptProposals=["author"]).status_code == 200
    assert patch(client, lib, doc_id, acceptProposals=["author"]).status_code == 400


# -- library descriptions -------------------------------------------------------------------


async def test_ingestion_queues_one_useful_description_proposal_after_a_burst_of_uploads():
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION)
    ids = [upload(client, lib, name=f"r{i}.txt") for i in range(12)]
    await drain(s, worker)
    library = client.get(f"/v1/libraries/{lib}").json()
    proposal = library["descriptionProposal"]
    assert proposal["text"] == DESCRIPTION["description"] and proposal["basedOnDocumentCount"] == 12
    assert library["description"] == ""  # never overwritten
    description_calls = [m for m in chat.calls if "description of a file library" in m[0]["content"]]
    assert len(description_calls) == 1  # 12 messages queued; 11 found nothing new
    assert len(ids) == 12


async def test_the_sample_reads_each_documents_active_opening_and_is_capped_at_30():
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION)
    for i in range(35):
        upload(client, lib, name=f"r{i:02}.txt", data=f"Report {i}. {TEXT}".encode())
    await drain(s, worker)
    s.queue.sent.clear()
    await worker.propose_description(lib, requested=True)
    import json

    sampled = json.loads(chat.calls[-1][1]["content"])["sampleDocuments"]
    assert len(sampled) == 30 and all(len(d["openingLines"]) <= 400 for d in sampled)
    assert sampled[0]["openingLines"].startswith("Report 0.")


async def test_the_endpoint_queues_a_requested_proposal_which_always_regenerates():
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION, {"description": "Second."})
    upload(client, lib)
    await drain(s, worker)
    r = client.post(f"/v1/libraries/{lib}/description:propose")
    assert r.status_code == 202
    assert s.queue.sent[-1] == (lib, {"type": "proposeDescription", "libraryId": lib, "requested": True})
    await drain(s, worker)
    assert client.get(f"/v1/libraries/{lib}").json()["descriptionProposal"]["text"] == "Second."


async def test_an_unrequested_proposal_that_is_not_due_does_nothing():
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION)
    upload(client, lib)
    await drain(s, worker)
    calls = len(chat.calls)
    await worker.handle({"type": "proposeDescription", "libraryId": lib})
    assert len(chat.calls) == calls


async def test_a_day_old_proposal_is_refreshed_once_something_changed():
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION)
    upload(client, lib)
    await drain(s, worker)
    record = await s.libraries.get(lib)
    record["descriptionProposal"]["generatedAt"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    await s.libraries.replace(record)
    await worker.refresh_library(lib)
    assert s.queue.sent == []  # nothing changed
    upload(client, lib, name="b.txt")
    await drain(s, worker)
    assert client.get(f"/v1/libraries/{lib}").json()["descriptionProposal"]["basedOnDocumentCount"] == 2


async def test_the_endpoint_refuses_a_missing_or_deleting_library():
    client, s, lib, _, _ = setup()
    assert client.post("/v1/libraries/nope/description:propose").json()["error"]["code"] == "library_not_found"
    client.delete(f"/v1/libraries/{lib}")
    r = client.post(f"/v1/libraries/{lib}/description:propose")
    assert r.status_code == 404 and r.json()["error"]["code"] == "library_not_found"


async def test_the_endpoint_reports_a_queue_failure():
    client, s, lib, _, _ = setup()

    async def down(message, *, session_id):
        raise RuntimeError("bus down")

    s.queue.send = down
    r = client.post(f"/v1/libraries/{lib}/description:propose")
    assert r.status_code == 503 and r.json()["error"]["code"] == "queue_unavailable"


async def test_a_useless_description_answer_raises_so_the_message_is_retried_and_dead_letters_quietly(caplog):
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION)
    upload(client, lib)
    await drain(s, worker)
    chat.answers = [{"description": ""}]
    with pytest.raises(ValueError):
        await worker.propose_description(lib, requested=True)
    # The previous proposal stays.
    assert client.get(f"/v1/libraries/{lib}").json()["descriptionProposal"]["text"] == DESCRIPTION["description"]
    await worker.fail({"type": "proposeDescription", "libraryId": lib}, "processing_error:ProposalError")
    assert any(r.getMessage() == "proposeDescription dead-lettered" for r in caplog.records)


async def test_a_description_for_an_empty_or_deleted_library_is_never_proposed():
    client, s, lib, worker, chat = setup(ANSWER, DESCRIPTION)
    await worker.propose_description(lib, requested=True)
    assert chat.calls == []
    client.delete(f"/v1/libraries/{lib}")
    await worker.propose_description(lib, requested=True)
    assert chat.calls == []
