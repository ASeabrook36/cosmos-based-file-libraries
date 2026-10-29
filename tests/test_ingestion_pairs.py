"""Partial pair recovery, shared sources, deletion receipts and normal API backstops."""

from uuid import uuid4

from fli.api.experiments import stable_id
from tests.fakes import api
from tests.test_modular_ingestion import SOURCE, MicrosoftFake, setup
from tests.test_worker_handlers import drain


def create(client, experiment_id=None, comparison="complete-strategy"):
    body = {
        "experimentId": experiment_id or str(uuid4()),
        "name": "Matched reports",
        "description": "Reports",
        "comparison": comparison,
    }
    if comparison == "controlled-component":
        body["component"] = "chunk-boundaries"
    response = client.post("/v1/experiments", json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def upload(client, exp_id, source_id=None, source=SOURCE):
    source_id = source_id or str(uuid4())
    response = client.post(f"/v1/experiments/{exp_id}/sources/{source_id}", files={"file": ("report.md", source)})
    return response, source_id


async def test_pair_creation_is_idempotent_and_uploads_identical_bytes_to_two_normal_libraries():
    client, s, _, worker = setup()
    manifest, body = create(client)
    assert client.post("/v1/experiments", json=body).json()["members"] == manifest["members"]
    assert len(await s.libraries.list(kind="shared", status="active")) == 3  # setup + two ordinary members
    response, source_id = upload(client, str(body["experimentId"]))
    assert response.status_code == 202, response.text
    members = response.json()["members"]
    assert {x["status"] for x in members.values()} == {"queued"}
    docs = [await s.documents.get(x["libraryId"], x["documentId"]) for x in members.values()]
    assert {d["sourceId"] for d in docs} == {source_id}
    assert len({d["sha256"] for d in docs}) == 1
    assert all([await s.blobs.download(d["blobPath"]) == SOURCE for d in docs])
    worker.microsoft = MicrosoftFake()
    await drain(s, worker)
    assert all(
        [(await s.documents.get(x["libraryId"], x["documentId"]))["status"] == "ready" for x in members.values()]
    )
    response, _ = upload(client, body["experimentId"], source_id)
    assert {x["status"] for x in response.json()["members"].values()} == {"ready"}
    assert all([len(await s.documents.list(x["libraryId"], status=None, facet=None)) == 1 for x in members.values()])


async def test_partial_creation_resumes_without_duplicate_pairs(monkeypatch):
    client, s = api()
    real = s.libraries.create
    calls = 0

    async def flaky(record):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("service unavailable")
        return await real(record)

    monkeypatch.setattr(s.libraries, "create", flaky)
    manifest, body = create(client)
    assert manifest["members"]["custom"]["state"] == "created"
    assert manifest["members"]["microsoft"]["state"] == "creating"
    resumed = client.post("/v1/experiments", json=body)
    assert resumed.status_code == 201
    assert {x["state"] for x in resumed.json()["members"].values()} == {"created"}
    assert len(await s.libraries.list(kind="shared", status="active")) == 2
    assert client.post("/v1/experiments", json=body | {"name": "Different"}).status_code == 409


async def test_one_side_queue_failure_is_visible_and_retry_resumes_same_documents(monkeypatch):
    client, s = api()
    _, body = create(client)
    real = s.queue.send
    count = 0

    async def flaky(message, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("queue unavailable")
        await real(message, **kwargs)

    monkeypatch.setattr(s.queue, "send", flaky)
    response, source_id = upload(client, body["experimentId"])
    first = response.json()["members"]
    assert first["microsoft"]["status"] == "enqueue_or_storage_failed"
    response, _ = upload(client, body["experimentId"], source_id)
    second = response.json()["members"]
    assert all(first[k]["documentId"] == second[k]["documentId"] for k in first)
    assert {x["status"] for x in second.values()} == {"queued"}
    assert upload(client, body["experimentId"], source_id, b"different")[0].status_code == 409


async def test_retry_never_resurrects_deleted_pair_member_or_document():
    client, s, _, worker = setup()
    manifest, body = create(client)
    response, source_id = upload(client, body["experimentId"])
    members = response.json()["members"]
    await drain(s, worker)
    custom = members["custom"]
    assert client.delete(f"/v1/libraries/{custom['libraryId']}/documents/{custom['documentId']}").status_code == 202
    await drain(s, worker)
    result, _ = upload(client, body["experimentId"], source_id)
    assert result.json()["members"]["custom"]["status"] == "deleted"
    assert await s.documents.get(custom["libraryId"], custom["documentId"]) is None
    microsoft_id = manifest["members"]["microsoft"]["libraryId"]
    assert client.delete(f"/v1/libraries/{microsoft_id}").status_code == 202
    await drain(s, worker)
    result = client.post("/v1/experiments", json=body)
    assert result.json()["members"]["microsoft"]["state"] == "deleted"
    assert await s.libraries.get(microsoft_id) is None


def test_controlled_comparison_records_chunk_settings_and_remaining_confounds():
    client, _ = api()
    result, _ = create(client, comparison="controlled-component")
    recipes = [m["ingestionProfile"] for m in result["members"].values()]
    assert all(p["chunking"]["targetTokens"] == 500 and p["chunking"]["overlapTokens"] == 50 for p in recipes)
    assert recipes[0]["chunking"]["algorithm"] != recipes[1]["chunking"]["algorithm"]
    assert all(p["extraction"] == "prebuilt-layout" for p in recipes)
    assert all(p["enrichment"]["adapter"] is None for p in recipes)
    assert "disabled" in result["controls"]["attribution"]


async def test_personal_microsoft_library_keeps_all_retrieval_backstops():
    client, s, _, worker = setup()
    lib = client.post(
        "/v1/libraries",
        json={
            "name": "Private",
            "kind": "personal",
            "boundAgentId": "owner",
            "ingestionProfile": "microsoft-indexer-layout-v1",
        },
    ).json()["id"]
    from tests.test_modular_ingestion import upload as upload_one

    doc_id = upload_one(client, lib)
    await drain(s, worker)
    for operation, body in [
        ("/search", {"query": "rainfall"}),
        ("/documents:discover", {}),
        ("/documents:read", {"documents": [{"documentId": doc_id}]}),
        ("/documents:images", {"documentId": doc_id, "pages": [1]}),
    ]:
        response = client.post(f"/v1/libraries/{lib}{operation}", json=body, headers={"X-Agent-Id": "wrong"})
        assert response.status_code == 403, response.text
    assert client.get(f"/v1/libraries/{lib}/tools", headers={"X-Agent-Id": "wrong"}).status_code == 403
    assert client.get(f"/v1/libraries/{lib}/metrics", headers={"X-Agent-Id": "wrong"}).status_code == 403


def test_pair_member_ids_are_scope_specific():
    experiment_id = str(uuid4())
    assert stable_id(experiment_id, "custom") != stable_id(experiment_id, "microsoft")


async def test_experiment_uuid_cannot_alias_an_existing_library_partition():
    client, s, lib, worker = setup()
    _, body = create(client, experiment_id=lib)
    assert len(await s.documents.list(lib, status=None, facet=None)) == 0
    assert (await s.documents.get("experiment:" + body["experimentId"], "experiment"))[
        "type"
    ] == "ingestion-experiment-v1"


async def test_controlled_pair_shares_extraction_and_model_but_uses_independent_chunking():
    client, s, _, worker = setup()
    _, body = create(client, comparison="controlled-component")
    response, _ = upload(client, body["experimentId"])
    await drain(s, worker)
    custom, microsoft = [
        await s.documents.get(member["libraryId"], member["documentId"])
        for member in response.json()["members"].values()
    ]
    assert custom["status"] == microsoft["status"] == "ready"
    assert (
        custom["extraction"]["method"] == microsoft["extraction"]["method"]
        or "layout" in custom["extraction"]["method"]
    )
    assert custom["structure"] is None and microsoft["structure"] is None
    import json

    custom_sections = json.loads(await s.blobs.download(custom["fullText"]["blobPath"]))
    microsoft_sections = json.loads(await s.blobs.download(microsoft["fullText"]["blobPath"]))
    assert custom_sections[0]["text"] == microsoft_sections[0]["text"]
    assert worker.embedder.calls == 1  # Python embeds its chunks; Microsoft supplies vectors.


async def test_complete_pair_has_a_shared_office_source_format():
    from tests.test_modular_ingestion import structured_docx

    client, s, _, worker = setup()
    _, body = create(client)
    response = client.post(
        f"/v1/experiments/{body['experimentId']}/sources/{uuid4()}", files={"file": ("report.docx", structured_docx())}
    )
    assert response.status_code == 202, response.text
    await drain(s, worker)
    docs = [
        await s.documents.get(member["libraryId"], member["documentId"])
        for member in response.json()["members"].values()
    ]
    assert all(doc["status"] == "ready" for doc in docs)
    assert docs[0]["sha256"] == docs[1]["sha256"]
    assert docs[0]["structure"] and docs[1]["structure"] is None


async def test_partial_pair_keeps_original_profiles_across_configuration_change(monkeypatch):
    from dataclasses import replace

    client, s = api(AOAI_EMBEDDING_MODEL_REVISION="original-model", FLI_CODE_REVISION="original-code")
    real = s.libraries.create

    async def flaky(record):
        if record["comparisonMember"] == "microsoft":
            raise RuntimeError("unavailable")
        return await real(record)

    monkeypatch.setattr(s.libraries, "create", flaky)
    _, body = create(client)
    client.app.state.settings = replace(
        client.app.state.settings, aoai_embedding_model_revision="changed-model", ingestion_code_revision="changed-code"
    )
    monkeypatch.setattr(s.libraries, "create", real)
    resumed = client.post("/v1/experiments", json=body).json()
    for member in resumed["members"].values():
        assert member["ingestionProfile"]["embedding"]["modelRevision"] == "original-model"
        assert member["ingestionProfile"]["codeRevision"] == "original-code"
