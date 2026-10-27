"""Read a known PDF page's figures independently of semantic indexing limits."""

import copy
import io
import json

import pytest
from pypdf import PdfWriter

from tests.fakes import FakeChat
from tests.layout_fakes import FakeLayout, result
from tests.test_worker_handlers import ctx as worker_ctx
from tests.test_worker_handlers import doc, drain, replace, upload

ctx = worker_ctx


def pdf(pages=4):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


async def prepared(ctx, pages=4, library=None, layout=None):
    client, services, lib, worker = ctx
    lib = library or lib
    worker.layout = layout or FakeLayout(figure_page=pages)
    worker.vision = FakeChat({"description": "A source diagram."})
    worker.image_max_per_document = 0  # no indexed figures, source page remains readable
    document = upload(client, lib, "source.pdf", pdf(pages))
    await drain(services, worker)
    client.app.state.layout = FakeLayout(figure_page=pages)
    return document


def lookup(client, lib, document, pages=None, **fields):
    return client.post(
        f"/v1/libraries/{lib}/documents:images",
        headers={"X-Agent-Id": "agent"},
        json={"documentId": document, "pages": pages or [4], **fields},
    )


async def test_page_lookup_returns_cropped_source_figure_and_page_text_beyond_indexing_cap(ctx):
    client, services, lib, worker = ctx
    document = await prepared(ctx)
    before = copy.deepcopy(services.chunks.items)
    # Source pairs/page text are sufficient: retrieval never reads/parses the original again.
    original = doc(client, lib, document)["blobPath"]
    await services.blobs.delete(original)
    response = lookup(client, lib, document)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scope"] == "stored_pdf_page_images" and payload["pages"] == [4]
    [image] = payload["results"]
    assert image["location"] == {"page": 4, "figure": "4.1"}
    assert image["image"]["kind"] == "pdf_figure" and image["image"]["caption"].startswith("Figure 1")
    assert "expansion on page 4" in str(payload["pageSections"])
    assert all(s["location"]["page"] == 4 for s in payload["pageSections"])
    assert "imageRef" not in response.text and "blobPath" not in response.text
    assert services.chunks.items == before and doc(client, lib, document)["imageCount"] == 0
    assert not worker.vision.calls  # lookup does not describe or embed more pictures
    layout = client.app.state.layout
    assert not layout.calls
    assert lookup(client, lib, document).json()["results"][0]["imageId"] == image["imageId"]
    assert not layout.calls  # copied crops survive service operation expiration


@pytest.mark.parametrize("pages", [[0], [1, 1], [1, 2, 3, 4], [True], [2001]])
async def test_invalid_page_selection_is_refused_before_service_calls(ctx, pages):
    client, _, lib, _ = ctx
    document = await prepared(ctx)
    assert lookup(client, lib, document, pages).status_code == 400
    assert not client.app.state.layout.calls


async def test_out_of_document_bounds_stale_generation_and_missing_manifest_are_explicit(ctx):
    client, _, lib, _ = ctx
    document = await prepared(ctx)
    assert lookup(client, lib, document, [5]).json()["error"]["code"] == "invalid_page"
    assert lookup(client, lib, document, generation=99).status_code == 409
    current = await client.app.state.documents.get(lib, document)
    current["imageAssets"] = None
    await client.app.state.documents.replace(current)
    assert lookup(client, lib, document).status_code == 409


async def test_lookup_enforces_personal_binding_and_cross_library_isolation(ctx):
    client, _, lib, _ = ctx
    personal = client.post(
        "/v1/libraries", json={"kind": "personal", "name": "Private", "boundAgentId": "owner"}
    ).json()["id"]
    document = await prepared(ctx, library=personal)
    assert lookup(client, personal, document).status_code == 403
    assert lookup(client, lib, document).status_code == 404
    response = client.post(
        f"/v1/libraries/{personal}/documents:images",
        headers={"X-Agent-Id": "owner"},
        json={"documentId": document, "pages": [4]},
    )
    assert response.status_code == 200


async def test_deletion_during_link_issuance_refuses_evidence_and_creates_no_late_assets(ctx, monkeypatch):
    client, services, lib, worker = ctx
    document = await prepared(ctx)
    original = services.blobs.read_link

    async def deleting(path):
        link = await original(path)
        current = await services.documents.get(lib, document)
        current["status"] = "deleting"
        await services.documents.replace(current)
        await worker.purge_document(lib, document)
        return link

    monkeypatch.setattr(services.blobs, "read_link", deleting)
    response = lookup(client, lib, document)
    assert response.status_code == 409 and "results" not in response.json()
    assert not any(f"/{document}/" in path for path in services.blobs.blobs)
    assert not services.queue.sent


async def test_replacement_during_link_creation_refuses_stale_source(ctx, monkeypatch):
    client, services, lib, worker = ctx
    document = await prepared(ctx)
    original = services.blobs.read_link

    async def changing(path):
        link = await original(path)
        replace(client, lib, document, "new.txt", b"A new document.")
        await drain(services, worker)
        return link

    monkeypatch.setattr(services.blobs, "read_link", changing)
    assert lookup(client, lib, document).status_code == 409


async def test_per_request_figure_cap_is_disclosed_and_all_selected_page_text_is_preserved(ctx):
    client, _, lib, _ = ctx
    layout = FakeLayout(figure_page=4)

    async def many(data, first, last):
        r = result(first, last, figure_page=4)
        original = r["figures"][0]
        r["figures"] = [{**original, "id": f"4.{i}"} for i in range(12)]
        return r, "operation"

    layout.analyze_pages = many
    document = await prepared(ctx, layout=layout)
    payload = lookup(client, lib, document).json()
    assert len(payload["results"]) == 10 and payload["imagesSkipped"] == 2 and not payload["complete"]
    assert payload["pageSections"] and any("omitted" in n for n in payload["notes"])


async def test_agent_routes_identified_page_to_bound_library_and_supplies_inline_image(ctx):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from fli_demo.client import IndexClient

    client, services, lib, _ = ctx
    document = await prepared(ctx)
    index = IndexClient("http://testserver", "k", http=client)
    seen = []

    def chat(messages, tools):
        seen.append(messages)
        if len(seen) == 1:
            name = next(t["function"]["name"] for t in tools if t["function"]["name"].startswith("inspect_images_"))
            return Reply(
                None,
                [
                    ToolCall(
                        "lookup",
                        name,
                        json.dumps({"documentId": document, "pages": [4], "libraryId": "injected-wrong-library"}),
                    )
                ],
            )
        payload = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))
        image = payload["results"][0]
        assert image["documentId"] == document and image["location"]["page"] == 4
        return Reply("Here is the requested figure: ![Source diagram](fli-image:" + image["imageId"] + ")")

    turn = run_turn(chat, index, {"id": "agent", "libraryIds": [lib]}, [], "Get the diagram from page 4.")
    assert "fli-image:" in turn.answer and turn.searches[0].operation == "images"
    assert turn.searches[0].result_count == 1 and turn.searches[0].images
    assert any(isinstance(m.get("content"), list) for m in seen[-1])


async def test_lookup_reuses_the_indexed_image_id_for_the_same_source_crop(ctx):
    client, services, lib, worker = ctx
    worker.layout = FakeLayout(figure_page=2)
    worker.vision = FakeChat({"description": "GDP chart"})
    document = upload(client, lib, "source.pdf", pdf(2))
    await drain(services, worker)
    client.app.state.layout = FakeLayout(figure_page=2)
    indexed = next(c for c in services.chunks.items.values() if c.get("contentKind") == "image")
    payload = lookup(client, lib, document, [2]).json()
    assert payload["results"][0]["imageId"] == indexed["id"]


@pytest.mark.parametrize("caption", [True, False])
async def test_agent_receives_ingested_caption_with_image_without_lookup_or_rewrite(ctx, caption):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from fli_demo.client import IndexClient

    client, services, lib, worker = ctx
    worker.layout = FakeLayout(figure_page=2, caption=caption)
    worker.vision = FakeChat({"description": "A source chart"})
    document = upload(client, lib, "source.pdf", pdf(2))
    await drain(services, worker)
    index = IndexClient("http://testserver", "k", http=client)

    def forbidden(*args, **kwargs):
        raise AssertionError("normal image search must not fetch caption metadata separately")

    index.read_images = forbidden
    count = 0

    def chat(messages, tools):
        nonlocal count
        count += 1
        if count == 1:
            name = next(t["function"]["name"] for t in tools if t["function"]["name"].startswith("search_library_"))
            return Reply(None, [ToolCall("search", name, '{"query":"source chart","contentKinds":["image"]}')])
        payload = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))
        image = payload["results"][0]
        assert bool(image["image"]["caption"]) is caption
        return Reply("![Source chart](fli-image:" + image["imageId"] + ")")

    turn = run_turn(chat, index, {"id": "agent", "libraryIds": [lib]}, [], "Find the chart")
    assert count == 2 and [t.operation for t in turn.searches] == ["search"]
    assert all(c["purpose"] != "caption_revision" for c in turn.model_calls)
    assert bool(turn.searches[0].images[0]["sourceCaption"]) is caption
    assert doc(client, lib, document)["imageCount"] == 1
