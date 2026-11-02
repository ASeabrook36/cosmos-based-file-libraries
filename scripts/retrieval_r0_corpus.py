"""Verify or explicitly fetch frozen R0 originals; reconstruct private source gold.

No models, Azure services, extraction recipes or application data are changed.
The normal command only reads the originals. Public downloads require ``fetch``;
all original bytes and regenerated gold must remain outside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import unicodedata
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from docx import Document

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "docs/testing/retrieval-r0"


def normalize(text: str) -> str:
    """Only Unicode compatibility and whitespace; keep case and punctuation."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def private_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if path == REPO or path.is_relative_to(REPO) or any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("originals and gold must remain outside the repository")
    return path


def validate_pack(manifest: dict, tasks: list[dict]) -> dict[str, dict]:
    documents = {d["id"]: d for d in manifest["documents"]}
    if len(documents) != len(manifest["documents"]):
        raise ValueError("duplicate source IDs")
    names = set()
    for document in documents.values():
        name = document["fileName"]
        if Path(name).name != name or name in {"", ".", ".."} or name in names:
            raise ValueError("source filenames must be unique plain filenames")
        names.add(name)
        if document["split"] not in {"development", "heldout"}:
            raise ValueError("unknown source split")
    case_ids, regions = set(), {}
    for task in tasks:
        if task["case"] in case_ids:
            raise ValueError("duplicate task IDs")
        case_ids.add(task["case"])
        for fact in task["sourceFacts"]:
            document = documents[fact["sourceId"]]
            if document["fileName"] != fact["source"] or document["split"] != task["split"]:
                raise ValueError("task source identity or split mismatch")
            for region in fact["regions"]:
                if region["sourceSha256"] != document["sha256"]:
                    raise ValueError("region bound to different original bytes")
                if region["id"] in regions and regions[region["id"]] != region:
                    raise ValueError("conflicting source region definitions")
                regions[region["id"]] = region
        for source_id in task.get("expectedSourceIds", []):
            if documents[source_id]["split"] != task["split"]:
                raise ValueError("catalogue task crosses frozen split")
    return documents


def region_text(document, region: dict) -> str:
    if region["kind"] == "paragraph":
        index = region["index"]
        if type(index) is not int or index < 0:
            raise ValueError("paragraph indices must be nonnegative integers")
        return document.paragraphs[index].text
    if region["kind"] == "table_cell":
        coordinates = [region[key] for key in ("table", "row", "column")]
        if any(type(value) is not int or value < 0 for value in coordinates):
            raise ValueError("table coordinates must be nonnegative integers")
        table, row, column = coordinates
        return document.tables[table].rows[row].cells[column].text
    raise ValueError("unknown original-source region kind")


def verify(manifest: dict, tasks: list[dict], directory: Path) -> dict:
    """Rebuild labels from exact originals, never from API sections or chunks."""
    documents = validate_pack(manifest, tasks)
    directory = private_path(directory)
    originals = {}
    for source_id, source in documents.items():
        path = private_path(directory / source["fileName"])
        if path.parent != directory:
            raise ValueError("source must be a direct child of the private corpus directory")
        data = path.read_bytes()
        if len(data) != source["sizeBytes"] or sha256(data) != source["sha256"]:
            raise ValueError(f"original bytes changed: {source_id}")
        originals[source_id] = Document(path)
    gold = {}
    for task in tasks:
        for fact in task["sourceFacts"]:
            for region in fact["regions"]:
                text = region_text(originals[fact["sourceId"]], region)
                normalized = normalize(text)
                if sha256(normalized.encode()) != region["normalizedTextSha256"]:
                    raise ValueError(f"source region changed: {region['id']}")
                if (not normalized) != region.get("expectedBlank", False):
                    raise ValueError(f"blankness label mismatch: {region['id']}")
                gold[region["id"]] = {
                    "sourceId": fact["sourceId"],
                    "fileName": fact["source"],
                    "locator": {
                        key: region[key] for key in ("kind", "index", "table", "row", "column") if key in region
                    },
                    "expectedText": text,
                    "normalizedText": normalized,
                    "normalizedTextSha256": region["normalizedTextSha256"],
                }
    return {
        "schema": "fli.retrieval-r0.private-gold.v1",
        "normalization": "Unicode NFKC; whitespace collapsed with split/join; preserve case and punctuation",
        "regions": gold,
    }


def fetch(manifest: dict, directory: Path) -> None:
    """Opt-in public acquisition; refuse replacements or a changed upstream file."""
    validate_pack(manifest, [])
    directory = private_path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for document in manifest["documents"]:
        path = private_path(directory / document["fileName"])
        if path.parent != directory:
            raise ValueError("source escaped the private corpus directory")
        if path.exists():
            data = path.read_bytes()
        else:
            url = document["provenance"]["downloadUrl"]
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query:
                raise ValueError("source acquisition needs a public HTTPS URL without credentials")
            request = urllib.request.Request(url, headers={"User-Agent": "fli-retrieval-r0/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(document["sizeBytes"] + 1)
            if len(data) != document["sizeBytes"] or sha256(data) != document["sha256"]:
                raise ValueError(f"download does not match frozen original: {document['id']}")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
        if len(data) != document["sizeBytes"] or sha256(data) != document["sha256"]:
            raise ValueError(f"existing source does not match frozen original: {document['id']}")
        print(f"{document['id']}: original hash verified", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "fetch"))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=PACK / "manifest.json")
    parser.add_argument("--tasks", type=Path, default=PACK / "tasks.json")
    parser.add_argument("--gold-output", type=Path, help="New private file; existing gold is never overwritten")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    tasks_bytes = args.tasks.read_bytes()
    if sha256(tasks_bytes) != manifest["tasksSha256"]:
        raise ValueError("task pack differs from frozen manifest")
    tasks = json.loads(tasks_bytes)
    validate_pack(manifest, tasks)
    if args.action == "fetch":
        fetch(manifest, args.source_dir)
    gold = verify(manifest, tasks, args.source_dir)
    if args.gold_output:
        output = private_path(args.gold_output)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(gold, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    print(
        json.dumps(
            {
                "documentsVerified": len(manifest["documents"]),
                "tasksVerified": len(tasks),
                "regionsVerified": len(gold["regions"]),
            }
        )
    )


if __name__ == "__main__":
    main()
