"""Private, bounded Responses-attempt meter for the exploratory model pilot.

No credentials are serialized. This wraps the existing Responses adapter's client,
so its reasoning continuity, requests, retry policy and answer mapping stay intact.
"""

from __future__ import annotations

import copy
import hashlib
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from fli_demo.responses import _plain, azure_responses_chat
from scripts.retrieval_controls import private_path, read
from scripts.structure_lab import save


def require(ok, message):
    if not ok:
        raise ValueError(message)


def scrub_media(value):
    if isinstance(value, list):
        return [scrub_media(v) for v in value]
    if isinstance(value, dict):
        return {
            k: {"redactedMediaUrlSha256": hashlib.sha256(str(v).encode()).hexdigest()}
            if k in {"image_url", "url"}
            else scrub_media(v)
            for k, v in value.items()
        }
    return value


class Meter:
    """One serial ledger shared by all cells; never resumes an existing ledger.

    Call ceilings are strict. Known token ceilings reserve one full context/output
    allowance before an attempt. Unmetered attempts retain that reservation; they
    never become zero cost. A provider input above the declared allowance stops
    the run after preserving the response, and invalidates the matched-budget gate.
    These are operating guards, not a bill guarantee or complete Azure usage meter.
    """

    def __init__(self, directory, limits, *, context=64000, output=8000):
        self.root = private_path(directory)
        self.root.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.path = self.root / "ledger.json"
        self.context, self.output = context, output
        self.state = {
            "schema": "fli.hybrid-model-pilot.provider-meter.v1",
            "limits": copy.deepcopy(limits),
            "contextAllowance": context,
            "outputAllowance": output,
            "logicalCallsStarted": 0,
            "physicalResponsesAttempts": 0,
            "knownInputTokens": 0,
            "knownOutputTokens": 0,
            "unmeteredAttempts": 0,
            "unmeteredInputReservations": 0,
            "unmeteredOutputReservations": 0,
            "logicalCalls": [],
            "calls": [],
        }
        self.persist()

    def persist(self):
        save(self.path, self.state)

    def logical(self, member):
        require(self.state["logicalCallsStarted"] < self.state["limits"]["logicalCalls"], "logical call ceiling")
        existing = sum(r.get("run") == member.get("run") for r in self.state["logicalCalls"])
        require(existing < 13, "per-turn logical call ceiling")
        self.state["logicalCallsStarted"] += 1
        call = {"logicalCall": self.state["logicalCallsStarted"], **member}
        self.state["logicalCalls"].append(call)
        self.persist()
        return call

    def begin_attempt(self, call, request):
        state, limits = self.state, self.state["limits"]
        require(sum(r["logicalCall"] == call["logicalCall"] for r in state["calls"]) < 3, "per-call attempt ceiling")
        require(state["physicalResponsesAttempts"] < limits["physicalResponsesAttempts"], "Responses attempt ceiling")
        require(
            state["knownInputTokens"] + state["unmeteredInputReservations"] + self.context
            <= limits["inputTokensWithUnknownReservations"],
            "input token reservation ceiling",
        )
        require(
            state["knownOutputTokens"] + state["unmeteredOutputReservations"] + self.output
            <= limits["outputTokensWithUnknownReservations"],
            "output token reservation ceiling",
        )
        state["physicalResponsesAttempts"] += 1
        n = state["physicalResponsesAttempts"]
        row = {
            **call,
            "attempt": n,
            "status": "started",
            "knownInputTokens": None,
            "knownOutputTokens": None,
            "startedAt": datetime.now(UTC).isoformat(),
        }
        state["calls"].append(row)
        state["unmeteredAttempts"] += 1
        state["unmeteredInputReservations"] += self.context
        state["unmeteredOutputReservations"] += self.output
        self.persist()
        save(self.root / f"request-{n:05}.json", scrub_media(request))
        return row

    def finish_attempt(self, row, *, response=None, error=None, elapsed_ms):
        row["providerAttemptElapsedMs"] = elapsed_ms
        row["finishedAt"] = datetime.now(UTC).isoformat()
        if error is not None:
            row.update(status="error", errorType=type(error).__name__, httpStatus=getattr(error, "status_code", None))
            # No exception string, HTTP headers or authentication data are serialized.
            self.persist()
            return
        body = _plain(response)
        save(self.root / f"response-{row['attempt']:05}.json", scrub_media(body))
        usage = body.get("usage") or {}
        ip, op = usage.get("input_tokens"), usage.get("output_tokens")
        row.update(status="response", responseStatus=body.get("status"))
        if type(ip) is int and ip >= 0 and type(op) is int and op >= 0:
            row.update(knownInputTokens=ip, knownOutputTokens=op)
            self.state["knownInputTokens"] += ip
            self.state["knownOutputTokens"] += op
            self.state["unmeteredAttempts"] -= 1
            self.state["unmeteredInputReservations"] -= self.context
            self.state["unmeteredOutputReservations"] -= self.output
        self.persist()
        require(type(ip) is int and type(op) is int and ip >= 0 and op >= 0, "provider token meters unavailable")
        require(ip <= self.context and op <= self.output, "actual provider input/output exceeds matched allowance")

    def client(self, actual, member, deployment):
        require(actual.max_retries == 0, "underlying OpenAI SDK retries must be zero")
        state = {"logical": None}

        def create(**request):
            require(state["logical"] is not None, "physical request lacks logical call binding")
            require(
                request.get("model") == deployment
                and request.get("reasoning") == {"effort": "medium"}
                and request.get("max_output_tokens") == self.output
                and request.get("store") is False
                and request.get("truncation") == "disabled"
                and request.get("include") == ["reasoning.encrypted_content"],
                "actual Responses controls differ",
            )
            row = self.begin_attempt(state["logical"], request)
            started = time.perf_counter()
            try:
                response = actual.responses.create(**request)
            except BaseException as error:
                self.finish_attempt(row, error=error, elapsed_ms=(time.perf_counter() - started) * 1000)
                raise
            self.finish_attempt(row, response=response, elapsed_ms=(time.perf_counter() - started) * 1000)
            return response

        proxy = SimpleNamespace(responses=SimpleNamespace(create=create))
        chat = azure_responses_chat(
            "unused-by-injected-client",
            deployment,
            client=proxy,
            reasoning_effort="medium",
            max_output_tokens=self.output,
            max_attempts=3,
        )

        def bounded(messages, tools):
            state["logical"] = self.logical(member)
            return chat(messages, tools)

        return bounded


def actual_client(endpoint):
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import OpenAI

    return OpenAI(
        base_url=endpoint.rstrip("/") + "/openai/v1/",
        api_key=get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"),
        max_retries=0,
    )


def meter_summary(path):
    state = read(private_path(path))
    calls = state["calls"]
    require(len(calls) == state["physicalResponsesAttempts"], "attempt ledger incomplete")
    require([r["attempt"] for r in calls] == list(range(1, len(calls) + 1)), "attempt sequence differs")
    logical = state["logicalCalls"]
    require(
        [r["logicalCall"] for r in logical] == list(range(1, state["logicalCallsStarted"] + 1)),
        "logical ledger incomplete",
    )
    for row in calls:
        require(1 <= row["logicalCall"] <= len(logical), "attempt logical binding missing")
        require(
            all(row.get(k) == v for k, v in logical[row["logicalCall"] - 1].items()), "attempt logical binding differs"
        )
    unknown = sum(r["knownInputTokens"] is None or r["knownOutputTokens"] is None for r in calls)
    require(
        state["unmeteredAttempts"] == unknown
        and state["unmeteredInputReservations"] == unknown * state["contextAllowance"]
        and state["unmeteredOutputReservations"] == unknown * state["outputAllowance"],
        "unknown usage reservation differs",
    )
    require(sum(r["knownInputTokens"] or 0 for r in calls) == state["knownInputTokens"], "input ledger differs")
    require(sum(r["knownOutputTokens"] or 0 for r in calls) == state["knownOutputTokens"], "output ledger differs")
    return {
        k: state[k]
        for k in (
            "logicalCallsStarted",
            "physicalResponsesAttempts",
            "knownInputTokens",
            "knownOutputTokens",
            "unmeteredAttempts",
            "unmeteredInputReservations",
            "unmeteredOutputReservations",
            "limits",
        )
    } | {
        "usageComplete": state["unmeteredAttempts"] == 0,
        "completeInputTokens": state["knownInputTokens"] if not state["unmeteredAttempts"] else None,
        "completeOutputTokens": state["knownOutputTokens"] if not state["unmeteredAttempts"] else None,
        "http429Attempts": sum(r.get("httpStatus") == 429 for r in calls),
        "otherFailedOrUnfinishedAttempts": sum(r["status"] != "response" and r.get("httpStatus") != 429 for r in calls),
        "limitation": "Responses create attempts only; excludes Azure credential, embedding and index requests. "
        "Unknown attempt reservations are guard allowances, never measured usage or billed cost.",
    }


def meter_by_cell(path):
    """Attribute known usage and unknown attempts from the actual provider ledger."""
    meter_summary(path)  # Reconcile complete ledger before projecting any subset.
    state = read(private_path(path))
    result = {}
    for cell in sorted({r["cell"] for r in state["logicalCalls"]}):
        calls = [r for r in state["calls"] if r["cell"] == cell]
        logical = [r for r in state["logicalCalls"] if r["cell"] == cell]
        unknown = sum(r["knownInputTokens"] is None or r["knownOutputTokens"] is None for r in calls)
        known_input = sum(r["knownInputTokens"] or 0 for r in calls)
        known_output = sum(r["knownOutputTokens"] or 0 for r in calls)
        result[cell] = {
            "logicalCallsStarted": len(logical),
            "physicalResponsesAttempts": len(calls),
            "knownInputTokens": known_input,
            "knownOutputTokens": known_output,
            "unmeteredAttempts": unknown,
            "usageComplete": unknown == 0,
            "completeInputTokens": known_input if unknown == 0 else None,
            "completeOutputTokens": known_output if unknown == 0 else None,
            "http429Attempts": sum(r.get("httpStatus") == 429 for r in calls),
            "scope": "Actual Responses attempts for this cell; unknown error usage is not zero.",
        }
    return result
