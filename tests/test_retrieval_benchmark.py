"""R0's evaluation controls and receipts, using local service/model fakes only."""

import copy
import fcntl
import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from docx import Document

from scripts import evaluate_agent_retrieval as evaluation
from scripts import retrieval_benchmark as benchmark


def pack():
    manifest = {
        "documents": [
            {
                "id": "d01",
                "fileName": "development.docx",
                "sha256": "development-bytes",
                "sizeBytes": 10,
                "split": "development",
                "expectedInitialGeneration": 1,
                "metadata": {"author": "Public publisher", "tags": [{"key": "topic", "value": "policy"}]},
            },
            {
                "id": "d02",
                "fileName": "heldout.docx",
                "sha256": "heldout-bytes",
                "sizeBytes": 11,
                "split": "heldout",
                "expectedInitialGeneration": 1,
                "metadata": {},
            },
        ],
        "baseline": {"sourceIds": ["d01"]},
    }
    tasks = [
        {
            "case": "development-fact",
            "split": "development",
            "question": "What is the policy deadline?",
            "sourceFacts": [{"sourceId": "d01", "assertion": "NEVER_SEND_GOLD"}],
        },
        {
            "case": "heldout-matrix",
            "split": "heldout",
            "question": "What does the held-out matrix show?",
            "sourceFacts": [{"sourceId": "d02", "assertion": "HELDOUT_GOLD"}],
        },
    ]
    return manifest, tasks


def configuration():
    return {
        "model": {"deployment": "diagnostic-answerer", **benchmark.MODEL},
        "libraries": {role: role + "-library" for role in benchmark.ROLES},
        "agent": {"id": "review-agent", "name": "Reviewer", "instructions": "Read and cite original evidence."},
    }


class Index:
    def __init__(self):
        manifest, _ = pack()
        source = manifest["documents"][0]
        self.document = {
            "id": "document-one",
            "fileName": source["fileName"],
            "sha256": source["sha256"],
            "status": "ready",
            "activeGeneration": 1,
            "pendingGeneration": None,
            "contentRevision": 1,
            "metadataVersion": 0,
            **source["metadata"],
        }
        self.profiles = {role: benchmark.profile(profile) for role, profile in benchmark.ROLES.items()}
        self.tools_version = 1

    def get_library(self, library):
        role = library.removesuffix("-library")
        return copy.deepcopy({"name": "Public documents", "ingestionProfile": self.profiles[role]})

    def list_documents(self, library):
        return [copy.deepcopy(self.document)]

    def tools(self, library, agent):
        return {"version": self.tools_version, "definition": "same retrieval tools"}


def run(index, tmp_path, **kwargs):
    manifest, tasks = pack()
    return benchmark.run_replay(
        index,
        configuration(),
        manifest,
        tasks,
        tmp_path,
        chat_factory=lambda *_: object(),
        hash_fn=lambda: {"agent.py": "frozen"},
        **kwargs,
    )


def test_fixed_model_budget_and_fresh_paired_order(tmp_path):
    manifest, tasks = pack()
    config = configuration()
    unchanged = copy.deepcopy((manifest, tasks, config))
    calls, clients = [], []

    def factory(model, transport):
        chat = object()
        clients.append((chat, copy.deepcopy(model), transport))
        return chat

    def replay(index, chat, agent, cases, output, budget):
        calls.append((copy.deepcopy(agent), copy.deepcopy(cases), budget))
        cases[0]["question"] = "mutation in one invocation only"
        return [None]

    status = benchmark.run_replay(
        Index(),
        config,
        manifest,
        tasks,
        tmp_path,
        chat_factory=factory,
        replay_fn=replay,
        hash_fn=lambda: {"agent.py": "frozen"},
    )
    assert status["completedTurns"] == 6
    assert status["heldoutExecuted"] is False
    assert [c[0]["libraryIds"] for c in calls] == [
        [role + "-library"] for role in ("current", "custom", "microsoft", "microsoft", "custom", "current")
    ]
    assert all(c[2] == 200000 for c in calls)
    assert all(c[1] == [tasks[0]] for c in calls)
    assert len({id(c[0]) for c in clients}) == 6
    assert all(c[1] == config["model"] and c[2] == "responses" for c in clients)
    assert (manifest, tasks, config) == unchanged
    controls = json.loads((tmp_path / "controls.json").read_text())
    assert controls["reasoningEffort"] == "medium"
    assert controls["maxOutputTokens"] == 8000
    assert controls["maxAttempts"] == 3
    assert controls["store"] is False and controls["automaticTruncation"] is False


@pytest.mark.parametrize("damage", ["model", "version", "context", "libraries", "agent"])
def test_unmatched_controls_rejected_before_model_calls(tmp_path, damage):
    manifest, tasks = pack()
    config = configuration()
    if damage in {"model", "version"}:
        config["model"][damage] = "different"
    elif damage == "context":
        config["contextAllowancePerRun"] = 24000
    elif damage == "libraries":
        config["libraries"]["custom"] = config["libraries"]["current"]
    else:
        config["agent"].pop("instructions")
    with pytest.raises(ValueError):
        benchmark.run_replay(Index(), config, manifest, tasks, tmp_path)
    assert not (tmp_path / "controls.json").exists()


@pytest.mark.parametrize("repeats", [0, 4, True, 1.5])
def test_unbounded_or_noninteger_repeats_rejected(tmp_path, repeats):
    with pytest.raises(ValueError, match="bounded"):
        run(Index(), tmp_path, repeats=repeats)


def test_gold_and_heldout_data_never_reach_answering_boundary(tmp_path, monkeypatch):
    observed = []

    def turn(chat, index, agent, history, question, **kwargs):
        observed.append(copy.deepcopy({"agent": agent, "history": history, "question": question, **kwargs}))
        return SimpleNamespace(answer="An answer from original evidence.", messages=[], searches=[])

    monkeypatch.setattr(evaluation, "run_turn", turn)
    monkeypatch.setattr(evaluation, "metrics", lambda _: {})
    run(Index(), tmp_path, repeats=1)
    assert len(observed) == 3
    encoded = json.dumps(observed)
    assert "NEVER_SEND_GOLD" not in encoded and "HELDOUT_GOLD" not in encoded
    assert "sourceFacts" not in encoded and "heldout-matrix" not in encoded
    assert all(row["history"] == [] and row["context_token_budget"] == 200000 for row in observed)


def test_explicit_heldout_case_selection_is_refused(tmp_path):
    with pytest.raises(ValueError, match="held-out"):
        run(Index(), tmp_path, case_ids=["heldout-matrix"])


def test_development_source_labels_cannot_reference_heldout():
    manifest, tasks = pack()
    tasks[0]["sourceFacts"][0]["sourceId"] = "d02"
    with pytest.raises(ValueError, match="held-out"):
        benchmark.validate_pack(manifest, tasks)


@pytest.mark.parametrize("source_ids", [["d02"], ["unknown"], ["d01", "d01"], []])
def test_baseline_source_selection_rejects_heldout_unknown_or_duplicates(source_ids):
    manifest, tasks = pack()
    manifest["baseline"]["sourceIds"] = source_ids
    with pytest.raises(ValueError, match="development sources"):
        benchmark.validate_pack(manifest, tasks)


def test_replay_refuses_development_task_whose_source_is_not_loaded(tmp_path):
    manifest, tasks = pack()
    extra = {**manifest["documents"][0], "id": "d03", "fileName": "unloaded.docx"}
    manifest["documents"].append(extra)
    tasks[0]["sourceFacts"][0]["sourceId"] = "d03"
    with pytest.raises(ValueError, match="unloaded source"):
        benchmark.run_replay(Index(), configuration(), manifest, tasks, tmp_path)


@pytest.mark.parametrize("change", ["source", "metadata", "profile", "generation", "tools", "code"])
def test_drift_stops_replay_and_preserves_completed_receipts(tmp_path, change):
    index, code = Index(), {"agent.py": "before"}

    def replay(index, chat, agent, cases, output, budget):
        output.mkdir()
        (output / "results.json").write_text('[{"answer":"retained"}]')
        if change == "source":
            index.document["sha256"] = "changed"
        elif change == "metadata":
            index.document["author"] = "Different publisher"
        elif change == "profile":
            index.profiles["current"]["version"] = "changed"
        elif change == "generation":
            index.document["activeGeneration"] += 1
        elif change == "tools":
            index.tools_version += 1
        else:
            code["agent.py"] = "after"
        return [None]

    manifest, tasks = pack()
    with pytest.raises(RuntimeError, match="confounded"):
        benchmark.run_replay(
            index,
            configuration(),
            manifest,
            tasks,
            tmp_path,
            chat_factory=lambda *_: object(),
            replay_fn=replay,
            hash_fn=lambda: dict(code),
        )
    status = json.loads((tmp_path / "run-status.json").read_text())
    assert status["status"] == "failed" and status["completedTurns"] == 1
    assert len(list(tmp_path.glob("*/results.json"))) == 1


@pytest.mark.parametrize("error", [RuntimeError("unavailable"), KeyboardInterrupt()])
def test_failure_and_interrupt_keep_partial_turn_receipts(tmp_path, error):
    def replay(index, chat, agent, cases, output, budget):
        output.mkdir()
        (output / "run-status.json").write_text('{"completedTurns":1,"failedAttempt":{"usage":{"total_tokens":17}}}')
        (output / "results.json").write_text('[{"answer":"retained"}]')
        raise error

    with pytest.raises(type(error)):
        run(Index(), tmp_path, replay_fn=replay)
    status = json.loads((tmp_path / "run-status.json").read_text())
    assert status["completedTurns"] == 1
    assert status["status"] == ("interrupted" if isinstance(error, KeyboardInterrupt) else "failed")
    cell = json.loads(next(tmp_path.glob("*/run-status.json")).read_text())
    assert cell["failedAttempt"]["usage"]["total_tokens"] == 17
    with pytest.raises(ValueError, match="empty"):
        run(Index(), tmp_path, replay_fn=replay)


@pytest.mark.parametrize("damage", ["source", "metadata", "profile", "recipe", "generation", "pending", "status"])
def test_initial_source_rejection_has_terminal_receipt(tmp_path, damage):
    index = Index()
    if damage == "source":
        index.document["sha256"] = "not-frozen"
    elif damage == "metadata":
        index.document["author"] = "not-frozen"
    elif damage == "profile":
        index.profiles["current"]["id"] = "not-frozen"
    elif damage == "recipe":
        index.profiles["current"]["chunking"]["targetTokens"] = 123
    elif damage == "generation":
        index.document["activeGeneration"] = 2
    elif damage == "pending":
        index.document["pendingGeneration"] = 2
    else:
        index.document["status"] = "processing"
    with pytest.raises(ValueError):
        run(index, tmp_path)
    assert json.loads((tmp_path / "run-status.json").read_text())["status"] == "failed"


def local_corpus(tmp_path):
    """Synthetic parser fixtures exercise plumbing, never search/model quality."""
    manifest, tasks = pack()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    doc = Document()
    doc.add_heading("Policy", level=1)
    doc.add_paragraph("Short source block.")
    doc.add_paragraph(" ".join(f"The published provision number {n} has a complete sentence." for n in range(110)))
    stream = io.BytesIO()
    doc.save(stream)
    payloads = [stream.getvalue(), b"heldout hash-only file is deliberately not a valid DOCX"]
    for source, data in zip(manifest["documents"], payloads, strict=True):
        (corpus / source["fileName"]).write_bytes(data)
        source["sha256"] = hashlib.sha256(data).hexdigest()
        source["sizeBytes"] = len(data)
    return manifest, tasks, corpus


async def test_local_baseline_measures_actual_chunks_without_opening_heldout(tmp_path):
    manifest, tasks, corpus = local_corpus(tmp_path)
    output = tmp_path / "measurement"
    result = await benchmark.local_baseline(manifest, tasks, corpus, output)
    assert result["heldoutExecuted"] is False
    assert result["tokenizer"] == "cl100k_base"
    assert {row["sourceId"] for row in result["rows"]} == {"d01"}
    assert {row["profile"] for row in result["rows"]} == {"current-v1", "custom-structured-v1"}
    assert not list(output.glob("d02-*"))
    for row in result["rows"]:
        stats = row["chunks"]
        assert stats["count"] > 1 and stats["min"] < stats["max"] <= 500
        assert stats["total"] <= stats["count"] * 500
        assert stats["total"] != stats["count"] * result["targetTokens"]
        assert row["bodyTokensEstimate"] > 500
        assert row["adjacentBodyOverlapTokens"]["max"] <= 50
    receipt = json.loads((output / "receipt.json").read_text())
    assert "published provision" not in json.dumps(receipt)
    assert (output / "d01-current.json").exists()


def test_hash_or_size_drift_is_rejected_before_extraction(tmp_path):
    manifest, _, corpus = local_corpus(tmp_path)
    (corpus / "development.docx").write_bytes(b"changed source bytes")
    with pytest.raises(ValueError, match="frozen manifest"):
        benchmark.verify_corpus(manifest, corpus)


class AmbiguousIndex:
    def __init__(self, fail_on):
        self.fail_on = fail_on
        self.creates = 0
        self.uploads = 0
        self.pair_ids = []
        self.source_ids = []

    def create_library(self, *args):
        self.creates += 1
        if self.fail_on == "create":
            raise TimeoutError("response missing after create")
        return {"id": "current-library"}

    def create_pair(self, experiment, *args):
        self.pair_ids.append(experiment)
        return {"members": {role: {"libraryId": role + "-library"} for role in ("custom", "microsoft")}}

    def _call(self, method, path, **kwargs):
        if path.startswith("/v1/experiments/"):
            self.source_ids.append(path.rsplit("/", 1)[-1])
            return {"status": "queued"}
        self.uploads += 1
        raise TimeoutError("response missing after current upload")


@pytest.mark.parametrize("failure", ["create", "upload"])
def test_ambiguous_nonidempotent_write_stops_without_retry(tmp_path, failure):
    manifest, _, corpus = local_corpus(tmp_path)
    index, output = AmbiguousIndex(failure), tmp_path / "ingest"
    with pytest.raises(TimeoutError):
        benchmark.ingest(index, manifest, corpus, output)
    with pytest.raises(ValueError, match="ambiguous"):
        benchmark.ingest(index, manifest, corpus, output)
    assert index.creates == 1
    assert index.uploads == (1 if failure == "upload" else 0)
    if failure == "upload":
        assert len(set(index.pair_ids)) == len(set(index.source_ids)) == 1
    assert (output / "ingest-state.json").is_file()


def test_ingestion_lock_refuses_a_second_owner_before_writes(tmp_path):
    manifest, _, corpus = local_corpus(tmp_path)
    output = tmp_path / "ingest"
    output.mkdir()
    index = AmbiguousIndex("create")
    with (output / ".ingest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another ingestion"):
            benchmark.ingest(index, manifest, corpus, output)
    assert index.creates == index.uploads == 0
    assert not (output / "ingest-state.json").exists()
