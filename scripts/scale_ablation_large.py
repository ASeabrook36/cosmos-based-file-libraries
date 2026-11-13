"""Run the approved large-library extension in the two retained E68 databases.

All bindings, payloads, questions, raw results and checkpoints are private. This
driver has no resource provisioning/deletion operation and changes no policy.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import importlib.metadata
import json
import math
import platform
import statistics
import sys
import time
import traceback
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fli.query import STORED_DTYPE, SearchFilters, render_search
from scripts import scale_probe
from scripts.canary import (
    K,
    RawTally,
    ResponseTally,
    SearchMeasurement,
    Throttle,
    definition_mismatches,
    exact_variant,
    run_query,
)
from scripts.scale_ablation import bind, private_path, read, save
from scripts.scale_ablation_provision import chunks_definition

ARMS = ("split", "unsplit")
NEW_LIBRARIES = ("large-p050000", "large-p100000", "large-p010000", "large-p025000")
SIZES = dict(zip(NEW_LIBRARIES, (50_000, 100_000, 10_000, 25_000), strict=True)) | {
    "scale-p01": 1000,
    "scale-p10": 5000,
}
REPO = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "scripts/scale_ablation_large.py",
    "scripts/scale_ablation_large_stats.py",
    "scripts/scale_ablation.py",
    "scripts/scale_ablation_stats.py",
    "scripts/canary.py",
    "scripts/scale_probe.py",
    "src/fli/query.py",
    "src/fli/embedding.py",
    "cosmos/chunks.container.json",
    "docs/scale-ablation-large-library-protocol.md",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return parsed.astimezone(UTC)


def late_due(last_write: str) -> str:
    return (parse_time(last_write) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def arm_order(run: int, question: int, library_ordinal: int = 0) -> tuple[str, str]:
    return ARMS if (run + question + library_ordinal) % 2 == 0 else ARMS[::-1]


def diagnostic_limit(first_question_rows: list[dict], inventory_vectors: int) -> int:
    """Any first-question repeat reading the whole container caps both arms."""
    if any(
        type(row.get("retrieved_document_count")) in (int, float)
        and row["retrieved_document_count"] >= inventory_vectors
        for row in first_question_rows
    ):
        return 5
    return 20


def rendered_query(library: str, vector: list, shape: str, exact: bool = False) -> tuple[str, list, bool]:
    if shape == "global":
        sql = (
            f"SELECT TOP {K} c.id FROM c ORDER BY "
            f"VectorDistance(c.embedding, @queryVector, false, {{'dataType': '{STORED_DTYPE}'}})"
        )
        parameters = [{"name": "@queryVector", "value": vector}]
    elif shape in {"routed", "no-prefix"}:
        rendered = render_search(library, vector, [], SearchFilters(), K)
        sql, parameters = rendered.sql, rendered.parameters
    else:
        raise ValueError("unknown query shape")
    return exact_variant(sql) if exact else sql, parameters, shape == "routed"


class RunStopped(RuntimeError):
    """Safe reason to report; details and raw exceptions remain private."""


class Ledger:
    def __init__(self, state: Path, started_at: str):
        self.path = state / "budget.json"
        self.data = read(self.path, {"knownRu": 5.8, "categories": {"protocol-preflight": 5.8}})
        self.started_at = started_at

    def add(self, category: str, charge: float) -> None:
        if not math.isfinite(charge) or charge < 0:
            raise RunStopped("invalid observed request charge")
        self.data["knownRu"] += charge
        categories = self.data["categories"]
        categories[category] = categories.get(category, 0.0) + charge
        save(self.path, self.data)

    def check(self, *, late: bool = False, additional_ru: float = 0, remaining_seconds: float = 0) -> None:
        if self.data["knownRu"] + additional_ru >= 64_000_000:
            raise RunStopped("known/request forecast reaches the $16 reserve threshold; user decision required")
        elapsed = (datetime.now(UTC) - parse_time(self.started_at)).total_seconds()
        if not late and elapsed + remaining_seconds >= 12 * 3600:
            raise RunStopped("initial elapsed/time forecast reaches twelve hours; user decision required")


class Runner:
    def __init__(self, config: dict, state: Path):
        self.config, self.state = config, private_path(state)
        self.prior = config["prior"]
        self.manifest = read(private_path(config["inputManifest"]))
        self.selected = list(config.get("selected", NEW_LIBRARIES))
        if self.selected[:2] != list(NEW_LIBRARIES[:2]) or any(lib not in NEW_LIBRARIES for lib in self.selected):
            raise ValueError("mandatory libraries and load order must be preserved")
        self.started_at = self.manifest["preparationStartedAt"]
        self.ledger = Ledger(self.state, self.started_at)
        self.status = read(self.state / "status.json", {"phase": "preparing", "startedAt": self.started_at})
        if "selectedLibraries" in self.status:
            self.selected = self.status["selectedLibraries"]
        self.clients: dict[str, Any] = {}
        self.rows: dict[str, list[dict]] = {}
        self.vectors: dict[str, list[list[float]]] = {}
        self.is_late = bool(self.status.get("mainComplete"))

    def checkpoint(self, phase: str, **values: Any) -> None:
        self.status.update({"phase": phase, "heartbeatAt": utc_now(), **values})
        save(self.state / "status.json", self.status)

    def phase_rows(self, phase: str) -> list[dict]:
        if phase not in self.rows:
            self.rows[phase] = read(self.state / f"measure-{phase}.json", {"rows": []})["rows"]
        return self.rows[phase]

    def save_rows(self, phase: str) -> None:
        save(self.state / f"measure-{phase}.json", {"rows": self.phase_rows(phase)})

    def find(self, phase: str, arm: str, library: str, shape: str, kind: str, q: int, run: int) -> dict | None:
        return next(
            (
                row
                for row in self.phase_rows(phase)
                if (row["arm"], row["library"], row["shape"], row["kind"], row["query"], row["run"])
                == (arm, library, shape, kind, q, run)
            ),
            None,
        )

    def libraries(self) -> list[str]:
        return sorted(["scale-p01", "scale-p10", *self.selected], key=lambda library: SIZES[library])

    def inventory_size(self) -> int:
        return 244_773 + sum(SIZES[library] for library in self.selected)

    async def connect(self) -> None:
        for arm in ARMS:
            bind(self.prior, arm)
            self.clients[arm] = await scale_probe._clients()

    async def close(self) -> None:
        for credential, client, _container in self.clients.values():
            await client.close()
            await credential.close()

    def bind_sources(self) -> None:
        source_hashes = {name: sha(REPO / name) for name in SOURCE_FILES}
        binding = {
            "configSha256": hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest(),
            "inputManifestSha256": sha(private_path(self.config["inputManifest"])),
            "sourceFiles": source_hashes,
            "e68ReceiptSha256": sha(REPO / "records/scale-ablation-20261111.json"),
            "e68Commit": self.config["e68Commit"],
            "client": {"python": platform.python_version(), "azureCosmos": importlib.metadata.version("azure-cosmos")},
        }
        existing = read(self.state / "binding.json")
        if existing is not None and existing != binding:
            raise RunStopped("configuration, frozen inputs or measurement sources changed")
        save(self.state / "binding.json", binding)

    async def prepare_vectors(self) -> None:
        prior_path = private_path(self.manifest["priorConfigFile"])
        if read(prior_path) != self.prior:
            raise RunStopped("database bindings differ from the retained E68 configuration")
        prior_binding = read(private_path(self.config["e68BindingFile"]), {})
        prior_hash = hashlib.sha256(json.dumps(self.prior, sort_keys=True).encode()).hexdigest()
        if prior_binding.get("configSha256") != prior_hash:
            raise RunStopped("retained E68 ownership/configuration binding does not match")
        checks = (
            ("newQuestionSourcesFile", "questionSourcesSha256"),
            ("oldQuestionSourcesFile", "oldQuestionSourcesSha256"),
            ("oldVectorsFile", "oldVectorsSha256"),
        )
        for source_key, hash_key in checks:
            if sha(private_path(self.manifest[source_key])) != self.manifest["hashes"][hash_key]:
                raise RunStopped("frozen question sources or historical query vectors changed")
        before = read(self.state / "embedding-model-before.json")
        expected = {"model": "text-embedding-3-large", "version": "1", "format": "OpenAI", "dimensions": 3072}
        if before != expected:
            raise RunStopped("expected embedding model/revision is not verified")
        question_state = self.state / "new-questions"
        question_state.mkdir(exist_ok=True, mode=0o700)
        source = private_path(self.manifest["newQuestionSourcesFile"])
        source_copy = question_state / "question-sources.json"
        if source_copy.exists() and sha(source_copy) != sha(source):
            raise RunStopped("cached new-question sources changed")
        source_copy.write_bytes(source.read_bytes())
        path = self.state / "question-vectors.json"
        if self.is_late and not path.exists():
            raise RunStopped("late check requires the original frozen question-vector cache")
        if not path.exists():
            from fli.embedding import measure_embeddings

            bind(self.prior, "split")
            with measure_embeddings() as usage:
                new_vectors = await scale_probe.embed_questions(argparse.Namespace(state=question_state))
            save(self.state / "embedding-usage.json", asdict(usage))
            old = read(private_path(self.manifest["oldVectorsFile"]))
            save(path, old | new_vectors)
        after_path = self.state / "embedding-model-after-questions.json"
        if not after_path.exists():
            script = private_path(self.config["modelSnapshotScript"])
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script),
                "after-questions",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            (self.state / "model-after-questions-private.log").write_bytes(stdout + stderr)
            if process.returncode:
                raise RunStopped("post-question embedding model readback failed")
        if read(after_path) != before:
            raise RunStopped("embedding model metadata changed during question preparation")
        self.vectors = read(path)
        if set(self.vectors) != set(SIZES) or any(
            len(vectors) != 20
            or any(len(vector) != 3072 or not all(math.isfinite(value) for value in vector) for vector in vectors)
            for vectors in self.vectors.values()
        ):
            raise RunStopped("frozen question coverage is incomplete")
        digest = sha(path)
        old_hash = read(self.state / "question-vector-hash.json")
        if old_hash not in (None, {"sha256": digest}):
            raise RunStopped("frozen query vectors changed")
        save(self.state / "question-vector-hash.json", {"sha256": digest})

    async def inventory(self, label: str, *, before: bool = False) -> None:
        expected_total = 244_773 if before else self.inventory_size()
        expected_libraries = {lib.library_id for stage in (1, 2, 3, 4) for lib in scale_probe.stage_libraries(stage)}
        if not before:
            expected_libraries.update(self.selected)
        result: dict[str, Any] = {"expectedVectors": expected_total, "checkedAt": utc_now(), "arms": {}}
        for arm in ARMS:
            self.ledger.check(late=self.is_late)
            container = self.clients[arm][2]
            stored = await container.read()
            problems = definition_mismatches(chunks_definition(arm), stored)
            if arm == "unsplit" and any(
                "vectorIndexShardKey" in index for index in stored.get("indexingPolicy", {}).get("vectorIndexes", [])
            ):
                problems.append("unexpected shard property")
            ranges = [value async for value in container.read_feed_ranges(force_refresh=True)]
            if problems or len(ranges) != 1:
                result["arms"][arm] = {
                    "policyMatches": not problems,
                    "physicalRanges": len(ranges),
                    "matchesExpected": False,
                }
                save(self.state / f"inventory-{label}.json", result)
                raise RunStopped("retained policy or physical range differs from the approved design")
            tally = RawTally()
            values = [v async for v in container.query_items("SELECT VALUE COUNT(1) FROM c", raw_response_hook=tally)]
            self.ledger.add("inventory", tally.request_charge)
            tally = RawTally()
            libraries = {
                v
                async for v in container.query_items(
                    "SELECT DISTINCT VALUE c.libraryId FROM c ORDER BY c.libraryId", raw_response_hook=tally
                )
            }
            self.ledger.add("inventory", tally.request_charge)
            counts = {}
            for library in self.libraries():
                tally = RawTally()
                count = [
                    v
                    async for v in container.query_items(
                        "SELECT VALUE COUNT(1) FROM c WHERE c.libraryId = @libraryId",
                        parameters=[{"name": "@libraryId", "value": library}],
                        partition_key=[library],
                        raw_response_hook=tally,
                    )
                ]
                self.ledger.add("inventory", tally.request_charge)
                expected = 0 if before and library in self.selected else SIZES[library]
                if count != [expected]:
                    result["arms"][arm] = {
                        "mismatchedLibrary": library,
                        "observedCount": count,
                        "expectedCount": expected,
                    }
                    save(self.state / f"inventory-{label}.json", result)
                    raise RunStopped("affected library inventory differs from its frozen assignment")
                counts[library] = count[0]
            valid = values == [expected_total] and libraries == expected_libraries
            result["arms"][arm] = {
                "vectors": values[0] if len(values) == 1 else None,
                "libraries": len(libraries),
                "physicalRanges": len(ranges),
                "policyMatches": not problems,
                "perLibrary": counts,
                "matchesExpected": valid,
            }
            save(self.state / f"inventory-{label}.json", result)
            if not valid:
                raise RunStopped("container inventory differs from the approved checkpoint")

    async def perform(self, phase: str, arm: str, library: str, shape: str, kind: str, q: int, run: int) -> dict:
        existing = self.find(phase, arm, library, shape, kind, q, run)
        if existing is not None:
            if existing.get("error"):
                raise RunStopped("a prior query failed; its observation is retained for review")
            return existing
        self.ledger.check(late=self.is_late, additional_ru=0 if self.is_late else 300_000)
        vector = self.vectors["scale-p01" if shape == "global" else library][q]
        sql, parameters, prefix = rendered_query(library, vector, shape, kind == "exact")
        measurement = SearchMeasurement(kind, q)
        started = time.perf_counter()
        try:
            await asyncio.wait_for(
                run_query(
                    self.clients[arm][2],
                    sql,
                    parameters,
                    library,
                    measurement,
                    Throttle(),
                    scored=False,
                    prefix_partition_key=prefix,
                ),
                timeout=min(180, self.remaining_main_seconds()) if not self.is_late else 180,
            )
        except Exception as exc:
            measurement.error = type(exc).__name__
            measurement.milliseconds = round((time.perf_counter() - started) * 1000, 1)
            measurement.known_attempt_charge = round(sum(a["knownRequestCharge"] for a in measurement.attempts), 2)
        row = asdict(measurement) | {
            "arm": arm,
            "library": library,
            "shape": shape,
            "run": run,
            "recordedAt": utc_now(),
            "response_metrics": measurement.attempts[-1]["rawResponses"] if measurement.attempts else [],
        }
        if "lastWriteAckAt" in self.status:
            row["secondsSinceLastWrite"] = (
                parse_time(row["recordedAt"]) - parse_time(self.status["lastWriteAckAt"])
            ).total_seconds()
        self.ledger.add(phase + "-" + kind, measurement.known_attempt_charge or measurement.request_charge)
        self.phase_rows(phase).append(row)
        self.save_rows(phase)
        self.checkpoint(phase, currentLibrary=library, currentShape=shape, currentRun=run, currentQuestion=q)
        if row["error"]:
            raise RunStopped("query error retained; user decision required")
        if len(set(row["result_ids"])) != K or row["retrieved_document_count"] is None:
            raise RunStopped("query result or retrieved-document diagnostics are incomplete")
        return row

    def remaining_main_seconds(self) -> float:
        elapsed = (datetime.now(UTC) - parse_time(self.started_at)).total_seconds()
        return max(0.001, 12 * 3600 - elapsed)

    async def measure_routed(self, phase: str, libraries: list[str]) -> None:
        for run in range(3):
            for ordinal, library in enumerate(libraries):
                for q in range(20):
                    for arm in arm_order(run, q, ordinal):
                        await self.perform(phase, arm, library, "routed", "vector", q, run)
                        if run == 0:
                            await self.perform(phase, arm, library, "routed", "exact", q, 0)
                    self.forecast_guard()

    async def measure_global(self) -> None:
        for run in range(3):
            for arm in arm_order(run, 0):
                await self.perform("global", arm, "global", "global", "vector", 0, run)
                if run == 0:
                    await self.perform("global", arm, "global", "global", "exact", 0, 0)
            self.forecast_guard()

    async def sentinels(self, phase: str) -> None:
        for run in range(3):
            for ordinal, library in enumerate(self.libraries()):
                for arm in arm_order(run, 0, ordinal):
                    await self.perform(phase, arm, library, "routed", "vector", 0, run)
            for arm in arm_order(run, 0):
                await self.perform(phase, arm, "global", "global", "vector", 0, run)

    async def run_prefix_diagnostic(self) -> None:
        decisions = read(self.state / "diagnostic-plan.json", {})
        for ordinal, library in enumerate(("scale-p10", "large-p050000", "large-p100000")):
            # All repetitions of the first frozen question precede any later question.
            first = []
            for run in range(3):
                for arm in arm_order(run, 0, ordinal):
                    first.append(await self.perform("no-prefix", arm, library, "no-prefix", "vector", 0, run))
                    if diagnostic_limit(first, self.inventory_size()) == 5:
                        decisions[library] = {
                            "questions": 5,
                            "queryOrdinals": list(range(5)),
                            "inventoryVectors": self.inventory_size(),
                            "trigger": "first question retrieved whole container",
                            "triggerObservations": [
                                {"arm": row["arm"], "run": row["run"], "retrieved": row["retrieved_document_count"]}
                                for row in first
                                if row["retrieved_document_count"] >= self.inventory_size()
                            ],
                        }
                        save(self.state / "diagnostic-plan.json", decisions)
            limit = diagnostic_limit(first, self.inventory_size())
            decision = decisions.get(library)
            if decision is not None and decision["questions"] != limit:
                raise RunStopped("frozen diagnostic coverage differs from the retained trigger")
            decisions[library] = {
                "questions": limit,
                "queryOrdinals": list(range(limit)),
                "inventoryVectors": self.inventory_size(),
                "trigger": "first question retrieved whole container"
                if limit == 5
                else "no first-question full-container read",
                "observations": [
                    {"arm": row["arm"], "run": row["run"], "retrieved": row["retrieved_document_count"]}
                    for row in first
                ],
                "triggerObservations": [
                    {"arm": row["arm"], "run": row["run"], "retrieved": row["retrieved_document_count"]}
                    for row in first
                    if row["retrieved_document_count"] >= self.inventory_size()
                ],
            }
            save(self.state / "diagnostic-plan.json", decisions)
            self.forecast_guard()
            for run in range(3):
                for q in range(1, limit):
                    for arm in arm_order(run, q, ordinal):
                        await self.perform("no-prefix", arm, library, "no-prefix", "vector", q, run)
                    self.forecast_guard()

    async def load_library(self, library: str) -> None:
        entry = self.manifest["libraries"][library]
        path = private_path(entry["payloadFile"])
        if sha(path) != entry["sha256"]:
            raise RunStopped("private payload hash differs from the frozen manifest")
        progress_path = self.state / f"load-{library}.json"
        progress = read(progress_path, {arm: {"written": 0, "ru": 0.0, "seconds": 0.0, "throttled": 0} for arm in ARMS})
        with path.open() as handle:
            batch = []
            for index, line in enumerate(handle):
                batch.append(json.loads(line))
                if len(batch) == 200 or index + 1 == SIZES[library]:
                    end = index + 1
                    for arm in ARMS:
                        completed = progress[arm]["written"]
                        if completed >= end:
                            continue
                        start = end - len(batch)
                        if completed < start:
                            raise RunStopped("load checkpoint contains a gap")
                        self.forecast_guard()
                        pending = batch[max(0, completed - start) :]
                        result = await self.write_batch(arm, pending)
                        self.ledger.add("load-" + arm, result["ru"])
                        progress[arm]["ru"] += result["ru"]
                        progress[arm]["seconds"] += result["seconds"]
                        progress[arm]["throttled"] += result["throttled"]
                        if result["lastWriteAckAt"]:
                            self.status["lastWriteAckAt"] = max(
                                self.status.get("lastWriteAckAt", result["lastWriteAckAt"]),
                                result["lastWriteAckAt"],
                                key=parse_time,
                            )
                        if result["errors"]:
                            save(
                                self.state / "partial-write-private.json",
                                {"library": library, "arm": arm, "start": start, **result},
                            )
                            save(progress_path, progress)
                            self.checkpoint("stopped-partial-write")
                            raise RunStopped("partial write batch retained; reconciliation required before resume")
                        progress[arm]["written"] = end
                        save(progress_path, progress)
                        self.checkpoint("loading", currentLibrary=library, loadProgress=progress)
                    batch.clear()
        if any(progress[arm]["written"] != SIZES[library] for arm in ARMS):
            raise RunStopped("payload ended before the declared library size")

    async def write_batch(self, arm: str, bodies: list[dict]) -> dict:
        started = time.perf_counter()
        gate = asyncio.Semaphore(2)
        results = []

        async def one(ordinal: int, body: dict) -> None:
            tally, throttle = ResponseTally(), Throttle()
            error, acknowledged = None, None
            try:
                async with gate:
                    await asyncio.wait_for(
                        throttle.run(lambda: self.clients[arm][2].upsert_item(body, response_hook=tally)),
                        timeout=min(180, self.remaining_main_seconds()),
                    )
                    acknowledged = utc_now()
            except Exception as exc:
                error = type(exc).__name__
            results.append(
                {
                    "ordinal": ordinal,
                    "ru": tally.request_charge,
                    "acknowledgedAt": acknowledged,
                    "error": error,
                    "throttled": throttle.count,
                }
            )

        await asyncio.gather(*(one(ordinal, body) for ordinal, body in enumerate(bodies)))
        acknowledgements = [row["acknowledgedAt"] for row in results if row["acknowledgedAt"]]
        return {
            "ru": sum(row["ru"] for row in results),
            "seconds": time.perf_counter() - started,
            "throttled": sum(row["throttled"] for row in results),
            "lastWriteAckAt": max(acknowledgements, key=parse_time) if acknowledgements else None,
            "errors": sum(bool(row["error"]) for row in results),
            "items": results,
        }

    async def settle(self) -> None:
        last = parse_time(self.status["lastWriteAckAt"])
        while (datetime.now(UTC) - last).total_seconds() < 1800:
            self.ledger.check()
            self.checkpoint("quiet-period")
            await asyncio.sleep(min(50, 1800 - (datetime.now(UTC) - last).total_seconds()))
        polls = read(self.state / "readiness.json", {"polls": []})
        stable_count, previous = 0, None
        while (datetime.now(UTC) - last).total_seconds() <= 3600:
            rows = []
            number = len(polls["polls"])
            for ordinal, library in enumerate(self.libraries()):
                for arm in arm_order(number, 0, ordinal):
                    row = await self.perform("readiness", arm, library, "routed", "vector", 0, number)
                    rows.append(row)
            current = {(row["arm"], row["library"]): row for row in rows}
            valid = all(row["index_utilizations"] for row in rows)
            stable = previous is not None and all(
                abs(row["request_charge"] - previous[key]["request_charge"])
                <= max(0.1, previous[key]["request_charge"] * 0.1)
                and mode_signature(row) == mode_signature(previous[key])
                for key, row in current.items()
            )
            stable_count = stable_count + 1 if valid and stable else 1 if valid else 0
            polls["polls"].append(
                {
                    "elapsedSinceLastWriteSeconds": (datetime.now(UTC) - last).total_seconds(),
                    "stableCount": stable_count,
                }
            )
            save(self.state / "readiness.json", polls)
            previous = current
            if stable_count >= 3:
                break
            await asyncio.sleep(60)
        else:
            raise RunStopped("bounded readiness did not stabilize; user decision required")
        for _ in range(6):
            self.ledger.check()
            self.checkpoint("settling")
            await asyncio.sleep(50)
        save(self.state / "readiness.json", polls | {"complete": True, "settledSeconds": 300})

    def forecast_guard(self) -> None:
        """Project remaining measured work; observed per-case means replace extrapolations."""
        self.ledger.check(late=self.is_late)
        if self.is_late:
            return
        remaining_ru, remaining_seconds = self.forecast_remaining()
        save(
            self.state / "forecast.json",
            {"at": utc_now(), "additionalKnownRu": remaining_ru, "additionalSeconds": remaining_seconds},
        )
        self.ledger.check(additional_ru=remaining_ru, remaining_seconds=remaining_seconds)

    def admit_optionals(self) -> None:
        """Choose optional coverage from cost/time only, before optional writes."""
        path = self.state / "selection-decision.json"
        existing = read(path)
        if existing is not None:
            self.selected = existing["selectedLibraries"]
            return
        original = list(self.selected)
        candidates = [original]
        if any(library in original for library in NEW_LIBRARIES[2:]):
            candidates += [list(NEW_LIBRARIES[:2]) + [NEW_LIBRARIES[2]], list(NEW_LIBRARIES[:2])]
        decision = None
        for selected in candidates:
            self.selected = selected
            ru, seconds = self.forecast_remaining()
            try:
                self.ledger.check(additional_ru=ru, remaining_seconds=seconds)
            except RunStopped:
                continue
            decision = {
                "selectedLibraries": selected,
                "additionalKnownRu": ru,
                "additionalSeconds": seconds,
                "decidedAt": utc_now(),
                "basis": "observed load charges/rate and frozen remaining workload; no large-library query outcomes",
            }
            break
        if decision is None:
            self.selected = original
            raise RunStopped("mandatory workload forecast exceeds a limit; user decision required")
        save(path, decision)
        self.checkpoint("optional-admission", selectedLibraries=self.selected)

    def forecast_remaining(self) -> tuple[float, float]:
        pending_ru, pending_seconds = 0.0, 0.0
        total_written = total_ru = total_seconds = 0.0
        remaining_writes = 0
        for library in self.selected:
            progress = read(self.state / f"load-{library}.json", {})
            for arm in ARMS:
                p = progress.get(arm, {})
                written = p.get("written", 0)
                remaining_writes += SIZES[library] - written
                total_written += written
                total_ru += p.get("ru", 0)
                total_seconds += p.get("seconds", 0)
        if remaining_writes:
            pending_ru += remaining_writes * (max(91.878, total_ru / total_written) if total_written else 91.878)
            pending_seconds += remaining_writes * (
                max(0.0409, total_seconds / total_written) if total_written else 0.0409
            )
            pending_seconds += 3900  # bounded quiet/readiness/settling plus inventory allowance
        elif not read(self.state / "readiness.json", {}).get("complete"):
            last = self.status.get("lastWriteAckAt")
            since_write = (datetime.now(UTC) - parse_time(last)).total_seconds() if last else 0
            pending_seconds += max(0, 3900 - since_write)
        groups: dict[tuple, list[dict]] = {}
        for phase in ("baseline", "routed", "global", "immediate", "no-prefix"):
            for row in self.phase_rows(phase):
                groups.setdefault((row["arm"], row["library"], row["shape"], row["kind"]), []).append(row)
        decisions = read(self.state / "diagnostic-plan.json", {})
        plans = [
            ("baseline", ["scale-p01", "scale-p10"], "routed", 20, True),
            ("routed", self.libraries(), "routed", 20, True),
            ("global", ["global"], "global", 1, True),
            ("immediate", self.libraries(), "routed", 1, False),
            ("immediate", ["global"], "global", 1, False),
        ]
        plans += [
            ("no-prefix", [library], "no-prefix", decisions.get(library, {}).get("questions", 20), False)
            for library in ("scale-p10", "large-p050000", "large-p100000")
        ]
        for phase, libraries, shape, questions, exact in plans:
            for library in libraries:
                for arm in ARMS:
                    for kind, runs in (("vector", 3), ("exact", 1 if exact else 0)):
                        n = sum(
                            self.find(phase, arm, library, shape, kind, q, run) is None
                            for run in range(runs)
                            for q in range(questions)
                        )
                        size = self.inventory_size() if shape == "global" else SIZES[library]
                        observed = groups.get((arm, library, shape, kind), [])
                        if observed:
                            ru = statistics.fmean(
                                row["known_attempt_charge"] or row["request_charge"] for row in observed
                            )
                            seconds = statistics.fmean(row["milliseconds"] for row in observed) / 1000
                        elif shape == "global":
                            ru, seconds = size * 11779.51 / 244773, size * 31.9976 / 244773
                        else:
                            ru, seconds = 3.175 + 0.067335 * size, 0.08585 + 0.0000987 * size
                        pending_ru += n * ru
                        pending_seconds += n * seconds
        # Delayed sentinels are charged, but their wait and execution are outside the main timer.
        pending_ru += 300_000
        return pending_ru, pending_seconds

    async def run(self) -> None:
        self.bind_sources()
        if (self.state / "partial-write-private.json").exists():
            raise RunStopped("partial batch requires explicit reconciliation before resume")
        await self.prepare_vectors()
        await self.connect()
        try:
            if not self.status.get("loadsCompleted"):
                if not any((self.state / f"load-{library}.json").exists() for library in self.selected):
                    await self.inventory("before", before=True)
                await self.measure_routed("baseline", ["scale-p01", "scale-p10"])
                for library in list(self.selected):
                    if library in NEW_LIBRARIES[2:]:
                        self.admit_optionals()
                        if library not in self.selected:
                            continue
                    self.forecast_guard()
                    await self.load_library(library)
                self.checkpoint("loads-complete", loadsCompleted=True, lastWriteUpperBoundAt=utc_now())
            if not self.status.get("mainComplete"):
                await self.inventory("final")
            if not self.status.get("mainComplete"):
                if not read(self.state / "readiness.json", {}).get("complete"):
                    await self.settle()
                await self.measure_routed("routed", self.libraries())
                await self.measure_global()
                await self.sentinels("immediate")
                await self.run_prefix_diagnostic()
                due = late_due(self.status["lastWriteUpperBoundAt"])
                self.checkpoint("main-complete", mainComplete=True, mainCompletedAt=utc_now(), lateDueAt=due)
            self.is_late = True
            due = parse_time(self.status["lateDueAt"])
            while datetime.now(UTC) < due:
                self.checkpoint("waiting-late-sentinels")
                await asyncio.sleep(min(50, (due - datetime.now(UTC)).total_seconds()))
            await self.inventory("late")
            await self.sentinels("late")
            self.checkpoint("complete", complete=True, lateCompletedAt=utc_now())
        finally:
            await self.close()


def mode_signature(row: dict) -> str:
    specs, no_utilized = set(), False
    for report in row.get("index_utilizations", []):
        entries = report.get("UtilizedVectorIndexes", [])
        no_utilized |= not entries
        for entry in entries:
            spec = entry.get("IndexSpec", {})
            specs.add((spec.get("VectorIndexType"), spec.get("Path"), tuple(spec.get("ShardKeys", []))))
    return json.dumps({"specs": sorted(specs, key=repr), "noUtilizedVectorIndex": no_utilized}, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    state = private_path(args.state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another large-library driver holds the private run lock")
            return 2
        runner = Runner(read(private_path(args.config)), state)
        try:
            asyncio.run(runner.run())
        except Exception as exc:
            save(state / "error-private.json", {"type": type(exc).__name__, "traceback": traceback.format_exc()})
            reason = str(exc) if isinstance(exc, RunStopped) else type(exc).__name__
            runner.checkpoint("stopped", reason=reason)
            print(json.dumps({"phase": "stopped", "reason": reason}))
            return 2
    print(json.dumps({"phase": "complete", "databasesRetained": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
