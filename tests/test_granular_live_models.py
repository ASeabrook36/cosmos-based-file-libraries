"""Bounded live-model coordinator contracts using fake transport and local API."""

import copy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError

from scripts import granular_live_models as models
from scripts.hybrid_model_meter import Meter, meter_summary
from scripts.qualify_granular_navigation import fixtures
from tests.test_granular_navigation import ready
from tests.test_modular_ingestion import upload
from tests.test_responses_chat import client_for, message, response
from tests.test_worker_handlers import drain


def binding():
    return {
        "model": {"format": "OpenAI", "name": "gpt-5-mini", "version": "2025-08-07"},
        "sku": {"name": "GlobalStandard", "capacity": 50},
        "bindingSha256": "a" * 64,
    }


def source_bindings():
    return {
        role: {
            "libraryId": "owned",
            "documentId": role,
            "generation": 1,
            "contentRevision": 2,
            "fileName": "source." + extension,
            "sourceSha256": hashlib.sha256(role.encode()).hexdigest(),
        }
        for role, extension in (("docx", "docx"), ("markdown", "md"))
    }


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_prepare_seals_six_cases_with_hidden_expectations_and_source_pins(tmp_path):
    root = tmp_path / "models"
    result = models.prepare_cases(root, source_bindings())
    controls = models._verify(root)
    assert result["cases"] == 6 and len({row["case"] for row in controls["cases"]}) == 6
    assert controls["contextAllowance"] == 200000 and controls["outputAllowance"] == 8000
    assert controls["pacing"]["serializedInputPerRequestCeiling"] == 37000
    for row in controls["cases"]:
        assert "document selector" in row["question"] and "contentRevision" in row["question"]
        assert row["expectedSourceFacts"] and row["expectedNavigation"]
        assert all(fact not in row["question"] for fact in row["expectedSourceFacts"])
    with pytest.raises(ValueError, match="new private"):
        models.prepare_cases(root, source_bindings())


@pytest.mark.parametrize("field", ["cases", "codeSha256", "contextAllowance", "searchMode", "pacing"])
def test_changed_control_or_code_cannot_start_a_model_run(tmp_path, field):
    root = tmp_path / "models"
    models.prepare_cases(root, source_bindings())
    controls = json.loads((root / "controls.json").read_text())
    controls[field] = None
    (root / "controls.json").write_text(json.dumps(controls))
    with pytest.raises(ValueError):
        models._verify(root)


def test_pacing_waits_on_rolling_reservations_and_deadline_without_cloud_calls():
    clock = Clock()
    pacer = models.Pacer(200, clock=clock, sleep=clock.sleep)
    first = pacer.reserve(20000)
    assert first["reserved"] == 28000 and not clock.sleeps
    pacer.reserve(15000)
    assert clock.now == 60 and max(clock.sleeps) <= 30
    with pytest.raises(models.PacingRefusal):
        pacer.reserve(37001)
    short = models.Pacer(clock.now + 1, clock=clock, sleep=clock.sleep)
    with pytest.raises(TimeoutError):
        short.pause(20)
    assert clock.now == 61


def test_loopback_http_deadline_clamps_each_request_and_rejects_late_response():
    clock, calls = Clock(), []
    pacer = models.Pacer(20, clock=clock, sleep=clock.sleep)

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        clock.now += 3
        return {"source": "private evidence"}

    original = SimpleNamespace(request=request, timeout=120, headers={"test": "unchanged"})
    bounded = models.DeadlineHTTP(original, pacer)
    assert bounded.request("GET", "/source") == {"source": "private evidence"}
    assert calls[0][2]["timeout"] == 5 and original.timeout == 120
    assert bounded.headers is original.headers
    bounded.request("POST", "/read", timeout=120)
    assert calls[1][2]["timeout"] == 17 / 4
    clock.now = 19
    with pytest.raises(TimeoutError, match="deadline"):
        bounded.request("GET", "/late")
    assert calls[-1][2]["timeout"] == 0.25
    with pytest.raises(TimeoutError, match="deadline"):
        bounded.request("GET", "/never-started")
    assert len(calls) == 3


def test_local_token_components_are_separate_from_provider_counters():
    payload = {
        "documents": [
            {
                "fileName": "source.docx",
                "sections": [
                    {
                        "text": "Only while cold.",
                        "textSource": "extracted_source_text",
                        "readSelector": {"mode": "selected", "documents": [{"documentId": "d", "nodeIds": ["p1"]}]},
                    }
                ],
            }
        ],
        "selectorGuidance": "Copy returned selectors.",
    }
    result = models.tool_metrics({"role": "tool", "tool_call_id": "call", "content": json.dumps(payload)})
    assert result["standaloneSourceTextTokensLocal"] > 0
    assert result["selectorSerializationDeltaTokensLocal"] > 0
    assert result["toolMessageJsonTokensLocal"] > result["evidenceJsonTokensLocal"]
    assert "not additive" in result["interpretation"]
    assert not any(key in result for key in ("input_tokens", "requestCharge", "cost"))


def test_attempt_meter_keeps_unknown_retry_reservations_and_real_response_usage(tmp_path):
    error = RateLimitError(
        "Do not serialize this private body",
        response=httpx.Response(
            429, headers={"retry-after": "0"}, request=httpx.Request("POST", "https://test.invalid")
        ),
        body={},
    )
    actual, requests = client_for(error, response(message("Finished")))
    actual.max_retries = 0
    meter = Meter(tmp_path / "provider", models.METER_LIMITS, context=200000, output=8000)
    clock, calls = Clock(), []
    directory = tmp_path / "case"
    directory.mkdir()
    chat = models._chat(
        actual,
        "chosen",
        meter,
        {"run": "case", "cell": "qualification"},
        models.Pacer(200, clock=clock, sleep=clock.sleep),
        directory,
        calls,
    )
    answer = chat([{"role": "user", "content": "Task"}], [])
    assert answer.content == "Finished" and len(requests) == 2
    assert all(0 < request["timeout"] <= 60 for request in requests)
    summary = meter_summary(meter.path)
    assert summary["knownInputTokens"] == 100 and summary["knownOutputTokens"] == 50
    assert summary["unmeteredInputReservations"] == 200000 and summary["unmeteredOutputReservations"] == 8000
    assert summary["completeInputTokens"] is None and summary["http429Attempts"] == 1
    assert "private body" not in meter.path.read_text()


def test_failed_provider_usage_does_not_become_zero_or_allow_unbounded_retry(tmp_path):
    actual, _ = client_for(response(message("Partial"), usage=None))
    actual.max_retries = 0
    meter = Meter(tmp_path / "provider", models.METER_LIMITS, context=200000, output=8000)
    directory = tmp_path / "case"
    directory.mkdir()
    clock = Clock()
    chat = models._chat(
        actual,
        "chosen",
        meter,
        {"run": "case", "cell": "qualification"},
        models.Pacer(200, clock=clock, sleep=clock.sleep),
        directory,
        [],
    )
    with pytest.raises(ValueError, match="meters unavailable"):
        chat([{"role": "user", "content": "Task"}], [])
    summary = meter_summary(meter.path)
    assert summary["physicalResponsesAttempts"] == 1
    assert summary["unmeteredAttempts"] == 1 and summary["completeInputTokens"] is None


def test_model_delivery_indicators_exclude_locally_generated_unsubmitted_tool_text():
    case = models.cases(source_bindings())[0]
    submitted = {"role": "tool", "tool_call_id": "sent", "content": json.dumps({"text": "Other source"})}
    unsent = {"role": "tool", "tool_call_id": "unsent", "content": json.dumps({"text": case["expectedSourceFacts"][0]})}
    turn = SimpleNamespace(answer="source.docx", total_ms=10, searches=[], messages=[submitted, unsent])
    summary = models._turn_summary(case, turn, [{"status": "returned", "messages": [submitted]}])
    assert summary["localToolReplies"] == 2 and summary["toolRepliesInSuccessfulModelCalls"] == 1
    assert not summary["factDeliveryIndicators"][0]["delivered"]
    assert all(value is None for value in summary["manualReview"].values())


async def test_six_turn_pipeline_uses_normal_agent_api_captures_and_no_runtime_context_repair(tmp_path, monkeypatch):
    fixture = fixtures()
    env = await ready(source=fixture["source.docx"], name="source.docx")
    markdown_id = upload(env.client, env.lib, fixture["source.md"], "source.md")
    await drain(env.s, env.worker)
    markdown = await env.s.documents.get(env.lib, markdown_id)
    bindings = {
        role: {
            "libraryId": env.lib,
            "documentId": doc["id"],
            "generation": doc["activeGeneration"],
            "contentRevision": doc["contentRevision"],
            "fileName": doc["fileName"],
            "sourceSha256": doc["sha256"],
        }
        for role, doc in (("docx", env.doc), ("markdown", markdown))
    }
    root = tmp_path / "models"
    models.prepare_cases(root, bindings)
    clock, original_pacer = Clock(), models.Pacer
    monkeypatch.setattr(models, "Pacer", lambda deadline: original_pacer(deadline, clock=clock, sleep=clock.sleep))
    requests = []

    def create(**request):
        requests.append(copy.deepcopy(request))
        user = next(item for item in request["input"] if item.get("role") == "user")
        question = (
            user["content"]
            if isinstance(user["content"], str)
            else "".join(part.get("text", "") for part in user["content"])
        )
        source = bindings["markdown" if "source.md" in question else "docx"]
        if not any(item.get("type") == "function_call_output" for item in request["input"]):
            name = next(tool["name"] for tool in request["tools"] if tool["name"].startswith("read_documents_library_"))
            pin = {key: source[key] for key in ("documentId", "generation", "contentRevision")}
            return response(
                {
                    "type": "function_call",
                    "id": "fc",
                    "call_id": "full",
                    "name": name,
                    "arguments": json.dumps({"mode": "full", "documents": [pin]}),
                    "status": "completed",
                }
            )
        return response(message("The source text was read completely. (" + source["fileName"] + ")"))

    actual = SimpleNamespace(max_retries=0, responses=SimpleNamespace(create=create))
    result = models.run_models(
        root,
        index=env.index,
        agent_id="reader",
        actual_client=actual,
        deployment="chosen",
        model_binding=binding(),
        deadline=10000,
        api_controls={"searchMode": "vector", "contextAccounting": "legacy-v1"},
    )
    assert result["status"] == "complete_pending_source_review" and result["completedTurns"] == 6
    assert len(requests) == 12 and result["provider"]["knownInputTokens"] == 1200
    assert result["sourceSnapshotsUnchanged"] and (root / "sources-after.json").exists()
    for case in result["cases"]:
        assert case["toolRepliesInSuccessfulModelCalls"] == 1
        assert all(fact["delivered"] for fact in case["factDeliveryIndicators"])
        assert case["toolReplyLocalMeasurements"] and case["citationFilenameMentioned"]
        assert (root / case["case"] / "model-input-02.json").exists()
        assert (root / case["case"] / "tool-result-02.json").exists()
        assert case["manualReview"]["navigationRequirementsMet"] is None
    with pytest.raises(FileExistsError):
        models.run_models(
            root,
            index=env.index,
            agent_id="reader",
            actual_client=actual,
            deployment="chosen",
            model_binding=binding(),
            deadline=10000,
            api_controls={"searchMode": "vector", "contextAccounting": "legacy-v1"},
        )
