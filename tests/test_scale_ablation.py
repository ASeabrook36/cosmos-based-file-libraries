"""Guard the paired experiment's request parity, ordering and private state."""

import argparse
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from fli.query import SearchFilters, render_search
from scripts import scale_ablation as ablation
from scripts import scale_probe


class Container:
    def __init__(self, shards=None, *, charge=10, retrieved=50, report=True):
        self.shards = ["/libraryId"] if shards is None else shards
        self.charge = charge
        self.retrieved = retrieved
        self.report = report
        self.calls = []

    async def read_feed_ranges(self, **kwargs):
        yield {"min": "", "max": "FF"}

    def query_items(self, sql, **kwargs):
        self.calls.append((sql, kwargs))

        async def response():
            headers = {
                "x-ms-request-charge": str(self.charge),
                "x-ms-documentdb-query-metrics": f"retrievedDocumentCount={self.retrieved};outputDocumentCount=10",
                "x-ms-documentdb-partitionkeyrangeid": "0",
            }
            if self.report:
                headers["x-ms-cosmos-index-utilization"] = base64.b64encode(
                    json.dumps(
                        {
                            "UtilizedVectorIndexes": [
                                {"IndexSpec": {"VectorIndexType": "QuantizedFlat", "ShardKeys": self.shards}}
                            ]
                        }
                    ).encode()
                ).decode()
            kwargs["raw_response_hook"](
                SimpleNamespace(http_response=SimpleNamespace(headers=headers, status_code=200))
            )
            for i in range(10):
                yield {"id": f"row-{i}", "score": 0.01 * i}

        return response()


def test_filtered_request_is_application_sql_and_prefix_routed(tmp_path):
    container = Container()
    vector = [0.5, 0.25]
    row = asyncio.run(ablation.query(container, "scale-p01", vector, "vector", 0, 1, ablation.Budget(tmp_path, 100)))
    [(sql, kwargs)] = container.calls
    expected = render_search("scale-p01", vector, [], SearchFilters(), 10)
    assert sql == expected.sql
    assert " AS score" not in sql
    assert kwargs["parameters"] == expected.parameters
    assert kwargs["partition_key"] == ["scale-p01"]
    assert row["run"] == 1 and row["request_charge"] == 10


def test_exact_reference_retains_the_same_filter_projection_and_prefix(tmp_path):
    container = Container()
    asyncio.run(ablation.query(container, "scale-p02", [0.5], "exact", 1, 0, ablation.Budget(tmp_path, 100)))
    [(sql, kwargs)] = container.calls
    assert "VectorDistance(c.embedding, @queryVector, true," in sql
    assert "WHERE c.libraryId = @libraryId" in sql
    assert " AS score" not in sql
    assert kwargs["partition_key"] == ["scale-p02"]


def test_global_control_deliberately_has_neither_library_filter_nor_prefix(tmp_path):
    container = Container(report=False, retrieved=25000)
    row = asyncio.run(ablation.control(container, [0.5], 2, ablation.Budget(tmp_path, 100)))
    [(sql, kwargs)] = container.calls
    assert sql.startswith("SELECT TOP 10 c.id FROM c ORDER BY VectorDistance(")
    assert "WHERE" not in sql and "partition_key" not in kwargs
    assert [p["name"] for p in kwargs["parameters"]] == ["@queryVector"]
    assert row["error"] is None and row["run"] == 2
    assert row["retrieved_document_count"] == 25000
    assert row["index_utilization"] is None


def test_budget_checkpoint_is_preserved_across_restart_and_limit_pause(tmp_path):
    first = ablation.Budget(tmp_path, 20)
    first.add("vector", 10)
    resumed = ablation.Budget(tmp_path, 20)
    assert resumed.data["knownRu"] == 10
    resumed.add("exact", 11)
    with pytest.raises(RuntimeError, match="budget"):
        resumed.check()
    assert ablation.read(tmp_path / "budget.json")["knownRu"] == 21


def test_extra_library_corpus_slice_follows_nominal_three_stages():
    assert scale_probe.stage_line_range(4).start == 243273
    assert len(scale_probe.stage_line_range(4)) == 1500
    assert {entry[0] for entry in scale_probe.assignment(4)} == {"scale-new"}


def test_private_state_paths_reject_repository_and_git_metadata(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        ablation.private_path(ablation.REPO / "records" / "state")
    with pytest.raises(ValueError, match="outside"):
        ablation.private_path(tmp_path / ".git" / "state")
    assert ablation.private_path(tmp_path) == tmp_path.resolve()
    checkout = tmp_path / "other-checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: unused")
    with pytest.raises(ValueError, match="outside"):
        ablation.private_path(checkout / "raw-state")


def test_ready_accepts_a_stable_expensive_unsplit_index_without_a_cheapness_gate(tmp_path, monkeypatch):
    config = {
        "cosmosEndpoint": "unused",
        "azureConfigDir": str(tmp_path),
        "arms": {arm: {"database": arm} for arm in ablation.ARMS},
    }
    containers = {
        "split": Container(charge=9000, retrieved=5000),
        "unsplit": Container([], charge=9000, retrieved=5000),
    }
    selected = {"arm": None}
    settled = []

    def bind(_config, arm):
        selected["arm"] = arm

    async def close():
        pass

    async def clients():
        return SimpleNamespace(close=close), SimpleNamespace(close=close), containers[selected["arm"]]

    async def sleep(_seconds):
        if _seconds == 17:
            checkpoint = ablation.read(tmp_path / selected["arm"] / "ready-1.json")
            assert not checkpoint["complete"]
            assert checkpoint["indexReady"]
            settled.append(selected["arm"])

    monkeypatch.setattr(ablation, "bind", bind)
    monkeypatch.setattr(scale_probe, "_clients", clients)
    monkeypatch.setattr(ablation.asyncio, "sleep", sleep)
    asyncio.run(
        ablation.ready(config, tmp_path, 1, {"scale-p01": [[0.5]]}, ablation.Budget(tmp_path, 1_000_000), 1, 17)
    )
    assert all(ablation.read(tmp_path / arm / "ready-1.json")["complete"] for arm in ablation.ARMS)
    assert all(len(container.calls) == 2 for container in containers.values())
    assert settled == ["split", "unsplit"]


def test_public_report_does_not_copy_unapproved_private_metadata(tmp_path):
    ablation.save(
        tmp_path / "shared" / "input-hashes.json",
        {"questionSourcesSha256": "a" * 64, "endpoint": "must-omit-input", "unexpected": {"key": "must-omit-nested"}},
    )
    ablation.save(
        tmp_path / "split" / "load-1.json",
        {"written": 1, "ru": 10, "seconds": 1, "privateMetadata": "must-omit-load"},
    )
    ablation.save(
        tmp_path / "budget.json",
        {"knownRu": 10, "categories": {"load-split": 10}, "privateMetadata": "must-omit-budget"},
    )
    ablation.save(
        tmp_path / "split" / "inventory-1.json",
        {"vectors": 25000, "libraries": 10, "matchesExpected": True, "labels": ["must-omit-label"]},
    )
    output = tmp_path / "sanitized.json"
    ablation.report(tmp_path, output)
    content = output.read_text()
    assert "must-omit" not in content
    data = json.loads(content)
    assert data["inputs"]["questionSourcesSha256"] == "a" * 64
    assert data["knownRequestCost"]["knownRu"] == 10
    assert data["stages"]["1"]["split"]["load"]["written"] == 1
    assert data["stages"]["1"]["split"]["observedInventory"] == {
        "vectors": 25000,
        "libraries": 10,
        "matchesExpected": True,
    }


def test_loading_resumes_each_arm_from_its_own_checkpoint(tmp_path, monkeypatch):
    written = {arm: [] for arm in ablation.ARMS}
    selected = {"arm": None}

    def bind(_config, arm):
        selected["arm"] = arm

    async def close():
        pass

    async def clients():
        return SimpleNamespace(close=close), SimpleNamespace(close=close), selected["arm"]

    async def write_all(container, bodies, _throttle, _progress):
        written[container].extend(body["id"] for body in bodies)
        return len(bodies) * 10

    def lines(_path, wanted, _text_field, _vector_field):
        assert wanted == range(4)
        yield from ((i, "private text", [0.5]) for i in range(4))

    monkeypatch.setattr(ablation, "bind", bind)
    monkeypatch.setattr(scale_probe, "_clients", clients)
    monkeypatch.setattr(scale_probe, "BATCH", 2)
    monkeypatch.setattr(scale_probe, "stage_line_range", lambda _stage: range(4))
    monkeypatch.setattr(scale_probe, "assignment", lambda _stage: [("probe", f"doc-{i}", 0) for i in range(4)])
    monkeypatch.setattr(scale_probe, "iter_lines", lines)
    monkeypatch.setattr(ablation, "write_all", write_all)
    ablation.save(tmp_path / "split" / "load-1.json", {"written": 2, "ru": 20, "seconds": 1})
    config = {"corpus": "unused", "textField": "text", "vectorField": "embedding"}
    asyncio.run(ablation.load_stage(config, tmp_path, 1, ablation.Budget(tmp_path, 1000)))
    assert written == {
        "split": ["doc-2:1:0", "doc-3:1:0"],
        "unsplit": ["doc-0:1:0", "doc-1:1:0", "doc-2:1:0", "doc-3:1:0"],
    }
    assert all(ablation.read(tmp_path / arm / "load-1.json")["written"] == 4 for arm in ablation.ARMS)


def run_fixture(tmp_path, monkeypatch):
    state = tmp_path / "state"
    config = {"arms": {arm: {"database": arm} for arm in ablation.ARMS}}
    config_path = tmp_path / "config.json"
    ablation.save(config_path, config)
    ablation.save(
        state / "canary-gates.json",
        {"configSha256": ablation.config_digest(config), **{arm: {"passed": True} for arm in ablation.ARMS}},
    )
    ablation.save(state / "shared" / "question-vectors.json", {"scale-p01": [[0.5]]})
    trace = []

    async def embed_questions(_args):
        return ablation.read(state / "shared" / "question-vectors.json")

    async def load(_config, _state, stage, _budget):
        trace.append(("load", stage))

    async def ready(_config, _state, stage, _vectors, _budget, _minutes, _settle):
        trace.append(("ready", stage))

    async def measure(_config, _state, stage, _vectors, _budget):
        trace.append(("measure", stage))

    async def inventory(_config, _state, stage, _budget):
        trace.append(("inventory", stage))

    monkeypatch.setattr(ablation, "bind", lambda *_args: None)
    monkeypatch.setattr(scale_probe, "embed_questions", embed_questions)
    monkeypatch.setattr(ablation, "load_stage", load)
    monkeypatch.setattr(ablation, "inventory", inventory)
    monkeypatch.setattr(ablation, "ready", ready)
    monkeypatch.setattr(ablation, "measure", measure)
    monkeypatch.setattr(ablation, "report", lambda *_args: {})
    return argparse.Namespace(state=state, config=config_path, minutes=1, settle=0), trace


def test_extra_lag_library_is_loaded_after_stage_two_measurements_before_stage_three(tmp_path, monkeypatch):
    args, trace = run_fixture(tmp_path, monkeypatch)
    asyncio.run(ablation.run(args))
    assert trace == [
        ("load", 1),
        ("inventory", 1),
        ("ready", 1),
        ("measure", 1),
        ("load", 2),
        ("inventory", 2),
        ("ready", 2),
        ("measure", 2),
        ("load", 4),
        ("load", 3),
        ("inventory", 3),
        ("ready", 3),
        ("measure", 3),
    ]


def test_canary_gate_is_bound_to_configuration_before_loading(tmp_path, monkeypatch):
    args, trace = run_fixture(tmp_path, monkeypatch)
    config = ablation.read(args.config)
    config["arms"]["unsplit"]["database"] = "different-target"
    ablation.save(args.config, config)
    with pytest.raises(RuntimeError, match="different private configuration"):
        asyncio.run(ablation.run(args))
    assert not trace


class InventoryContainer:
    def __init__(self, count, libraries):
        self.count = count
        self.libraries = libraries

    def query_items(self, sql, **kwargs):
        async def response():
            kwargs["raw_response_hook"](
                SimpleNamespace(http_response=SimpleNamespace(headers={"x-ms-request-charge": "1"}, status_code=200))
            )
            if sql == "SELECT VALUE COUNT(1) FROM c":
                yield self.count
            else:
                assert sql == "SELECT DISTINCT VALUE c.libraryId FROM c ORDER BY c.libraryId"
                for library in self.libraries:
                    yield library

        return response()


def inventory_fixture(stage, monkeypatch, *, fault=None):
    libraries = [lib for number in range(1, stage + 1) for lib in scale_probe.stage_libraries(number)]
    if stage == 3:
        libraries += scale_probe.stage_libraries(4)
    ids = {lib.library_id for lib in libraries}
    count = sum(lib.size for lib in libraries)
    observed_ids, observed_count = ids.copy(), count
    if fault == "canary-leftover":
        observed_ids.add("unexpected-canary")
        observed_count += 1500
    elif fault == "missing-lag-library":
        observed_ids.remove("scale-new")
        observed_count -= 1500
    elif fault == "wrong-label-same-total":
        observed_ids.remove("scale-p01")
        observed_ids.add("unexpected-library")
    selected = {"arm": None}

    def bind(_config, arm):
        selected["arm"] = arm

    async def close():
        pass

    async def clients():
        container = (
            InventoryContainer(count, ids)
            if selected["arm"] == "split"
            else InventoryContainer(observed_count, observed_ids)
        )
        return SimpleNamespace(close=close), SimpleNamespace(close=close), container

    monkeypatch.setattr(ablation, "bind", bind)
    monkeypatch.setattr(scale_probe, "_clients", clients)
    return count, len(ids)


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_inventory_verifies_actual_counts_and_membership_at_every_stage(stage, tmp_path, monkeypatch):
    count, libraries = inventory_fixture(stage, monkeypatch)
    asyncio.run(ablation.inventory({}, tmp_path, stage, ablation.Budget(tmp_path, 100)))
    for arm in ablation.ARMS:
        assert ablation.read(tmp_path / arm / f"inventory-{stage}.json") == {
            "vectors": count,
            "libraries": libraries,
            "matchesExpected": True,
        }
    if stage == 3:
        assert (count, libraries) == (244773, 1014)


@pytest.mark.parametrize("fault", ["canary-leftover", "missing-lag-library", "wrong-label-same-total"])
def test_inventory_refuses_contamination_or_wrong_membership_before_queries(fault, tmp_path, monkeypatch):
    inventory_fixture(3, monkeypatch, fault=fault)
    with pytest.raises(RuntimeError, match="actual inventory differs"):
        asyncio.run(ablation.inventory({}, tmp_path, 3, ablation.Budget(tmp_path, 100)))
    assert ablation.read(tmp_path / "split" / "inventory-3.json")["matchesExpected"]
    assert not ablation.read(tmp_path / "unsplit" / "inventory-3.json")["matchesExpected"]


def test_resume_refuses_changed_frozen_query_vectors_before_loading(tmp_path, monkeypatch):
    args, trace = run_fixture(tmp_path, monkeypatch)
    asyncio.run(ablation.run(args))
    trace.clear()
    ablation.save(args.state / "shared" / "question-vectors.json", {"scale-p01": [[0.75]]})
    with pytest.raises(RuntimeError, match="frozen query vectors changed"):
        asyncio.run(ablation.run(args))
    assert not trace
