"""Opt-in usable selectors over authorized results; hints never grant permission.

Unavailable source coordinates are explicit. Hints do not synthesize locations,
expand a selected scope, or silently discard generation/revision requirements.
"""

from __future__ import annotations

import copy

SCOPE = ("documentId", "generation", "contentRevision")
GUIDANCE = (
    "Copy returned documentSelector objects exactly (never a bare ID or filename). "
    "Use readSelector for selected source reads. If evidenceExpansion is outline_required, "
    "request the returned outlineSelector and then use its section readSelector. "
    "On stale references rediscover; do not remove generation/contentRevision pins."
)


def add_hints(evidence):
    result = copy.deepcopy(evidence)

    def visit(value, parent=None):
        if isinstance(value, list):
            for row in value:
                visit(row, parent)
            return
        if not isinstance(value, dict):
            return
        context = {**(parent or {}), **{k: value[k] for k in SCOPE if k in value}}
        pinned = (
            isinstance(context.get("documentId"), str)
            and type(context.get("generation")) is int
            and context["generation"] > 0
            and type(context.get("contentRevision")) is int
            and context["contentRevision"] >= 0
        )
        # Descend before adding hints so the generated request templates aren't
        # recursively decorated. Source location dictionaries are not source rows.
        for key, item in list(value.items()):
            if key in {"documents", "results", "sections", "outline", "candidateImages", "pageSections"}:
                visit(item, context)
        if not pinned or not ("documentId" in value or "sectionId" in value or "evidenceId" in value):
            return
        scope = {k: context[k] for k in SCOPE}
        value["documentSelector"] = scope
        value["outlineSelector"] = {"mode": "outline", "documents": [scope]}
        field, handle = (
            ("sectionIds", value.get("sectionId"))
            if value.get("sectionId")
            else ("evidenceIds", value.get("evidenceId"))
        )
        location = value.get("sourceScope") or value.get("location") or {}
        usable = (
            field == "sectionIds"
            or value.get("representation") == "indexed_chunks"
            or any(key in location for key in ("paragraph", "page", "slide"))
            or ("charStart" in location and "charEnd" in location)
        )
        value["readCapabilities"] = {
            "snapshotBound": True,
            "full": value.get("readable", True),
            "outline": value.get("readable", True),
            "evidenceExpansion": "selected_handle" if handle and usable else "outline_required",
            "sourceLocationPrecision": "stored_extraction_only",
        }
        if handle and usable:
            value["readSelector"] = {"mode": "selected", "documents": [{**scope, field: [handle]}]}

    visit(result)
    result["selectorGuidance"] = GUIDANCE
    return result


def tool_hints(tools):
    result = copy.deepcopy(tools)
    for tool in result:
        function = tool["function"]
        function["description"] += " " + GUIDANCE
    return result


def error_details(error):
    """Sanitized categories for model recovery; never repeat untrusted selectors."""
    code = getattr(
        error, "code", "invalid_selector" if isinstance(error, (ValueError, TypeError)) else "retrieval_error"
    )
    actions = {
        "source_scope_unavailable": (
            "Request an outline for the same pinned document, then copy a returned readSelector."
        ),
        "evidence_changed": "Rediscover the document and obtain current selectors. Do not drop snapshot pins.",
        "document_changed": "Rediscover the document and obtain current selectors. Do not drop snapshot pins.",
        "invalid_request": "Use a returned documentSelector object and a supported readSelector; never a bare ID.",
        "invalid_selector": "Use a returned documentSelector object and a supported readSelector; never a bare ID.",
    }
    allowed = {*actions, "library_not_found", "library_not_bound_to_agent", "document_not_found", "document_not_ready"}
    category = code if code in allowed else "retrieval_error"
    return {
        "error": category,
        "errorCategory": category,
        "complete": False,
        "nextAction": actions.get(
            category, "Retry only after resolving the retrieval error; no evidence was returned."
        ),
    }
