"""Snapshot-bound references, resolved inside the already authorized document.

References are locators, not capabilities. They never contain Blob paths or grant access.
An enriched snapshot can change section positions: bind them to its content revision/digest.
"""

import hashlib
import json

from fli.errors import ApiError


def snapshot(doc):
    value = [
        doc["id"],
        doc.get("activeGeneration"),
        doc.get("contentRevision", 0),
        (doc.get("fullText") or {}).get("sha256"),
    ]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()[:20]


def reference(doc, kind, index):
    return f"ev1.{snapshot(doc)}.{kind}.{index}"


def resolve_reference(doc, value, kind):
    parts = value.split(".")
    if len(parts) != 4 or parts[:3] != ["ev1", snapshot(doc), kind] or not parts[3].isdigit():
        raise ApiError(409, "evidence_changed", "invalid or stale evidence reference; retrieve again")
    index = int(parts[3])
    if not 0 <= index < 100_000:
        raise ApiError(400, "invalid_request", "evidence index is outside supported bounds")
    return index
