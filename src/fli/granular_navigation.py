"""Model-facing navigation over an already validated immutable source catalog.

No queries, provider calls or permissions live here. Call only after the ordinary
reader validates the artifact and before its post-I/O lifecycle check.
"""

from __future__ import annotations

from fli.errors import ApiError
from fli.granular_source import node_indexes
from fli.presentation import evidence_json, project


def node_fields(node):
    return {
        "navigationNodeId": node["id"],
        "navigationKind": node["kind"],
        "parentNodeId": node["parentId"],
        "containingSectionId": node.get("containingSectionId"),
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
    return entries, len(children)


def section_fields(section, tree):
    atom = (section.get("location") or {}).get("sourceAtomId")
    node = next((n for n in tree["nodes"] if n["id"] == atom), None)
    if node is None:
        raise ApiError(409, "document_text_incomplete", "source atom has no navigation node")
    return node_fields(node)
