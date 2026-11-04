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
        if not pinned or not any(k in value for k in ("documentId", "sectionId", "evidenceId", "navigationNodeId")):
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
        granular = (value.get("processingCoverage") or {}).get("sourceStructure") == "fli.granular-source.v1"
        if granular and field == "evidenceIds":
            usable = bool(location.get("sourceAtomId"))
            value["passageToSourceMapping"] = "recorded_containing_source_atom" if usable else "unavailable"
            value["passageMappingVerification"] = "publication_bound; revalidated_on_expansion"
        value["readCapabilities"] = {
            "snapshotBound": True,
            "full": value.get("readable", True),
            "outline": value.get("readable", True),
            "evidenceExpansion": "selected_handle" if handle and usable else "outline_required",
            "sourceLocationPrecision": "stored_extraction_only",
        }
        if granular and field == "evidenceIds":
            value["readCapabilities"]["sourceLocationPrecision"] = (
                "recorded_containing_source_atom_not_exact_passage_offsets" if usable else "unavailable"
            )
        if handle and usable:
            value["readSelector"] = {"mode": "selected", "documents": [{**scope, field: [handle]}]}
        if node := value.get("navigationNodeId"):
            selected = {**scope, "nodeIds": [node]}
            value["readSelector"] = {
                "mode": "selected",
                "documents": [
                    {
                        **selected,
                        **({"nodeScope": "section"} if value.get("navigationKind") in {"section", "chapter"} else {}),
                    }
                ],
            }
            value["subtreeSelector"] = {"mode": "selected", "documents": [selected]}
            value["outlineSelector"] = {"mode": "outline", "documents": [{**scope, "outlineNodeId": node}]}
            value["fullSelector"] = {"mode": "full", "documents": [scope]}
            if parent := value.get("parentNodeId"):
                value["parentSelector"] = {"mode": "selected", "documents": [{**scope, "nodeIds": [parent]}]}
            if containing := value.get("containingSectionId"):
                value["containingSectionSelector"] = {
                    "mode": "selected",
                    "documents": [{**scope, "nodeIds": [containing]}],
                }
                value["containingSectionScope"] = (
                    "document_root_no_containing_authored_section"
                    if containing == "n0"
                    else "complete_authored_subtree"
                )
            value["readCapabilities"].update(
                evidenceExpansion="selected_handle",
                sourceLocationPrecision="verified_source_atom",
                sectionReadScope="own_content_excluding_authored_subsections",
                subtreeReadScope="all_descendant_source_atoms",
            )

    visit(result)
    result["selectorGuidance"] = GUIDANCE
    return result


def tool_hints(tools):
    result = copy.deepcopy(tools)
    for tool in result:
        function = tool["function"]
        function["description"] += " " + GUIDANCE
    return result


def inspection_hints(evidence, read_selector):
    """Recovery for granular size inspection, after source content was removed.

    The reader supplies an already validated, pinned request for exactly the same
    scope and effective budget. These hints neither deliver nor authorize content.
    """
    result = copy.deepcopy(evidence)
    result["readSelector"] = copy.deepcopy(read_selector)
    result["nextAction"] = (
        "This was a size/coverage inspection (includeText=false), not a source or outline read. "
        "Copy readSelector with includeText=true to receive the same scope. In mode=outline this returns "
        "navigation entries without body text. The same maxTokens and snapshot checks apply; "
        "if the content does not fit, narrow scope or paginate."
    )
    result["notes"] = [*(result.get("notes") or []), "includeText=false returned no source bodies or outline entries."]
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
