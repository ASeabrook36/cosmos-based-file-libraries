"""Granular source navigation through the real API and normal agent adapter.

Storage, embedding and chat are local substitutes. These tests establish source
and protocol contracts, not ranking quality or autonomous model performance.
"""

import copy
import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from docx import Document

from fli.checkpoints import encoded
from fli.reference_format import expanded
from fli_demo.agent import Reply, ToolCall, run_turn
from fli_demo.client import IndexClient
from tests.test_modular_ingestion import setup, upload
from tests.test_worker_handlers import drain

PROFILE = "custom-granular-v1"
QUALIFIER = "This exception applies only during the trial."
CHILD_TEXT = "Keep the full audit record for seven years."
OPENING = "The procedure starts with a source review."
CLOSING = "Notify the owner when the process ends."


def source_docx(*, qualifier=QUALIFIER):
    document = Document()
    document.core_properties.author = ""
    document.add_heading("Operating procedure", level=1)
    document.add_paragraph(OPENING)
    document.add_heading("Eligibility", level=2)
    document.add_paragraph("A submission needs approval.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Term", "Condition"
    table.cell(1, 0).text = "Training"
    cell = table.cell(1, 1)
    cell.text = "The applicant must complete training."
    cell.add_paragraph("Provide the signed declaration.", style="List Number")
    cell.add_paragraph("Retain the review record.", style="List Number")
    cell.add_paragraph(qualifier)
    document.add_heading("Evidence", level=3)
    document.add_paragraph(CHILD_TEXT)
    document.add_heading("Closure", level=2)
    document.add_paragraph(CLOSING)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


async def ready(*, source=None, name="procedure.docx", view="compact"):
    client, services, library, worker = setup(PROFILE)
    document_id = upload(client, library, source if source is not None else source_docx(), name)
    await drain(services, worker)
    document = await services.documents.get(library, document_id)
    assert document["status"] == "ready", document.get("statusDetail")
    index = IndexClient("http://testserver", "k", http=client, evidence_view=view)
    pin = {
        "documentId": document_id,
        "generation": document["activeGeneration"],
        "contentRevision": document["contentRevision"],
    }
    return SimpleNamespace(client=client, s=services, lib=library, worker=worker, doc=document, index=index, pin=pin)


def read(env, selector=None, **overrides):
    body = {"mode": "full", "documents": [env.pin], "maxTokens": 200000}
    body.update(selector or {})
    body.update(overrides)
    response = env.index.read_documents(env.lib, "reader", **body)
    return expanded(response["evidence"]) if env.index.evidence_view == "references" else response["evidence"]


def request(env, body, *, library=None, agent="reader"):
    return env.client.post(
        f"/v1/libraries/{library or env.lib}/documents:read?view=compact&selectors=handles",
        json={"maxTokens": 200000, **body},
        headers={"X-Agent-Id": agent},
    )


def outline(env, entry=None, **overrides):
    selector = entry["outlineSelector"] if entry else {"mode": "outline", "documents": [env.pin]}
    return read(env, selector, **overrides)["documents"][0]


def text_of(evidence):
    return "\n".join(s["text"] for d in evidence["documents"] for s in d.get("sections", []))


def title(entry):
    return entry.get("heading") or entry.get("title")


def child_named(env, entry, name):
    rows = outline(env, entry)["outline"]
    return next(row for row in rows if title(row) == name)


def hierarchy(env):
    operating = child_named(env, None, "Operating procedure")
    eligibility = child_named(env, operating, "Eligibility")
    children = outline(env, eligibility)["outline"]
    table = next(row for row in children if row["navigationKind"] == "table")
    cells = outline(env, table)["outline"]
    cell = next(row for row in cells if QUALIFIER in text_of(read(env, row["readSelector"])))
    paragraphs = outline(env, cell)["outline"]
    qualifier = next(row for row in paragraphs if QUALIFIER in text_of(read(env, row["readSelector"])))
    return operating, eligibility, table, cell, qualifier


@pytest.mark.parametrize("view", ["compact", "references"])
async def test_docx_paragraph_cell_section_subtree_and_document_preserve_source_and_snapshot(view):
    env = await ready(view=view)
    operating, eligibility, table, cell, qualifier = hierarchy(env)
    assert eligibility["parentNodeId"] == operating["navigationNodeId"]
    assert table["parentNodeId"] == eligibility["navigationNodeId"]
    assert cell["parentNodeId"] == table["navigationNodeId"]
    assert qualifier["parentNodeId"] == cell["navigationNodeId"]
    assert qualifier["containingSectionId"] == eligibility["navigationNodeId"]

    paragraph = read(env, qualifier["readSelector"])
    paragraph_text = text_of(paragraph)
    assert QUALIFIER in paragraph_text and "signed declaration" not in paragraph_text
    assert len(paragraph["documents"][0]["sections"]) == 1
    assert paragraph["selectionComplete"] and not paragraph["documentComplete"]
    section = paragraph["documents"][0]["sections"][0]
    assert "procedure.docx" in section["citation"]
    assert "blobPath" not in json.dumps(paragraph) and "embedding" not in json.dumps(paragraph)

    cell_text = text_of(read(env, cell["readSelector"]))
    assert cell_text.index("signed declaration") < cell_text.index("review record") < cell_text.index(QUALIFIER)
    assert "Training" not in cell_text and CHILD_TEXT not in cell_text
    own = text_of(read(env, eligibility["readSelector"]))
    assert QUALIFIER in own and "A submission needs approval." in own
    assert CHILD_TEXT not in own and OPENING not in own and CLOSING not in own
    subtree = text_of(read(env, eligibility["subtreeSelector"]))
    assert QUALIFIER in subtree and CHILD_TEXT in subtree and CLOSING not in subtree
    containing = text_of(read(env, qualifier["containingSectionSelector"]))
    assert containing == subtree
    full = read(env, qualifier["fullSelector"])
    assert full["complete"] and all(part in text_of(full) for part in (OPENING, QUALIFIER, CHILD_TEXT, CLOSING))
    assert all(key not in json.dumps(full) for key in ('"sourceNavigation"', '"runs"', '"numberingDefinitions"'))
    assert qualifier["containingSectionScope"] == "complete_authored_subtree"
    for entry in (operating, eligibility, table, cell, qualifier, section):
        for key in ("readSelector", "outlineSelector", "containingSectionSelector", "fullSelector"):
            for selection in entry[key]["documents"]:
                assert all(selection[k] == v for k, v in env.pin.items())


async def test_outline_paginates_only_immediate_children_and_keeps_body_out_of_outline():
    env = await ready()
    operating, eligibility, _, _, _ = hierarchy(env)
    full = outline(env, operating)
    rows, after = [], 0
    while True:
        page = outline(env, operating, sectionLimit=1, afterSection=after)
        assert len(page["outline"]) <= 1
        rows.extend(page["outline"])
        if not page["hasMore"]:
            assert page["nextAfterSection"] is None
            break
        assert page["nextAfterSection"] > after
        after = page["nextAfterSection"]
        assert after < 20
    assert [row["navigationNodeId"] for row in rows] == [row["navigationNodeId"] for row in full["outline"]]
    assert all(row["parentNodeId"] == operating["navigationNodeId"] and "text" not in row for row in rows)
    assert eligibility["navigationNodeId"] in {row["navigationNodeId"] for row in rows}
    assert "Evidence" not in {title(row) for row in rows}
    assert QUALIFIER not in json.dumps(full)
    assert all(type(row["sectionTokens"]) is int and row["subtreeTokens"] >= row["sectionTokens"] >= 0 for row in rows)
    assert all(
        row["sizeEstimateScope"] == "sum_of_projected_source_atoms_excludes_response_and_selector_overhead"
        for row in rows
    )
    assert all(row["sizeEstimateTokenizer"] for row in rows)


async def test_verified_passage_expands_only_to_its_original_paragraph():
    env = await ready()
    # A deterministic local reader selects a real stored paragraph passage. This
    # tests mapping and delivery rather than Cosmos ranking or model relevance.
    row = next(row for row in env.s.chunks.items.values() if QUALIFIER in row["text"])
    env.s.reader.order = [row["id"]]
    response = env.index.search(env.lib, "reader", "trial exception", document=env.pin)
    hit = response["evidence"]["results"][0]
    assert hit["sourceScope"]["sourceAtomId"]
    assert hit["readCapabilities"]["evidenceExpansion"] == "selected_handle"
    assert hit["passageToSourceMapping"] == "recorded_containing_source_atom"
    assert hit["passageMappingVerification"] == "publication_bound; revalidated_on_expansion"
    assert (
        hit["readCapabilities"]["sourceLocationPrecision"]
        == "recorded_containing_source_atom_not_exact_passage_offsets"
    )
    selected = read(env, hit["readSelector"])
    assert QUALIFIER in text_of(selected) and "signed declaration" not in text_of(selected)
    assert len(selected["documents"][0]["sections"]) == 1
    assert selected["documents"][0]["sections"][0]["location"]["sourceAtomId"] == hit["sourceScope"]["sourceAtomId"]


@pytest.mark.parametrize("damage", ["different-existing-atom", "different-coordinate"])
async def test_passage_expansion_revalidates_recorded_atom_and_coordinates_before_returning_source(damage):
    env = await ready()
    row = next(row for row in env.s.chunks.items.values() if QUALIFIER in row["text"])
    if damage == "different-existing-atom":
        other = next(
            value
            for value in env.s.chunks.items.values()
            if value["location"]["sourceAtomId"] != row["location"]["sourceAtomId"]
        )
        row["location"]["sourceAtomId"] = other["location"]["sourceAtomId"]
    else:
        row["location"]["paragraph"] += 1
    env.s.reader.order = [row["id"]]
    hit = env.index.search(env.lib, "reader", "trial exception", document=env.pin)["evidence"]["results"][0]
    assert hit["passageToSourceMapping"] == "recorded_containing_source_atom"
    assert hit["passageMappingVerification"] == "publication_bound; revalidated_on_expansion"
    result = request(env, hit["readSelector"])
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "source_scope_unavailable"
    assert "sections" not in result.json()
    assert all(text not in result.text for text in (OPENING, QUALIFIER, CHILD_TEXT, CLOSING))


async def test_unavailable_passage_mapping_discloses_outline_recovery_without_guessing_paragraph_scope():
    env = await ready()
    row = next(row for row in env.s.chunks.items.values() if QUALIFIER in row["text"])
    # A legacy/damaged location may still name a paragraph. The granular profile
    # requires its verified atom binding and must not silently use that fallback.
    row["location"].pop("sourceAtomId")
    env.s.reader.order = [row["id"]]
    hit = env.index.search(env.lib, "reader", "trial exception", document=env.pin)["evidence"]["results"][0]
    assert hit["passageToSourceMapping"] == "unavailable"
    assert hit["readCapabilities"]["evidenceExpansion"] == "outline_required" and "readSelector" not in hit
    result = request(env, {"mode": "selected", "documents": [{**env.pin, "evidenceIds": [hit["evidenceId"]]}]})
    assert result.status_code == 409 and result.json()["error"]["code"] == "source_scope_unavailable"
    assert QUALIFIER not in result.text
    recovered = read(env, hit["outlineSelector"])
    assert recovered["documents"][0]["outline"]


async def test_markdown_authored_hierarchy_remains_selectable_with_exact_source_text():
    source = b"# Report\n\nOpening.\n\n## Findings\n\nRainfall reduced salinity.\n\n### Limits\n\nOnly two sites.\n"
    env = await ready(source=source, name="report.md")
    report = child_named(env, None, "Report")
    findings = child_named(env, report, "Findings")
    limits = child_named(env, findings, "Limits")
    own = text_of(read(env, findings["readSelector"]))
    assert "Rainfall reduced salinity." in own and "Only two sites." not in own
    subtree = text_of(read(env, findings["subtreeSelector"]))
    assert "Rainfall reduced salinity." in subtree and "Only two sites." in subtree
    assert limits["parentNodeId"] == findings["navigationNodeId"]
    full = read(env)
    assert "".join(s["text"] for s in full["documents"][0]["sections"]).encode() == source


async def test_explicit_markdown_chapter_has_distinct_own_content_and_subtree_selectors():
    source = b"# Chapter 1: Scope\n\nOwn chapter content.\n\n## Evidence\n\nChild evidence content.\n"
    env = await ready(source=source, name="chapters.md")
    chapter = child_named(env, None, "Chapter 1: Scope")
    assert chapter["navigationKind"] == "chapter"
    assert chapter["heading"] == "Chapter 1: Scope"
    assert chapter["readSelector"]["documents"][0]["nodeScope"] == "section"
    own = text_of(read(env, chapter["readSelector"]))
    assert "Own chapter content." in own and "Child evidence content." not in own
    subtree = text_of(read(env, chapter["subtreeSelector"]))
    assert "Own chapter content." in subtree and "Child evidence content." in subtree
    assert child_named(env, chapter, "Evidence")["parentNodeId"] == chapter["navigationNodeId"]


async def test_markdown_source_block_citation_uses_concise_verified_character_extent():
    source = b"# Scope\n\nRetain this exact source block.\n"
    env = await ready(source=source, name="source.md")
    heading = child_named(env, None, "Scope")
    block = outline(env, heading)["outline"][0]
    section = read(env, block["readSelector"])["documents"][0]["sections"][0]
    location = section["location"]
    assert location["originalCoordinate"]["kind"] == "sourceBlock"
    assert section["citation"] == (
        f"source.md (source characters {location['charStart']}–{location['charEnd']} (end exclusive))"
    )
    assert all(token not in section["citation"] for token in ("sourceAtomId", "nodeId", "{", "}"))
    assert source.decode()[location["charStart"] : location["charEnd"]] == section["text"]


@pytest.mark.parametrize("file_type", ["docx", "md"])
async def test_pre_heading_content_discloses_document_root_instead_of_inventing_authored_section(file_type):
    if file_type == "md":
        source = b"Prefatory source text.\n\n# Scope\n\nAuthored section text.\n"
    else:
        document = Document()
        document.core_properties.author = ""
        document.add_paragraph("Prefatory source text.")
        document.add_heading("Scope", level=1)
        document.add_paragraph("Authored section text.")
        stream = io.BytesIO()
        document.save(stream)
        source = stream.getvalue()
    env = await ready(source=source, name="preamble." + file_type)
    entry = next(row for row in outline(env)["outline"] if row["navigationKind"] in {"paragraph", "sourceBlock"})
    assert entry["containingSectionScope"] == "document_root_no_containing_authored_section"
    assert "Prefatory source text." in text_of(read(env, entry["readSelector"]))
    assert "Authored section text." not in text_of(read(env, entry["readSelector"]))
    expanded = text_of(read(env, entry["containingSectionSelector"]))
    assert "Authored section text." in expanded and "Prefatory source text." in expanded
    assert expanded == text_of(read(env, entry["fullSelector"]))


async def test_pages_and_unpinned_hierarchy_requests_fail_without_returning_source():
    env = await ready()
    _, eligibility, _, _, _ = hierarchy(env)
    page = request(env, {"mode": "selected", "documents": [{**env.pin, "pages": [1]}]})
    assert page.status_code == 409 and page.json()["error"]["code"] == "source_scope_unavailable"
    for key in ("generation", "contentRevision"):
        body = copy.deepcopy(eligibility["readSelector"])
        body["documents"][0].pop(key)
        rejected = request(env, body)
        assert rejected.status_code == 400 and rejected.json()["error"]["code"] == "invalid_request"
        body = copy.deepcopy(eligibility["outlineSelector"])
        body["documents"][0].pop(key)
        assert request(env, body).status_code == 400
    wrong_mode = {"mode": "full", "documents": [{**env.pin, "outlineNodeId": eligibility["navigationNodeId"]}]}
    assert request(env, wrong_mode).status_code == 400


@pytest.mark.parametrize("change", ["revision", "delete", "foreign", "personal"])
async def test_pinned_navigation_refuses_stale_deleted_foreign_or_unauthorized_source(change):
    env = await ready()
    _, eligibility, _, _, _ = hierarchy(env)
    body = eligibility["subtreeSelector"]
    library = env.lib
    if change == "revision":
        document = await env.s.documents.get(env.lib, env.doc["id"])
        document["contentRevision"] += 1
        await env.s.documents.replace(document)
        expected = (409, "document_changed")
    elif change == "delete":
        env.index.delete_document(env.lib, env.doc["id"])
        expected = (404, "document_not_found")
    elif change == "foreign":
        library = env.index.create_library("shared", "Separate scope")["id"]
        expected = (404, "document_not_found")
    else:
        record = await env.s.libraries.get(env.lib)
        record.update(kind="personal", boundAgentId="owner")
        await env.s.libraries.replace(record)
        expected = (403, "library_not_bound_to_agent")
    result = request(env, body, library=library)
    assert (result.status_code, result.json()["error"]["code"]) == expected
    assert QUALIFIER not in result.text and "sections" not in result.json()


@pytest.mark.parametrize("change", ["metadata", "revision", "delete", "library"])
async def test_navigation_rechecks_lifecycle_after_reading_source_and_refuses_atomic_batch(monkeypatch, change):
    env = await ready()
    second = upload(env.client, env.lib, source_docx(), "second.docx")
    await drain(env.s, env.worker)
    second_doc = await env.s.documents.get(env.lib, second)
    original = env.s.blobs.download
    changed = False

    async def download(path):
        nonlocal changed
        data = await original(path)
        if path == second_doc["fullText"]["blobPath"] and not changed:
            changed = True
            if change == "library":
                library = await env.s.libraries.get(env.lib)
                library["status"] = "deleting"
                await env.s.libraries.replace(library)
            else:
                document = await env.s.documents.get(env.lib, env.doc["id"])
                if change == "metadata":
                    document["metadataVersion"] += 1
                elif change == "revision":
                    document["contentRevision"] += 1
                else:
                    document["status"] = "deleting"
                await env.s.documents.replace(document)
        return data

    monkeypatch.setattr(env.s.blobs, "download", download)
    response = request(env, {"mode": "outline", "documents": [env.pin, {"documentId": second}]})
    assert changed and response.status_code in {404, 409}
    assert "outline" not in response.json() and QUALIFIER not in response.text


@pytest.mark.parametrize("mode", ["full", "outline", "selected"])
async def test_over_budget_multi_document_navigation_returns_no_partial_source(mode):
    env = await ready()
    second = upload(env.client, env.lib, source_docx(), "second.docx")
    await drain(env.s, env.worker)
    second_doc = await env.s.documents.get(env.lib, second)
    second_pin = {
        "documentId": second,
        "generation": second_doc["activeGeneration"],
        "contentRevision": second_doc["contentRevision"],
    }
    selections = [env.pin, second_pin]
    if mode == "selected":
        first = child_named(env, None, "Operating procedure")
        second_outline = read(env, {"mode": "outline", "documents": [second_pin]})
        other = next(row for row in second_outline["documents"][0]["outline"] if title(row) == "Operating procedure")
        selections = [first["subtreeSelector"]["documents"][0], other["subtreeSelector"]["documents"][0]]
    result = request(env, {"mode": mode, "documents": selections, "maxTokens": 1})
    assert result.status_code == 200
    evidence = result.json()["evidence"]
    assert evidence["disposition"] == "budget_exceeded" and not evidence["complete"]
    assert len(evidence["documents"]) == 2
    assert all("sections" not in d and "outline" not in d for d in evidence["documents"])
    assert QUALIFIER not in json.dumps(evidence) and not result.json()["attachments"]


@pytest.mark.parametrize("damage", ["bytes", "bound-source", "missing-descriptor"])
async def test_granular_source_artifact_tamper_never_falls_back_to_passages(damage):
    env = await ready()
    doc = await env.s.documents.get(env.lib, env.doc["id"])
    path = doc["fullText"]["blobPath"]
    if damage == "bytes":
        await env.s.blobs.upload(path, b"broken", "application/json")
    elif damage == "missing-descriptor":
        doc.pop("fullText")
    else:
        sections = json.loads(await env.s.blobs.download(path))
        sections[0]["text"] = "Forged source with an updated outer hash."
        data = encoded(sections)
        await env.s.blobs.upload(path, data, "application/json")
        doc["fullText"]["sha256"] = hashlib.sha256(data).hexdigest()
    await env.s.documents.replace(doc)
    result = request(env, {"mode": "full", "documents": [env.pin]})
    assert result.status_code == 409
    assert "sections" not in result.json() and "Forged source" not in result.text


@pytest.mark.parametrize("field", ["sourceStructure", "paragraphs", "paragraph"])
@pytest.mark.parametrize("invalid", [None, [], "invalid container"])
async def test_malformed_source_structure_containers_refuse_without_server_error(field, invalid):
    env = await ready()
    document = await env.s.documents.get(env.lib, env.doc["id"])
    path = document["fullText"]["blobPath"]
    sections = json.loads(await env.s.blobs.download(path))
    if field == "sourceStructure":
        sections[0][field] = invalid
    elif field == "paragraphs":
        sections[0]["sourceStructure"][field] = invalid
    else:
        sections[0]["sourceStructure"]["paragraphs"] = [invalid]
    data = encoded(sections)
    await env.s.blobs.upload(path, data, "application/json")
    document["fullText"]["sha256"] = hashlib.sha256(data).hexdigest()
    await env.s.documents.replace(document)
    result = request(env, {"mode": "outline", "documents": [env.pin]})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "document_text_incomplete"
    assert "outline" not in result.json() and "sections" not in result.json()


@pytest.mark.parametrize("field", ["ingestionJob", "profile"])
@pytest.mark.parametrize("invalid", [None, [], "invalid binding container"])
async def test_malformed_document_job_or_profile_refuses_without_server_error(field, invalid):
    env = await ready()
    document = await env.s.documents.get(env.lib, env.doc["id"])
    if field == "ingestionJob":
        document[field] = invalid
    else:
        document["ingestionJob"][field] = invalid
    await env.s.documents.replace(document)
    result = request(env, {"mode": "outline", "documents": [env.pin]})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "document_text_incomplete"
    assert "outline" not in result.json() and "sections" not in result.json()


async def test_pending_replacement_retains_old_navigation_then_stales_pins_and_deletion_cleans_owned_artifacts():
    env = await ready()
    _, _, _, _, qualifier = hierarchy(env)
    selector = qualifier["readSelector"]
    assert (
        env.client.patch(f"/v1/libraries/{env.lib}/documents/{env.doc['id']}", json={"author": "Edited"}).status_code
        == 200
    )
    assert QUALIFIER in text_of(read(env, selector))
    replacement = env.index.replace_document(
        env.lib, env.doc["id"], "revised.docx", source_docx(qualifier="The trial has ended.")
    )
    assert replacement
    assert QUALIFIER in text_of(read(env, selector))
    await drain(env.s, env.worker)
    result = request(env, selector)
    assert result.status_code == 409 and QUALIFIER not in result.text
    current = env.index.read_documents(env.lib, "reader", documents=[{"documentId": env.doc["id"]}], maxTokens=200000)
    assert "The trial has ended." in text_of(current["evidence"])
    env.index.delete_document(env.lib, env.doc["id"])
    await drain(env.s, env.worker)
    assert await env.s.documents.get(env.lib, env.doc["id"]) is None
    assert not await env.s.blobs.list_prefix(f"{env.lib}/{env.doc['id']}/")
    assert not env.s.chunks.items


async def test_retry_reuses_immutable_source_artifact_without_duplicate_chunks(monkeypatch):
    client, services, library, worker = setup(PROFILE)
    document_id = upload(client, library, source_docx(), "procedure.docx")
    _, message = services.queue.sent.pop(0)
    original = services.documents.replace
    failed = False

    async def fail_before_publication(document, *args, **kwargs):
        nonlocal failed
        if document.get("activeGeneration") == 1 and not failed:
            failed = True
            raise RuntimeError("simulated source publication interruption")
        return await original(document, *args, **kwargs)

    monkeypatch.setattr(services.documents, "replace", fail_before_publication)
    with pytest.raises(RuntimeError, match="publication interruption"):
        await worker.handle(message)
    assert (await services.documents.get(library, document_id))["activeGeneration"] is None
    paths = await services.blobs.list_prefix(f"{library}/{document_id}/text/1/")
    sections_path = next(path for path in paths if path.endswith("/sections.json"))
    before = await services.blobs.download(sections_path)
    chunks = copy.deepcopy(services.chunks.items)
    await worker.handle(message)
    assert services.chunks.items == chunks
    assert await services.blobs.download(sections_path) == before
    assert (await services.documents.get(library, document_id))["status"] == "ready"
    index = IndexClient("http://testserver", "k", http=client)
    result = index.read_documents(library, "reader", documents=[{"documentId": document_id}], maxTokens=200000)
    assert QUALIFIER in text_of(result["evidence"]) and result["evidence"]["complete"]


async def test_normal_agent_uses_returned_granular_handles_and_platform_library_binding():
    env = await ready()
    state, calls = {}, 0

    def chat(messages, tools):
        nonlocal calls
        calls += 1
        names = {tool["function"]["name"].split("_library_")[0]: tool["function"]["name"] for tool in tools}

        def invoke(selector):
            return Reply(None, [ToolCall(str(calls), names["read_documents"], json.dumps(selector))])

        if calls == 1:
            return Reply(None, [ToolCall("discover", names["find_documents"], '{"fileNames":["procedure.docx"]}')])
        evidence = json.loads(messages[-1]["content"])
        assert "error" not in evidence, evidence
        if calls == 2:
            document = evidence["documents"][0]
            assert document["documentSelector"] == env.pin
            return invoke({**document["outlineSelector"], "libraryId": "agent-cannot-choose-this"})
        if calls == 3:
            entry = next(row for row in evidence["documents"][0]["outline"] if title(row) == "Operating procedure")
            return invoke(entry["outlineSelector"])
        if calls == 4:
            state["section"] = next(row for row in evidence["documents"][0]["outline"] if title(row) == "Eligibility")
            return invoke(state["section"]["outlineSelector"])
        if calls == 5:
            entry = next(row for row in evidence["documents"][0]["outline"] if row["navigationKind"] == "table")
            return invoke(entry["outlineSelector"])
        if calls == 6:
            cells = evidence["documents"][0]["outline"]
            # The target is the fourth cell in authored table order, selected by
            # the returned outline's original coordinates rather than a guessed ID.
            entry = cells[-1]
            return invoke(entry["outlineSelector"])
        if calls == 7:
            state["paragraph"] = evidence["documents"][0]["outline"][-1]
            return invoke(state["paragraph"]["readSelector"])
        if calls == 8:
            assert QUALIFIER in text_of(evidence) and "signed declaration" not in text_of(evidence)
            assert "procedure.docx" in evidence["documents"][0]["sections"][0]["citation"]
            return invoke(state["section"]["readSelector"])
        if calls == 9:
            assert QUALIFIER in text_of(evidence) and CHILD_TEXT not in text_of(evidence)
            return invoke(state["paragraph"]["containingSectionSelector"])
        if calls == 10:
            assert QUALIFIER in text_of(evidence) and CHILD_TEXT in text_of(evidence)
            return invoke(state["paragraph"]["fullSelector"])
        assert calls == 11 and evidence["complete"]
        assert all(part in text_of(evidence) for part in (OPENING, QUALIFIER, CHILD_TEXT, CLOSING))
        return Reply(
            "The exception is limited to the trial, and the audit record is retained for seven years. (procedure.docx)"
        )

    turn = run_turn(
        chat,
        env.index,
        {"id": "reader", "libraryIds": [env.lib]},
        [],
        "Inspect the trial qualifier, its containing section and subsection, then confirm against the whole procedure.",
        context_token_budget=200000,
        image_inputs=False,
    )
    assert calls == 11 and len(turn.searches) == 10
    assert all(trace.library_id == env.lib and trace.error is None for trace in turn.searches)
