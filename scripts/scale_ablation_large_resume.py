"""Resume the frozen large-library driver with independently verified exact references.

Raw observations, SQL, routing and the original source binding stay unchanged.
Only an exact reference may have a derived, independently verified top ten.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import importlib.metadata
import json
import math
import shutil
import traceback
from pathlib import Path

from scripts import scale_ablation_large as original
from scripts.scale_ablation import private_path, read, save
from scripts.scale_ablation_large_exact import derive_reference, validate_oracle

REPAIR_SOURCES = (
    "scripts/scale_ablation_large_resume.py",
    "scripts/scale_ablation_large_exact.py",
    "docs/scale-ablation-large-reference-repair.md",
)
PHASES = ("baseline", "readiness", "routed", "global", "immediate", "no-prefix", "late")
CARDINALITY_STOP = "query result or retrieved-document diagnostics are incomplete"
INITIAL_FAILURE_SHA256 = "63e939e5cf6dfe1b16696530410509222c3c954e18359291a9707300a8b7b4e9"
ORIGINAL_BINDING_SHA256 = "90c44807502e81e549350c015219967a9ca8f465f38d98e2065a1204b6a618e8"


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_observation(row: dict, *, exact: bool = False) -> None:
    """Recheck cached rows too; an error-free flag cannot bypass result validation."""
    if row.get("error"):
        raise original.RunStopped("a query error remains retained; reference repair cannot override it")
    ids = row.get("result_ids")
    if (
        not isinstance(ids, list)
        or any(not isinstance(value, str) or not value for value in ids)
        or len(ids) != len(set(ids))
        or (len(ids) < original.K if exact else len(ids) != original.K)
    ):
        raise original.RunStopped("cached or new query has invalid candidate cardinality or uniqueness")
    for key in ("request_charge", "retrieved_document_count", "milliseconds"):
        value = row.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise original.RunStopped("cached or new query has incomplete numerical diagnostics")
    if row.get("shape") != "global":
        library = row["library"]
        if any(not value.startswith(library + "-d") for value in ids):
            raise original.RunStopped("query candidates do not belong to the named library")
        bindings = row.get("result_bindings", [])
        if bindings and (
            len(bindings) != len(ids)
            or [binding.get("id") for binding in bindings] != ids
            or any(binding.get("libraryId", library) != library for binding in bindings)
        ):
            raise original.RunStopped("query candidate bindings disagree with their eligible library")


class Runner(original.Runner):
    def __init__(self, config: dict, state: Path):
        super().__init__(config, state)
        self.oracle = read(self.state / "offline-exact.json")
        validate_oracle(config, self.state, self.oracle)

    def bind_sources(self) -> None:
        super().bind_sources()
        stopped = [
            row
            for row in self.phase_rows("routed")
            if (row.get("arm"), row.get("library"), row.get("kind"), row.get("query"), row.get("run"))
            == ("unsplit", "large-p025000", "exact", 0, 0)
        ]
        if len(stopped) != 1 or len(stopped[0].get("result_ids", [])) != 20:
            raise original.RunStopped("the retained reference-pagination failure cannot be verified")
        if digest(stopped[0]) != INITIAL_FAILURE_SHA256:
            raise original.RunStopped("the original pagination failure observation changed")
        if original.sha(self.state / "binding.json") != ORIGINAL_BINDING_SHA256:
            raise original.RunStopped("the original measurement binding changed")
        binding = {
            "version": 1,
            "repair": "unchanged-cosmos-query-independent-exhaustive-candidate-validation",
            "originalBindingSha256": original.sha(self.state / "binding.json"),
            "offlineOracleSha256": original.sha(self.state / "offline-exact.json"),
            "numericalClient": {"numpy": importlib.metadata.version("numpy")},
            "sourceFiles": {name: original.sha(original.REPO / name) for name in REPAIR_SOURCES},
            "initialFailure": {
                "phase": "routed",
                "rowSha256": digest(stopped[0]),
                "arm": "unsplit",
                "librarySize": 25_000,
                "questionOrdinal": 0,
                "rawResultCount": 20,
                "requestCharge": stopped[0]["request_charge"],
                "retrievedDocuments": stopped[0]["retrieved_document_count"],
                "milliseconds": stopped[0]["milliseconds"],
            },
        }
        path = self.state / "reference-repair-binding.json"
        existing = read(path)
        if existing is not None and existing != binding:
            raise original.RunStopped("reference-repair sources or exhaustive inputs changed")
        for name, expected in binding["sourceFiles"].items():
            destination = self.state / "repair-source" / name
            if destination.exists():
                if original.sha(destination) != expected:
                    raise original.RunStopped("archived reference-repair source changed")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(original.REPO / name, destination)
                destination.chmod(0o600)
        save(path, binding)

    def checked(self, phase: str, row: dict) -> dict:
        exact = row.get("kind") == "exact"
        validate_observation(row, exact=exact)
        if not exact:
            return row
        try:
            derived = derive_reference(row, self.oracle)
        except (ValueError, KeyError, TypeError) as exc:
            raise original.RunStopped("exhaustive reference validation failed; raw observation retained") from exc
        permitted = {"result_ids", "result_bindings", "referenceDerivation"}
        if {key: value for key, value in row.items() if key not in permitted} != {
            key: value for key, value in derived.items() if key not in permitted
        }:
            raise original.RunStopped("reference derivation changed a raw metric or measurement context")
        validate_observation(derived)
        path = self.state / "exact-reference-derivations.json"
        data = read(path, {"rows": []})
        key = [phase, *[row[name] for name in ("arm", "library", "shape", "kind", "query", "run")]]
        entry = {"key": key, "rawRowSha256": digest(row), "derived": derived}
        previous = next((value for value in data["rows"] if value["key"] == key), None)
        if previous is not None and previous != entry:
            raise original.RunStopped("a previously derived exact reference changed")
        if previous is None:
            data["rows"].append(entry)
            save(path, data)
        return derived

    async def perform(self, phase, arm, library, shape, kind, q, run):
        try:
            row = await super().perform(phase, arm, library, shape, kind, q, run)
        except original.RunStopped as exc:
            if kind != "exact" or str(exc) != CARDINALITY_STOP:
                raise
            row = self.find(phase, arm, library, shape, kind, q, run)
            if row is None:
                raise
        return self.checked(phase, row)

    async def run(self) -> None:
        self.bind_sources()
        for phase in PHASES:
            for row in self.phase_rows(phase):
                self.checked(phase, row)
        await super().run()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    state = private_path(args.state)
    with (state / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another large-library driver holds the private run lock")
            return 2
        runner = None
        try:
            runner = Runner(read(private_path(args.config)), state)
            asyncio.run(runner.run())
        except Exception as exc:
            save(state / "repair-error-private.json", {"type": type(exc).__name__, "traceback": traceback.format_exc()})
            reason = str(exc) if isinstance(exc, original.RunStopped) else type(exc).__name__
            if runner is not None:
                runner.checkpoint("stopped", reason=reason)
            print(json.dumps({"phase": "stopped", "reason": reason}))
            return 2
    print(json.dumps({"phase": "complete", "databasesRetained": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
