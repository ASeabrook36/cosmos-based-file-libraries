"""Explicit, lifecycle-gated held-out selector confirmation; no provisioning.

R0/P1 remain development-only. This driver consumes the entire frozen held-out
pack once, preserving incomplete attempts and exact provider-wire source evidence.
Private receipts are operator attestations, not cryptographic proof of execution.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fli.chunking import default_tokenizer
from fli.selector_hints import tool_hints
from fli.structure_lab import digest
from scripts.compare_agent_models import default_chat_factory
from scripts.evaluate_agent_retrieval import prepare_output, replay, snapshot
from scripts.retrieval_benchmark import validate_sources
from scripts.retrieval_controls import (
    AGENT_INSTRUCTIONS,
    ANSWER_CONTROLS,
    answer_controls,
    frozen_pack,
    private_path,
    read,
    verify_cell,
)
from scripts.retrieval_p1 import RecordedChat, bound_deployment, make_index, verify_wire_sources
from scripts.score_retrieval_benchmark import (
    FAILURES,
    QUALITY,
    coverage,
    normalize,
    relevant_source_coverage,
    usage,
    validate_gold,
)
from scripts.structure_lab import save

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "docs/testing/retrieval-i1-controls.json"
PLAN_SHA256 = "d87f983214e8f8ae2b241dfe08db12be831839eb35c4b22c2b7056a64772e643"
CELLS = ("baseline", "selectors-only")
SOURCE_IDS = ("d09", "d10", "d11", "d12")
SETTINGS = {
    "searchMode": "vector",
    "searchDefaultTop": 8,
    "searchMaxTop": 20,
    "embeddingDimensions": 3072,
    "embeddingRevision": "1",
}
BINDING_KEYS = {
    "COSMOS_ENDPOINT",
    "COSMOS_DATABASE",
    "BLOB_ACCOUNT_URL",
    "BLOB_CONTAINER",
    "SERVICEBUS_NAMESPACE",
    "SERVICEBUS_QUEUE",
    "MICROSOFT_WORKFLOW_ENDPOINT",
    "SEARCH_MODE",
    "AOAI_EMBEDDING_MODEL_REVISION",
    "AOAI_ENDPOINT",
    "AOAI_EMBEDDING_DEPLOYMENT",
    "AOAI_EMBEDDING_DIMENSIONS",
    "SEARCH_DEFAULT_TOP",
    "SEARCH_MAX_TOP",
}
SELECTOR_ERRORS = {
    "source_scope_unavailable",
    "evidence_changed",
    "invalid_request",
    "invalid_selector",
    "document_not_found",
    "document_changed",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def api_hashes():
    return {
        str(p.relative_to(REPO)): sha(p)
        for folder in (REPO / "src/fli", REPO / "src/fli_demo")
        for p in sorted(folder.rglob("*.py"))
    }


def hashes():
    paths = [PLAN, *sorted((REPO / "scripts").glob("*.py")), *sorted((REPO / "cosmos").glob("*.json"))]
    return {**api_hashes(), **{str(p.relative_to(REPO)): sha(p) for p in paths}}


def frozen_inputs():
    if sha(PLAN) != PLAN_SHA256:
        raise ValueError("I1 plan bytes changed after predeclaration")
    plan = read(PLAN)
    manifest, tasks = frozen_pack(REPO / "docs/testing/retrieval-r0")
    cases = [c for c in tasks if c["split"] == "heldout"]
    if (
        [c["case"] for c in cases] != plan["cases"]
        or len(cases) != 16
        or any("questions" in c or not c.get("question") for c in cases)
        or [d["id"] for d in manifest["documents"] if d["split"] == "heldout"] != list(SOURCE_IDS)
    ):
        raise ValueError("I1 requires every frozen single-turn held-out task and source")
    return manifest, cases, plan


def recent(value, seconds, now=None):
    when = datetime.fromisoformat(value)
    now = now or datetime.now(UTC)
    if when.tzinfo is None or not 0 <= (now - when).total_seconds() <= seconds:
        raise ValueError("receipt timestamp is stale or invalid")
    return when


def runtime_binding(receipt, config, *, fresh=True):
    """Bind a fresh actual-process capture, never a copied config assertion alone."""
    if fresh:
        recent(receipt["capturedAt"], 900)
    binding = receipt["settingsBinding"]
    if (
        receipt.get("schema") != "fli.i1.api-runtime.v1"
        or receipt.get("indexUrl") != config["indexUrl"]
        or receipt.get("apiSourceSha256") != api_hashes()
        or set(binding) != BINDING_KEYS
        or not all(isinstance(v, str) and v for v in binding.values())
        or binding["SEARCH_MODE"] != "vector"
        or binding["AOAI_EMBEDDING_MODEL_REVISION"] != "1"
        or binding["AOAI_EMBEDDING_DIMENSIONS"] != "3072"
        or binding["SEARCH_DEFAULT_TOP"] != "8"
        or binding["SEARCH_MAX_TOP"] != "20"
        or receipt.get("settingsBindingSha256") != digest(binding)
        or receipt.get("effectiveSettings") != SETTINGS
        or receipt.get("authenticationMode") != "dev"
        or receipt.get("environment") != "local"
        or type(receipt.get("processId")) is not int
        or receipt["processId"] <= 0
        or receipt.get("ownEnvironmentAndImportVerified") is not True
        or not receipt.get("sourceRoot")
        or not receipt.get("pythonPrefix")
        or receipt.get("declaredCodeRevision") != receipt.get("gitRevision")
        or not receipt.get("gitRevision")
    ):
        raise ValueError("actual API runtime differs from frozen I1 controls")
    return {
        "apiSourceSha256": receipt["apiSourceSha256"],
        "settingsBindingSha256": receipt["settingsBindingSha256"],
        "indexUrl": receipt["indexUrl"],
        "processId": receipt["processId"],
        "gitRevision": receipt["gitRevision"],
        "effectiveSettings": receipt["effectiveSettings"],
    }


def lifecycle_binding(path, authorization, runtime, config, output, *, fresh=True):
    path = private_path(path)
    receipt = read(path)
    _, _, plan = frozen_inputs()
    if fresh:
        finished = recent(receipt["finishedAt"], 86400)
        reviewed = recent(authorization["reviewedAt"], 900)
        if datetime.fromisoformat(receipt["startedAt"]) > finished or reviewed < finished:
            raise ValueError("lifecycle/review timestamps are out of order")
    checks = receipt.get("checks", [])
    if (
        receipt.get("schema") != "fli.retrieval-i1-lifecycle.v1"
        or receipt.get("status") != "complete"
        or receipt.get("live") is not True
        or receipt.get("authenticationMode") != "dev"
        or receipt.get("profile") != "microsoft-indexer-layout-v1"
        or receipt.get("experimentPlanSha256") != PLAN_SHA256
        or receipt.get("apiSourceSha256") != runtime["apiSourceSha256"]
        or receipt.get("settingsBindingSha256") != runtime["settingsBindingSha256"]
        or len(checks) != len(plan["requiredLifecycleChecks"])
        or {c.get("id") for c in checks} != set(plan["requiredLifecycleChecks"])
        or any(c.get("passed") is not True for c in checks)
        or not receipt.get("ownedLibraryIds")
        or config["libraryId"] in receipt["ownedLibraryIds"]
    ):
        raise ValueError("passing live lifecycle evidence is required before held-out execution")
    paths, expected = receipt.get("rawReceiptPaths", {}), receipt.get("rawReceiptsSha256", {})
    if not paths or set(paths) != set(expected):
        raise ValueError("lifecycle raw receipt bindings are incomplete")
    for name, location in paths.items():
        if sha(private_path(location)) != expected[name]:
            raise ValueError("lifecycle raw evidence changed")
    if (
        authorization.get("schema") != "fli.i1.execution-authorization.v1"
        or authorization.get("status") != "accepted"
        or authorization.get("lifecycleReceiptSha256") != sha(path)
        or authorization.get("experimentPlanSha256") != PLAN_SHA256
        or authorization.get("settingsBindingSha256") != runtime["settingsBindingSha256"]
        or authorization.get("libraryId") != config["libraryId"]
        or private_path(authorization["outputDirectory"]) != private_path(output)
    ):
        raise ValueError("coordinator review must bind the exact lifecycle, plan and execution")
    ledger = private_path(authorization["executionLedger"])
    if ledger == private_path(output) or ledger.is_relative_to(private_path(output)):
        raise ValueError("execution ledger must survive outside the run directory")
    return {"receiptSha256": sha(path), "authorizationSha256": digest(authorization), "executionLedger": str(ledger)}


def validate_config(config):
    if set(config) != {"model", "agent", "libraryId", "indexUrl", "searchMode"}:
        raise ValueError("unexpected private configuration fields")
    if config["agent"].get("instructions") != AGENT_INSTRUCTIONS or config.get("searchMode") != "vector":
        raise ValueError("agent/query controls differ from R0")
    if set(config["model"]) != {"model", "version", "deployment"}:
        raise ValueError("model configuration may not override frozen controls")
    answer_controls({**ANSWER_CONTROLS, "model": config["model"], "agent": config["agent"]})


def validate_heldout_sources(frozen, config, manifest):
    # Reuse exact profile/metadata/generation checks with an explicit new selection;
    # the committed manifest and R0/P1 development selection are never changed.
    selected = copy.deepcopy(manifest)
    selected["baseline"]["sourceIds"] = list(SOURCE_IDS)
    validate_sources(frozen, {"libraries": {"microsoft": config["libraryId"]}}, selected)
    captured = frozen["libraries"][config["libraryId"]]
    recipe = captured["library"]["ingestionProfile"]
    if recipe["embedding"].get("modelRevision") != "1":
        raise ValueError("held-out source embedding revision differs from fixed query revision")
    for document in captured["documents"]:
        job = document.get("ingestionJob") or {}
        if (
            job.get("profile") != recipe
            or job.get("generation") != document["activeGeneration"]
            or job.get("sourceSha256") != document["sha256"]
        ):
            raise ValueError("active held-out generation does not bind the frozen source recipe")


def cell_snapshot(baseline, cell):
    expected = copy.deepcopy(baseline)
    if cell == "selectors-only":
        for library in expected["libraries"].values():
            library["tools"] = tool_hints(library["tools"])
    return expected


def run(config, before_receipt, runtime_receipt, lifecycle_path, authorization, output):
    manifest, cases, plan = frozen_inputs()
    validate_config(config)
    runtime = runtime_binding(runtime_receipt, config)
    if runtime_receipt["settingsBinding"]["AOAI_ENDPOINT"] != os.environ.get("AOAI_ENDPOINT"):
        raise ValueError("invoked answering endpoint differs from captured runtime")
    lifecycle = lifecycle_binding(lifecycle_path, authorization, runtime, config, output)
    deployment = bound_deployment(before_receipt, config)
    root = prepare_output(private_path(output))
    controls = {
        **ANSWER_CONTROLS,
        "model": config["model"],
        "agent": config["agent"],
        "plan": plan,
        "cases": cases,
        "sourceStrategy": plan["sourceStrategy"],
        "searchMode": "vector",
        "evidenceView": "compact",
        "heldoutPlanned": True,
        "codeSha256": hashes(),
        "deploymentBinding": deployment,
        "runtimeBinding": runtime,
        "lifecycleBinding": lifecycle,
    }
    completed, started = 0, False
    try:
        agent = {**config["agent"], "libraryIds": [config["libraryId"]]}
        index = make_index(config, "baseline")
        baseline = snapshot(index, agent)
        validate_heldout_sources(baseline, config, manifest)
        for name, value in (
            ("source-snapshot", baseline),
            ("controls", controls),
            ("private-config", config),
            ("deployment-before", before_receipt),
            ("runtime-before", runtime_receipt),
            ("authorization", authorization),
        ):
            save(root / f"{name}.json", value)
        # Authorization binds original file bytes, not a reformatted equivalent.
        (root / "lifecycle.json").write_bytes(private_path(lifecycle_path).read_bytes())
        (root / "lifecycle.json").chmod(0o600)
        ledger = Path(lifecycle["executionLedger"])
        ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation prevents accidental resubmission under a fresh output
        # directory. Failures remain claimed: no held-out feedback-driven rerun.
        with ledger.open("x") as handle:
            json.dump(
                {"planSha256": PLAN_SHA256, "outputDirectory": str(root), "claimedAt": datetime.now(UTC).isoformat()},
                handle,
            )
        ledger.chmod(0o600)
        for repeat in (1, 2):
            for cell in CELLS if repeat == 1 else tuple(reversed(CELLS)):
                if hashes() != controls["codeSha256"] or snapshot(index, agent) != baseline:
                    raise RuntimeError("source or code drift before I1 cell")
                member, expected = make_index(config, cell), cell_snapshot(baseline, cell)
                if snapshot(member, agent) != expected:
                    raise RuntimeError("unexpected I1 source/tool difference")
                wire = root / f"wire-r{repeat}-{cell}"
                wire.mkdir(mode=0o700)
                chat = RecordedChat(default_chat_factory(config["model"], "responses"), wire, cell)
                started = True
                rows = replay(member, chat, agent, copy.deepcopy(cases), root / f"r{repeat}-{cell}", budget=200000)
                verify_cell(root / f"r{repeat}-{cell}", cases, expected)
                completed += len(rows)
                if hashes() != controls["codeSha256"] or snapshot(index, agent) != baseline:
                    raise RuntimeError("source or code drift after I1 cell")
                save(
                    root / "run-status.json",
                    {"status": "running", "completedTurns": completed, "heldoutExecuted": True},
                )
    except (Exception, KeyboardInterrupt) as error:
        save(
            root / "run-status.json",
            {
                "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                "completedTurns": completed,
                "heldoutExecuted": started,
                "errorType": type(error).__name__,
                "unfinishedUsage": "Completed cells and partial cell/wire receipts retained; "
                "unavailable counters are not zero. No automatic rerun.",
            },
        )
        raise
    result = {"status": "pending_deployment_recheck", "completedTurns": completed, "heldoutExecuted": True}
    save(root / "run-status.json", result)
    return result


def verify_execution(root):
    manifest, cases, plan = frozen_inputs()
    controls, config = read(root / "controls.json"), read(root / "private-config.json")
    validate_config(config)
    answer_controls(controls)
    if controls["plan"] != plan or controls["cases"] != cases or controls["codeSha256"] != hashes():
        raise ValueError("I1 code or plan changed during comparison")
    baseline = read(root / "source-snapshot.json")
    if runtime_binding(read(root / "runtime-before.json"), config, fresh=False) != controls["runtimeBinding"]:
        raise ValueError("saved API runtime control is inconsistent")
    validate_heldout_sources(baseline, config, manifest)
    binding = lifecycle_binding(
        root / "lifecycle.json",
        read(root / "authorization.json"),
        controls["runtimeBinding"],
        config,
        root,
        fresh=False,
    )
    if binding != controls["lifecycleBinding"]:
        raise ValueError("lifecycle or authorization binding changed")
    ledger = read(private_path(binding["executionLedger"]))
    if ledger.get("planSha256") != PLAN_SHA256 or private_path(ledger["outputDirectory"]) != root:
        raise ValueError("one-time held-out execution claim changed")
    for repeat in (1, 2):
        for cell in CELLS:
            verify_cell(root / f"r{repeat}-{cell}", cases, cell_snapshot(baseline, cell))
    return controls, config, baseline


def finalize(directory, after_receipt, runtime_receipt):
    root = private_path(directory)
    controls, config, baseline = verify_execution(root)
    if read(root / "run-status.json") != {
        "status": "pending_deployment_recheck",
        "completedTurns": 64,
        "heldoutExecuted": True,
    }:
        raise ValueError("only the entire declared I1 matrix can be finalized")
    if bound_deployment(after_receipt, config) != controls["deploymentBinding"]:
        raise ValueError("answering deployment changed")
    if runtime_binding(runtime_receipt, config) != controls["runtimeBinding"]:
        raise ValueError("API runtime changed")
    index = make_index(config, "baseline")
    if snapshot(index, {**config["agent"], "libraryIds": [config["libraryId"]]}) != baseline:
        raise ValueError("source changed after I1 replay")
    save(root / "deployment-after.json", after_receipt)
    save(root / "runtime-after.json", runtime_receipt)
    save(
        root / "finalization.json",
        {
            "deploymentBinding": controls["deploymentBinding"],
            "runtimeBinding": controls["runtimeBinding"],
            "artifactsSha256": {
                name: sha(root / name)
                for name in (
                    "deployment-before.json",
                    "deployment-after.json",
                    "runtime-before.json",
                    "runtime-after.json",
                    "source-snapshot.json",
                    "controls.json",
                    "lifecycle.json",
                    "authorization.json",
                )
            },
        },
    )
    result = {
        "status": "complete",
        "completedTurns": 64,
        "heldoutExecuted": True,
        "sourcesUnchanged": True,
        "deploymentUnchanged": True,
        "runtimeUnchanged": True,
    }
    save(root / "run-status.json", result)
    return result


def reviewed_regions(case, review, gold):
    required = {r["id"] for fact in case["sourceFacts"] for r in fact["regions"]}
    delivered = review.get("deliveredRegionIds")
    if not isinstance(delivered, list) or len(delivered) != len(set(delivered)) or not set(delivered) <= required:
        raise ValueError("review needs distinct original delivered region IDs")
    blank_required = {r for r in required if not normalize(gold["regions"][r]["expectedText"])}
    blank_verified = review.get("blankRegionIdsVerified")
    if (
        not isinstance(blank_verified, list)
        or len(blank_verified) != len(set(blank_verified))
        or set(blank_verified) != set(delivered) & blank_required
    ):
        raise ValueError("blank paragraphs/cells require explicit source-coordinate review")
    relevant = relevant_source_coverage(case, review["relevantSourceCoverage"])
    if relevant is None or relevant["deliveredRegionCount"] != len(delivered):
        raise ValueError("review count differs from exact original region set")
    return sorted(delivered), relevant


def verify_i1_wire_sources(calls, results):
    """Independent tasks retain every prior source message through the final call."""
    final = verify_wire_sources(calls, results)
    by_question = {row["question"]: row for row in results}
    for call in calls:
        questions = [m.get("content") for m in call["messages"] if m.get("role") == "user"]
        if not questions or questions[0] not in by_question:
            raise ValueError("I1 provider input contains a non-frozen or shared task history")
        for followup in questions[1:]:
            repair = (
                isinstance(followup, str)
                and followup.startswith("Before finalizing, correct these unmet requirements: ")
                and followup.endswith("If the source cannot support a deliverable, explain the limit.")
            )
            limit = followup == (
                "Tool-round limit reached. Answer from content actually received. "
                "Explicitly disclose any unfinished full-document reads or catalogue pages; "
                "never claim exhaustive coverage."
            )
            images = (
                isinstance(followup, list)
                and bool(followup)
                and isinstance(followup[0], dict)
                and followup[0]
                .get("text", "")
                .startswith("Continue answering only the current user request: " + questions[0] + "\n")
            )
            if not (repair or limit or images):
                raise ValueError("I1 provider input contains a non-frozen or shared task history")
        actual = [json.loads(m["content"]) for m in call["messages"] if m.get("role") == "tool"]
        if actual != by_question[questions[0]]["toolReplies"][: len(actual)]:
            raise ValueError("earlier provider evidence differs from final source sequence")
    return final


def paired_comparison(rows, cases):
    paired = []
    by_key = {(r["repeat"], r["cell"], r["case"]): r for r in rows}
    for repeat in (1, 2):
        for case in cases:
            before, after = (by_key[repeat, cell, case["case"]] for cell in CELLS)
            applicable_quality = ["taskCompletion", "factualSupport", "citationAccuracy"]
            applicable_coverage = []
            if case.get("relationshipReviewRequired"):
                applicable_quality.append("sourceRelationships")
                applicable_coverage.append("relationshipsDelivered")
            if case.get("family") == "complete-section-read":
                applicable_quality.append("sourceRelationships")
                applicable_coverage.append("completeOrderedSection")
            unresolved = [
                f"{cell}:{field}"
                for cell, row in zip(CELLS, (before, after), strict=True)
                for field in set(applicable_quality)
                if row["quality"][field] is None
            ] + [
                f"{cell}:{field}"
                for cell, row in zip(CELLS, (before, after), strict=True)
                for field in applicable_coverage
                if row["relevantSourceCoverage"][field] is None
            ]
            lost = sorted(set(before["deliveredRegionIds"]) - set(after["deliveredRegionIds"]))
            regressed = [
                field for field in QUALITY if before["quality"][field] is True and after["quality"][field] is not True
            ]
            for field in ("relationshipsDelivered", "completeOrderedSection", "catalogueComplete"):
                if (
                    before["relevantSourceCoverage"][field] is True
                    and after["relevantSourceCoverage"][field] is not True
                ):
                    regressed.append(field)
            old_errors = sum(before["errorCategories"].values())
            new_errors = sum(after["errorCategories"].values())
            paired.append(
                {
                    "repeat": repeat,
                    "case": case["case"],
                    "lostSourceRegionIds": lost,
                    "qualityRegressions": regressed,
                    "unresolvedQualityFields": sorted(unresolved),
                    "selectorErrorsBefore": old_errors,
                    "selectorErrorsAfter": new_errors,
                    "noRegression": not lost and not regressed and not unresolved and new_errors <= old_errors,
                }
            )
    return paired


def score(directory, gold, reviews):
    root = private_path(directory)
    controls, config, baseline = verify_execution(root)
    if read(root / "run-status.json") != {
        "status": "complete",
        "completedTurns": 64,
        "heldoutExecuted": True,
        "sourcesUnchanged": True,
        "deploymentUnchanged": True,
        "runtimeUnchanged": True,
    }:
        raise ValueError("partial I1 runs cannot establish comparison results")
    finalization = read(root / "finalization.json")
    if (
        finalization["deploymentBinding"] != controls["deploymentBinding"]
        or finalization["runtimeBinding"] != controls["runtimeBinding"]
    ):
        raise ValueError("finalization control bindings differ")
    names = {
        "deployment-before.json",
        "deployment-after.json",
        "runtime-before.json",
        "runtime-after.json",
        "source-snapshot.json",
        "controls.json",
        "lifecycle.json",
        "authorization.json",
    }
    if set(finalization["artifactsSha256"]) != names or any(
        sha(root / name) != expected for name, expected in finalization["artifactsSha256"].items()
    ):
        raise ValueError("finalized comparison artifacts changed")
    if (
        runtime_binding(read(root / "runtime-before.json"), config, fresh=False) != controls["runtimeBinding"]
        or runtime_binding(read(root / "runtime-after.json"), config, fresh=False) != controls["runtimeBinding"]
    ):
        raise ValueError("saved runtime controls are inconsistent")
    manifest, cases, _ = frozen_inputs()
    validate_gold(manifest, read(REPO / "docs/testing/retrieval-r0/tasks.json"), gold)
    by_case = {c["case"]: c for c in cases}
    expected = {(repeat, cell, c["case"], 1) for repeat in (1, 2) for cell in CELLS for c in cases}
    judgments = {}
    for review in reviews:
        key = review["repeat"], review["cell"], review["case"], review.get("turn", 1)
        if key not in expected or key in judgments or type(key[0]) is not int or type(key[3]) is not int:
            raise ValueError("unexpected or duplicate I1 judgment")
        if any(
            field not in review or (review[field] is not None and type(review[field]) is not bool) for field in QUALITY
        ):
            raise ValueError("every quality dimension requires explicit review")
        if not set(review.get("failureCategories", [])) <= FAILURES:
            raise ValueError("unknown failure category")
        reviewed_regions(by_case[review["case"]], review, gold)
        judgments[key] = review
    if set(judgments) != expected:
        raise ValueError("all 64 I1 turns require source-based review")
    rows, wire_rows = [], []
    tokenizer = default_tokenizer()
    for repeat in (1, 2):
        for cell in CELLS:
            results = read(root / f"r{repeat}-{cell}/results.json")
            wires = read(root / f"wire-r{repeat}-{cell}/wire-receipts.json")
            if len(wires) != sum(len(r["metrics"]["modelCalls"]) for r in results):
                raise ValueError("wire/model call receipts incomplete")
            calls = []
            for ordinal, wire in enumerate(wires, 1):
                captured = read(root / f"wire-r{repeat}-{cell}/wire-{ordinal:03}.json")
                if (
                    wire["call"] != ordinal
                    or digest(captured["messages"]) != wire["wireSha256"]
                    or wire["referenceToolMessages"] != 0
                ):
                    raise ValueError("actual compact wire evidence changed or differs from cell")
                calls.append(captured)
                wire_rows.append(
                    {
                        "repeat": repeat,
                        "cell": cell,
                        **{
                            k: wire[k]
                            for k in (
                                "call",
                                "wireSha256",
                                "wireInputTokensEstimate",
                                "expandedInputTokensEstimate",
                                "referenceToolMessages",
                            )
                        },
                        "serializedToolEvidenceTokensEstimate": sum(
                            len(tokenizer.encode(m["content"])) for m in captured["messages"] if m.get("role") == "tool"
                        ),
                    }
                )
            delivered = verify_i1_wire_sources(calls, results)
            for result in results:
                case = by_case[result["case"]]
                review = judgments[repeat, cell, result["case"], result["turn"]]
                region_ids, relevant = reviewed_regions(case, review, gold)
                errors = Counter()
                for reply in result["toolReplies"]:
                    error = reply.get("errorCategory") or reply.get("error")
                    if error:
                        errors[
                            next((name for name in sorted(SELECTOR_ERRORS) if name in str(error)), "retrieval_error")
                        ] += 1
                rows.append(
                    {
                        "repeat": repeat,
                        "cell": cell,
                        "case": result["case"],
                        "turn": result["turn"],
                        "sourceDelivery": coverage(case, delivered[result["case"]], gold),
                        "providerSourceSequenceVerified": True,
                        "deliveredRegionIds": region_ids,
                        "blankRegionIdsVerified": sorted(review["blankRegionIdsVerified"]),
                        "relevantSourceCoverage": relevant,
                        "quality": {field: review[field] for field in QUALITY},
                        "failureCategories": review.get("failureCategories", []),
                        "errorCategories": dict(errors),
                        "usage": usage(result),
                    }
                )
    summary = {}
    for cell in CELLS:
        members = [r for r in rows if r["cell"] == cell]
        counters = {}
        for field in (
            "promptTokens",
            "completionTokens",
            "totalTokens",
            "rateLimitRetries",
            "rateLimitWaitMs",
            "retrievalRequestCharge",
            "totalMs",
            "toolErrors",
            "toolBudgetRefusals",
        ):
            values = [r["usage"][field] for r in members]
            counters[field] = sum(values) if all(v is not None for v in values) else None
            counters[field + "KnownSubtotal"] = sum(v for v in values if v is not None)
        counters["retrievalChargeComplete"] = all(r["usage"]["retrievalChargeComplete"] is True for r in members)
        if not counters["retrievalChargeComplete"]:
            counters["retrievalRequestCharge"] = None
        counters["serializedToolEvidenceTokensEstimate"] = sum(
            w["serializedToolEvidenceTokensEstimate"] for w in wire_rows if w["cell"] == cell
        )
        summary[cell] = {
            "turns": len(members),
            "quality": {
                field: {
                    "passed": sum(r["quality"][field] for r in members if r["quality"][field] is not None),
                    "applicable": sum(r["quality"][field] is not None for r in members),
                }
                for field in QUALITY
            },
            "usage": counters,
        }
    paired = paired_comparison(rows, cases)
    before, after = (summary[cell]["usage"]["promptTokens"] for cell in CELLS)
    improvement = 1 - after / before if before and after is not None else None
    conclusive = all(not p["unresolvedQualityFields"] for p in paired)
    source_quality = (
        all(not p["lostSourceRegionIds"] and not p["qualityRegressions"] for p in paired) if conclusive else None
    )
    unsupported = {
        cell: sum(r["errorCategories"].get("source_scope_unavailable", 0) for r in rows if r["cell"] == cell)
        for cell in CELLS
    }
    return {
        "schema": "fli.i1.scorecard.v1",
        "status": "complete",
        "heldoutExecuted": True,
        "model": {"model": "gpt-6.1-sol", "version": "2026-09-29"},
        "contextAllowancePerRun": 200000,
        "planSha256": PLAN_SHA256,
        "controlsSha256": digest(controls),
        "codeSha256": controls["codeSha256"],
        "lifecycleReceiptSha256": controls["lifecycleBinding"]["receiptSha256"],
        "reviewMethod": "original-source and actual provider-wire assistant review; not blinded human accuracy",
        "summary": summary,
        "rows": rows,
        "paired": paired,
        "wireReceipts": wire_rows,
        "gates": {
            "sourceAndQualityNoRegression": source_quality,
            "qualityGateConclusive": conclusive,
            "providerInputReductionFraction": improvement,
            "efficiencyTargetMet": improvement is not None and improvement >= 0.2 and source_quality is True,
            "usabilityNoRegression": all(p["noRegression"] for p in paired),
            "unsupportedExpansionBenefitObserved": unsupported["selectors-only"] < unsupported["baseline"],
            "promotion": "requires coordinator decision; no automatic default or architecture change",
        },
        "limitations": [
            "Unblinded assistant review",
            "Cache/provider rate waits are not controlled",
            "No production Entra authentication",
            "No broker worker redelivery proof",
            "No new foundation/format or R3 comparison",
            "No complete billed cost",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["replay", "finalize", "score"])
    for name in ("config", "deployment", "runtime", "lifecycle", "authorization", "source", "gold", "reviews"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    required = {
        "replay": ("config", "deployment", "runtime", "lifecycle", "authorization"),
        "finalize": ("deployment", "runtime"),
        "score": ("source", "gold", "reviews"),
    }[args.action]
    if any(getattr(args, name) is None for name in required):
        parser.error("required: " + ", ".join("--" + name for name in required))
    inputs = {name: read(private_path(getattr(args, name))) for name in required if name not in {"source", "lifecycle"}}
    if args.action == "replay":
        result = run(
            inputs["config"],
            inputs["deployment"],
            inputs["runtime"],
            args.lifecycle,
            inputs["authorization"],
            args.output,
        )
    elif args.action == "finalize":
        result = finalize(args.output, inputs["deployment"], inputs["runtime"])
    else:
        result = score(args.source, inputs["gold"], inputs["reviews"])
        save(args.output, result)
    print(json.dumps(result.get("gates", result)))


if __name__ == "__main__":
    main()
