"""Model-facing navigation over an already validated immutable source catalog.

No queries, provider calls or permissions live here. Call only after the ordinary
reader validates the artifact and before its post-I/O lifecycle check.
"""

from __future__ import annotations

from fli.errors import ApiError
from fli.granular_source import node_indexes
from fli.presentation import evidence_json, project

PREVIEW_CHARACTERS = 160


def read_scope(node, *, subtree=False):
    if node["kind"] in {"section", "chapter"}:
        return "authored_subtree" if subtree else "own_content_excluding_authored_subsections"
    if node["kind"] == "document":
        return "whole_document"
    return "complete_source_subtree" if node["kind"] in {"table", "tableCell"} else "complete_source_atom"


def node_context(tree, identifier):
    """Source-authored ancestry; coordinate labels are never inferred headings."""
    nodes = {n["id"]: n for n in tree["nodes"]}
    node = nodes[identifier]
    ancestors, cursor = [], node
    while cursor:
        if cursor["kind"] in {"section", "chapter"}:
            ancestors.append({"nodeId": cursor["id"], "title": cursor["title"]})
        cursor = nodes.get(cursor["parentId"])
    return {
        "nodeId": identifier,
        "kind": node["kind"],
        "title": node["title"],
        "authoredPath": list(reversed(ancestors)),
    }


def selection_summary(tree, scope, atom_count):
    """Describe requested scope; only the response's completeness flags assert delivery."""
    return {
        "sourceAtomCount": atom_count,
        "nodes": [
            {
                **node_context(tree, identifier),
                "readScope": read_scope(
                    next(n for n in tree["nodes"] if n["id"] == identifier),
                    subtree=scope.get("nodeScope", "subtree") == "subtree",
                ),
            }
            for identifier in scope.get("nodeIds", [])
        ],
        "selectionUnit": "requested_nodes" if scope.get("nodeIds") else "source_atoms_not_container_completeness",
    }


def node_fields(node):
    return {
        "navigationNodeId": node["id"],
        "navigationKind": node["kind"],
        "parentNodeId": node["parentId"],
        "containingSectionId": node.get("containingSectionId"),
        "readScope": read_scope(node),
        **{k: node[k] for k in ("headingLevel", "headingOrigin") if k in node},
    }


def outline_entries(sections, tree, root, after, limit, tokenizer):
    nodes = {n["id"]: n for n in tree["nodes"]}
    if root not in nodes:
        raise ApiError(409, "source_scope_unavailable", "outline node unavailable for this snapshot")
    children = [n for n in tree["nodes"] if n["parentId"] == root]
    # Each atom is counted once locally; these are source-size hints, not the
    # complete response cost or model usage. Actual atomic budgeting runs later.
    sizes = [len(tokenizer.encode(evidence_json(project(s)[0]))) for s in sections]
    entries = []
    for node in children[after : after + limit]:
        direct = node_indexes(tree, [node["id"]], subtree=False)
        subtree = node_indexes(tree, [node["id"]], subtree=True)
        entries.append(
            {
                **node_fields(node),
                "title": node.get("title"),
                "heading": node.get("title") if node["kind"] in {"section", "chapter"} else None,
                "location": node.get("location") or node.get("sourceCoordinate") or {},
                "sectionCount": len(direct),
                "subtreeSectionCount": len(subtree),
                "sectionTokens": sum(sizes[i] for i in direct),
                "subtreeTokens": sum(sizes[i] for i in subtree),
                "sizeEstimateScope": "sum_of_projected_source_atoms_excludes_response_and_selector_overhead",
                "sizeEstimateTokenizer": tokenizer.name,
                "hasChildren": any(n["parentId"] == node["id"] for n in tree["nodes"]),
                "contentKind": "navigation",
            }
        )
        if node["kind"] in {"paragraph", "sourceBlock"}:
            # A preview is navigation, never the body returned by a source read.
            # Work from validated original text, not XML wrappers or a summary.
            section = sections[node["sectionIndexes"][0]]
            structure = section["sourceStructure"]
            paragraph = structure.get("paragraphs", [None])[0]
            source = paragraph["text"] if paragraph is not None else section["text"]
            entries[-1]["navigationPreview"] = {
                "prefix": source[:PREVIEW_CHARACTERS],
                "characters": min(len(source), PREVIEW_CHARACTERS),
                "sourceCharacters": len(source),
                "truncated": len(source) > PREVIEW_CHARACTERS,
            }
            if paragraph is not None and paragraph.get("authoredHeading"):
                entries[-1]["paragraphRole"] = "authored_heading"
    return entries, len(children)


def section_fields(section, tree):
    atom = (section.get("location") or {}).get("sourceAtomId")
    node = next((n for n in tree["nodes"] if n["id"] == atom), None)
    if node is None:
        raise ApiError(409, "document_text_incomplete", "source atom has no navigation node")
    return node_fields(node)
