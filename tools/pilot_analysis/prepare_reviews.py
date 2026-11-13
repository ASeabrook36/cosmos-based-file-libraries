#!/usr/bin/env python3
"""Private source-based review preparation; no model calls or automatic judgments.

Completed four-cell question groups can be inspected provisionally. A final verify
must rebind every packet and raw input to the completed pilot before acceptance.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.umask(0o077)
REFERENCE = "$privateEvidenceBodySha256"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    with Path(path).open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    Path(path).chmod(0o600)


def load_pilot(repo):
    repo = Path(repo).resolve()
    require(Path.cwd().resolve() == repo, "run helper from the experiment checkout")
    sys.path.insert(0, str(repo))
    from scripts import hybrid_model_pilot as p

    require(Path(sys.prefix).resolve() == repo / ".venv", "use this worktree's Python")
    return p


def packet_for(p, run, member, task, baseline, gold):
    case = {"case": task["case"], "question": task["question"]}
    p.verify_cell(run / member["run"], case, baseline)
    result, evidence, _ = p.h.wire_result(run, member)
    regions = [r["id"] for f in task["sourceFacts"] for r in f["regions"]]
    return {
        "reviewId": member["reviewId"],
        "case": task["case"],
        "question": task["question"],
        "answerable": task["answerable"],
        "answer": result["answer"],
        "evidence": p.h.blinded_evidence(evidence),
        "originalSourceRegions": {k: gold["regions"][k] for k in regions},
        "requiredFacts": {k: gold["facts"][k] for k in task["requiredFactIds"]},
        "goldTask": gold.get("tasks", {}).get(task["case"]),
        "reviewTemplate": {
            "reviewId": member["reviewId"],
            **dict.fromkeys(p.QUALITY),
            **{
                k: []
                for k in (
                    "deliveredRegionIds",
                    "supportedFactIds",
                    "fullReadRegionIds",
                    "searchRegionIds",
                    "selectedReadRegionIds",
                    "blankRegionIdsVerified",
                )
            },
        },
    }


def factor(packet, bodies):
    """Deduplicate text only; preserve every reply, nested field and ordering."""

    def visit(value):
        if isinstance(value, list):
            return [visit(v) for v in value]
        if isinstance(value, dict):
            require(REFERENCE not in value, "reserved factoring key present")
            result = {}
            for key, child in value.items():
                if key == "text" and isinstance(child, str):
                    body = child.encode()
                    hashed = hashlib.sha256(body).hexdigest()
                    target = bodies / (hashed + ".txt")
                    if target.exists():
                        require(target.read_bytes() == body, "factored body collision")
                    else:
                        with target.open("xb") as f:
                            f.write(body)
                        target.chmod(0o600)
                    result[key] = {REFERENCE: hashed}
                else:
                    result[key] = visit(child)
            return result
        return value

    result = copy.deepcopy(packet)
    result["evidence"] = visit(packet["evidence"])
    return result


def hydrate(packet, bodies):
    def visit(value):
        if isinstance(value, list):
            return [visit(v) for v in value]
        if isinstance(value, dict):
            if set(value) == {REFERENCE}:
                hashed = value[REFERENCE]
                require(len(hashed) == 64 and all(c in "0123456789abcdef" for c in hashed), "body identifier")
                path = bodies / (hashed + ".txt")
                require(sha(path) == hashed, "factored body changed")
                return path.read_text()
            return {k: visit(v) for k, v in value.items()}
        return value

    restored = copy.deepcopy(packet)
    restored["evidence"] = visit(packet["evidence"])
    return restored


def prerequisites(p, run, gold_path):
    controls, baseline = read(run / "controls.json"), read(run / "source-snapshot.json")
    plan, manifest, tasks = p.inputs(run / "plan.json", run / "manifest.json", run / "tasks.json")
    require(controls["plan"] == plan and controls["codeSha256"] == p.h.hashes(), "code/plan changed")
    blind = read(run / "blind-map.json")
    require(p.digest(blind) == controls["blindMapSha256"], "blind map changed")
    expected = p.schedule(tasks)
    require([{k: row[k] for k in expected[0]} for row in blind] == expected, "schedule changed")
    config = read(run / "private-config.json")
    p.validate_config(config)
    p.h.validate_snapshot(baseline, config, manifest)
    gold = p.h.gold_check(gold_path, plan, manifest, tasks)
    return controls, baseline, {t["case"]: t for t in tasks}, blind, gold


def prepare(p, run, gold_path, output, cases=None):
    run, gold_path, output = map(lambda v: p.private_path(v), (run, gold_path, output))
    controls, baseline, tasks, blind, gold = prerequisites(p, run, gold_path)
    requested = set(cases or tasks)
    require(requested <= tasks.keys(), "unknown requested question")
    complete = []
    for case in sorted(requested):
        group = [m for m in blind if m["case"] == case]
        require(len(group) == 4, "not four cells per question")
        statuses = [run / m["run"] / "run-status.json" for m in group]
        if all(
            path.exists() and read(path) == {"status": "complete", "completedTurns": 1, "budget": 64000}
            for path in statuses
        ):
            complete.extend(group)
    require(complete, "no completed four-cell question group yet")
    # Validate all packets before creating output; no answer/coverage judgments copied or inferred.
    packets = [(m, packet_for(p, run, m, tasks[m["case"]], baseline, gold)) for m in complete]
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name in ("packets", "factored", "bodies"):
        (output / name).mkdir(mode=0o700)
    receipt = {
        "schema": "fli.private.hybrid-model-pilot.review-batch.v1",
        "provisional": True,
        "createdAt": datetime.now(UTC).isoformat(),
        "producerSha256": sha(__file__),
        "sourceRun": str(run),
        "goldPath": str(gold_path),
        "goldSha256": sha(gold_path),
        "sourceControlsSha256": sha(run / "controls.json"),
        "sourceBlindMapSha256": sha(run / "blind-map.json"),
        "semanticJudgmentsGenerated": False,
        "priorQualityLabelsUsed": False,
        "inputsSha256": {
            n: sha(run / n)
            for n in (
                "controls.json",
                "source-snapshot.json",
                "private-config.json",
                "plan.json",
                "manifest.json",
                "tasks.json",
                "blind-map.json",
            )
        },
        "packets": [],
        "limitation": (
            "Provisional completed-cell evidence; acceptance requires whole-run finalization and verify-final."
        ),
    }
    index = {}
    for member, packet in sorted(packets, key=lambda pair: pair[0]["reviewId"]):
        rid = member["reviewId"]
        target = output / "packets" / (rid + ".json")
        save(target, packet)
        factored = factor(packet, output / "bodies")
        require(hydrate(factored, output / "bodies") == packet, "lossless factoring failed")
        save(output / "factored" / (rid + ".json"), factored)
        sources = [
            path
            for name in (member["run"], "wire-" + member["run"])
            for path in sorted((run / name).rglob("*"))
            if path.is_file()
        ]
        # This coordinator-only receipt contains raw paths; reviewers read packets/factored/index only.
        receipt["packets"].append(
            {
                "reviewId": rid,
                "case": member["case"],
                "packetSha256": sha(target),
                "canonicalPacketSha256": digest(packet),
                "factoredSha256": sha(output / "factored" / (rid + ".json")),
                "sourceInputsSha256": {str(path.relative_to(run)): sha(path) for path in sources},
            }
        )
        index.setdefault(member["case"], []).append(rid)
    save(output / "case-index.json", index)
    save(output / "batch-receipt.json", receipt)
    return {
        "status": "provisional",
        "questions": len(index),
        "packets": len(packets),
        "uniqueBodies": len(list((output / "bodies").iterdir())),
        "judgmentsGenerated": False,
    }


def verify_final(p, batch, output):
    batch, output = p.private_path(batch), p.private_path(output)
    receipt = read(batch / "batch-receipt.json")
    require(receipt["producerSha256"] == sha(__file__), "review producer changed")
    run, gold_path = p.private_path(receipt["sourceRun"]), p.private_path(receipt["goldPath"])
    p.finalized(run)  # Full live run must be finalized; never infer this from per-cell completion.
    _, baseline, tasks, blind, gold = prerequisites(p, run, gold_path)
    require(sha(gold_path) == receipt["goldSha256"], "gold changed")
    for name, hashed in receipt["inputsSha256"].items():
        require(sha(run / name) == hashed, "provisional common input changed")
    by_id = {m["reviewId"]: m for m in blind}
    require(len({r["reviewId"] for r in receipt["packets"]}) == len(receipt["packets"]), "duplicate packet receipt")
    for row in receipt["packets"]:
        rid = row["reviewId"]
        require(sha(batch / "packets" / (rid + ".json")) == row["packetSha256"], "review packet changed")
        require(sha(batch / "factored" / (rid + ".json")) == row["factoredSha256"], "factored packet changed")
        for name, hashed in row["sourceInputsSha256"].items():
            require(sha(run / name) == hashed, "provisional cell input changed")
        member = by_id[rid]
        packet = packet_for(p, run, member, tasks[member["case"]], baseline, gold)
        require(packet == read(batch / "packets" / (rid + ".json")), "final packet differs from provisional packet")
        require(digest(packet) == row["canonicalPacketSha256"], "canonical packet changed")
        require(
            hydrate(read(batch / "factored" / (rid + ".json")), batch / "bodies") == packet,
            "factored body reconstruction differs",
        )
    result = {
        "schema": "fli.private.hybrid-model-pilot.review-batch-finalization.v1",
        "status": "accepted",
        "packets": len(receipt["packets"]),
        "sourceFinalizationSha256": sha(run / "finalization.json"),
        "reviewBatchReceiptSha256": sha(batch / "batch-receipt.json"),
        "verifiedAt": datetime.now(UTC).isoformat(),
        "limitation": "Input identity verified; review judgments still require independent source assessment.",
    }
    save(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "verify-final"))
    for name in ("repo", "run", "gold", "output", "batch"):
        parser.add_argument("--" + name, type=Path, required=name in {"repo", "output"})
    parser.add_argument("--cases", nargs="*")
    args = parser.parse_args()
    p = load_pilot(args.repo)
    if args.operation == "prepare":
        require(args.run and args.gold, "--run and --gold required")
        result = prepare(p, args.run, args.gold, args.output, args.cases)
    else:
        require(args.batch, "--batch required")
        result = verify_final(p, args.batch, args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
