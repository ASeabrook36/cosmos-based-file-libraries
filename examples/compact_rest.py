"""Minimal platform integration, independent of the demo. Supply an authenticated httpx.Client.

Keep this client and returned attachments inside the authorized conversation's lifetime.
Do not persist/log envelopes, bearer URLs or source text. The host owns library attachment
permissions, model context/native-image budgets, expiry refresh and display/cache policy.
"""

import json


def retrieve(http, library_id, agent_id, operation, body, max_text_tokens=12_000):
    """Use only the platform's bound library/agent, never model-supplied credentials/scope."""
    response = http.post(
        f"/v1/libraries/{library_id}/{operation}",
        headers={"X-Agent-Id": agent_id},
        params={"view": "compact", "maxEvidenceTokens": max_text_tokens},
        json=body,
    )
    response.raise_for_status()
    envelope = response.json()
    if envelope["contract"] != "fli.compact.v1":
        raise ValueError("unsupported evidence contract")
    # Send ONLY this JSON as tool text, with normal untrusted-source safeguards.
    model_text = json.dumps(envelope["evidence"], ensure_ascii=False, separators=(",", ":"))
    return model_text, envelope["attachments"], envelope["metadata"]


def search(http, bound_library, calling_agent, query):
    return retrieve(http, bound_library, calling_agent, "search", {"query": query})


def resolve_selected(http, bound_library, calling_agent, returned_document, image_ids):
    """Revalidate references and refresh links before using selected retained images.

    returned_document comes from evidence; the host verifies the selection belongs to its
    authorized conversation. The API validates membership, active generation and revision.
    Batch by document (up to ten IDs per request); handle 409 by rediscovering, not by dropping pins.
    """
    return retrieve(
        http,
        bound_library,
        calling_agent,
        "documents:images",
        {**{k: returned_document[k] for k in ("documentId", "generation", "contentRevision")}, "imageIds": image_ids},
    )


def display_selected(attachments, selected_ids, display):
    """display(url, caption, provenance) is the platform's ephemeral image/UI callback."""
    by_id = {a["imageId"]: a for a in attachments}
    for image_id in selected_ids:
        asset = by_id[image_id]  # never resolve an unreturned ID or arbitrary model URL
        image = asset["image"]
        caption = image.get("caption") or "No source caption identified"
        if image.get("captionAssociation") == "adjacent_inferred":
            caption = "Nearby source text (association inferred): " + caption
        provenance = {
            "fileName": asset["fileName"],
            "location": {k: v for k, v in (asset.get("location") or {}).items() if k != "figure"},
        }
        display(image["url"], caption, provenance)


def native_image_parts(attachments, selected_ids):
    """Optional, ONLY after the host checks model support and reserves image-input cost.

    Text-only consumers skip this entirely and can still display selected attachments.
    Sending these parts is distinct from displaying them; record supplied/withheld IDs.
    Provider-reported usage, not attachment count, determines actual model-token charges.
    """
    by_id = {a["imageId"]: a for a in attachments}
    return [
        {"type": "image_url", "image_url": {"url": by_id[i]["image"]["url"], "detail": "high"}} for i in selected_ids
    ]
