"""Bounded pair preparation and real local API wiring; no Azure requests."""

import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from fli_demo.client import IndexError_
from scripts import granular_reference_live as runner
from scripts.granular_live_models import _source_snapshot
from scripts.granular_pacing import PacingRefusal
from scripts.qualify_granular_navigation import fixtures
from tests.test_granular_followup import hashes, write
from tests.test_granular_live_models import Clock, binding
from tests.test_granular_navigation import ready
from tests.test_granular_pacing import policy


@pytest.fixture
def prior(tmp_path, monkeypatch):
    original = fixtures()["source.docx"]
    env = asyncio.run(ready(source=original, name="source.docx"))
    doc = env.doc
    source = {
        "docx": {
            "libraryId": env.lib,
            "documentId": doc["id"],
            "generation": doc["activeGeneration"],
            "contentRevision": doc["contentRevision"],
            "fileName": doc["fileName"],
            "sourceSha256": doc["sha256"],
        }
    }
    previous = tmp_path / "prior"
    (previous / "fixtures").mkdir(parents=True)
    (previous / "fixtures/source.docx").write_bytes(original)
    sections = json.loads(asyncio.run(env.s.blobs.download(doc["fullText"]["blobPath"])))
    write(
        previous / f"{doc['id']}-publication-{doc['activeGeneration']}.json",
        {"document": doc, "sections": sections, "chunks": []},
    )
    write(previous / "models/sources-before.json", _source_snapshot(env.index, source))
    write(previous / "source-bindings.json", source)
    write(previous / "answering-binding-after.json", binding())
    write(previous / "capacity-policy.json", policy())
    write(
        previous / "account.json",
        {"id": "/subscriptions/example/resourceGroups/example/providers/Microsoft.CognitiveServices/accounts/example"},
    )
    write(
        previous / "environment.json",
        {
            "AOAI_ENDPOINT": "https://model.example.invalid",
            "AOAI_CHAT_DEPLOYMENT": "chosen",
            "COSMOS_ENDPOINT": "must-not-copy",
            "DEV_API_KEY": "must-not-copy",
        },
    )
    write(
        previous / "execution-result.json",
        {
            "cleanup": {"passed": True},
            "models": {"completedTurns": 1},
            "finalOwnership": {"unresolvedOperations": 0, "unfinishedMessages": 0},
        },
    )
    manifest = tmp_path / "archive-manifest.json"
    write(manifest, {"files": hashes(previous)})
    repo = tmp_path / "repository"
    write(
        repo / "records/granular-refinement-qualification-20261105.json",
        {"privateArchives": {"archives": [{"role": "reviewed-final", "manifestSha256": runner.sha(manifest)}]}},
    )
    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "code_hashes", lambda: {"reviewed.py": "a" * 64})
    return previous, manifest


@pytest.fixture
def prepared(tmp_path, prior):
    root = tmp_path / "pair"
    runner.prepare(root, prior[0], prior_manifest=prior[1])
    return root


async def test_prepare_copies_only_exact_source_and_answering_config_without_cloud(tmp_path, prior, monkeypatch):
    monkeypatch.setattr(runner, "management_get", lambda *args: pytest.fail("cloud call during preparation"))
    previous = hashes(prior[0])
    root = tmp_path / "pair"
    result = runner.prepare(root, prior[0], prior_manifest=prior[1])
    controls = runner.verify(root)
    assert result["cells"] == ["compact", "navigation"] and result["cloudCalls"] == 0
    assert controls["contextAllowance"] == 200000 and controls["outputAllowance"] == 8000
    assert controls["meterLimits"] == runner.LIMITS and controls["maxRounds"] == 12
    assert set(runner.read(root / "model-config.json")) == {"AOAI_ENDPOINT", "AOAI_CHAT_DEPLOYMENT"}
    assert "must-not-copy" not in (root / "model-config.json").read_text()
    assert (root / "fixture/source.docx").read_bytes() == (prior[0] / "fixtures/source.docx").read_bytes()
    assert hashes(prior[0]) == previous
    with pytest.raises(ValueError, match="fresh private"):
        runner.prepare(root, prior[0], prior_manifest=prior[1])


@pytest.mark.parametrize(
    "changed",
    ["fixtures/source.docx", "models/sources-before.json", "answering-binding-after.json", "environment.json"],
)
async def test_prepare_refuses_archive_input_drift(tmp_path, prior, changed):
    (prior[0] / changed).write_bytes((prior[0] / changed).read_bytes() + b"\n")
    with pytest.raises(ValueError, match="prior archive input changed"):
        runner.prepare(tmp_path / "pair", prior[0], prior_manifest=prior[1])
    assert not (tmp_path / "pair").exists()


@pytest.mark.parametrize("changed", ["controls", "source", "policy", "code"])
async def test_frozen_controls_inputs_and_code_fail_closed(prepared, monkeypatch, changed):
    if changed == "controls":
        path = prepared / "controls.json"
        value = runner.read(path)
        value["cells"].reverse()
        write(path, value)
    elif changed == "code":
        monkeypatch.setattr(runner, "code_hashes", lambda: {"changed": True})
    else:
        path = prepared / ("fixture/source.docx" if changed == "source" else "capacity-policy.json")
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="changed|drift"):
        runner.verify(prepared)


async def test_local_snapshot_uses_normal_api_and_preserves_complete_source_without_cloud(prepared):
    source = runner.read(prepared / "fixture/source-bindings.json")["docx"]
    pin = {k: source[k] for k in ("documentId", "generation", "contentRevision")}
    with runner.local_index(prepared) as index:
        outline = index.read_documents(source["libraryId"], "reader", mode="outline", documents=[pin])
        assert outline["evidence"]["documents"][0]["outline"]
        full = index.read_documents(source["libraryId"], "reader", mode="full", documents=[pin], maxTokens=200000)
        assert full["evidence"]["documentComplete"]
        original = runner.read(prepared / "fixture/publication.json")["sections"]
        assert [s["text"] for s in full["evidence"]["documents"][0]["sections"]] == [s["text"] for s in original]
        with pytest.raises(IndexError_, match="snapshot_search_unavailable"):
            index.search(source["libraryId"], "reader", "not synthetic ranking")
        with pytest.raises(IndexError_) as error:
            index.read_documents(source["libraryId"], "reader", mode="full", documents=[{**pin, "generation": 99}])
        assert error.value.status == 409
        http = index.http
    assert http.is_closed


async def test_invalid_image_descriptor_cannot_be_silently_ignored(prepared):
    publication = runner.read(prepared / "fixture/publication.json")
    publication["document"]["imageAssets"]["sha256"] = "0" * 64
    write(prepared / "fixture/publication.json", publication)
    with pytest.raises(ValueError, match="image manifest"):
        with runner.local_index(prepared):
            pytest.fail("corrupt source accepted")


async def test_preflight_is_read_only_and_single_use(prepared, monkeypatch):
    called = []
    monkeypatch.setattr(runner, "current_binding", lambda root, role: called.append(role) or binding())
    assert runner.preflight(prepared)["modelCalls"] == 0
    assert called == ["preflight"]
    with pytest.raises(ValueError, match="single-use"):
        runner.preflight(prepared)
    assert called == ["preflight"]


async def test_runtime_binding_mismatch_prevents_pair_before_any_provider(prepared):
    provider = SimpleNamespace(
        max_retries=0, responses=SimpleNamespace(create=lambda **kwargs: pytest.fail("provider called"))
    )
    with pytest.raises(ValueError, match="runtime model binding"):
        runner.run_pair(prepared, provider=provider, deployment="chosen", binding={"wrong": True}, deadline=1000)
    assert not (prepared / "pair-started.json").exists()


async def test_pair_failure_captures_source_after_closes_api_and_cannot_rerun(prepared, monkeypatch):
    contexts = []
    original = runner.local_index

    @contextmanager
    def local(root):
        with original(root) as index:
            contexts.append(index.http)
            yield index

    def fail(*args, **kwargs):
        assert kwargs["presentation"] is None
        raise PacingRefusal("stop once")

    monkeypatch.setattr(runner, "local_index", local)
    monkeypatch.setattr(runner, "run_turn", fail)
    provider = SimpleNamespace(max_retries=0)
    with pytest.raises(PacingRefusal, match="stop once"):
        runner.run_pair(prepared, provider=provider, deployment="chosen", binding=binding(), deadline=10**12)
    result = runner.read(prepared / "pair-result.json")
    assert result["completedTurns"] == 0 and result["provider"]["physicalResponsesAttempts"] == 0
    assert runner.read(prepared / "compact/source-check.json")["passed"]
    assert contexts[0].is_closed and result["cosmosRu"] is None
    assert not (prepared / "navigation").exists()
    with pytest.raises(FileExistsError):
        runner.run_pair(prepared, provider=provider, deployment="chosen", binding=binding(), deadline=10**12)


@pytest.mark.parametrize("failure", ["stale", "dirty", "postcheck"])
async def test_execution_gates_and_failure_preservation_archive(prepared, tmp_path, monkeypatch, failure):
    events = []
    write(
        prepared / "preflight.json",
        {
            "passed": True,
            "capturedAt": (datetime.now(UTC) - timedelta(seconds=901 if failure == "stale" else 0)).isoformat(),
            "binding": binding(),
        },
    )
    monkeypatch.setattr(
        runner.subprocess,
        "check_output",
        lambda args, **kwargs: (" M changed.py" if failure == "dirty" else "") if "status" in args else "a" * 40,
    )
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    def current(root, role):
        events.append(role)
        if role == "after":
            raise RuntimeError("postcheck also failed")
        return binding()

    def pair(*args, **kwargs):
        events.append("pair")
        raise PacingRefusal("primary model failure")

    monkeypatch.setattr(runner, "current_binding", current)
    monkeypatch.setattr(
        runner, "actual_client", lambda endpoint: SimpleNamespace(close=lambda: events.append("closed"))
    )
    monkeypatch.setattr(runner, "run_pair", pair)
    output = tmp_path / "archive"
    if failure != "postcheck":
        with pytest.raises(ValueError):
            runner.execute(prepared, output)
        assert events == [] and not (prepared / "execution-started.json").exists()
    else:
        with pytest.raises(PacingRefusal, match="primary model failure"):
            runner.execute(prepared, output)
        assert events == ["before", "pair", "closed", "after"]
        result = runner.read(prepared / "execution-result.json")
        assert result["providerClosed"] and not result["postchecks"]["passed"]
        assert result["cloudDataResourcesCreated"] == 0
        assert (output / "execution-result.json").exists()
        assert runner.read(prepared / "archive-receipt.json")["files"] > 0


async def test_real_agent_pair_resolves_only_delivered_references_and_shares_usage_window(prepared, monkeypatch):
    """Scripted provider checks wiring, never autonomous model quality."""
    from tests.test_responses_chat import call, message, reasoning, response

    source = runner.read(prepared / "fixture/source-bindings.json")["docx"]
    pin = {key: source[key] for key in ("documentId", "generation", "contentRevision")}
    clock, pacers, requests, delivered = Clock(), [], [], []
    original_pacer = runner.Pacer

    def make_pacer(deadline, *, policy):
        pacer = original_pacer(deadline, policy=policy, clock=clock, sleep=clock.sleep)
        pacers.append(pacer)
        return pacer

    monkeypatch.setattr(runner, "Pacer", make_pacer)

    def create(**request):
        requests.append(request)
        tool_name = next(tool["name"] for tool in request["tools"] if tool["name"].startswith("read_documents_"))
        outputs = [item for item in request["input"] if item.get("type") == "function_call_output"]
        if not outputs:
            arguments = {"mode": "outline", "documents": [pin]}
        else:
            payload = json.loads(outputs[-1]["output"])
            delivered.append(payload)
            assert not payload.get("error"), payload
            document = payload["documents"][0]
            if payload["mode"] == "selected":
                rows = document["sections"]
                assert len(rows) == 1 and rows[0]["text"] == "<p>Do not sample a damaged vessel.</p>"
                return response(message("Do not sample a damaged vessel. (" + rows[0]["citation"] + ")"))
            outlines = document["outline"]
            row = next(
                (
                    r
                    for r in outlines
                    if r.get("navigationPreview", {}).get("prefix") == "Do not sample a damaged vessel."
                ),
                None,
            )
            if row is None:
                row = next(
                    (r for r in outlines if r.get("heading") in {"Operating rules", "Sampling", "Exceptions"}), None
                )
                assert row is not None, outlines
                action, selector = "outline", "outlineSelector"
            else:
                action, selector = "read", "readSelector"
            arguments = {"reference": row["actions"][action]} if "actions" in row else row[selector]
        item = call("c" + str(len(requests)))
        item.update(name=tool_name, arguments=json.dumps(arguments))
        return response(reasoning("rs" + str(len(requests))), item)

    provider = SimpleNamespace(max_retries=0, responses=SimpleNamespace(create=create))
    result = runner.run_pair(prepared, provider=provider, deployment="chosen", binding=binding(), deadline=1000)
    assert result["completedTurns"] == 2 and result["status"] == "pair_complete_pending_source_review"
    assert len(pacers) == 1 and len(requests) == 12
    assert result["provider"]["physicalResponsesAttempts"] == 12
    assert result["provider"]["knownInputTokens"] == 1200
    assert result["provider"]["knownOutputTokens"] == 600
    assert clock.sleeps and result["localApiContextsClosed"]
    compact = runner.read(prepared / "compact/turn.json")
    navigation = runner.read(prepared / "navigation/turn.json")
    assert compact["answer"] == navigation["answer"]
    diagnostics = result["cells"][1]["presentationDiagnostics"]
    assert diagnostics["closed"] and diagnostics["turnLocal"]
    resolutions = [r for r in diagnostics["resolutions"] if "reference" in r["originalArguments"]]
    assert len(resolutions) == 4 and all("reference" not in r["resolvedArguments"] for r in resolutions)
    assert all(r["resolvedArguments"]["documents"][0]["generation"] == pin["generation"] for r in resolutions)
    # Canonical stored evidence remains diagnostic and exact; only actual wire is rendered.
    assert all("presentation" not in json.loads(m["content"]) for m in navigation["messages"] if m["role"] == "tool")
    assert any(p.get("presentation") == "fli.navigation.v1" for p in delivered)
    assert all(
        "Do not sample a damaged vessel." in runner.read(prepared / cell / "turn.json")["answer"]
        for cell in runner.CELLS
    )
    # Responses retains exact opaque output on append-only histories in both cells.
    for offset in (0, 6):
        assert requests[offset + 1]["input"][2:4] == [
            reasoning("rs" + str(offset + 1)),
            {
                **call("c" + str(offset + 1)),
                "name": next(t["name"] for t in requests[offset]["tools"] if t["name"].startswith("read_documents_")),
                "arguments": json.dumps({"mode": "outline", "documents": [pin]}),
            },
        ]


@pytest.mark.parametrize("mismatch", [None, "binding", "capacity"])
async def test_fresh_arm_binding_and_capacity_are_required_before_provider(prepared, monkeypatch, mismatch):
    from tests.test_granular_pacing import capacity

    observed, arm = [], capacity()
    if mismatch == "capacity":
        arm["properties"]["rateLimits"][0]["count"] = 40000

    def get(url, path):
        observed.append(url)
        return arm if "/deployments/" in url else {"account": "matching"}

    monkeypatch.setattr(runner, "management_get", get)
    monkeypatch.setattr(
        runner, "deployment_binding", lambda *a, **kw: {"wrong": True} if mismatch == "binding" else binding()
    )
    if mismatch:
        with pytest.raises(ValueError, match="fresh answering|fresh deployment"):
            runner.current_binding(prepared, "before")
        assert not (prepared / "binding-before.json").exists()
    else:
        assert runner.current_binding(prepared, "before") == binding()
        assert runner.read(prepared / "binding-before.json")["binding"] == binding()
    assert len(observed) == 2 and all(url.startswith("https://management.azure.com/") for url in observed)
