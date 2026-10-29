"""Opt-in Microsoft reuse probes; no imports or changes to the application pipeline.

Raw files, responses and configuration must stay outside the checkout. This driver
tests extraction components, not a deployed Document Indexer or KBaaS workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "e6b7d87e120a400b3e321caacf858c08fa4d060f"
TEMPLATE = "ingest-doc-blob-document-intelligence-cosmos/default/workflow.json"
SOURCE = f"https://raw.githubusercontent.com/Azure/LogicAppsTemplates/{COMMIT}/{TEMPLATE}"


def private_directory(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("runtime directory must be outside the repository")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_mode & 0o077:
        raise ValueError("runtime directory must have mode 0700")
    return path


def save_bytes(path: Path, data: bytes) -> None:
    # Exclusive temporary file and atomic replace; never publish partially written evidence.
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    temporary.replace(path)


def save(path: Path, data: object) -> None:
    save_bytes(path, (json.dumps(data, indent=2) + "\n").encode())


def inspect_template(workflow: dict) -> dict:
    actions = workflow.get("definition", workflow)["actions"]
    projection = actions["Select_embeddings_and_map_to_CosmosDB"]["inputs"]["select"]
    analyze = next(v for k, v in actions.items() if k.startswith("Analyze_Document"))
    chunk = actions["Chunk_text"]["inputs"]
    return {
        "modelPath": analyze["inputs"]["path"],
        "analysisQueries": analyze["inputs"]["queries"],
        "chunkInput": chunk["text"],
        "chunking": {k: v for k, v in chunk.items() if k != "text"},
        "projection": projection,
        "randomIds": projection.get("id") == "@{guid()}",
        "preservesElementGraph": any("sections" in str(v) or "elements" in str(v) for v in projection.values()),
        "triggerOperations": [
            v["inputs"].get("serviceProviderConfiguration", {}).get("operationId")
            for v in workflow.get("definition", workflow)["triggers"].values()
        ],
        "outputs": workflow.get("definition", workflow).get("outputs"),
    }


def structure_metrics(result: dict) -> dict:
    """Count retained graph data and check references; counts do not establish quality."""
    errors = 0
    refs = 0
    nested = 0
    for section in result.get("sections", []):
        for ref in section.get("elements", []):
            refs += 1
            match = re.fullmatch(r"/(sections|paragraphs|tables|figures)/(\d+)", ref)
            if not match or int(match[2]) >= len(result.get(match[1], [])):
                errors += 1
            elif match[1] == "sections":
                nested += 1
    return {
        "contentCharacters": len(result.get("content", "")),
        **{key: len(result.get(key, [])) for key in ("pages", "sections", "paragraphs", "tables", "figures")},
        "sectionElementReferences": refs,
        "nestedSectionReferences": nested,
        "unresolvedSectionReferences": errors,
        "figuresWithCaption": sum(bool(x.get("caption")) for x in result.get("figures", [])),
        "figuresWithElements": sum(bool(x.get("elements")) for x in result.get("figures", [])),
    }


def load_cases(manifest: Path, max_pages: int) -> list[dict]:
    from pypdf import PdfReader

    cases = json.loads(manifest.read_text())["documents"]
    if not 1 <= len(cases) <= 6:
        raise ValueError("bounded experiment requires one to six documents")
    names = set()
    for case in cases:
        label = case["label"]
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", label) or label in names:
            raise ValueError("unique safe case labels required")
        names.add(label)
        path = (manifest.parent / case["path"]).resolve()
        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("missing or oversized source")
        if path.suffix.lower() == ".pdf" and len(PdfReader(path).pages) > max_pages:
            raise ValueError("PDF exceeds page budget; never silently analyze only a prefix")
        if path.suffix.lower() not in {".pdf", ".docx", ".png", ".jpg", ".tiff"}:
            raise ValueError("use supported extraction inputs; do not silently convert format")
        case["sourcePath"] = path
    return cases


def section_diagnostic(result: dict, task: dict) -> dict:
    """Probe the provider's own section spans, without repairing its inferred tree."""
    heading = " ".join(task["heading"].split()).casefold()
    paragraph_refs = {
        f"/paragraphs/{i}"
        for i, p in enumerate(result.get("paragraphs", []))
        if " ".join(p.get("content", "").split()).casefold() == heading
    }
    candidates = [s for s in result.get("sections", []) if paragraph_refs.intersection(s.get("elements", []))]
    if len(candidates) != 1:
        return {"task": task["label"], "resolved": False, "passed": False}
    content = result.get("content", "")
    spans = candidates[0].get("spans", [])
    if not spans or any(s["offset"] < 0 or s["length"] < 0 or s["offset"] + s["length"] > len(content) for s in spans):
        return {"task": task["label"], "resolved": False, "passed": False}
    selected = " ".join(" ".join(content[s["offset"] : s["offset"] + s["length"]] for s in spans).split()).casefold()
    included = [" ".join(x.split()).casefold() in selected for x in task.get("include", [])]
    excluded = [" ".join(x.split()).casefold() not in selected for x in task.get("exclude", [])]
    return {
        "task": task["label"],
        "resolved": True,
        "included": included,
        "excluded": excluded,
        "passed": all(included) and all(excluded),
        "selectedCharacters": sum(s["length"] for s in spans),
    }


def assess(row: dict, result: dict, probes: list[str], selections: list[dict] | None = None) -> dict:
    normalized = " ".join(result.get("content", "").split()).casefold()
    row = {**row, "metrics": structure_metrics(result)}
    row["probes"] = [" ".join(probe.split()).casefold() in normalized for probe in probes]
    row["acceptancePassed"] = all(row["probes"]) and row["metrics"]["unresolvedSectionReferences"] == 0
    row["sectionDiagnostics"] = [section_diagnostic(result, task) for task in selections or []]
    return row


def resume(receipt: dict, response_path: Path, probes: list[str], selections: list[dict] | None = None) -> dict:
    response_bytes = response_path.read_bytes()
    if hashlib.sha256(response_bytes).hexdigest() != receipt["responseSha256"]:
        raise ValueError("cached response integrity check failed")
    for index, asset in enumerate(receipt.get("figureAssets", [])):
        path = response_path.with_name(response_path.name.removesuffix("-response.json") + f"-figure-{index}.png")
        data = path.read_bytes()
        if len(data) != asset["bytes"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError("cached figure integrity check failed")
    return assess(receipt, json.loads(response_bytes), probes, selections)


async def analyze(manifest: Path, work: Path, max_pages: int, *, figures_only: bool = False) -> dict:
    import httpx
    from azure.identity.aio import AzureCliCredential

    from fli.docintel import DocumentIntelligenceLayout

    cases = load_cases(manifest, max_pages)
    if figures_only and len(cases) != 1:
        raise ValueError("figure-probe requires a single-document manifest")
    endpoint = os.environ["MICROSOFT_REUSE_DOCINTEL_ENDPOINT"]
    measurements = []
    calls = 0
    async with AzureCliCredential() as credential, httpx.AsyncClient(timeout=60) as http:

        async def token() -> str:
            return (await credential.get_token("https://cognitiveservices.azure.com/.default")).token

        reader = DocumentIntelligenceLayout(
            endpoint=endpoint,
            token_provider=token,
            http=http,
            max_pages=max_pages,
            max_bytes=20 * 1024 * 1024,
            timeout_seconds=180,
        )
        for case in cases:
            source = case["sourcePath"].read_bytes()
            for model in ("prebuilt-layout",) if figures_only else ("prebuilt-read", "prebuilt-layout"):
                name = f"{case['label']}-{model}"
                params = {"outputContentFormat": "markdown", "stringIndexType": "unicodeCodePoint"}
                if model == "prebuilt-layout" and case["sourcePath"].suffix.lower() == ".pdf":
                    params["output"] = "figures"
                fingerprint = hashlib.sha256(
                    source
                    + json.dumps([endpoint, reader.api_version, model, params, figures_only], sort_keys=True).encode()
                ).hexdigest()
                receipt_path = work / f"{name}-receipt.json"
                if receipt_path.exists():
                    cached = json.loads(receipt_path.read_text())
                    if cached["fingerprint"] != fingerprint:
                        raise ValueError("changed inputs require a fresh work directory")
                    if cached["status"] == "succeeded":
                        cached = resume(
                            cached, work / f"{name}-response.json", case.get("probes", []), case.get("selections", [])
                        )
                        save(receipt_path, cached)
                        measurements.append(cached)
                        continue
                started = time.monotonic()
                try:
                    calls += 1
                    result, operation = await reader.analyze_operation(source, model, params)
                    response_path = work / f"{name}-response.json"
                    save(response_path, result)
                    row = {
                        "case": case["label"],
                        "model": model,
                        "fingerprint": fingerprint,
                        "status": "succeeded",
                        "elapsedSeconds": round(time.monotonic() - started, 3),
                        "responseSha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
                    }
                    row = assess(row, result, case.get("probes", []), case.get("selections", []))
                    if figures_only:
                        assets = result.get("figures", [])
                        if not 1 <= len(assets) <= 8:
                            raise ValueError("figure probe expects one to eight figures")
                        row["figureAssets"] = []
                        for index, figure in enumerate(assets):
                            data = await reader.figure(operation, figure["id"])
                            asset = work / f"{name}-figure-{index}.png"
                            save_bytes(asset, data)
                            row["figureAssets"].append({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                except Exception as exc:
                    # SDK errors can contain resource endpoints and text. Do not print them.
                    save(work / f"{name}-error.json", {"type": type(exc).__name__, "detail": str(exc)})
                    row = {
                        "case": case["label"],
                        "model": model,
                        "fingerprint": fingerprint,
                        "status": "failed",
                        "errorType": type(exc).__name__,
                        "elapsedSeconds": round(time.monotonic() - started, 3),
                    }
                save(receipt_path, row)
                measurements.append(row)
                print(json.dumps({"case": case["label"], "model": model, "status": row["status"]}), flush=True)
    report = {
        "scope": "direct-extraction-component-probes",
        "workflowExecuted": False,
        "analysisCallsThisInvocation": calls,
        "measurements": measurements,
    }
    save(work / "extraction-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect-template", "analyze", "figure-probe"))
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--max-pages", type=int, default=12)
    args = parser.parse_args()
    work = private_directory(args.work_dir)
    if args.command == "inspect-template":
        with urllib.request.urlopen(SOURCE, timeout=30) as response:
            data = response.read()
        workflow = json.loads(data)
        save(work / "template.json", workflow)
        report = {"commit": COMMIT, "sha256": hashlib.sha256(data).hexdigest(), **inspect_template(workflow)}
        save(work / "template-inspection.json", report)
        print(json.dumps(report, indent=2))
        return 0
    if not args.manifest or not 1 <= args.max_pages <= 12:
        parser.error("analyze needs --manifest and a page bound from 1 to 12")
    report = asyncio.run(
        analyze(args.manifest.resolve(), work, args.max_pages, figures_only=args.command == "figure-probe")
    )
    return int(any(x["status"] != "succeeded" or not x["acceptancePassed"] for x in report["measurements"]))


if __name__ == "__main__":
    raise SystemExit(main())
