"""Acquire and verify fresh hybrid-quality originals and reconstruct private gold.

No Azure calls, retrieval results, model answers, or production changes. Public
labels use original DOCX coordinates and hashes; source bodies stay private.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document  # noqa: E402

from scripts import retrieval_r0_corpus as r0  # noqa: E402

PACK = r0.REPO / "docs/testing/hybrid-quality"
LOCATOR_KEYS = ("kind", "index", "table", "row", "column")


def serialized(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def legacy_splits(manifest: dict, tasks: list[dict]) -> tuple[dict, list[dict]]:
    """Reuse R0 byte/coordinate checks without relabeling its frozen pack."""
    manifest, tasks = copy.deepcopy(manifest), copy.deepcopy(tasks)
    for document in manifest["documents"]:
        if document["split"] != "confirmation":
            raise ValueError("fresh confirmation sources only")
        document["split"] = "heldout"
    for task in tasks:
        if task["split"] != "confirmation":
            raise ValueError("fresh confirmation tasks only")
        task["split"] = "heldout"
    return manifest, tasks


def validate_pack(manifest: dict, tasks: list[dict]) -> dict[str, dict]:
    translated, translated_tasks = legacy_splits(manifest, tasks)
    r0.validate_pack(translated, translated_tasks)
    facts = manifest["facts"]
    questions = set()
    used_facts = set()
    for task in tasks:
        question = r0.normalize(task["question"])
        if not question or question in questions:
            raise ValueError("empty or duplicate confirmation question")
        questions.add(question)
        if type(task["answerable"]) is not bool or not task["families"]:
            raise ValueError("explicit answerability and families required")
        regions = {}
        for source in task["sourceFacts"]:
            for region in source["regions"]:
                if region["id"] in regions or region.get("expectedBlank") is not False:
                    raise ValueError("duplicate or blank required source region")
                if region["normalizedTextSha256"] == r0.sha256(b""):
                    raise ValueError("empty-string source support is forbidden")
                regions[region["id"]] = region
        if len(set(task["requiredRegionIds"])) != len(task["requiredRegionIds"]) or set(regions) != set(
            task["requiredRegionIds"]
        ):
            raise ValueError("required region IDs differ from source labels")
        fact_ids = task["requiredFactIds"]
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("duplicate required fact IDs")
        if not task["answerable"]:
            if regions or fact_ids or task["expectedSourceIds"] or not task.get("missingAnswerDefinition"):
                raise ValueError("unanswerable tasks require explicit absence criteria and no recall denominator")
            continue
        if not regions or not fact_ids:
            raise ValueError("answerable task has no source facts")
        covered = set()
        for fact_id in fact_ids:
            fact = facts[fact_id]
            ids = fact["sourceRegionIds"]
            if not ids or not set(ids) <= set(regions):
                raise ValueError("fact references source regions outside task")
            if set(ids) != {anchor["regionId"] for anchor in fact["anchorSpans"]}:
                raise ValueError("fact anchors do not bind every source region")
            for anchor in fact["anchorSpans"]:
                if (
                    type(anchor["start"]) is not int
                    or type(anchor["end"]) is not int
                    or anchor["start"] < 0
                    or anchor["end"] <= anchor["start"]
                    or anchor["normalizedTextSha256"] == r0.sha256(b"")
                ):
                    raise ValueError("invalid or empty fact anchor")
            covered.update(ids)
            used_facts.add(fact_id)
        if covered != set(regions):
            raise ValueError("required source region has no scored fact")
        if set(task["expectedSourceIds"]) != {source["sourceId"] for source in task["sourceFacts"]}:
            raise ValueError("expected sources differ from fact sources")
    if tasks and used_facts != set(facts):
        raise ValueError("unused public fact definition")
    return {document["id"]: document for document in manifest["documents"]}


def _complete_region_text(document, region: dict) -> str:
    """Read original XML text, including inline content controls and table rows."""
    r0.region_text(document, region)  # Validate the public coordinate shape first.
    if region["kind"] == "paragraph":
        element = document.paragraphs[region["index"]]._p
    else:
        element = document.tables[region["table"]].rows[region["row"]].cells[region["column"]]._tc
    # Paragraph boundaries matter in cells, including nested content controls.
    parts = []
    for node in element.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t":
            parts.append(node.text or "")
        elif tag in {"tab", "br", "cr", "p"}:
            parts.append(" ")
    text = "".join(parts).strip()
    if not r0.normalize(text):
        raise ValueError("empty-string source support is forbidden")
    return text


def verify(manifest: dict, tasks: list[dict], directory: Path) -> dict:
    validate_pack(manifest, tasks)
    directory = r0.private_path(directory)
    originals = {}
    for source in manifest["documents"]:
        path = r0.private_path(directory / source["fileName"])
        if path.parent != directory:
            raise ValueError("source must be a direct child of the private corpus directory")
        data = path.read_bytes()
        if len(data) != source["sizeBytes"] or r0.sha256(data) != source["sha256"]:
            raise ValueError(f"original bytes changed: {source['id']}")
        originals[source["id"]] = Document(path)
    gold = {
        "normalization": "Unicode NFKC; whitespace collapsed with split/join; preserve case and punctuation",
        "regions": {},
    }
    for task in tasks:
        for source in task["sourceFacts"]:
            for region in source["regions"]:
                text = _complete_region_text(originals[source["sourceId"]], region)
                normalized = r0.normalize(text)
                if r0.sha256(normalized.encode()) != region["normalizedTextSha256"]:
                    raise ValueError(f"source region changed: {region['id']}")
                gold["regions"][region["id"]] = {
                    "sourceId": source["sourceId"],
                    "fileName": source["source"],
                    "locator": {key: region[key] for key in LOCATOR_KEYS if key in region},
                    "expectedText": text,
                    "normalizedText": normalized,
                    "normalizedTextSha256": region["normalizedTextSha256"],
                }
    gold["schema"] = "fli.hybrid-quality.private-gold.v1"
    gold["facts"] = {}
    for fact_id, fact in manifest["facts"].items():
        anchors = []
        for anchor in fact["anchorSpans"]:
            body = gold["regions"][anchor["regionId"]]["normalizedText"]
            if anchor["end"] > len(body):
                raise ValueError("fact anchor exceeds original region")
            text = body[anchor["start"] : anchor["end"]]
            if not text.strip() or r0.sha256(text.encode()) != anchor["normalizedTextSha256"]:
                raise ValueError("fact anchor differs from original source")
            anchors.append(text)
        gold["facts"][fact_id] = {
            "sourceRegionIds": fact["sourceRegionIds"],
            "expectedFact": "\n".join(anchors),
            "requiredAnchorTexts": anchors,
        }
    gold["tasks"] = {}
    for task in tasks:
        value = {"answerable": task["answerable"], "requiredFactIds": task["requiredFactIds"]}
        if task["answerable"]:
            value["expectedAnswer"] = "\n\n".join(gold["facts"][key]["expectedFact"] for key in task["requiredFactIds"])
        else:
            value["abstentionCriteria"] = task["missingAnswerDefinition"]
        gold["tasks"][task["case"]] = value
    return gold


def load_pack(manifest_path: Path, tasks_path: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads(manifest_path.read_text())
    data = tasks_path.read_bytes()
    if r0.sha256(data) != manifest["tasksSha256"]:
        raise ValueError("task pack differs from frozen manifest")
    tasks = json.loads(data)
    validate_pack(manifest, tasks)
    return manifest, tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "fetch"))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=PACK / "manifest.json")
    parser.add_argument("--tasks", type=Path, default=PACK / "tasks.json")
    parser.add_argument("--gold-output", type=Path)
    args = parser.parse_args()
    manifest, tasks = load_pack(args.manifest, args.tasks)
    directory = r0.private_path(args.source_dir)
    if args.action == "fetch":
        translated, _ = legacy_splits(manifest, [])
        r0.fetch(translated, directory)
    gold = verify(manifest, tasks, directory)
    data = serialized(gold)
    if r0.sha256(data) != manifest["goldSha256"]:
        raise ValueError("reconstructed gold differs from frozen digest")
    if args.gold_output:
        output = r0.private_path(args.gold_output)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    print(
        json.dumps(
            {
                "sourcesVerified": len(manifest["documents"]),
                "tasksVerified": len(tasks),
                "regionsVerified": len(gold["regions"]),
                "factsVerified": len(gold["facts"]),
                "goldSha256": r0.sha256(data),
            }
        )
    )


if __name__ == "__main__":
    main()
