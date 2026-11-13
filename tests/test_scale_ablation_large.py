"""Exercise the approved phase, routing, retention and delayed-check boundaries."""

import asyncio
import base64
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from fli.query import SearchFilters, render_search
from scripts import scale_ablation_large as large
from scripts.scale_ablation import read, save


def make_runner(tmp_path, *, selected=None, started_at=None, status=None):
    manifest = tmp_path / "input-manifest.json"
    save(
        manifest,
        {
            "preparationStartedAt": started_at or large.utc_now(),
            "libraries": {},
            "hashes": {"assignmentSha256": "frozen-assignment"},
        },
    )
    state = tmp_path / "state"
    state.mkdir()
    if status is not None:
        save(state / "status.json", status)
    runner = large.Runner(
        {
            "prior": {},
            "inputManifest": str(manifest),
            "selected": selected or list(large.NEW_LIBRARIES[:2]),
            "e68Commit": "example-historical-commit",
        },
        state,
    )
    runner.vectors = {library: [[0.25, 0.5] for _ in range(20)] for library in large.SIZES}
    return runner


class QueryContainer:
    """A read/query surface; resource and data lifecycle methods are unavailable."""

    def __init__(self):
        self.calls = []

    def query_items(self, sql, **kwargs):
        self.calls.append((sql, kwargs))

        async def values():
            # Preserve a missing index header from an additional HTTP response.
            kwargs["raw_response_hook"](
                SimpleNamespace(http_response=SimpleNamespace(headers={"x-ms-request-charge": "1"}, status_code=200))
            )
            report = {"UtilizedVectorIndexes": [{"IndexSpec": {"VectorIndexType": "QuantizedFlat"}}]}
            headers = {
                "x-ms-request-charge": "9",
                "x-ms-documentdb-query-metrics": "retrievedDocumentCount=50;outputDocumentCount=10",
                "x-ms-cosmos-index-utilization": base64.b64encode(json.dumps(report).encode()).decode(),
            }
            kwargs["raw_response_hook"](
                SimpleNamespace(http_response=SimpleNamespace(headers=headers, status_code=200))
            )
            for ordinal in range(10):
                yield {"id": f"scale-p10-d0000:1:{ordinal}"}

        return values()


def test_where_only_changes_only_partition_option_and_keeps_missing_response_reports(tmp_path):
    runner = make_runner(tmp_path)
    container = QueryContainer()
    runner.clients = {arm: (None, None, container) for arm in large.ARMS}

    async def measure():
        routed = await runner.perform("routed", "split", "scale-p10", "routed", "vector", 0, 0)
        no_prefix = await runner.perform("no-prefix", "split", "scale-p10", "no-prefix", "vector", 0, 0)
        exact = await runner.perform("routed", "split", "scale-p10", "routed", "exact", 0, 0)
        return routed, no_prefix, exact

    routed, no_prefix, exact = asyncio.run(measure())
    (routed_sql, routed_options), (diagnostic_sql, diagnostic_options), (exact_sql, exact_options) = container.calls
    expected = render_search("scale-p10", runner.vectors["scale-p10"][0], [], SearchFilters(), 10)
    assert routed_sql == diagnostic_sql == expected.sql
    assert routed_options["parameters"] == diagnostic_options["parameters"] == expected.parameters
    assert routed_options["partition_key"] == exact_options["partition_key"] == ["scale-p10"]
    assert "partition_key" not in diagnostic_options
    assert "VectorDistance(c.embedding, @queryVector, true," in exact_sql
    assert " AS score" not in routed_sql
    assert all(row["request_charge"] == 10 for row in (routed, no_prefix, exact))
    assert no_prefix["response_metrics"][0]["indexUtilization"] is None
    assert no_prefix["response_metrics"][1]["indexUtilization"]["UtilizedVectorIndexes"]
    assert no_prefix["http_responses"] == 2


def test_global_exact_keeps_global_eligible_set_and_id_only_projection():
    sql, parameters, prefix = large.rendered_query("global", [0.25], "global", exact=True)
    assert sql.startswith("SELECT TOP 10 c.id FROM c ORDER BY VectorDistance(")
    assert "true," in sql and "WHERE" not in sql
    assert parameters == [{"name": "@queryVector", "value": [0.25]}]
    assert not prefix


def test_readiness_mode_ignores_impact_scores_but_distinguishes_actual_index_type():
    original = {
        "index_utilizations": [
            {
                "UtilizedVectorIndexes": [
                    {
                        "IndexSpec": {
                            "Path": "/embedding",
                            "VectorIndexType": "QuantizedFlat",
                            "ShardKeys": ["/libraryId"],
                        },
                        "IndexImpactScore": "High",
                        "IndexPreciseSet": True,
                    }
                ]
            }
        ]
    }
    changed_metadata = copy.deepcopy(original)
    changed_metadata["index_utilizations"][0]["UtilizedVectorIndexes"][0]["IndexImpactScore"] = "Low"
    assert large.mode_signature(original) == large.mode_signature(changed_metadata)
    changed_metadata["index_utilizations"][0]["UtilizedVectorIndexes"][0]["IndexSpec"]["VectorIndexType"] = "DiskANN"
    assert large.mode_signature(original) != large.mode_signature(changed_metadata)


@pytest.mark.parametrize("trigger_arm", [None, "split", "unsplit"])
def test_diagnostic_first_question_fixes_identical_coverage_before_later_questions(tmp_path, monkeypatch, trigger_arm):
    runner = make_runner(tmp_path)
    calls = []
    decisions_seen = []

    async def perform(phase, arm, library, shape, kind, query, run):
        calls.append((library, arm, query, run))
        if query:
            decision = read(runner.state / "diagnostic-plan.json")[library]
            decisions_seen.append(copy.deepcopy(decision))
            assert decision["questions"] == (5 if trigger_arm else 20)
            assert decision["queryOrdinals"] == list(range(5 if trigger_arm else 20))
        return {
            "arm": arm,
            "run": run,
            "retrieved_document_count": runner.inventory_size() if arm == trigger_arm and run == 1 else 50,
        }

    monkeypatch.setattr(runner, "perform", perform)
    monkeypatch.setattr(runner, "forecast_guard", lambda: None)
    asyncio.run(runner.run_prefix_diagnostic())
    plan = read(runner.state / "diagnostic-plan.json")
    for library in ("scale-p10", "large-p050000", "large-p100000"):
        library_calls = [call for call in calls if call[0] == library]
        assert [call[2] for call in library_calls[:6]] == [0] * 6
        assert {(call[1], call[3]) for call in library_calls[:6]} == {
            (arm, run) for arm in large.ARMS for run in range(3)
        }
        expected = set(range(5 if trigger_arm else 20))
        for arm in large.ARMS:
            assert {(call[2], call[3]) for call in library_calls if call[1] == arm} == {
                (query, run) for query in expected for run in range(3)
            }
        assert plan[library]["queryOrdinals"] == sorted(expected)
        assert len(plan[library]["observations"]) == 6
    assert decisions_seen


def test_completed_queries_are_not_replayed_and_failures_require_review(tmp_path):
    runner = make_runner(tmp_path)
    row = {
        "arm": "split",
        "library": "scale-p10",
        "shape": "routed",
        "kind": "vector",
        "query": 0,
        "run": 0,
        "error": None,
    }
    runner.phase_rows("routed").append(row)
    assert asyncio.run(runner.perform("routed", "split", "scale-p10", "routed", "vector", 0, 0)) == row
    row["error"] = "retained-failure"
    with pytest.raises(large.RunStopped, match="prior query failed"):
        asyncio.run(runner.perform("routed", "split", "scale-p10", "routed", "vector", 0, 0))


def test_changed_input_manifest_stops_before_connect_or_writes(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    monkeypatch.setattr(large, "SOURCE_FILES", ())
    runner.bind_sources()
    manifest = read(tmp_path / "input-manifest.json")
    manifest["hashes"]["assignmentSha256"] = "different-assignment"
    save(tmp_path / "input-manifest.json", manifest)

    async def forbidden():
        pytest.fail("changed inputs must stop before any connection or resource action")

    monkeypatch.setattr(runner, "connect", forbidden)
    monkeypatch.setattr(runner, "prepare_vectors", forbidden)
    with pytest.raises(large.RunStopped, match="frozen inputs"):
        asyncio.run(runner.run())


def test_late_time_exception_preserves_cost_threshold_and_ledger_on_restart(tmp_path):
    started = (datetime.now(UTC) - timedelta(hours=13)).isoformat()
    ledger = large.Ledger(tmp_path, started)
    ledger.add("measured", 7)
    with pytest.raises(large.RunStopped, match="twelve hours"):
        ledger.check()
    ledger.check(late=True)
    resumed = large.Ledger(tmp_path, started)
    assert resumed.data == ledger.data
    resumed.check(late=True)
    resumed.add("retained-late-charge", 64_000_000 - resumed.data["knownRu"])
    with pytest.raises(large.RunStopped, match="reserve"):
        resumed.check(late=True)
    assert resumed.data["categories"]["measured"] == 7


def test_main_queries_cannot_consume_the_delayed_reserve(tmp_path):
    runner = make_runner(tmp_path)
    container = QueryContainer()
    runner.clients = {arm: (None, None, container) for arm in large.ARMS}
    runner.ledger.add("earlier-required-work", 63_800_000)
    with pytest.raises(large.RunStopped, match="reserve"):
        asyncio.run(runner.perform("no-prefix", "split", "scale-p10", "no-prefix", "vector", 0, 0))
    assert not container.calls
    runner.is_late = True
    row = asyncio.run(runner.perform("late", "split", "scale-p10", "routed", "vector", 0, 0))
    assert row["request_charge"] == 10
    assert runner.ledger.data["knownRu"] < 64_000_000


def test_optional_admission_uses_forecast_and_persists_one_common_selection(tmp_path, monkeypatch):
    runner = make_runner(tmp_path, selected=list(large.NEW_LIBRARIES))
    considered = []

    def forecast():
        considered.append(list(runner.selected))
        return (64_000_000 if len(runner.selected) == 4 else 1_000_000), 60

    monkeypatch.setattr(runner, "forecast_remaining", forecast)
    runner.admit_optionals()
    expected = list(large.NEW_LIBRARIES[:3])
    assert considered == [list(large.NEW_LIBRARIES), expected]
    assert runner.selected == expected
    assert read(runner.state / "selection-decision.json")["selectedLibraries"] == expected
    assert read(runner.state / "status.json")["selectedLibraries"] == expected
    assert runner.inventory_size() == 404_773
    runner.selected = list(large.NEW_LIBRARIES)

    def forbidden_forecast():
        pytest.fail("a frozen optional decision must not be selected again after outcomes exist")

    monkeypatch.setattr(runner, "forecast_remaining", forbidden_forecast)
    runner.admit_optionals()
    assert runner.selected == expected


def test_optional_admission_cannot_relax_limits_when_required_work_does_not_fit(tmp_path, monkeypatch):
    runner = make_runner(tmp_path, selected=list(large.NEW_LIBRARIES))
    monkeypatch.setattr(runner, "forecast_remaining", lambda: (64_000_000, 60))
    with pytest.raises(large.RunStopped, match="mandatory workload"):
        runner.admit_optionals()
    assert runner.selected == list(large.NEW_LIBRARIES)
    assert not (runner.state / "selection-decision.json").exists()


def install_question_inputs(runner, tmp_path):
    prior_path = tmp_path / "prior" / "config.json"
    save(prior_path, runner.prior)
    save(
        prior_path.parent / "measurement-binding.json",
        {"configSha256": hashlib.sha256(json.dumps(runner.prior, sort_keys=True).encode()).hexdigest()},
    )
    runner.config["e68BindingFile"] = str(prior_path.parent / "measurement-binding.json")
    runner.manifest["priorConfigFile"] = str(prior_path)
    vectors = {library: [[0.125] * 3072 for _ in range(20)] for library in large.SIZES}
    sources = {
        "newQuestionSourcesFile": {library: ["Invented unit-test sentence."] * 20 for library in large.NEW_LIBRARIES},
        "oldQuestionSourcesFile": {
            library: ["Invented historical unit-test sentence."] * 20 for library in large.SIZES
        },
        "oldVectorsFile": {library: vectors[library] for library in ("scale-p01", "scale-p10")},
    }
    hashes = {
        "newQuestionSourcesFile": "questionSourcesSha256",
        "oldQuestionSourcesFile": "oldQuestionSourcesSha256",
        "oldVectorsFile": "oldVectorsSha256",
    }
    for key, value in sources.items():
        path = tmp_path / (key + ".json")
        save(path, value)
        runner.manifest[key] = str(path)
        runner.manifest["hashes"][hashes[key]] = large.sha(path)
    expected_model = {"model": "text-embedding-3-large", "version": "1", "format": "OpenAI", "dimensions": 3072}
    save(runner.state / "embedding-model-before.json", expected_model)
    save(runner.state / "embedding-model-after-questions.json", expected_model)
    save(runner.state / "question-vectors.json", vectors)
    return expected_model


@pytest.mark.parametrize("source_key", ["newQuestionSourcesFile", "oldQuestionSourcesFile", "oldVectorsFile"])
def test_changed_question_sources_or_historical_vectors_stop_before_embedding(tmp_path, monkeypatch, source_key):
    runner = make_runner(tmp_path)
    install_question_inputs(runner, tmp_path)
    path = large.private_path(runner.manifest[source_key])
    path.write_text(path.read_text() + "\n")

    async def forbidden(*_args):
        pytest.fail("a changed input must not cause an embedding call")

    monkeypatch.setattr(large.scale_probe, "embed_questions", forbidden)
    with pytest.raises(large.RunStopped, match="question sources or historical query vectors"):
        asyncio.run(runner.prepare_vectors())


@pytest.mark.parametrize("when", ["before", "after-questions"])
def test_unverified_or_changed_embedding_model_blocks_preparation(tmp_path, when):
    runner = make_runner(tmp_path)
    model = install_question_inputs(runner, tmp_path)
    save(runner.state / f"embedding-model-{when}.json", model | {"version": "another-revision"})
    with pytest.raises(large.RunStopped, match="model"):
        asyncio.run(runner.prepare_vectors())


def test_cached_frozen_question_vectors_are_shared_then_verified_on_resume(tmp_path):
    runner = make_runner(tmp_path)
    install_question_inputs(runner, tmp_path)
    asyncio.run(runner.prepare_vectors())
    assert set(runner.vectors) == set(large.SIZES)
    assert read(runner.state / "question-vector-hash.json")["sha256"] == large.sha(
        runner.state / "question-vectors.json"
    )
    changed = read(runner.state / "question-vectors.json")
    changed["large-p050000"][0][0] = 0.25
    save(runner.state / "question-vectors.json", changed)
    with pytest.raises(large.RunStopped, match="frozen query vectors"):
        asyncio.run(runner.prepare_vectors())


@pytest.mark.parametrize("fault", ["configuration", "ownership-binding"])
def test_historical_database_binding_is_checked_before_embedding(tmp_path, fault):
    runner = make_runner(tmp_path)
    install_question_inputs(runner, tmp_path)
    if fault == "configuration":
        save(large.private_path(runner.manifest["priorConfigFile"]), {"differentBinding": True})
    else:
        save(large.private_path(runner.config["e68BindingFile"]), {"configSha256": "different-binding"})
    with pytest.raises(large.RunStopped, match="binding"):
        asyncio.run(runner.prepare_vectors())


def test_existing_inventory_checks_use_read_queries_and_preserve_mismatch(tmp_path):
    runner = make_runner(tmp_path)
    expected_libraries = {
        library.library_id for stage in (1, 2, 3, 4) for library in large.scale_probe.stage_libraries(stage)
    }

    class Inventory:
        def __init__(self, arm):
            self.arm, self.calls = arm, []

        async def read(self):
            return large.chunks_definition(self.arm)

        async def read_feed_ranges(self, **_kwargs):
            yield {"min": "", "max": "FF"}

        def query_items(self, sql, **kwargs):
            self.calls.append(sql)

            async def values():
                kwargs["raw_response_hook"](
                    SimpleNamespace(
                        http_response=SimpleNamespace(headers={"x-ms-request-charge": "2"}, status_code=200)
                    )
                )
                if "DISTINCT" in sql:
                    for library in sorted(expected_libraries):
                        yield library
                elif "WHERE" not in sql:
                    yield 244_773
                else:
                    library = kwargs["parameters"][0]["value"]
                    yield 1 if library in runner.selected else large.SIZES[library]

            return values()

    split = Inventory("split")
    runner.clients = {"split": (None, None, split)}
    with pytest.raises(large.RunStopped, match="affected library inventory"):
        asyncio.run(runner.inventory("before", before=True))
    mismatch = read(runner.state / "inventory-before.json")["arms"]["split"]
    assert mismatch["observedCount"] == [1] and mismatch["expectedCount"] == 0
    assert mismatch["mismatchedLibrary"] in runner.selected
    assert split.calls and all(sql.startswith("SELECT ") for sql in split.calls)
    assert runner.ledger.data["categories"]["inventory"] > 0


def test_late_due_uses_ack_bound_and_requires_timezone():
    assert large.late_due("2026-11-12T18:03:19Z") == "2026-11-13T06:03:19Z"
    with pytest.raises(ValueError, match="timezone"):
        large.late_due("2026-11-12T18:03:19")


def install_phase_fakes(runner, monkeypatch):
    calls = []

    def sync(name):
        def action(*args, **kwargs):
            calls.append((name, args, kwargs))

        return action

    def asynchronous(name):
        async def action(*args, **kwargs):
            calls.append((name, args, kwargs))
            if name == "load":
                runner.status["lastWriteAckAt"] = large.utc_now()
            if name == "inventory":
                runner.ledger.check(late=runner.is_late)

        return action

    monkeypatch.setattr(runner, "bind_sources", sync("binding"))
    monkeypatch.setattr(runner, "forecast_guard", sync("forecast"))
    for attribute, name in (
        ("prepare_vectors", "vectors"),
        ("connect", "connect"),
        ("close", "close"),
        ("inventory", "inventory"),
        ("measure_routed", "routed"),
        ("load_library", "load"),
        ("settle", "readiness"),
        ("measure_global", "global"),
        ("sentinels", "sentinels"),
        ("run_prefix_diagnostic", "no-prefix"),
    ):
        monkeypatch.setattr(runner, attribute, asynchronous(name))
    return calls


def test_phase_order_keeps_primary_work_and_immediate_sentinels_before_diagnostic(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    calls = install_phase_fakes(runner, monkeypatch)
    monkeypatch.setattr(large, "late_due", lambda _ack: "2000-01-01T00:00:00Z")
    asyncio.run(runner.run())
    actual = [(name, args[0] if args else None) for name, args, _kwargs in calls if name != "forecast"]
    assert actual == [
        ("binding", None),
        ("vectors", None),
        ("connect", None),
        ("inventory", "before"),
        ("routed", "baseline"),
        ("load", "large-p050000"),
        ("load", "large-p100000"),
        ("inventory", "final"),
        ("readiness", None),
        ("routed", "routed"),
        ("global", None),
        ("sentinels", "immediate"),
        ("no-prefix", None),
        ("inventory", "late"),
        ("sentinels", "late"),
        ("close", None),
    ]
    assert runner.status["complete"] is True


def test_restart_after_main_complete_can_finish_late_set_after_initial_timer(tmp_path, monkeypatch):
    old = (datetime.now(UTC) - timedelta(hours=14)).isoformat()
    runner = make_runner(
        tmp_path,
        started_at=old,
        status={
            "loadsCompleted": True,
            "mainComplete": True,
            "lastWriteAckAt": old,
            "lastWriteUpperBoundAt": old,
            "lateDueAt": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    )
    calls = install_phase_fakes(runner, monkeypatch)
    asyncio.run(runner.run())
    assert [(name, args) for name, args, _ in calls if name in {"load", "routed", "no-prefix", "sentinels"}] == [
        ("sentinels", ("late",))
    ]
    assert runner.status["complete"] is True


def test_partial_batch_keeps_visible_charges_and_successful_acknowledgements(tmp_path):
    runner = make_runner(tmp_path)

    class Writes:
        async def upsert_item(self, body, *, response_hook):
            response_hook({"x-ms-request-charge": "7"}, {})
            if body["id"] == "failed":
                raise RuntimeError("retained-partial-failure")
            return body

    runner.clients = {"split": (None, None, Writes())}
    result = asyncio.run(runner.write_batch("split", [{"id": "succeeded"}, {"id": "failed"}]))
    assert result["ru"] == 14
    assert result["errors"] == 1
    assert result["lastWriteAckAt"]
    items = {row["ordinal"]: row for row in result["items"]}
    assert items[0]["acknowledgedAt"] == result["lastWriteAckAt"]
    assert items[1]["acknowledgedAt"] is None and items[1]["error"] == "RuntimeError"


def install_tiny_payload(runner, tmp_path, monkeypatch):
    library = large.NEW_LIBRARIES[0]
    payload = tmp_path / "frozen-library.jsonl"
    bodies = [
        {"id": f"{library}-d0000:1:{ordinal}", "libraryId": library, "chunkIndex": ordinal} for ordinal in range(2)
    ]
    payload.write_text("".join(json.dumps(body) + "\n" for body in bodies))
    runner.manifest["libraries"][library] = {"payloadFile": str(payload), "sha256": large.sha(payload)}
    monkeypatch.setitem(large.SIZES, library, 2)
    monkeypatch.setattr(runner, "forecast_guard", lambda: None)
    return library, bodies, payload


def test_loading_binds_same_frozen_payload_to_both_arms_and_retains_ack_bound(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    library, bodies, _ = install_tiny_payload(runner, tmp_path, monkeypatch)
    written = {}
    acknowledgements = {"split": "2026-11-12T18:00:01Z", "unsplit": "2026-11-12T18:00:09Z"}

    async def write_batch(arm, values):
        written[arm] = copy.deepcopy(values)
        return {
            "ru": 20,
            "seconds": 1,
            "throttled": 0,
            "errors": 0,
            "lastWriteAckAt": acknowledgements[arm],
        }

    monkeypatch.setattr(runner, "write_batch", write_batch)
    asyncio.run(runner.load_library(library))
    assert written == {arm: bodies for arm in large.ARMS}
    assert runner.status["lastWriteAckAt"] == acknowledgements["unsplit"]
    assert read(runner.state / "status.json")["lastWriteAckAt"] == acknowledgements["unsplit"]
    assert read(runner.state / f"load-{library}.json") == {
        arm: {"written": 2, "ru": 20, "seconds": 1, "throttled": 0} for arm in large.ARMS
    }
    assert runner.ledger.data["categories"]["load-split"] == 20
    assert runner.ledger.data["categories"]["load-unsplit"] == 20


def test_partial_library_stops_before_other_arm_and_retains_charge_ack_and_item_outcomes(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    library, _, _ = install_tiny_payload(runner, tmp_path, monkeypatch)
    calls = []
    items = [
        {"ordinal": 0, "acknowledgedAt": "2026-11-12T18:00:09Z", "error": None, "ru": 7},
        {"ordinal": 1, "acknowledgedAt": None, "error": "TimeoutError", "ru": 3},
    ]

    async def write_batch(arm, _values):
        calls.append(arm)
        return {
            "ru": 10,
            "seconds": 1,
            "throttled": 0,
            "errors": 1,
            "lastWriteAckAt": "2026-11-12T18:00:09Z",
            "items": items,
        }

    monkeypatch.setattr(runner, "write_batch", write_batch)
    with pytest.raises(large.RunStopped, match="partial write"):
        asyncio.run(runner.load_library(library))
    assert calls == ["split"]
    checkpoint = read(runner.state / "partial-write-private.json")
    assert checkpoint["items"] == items
    assert checkpoint["start"] == 0 and checkpoint["library"] == library
    assert runner.ledger.data["categories"]["load-split"] == 10
    assert read(runner.state / "status.json")["lastWriteAckAt"] == "2026-11-12T18:00:09Z"
    assert read(runner.state / f"load-{library}.json")["split"]["written"] == 0


def test_changed_payload_fails_before_loading_either_arm(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    library, _, payload = install_tiny_payload(runner, tmp_path, monkeypatch)
    payload.write_text(payload.read_text() + "{}\n")

    async def forbidden(*_args):
        pytest.fail("changed payload must not reach a write")

    monkeypatch.setattr(runner, "write_batch", forbidden)
    with pytest.raises(large.RunStopped, match="payload hash"):
        asyncio.run(runner.load_library(library))


def test_partial_write_checkpoint_blocks_automatic_resume(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    monkeypatch.setattr(runner, "bind_sources", lambda: None)
    save(runner.state / "partial-write-private.json", {"errors": 1})

    async def forbidden():
        pytest.fail("a partial batch requires reconciliation before more cloud work")

    monkeypatch.setattr(runner, "prepare_vectors", forbidden)
    with pytest.raises(large.RunStopped, match="reconciliation"):
        asyncio.run(runner.run())
