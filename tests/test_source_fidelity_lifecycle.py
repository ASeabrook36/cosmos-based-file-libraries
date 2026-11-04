"""Opt-in source repair through the normal API/publisher, with isolated fakes."""

import hashlib
import json

import pytest

from fli.checkpoints import digest, encoded
from fli.reference_format import expanded
from fli.source_fidelity import PROFILE, evidence_structure
from tests.test_modular_ingestion import output, setup, upload
from tests.test_source_fidelity import analysis, cell_section, fixture, save
from tests.test_worker_handlers import drain


class Provider:
    def __init__(self, callback=None):
        self.calls = 0
        self.callback = callback

    async def prepare(self, work, source):
        self.calls += 1
        if self.callback:
            await self.callback(work)
        value = output(work)
        import base64

        value["sourceBase64"] = base64.b64encode(source).decode()
        value["analysis"] = {"analyzeResult": analysis()}
        return value


def environment():
    client, s, lib, worker = setup(PROFILE)
    worker.microsoft = Provider()
    return client, s, lib, worker


def read(client, lib, doc_id, *, mode="full", **kwargs):
    return client.post(
        f"/v1/libraries/{lib}/documents:read",
        json={"mode": mode, "documents": [{"documentId": doc_id, **kwargs}], "maxTokens": 200000},
        headers={"X-Agent-Id": "reader"},
    )


async def ready():
    client, s, lib, worker = environment()
    doc_id = upload(client, lib, source=save(fixture()), name="source.docx", metadata={"author": "Owner"})
    await drain(s, worker)
    doc = await s.documents.get(lib, doc_id)
    assert doc["status"] == "ready", doc.get("statusDetail")
    return client, s, lib, worker, doc_id, doc


@pytest.mark.parametrize("view", ["raw", "compact", "references"])
async def test_complete_selected_and_search_to_outline_expansion_preserve_structure(view):
    client, s, lib, worker, doc_id, doc = await ready()
    source = json.loads(await s.blobs.download(doc["fullText"]["blobPath"]))
    expected = cell_section(source)["sourceStructure"]
    endpoint = f"/v1/libraries/{lib}/documents:read?view={view}&selectors=handles"
    response = client.post(
        endpoint, json={"documents": [{"documentId": doc_id}], "maxTokens": 200000}, headers={"X-Agent-Id": "reader"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    evidence = expanded(body["evidence"]) if view == "references" else body.get("evidence", body)
    cell = cell_section(evidence["documents"][0]["sections"])
    projected = expected if view == "raw" else evidence_structure(expected)
    assert cell["sourceStructure"] == projected
    assert cell["text"] == cell_section(source)["text"]
    if view != "raw":
        assert "runs" not in json.dumps(projected) and "abstract" not in json.dumps(projected)
    assert evidence["complete"]
    assert worker.embedder.calls == 0
    assert all(c["embedding"] == [1.0, 2.0, 3.0] and c["location"] == {} for c in s.chunks.items.values())
    search = client.post(
        f"/v1/libraries/{lib}/search", json={"query": "condition"}, headers={"X-Agent-Id": "reader"}
    ).json()
    hit = search["results"][0]
    assert hit["generation"] == 1 and hit["contentRevision"] == 2
    unavailable = read(client, lib, doc_id, mode="selected", evidenceIds=[hit["evidenceId"]])
    assert unavailable.status_code == 409 and unavailable.json()["error"]["code"] == "source_scope_unavailable"
    outline = read(client, lib, doc_id, mode="outline").json()["documents"][0]["outline"]
    target = next(s for s in outline if s["location"].get("originalCoordinate") == expected["coordinate"])
    selected = client.post(
        endpoint,
        json={
            "mode": "selected",
            "documents": [
                {"documentId": doc_id, "generation": 1, "contentRevision": 2, "sectionIds": [target["sectionId"]]}
            ],
            "maxTokens": 200000,
        },
        headers={"X-Agent-Id": "reader"},
    )
    assert selected.status_code == 200, selected.text
    value = selected.json()
    evidence = expanded(value["evidence"]) if view == "references" else value.get("evidence", value)
    item = evidence["documents"][0]
    assert item["selectionComplete"] and not item["documentComplete"]
    assert len(item["sections"]) == 1 and item["sections"][0]["sourceStructure"] == projected


@pytest.mark.parametrize(
    "damage",
    [
        "missing-descriptor",
        "descriptor-generation",
        "missing-blob",
        "corrupt-blob",
        "missing-coverage",
        "wrong-schema",
        "missing-structure",
        "foreign-job",
        "location",
    ],
)
async def test_missing_or_corrupt_source_artifact_never_falls_back_to_flattened_chunks(damage):
    client, s, lib, _, doc_id, doc = await ready()
    path = doc["fullText"]["blobPath"]
    if damage == "missing-descriptor":
        doc.pop("fullText")
    elif damage == "descriptor-generation":
        doc["fullText"]["generation"] = 2
    elif damage == "missing-blob":
        await s.blobs.delete(path)
    elif damage == "corrupt-blob":
        await s.blobs.upload(path, b"broken", "application/json")
    elif damage == "missing-coverage":
        doc["processingCoverage"].pop("sourceStructure")
    elif damage == "wrong-schema":
        doc["processingCoverage"]["sourceStructure"] = "unknown"
    else:
        artifact = json.loads(await s.blobs.download(path))
        if damage == "missing-structure":
            artifact[0].pop("sourceStructure")
        elif damage == "foreign-job":
            artifact[0]["sourceStructure"]["jobSha256"] = "0" * 64
        elif damage == "location":
            artifact[0]["location"]["paragraph"] += 1
        data = encoded(artifact)
        # A malformed producer artifact with an internally consistent descriptor
        # must still fail its source/job/location validation.
        await s.blobs.upload(path, data, "application/json")
        doc["fullText"]["sha256"] = hashlib.sha256(data).hexdigest()
    await s.documents.replace(doc)
    response = read(client, lib, doc_id)
    assert response.status_code == 409
    assert "sections" not in response.json()


async def test_metadata_patch_keeps_source_readable_pending_replacement_keeps_old_active_source_and_stales_selectors():
    client, s, lib, worker, doc_id, doc = await ready()
    outline = read(client, lib, doc_id, mode="outline").json()["documents"][0]["outline"]
    selector = outline[0]["sectionId"]
    old_source = read(client, lib, doc_id).json()["documents"][0]["sections"]
    assert client.patch(f"/v1/libraries/{lib}/documents/{doc_id}", json={"author": "Edited"}).status_code == 200
    assert read(client, lib, doc_id).json()["documents"][0]["author"] == "Edited"
    assert read(client, lib, doc_id).json()["documents"][0]["sections"] == old_source
    replacement = client.put(
        f"/v1/libraries/{lib}/documents/{doc_id}/content", files={"file": ("next.docx", save(fixture()))}
    )
    assert replacement.status_code == 202
    assert read(client, lib, doc_id).json()["documents"][0]["sections"] == old_source
    await drain(s, worker)
    current = await s.documents.get(lib, doc_id)
    assert current["activeGeneration"] == 2 and current["author"] == "Edited"
    assert read(client, lib, doc_id, mode="selected", sectionIds=[selector]).status_code == 409
    assert read(client, lib, doc_id, generation=1).status_code == 409
    assert read(client, lib, doc_id, contentRevision=1).status_code == 409


async def test_private_library_and_oversized_multidocument_batch_return_no_source():
    client, s, lib, worker, doc_id, doc = await ready()
    second = upload(client, lib, source=save(fixture()), name="second.docx")
    await drain(s, worker)
    response = client.post(
        f"/v1/libraries/{lib}/documents:read?view=compact",
        json={"documents": [{"documentId": doc_id}, {"documentId": second}], "maxTokens": 1},
        headers={"X-Agent-Id": "reader"},
    )
    assert response.status_code == 200 and response.json()["evidence"]["disposition"] == "budget_exceeded"
    assert all("sections" not in d for d in response.json()["evidence"]["documents"])
    # Fixture-only trust-boundary change, never an application API mutation.
    library = await s.libraries.get(lib)
    library.update(kind="personal", boundAgentId="owner")
    await s.libraries.replace(library)
    assert read(client, lib, doc_id).status_code == 403
    assert (
        client.post(
            f"/v1/libraries/{lib}/search", json={"query": "condition"}, headers={"X-Agent-Id": "reader"}
        ).status_code
        == 403
    )
    assert client.get(f"/v1/libraries/{lib}/tools", headers={"X-Agent-Id": "reader"}).status_code == 403


@pytest.mark.parametrize("action", ["delete", "replace", "metadata"])
async def test_late_provider_output_respects_existing_conditional_lifecycle(action):
    client, s, lib, worker = environment()
    source = save(fixture())
    doc_id = upload(client, lib, source=source, name="source.docx")
    _, message = s.queue.sent.pop(0)

    async def intervene(work):
        if action == "delete":
            client.delete(f"/v1/libraries/{lib}/documents/{doc_id}")
        elif action == "replace":
            client.put(f"/v1/libraries/{lib}/documents/{doc_id}/content", files={"file": ("next.docx", source)})
        else:
            client.patch(f"/v1/libraries/{lib}/documents/{doc_id}", json={"author": "During extraction"})

    worker.microsoft = Provider(intervene)
    await worker.handle(message)
    doc = await s.documents.get(lib, doc_id)
    if action == "metadata":
        assert doc["activeGeneration"] == 1 and doc["author"] == "During extraction"
        assert read(client, lib, doc_id).status_code == 200
    else:
        assert doc["activeGeneration"] is None and not s.chunks.items
    worker.microsoft = Provider()
    await drain(s, worker)
    if action == "delete":
        assert await s.documents.get(lib, doc_id) is None
        assert not await s.blobs.list_prefix(f"{lib}/{doc_id}/")
    elif action == "replace":
        assert (await s.documents.get(lib, doc_id))["activeGeneration"] == 2


async def test_retry_after_source_artifact_write_reuses_frozen_plan_and_rejects_changed_same_generation_artifact(
    monkeypatch,
):
    client, s, lib, worker = environment()
    doc_id = upload(client, lib, source=save(fixture()), name="source.docx")
    _, message = s.queue.sent.pop(0)
    original = s.documents.replace
    failed = False

    async def fail_at_commit(record, *args, **kwargs):
        nonlocal failed
        if record.get("activeGeneration") == 1 and not failed:
            failed = True
            raise RuntimeError("simulated failure before conditional commit")
        return await original(record, *args, **kwargs)

    monkeypatch.setattr(s.documents, "replace", fail_at_commit)
    with pytest.raises(RuntimeError, match="simulated"):
        await worker.handle(message)
    assert (await s.documents.get(lib, doc_id))["activeGeneration"] is None
    paths = await s.blobs.list_prefix(f"{lib}/{doc_id}/text/1/")
    path = next(p for p in paths if p.endswith("/sections.json"))
    before = await s.blobs.download(path)
    await s.blobs.upload(path, b"changed", "application/json")
    with pytest.raises(ValueError, match="immutable extraction conflicts"):
        await worker.handle(message)
    assert worker.microsoft.calls == 1
    await s.blobs.upload(path, before, "application/json")
    await worker.handle(message)
    assert worker.microsoft.calls == 1 and read(client, lib, doc_id).status_code == 200


@pytest.mark.parametrize("change", ["metadata", "revision", "delete", "library"])
async def test_post_artifact_fetch_changes_refuse_the_entire_read(monkeypatch, change):
    client, s, lib, _, doc_id, doc = await ready()
    original = s.blobs.download
    mutated = False

    async def raced_download(path):
        nonlocal mutated
        data = await original(path)
        if path == doc["fullText"]["blobPath"] and not mutated:
            mutated = True
            if change == "library":
                library = await s.libraries.get(lib)
                library["status"] = "deleting"
                await s.libraries.replace(library)
            else:
                current = await s.documents.get(lib, doc_id)
                if change == "metadata":
                    current["metadataVersion"] += 1
                elif change == "revision":
                    current["contentRevision"] += 1
                else:
                    current["status"] = "deleting"
                await s.documents.replace(current)
        return data

    monkeypatch.setattr(s.blobs, "download", raced_download)
    response = read(client, lib, doc_id)
    assert response.status_code in {404, 409} and "sections" not in response.json()


async def test_identical_provider_rows_and_query_semantics_under_source_only_opt_in():
    source = save(fixture())
    receipts = []
    for profile_id in ("microsoft-indexer-layout-v1", PROFILE):
        client, s, lib, worker = setup(profile_id)
        worker.microsoft = Provider()
        upload(client, lib, source=source, name="source.docx")
        await drain(s, worker)
        rows = [
            {k: c[k] for k in ("text", "embedding", "location", "chunkIndex", "tokenCount")}
            for c in s.chunks.items.values()
        ]
        client.post(f"/v1/libraries/{lib}/search", json={"query": "condition"}, headers={"X-Agent-Id": "reader"})
        query = s.reader.queries[-1][1]
        params = [{**p, "value": "bound-library"} if p["name"] == "@libraryId" else p for p in query.parameters]
        receipts.append({"rows": digest(rows), "query": digest({"sql": query.sql, "parameters": params})})
    assert receipts[0] == receipts[1]


async def test_non_docx_profile_fails_before_provider_dispatch():
    client, s, lib, worker = environment()
    doc_id = upload(client, lib, source=b"Unsupported text", name="source.txt")
    await drain(s, worker)
    doc = await s.documents.get(lib, doc_id)
    assert doc["statusDetail"] == "source_structure_format_unsupported"
    assert worker.microsoft.calls == 0 and not s.chunks.items


async def test_compact_size_hint_is_distinct_from_rich_artifact_count():
    client, s, lib, _, doc_id, doc = await ready()
    full = doc["fullText"]
    assert full["compactSectionsEstimateScope"] == "projected_sections_only"
    assert full["compactSectionsTokenCount"] < full["tokenCount"]
    search = client.post(
        f"/v1/libraries/{lib}/search", json={"query": "condition"}, headers={"X-Agent-Id": "reader"}
    ).json()
    assert search["results"][0]["documentTokens"] == full["compactSectionsTokenCount"]
    assert search["results"][0]["documentTokenEstimateScope"] == "projected_sections_only"
    body = client.post(
        f"/v1/libraries/{lib}/documents:read?view=compact",
        json={"documents": [{"documentId": doc_id}], "maxTokens": 200000},
        headers={"X-Agent-Id": "reader"},
    ).json()
    assert body["evidence"]["documents"][0]["documentTokens"] == full["compactSectionsTokenCount"]
    assert body["evidence"]["documents"][0]["documentTokenEstimateScope"] == "projected_sections_only"
