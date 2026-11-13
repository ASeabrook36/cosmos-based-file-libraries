"""Qualification-only payload-aware pacing for the explicit outline-v2 protocol.

This changes neither runtime context accounting nor the Responses wire payload.
The estimates are conservative scheduling heuristics, not provider token counts
or an assertion about Azure's precise distributed rate-limit implementation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

from fli.chunking import default_tokenizer
from fli_demo.responses import _plain, azure_responses_chat
from scripts.evaluate_agent_retrieval import capture
from scripts.granular_live_models import Pacer as LegacyPacer
from scripts.granular_live_models import PacingRefusal
from scripts.hybrid_model_meter import scrub_media
from scripts.qualify_granular_navigation import save_new

MODEL = {"format": "OpenAI", "name": "gpt-5-mini", "version": "2025-08-07"}
CONTEXT, OUTPUT = 200000, 8000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _count(value, tokenizer):
    # Match the earlier serialization diagnostic: order preserved, compact JSON.
    return len(tokenizer.encode(json.dumps(value, ensure_ascii=False, separators=(",", ":"))))


def _capacity(arm_record):
    require(isinstance(arm_record, dict), "capacity record must be an object")
    properties = arm_record.get("properties") or {}
    model, sku = properties.get("model"), arm_record.get("sku")
    require(model == MODEL, "pacing is qualified only for the pinned answering model")
    require(
        isinstance(sku, dict)
        and sku.get("name") == "GlobalStandard"
        and type(sku.get("capacity")) is int
        and sku["capacity"] > 0,
        "answering deployment capacity binding unavailable",
    )
    rates = properties.get("rateLimits")
    require(isinstance(rates, list) and len(rates) == 2, "explicit ARM token/request rate limits required")
    normalized = []
    for item in rates:
        require(
            isinstance(item, dict)
            and item.get("key") in {"token", "request"}
            and type(item.get("count")) is int
            and item["count"] > 0
            and type(item.get("renewalPeriod")) is int
            and item["renewalPeriod"] == 60,
            "unsupported or unavailable ARM rate limit",
        )
        normalized.append({key: item[key] for key in ("key", "count", "renewalPeriod")})
    require({item["key"] for item in normalized} == {"token", "request"}, "duplicate ARM rate limit")
    require(properties.get("versionUpgradeOption") == "NoAutoUpgrade", "answering upgrade policy changed")
    return {
        "model": copy.deepcopy(model),
        "sku": {"name": sku["name"], "capacity": sku["capacity"]},
        "versionUpgradeOption": properties["versionUpgradeOption"],
        "rateLimits": sorted(normalized, key=lambda item: item["key"]),
    }


def policy_from_capacity(arm_record, *, binding_sha256):
    """Create public-safe controls from an independently read private ARM record."""
    require(isinstance(binding_sha256, str) and re.fullmatch(r"[a-f0-9]{64}", binding_sha256), "invalid binding hash")
    capacity = _capacity(arm_record)
    rates = {row["key"]: row["count"] for row in capacity["rateLimits"]}
    token_ceiling, request_ceiling = min(45000, rates["token"] * 9 // 10), min(45, rates["request"] * 9 // 10)
    require(token_ceiling > OUTPUT and request_ceiling > 0, "deployment limits cannot support this qualification")
    return {
        "schema": "fli.granular-pacing.v1",
        "protocol": "outline-v2",
        "bindingSha256": binding_sha256,
        "capacityBinding": capacity,
        "capacityBindingSha256": _sha(capacity),
        "windowSeconds": 60,
        "localInputPlusMaxOutputCeiling": token_ceiling,
        "localRequestsPerWindowCeiling": request_ceiling,
        "minimumRequestSpacingSeconds": 60 / request_ceiling,
        "outputReservation": OUTPUT,
        "harnessContextAllowance": CONTEXT,
        "uncertaintyReserveMinimum": 1024,
        "uncertaintyReservePercent": 10,
        "estimator": "max(semantic_json_plus_bound_reasoning,exact_prefix_measured_anchor)_plus_uncertainty",
        "unknownEncryptedItemPolicy": "refuse",
        "missingReasoningCounterPolicy": "reserve_entire_measured_output",
        "observedReservationPolicy": "raise_only;retain_failed_attempt_reservations",
        "interpretation": "Local pacing estimates, not provider usage, billing, model-context accounting, "
        "or Azure's exact rate-limit formula. Full output reservation and uncertainty margin are deliberate "
        "operational headroom, not a statistically calibrated bound. Ciphertext is preserved on the wire.",
    }


def validate_policy(policy):
    require(isinstance(policy, dict), "pacing policy must be an object")
    capacity = policy.get("capacityBinding") or {}
    expected = policy_from_capacity(
        {"sku": capacity.get("sku"), "properties": capacity}, binding_sha256=policy.get("bindingSha256")
    )
    require(policy == expected, "pacing policy differs from the reviewed policy")
    return copy.deepcopy(policy)


def validate_capacity(policy, arm_record, *, binding_sha256):
    validate_policy(policy)
    require(
        policy == policy_from_capacity(arm_record, binding_sha256=binding_sha256),
        "fresh deployment binding/rate limits differ from frozen pacing policy",
    )
    return True


class Pacer(LegacyPacer):
    """Per-attempt pacing with verified opaque-item provenance and known usage anchors."""

    def __init__(self, deadline, *, policy, clock=time.monotonic, sleep=time.sleep):
        super().__init__(deadline, clock=clock, sleep=sleep)
        self.policy = validate_policy(policy)
        self.tokenizer = default_tokenizer()
        self.opaque = {}
        self.previous = None
        self.decisions = 0

    def estimate(self, request):
        require(
            isinstance(request.get("input"), list) and isinstance(request.get("tools"), list), "input/tools missing"
        )

        # Native-image estimation is not part of this DOCX text qualification.
        def unsupported(value):
            if isinstance(value, list):
                return any(unsupported(item) for item in value)
            if isinstance(value, dict):
                return value.get("type") in {"input_image", "input_file"} or any(unsupported(v) for v in value.values())
            return False

        require(not unsupported(request["input"]), "native media requires a separately reviewed pacing estimator")
        semantic = copy.deepcopy({"input": request["input"], "tools": request["tools"]})
        reasoning, opaque_items, fallback_items = 0, 0, 0
        for item in semantic["input"]:
            if not isinstance(item, dict) or "encrypted_content" not in item:
                continue
            require(item.get("type") == "reasoning", "unsupported opaque input item")
            receipt = self.opaque.get(_sha(item))
            require(receipt is not None, "unknown or mutated encrypted reasoning item")
            reasoning += receipt["reservation"]
            opaque_items += 1
            fallback_items += int(receipt["reasoningCounterUnavailable"])
            del item["encrypted_content"]

        def has_opaque(value):
            if isinstance(value, list):
                return any(has_opaque(item) for item in value)
            if isinstance(value, dict):
                return "encrypted_content" in value or any(has_opaque(item) for item in value.values())
            return False

        require(not has_opaque(semantic), "nested or unsupported opaque input item")
        raw = _count({"input": request["input"], "tools": request["tools"]}, self.tokenizer)
        local = _count(semantic, self.tokenizer)
        anchor = None
        added = None
        if self.previous:
            previous = self.previous
            old_input, output = previous["request"]["input"], previous["response"]["output"]
            if request["tools"] == previous["request"]["tools"] and request["input"][: len(old_input)] == old_input:
                suffix = request["input"][len(old_input) :]
                if suffix[: len(output)] == output:
                    added = _count(suffix[len(output) :], self.tokenizer)
                    anchor = previous["inputTokens"] + previous["outputTokens"] + added
        base = max(local + reasoning, anchor or 0)
        uncertainty = max(
            self.policy["uncertaintyReserveMinimum"], math.ceil(base * self.policy["uncertaintyReservePercent"] / 100)
        )
        return {
            "requestSha256": _sha(request),
            "tokenizer": self.tokenizer.name,
            "rawJsonInputAndToolsTokensLocal": raw,
            "semanticJsonTokensLocal": local,
            "encryptedFieldRemovalDeltaLocal": raw - local,
            "boundReasoningReservation": reasoning,
            "knownEncryptedItems": opaque_items,
            "missingReasoningCounterItems": fallback_items,
            "measuredPrefixAnchorEstimate": anchor,
            "addedContentAfterExactPreviousOutputTokensLocal": added,
            "baseInputEstimate": base,
            "uncertaintyReserve": uncertainty,
            "inputEstimate": base + uncertainty,
            "outputReservation": OUTPUT,
            "totalReservation": base + uncertainty + OUTPUT,
            "counterStatus": "local_estimate; provider_input_unavailable_until_response",
        }

    def reserve(self, estimate):
        amount = estimate["totalReservation"]
        require(
            type(estimate.get("inputEstimate")) is int
            and estimate["inputEstimate"] >= 0
            and type(amount) is int
            and amount == estimate["inputEstimate"] + OUTPUT,
            "invalid pacing reservation",
        )
        if amount > self.policy["localInputPlusMaxOutputCeiling"]:
            raise PacingRefusal("request estimate exceeds local pacing ceiling; waiting cannot make one request fit")
        if estimate["inputEstimate"] + OUTPUT > CONTEXT:
            raise PacingRefusal("estimated request exceeds separate harness context allowance")
        started = self.clock()
        while True:
            self.remaining()
            now = self.clock()
            self.rows = [row for row in self.rows if now - row["at"] < self.policy["windowSeconds"]]
            wait = 0
            if self.rows:
                wait = max(0, self.policy["minimumRequestSpacingSeconds"] - (now - self.rows[-1]["at"]))
                if (
                    sum(row["reserved"] for row in self.rows) + amount > self.policy["localInputPlusMaxOutputCeiling"]
                    or len(self.rows) >= self.policy["localRequestsPerWindowCeiling"]
                ):
                    wait = max(wait, self.policy["windowSeconds"] - (now - min(row["at"] for row in self.rows)))
            if wait <= 0:
                row = {
                    "at": now,
                    "reserved": amount,
                    "waitSeconds": now - started,
                    "inputEstimate": estimate["inputEstimate"],
                }
                self.rows.append(row)
                return row
            self.pause(max(0.001, wait))

    def observe(self, request, response, reservation):
        body = _plain(response)
        usage = body.get("usage") or {}
        ip, op = usage.get("input_tokens"), usage.get("output_tokens")
        require(
            type(ip) is int and ip >= 0 and type(op) is int and 0 <= op <= OUTPUT,
            "provider usage unavailable or invalid",
        )
        reservation["reserved"] = max(reservation["reserved"], ip + OUTPUT)
        reservation["observedInputTokens"] = ip
        reservation["inputEstimateError"] = reservation["inputEstimate"] - ip
        require(ip <= CONTEXT, "provider input exceeds harness context allowance")
        detail = usage.get("output_tokens_details") or {}
        reasoning = detail.get("reasoning_tokens")
        require(
            reasoning is None or (type(reasoning) is int and 0 <= reasoning <= op), "invalid reasoning token counter"
        )
        require(isinstance(body.get("output"), list), "provider output unavailable")
        for item in body["output"]:
            if isinstance(item, dict) and "encrypted_content" in item:
                require(item.get("type") == "reasoning", "unsupported opaque provider output")
                key = _sha(item)
                proposed = {
                    "reservation": op if reasoning is None else reasoning,
                    "reasoningCounterUnavailable": reasoning is None,
                }
                prior = self.opaque.get(key)
                if prior:
                    proposed["reservation"] = max(prior["reservation"], proposed["reservation"])
                    proposed["reasoningCounterUnavailable"] |= prior["reasoningCounterUnavailable"]
                self.opaque[key] = proposed
        self.previous = {"request": copy.deepcopy(request), "response": body, "inputTokens": ip, "outputTokens": op}
        return {key: reservation[key] for key in ("reserved", "observedInputTokens", "inputEstimateError")}

    def failed(self, reservation):
        """Unknown attempts consume the entire local window before another try.

        The independent Meter also retains its full 200k/8k cumulative unknown
        reservations. Neither a timeout nor a rejected attempt becomes zero cost.
        """
        reservation["reserved"] = max(reservation["reserved"], self.policy["localInputPlusMaxOutputCeiling"])
        reservation["at"] = self.clock()
        reservation["usageStatus"] = "unavailable;full_local_window_reserved"


def chat(actual, deployment, meter, member, pacer, directory, calls):
    """Same callback contract as the frozen helper; only outline-v2 selects it."""
    require(getattr(actual, "max_retries", None) == 0, "underlying OpenAI SDK retries must be zero")
    logical = None

    def create(**request):
        require(logical is not None, "unbound provider attempt")
        require(
            request.get("model") == deployment
            and request.get("reasoning") == {"effort": "medium"}
            and request.get("max_output_tokens") == OUTPUT
            and request.get("store") is False
            and request.get("truncation") == "disabled"
            and request.get("include") == ["reasoning.encrypted_content"],
            "actual model controls differ",
        )
        pacer.decisions += 1
        number = pacer.decisions
        save_new(directory / f"pacing-request-{number:02}.json", scrub_media(request))
        plan = {
            "logicalCall": logical["logicalCall"],
            "requestSha256": _sha(request),
            "providerDispatched": False,
            "at": datetime.now(UTC).isoformat(),
        }
        try:
            estimate = pacer.estimate(request)
            plan["estimate"] = estimate
            save_new(directory / f"pacing-plan-{number:02}.json", plan)
            reservation = pacer.reserve(estimate)
        except BaseException as error:
            save_new(directory / f"pacing-refusal-{number:02}.json", {**plan, "errorType": type(error).__name__})
            raise
        save_new(directory / f"pacing-reserved-{number:02}.json", {**plan, "reservation": reservation})
        try:
            # Resolve the deadline before recording a physical-attempt boundary;
            # a locally expired wait is not an unknown provider request.
            timeout = min(60.0, pacer.remaining())
            row = meter.begin_attempt(logical, request)
        except BaseException as error:
            save_new(
                directory / f"pacing-admission-refusal-{number:02}.json",
                {**plan, "errorType": type(error).__name__, "providerDispatched": False},
            )
            raise
        row["localPacingInputEstimate"] = estimate["inputEstimate"]
        row["localRawSerializationInputEstimate"] = estimate["rawJsonInputAndToolsTokensLocal"]
        meter.persist()
        save_new(
            directory / f"pacing-dispatch-{number:02}.json",
            {"attempt": row["attempt"], "requestSha256": _sha(request), "dispatchAboutToBeAttempted": True},
        )
        started = time.perf_counter()
        try:
            response = actual.responses.create(**request, timeout=timeout)
        except BaseException as error:
            pacer.failed(reservation)
            meter.finish_attempt(row, error=error, elapsed_ms=(time.perf_counter() - started) * 1000)
            save_new(
                directory / f"pacing-observed-{number:02}.json",
                {
                    "attempt": row["attempt"],
                    "status": "error",
                    "errorType": type(error).__name__,
                    "reservationRetained": reservation,
                },
            )
            raise
        try:
            meter.finish_attempt(row, response=response, elapsed_ms=(time.perf_counter() - started) * 1000)
            observed = pacer.observe(request, response, reservation)
        except BaseException as error:
            # Preserve the raw provider response through Meter before rejecting
            # malformed/missing counters. Never silently release its reservation.
            pacer.failed(reservation)
            usage = _plain(response).get("usage") or {}
            ip = usage.get("input_tokens")
            if type(ip) is int and ip >= 0:
                reservation["reserved"] = max(reservation["reserved"], ip + OUTPUT)
            save_new(
                directory / f"pacing-observed-{number:02}.json",
                {
                    "attempt": row["attempt"],
                    "status": "response_rejected",
                    "errorType": type(error).__name__,
                    "reservationRetained": reservation,
                    "providerInputTokens": ip if type(ip) is int and ip >= 0 else None,
                },
            )
            raise
        save_new(
            directory / f"pacing-observed-{number:02}.json",
            {"attempt": row["attempt"], "status": "response", **observed},
        )
        return response

    adapter = azure_responses_chat(
        "unused-by-injected-client",
        deployment,
        client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        reasoning_effort="medium",
        max_output_tokens=OUTPUT,
        max_attempts=3,
        sleep=pacer.pause,
    )

    def recorded(messages, tools):
        nonlocal logical
        pacer.remaining()
        logical = meter.logical(member)
        record = {
            "logicalCall": logical["logicalCall"],
            "messages": capture(messages),
            "tools": capture(tools),
            "status": "started",
        }
        calls.append(record)
        number = len(calls)
        save_new(directory / f"model-input-{number:02}.json", record)
        try:
            reply = adapter(messages, tools)
        except BaseException as error:
            record.update(status="failed", errorType=type(error).__name__)
            save_new(
                directory / f"model-result-{number:02}.json",
                {"logicalCall": logical["logicalCall"], "status": "failed", "errorType": type(error).__name__},
            )
            raise
        record.update(status="returned")
        save_new(directory / f"model-result-{number:02}.json", capture(asdict(reply)))
        return reply

    return recorded
