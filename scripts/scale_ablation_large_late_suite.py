"""Isolated late routed epoch. Default is a read-only local dry run.

--execute is for use only after the user's separate approval. No write, model,
exact-reference or no-prefix operation is part of this supplementary epoch.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from scripts import scale_ablation_large as original
from scripts import scale_ablation_large_resume as repair
from scripts.scale_ablation import private_path, read, save
from scripts.scale_ablation_large_exact import validate_oracle

PARENT_RECORD = "records/scale-ablation-large-20261113.json"
PARENT_TABLE = "docs/scale-ablation-large-results.md"
PROPOSAL = "docs/scale-ablation-large-late-suite-proposal.md"
PARENT_SHA = "6df1337e5d54e583b81ba7eee5469d8b67b8b9cd05ab9242bb82952879e17e19"
CAP_RU, CAP_SECONDS, RESERVE_RU = 1_000_000, 1800, 300_000
PHASE = "late-suite"


def require(value, reason):
    if not value:
        raise original.RunStopped(reason)


def parent_snapshot(parent):
    files = {
        "parent/" + str(p.relative_to(parent)): original.sha(p)
        for p in sorted(parent.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }
    files.update({name: original.sha(original.REPO / name) for name in (PARENT_RECORD, PARENT_TABLE)})
    return files


def query_plan(selected):
    libraries = sorted(["scale-p01", "scale-p10", *selected], key=lambda lib: original.SIZES[lib])
    rows = [
        (arm, lib, "routed", q, run)
        for run in range(3)
        for n, lib in enumerate(libraries)
        for q in range(20)
        for arm in original.arm_order(run, q, n)
    ]
    return rows + [(arm, "global", "global", 0, run) for run in range(3) for arm in original.arm_order(run, 0)]


def plan(parent, state):
    parent, state = private_path(parent), private_path(state)
    require(
        not state.is_relative_to(parent) and not parent.is_relative_to(state), "supplementary state must be separate"
    )
    require(original.sha(original.REPO / PARENT_RECORD) == PARENT_SHA, "immutable parent record differs")
    status, config = read(parent / "status.json"), read(parent / "config.json")
    require(status.get("complete") is True and status.get("phase") == "complete", "parent experiment is incomplete")
    require(status.get("selectedLibraries") == list(original.NEW_LIBRARIES), "parent six-library coverage differs")
    for filename, archive, members in (
        ("binding.json", "frozen-source", original.SOURCE_FILES),
        ("reference-repair-binding.json", "repair-source", repair.REPAIR_SOURCES),
    ):
        source = read(parent / filename)["sourceFiles"]
        require(set(source) == set(members), "parent source membership differs")
        require(
            all(
                original.sha(original.REPO / name) == digest and original.sha(parent / archive / name) == digest
                for name, digest in source.items()
            ),
            "parent frozen measurement sources differ",
        )
    record = read(original.REPO / PARENT_RECORD)
    require(
        original.sha(parent / "offline-exact.json") == record["referenceRepair"]["oracleFileSha256"],
        "parent exhaustive references differ",
    )
    known = read(parent / "budget.json")["knownRu"]
    require(abs(known - record["costAccounting"]["knownRu"]) < 0.01, "parent charge ledger differs")
    hashes = parent_snapshot(parent)
    binding = {
        "parentRecordSha256": PARENT_SHA,
        "parentEndStateSha256": repair.digest(hashes),
        "parentFiles": hashes,
        "sourceFiles": {
            name: original.sha(original.REPO / name)
            for name in ("scripts/scale_ablation_large_late_suite.py", PROPOSAL)
        },
        "queryPlanSha256": repair.digest({"rows": query_plan(status["selectedLibraries"])}),
        "parentKnownRu": known,
        "additionalKnownRuCap": CAP_RU,
        "elapsedSecondsCap": CAP_SECONDS,
        "notBefore": original.late_due(status["lastWriteUpperBoundAt"]),
    }
    return config, binding


class Ledger:
    def __init__(self, state, baseline, started):
        self.path, self.started = state / "budget.json", started
        self.data = read(
            self.path, {"parentKnownRu": baseline, "knownRu": baseline, "additionalKnownRu": 0, "categories": {}}
        )
        values = [
            baseline,
            self.data["parentKnownRu"],
            self.data["knownRu"],
            self.data["additionalKnownRu"],
            *self.data["categories"].values(),
        ]
        require(
            all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values),
            "supplementary ledger has invalid charges",
        )
        require(self.data["parentKnownRu"] == baseline, "supplementary parent ledger seed differs")
        require(
            abs(self.data["knownRu"] - baseline - self.data["additionalKnownRu"]) < 0.01
            and abs(sum(self.data["categories"].values()) - self.data["additionalKnownRu"]) < 0.01,
            "supplementary ledger does not reconcile",
        )
        if not self.path.exists():
            save(self.path, self.data)

    def remaining(self):
        return max(0.001, CAP_SECONDS - (datetime.now(UTC) - original.parse_time(self.started)).total_seconds())

    def check(self, *, late=False, additional_ru=0, remaining_seconds=0):
        require(self.data["additionalKnownRu"] + additional_ru < CAP_RU, "supplementary visible-RU limit reached")
        require(self.data["knownRu"] + additional_ru < 64_000_000, "extension reserve threshold reached")
        require(self.remaining() > max(0.001, remaining_seconds), "supplementary thirty-minute limit reached")

    def add(self, category, charge):
        require(type(charge) in (int, float) and 0 <= charge < float("inf"), "invalid supplementary charge")
        self.data["knownRu"] += charge
        self.data["additionalKnownRu"] += charge
        self.data["categories"][category] = self.data["categories"].get(category, 0) + charge
        save(self.path, self.data)


class Runner(repair.Runner):
    def __init__(self, parent, state):
        started = original.utc_now()
        self.parent, self.state = private_path(parent), private_path(state)
        require(
            not self.state.is_relative_to(self.parent) and not self.parent.is_relative_to(self.state),
            "supplementary state must be separate",
        )
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        checkpoints = [(self.state / name).exists() for name in ("binding.json", "status.json", "budget.json")]
        require(not any(checkpoints) or all(checkpoints), "supplementary checkpoints are incomplete; decision required")
        require(all(checkpoints) or not any(self.state.iterdir()), "unbound supplementary files already exist")
        old = read(self.state / "binding.json")
        status = read(self.state / "status.json", {"startedAt": started, "phase": "preparing"})
        require(not status.get("complete"), "supplementary epoch already complete")
        require(status.get("phase") != "stopped", "stopped supplementary epoch needs an explicit decision")
        require(not status.get("inFlight"), "interrupted supplementary request needs an explicit decision")
        # An interrupted first binding stays incomplete and cannot reset the timer on resume.
        save(self.state / "status.json", status)
        config, binding = plan(parent, state)
        require(old in (None, binding), "supplementary epoch binding differs")
        original.Runner.__init__(self, config, parent)  # Read-only constructor; never run/bind the parent.
        parent_status = self.status
        self.parent, self.state, self.status = private_path(parent), private_path(state), status
        self.status.update(
            {key: parent_status.get(key) for key in ("selectedLibraries", "lastWriteAckAt", "lastWriteUpperBoundAt")}
        )
        save(self.state / "status.json", self.status)
        self.started_at = self.status["startedAt"]
        self.ledger = Ledger(self.state, binding["parentKnownRu"], self.started_at)
        self.rows, self.is_late = {}, False  # Use the original query timeout with the new bounded remaining time.
        self.vectors = read(self.parent / "question-vectors.json")
        self.oracle = read(self.parent / "offline-exact.json")
        save(self.state / "binding.json", binding)
        self.binding = binding
        validate_oracle(config, self.parent, self.oracle)

    def remaining_main_seconds(self):
        return self.ledger.remaining()

    def validate_rows(self, *, complete=False):
        rows = self.phase_rows(PHASE)
        keys = [tuple(row.get(key) for key in ("arm", "library", "shape", "query", "run")) for row in rows]
        expected = set(query_plan(self.selected))
        require(
            len(keys) == len(set(keys)) and set(keys) <= expected, "supplementary coverage has extras or duplicates"
        )
        for row in rows:
            require(row.get("kind") == "vector", "supplementary row is not ANN")
            repair.validate_observation(row)
        charge = sum(row.get("known_attempt_charge") or row["request_charge"] for row in rows)
        require(
            abs(charge - self.ledger.data["categories"].get(PHASE + "-vector", 0)) < 0.01,
            "supplementary ANN charges and observations do not reconcile",
        )
        require(not complete or set(keys) == expected, "supplementary coverage is incomplete")

    async def inventory(self, label):
        """The frozen inventory guards, with partial query charges retained on cancellation."""
        expected_libraries = {
            lib.library_id for stage in (1, 2, 3, 4) for lib in original.scale_probe.stage_libraries(stage)
        } | set(self.selected)
        result = {
            "expectedVectors": self.inventory_size(),
            "checkedAt": original.utc_now(),
            "partial": True,
            "arms": {},
        }

        async def items(container, sql, **kwargs):
            tally = original.RawTally()
            try:
                return [v async for v in container.query_items(sql, raw_response_hook=tally, **kwargs)]
            finally:
                self.ledger.add("inventory", tally.request_charge)

        try:
            for arm in original.ARMS:
                self.ledger.check(additional_ru=RESERVE_RU)
                container = self.clients[arm][2]
                stored = await container.read()
                problems = original.definition_mismatches(original.chunks_definition(arm), stored)
                if arm == "unsplit" and any(
                    "vectorIndexShardKey" in index
                    for index in stored.get("indexingPolicy", {}).get("vectorIndexes", [])
                ):
                    problems.append("unexpected shard property")
                ranges = [v async for v in container.read_feed_ranges(force_refresh=True)]
                require(
                    not problems and len(ranges) == 1,
                    "retained policy or physical range differs from the approved design",
                )
                values = await items(container, "SELECT VALUE COUNT(1) FROM c")
                libraries = set(await items(container, "SELECT DISTINCT VALUE c.libraryId FROM c ORDER BY c.libraryId"))
                counts = {}
                for library in self.libraries():
                    self.ledger.check(additional_ru=RESERVE_RU)
                    count = await items(
                        container,
                        "SELECT VALUE COUNT(1) FROM c WHERE c.libraryId = @libraryId",
                        parameters=[{"name": "@libraryId", "value": library}],
                        partition_key=[library],
                    )
                    require(
                        count == [original.SIZES[library]],
                        "affected library inventory differs from its frozen assignment",
                    )
                    counts[library] = count[0]
                valid = values == [self.inventory_size()] and libraries == expected_libraries
                result["arms"][arm] = {
                    "vectors": values[0] if len(values) == 1 else None,
                    "libraries": len(libraries),
                    "physicalRanges": len(ranges),
                    "policyMatches": True,
                    "perLibrary": counts,
                    "matchesExpected": valid,
                }
                require(valid, "container inventory differs from the approved checkpoint")
            result["partial"] = False
        finally:
            save(self.state / f"inventory-{label}.json", result)

    async def run(self):
        require(datetime.now(UTC) >= original.parse_time(self.binding["notBefore"]), "approved late age not reached")
        self.ledger.check()
        self.validate_rows()
        try:
            await asyncio.wait_for(self.connect(), timeout=min(180, self.ledger.remaining()))
            self.checkpoint("inventory", inFlight=["inventory", "supplementary"])
            await asyncio.wait_for(self.inventory("supplementary"), timeout=min(600, self.ledger.remaining()))
            self.checkpoint("inventory-complete", inFlight=None)
            for arm, library, shape, question, repeat in query_plan(self.selected):
                self.ledger.check(additional_ru=RESERVE_RU)
                self.checkpoint(PHASE, inFlight=[arm, library, shape, question, repeat])
                await self.perform(PHASE, arm, library, shape, "vector", question, repeat)
                self.checkpoint(PHASE, inFlight=None)
                self.ledger.check()
            self.validate_rows(complete=True)
            require(parent_snapshot(self.parent) == self.binding["parentFiles"], "parent artifacts changed")
            self.ledger.check()
            self.checkpoint("complete", complete=True, completedAt=original.utc_now(), parentArtifactsUnchanged=True)
        finally:
            await self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-state", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="only after separate user approval")
    args = parser.parse_args()
    if not args.execute:
        _, binding = plan(args.parent_state, args.state)
        print(
            json.dumps(
                {
                    "proposed": True,
                    "networkCalls": 0,
                    "plannedAnnCalls": 726,
                    "routedCalls": 720,
                    "globalCalls": 6,
                    "binding": {k: v for k, v in binding.items() if k != "parentFiles"},
                }
            )
        )
        return 0
    parent, state = private_path(args.parent_state), private_path(args.state)
    with (parent / "run.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runner = None
        try:
            runner = Runner(parent, state)
            asyncio.run(runner.run())
        except Exception as exc:
            if runner is not None:
                runner.checkpoint("stopped", errorType=type(exc).__name__)
            print(json.dumps({"phase": "stopped", "errorType": type(exc).__name__}))
            return 2
    print(json.dumps({"phase": "complete", "parentArtifactsUnchanged": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
