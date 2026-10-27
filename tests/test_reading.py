"""Whole-file reads must be complete, generation-safe and bound to the attached library."""

from __future__ import annotations

import json
import logging

import pytest

from fli.chunking import ApproxTokenizer, Chunker
from fli.query import DateRange, SearchFilters
from fli.reading import manifest, render_catalog
from fli.search import CosmosChunkReader, QueryCost
from fli.worker.handlers import Worker
from tests.fakes import FakeEmbedder, api


@pytest.fixture
def ctx():
    client, services = api()
    client.headers["X-Agent-Id"] = "agent"
    lib = client.post("/v1/libraries", json={"kind": "shared", "name": "Procedures"}).json()["id"]
    worker = Worker(
        services.libraries,
        services.documents,
        services.chunks,
        services.blobs,
        services.queue,
        FakeEmbedder(),
        Chunker(ApproxTokenizer()),
    )
    return client, services, lib, worker


def upload(
    client,
    lib,
    name="procedure.md",
    text="# Safety\n\nCheck every valve.\n\n## Completion\n\nRecord the final result.",
    **metadata,
):
    response = client.post(
        f"/v1/libraries/{lib}/documents",
        files={"file": (name, text.encode(), "text/plain")},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 202, response.text
    return response.json()["documentId"]


async def drain(s, worker):
    while s.queue.sent:
        _, message = s.queue.sent.pop(0)
        await worker.handle(message)


def read(client, lib, *ids, **options):
    return client.post(
        f"/v1/libraries/{lib}/documents:read", json={"documents": [{"documentId": d} for d in ids], **options}
    )


def discover(client, lib, **selectors):
    return client.post(f"/v1/libraries/{lib}/documents:discover", json=selectors)


def test_legacy_visual_omissions_are_not_reported_as_complete():
    document = {"id": "legacy", "activeGeneration": 1, "imageCount": 1, "imagesSkipped": 2}
    assert not manifest(document)["visualsComplete"]


async def test_complete_extraction_is_preserved_before_overlap_and_survives_full_response(ctx):
    client, s, lib, worker = ctx
    text = "# Procedure\n\n" + ("Inspect the valve. Record its pressure. " * 130) + "\n\n## End\n\nFinal signoff."
    doc_id = upload(client, lib, text=text)
    await drain(s, worker)
    result = read(client, lib, doc_id)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["complete"] and data["disposition"] == "complete"
    [doc] = data["documents"]
    assert doc["representation"] == "extracted_sections"
    assert doc["generation"] == 1 and doc["complete"]
    assert "Final signoff." in json.dumps(doc["sections"])
    assert sum(x["text"].count("Inspect the valve.") for x in doc["sections"]) == 130
    stored = await s.documents.get(lib, doc_id)
    assert stored["chunkCount"] > len(doc["sections"])
    assert stored["fullText"]["sectionCount"] == len(doc["sections"])
    assert data["estimatedTokens"] > doc["estimatedTokens"] > 0
    assert "blobPath" not in result.text and "embedding" not in result.text


async def test_catalogue_enumerates_all_author_and_tag_matches_including_unreadable_files(ctx):
    client, s, lib, worker = ctx
    ids = [
        upload(client, lib, name="rules.md", author="Jane", tags=[{"key": "topic", "value": "safety"}])
        for _ in range(3)
    ]
    upload(client, lib, author="Sam", tags=[{"key": "topic", "value": "safety"}])
    upload(client, lib, author="Jane", tags=[{"key": "topic", "value": "finance"}])
    await drain(s, worker)
    pending = upload(client, lib, name="not-ready.md", author="Jane", tags=[{"key": "topic", "value": "safety"}])
    selectors = {"filters": {"authors": ["Jane"], "tags": ["topic=safety"]}, "limit": 1}
    found = []
    while True:
        page = discover(client, lib, **selectors).json()
        found += page["documents"]
        if not page["hasMore"]:
            assert page["nextAfter"] is None
            break
        selectors["after"] = page["nextAfter"]
    assert [d["documentId"] for d in found] == sorted([*ids, pending])
    assert sum(not d["readable"] for d in found) == 1
    assert all(d["estimatedTokens"] > 0 for d in found if d["readable"])
    # Case-insensitive exact name returns duplicates, not an arbitrary first match.
    matches = discover(client, lib, fileNames=["RULES.MD"]).json()["documents"]
    assert {d["documentId"] for d in matches} == set(ids)
    assert len(discover(client, lib, fileNameContains="ready").json()["documents"]) == 1


async def test_full_batch_is_atomic_for_budget_and_inspection(ctx):
    client, s, lib, worker = ctx
    ids = [upload(client, lib, name=f"{i}.md") for i in range(2)]
    await drain(s, worker)
    for options, disposition in (({"maxTokens": 1}, "budget_exceeded"), ({"includeText": False}, "inspected")):
        data = read(client, lib, *ids, **options).json()
        assert data["disposition"] == disposition and not data["complete"]
        assert len(data["documents"]) == 2 and data["estimatedTokens"] > 1
        assert all("sections" not in d and not d["complete"] for d in data["documents"])
    success = read(client, lib, *ids).json()
    assert all(d["complete"] and d["sections"] for d in success["documents"])
    assert read(client, lib, *ids, "missing").status_code == 404


async def test_replacement_keeps_old_full_text_until_commit_then_purges_it(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    old_path = (await s.documents.get(lib, doc_id))["fullText"]["blobPath"]
    replacement = client.put(
        f"/v1/libraries/{lib}/documents/{doc_id}/content",
        files={"file": ("new.md", b"# Replacement\n\nNew rules only.", "text/plain")},
    )
    assert replacement.status_code == 202
    assert read(client, lib, doc_id).json()["documents"][0]["generation"] == 1
    await drain(s, worker)
    current = read(client, lib, doc_id).json()["documents"][0]
    assert current["generation"] == 2 and current["fileName"] == "new.md"
    assert "Check every valve" not in json.dumps(current["sections"])
    assert old_path not in s.blobs.blobs
    pinned = client.post(
        f"/v1/libraries/{lib}/documents:read", json={"documents": [{"documentId": doc_id, "generation": 1}]}
    )
    assert pinned.status_code == 409 and pinned.json()["error"]["code"] == "document_changed"
    client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")
    assert read(client, lib, doc_id).status_code == 404
    assert discover(client, lib).json()["documents"] == []
    await drain(s, worker)
    assert not any(p.startswith(f"{lib}/{doc_id}/") for p in s.blobs.blobs)


async def test_legacy_read_fetches_every_active_chunk_in_order_and_refuses_missing_chunks(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib, text="Step one. Step two. " * 200)
    await drain(s, worker)
    record = await s.documents.get(lib, doc_id)
    record.pop("fullText")
    await s.documents.replace(record)
    s.chunks.items = dict(reversed(list(s.chunks.items.items())))
    data = read(client, lib, doc_id).json()
    assert data["complete"] and data["documents"][0]["representation"] == "indexed_chunks"
    assert len(data["documents"][0]["sections"]) == record["chunkCount"]
    assert any("overlapping" in n for n in data["notes"])
    first = s.chunks.items[(lib, doc_id, f"{doc_id}:1:0")]["text"]
    assert data["documents"][0]["sections"][0]["text"] == first
    del s.chunks.items[(lib, doc_id, f"{doc_id}:1:0")]
    failed = read(client, lib, doc_id)
    assert failed.status_code == 409 and failed.json()["error"]["code"] == "document_text_incomplete"


@pytest.mark.parametrize("event", ["replace", "delete", "retag", "delete_library"])
async def test_a_change_during_the_read_returns_no_content(ctx, monkeypatch, event):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    download = s.blobs.download

    async def change_during_download(path):
        data = await download(path)
        if event == "delete_library":
            library = await s.libraries.get(lib)
            library["status"] = "deleting"
            await s.libraries.replace(library)
        else:
            document = await s.documents.get(lib, doc_id)
            document.update(
                {"replace": {"activeGeneration": 2}, "delete": {"status": "deleting"}, "retag": {"metadataVersion": 5}}[
                    event
                ]
            )
            await s.documents.replace(document)
        return data

    monkeypatch.setattr(s.blobs, "download", change_during_download)
    response = read(client, lib, doc_id)
    assert response.status_code in (404, 409)
    assert "sections" not in response.text and "Check every valve" not in response.text


@pytest.mark.parametrize(
    "endpoint,body",
    [("tools", None), ("documents:discover", {}), ("documents:read", {"documents": [{"documentId": "unknown"}]})],
)
def test_personal_library_backstop_applies_to_every_agent_tool(ctx, endpoint, body):
    client, _, _, _ = ctx
    lib = client.post("/v1/libraries", json={"kind": "personal", "name": "Private", "boundAgentId": "owner"}).json()[
        "id"
    ]
    response = client.request(
        "GET" if body is None else "POST",
        f"/v1/libraries/{lib}/{endpoint}",
        json=body,
        headers={"X-Agent-Id": "intruder"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "library_not_bound_to_agent"


async def test_wrong_library_and_not_ready_never_return_content(ctx):
    client, s, lib, worker = ctx
    pending = upload(client, lib)
    assert read(client, lib, pending).status_code == 409
    await drain(s, worker)
    other = client.post("/v1/libraries", json={"kind": "shared", "name": "Other"}).json()["id"]
    assert read(client, other, pending).status_code == 404
    assert discover(client, other).json()["documents"] == []
    assert (
        client.post(
            f"/v1/libraries/{lib}/documents:read", json={"documents": [{"documentId": pending}], "libraryId": other}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "options",
    [
        {"maxTokens": 0},
        {"maxTokens": True},
        {"maxTokens": 200001},
        {"includeText": "yes"},
        {"documents": []},
        {"documents": [{"documentId": "d"}, {"documentId": "d"}]},
    ],
)
def test_read_limits_and_types_are_validated(ctx, options):
    client, _, lib, _ = ctx
    body = {"documents": [{"documentId": "d"}], **options}
    assert client.post(f"/v1/libraries/{lib}/documents:read", json=body).status_code == 400


async def test_corrupted_canonical_text_does_not_silently_fall_back_to_chunks(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    path = (await s.documents.get(lib, doc_id))["fullText"]["blobPath"]
    await s.blobs.upload(path, b'[{"text":"only part of the file"}]', "application/json")
    response = read(client, lib, doc_id)
    assert response.status_code == 409 and "sections" not in response.text


async def test_read_telemetry_has_counts_and_cost_but_no_content(ctx, caplog):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib, text="Sensitive valve procedure uniquely identifying the document.")
    await drain(s, worker)
    with caplog.at_level(logging.INFO, logger="fli.reading"):
        assert read(client, lib, doc_id).json()["complete"]
    [record] = [r for r in caplog.records if r.msg == "library_read"]
    assert record.documentCount == 1 and record.estimatedTokens > 0
    assert "Sensitive valve procedure" not in str(record.__dict__)


def test_catalogue_sql_keeps_library_scope_and_parameters_without_vector_or_chunk_limits():
    query = render_catalog(
        "lib",
        SearchFilters(
            authors=("O'Brien",), tags=("type=procedure",), authored_between=DateRange("2025-01-01", "2026-01-01")
        ),
        ["Safety.md", "Other.md"],
        "safety",
        "cursor",
        50,
    )
    assert query.sql.startswith("SELECT TOP 51") and "ORDER BY c.id" in query.sql
    assert "c.libraryId = @libraryId" in query.sql and "c.id > @after" in query.sql
    assert "c.authoredAt >= @aFrom" in query.sql and "ARRAY_CONTAINS(c.facets, @tag0)" in query.sql
    assert "O'Brien" not in query.sql and "Safety.md" not in query.sql
    assert "c.embedding" not in query.sql and "SELECT *" not in query.sql


async def test_cosmos_legacy_reader_selects_text_only_in_full_document_partition():
    class Container:
        def query_items(self, sql, **kwargs):
            assert "c.libraryId = @libraryId" in sql and "c.generation = @generation" in sql
            assert sql.startswith("SELECT TOP 6") and "embedding" not in sql and "SELECT *" not in sql
            assert kwargs["partition_key"] == ["lib", "doc"]
            assert kwargs["raw_response_hook"] is cost

            async def rows():
                yield {"chunkIndex": 0, "text": "Procedure"}

            return rows()

    cost = QueryCost()
    assert await CosmosChunkReader(Container()).document("lib", "doc", 2, 6, cost) == [
        {"chunkIndex": 0, "text": "Procedure"}
    ]


async def test_demo_agent_can_find_a_vaguely_described_file_then_receive_the_entire_text(ctx):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from fli_demo.client import IndexClient

    client, s, lib, worker = ctx
    tail = "This final signoff applies after the entire procedure has been completed."
    doc_id = upload(
        client, lib, text="# Process standard\n\n" + "Inspect the valve and record the pressure. " * 140 + tail
    )
    await drain(s, worker)
    index = IndexClient("http://testserver", "k", http=client)
    calls = []

    def chat(messages, tools):
        calls.append(messages)
        offered = [t["function"]["name"] for t in tools]
        assert len(offered) == 4
        if len(calls) == 1:
            return Reply(None, [ToolCall("search", offered[0], json.dumps({"query": "process standard"}))])
        if len(calls) == 2:
            result = json.loads(messages[-1]["content"])["results"][0]
            assert result["documentId"] == doc_id and result["generation"] == 1
            return Reply(
                None,
                [
                    ToolCall(
                        "read",
                        offered[2],
                        json.dumps(
                            {
                                "documents": [{"documentId": result["documentId"], "generation": result["generation"]}],
                                "libraryId": "injected",
                            }
                        ),
                    )
                ],
            )
        full = json.loads(messages[-1]["content"])
        assert full["complete"] and full["documents"][0]["complete"]
        assert tail in json.dumps(full["documents"][0]["sections"])
        assert sum(len(section["text"]) for section in full["documents"][0]["sections"]) > 1500
        return Reply("Read the complete extracted procedure, including the final signoff.")

    turn = run_turn(
        chat, index, {"id": "agent", "libraryIds": [lib]}, [], "Read the whole file about standards for this process."
    )
    assert [s.operation for s in turn.searches] == ["search", "read"]
    assert turn.searches[-1].complete and turn.searches[-1].estimated_tokens > 0
    assert all(s.library_id == lib for s in turn.searches)


async def test_demo_agent_enumerates_metadata_pages_inspects_and_reads_multiple_files(ctx):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from fli_demo.client import IndexClient

    client, s, lib, worker = ctx
    ids = [upload(client, lib, name=f"Rules-{i}.md", author="Jane") for i in range(2)]
    await drain(s, worker)
    index = IndexClient("http://testserver", "k", http=client)
    selected = []
    stage = 0

    def chat(messages, tools):
        nonlocal stage
        stage += 1
        names = [t["function"]["name"] for t in tools]
        if stage in (1, 2):
            arguments = {"filters": {"authors": ["Jane"]}, "limit": 1}
            if stage == 2:
                page = json.loads(messages[-1]["content"])
                assert page["hasMore"]
                selected.extend(
                    {"documentId": d["documentId"], "generation": d["generation"]} for d in page["documents"]
                )
                arguments["after"] = page["nextAfter"]
            return Reply(None, [ToolCall(str(stage), names[1], json.dumps(arguments))])
        if stage == 3:
            page = json.loads(messages[-1]["content"])
            assert not page["hasMore"]
            selected.extend({"documentId": d["documentId"], "generation": d["generation"]} for d in page["documents"])
            return Reply(
                None, [ToolCall("inspect", names[2], json.dumps({"documents": selected, "includeText": False}))]
            )
        if stage == 4:
            inspected = json.loads(messages[-1]["content"])
            assert inspected["disposition"] == "inspected" and not inspected["complete"]
            assert all("sections" not in d for d in inspected["documents"])
            return Reply(None, [ToolCall("read", names[2], json.dumps({"documents": selected}))])
        data = json.loads(messages[-1]["content"])
        assert data["complete"] and {d["documentId"] for d in data["documents"]} == set(ids)
        return Reply("Both matching documents have been read.")

    turn = run_turn(chat, index, {"id": "agent", "libraryIds": [lib]}, [], "Read all documents by Jane.")
    assert [t.operation for t in turn.searches] == ["discover", "discover", "read", "read"]
    assert turn.searches[-1].complete


async def test_demo_budget_refusal_never_claims_or_delivers_a_partial_full_read(ctx):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from fli_demo.client import IndexClient

    client, s, lib, worker = ctx
    doc_id = upload(client, lib, text="Private full text should be withheld in its entirety. " * 50)
    await drain(s, worker)
    index = IndexClient("http://testserver", "k", http=client)
    calls = 0

    def chat(messages, tools):
        nonlocal calls
        calls += 1
        if calls == 1:
            name = next(t["function"]["name"] for t in tools if t["function"]["name"].startswith("read_documents_"))
            return Reply(
                None, [ToolCall("r", name, json.dumps({"documents": [{"documentId": doc_id}], "maxTokens": 1}))]
            )
        data = json.loads(messages[-1]["content"])
        assert data["disposition"] == "budget_exceeded" and not data["complete"]
        assert "sections" not in data["documents"][0]
        assert "Private full text" not in messages[-1]["content"]
        return Reply("The selected full text exceeds the specified budget; it has not been read.")

    turn = run_turn(chat, index, {"id": "agent", "libraryIds": [lib]}, [], "Read it all")
    assert not turn.searches[-1].complete


async def test_token_budget_counts_the_whole_success_response(ctx):
    from fli.chunking import default_tokenizer

    client, s, lib, worker = ctx
    doc_id = upload(client, lib)
    await drain(s, worker)
    result = read(client, lib, doc_id, maxTokens=3000).json()
    actual = len(default_tokenizer().encode(json.dumps(result, ensure_ascii=False)))
    assert actual <= result["estimatedTokens"] <= actual + 64
    assert result["estimatedTokens"] <= 3000


async def test_opening_description_is_bounded_and_labelled_as_a_discovery_hint(ctx):
    from fli.proposals import parse_document_proposals
    from tests.fakes import FakeChat

    client, s, lib, worker = ctx
    worker.proposer = FakeChat({"description": "An operational valve inspection procedure.", "tags": []})
    doc_id = upload(client, lib)
    await drain(s, worker)
    [document] = discover(client, lib).json()["documents"]
    assert document["documentId"] == doc_id
    assert document["description"] == "An operational valve inspection procedure."
    assert document["descriptionSource"] == "generated-from-opening"
    assert parse_document_proposals({"description": "x" * 801}).description is None


async def test_one_question_can_mix_multiple_full_files_with_passages_from_another_without_confirmation(ctx):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from fli_demo.client import IndexClient

    client, s, lib, worker = ctx
    complete_ids = [
        upload(
            client,
            lib,
            name=f"Standard-{i}.md",
            text="# Standard\n\n" + ("The complete procedure requires pressure logging. " * 80) + f"Final clause {i}.",
        )
        for i in range(2)
    ]
    supporting_id = upload(
        client, lib, name="incident-notes.md", text="One incident involved an incorrectly calibrated pressure gauge."
    )
    await drain(s, worker)
    # Ranking itself is tested live elsewhere. Here make the relevant supporting
    # document the fake search hit so we can verify what the model actually receives.
    s.reader.order = [cid for (library, doc, cid) in s.chunks.items if library == lib and doc == supporting_id]
    index = IndexClient("http://testserver", "k", http=client)
    calls = 0

    def chat(messages, tools):
        nonlocal calls
        calls += 1
        names = [t["function"]["name"] for t in tools]
        if calls == 1:
            return Reply(
                None,
                [
                    ToolCall(
                        "full",
                        names[2],
                        json.dumps({"documents": [{"documentId": d, "generation": 1} for d in complete_ids]}),
                    )
                ],
            )
        if calls == 2:
            full = json.loads(messages[-1]["content"])
            assert full["complete"] and len(full["documents"]) == 2
            return Reply(None, [ToolCall("support", names[0], json.dumps({"query": "pressure gauge incidents"}))])
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        assert len(results) == 2 and results[0]["complete"]
        assert all(f"Final clause {i}." in json.dumps(results[0]["documents"]) for i in range(2))
        assert results[1]["results"][0]["documentId"] == supporting_id
        assert "incorrectly calibrated" in results[1]["results"][0]["text"]
        assert "combine complete reads" in messages[0]["content"]
        assert "without routine user confirmation" in messages[0]["content"]
        return Reply("The two complete standards require logging; incident-notes.md supplies the calibration example.")

    turn = run_turn(
        chat,
        index,
        {"id": "agent", "libraryIds": [lib]},
        [],
        "Read both standards completely and use relevant incident evidence to assess the procedure.",
    )
    assert calls == 3 and [t.operation for t in turn.searches] == ["read", "search"]
    assert turn.searches[0].complete
    assert {c.file_name for c in turn.searches[0].citations} == {"Standard-0.md", "Standard-1.md"}


async def test_parallel_full_read_calls_share_the_remaining_turn_budget(ctx):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from fli_demo.client import IndexClient

    client, s, lib, worker = ctx
    ids = [
        upload(client, lib, name=f"Large-{i}.md", text="Record the measured pressure at each inspection. " * 700)
        for i in range(2)
    ]
    await drain(s, worker)
    index = IndexClient("http://testserver", "k", http=client)
    seen_budgets = []
    read_call = index.read_documents

    def record_budget(library, agent, **selection):
        seen_budgets.append(selection["maxTokens"])
        return read_call(library, agent, **selection)

    index.read_documents = record_budget
    calls = 0

    def chat(messages, tools):
        nonlocal calls
        calls += 1
        if calls == 1:
            name = next(t["function"]["name"] for t in tools if t["function"]["name"].startswith("read_documents_"))
            return Reply(
                None,
                [ToolCall(str(i), name, json.dumps({"documents": [{"documentId": d}]})) for i, d in enumerate(ids)],
            )
        first, second = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        assert first["complete"] and not second["complete"]
        assert second["disposition"] == "budget_exceeded"
        assert "sections" not in second["documents"][0]
        return Reply("One file was read completely; the second needs additional context capacity.")

    turn = run_turn(
        chat, index, {"id": "agent", "libraryIds": [lib]}, [], "Read both documents.", context_token_budget=20000
    )
    assert len(seen_budgets) == 2 and seen_budgets[1] < seen_budgets[0]
    assert [t.complete for t in turn.searches] == [True, False]


async def test_obviously_oversized_canonical_read_does_not_download_text(ctx, monkeypatch):
    from fli.chunking import default_tokenizer

    client, s, lib, worker = ctx
    doc_id = upload(client, lib, text="Inspect the valve and record the pressure. " * 100)
    await drain(s, worker)
    doc = await s.documents.get(lib, doc_id)
    raw, _ = s.blobs.blobs[doc["fullText"]["blobPath"]]
    tokenizer = default_tokenizer()
    doc["fullText"].update(tokenizer=tokenizer.name, tokenCount=len(tokenizer.encode(raw.decode())))
    await s.documents.replace(doc)

    async def unexpected_download(path):
        pytest.fail("known oversized text should be refused before downloading")

    monkeypatch.setattr(s.blobs, "download", unexpected_download)
    response = read(client, lib, doc_id, maxTokens=1).json()
    assert response["disposition"] == "budget_exceeded" and not response["complete"]
    assert response["estimateScope"] == "stored_sections_lower_bound"


async def test_catalogue_tools_allow_metadata_newer_than_the_cached_search_vocabulary(ctx):
    client, s, lib, worker = ctx
    doc_id = upload(client, lib, author="Original", tags=[{"key": "topic", "value": "old"}])
    await drain(s, worker)
    client.patch(
        f"/v1/libraries/{lib}/documents/{doc_id}",
        json={"author": "New Author", "tags": [{"key": "topic", "value": "new"}]},
    )
    # No drain: the searchable vocabulary and chunks still hold the previous metadata.
    tools = client.get(f"/v1/libraries/{lib}/tools").json()["tools"]
    filters = tools[1]["function"]["parameters"]["properties"]["filters"]["properties"]
    assert "enum" not in filters["authors"]["items"] and "enum" not in filters["tags"]["items"]
    page = discover(client, lib, filters={"authors": ["New Author"], "tags": ["topic=new"]}).json()
    assert [d["documentId"] for d in page["documents"]] == [doc_id]
    assert not any("No document" in n for n in page["coverage"]["notes"])
