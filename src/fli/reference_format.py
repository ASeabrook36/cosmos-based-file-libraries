"""Lossless, provider-neutral factoring of already authorized evidence.

This is presentation only: no source selection, permissions, budget, or storage
policy lives here. Source strings are never clipped, normalized, or summarized.
"""

from __future__ import annotations

import copy
import json
from collections import Counter

CONTRACT = "fli.evidence.references.v1"
ENVELOPE = "fli.references.v1"
RESERVED = {"$ref", "$columns", "$rows", "$literal"}
# Only repeated metadata values enter the dictionary. Body/caption strings stay
# at their source position, even when identical text appears more than once.
METADATA = {
    "documentId",
    "generation",
    "contentRevision",
    "fileName",
    "fileType",
    "heading",
    "headingPath",
    "citation",
    "sourceScope",
    "location",
    "scope",
    "processingCoverage",
    "representation",
    "author",
    "tags",
    "periodStart",
    "periodEnd",
    "availableActions",
    "notes",
    "documentSelector",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def pack(evidence):
    """Factor metadata and uniform record lists; decode(pack(x)) == x exactly.

    Arrays retain their order. Every field remains present, including explicit
    nulls, empty table cells, coverage flags, and source revision bindings.
    """
    counts, values = Counter(), {}

    def collect(value, key=None):
        if key in METADATA and isinstance(value, (str, dict, list)) and value:
            fingerprint = canonical(value)
            if len(fingerprint) >= 24:
                counts[fingerprint] += 1
                values.setdefault(fingerprint, value)
                return
        if isinstance(value, dict):
            for k, v in value.items():
                collect(v, k)
        elif isinstance(value, list):
            for v in value:
                collect(v, key)

    collect(evidence)
    selected = [k for k, count in counts.items() if count > 1 and (count - 1) * len(k) > count * 12 + 12]
    ids = {key: n for n, key in enumerate(selected)}

    def encode(value, key=None):
        if key in METADATA and isinstance(value, (str, dict, list)) and value:
            fingerprint = canonical(value)
            if fingerprint in ids:
                return {"$ref": ids[fingerprint]}
        if isinstance(value, dict):
            out = {k: encode(v, k) for k, v in value.items()}
            return {"$literal": out} if RESERVED.intersection(value) else out
        if isinstance(value, list):
            # Column factoring only changes the wire shape of homogeneous lists.
            # It must not imply row/cell relationships absent in original evidence.
            if len(value) >= 2 and all(isinstance(v, dict) and v for v in value):
                columns = list(value[0])
                if not RESERVED.intersection(columns) and all(list(v) == columns for v in value):
                    return {"$columns": columns, "$rows": [[encode(v[k], k) for k in columns] for v in value]}
            return [encode(v, key) for v in value]
        return copy.deepcopy(value)

    return {
        "contract": CONTRACT,
        "dictionary": [copy.deepcopy(values[k]) for k in selected],
        "evidence": encode(evidence),
    }


def unpack(value):
    """Strictly decode the versioned contract; reject broken or ambiguous refs."""
    if not isinstance(value, dict) or value.get("contract") != CONTRACT:
        raise ValueError("unsupported evidence reference contract")
    if set(value) != {"contract", "dictionary", "evidence"} or not isinstance(value["dictionary"], list):
        raise ValueError("invalid evidence reference envelope")
    dictionary = value["dictionary"]

    def decode(item):
        if isinstance(item, list):
            return [decode(v) for v in item]
        if not isinstance(item, dict):
            return item
        if "$literal" in item:
            if set(item) != {"$literal"} or not isinstance(item["$literal"], dict):
                raise ValueError("invalid escaped evidence object")
            return {k: decode(v) for k, v in item["$literal"].items()}
        if "$ref" in item:
            index = item["$ref"]
            if set(item) != {"$ref"} or type(index) is not int or not 0 <= index < len(dictionary):
                raise ValueError("invalid evidence dictionary reference")
            return copy.deepcopy(dictionary[index])
        if "$columns" in item or "$rows" in item:
            if set(item) != {"$columns", "$rows"}:
                raise ValueError("invalid evidence column block")
            columns, rows = item["$columns"], item["$rows"]
            if (
                not isinstance(columns, list)
                or not columns
                or not all(isinstance(k, str) for k in columns)
                or len(set(columns)) != len(columns)
                or not isinstance(rows, list)
                or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)
            ):
                raise ValueError("invalid evidence column dimensions")
            return [{key: decode(v) for key, v in zip(columns, row, strict=True)} for row in rows]
        return {k: decode(v) for k, v in item.items()}

    return decode(value["evidence"])


def expanded(evidence):
    return unpack(evidence) if isinstance(evidence, dict) and evidence.get("contract") == CONTRACT else evidence


def model_messages(messages):
    """Pack tool evidence only; prompts, tool requests and native image parts stay intact."""
    result = []
    for message in messages:
        value = copy.deepcopy(message)
        if value.get("role") == "tool" and isinstance(value.get("content"), str):
            evidence = json.loads(value["content"])
            if not isinstance(evidence, dict):
                raise ValueError("tool evidence must be an object")
            value["content"] = json.dumps(pack(expanded(evidence)), ensure_ascii=False, separators=(",", ":"))
        result.append(value)
    return result
