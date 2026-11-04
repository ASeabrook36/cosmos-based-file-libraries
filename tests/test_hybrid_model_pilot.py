"""The exploratory pilot must fail closed before changing controls or spending more."""

import json
from collections import Counter
from datetime import UTC, datetime

import httpx
import pytest
from openai import RateLimitError

from scripts import hybrid_model_pilot as p
from scripts.hybrid_model_meter import Meter, meter_summary, scrub_media
from tests.test_responses_chat import client_for, message, reasoning, response


@pytest.fixture
def config():
    return {
        "models": {role: {**value, "deployment": role + "-isolated"} for role, value in p.MODELS.items()},
        "agent": {"id": "agent", "name": "Agent", "instructions": p.AGENT_INSTRUCTIONS},
        "libraryId": "private-library",
        "indexUrls": {"vector": "http://127.0.0.1:8124", "hybrid": "http://127.0.0.1:8125"},
    }


def deployment(config, role):
    return {
        "capturedAt": datetime.now(UTC).isoformat(),
        "account": {"id": "/accounts/test", "properties": {"endpoint": "https://test.invalid"}},
        "deployment": {
            "id": "/accounts/test/deployments/" + config["models"][role]["deployment"],
            "sku": {"name": "GlobalStandard", "capacity": 200},
            "properties": {
                "model": {"format": "OpenAI", "name": p.MODELS[role]["model"], "version": p.MODELS[role]["version"]},
                "versionUpgradeOption": "NoAutoUpgrade",
                "provisioningState": "Succeeded",
            },
        },
    }


def loaded():
    return p.inputs(
        p.REPO / "docs/testing/hybrid-model-pilot/plan.json",
        p.REPO / "docs/testing/hybrid-quality/manifest.json",
        p.REPO / "docs/testing/hybrid-quality/tasks.json",
    )


def test_real_pack_and_order_are_frozen_and_balanced():
    plan, _, tasks = loaded()
    assert plan["runLimits"] == p.LIMITS and len(tasks) == 48
    rows = p.schedule(tasks)
    assert len(rows) == len({r["run"] for r in rows}) == 192
    assert set(Counter((i % 4, r["cell"]) for i, r in enumerate(rows)).values()) == {12}
    predecessors = Counter((rows[i]["cell"], rows[i + 1]["cell"]) for i in range(192) if i % 4 != 3)
    assert len(predecessors) == 12 and set(predecessors.values()) == {12}


@pytest.mark.parametrize(
    "key,value",
    [
        ("repeats", 2),
        ("runLimits", {}),
        ("answering", {}),
        ("models", {}),
        ("cases", []),
        ("packUse", "fresh-confirmation"),
    ],
)
def test_changed_plan_rejected(tmp_path, key, value):
    plan, _, _ = loaded()
    plan[key] = value
    path = tmp_path / "plan.json"
    p.save(path, plan)
    with pytest.raises(ValueError, match="prospective freeze"):
        p.inputs(
            path,
            p.REPO / "docs/testing/hybrid-quality/manifest.json",
            p.REPO / "docs/testing/hybrid-quality/tasks.json",
        )


def test_config_and_actual_capacity_are_bound(config):
    p.validate_config(config)
    receipts = {role: deployment(config, role) for role in p.ROLES}
    result = p.model_bindings(receipts, config, "https://test.invalid")
    assert result["budget"]["model"]["version"] == "2026-03-17"
    receipts["budget"]["deployment"]["sku"]["capacity"] = 100
    with pytest.raises(ValueError, match="capacity differs"):
        p.model_bindings(receipts, config, "https://test.invalid")
    config["models"]["budget"]["version"] = "latest"
    with pytest.raises(ValueError, match="identity differs"):
        p.validate_config(config)


def fake_client(*responses):
    client, requests = client_for(*responses)
    client.max_retries = 0
    client.close = lambda: None
    return client, requests


def test_meter_reserves_unknown_attempt_and_records_actual_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("fli_demo.responses.time.sleep", lambda seconds: None)
    error = RateLimitError(
        "secret server body",
        response=httpx.Response(
            429, headers={"retry-after-ms": "1"}, request=httpx.Request("POST", "https://test.invalid")
        ),
        body=None,
    )
    actual, requests = fake_client(error, response(message()))
    meter = Meter(tmp_path / "provider", p.LIMITS)
    chat = meter.client(actual, {"run": "r", "modelRole": "sol"}, "sol-isolated")
    reply = chat([{"role": "user", "content": "Q"}], [])
    assert reply.content == "Answer" and len(requests) == 2
    summary = meter_summary(meter.path)
    assert summary["physicalResponsesAttempts"] == 2 and summary["logicalCallsStarted"] == 1
    assert summary["knownInputTokens"] == 100 and summary["knownOutputTokens"] == 50
    assert summary["unmeteredInputReservations"] == 64000 and summary["unmeteredOutputReservations"] == 8000
    assert summary["completeInputTokens"] is None and summary["http429Attempts"] == 1
    assert "secret server body" not in meter.path.read_text()
    assert p.read(meter.path)["calls"][0]["finishedAt"]


def test_reservation_ceiling_stops_before_provider_call(tmp_path):
    actual, requests = fake_client(response(message()), response(message()))
    meter = Meter(tmp_path / "provider", {**p.LIMITS, "inputTokensWithUnknownReservations": 64000})
    chat = meter.client(actual, {"run": "r"}, "sol-isolated")
    chat([], [])
    with pytest.raises(ValueError, match="reservation ceiling"):
        chat([], [])
    assert len(requests) == 1 and meter.state["physicalResponsesAttempts"] == 1


@pytest.mark.parametrize(
    "body,error",
    [
        (response(message(), usage=None), "meters unavailable"),
        (response(message(), usage={"input_tokens": 64001, "output_tokens": 1}), "matched allowance"),
    ],
)
def test_unmetered_or_excessive_response_preserved_before_stop(tmp_path, body, error):
    actual, _ = fake_client(body)
    meter = Meter(tmp_path / "provider", p.LIMITS)
    with pytest.raises(ValueError, match=error):
        meter.client(actual, {"run": "r"}, "sol-isolated")([], [])
    assert (meter.root / "response-00001.json").is_file()
    assert meter.state["calls"][0]["status"] == "response"


def test_hidden_retries_and_fourth_attempt_forbidden(tmp_path):
    actual, _ = fake_client()
    actual.max_retries = 2
    meter = Meter(tmp_path / "provider", p.LIMITS)
    with pytest.raises(ValueError, match="SDK retries"):
        meter.client(actual, {"run": "r"}, "sol-isolated")
    logical = meter.logical({"run": "r"})
    for _ in range(3):
        row = meter.begin_attempt(logical, {})
        meter.finish_attempt(row, error=ValueError("secret"), elapsed_ms=1)
    with pytest.raises(ValueError, match="per-call attempt"):
        meter.begin_attempt(logical, {})
    for _ in range(12):
        meter.logical({"run": "r"})
    with pytest.raises(ValueError, match="per-turn"):
        meter.logical({"run": "r"})


def test_media_urls_are_not_serialized():
    output = scrub_media({"input": [{"image_url": "https://secret.invalid/?credential=secret"}], "url": "secret"})
    assert "secret" not in json.dumps(output) and "redactedMediaUrlSha256" in json.dumps(output)


def test_tool_probe_uses_same_nested_schema_and_actual_reasoning_continuity(tmp_path, monkeypatch, config):
    tool_call = {
        "type": "function_call",
        "id": "fc_echo",
        "call_id": "echo",
        "name": "pilot_echo",
        "arguments": '{"value":"READY","selectors":[{"id":"section-1"}]}',
        "status": "completed",
    }
    first = response(reasoning(), tool_call)
    actual, requests = fake_client(first, response(message("READY")), first, response(message("READY")))
    monkeypatch.setattr(p, "actual_client", lambda endpoint: actual)
    monkeypatch.setenv("AOAI_ENDPOINT", "https://test.invalid")
    receipts = {role: deployment(config, role) for role in p.ROLES}
    result = p.probe(config, receipts, tmp_path / "probe")
    assert result["logicalCalls"] == 4 and result["providerMeter"]["physicalResponsesAttempts"] == 4
    assert all(r["reasoningItems"] == 1 for r in result["records"])
    assert all(r["tools"] == requests[0]["tools"] for r in requests)
    assert first["output"][0] in requests[1]["input"] and first["output"][0] in requests[3]["input"]
    assert result["toolSchemaSha256"] == p.digest(p.PROBE_TOOLS)
    p.probe_gate(tmp_path / "probe/receipt.json", result["modelBindings"], config)
    (tmp_path / "probe/provider/request-00001.json").write_text("{}")
    with pytest.raises(ValueError, match="probe evidence changed"):
        p.probe_gate(tmp_path / "probe/receipt.json", result["modelBindings"], config)


def test_meter_and_replay_usage_reconciled(tmp_path, config):
    member = p.schedule([{"case": "q"}])[0]
    meter = Meter(tmp_path / "provider", p.LIMITS)
    actual, _ = fake_client(response(message()))
    meter.client(actual, member, config["models"]["sol"]["deployment"])([], [])
    path = tmp_path / member["run"] / "results.json"
    path.parent.mkdir()
    p.save(path, [{"metrics": {"modelCalls": [{"promptTokens": 100, "completionTokens": 50}]}}])
    p.verify_provider(tmp_path, {"schedule": [member]}, config)
    changed = p.read(path)
    changed[0]["metrics"]["modelCalls"][0]["promptTokens"] = 99
    p.save(path, changed)
    with pytest.raises(ValueError, match="token meters disagree"):
        p.verify_provider(tmp_path, {"schedule": [member]}, config)


def test_interaction_is_paired_and_unknown_ru_is_not_a_complete_total():
    rows = []
    for i in range(48):
        for role, arm in p.CELLS:
            passed = role == "sol" or arm == "hybrid"
            rows.append(
                {
                    "case": str(i),
                    "modelRole": role,
                    "arm": arm,
                    "families": ["facts"],
                    "answerable": True,
                    **{key: passed for key in p.QUALITY},
                    "requiredRegionRecall": float(passed),
                    "requiredFactCoverage": float(passed),
                    "usage": {"retrievalRequestCharge": 5, "retrievalChargeComplete": False},
                    "sourceActivity": dict.fromkeys(
                        (
                            "explicitEvidenceSearchReplies",
                            "additionalEvidenceSearchReplies",
                            "automaticDocumentPassageReplies",
                            "remainingContextWithheldReplies",
                        ),
                        0,
                    ),
                }
            )
    result = p.paired(rows)
    assert result["retrievalModelInteraction"]["taskCompletion"]["meanBudgetMinusSolHybridGain"] == 1
    ru = result["usageByCell"]["sol-vector"]["retrievalRequestCharge"]
    assert ru["completeTotal"] is None and ru["knownSubtotal"] == 240
    rows[0]["taskCompletion"] = None
    assert p.paired(rows)["retrievalModelInteraction"]["taskCompletion"]["applicableQuestions"] == 47


def test_source_gate_binds_originals_chunks_and_evidence(tmp_path):
    source = {"library": {}, "documents": [], "tools": [], "chunks": [{"id": "chunk"}]}
    source_path = tmp_path / "source.json"
    p.save(source_path, source)
    receipt = {
        "schema": "fli.hybrid-model-pilot.preparation.v1",
        "status": "accepted",
        "capturedAt": datetime.now(UTC).isoformat(),
        "manifestSha256": "frozen",
        "sourceStrategy": "microsoft-indexer-layout-v1",
        "sourceSnapshotPath": str(source_path),
        "sourceSnapshotFileSha256": p.sha(source_path),
        "sourceSnapshotSha256": p.digest(source),
        "embeddingDeploymentBinding": {"pinned": True},
        "sourceOriginalsVerified": True,
        "allSourcesCommittedAndObservedAttemptsTerminal": True,
        "hybridWhereOnlyRanksMeaningfully": True,
        "protectedScopesUnchanged": True,
        "evidenceReferences": [{"path": str(source_path), "sha256": p.sha(source_path)}],
    }
    path = tmp_path / "prep.json"
    p.save(path, receipt)
    baseline = {"libraries": {"lib": {k: v for k, v in source.items() if k != "chunks"}}}
    runtimes = {role: {"storedRowsSha256": p.digest(source["chunks"])} for role in p.ROLES}
    plan = {"manifestSha256": "frozen", "sourceStrategy": "microsoft-indexer-layout-v1"}
    p.preparation_gate(path, baseline, runtimes, {"pinned": True}, plan)
    runtimes["sol"]["storedRowsSha256"] = "changed"
    with pytest.raises(ValueError, match="stored rows differ"):
        p.preparation_gate(path, baseline, runtimes, {"pinned": True}, plan)


def test_coordinator_authorization_and_ledger_root_are_required(tmp_path):
    template = {
        "status": "requires_coordinator_review",
        "outputDirectory": str(tmp_path / "run"),
        "planSha256": "frozen",
    }
    authorization = {
        **template,
        "status": "accepted",
        "reviewedAt": datetime.now(UTC).isoformat(),
        "executionLedger": str(tmp_path / "claim.json"),
        **dict.fromkeys(
            (
                "withinExistingResourceAuthorization",
                "exclusiveMeasurementWindow",
                "consumedPackAcknowledged",
                "noRepresentationChanges",
            ),
            True,
        ),
    }
    assert p.authorize(authorization, template) == tmp_path / "claim.json"
    authorization["executionLedger"] = str(tmp_path / "run/claim.json")
    with pytest.raises(ValueError, match="outside run"):
        p.authorize(authorization, template)
