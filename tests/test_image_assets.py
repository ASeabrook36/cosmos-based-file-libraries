"""Caption/image pairs are prepared once, published together, and read without analysis."""

import json

from fli.image_assets import load_assets
from tests.fakes import FakeChat
from tests.layout_fakes import FakeLayout, result
from tests.test_images import office
from tests.test_page_images import lookup, pdf
from tests.test_worker_handlers import ctx as worker_ctx
from tests.test_worker_handlers import doc, drain, upload

ctx = worker_ctx


async def test_all_source_figures_and_captions_are_available_before_semantic_enrichment(ctx):
    client, services, lib, worker = ctx

    class Many(FakeLayout):
        async def analyze_pages(self, data, first, last):
            self.calls.append((first, last))
            r = result(first, last, figure_page=1)
            r["figures"] = [{**r["figures"][0], "id": f"1.{i}"} for i in range(4)]
            return r, "operation"

    worker.layout = Many()
    worker.vision = FakeChat({"description": "A diagram"})
    worker.image_max_per_document = 1
    document = upload(client, lib, "source.pdf", pdf(2))
    await worker.handle(services.queue.sent.pop(0)[1])
    current = doc(client, lib, document)
    assert current["sourceImageCount"] == 4 and current["imageCount"] == 0 and not worker.vision.calls
    before = dict(services.blobs.blobs)
    response = lookup(client, lib, document, [1]).json()
    assert len(response["results"]) == 4 and all(r["image"]["caption"] for r in response["results"])
    assert services.blobs.blobs == before and worker.layout.calls == [(1, 2)]
    await drain(services, worker)
    current = doc(client, lib, document)
    assert current["imageCount"] == 1 and current["sourceImageCount"] == 4 and current["imagesSkipped"] == 3
    assert len(worker.vision.calls) == 1
    image = next(c for c in services.chunks.items.values() if c["contentKind"] == "image")
    assert (
        response["results"][0]["imageId"] == image["id"]
        and image["imageMeta"]["caption"] == response["results"][0]["image"]["caption"]
    )
    assert "Source caption:" in image["text"]
    full = client.post(
        f"/v1/libraries/{lib}/documents:read",
        headers={"X-Agent-Id": "agent"},
        json={"documents": [{"documentId": document}]},
    ).json()
    assert full["complete"] and not full["documents"][0]["visualsComplete"]
    assert any("additional stored source" in n for n in full["notes"])


async def test_asset_limit_is_separate_from_description_limit_and_disclosed_for_affected_page(ctx):
    client, services, lib, worker = ctx

    class Many(FakeLayout):
        async def analyze_pages(self, data, first, last):
            r = result(first, last, figure_page=1)
            r["figures"] = [{**r["figures"][0], "id": f"1.{i}"} for i in range(3)]
            return r, "operation"

    worker.layout = Many()
    worker.vision = FakeChat({"description": "diagram"})
    worker.image_max_per_document = 1
    worker.image_asset_max_per_document = 2
    document = upload(client, lib, "source.pdf", pdf(2))
    await drain(services, worker)
    response = lookup(client, lib, document, [1]).json()
    assert len(response["results"]) == 2 and response["imagesSkipped"] == 1 and not response["complete"]
    assert lookup(client, lib, document, [2]).json()["complete"]
    assert doc(client, lib, document)["sourceImagesSkipped"] == 1


async def test_manifest_digest_and_generation_are_validated_before_source_images_are_returned(ctx):
    client, services, lib, worker = ctx
    worker.layout = FakeLayout()
    worker.vision = FakeChat({"description": "diagram"})
    document = upload(client, lib, "source.pdf", pdf(2))
    await drain(services, worker)
    stored = await services.documents.get(lib, document)
    manifest = await load_assets(services.blobs, lib, stored)
    assert manifest["assets"][0]["imageMeta"]["captionStatus"] == "identified"
    path = stored["imageAssets"]["blobPath"]
    await services.blobs.upload(path, json.dumps({"assets": []}).encode(), "application/json")
    response = lookup(client, lib, document, [1])
    assert response.status_code == 409 and "results" not in response.json()


async def test_office_coverage_does_not_offer_pdf_page_lookup_for_unindexed_pictures(ctx):
    client, services, lib, worker = ctx
    worker.image_max_per_document = 0
    document = upload(client, lib, "source.docx", office("docx"))
    await drain(services, worker)
    full = client.post(
        f"/v1/libraries/{lib}/documents:read",
        headers={"X-Agent-Id": "agent"},
        json={"documents": [{"documentId": document}]},
    ).json()
    assert full["complete"] and not full["documents"][0]["visualsComplete"]
    assert full["documents"][0]["sourceImageCount"] == 1
    assert any("Direct reads of unindexed Office pictures are not supported" in n for n in full["notes"])
    assert not any("available through selected-page image reads" in n for n in full["notes"])
