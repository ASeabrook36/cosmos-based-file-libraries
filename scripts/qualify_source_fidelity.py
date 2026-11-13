"""Offline counterfactual adapter qualification against immutable retained sources.

The provider did not emit this new profile. A deep-copied envelope is locally rebound
only to exercise the new source adapter with identical archived analysis/rows/vectors.
No cloud/model calls, original mutation, source search, reembedding or answer grading.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from dataclasses import asdict
from pathlib import Path

from fli.checkpoints import digest, encoded
from fli.chunking import default_tokenizer
from fli.ingestion import job, profile, validate_microsoft
from fli.presentation import compact_response, evidence_json, project
from fli.source_fidelity import PROFILE, validate_sections
from scripts.source_structure_probe import checked, private_path, probe, require, sha


def sizes(sections, coverage, source_hash):
    tokenizer = default_tokenizer()
    response = {
        "documents": [
            {
                "documentId": "retained-document",
                "fileName": "retained.docx",
                "generation": 1,
                "contentRevision": 2,
                "sourceSha256": source_hash,
                "processingCoverage": coverage,
                "sections": sections,
            }
        ],
        "complete": True,
    }
    projected, _ = project({"sections": sections})
    result = {
        "artifactTokens": len(tokenizer.encode(encoded(sections).decode())),
        "projectedSectionsOnlyTokens": len(tokenizer.encode(evidence_json(projected["sections"]))),
        "rawEnvelopeTokens": len(tokenizer.encode(json.dumps(response))),
        "tokenizer": tokenizer.name,
        "scope": "actual source sections in a fixed generic envelope; not measured provider context or billing",
    }
    for view in ("compact", "references"):
        result[view + "EnvelopeTokens"] = compact_response(response, max_tokens=200000, view=view)["metadata"][
            "budget"
        ]["requiredTokens"]
    return result


def execute(inputs_path, output_path):
    inputs = json.loads(private_path(inputs_path).read_text())
    require(inputs["schema"] == "fli.source-structure-probe.inputs.v1", "unknown source envelope")
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", inputs["sourceId"]) is not None, "public source label required")
    data = {name: checked(inputs["files"][name]) for name in ("original", "extraction", "result", "sections")}
    extraction, result, old_sections = [json.loads(data[name]) for name in ("extraction", "result", "sections")]
    old_failure, _ = probe(
        data["original"], extraction, result, old_sections, inputs["locator"], inputs["normalizedTextSha256"]
    )
    dimensions = len(result["rows"][0]["embedding"])
    baseline = validate_microsoft(result, result["job"], data["original"], dimensions)
    rebound = copy.deepcopy(result)
    new_profile = profile(PROFILE)
    old_profile = result["job"]["profile"]
    new_profile["embedding"] = copy.deepcopy(old_profile["embedding"])
    new_profile["codeRevision"] = old_profile.get("codeRevision")
    for key in ("provider", "extraction", "extractionOnly", "chunking", "embedding", "workflowVersion"):
        require(new_profile[key] == old_profile[key], "provider-facing recipe changed")
    old_job = result["job"]
    rebound["job"] = job(
        old_job["libraryId"],
        old_job["documentId"],
        old_job["generation"],
        {"sha256": old_job["sourceSha256"], "blobPath": old_job["sourcePath"]},
        new_profile,
        old_job["metadataVersion"],
    )
    candidate = validate_microsoft(rebound, rebound["job"], data["original"], dimensions)
    require(
        baseline.pieces == candidate.pieces and baseline.vectors == candidate.vectors, "passages or vectors changed"
    )
    document = {
        "activeGeneration": rebound["job"]["generation"],
        "sha256": rebound["job"]["sourceSha256"],
        "ingestionJob": rebound["job"],
    }
    validate_sections(candidate.sections, document)
    target = [
        s
        for s in candidate.sections
        if all(s["sourceStructure"].get("coordinate", {}).get(k) == v for k, v in inputs["locator"].items())
    ]
    require(len(target) == 1, "target region is not unique")
    paragraphs = target[0]["sourceStructure"]["paragraphs"]
    definitions = candidate.sections[0]["sourceStructure"]["numberingDefinitions"]
    metrics = {
        "wholeSource": candidate.coverage,
        "targetParagraphs": len(paragraphs),
        "targetNumberedParagraphs": sum(
            p["numbering"] is not None and p["numbering"]["status"] != "suppressed" for p in paragraphs
        ),
        "targetFinalParagraphUnnumbered": paragraphs[-1]["numbering"] is None,
        "targetFinalParagraphRenderedAsSeparateSibling": target[0]["text"].rstrip().endswith("</p>\n</table-cell>"),
        "targetParagraphTextSha256": [sha(p["text"].encode()) for p in paragraphs],
        "targetRunsSha256": [digest(p["runs"]) for p in paragraphs],
        "numberingDefinitions": len(definitions),
    }
    require(
        metrics["targetFinalParagraphUnnumbered"] and metrics["targetFinalParagraphRenderedAsSeparateSibling"],
        "known qualifier boundary not preserved",
    )
    root = Path(__file__).resolve().parents[1]
    sources = [
        "src/fli/source_fidelity.py",
        "src/fli/ingestion.py",
        "src/fli/reading.py",
        "src/fli/presentation.py",
        "src/fli/worker/handlers.py",
        "src/fli/search.py",
        "scripts/qualify_source_fidelity.py",
    ]
    public = {
        "schema": "fli.source-fidelity.qualification.v1",
        "status": "qualified-offline",
        "sourceId": inputs["sourceId"],
        "profile": PROFILE,
        "inputSha256": {k: sha(v) for k, v in data.items()},
        "implementationSha256": {p: sha((root / p).read_bytes()) for p in sources},
        "originalFailureStillReproduces": old_failure["paragraphBoundaryLossReproduced"],
        "providerFacingRecipeUnchanged": True,
        "counterfactualExecutionIdChanged": rebound["job"]["executionId"] != result["job"]["executionId"],
        "passageTextAndLocationsUnchanged": True,
        "vectorsUnchanged": True,
        "passagesSha256": digest([asdict(p) for p in baseline.pieces]),
        "vectorsSha256": digest(baseline.vectors),
        "sectionsSha256": sha(encoded(candidate.sections)),
        "coverageSha256": digest(candidate.coverage),
        "observations": metrics,
        "tokens": {
            "wholeSource": sizes(candidate.sections, candidate.coverage, document["sha256"]),
            "target": sizes(target, candidate.coverage, document["sha256"]),
        },
        "cloudCalls": 0,
        "modelCalls": 0,
        "newConfirmationQuestions": 0,
        "limits": [
            "Counterfactual local profile/job rebind, not a new live provider execution.",
            "Consumed-source regression; no answer-quality or latency improvement claim.",
            "Printed numbering markers, complete original-document fidelity and direct passage-to-source mapping "
            "remain unavailable.",
            "Live distributed lifecycle, Azure publication and larger confirmation remain unrun.",
        ],
    }
    private = {
        "public": public,
        "counterfactualJob": rebound["job"],
        "sections": candidate.sections,
        "coverage": candidate.coverage,
        "inputEnvelope": inputs,
    }
    destination = private_path(output_path)
    with destination.open("x") as file:
        json.dump(private, file, ensure_ascii=False, indent=2)
        file.write("\n")
    destination.chmod(0o600)
    require(all(checked(inputs["files"][name]) == content for name, content in data.items()), "retained inputs changed")
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.inputs, args.output), indent=2))


if __name__ == "__main__":
    main()
