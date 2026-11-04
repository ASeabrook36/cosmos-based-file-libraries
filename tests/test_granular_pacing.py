"""Payload-aware qualification pacing; all provider/client operations are fake."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError
from openai.types.responses import Response

from scripts import granular_live_models as old
from scripts import granular_pacing as pacing
from scripts.hybrid_model_meter import Meter
from tests.test_granular_live_models import Clock
from tests.test_responses_chat import assistant, call, message, reasoning, response


def capacity():
    return {
        "id": "private-resource-id-never-copied",
        "sku": {"name": "GlobalStandard", "capacity": 50},
        "properties": {
            "model": dict(pacing.MODEL),
            "versionUpgradeOption": "NoAutoUpgrade",
            "rateLimits": [
                {"key": "token", "count": 50000, "renewalPeriod": 60},
                {"key": "request", "count": 50, "renewalPeriod": 60},
            ],
        },
    }


def policy():
    return pacing.policy_from_capacity(capacity(), binding_sha256="a" * 64)


def make_pacer(deadline=1000):
    clock = Clock()
    return pacing.Pacer(deadline, policy=policy(), clock=clock, sleep=clock.sleep), clock


def request(items=None, tools=None):
    return {
        "model": "pinned-deployment",
        "reasoning": {"effort": "medium"},
        "max_output_tokens": 8000,
        "store": False,
        "truncation": "disabled",
        "include": ["reasoning.encrypted_content"],
        "input": items if items is not None else [{"role": "user", "content": "Use the authored outline."}],
        "tools": tools if tools is not None else [],
    }


def measured(ip=3000, op=500, reason=300):
    result = response(reasoning(), call())
    result["usage"] = {
        "input_tokens": ip,
        "output_tokens": op,
        "total_tokens": ip + op,
        "input_tokens_details": {"cached_tokens": 100},
        "output_tokens_details": {"reasoning_tokens": reason},
    }
    return result


def receive(pacer, req, result):
    estimate = pacer.estimate(req)
    row = pacer.reserve(estimate)
    pacer.observe(req, result, row)
    return row


def test_capacity_uses_explicit_rate_limits_not_capacity_units_and_excludes_identifiers():
    arm = capacity()
    arm["sku"]["capacity"] = 1
    p = pacing.policy_from_capacity(arm, binding_sha256="a" * 64)
    assert p["localInputPlusMaxOutputCeiling"] == 45000
    assert p["localRequestsPerWindowCeiling"] == 45
    assert "private-resource-id" not in json.dumps(p)
    assert p["outputReservation"] == 8000 and p["harnessContextAllowance"] == 200000
    assert pacing.validate_capacity(p, arm, binding_sha256="a" * 64)
    arm["properties"]["rateLimits"][0]["count"] = 40000
    lower = pacing.policy_from_capacity(arm, binding_sha256="a" * 64)
    assert lower["localInputPlusMaxOutputCeiling"] == 36000
    with pytest.raises(ValueError, match="fresh deployment"):
        pacing.validate_capacity(p, arm, binding_sha256="a" * 64)


@pytest.mark.parametrize("field", ["rateLimits", "model", "versionUpgradeOption"])
def test_missing_arm_fields_fail_closed(field):
    arm = capacity()
    del arm["properties"][field]
    with pytest.raises(ValueError):
        pacing.policy_from_capacity(arm, binding_sha256="a" * 64)


@pytest.mark.parametrize("bad", [0, -1, True, "50000"])
def test_bad_explicit_rates_are_not_coerced(bad):
    arm = capacity()
    arm["properties"]["rateLimits"][0]["count"] = bad
    with pytest.raises(ValueError):
        pacing.policy_from_capacity(arm, binding_sha256="a" * 64)


@pytest.mark.parametrize(
    "key",
    [
        "outputReservation",
        "uncertaintyReserveMinimum",
        "uncertaintyReservePercent",
        "localInputPlusMaxOutputCeiling",
        "estimator",
    ],
)
def test_policy_edit_is_rejected(key):
    p = policy()
    p[key] = "changed"
    with pytest.raises(ValueError, match="reviewed policy"):
        pacing.validate_policy(p)


def test_initial_estimate_contains_all_semantic_input_tools_and_operational_margin():
    pacer, _ = make_pacer()
    req = request(tools=[{"type": "function", "name": "outline", "parameters": {}}])
    original = copy.deepcopy(req)
    estimate = pacer.estimate(req)
    assert estimate["semanticJsonTokensLocal"] == estimate["rawJsonInputAndToolsTokensLocal"]
    assert estimate["measuredPrefixAnchorEstimate"] is None
    assert estimate["boundReasoningReservation"] == 0
    assert estimate["uncertaintyReserve"] == 1024
    assert estimate["totalReservation"] == estimate["inputEstimate"] + 8000
    assert req == original


def test_exact_registered_opaque_output_preserved_but_ciphertext_length_not_input_tokens():
    pacer, _ = make_pacer()
    first = request()
    result = measured()
    result["output"][0]["encrypted_content"] = "randomCipher_" * 3000
    receive(pacer, first, result)
    tool_result = {"type": "function_call_output", "call_id": "c1", "output": "Authored outline source."}
    second = request(first["input"] + result["output"] + [tool_result])
    original = copy.deepcopy(second)
    estimate = pacer.estimate(second)
    assert estimate["knownEncryptedItems"] == 1
    assert estimate["boundReasoningReservation"] == 300
    assert estimate["encryptedFieldRemovalDeltaLocal"] > 3000
    assert (
        estimate["measuredPrefixAnchorEstimate"] == 3500 + estimate["addedContentAfterExactPreviousOutputTokensLocal"]
    )
    assert estimate["baseInputEstimate"] >= estimate["measuredPrefixAnchorEstimate"]
    assert second == original
    changed = copy.deepcopy(second)
    changed["input"][1]["encrypted_content"] += "x"
    with pytest.raises(ValueError, match="unknown or mutated"):
        pacer.estimate(changed)


@pytest.mark.parametrize("change", ["id", "summary", "encrypted_content"])
def test_full_reasoning_item_identity_not_just_ciphertext_is_bound(change):
    pacer, _ = make_pacer()
    first, result = request(), measured()
    receive(pacer, first, result)
    item = copy.deepcopy(result["output"][0])
    item[change] = "mutated"
    with pytest.raises(ValueError, match="unknown or mutated"):
        pacer.estimate(request([item]))


def test_unregistered_nested_and_native_media_inputs_fail_closed():
    pacer, _ = make_pacer()
    for item in [
        reasoning(),
        {"role": "user", "content": {"encrypted_content": "unknown"}},
        {"role": "user", "content": [{"type": "input_image", "image_url": "opaque"}]},
    ]:
        with pytest.raises(ValueError):
            pacer.estimate(request([item]))


def test_missing_reasoning_usage_reserves_entire_reported_output():
    pacer, _ = make_pacer()
    first, result = request(), measured()
    del result["usage"]["output_tokens_details"]
    receive(pacer, first, result)
    estimate = pacer.estimate(request(first["input"] + result["output"]))
    assert estimate["boundReasoningReservation"] == 500
    assert estimate["missingReasoningCounterItems"] == 1


def test_real_sdk_response_is_normalized_and_exact_output_accepted():
    pacer, _ = make_pacer()
    first = request()
    sdk = Response.model_construct(**measured())
    receive(pacer, first, sdk)
    exact = sdk.model_dump(exclude_none=True)
    estimate = pacer.estimate(request(first["input"] + exact["output"]))
    assert estimate["knownEncryptedItems"] == 1
    assert estimate["boundReasoningReservation"] == 300


def test_measured_anchor_invalidated_by_tools_or_prefix_changes_without_releasing_reasoning():
    pacer, _ = make_pacer()
    first, result = request(), measured()
    receive(pacer, first, result)
    second = request(first["input"] + result["output"], tools=[{"type": "function", "name": "different"}])
    assert pacer.estimate(second)["measuredPrefixAnchorEstimate"] is None
    second = request([{"role": "user", "content": "new"}] + result["output"])
    estimate = pacer.estimate(second)
    assert estimate["measuredPrefixAnchorEstimate"] is None
    assert estimate["boundReasoningReservation"] == 300


def test_observed_input_can_raise_but_never_credit_pacing_cached_or_short_output():
    pacer, _ = make_pacer()
    req = request()
    row = pacer.reserve(pacer.estimate(req))
    initial = row["reserved"]
    pacer.observe(req, measured(ip=30000), row)
    assert row["reserved"] == 38000 > initial
    pacer.observe(req, measured(ip=100), row)
    assert row["reserved"] == 38000


def test_reservations_wait_for_window_and_space_requests_even_after_success():
    pacer, clock = make_pacer()
    first = pacer.reserve({"inputEstimate": 20000, "totalReservation": 28000})
    second = pacer.reserve({"inputEstimate": 20000, "totalReservation": 28000})
    assert second["at"] - first["at"] == 60
    assert second["waitSeconds"] == 60 and max(clock.sleeps) <= 30
    pacer2, clock2 = make_pacer()
    a = pacer2.reserve({"inputEstimate": 1, "totalReservation": 8001})
    b = pacer2.reserve({"inputEstimate": 1, "totalReservation": 8001})
    assert b["at"] - a["at"] == pytest.approx(60 / 45)
    assert clock2.now > 0


def test_unknown_failure_occupies_entire_window_and_cannot_be_credited_as_zero():
    pacer, clock = make_pacer()
    row = pacer.reserve({"inputEstimate": 1, "totalReservation": 8001})
    clock.now = 5
    pacer.failed(row)
    assert row["reserved"] == 45000
    next_row = pacer.reserve({"inputEstimate": 1, "totalReservation": 8001})
    assert next_row["at"] == 65


def test_one_request_too_large_refuses_and_wait_deadline_still_enforced():
    pacer, clock = make_pacer(deadline=10)
    with pytest.raises(pacing.PacingRefusal):
        pacer.reserve({"inputEstimate": 37001, "totalReservation": 45001})
    assert not pacer.rows and clock.now == 0
    pacer.reserve({"inputEstimate": 30000, "totalReservation": 38000})
    with pytest.raises(TimeoutError):
        pacer.reserve({"inputEstimate": 30000, "totalReservation": 38000})


def setup_chat(tmp_path, create, *, input_cap=1000000):
    meter = Meter(
        tmp_path / "provider",
        {
            "logicalCalls": 13,
            "physicalResponsesAttempts": 18,
            "inputTokensWithUnknownReservations": input_cap,
            "outputTokensWithUnknownReservations": 100000,
        },
        context=200000,
        output=8000,
    )
    directory = tmp_path / "turn"
    directory.mkdir()
    pacer, clock = make_pacer()
    calls = []
    client = SimpleNamespace(max_retries=0, responses=SimpleNamespace(create=create))
    chat = pacing.chat(client, "pinned-deployment", meter, {"run": "one", "case": "outline"}, pacer, directory, calls)
    return chat, meter, pacer, clock, directory, calls


def test_chat_preserves_wire_reasoning_and_records_decision_before_dispatch(tmp_path):
    requests = []
    answers = iter([measured(), response(message("A supported answer"))])

    def create(**kwargs):
        n = len(requests) + 1
        assert (tmp_path / "turn" / f"pacing-plan-{n:02}.json").exists()
        assert (tmp_path / "turn" / f"pacing-reserved-{n:02}.json").exists()
        assert (tmp_path / "provider" / f"request-{n:05}.json").exists()
        requests.append(copy.deepcopy(kwargs))
        return next(answers)

    chat, meter, _, _, directory, calls = setup_chat(tmp_path, create)
    messages = [{"role": "user", "content": "Read the outline"}]
    reply = chat(messages, [])
    messages += [assistant(reply), {"role": "tool", "tool_call_id": "c1", "content": "Source outline."}]
    final = chat(messages, [])
    assert final.content == "A supported answer"
    assert requests[1]["input"][1:3] == measured()["output"]
    assert all(r["include"] == ["reasoning.encrypted_content"] for r in requests)
    assert meter.state["physicalResponsesAttempts"] == 2
    assert all(c["status"] == "returned" for c in calls)
    assert json.loads((directory / "pacing-plan-02.json").read_text())["estimate"]["knownEncryptedItems"] == 1
    assert old.Pacer is not pacing.Pacer and old.TOKEN_RATE == 45000


def test_unsent_refusal_is_durable_without_spurious_physical_attempt(tmp_path):
    called = []
    chat, meter, _, _, directory, calls = setup_chat(tmp_path, lambda **kw: called.append(kw))
    with pytest.raises(pacing.PacingRefusal):
        chat([{"role": "user", "content": "too many tokens " * 15000}], [])
    assert not called and meter.state["physicalResponsesAttempts"] == 0
    assert meter.state["logicalCallsStarted"] == 1 and calls[0]["status"] == "failed"
    refusal = json.loads((directory / "pacing-refusal-01.json").read_text())
    assert refusal["providerDispatched"] is False
    assert refusal["estimate"]["totalReservation"] > 45000
    assert (directory / "pacing-request-01.json").exists()


def test_429_retry_retains_unknown_meter_and_waits_full_pacing_window(tmp_path):
    attempts = []
    error = RateLimitError(
        "bounded synthetic rate error",
        response=httpx.Response(
            429, headers={"retry-after-ms": "100"}, request=httpx.Request("POST", "https://example.invalid")
        ),
        body=None,
    )

    def create(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise error
        return response(message())

    chat, meter, _, clock, directory, _ = setup_chat(tmp_path, create)
    assert chat([{"role": "user", "content": "Read"}], []).content == "Answer"
    assert len(attempts) == meter.state["physicalResponsesAttempts"] == 2
    assert meter.state["unmeteredAttempts"] == 1 and meter.state["unmeteredInputReservations"] == 200000
    assert clock.now >= 60
    failed = json.loads((directory / "pacing-observed-01.json").read_text())
    assert failed["reservationRetained"]["reserved"] == 45000


def test_cumulative_meter_reservation_still_stops_dispatch(tmp_path):
    calls = []
    chat, meter, _, _, directory, _ = setup_chat(tmp_path, lambda **kw: calls.append(kw), input_cap=199999)
    with pytest.raises(ValueError, match="input token reservation ceiling"):
        chat([{"role": "user", "content": "small"}], [])
    assert not calls and meter.state["physicalResponsesAttempts"] == 0
    assert (directory / "pacing-reserved-01.json").exists()
    admission = json.loads((directory / "pacing-admission-refusal-01.json").read_text())
    assert admission["providerDispatched"] is False


@pytest.mark.parametrize("ip", [None, 200001])
def test_response_usage_failure_keeps_raw_receipt_and_conservative_reservation(tmp_path, ip):
    result = measured(ip=10)
    result["usage"]["input_tokens"] = ip
    chat, meter, pacer, _, directory, _ = setup_chat(tmp_path, lambda **kw: result)
    with pytest.raises(ValueError):
        chat([{"role": "user", "content": "Read"}], [])
    assert meter.state["physicalResponsesAttempts"] == 1
    assert (tmp_path / "provider" / "response-00001.json").exists()
    assert pacer.rows[0]["reserved"] >= 45000
    receipt = json.loads((directory / "pacing-observed-01.json").read_text())
    assert receipt["status"] == "response_rejected"
    assert receipt["providerInputTokens"] == ip
    assert receipt["reservationRetained"]["reserved"] == (208001 if ip else 45000)


def test_deadline_expiring_at_admission_is_not_an_unknown_physical_attempt(tmp_path):
    dispatched = []
    chat, meter, pacer, _, directory, _ = setup_chat(tmp_path, lambda **kw: dispatched.append(kw))
    checks = []

    def remaining():
        checks.append(None)
        if len(checks) == 3:
            raise TimeoutError("synthetic deadline at admission")
        return 10

    pacer.remaining = remaining
    with pytest.raises(TimeoutError):
        chat([{"role": "user", "content": "small"}], [])
    assert not dispatched and meter.state["physicalResponsesAttempts"] == 0
    assert meter.state["unmeteredAttempts"] == 0
    assert (directory / "pacing-admission-refusal-01.json").exists()


def test_refused_media_capture_contains_hash_not_native_attachment_credential(tmp_path):
    dispatched = []
    chat, meter, _, _, directory, _ = setup_chat(tmp_path, lambda **kw: dispatched.append(kw))
    messages = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "https://example.invalid/image?secret=credential"}}],
        }
    ]
    with pytest.raises(ValueError, match="native media"):
        chat(messages, [])
    assert not dispatched and meter.state["physicalResponsesAttempts"] == 0
    saved = (directory / "pacing-request-01.json").read_text()
    assert "credential" not in saved and "redactedMediaUrlSha256" in saved
    assert (directory / "pacing-refusal-01.json").exists()
