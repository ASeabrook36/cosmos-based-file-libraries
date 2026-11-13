"""Six bounded source-navigation turns using the normal agent and Responses adapter.

Importable by the separately reviewed live coordinator; importing or preparing
cases makes no network calls. All captures/gold/identities stay in fresh private
directories. This is a controlled mechanics qualification, not fresh confirmation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fli.chunking import default_tokenizer
from fli_demo.agent import MAX_ROUNDS, run_turn
from fli_demo.responses import azure_responses_chat
from scripts.evaluate_agent_retrieval import capture
from scripts.hybrid_model_meter import Meter, meter_summary
from scripts.qualify_granular_navigation import LIMITS, PROFILE, save_new, sha
from scripts.retrieval_controls import AGENT_INSTRUCTIONS, private_path
from scripts.structure_lab import save

REPO = Path(__file__).resolve().parents[1]
CONTEXT = 200000
OUTPUT = 8000
TOKEN_RATE = 45000
PACING = {
    "windowSeconds": 60,
    "localInputPlusMaxOutputCeiling": TOKEN_RATE,
    "serializedInputPerRequestCeiling": TOKEN_RATE - OUTPUT,
    "limitation": "Separate local TPM pacing constraint, not native/model context capacity. "
    "Opaque encrypted Responses items may inflate serialization estimates.",
}
METER_LIMITS = {
    "logicalCalls": LIMITS["logicalModelCalls"],
    "physicalResponsesAttempts": LIMITS["physicalModelAttempts"],
    "inputTokensWithUnknownReservations": LIMITS["inputTokensIncludingUnknownReservations"],
    "outputTokensWithUnknownReservations": LIMITS["outputTokensIncludingUnknownReservations"],
}
SELECTOR_FIELDS = {
    "documentSelector",
    "outlineSelector",
    "readSelector",
    "subtreeSelector",
    "fullSelector",
    "parentSelector",
    "containingSectionSelector",
    "selectorGuidance",
    "readCapabilities",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def code_hashes():
    paths = [
        *REPO.glob("src/**/*.py"),
        Path(__file__),
        REPO / "scripts/hybrid_model_meter.py",
        REPO / "scripts/qualify_granular_navigation.py",
        REPO / "scripts/retrieval_controls.py",
        REPO / "scripts/evaluate_agent_retrieval.py",
        REPO / "scripts/structure_lab.py",
        REPO / "requirements-local.lock",
    ]
    return {str(path.relative_to(REPO)): sha(path) for path in sorted(paths)}


def _bindings(values):
    require(isinstance(values, dict) and set(values) == {"docx", "markdown"}, "bind DOCX and Markdown source roles")
    for role, value in values.items():
        require(isinstance(value, dict), "source binding must be an object")
        for key in ("libraryId", "documentId", "fileName", "sourceSha256"):
            require(isinstance(value.get(key), str) and value[key], "source identity incomplete")
        require(re.fullmatch(r"[a-f0-9]{64}", value["sourceSha256"]), "source hash unavailable")
        require(type(value.get("generation")) is int and value["generation"] > 0, "source generation unavailable")
        require(
            type(value.get("contentRevision")) is int and value["contentRevision"] >= 0, "source revision unavailable"
        )
        require(value["fileName"].lower().endswith(".docx" if role == "docx" else ".md"), "source format differs")
    return copy.deepcopy(values)


def cases(source_bindings):
    """Prompts and hidden expectations fixed before any answering call."""
    bindings = _bindings(source_bindings)
    specifications = [
        (
            "paragraph-parent",
            "docx",
            "Find the paragraph about recording inspection results. Read only that paragraph first, then expand "
            "to its containing authored section using the returned selector. State which recorder is used and "
            "explain how the selected paragraph differs in scope from the expanded section. Cite the source.",
            ["The amber recorder stores each inspection result."],
            ["paragraph source read", "containing authored section expansion"],
        ),
        (
            "cell-qualifier",
            "docx",
            "Locate the cold-storage action cell and read that complete table cell. Describe its instructions "
            "and the final qualification, preserving which paragraphs are list items and which paragraph is "
            "outside the list. Do not invent printed list numbers. Cite source table/cell coordinates.",
            [
                "Keep the vessel sealed.",
                "Record the temperature.",
                "Notify the supervisor.",
                "These steps apply only while the vessel is in cold storage.",
            ],
            ["whole cell read", "qualifier retained as a separate unnumbered paragraph"],
        ),
        (
            "section-subtree",
            "docx",
            "Read the Sampling section's own content, then its complete subtree using the returned selectors. "
            "Explain which sampling instruction belongs to the section itself and which damaged-vessel "
            "exception becomes available through the authored subsection. Cite both sources and distinguish scopes.",
            ["Collect two samples before opening the valve.", "Do not sample a damaged vessel."],
            ["nodeScope section read", "subtree read including Exceptions"],
        ),
        (
            "region-document",
            "docx",
            "Find and read the reporting instruction as a small source region, then use its full-document "
            "selector to check the complete document. State the reporting deadline and the recorder named "
            "elsewhere. Distinguish the initial selected read from the completed document read, with citations.",
            ["Submit the record on the next working day.", "The amber recorder stores each inspection result."],
            ["small selected read", "complete full-document read"],
        ),
        (
            "markdown-block",
            "markdown",
            "Use the authored outline to find Sampling and its Exceptions subsection. Read the selected "
            "Markdown source block for Exceptions. State the exception and cite its source location; "
            "do not claim the selected block is a complete document read.",
            ["Do not sample a damaged vessel."],
            ["Markdown authored outline", "selected source block"],
        ),
        (
            "unsupported-locators",
            "docx",
            "An old saved passage has no verifiable source mapping. Use the current authored outline to find "
            "and read the damaged-vessel rule instead. Can you give a rendered page number or authored chapter "
            "number for it? State what this source representation actually supports, cite the available source "
            "coordinates, and do not guess the old passage mapping or unavailable page/chapter numbers.",
            ["Do not sample a damaged vessel."],
            [
                "outline navigation from a hypothetical missing mapping",
                "no invented rendered page or chapter",
                "mapping limitation disclosed",
            ],
        ),
    ]
    result = []
    for identifier, role, question, facts, actions in specifications:
        source = bindings[role]
        pin = {key: source[key] for key in ("documentId", "generation", "contentRevision")}
        result.append(
            {
                "case": identifier,
                "sourceRole": role,
                "question": (
                    f"The platform has selected {source['fileName']}. Its current document selector is "
                    + json.dumps(pin, sort_keys=True)
                    + ". "
                    + question
                ),
                "expectedSourceFacts": facts,
                "expectedNavigation": actions,
                "requiredCitationFile": source["fileName"],
                "judgment": "manual source-based review required; lexical delivery indicators are not answer grades",
            }
        )
    return result


def prepare_cases(output, source_bindings):
    root = private_path(output)
    require(not root.exists(), "model qualification requires a new private directory")
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair is separately attributable")
    bindings = _bindings(source_bindings)
    controls = {
        "schema": "fli.granular-live-models.controls.v1",
        "sourceBindings": bindings,
        "cases": cases(bindings),
        "codeSha256": code_hashes(),
        "meterLimits": METER_LIMITS,
        "contextAllowance": CONTEXT,
        "outputAllowance": OUTPUT,
        "reasoningEffort": "medium",
        "maxRounds": MAX_ROUNDS,
        "searchMode": "vector",
        "evidenceView": "compact",
        "selectors": "handles",
        "contextAccounting": "legacy-v1",
        "agentInstructions": AGENT_INSTRUCTIONS,
        "pacing": PACING,
        "qualificationScope": "Six controlled fresh navigation turns with platform-provided current document pins.",
        "largerConfirmation": False,
    }
    root.mkdir(parents=True, mode=0o700)
    save_new(root / "controls.json", controls)
    return {"controlsSha256": sha(root / "controls.json"), "cases": len(controls["cases"]), "status": "prepared"}


def _verify(root):
    controls = json.loads((root / "controls.json").read_text())
    require(controls.get("schema") == "fli.granular-live-models.controls.v1", "unknown model controls")
    require(controls.get("cases") == cases(controls.get("sourceBindings")), "sealed cases changed")
    require(controls.get("codeSha256") == code_hashes(), "model qualification code drift")
    for key, value in {
        "meterLimits": METER_LIMITS,
        "contextAllowance": CONTEXT,
        "outputAllowance": OUTPUT,
        "reasoningEffort": "medium",
        "maxRounds": MAX_ROUNDS,
        "searchMode": "vector",
        "evidenceView": "compact",
        "selectors": "handles",
        "contextAccounting": "legacy-v1",
        "agentInstructions": AGENT_INSTRUCTIONS,
        "pacing": PACING,
    }.items():
        require(controls.get(key) == value, "model qualification controls changed")
    return controls


class PacingRefusal(ValueError):
    """Local TPM pacing prevented a request; this is not a context-budget stop."""


class Pacer:
    """Local conservative TPM pacing; reservations are not measured provider usage."""

    def __init__(self, deadline, *, clock=time.monotonic, sleep=time.sleep):
        self.deadline, self.clock, self.sleep = deadline, clock, sleep
        self.rows = []

    def remaining(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("model qualification deadline reached")
        return remaining

    def pause(self, seconds):
        require(seconds >= 0, "invalid pacing delay")
        left = seconds
        while left > 0:
            remaining = self.remaining()
            pause = min(left, remaining, 30)
            self.sleep(pause)
            left -= pause
        self.remaining()

    def reserve(self, input_estimate):
        amount = input_estimate + OUTPUT
        if amount > TOKEN_RATE:
            raise PacingRefusal("one request exceeds qualification TPM pacing ceiling")
        while True:
            self.remaining()
            self.rows = [row for row in self.rows if self.clock() - row["at"] < 60]
            if sum(row["reserved"] for row in self.rows) + amount <= TOKEN_RATE:
                row = {"at": self.clock(), "reserved": amount}
                self.rows.append(row)
                return row
            self.pause(max(0.001, 60 - (self.clock() - min(row["at"] for row in self.rows))))


def _count(value, tokenizer):
    return len(tokenizer.encode(json.dumps(value, ensure_ascii=False, separators=(",", ":"))))


def _omit(value, keys):
    if isinstance(value, list):
        return [_omit(item, keys) for item in value]
    if isinstance(value, dict):
        return {key: _omit(item, keys) for key, item in value.items() if key not in keys}
    return value


def _texts(value):
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    if not isinstance(value, dict):
        return []
    own = (
        [value["text"]]
        if isinstance(value.get("text"), str)
        and value.get("textSource", "extracted_source_text") == "extracted_source_text"
        else []
    )
    return own + [text for key, item in value.items() if key != "text" for text in _texts(item)]


def tool_metrics(message, tokenizer=None):
    tokenizer = tokenizer or default_tokenizer()
    payload = json.loads(message["content"])
    evidence_tokens = _count(payload, tokenizer)
    envelope_tokens = _count(message, tokenizer)
    return {
        "tokenizer": tokenizer.name,
        "evidenceJsonTokensLocal": evidence_tokens,
        "standaloneSourceTextTokensLocal": sum(len(tokenizer.encode(text)) for text in _texts(payload)),
        "selectorSerializationDeltaTokensLocal": evidence_tokens - _count(_omit(payload, SELECTOR_FIELDS), tokenizer),
        "envelopeWithoutSourceOrSelectorsTokensLocal": _count(_omit(payload, SELECTOR_FIELDS | {"text"}), tokenizer),
        "toolMessageJsonTokensLocal": envelope_tokens,
        "toolMessageWrapperDeltaTokensLocal": envelope_tokens - evidence_tokens,
        "interpretation": "Local serialization estimates, not provider tokens or billing. "
        "Counterfactual components are not additive.",
    }


class CapturedIndex:
    def __init__(self, index, directory):
        self.index, self.directory, self.calls = index, directory, []

    def __getattr__(self, name):
        value = getattr(self.index, name)
        if name not in {"tools", "discover_documents", "search", "read_documents", "read_images"}:
            return value

        def invoke(*args, **kwargs):
            number = len(self.calls) + 1
            row = {"operation": name, "args": capture(args), "kwargs": capture(kwargs), "status": "started"}
            self.calls.append(row)
            save_new(self.directory / f"tool-request-{number:02}.json", row)
            try:
                response = value(*args, **kwargs)
            except BaseException as error:
                row.update(status="failed", errorType=type(error).__name__)
                save_new(self.directory / f"tool-result-{number:02}.json", row)
                raise
            row.update(status="returned", response=capture(response))
            save_new(self.directory / f"tool-result-{number:02}.json", row)
            return response

        return invoke


class DeadlineHTTP:
    """Qualification-only deadline guard around the normal loopback HTTP client.

    HTTP timeouts cover inactivity in connect/read/write/pool phases, not an
    independent wall-clock cancellation. Each phase receives at most one fourth
    of the remaining window; a reply is checked again before it can be consumed.
    """

    def __init__(self, http, pacer):
        self.http, self.pacer = http, pacer

    def __getattr__(self, name):
        return getattr(self.http, name)

    def request(self, method, path, **kwargs):
        kwargs["timeout"] = min(30.0, self.pacer.remaining() / 4)
        response = self.http.request(method, path, **kwargs)
        self.pacer.remaining()
        return response


def _source_snapshot(index, bindings):
    result = {}
    for role, source in bindings.items():
        library = index.get_library(source["libraryId"])
        require(
            library.get("status") == "active" and library.get("ingestionProfile", {}).get("id") == PROFILE,
            "source library/profile changed",
        )
        rows = index.list_documents(source["libraryId"])
        matches = [row for row in rows if row.get("id") == source["documentId"]]
        require(len(matches) == 1, "model source document unavailable")
        document = matches[0]
        require(
            document.get("status") == "ready"
            and document.get("pendingGeneration") is None
            and document.get("activeGeneration") == source["generation"]
            and document.get("contentRevision") == source["contentRevision"]
            and document.get("sha256") == source["sourceSha256"]
            and document.get("fileName") == source["fileName"],
            "model source snapshot changed",
        )
        result[role] = {"library": capture(library), "document": capture(document)}
    return result


def _chat(actual, deployment, meter, member, pacer, directory, calls):
    tokenizer = default_tokenizer()
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
        estimate = _count({"input": request["input"], "tools": request["tools"]}, tokenizer)
        reservation = pacer.reserve(estimate)
        row = meter.begin_attempt(logical, request)
        row["localPacingInputEstimate"] = estimate
        meter.persist()
        started = time.perf_counter()
        try:
            response = actual.responses.create(**request, timeout=min(60.0, pacer.remaining()))
        except BaseException as error:
            meter.finish_attempt(row, error=error, elapsed_ms=(time.perf_counter() - started) * 1000)
            raise
        meter.finish_attempt(row, response=response, elapsed_ms=(time.perf_counter() - started) * 1000)
        reservation["reserved"] = max(reservation["reserved"], row["knownInputTokens"] + OUTPUT)
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


def _turn_summary(case, turn, calls):
    submitted = {}
    for call in calls:
        if call["status"] != "returned":
            continue
        for message in call["messages"]:
            if message.get("role") == "tool":
                key = (message.get("tool_call_id"), hashlib.sha256(message["content"].encode()).hexdigest())
                submitted[key] = message
    received = [message for message in turn.messages if message.get("role") == "tool"]
    sources = "\n".join(text for message in submitted.values() for text in _texts(json.loads(message["content"])))
    charges = [trace.request_charge for trace in turn.searches]
    return {
        "case": case["case"],
        "answer": turn.answer,
        "totalMs": turn.total_ms,
        "logicalModelCalls": len(calls),
        "toolOperations": len(turn.searches),
        "localToolReplies": len(received),
        "toolRepliesInSuccessfulModelCalls": len(submitted),
        "toolReplyLocalMeasurements": [tool_metrics(message) for message in submitted.values()],
        "knownToolRequestCharge": sum(charge for charge in charges if charge is not None),
        "unknownToolCharges": sum(charge is None for charge in charges),
        "completeToolRequestCharge": sum(charges) if all(charge is not None for charge in charges) else None,
        "chargeScope": "Agent operation traces only; excludes preflight, tool definitions, source snapshot reads "
        "and other uninstrumented operations.",
        "factDeliveryIndicators": [
            {"sourceFact": fact, "delivered": fact in sources} for fact in case["expectedSourceFacts"]
        ],
        "citationFilenameMentioned": case["requiredCitationFile"] in turn.answer,
        "manualReview": {
            "answerCorrect": None,
            "citationsCorrect": None,
            "relationshipsPreserved": None,
            "navigationRequirementsMet": None,
        },
        "judgment": "Delivery and lexical indicators only; source-based manual review remains required.",
    }


def run_models(state, *, index, agent_id, actual_client, deployment, model_binding, deadline, api_controls):
    """Synchronous; coordinator may call via asyncio.to_thread beside its API loop.

    The coordinator supplies a freshly verified live model binding and API control
    receipt. This function does not provision resources or load credentials.
    """
    root = private_path(state)
    controls = _verify(root)
    require(index.evidence_view == "compact" and index.selector_mode == "handles", "normal client presentation differs")
    require(
        api_controls.get("searchMode") == "vector" and api_controls.get("contextAccounting") == "legacy-v1",
        "API/context controls differ",
    )
    require(actual_client.max_retries == 0, "OpenAI transport retries must be zero")
    require(
        isinstance(deployment, str) and deployment and isinstance(agent_id, str) and agent_id,
        "runtime model/agent binding unavailable",
    )
    require(
        isinstance(model_binding, dict)
        and isinstance(model_binding.get("model"), dict)
        and model_binding["model"].get("name")
        and model_binding["model"].get("version")
        and model_binding.get("sku")
        and re.fullmatch(r"[a-f0-9]{64}", model_binding.get("bindingSha256", "")),
        "fresh verified model binding required",
    )
    pacer = Pacer(deadline)
    pacer.remaining()
    index = copy.copy(index)
    index.http = DeadlineHTTP(index.http, pacer)
    save_new(
        root / "started.json",
        {
            "controlsSha256": sha(root / "controls.json"),
            "modelBinding": model_binding,
            "deployment": deployment,
            "apiControls": api_controls,
            "startedAt": datetime.now(UTC).isoformat(),
        },
    )
    meter = Meter(root / "provider", METER_LIMITS, context=CONTEXT, output=OUTPUT)
    summaries, active = [], None
    before = None
    try:
        before = _source_snapshot(index, controls["sourceBindings"])
        save_new(root / "sources-before.json", before)
        for active in controls["cases"]:
            pacer.remaining()
            _verify(root)
            require(
                _source_snapshot(index, controls["sourceBindings"]) == before,
                "source metadata changed before model turn",
            )
            directory = root / active["case"]
            directory.mkdir(mode=0o700)
            calls = []
            captured = CapturedIndex(index, directory)
            source = controls["sourceBindings"][active["sourceRole"]]
            agent = {"id": agent_id, "libraryIds": [source["libraryId"]], "instructions": AGENT_INSTRUCTIONS}
            chat = _chat(
                actual_client,
                deployment,
                meter,
                {"run": active["case"], "cell": "granular-vector", "case": active["case"]},
                pacer,
                directory,
                calls,
            )
            turn = run_turn(
                chat,
                captured,
                agent,
                [],
                active["question"],
                context_token_budget=CONTEXT,
                max_rounds=MAX_ROUNDS,
                image_inputs=False,
            )
            save_new(directory / "turn.json", capture(asdict(turn)))
            require(
                _source_snapshot(index, controls["sourceBindings"]) == before,
                "source metadata changed during model turn",
            )
            summary = _turn_summary(active, turn, calls)
            summaries.append(summary)
            save_new(directory / "summary.json", summary)
            save(
                root / "status.json",
                {"status": "running", "completedTurns": len(summaries), "provider": meter_summary(meter.path)},
            )
        _verify(root)
        after = _source_snapshot(index, controls["sourceBindings"])
        require(after == before, "source snapshot changed at model exit")
        save_new(root / "sources-after.json", after)
        result = {
            "status": "complete_pending_source_review",
            "completedTurns": len(summaries),
            "cases": summaries,
            "provider": meter_summary(meter.path),
            "sourceSnapshotsUnchanged": True,
            "largerConfirmation": False,
        }
        save_new(root / "results.json", result)
        save(root / "status.json", {key: value for key, value in result.items() if key != "cases"})
        return result
    except BaseException as error:
        save(
            root / "status.json",
            {
                "status": "failed",
                "completedTurns": len(summaries),
                "activeCase": active["case"] if active else None,
                "errorType": type(error).__name__,
                "provider": meter_summary(meter.path),
                "limitation": "Single-use run stopped. Preserve partial evidence; no automatic resume, "
                "selective rerun or raised ceiling.",
            },
        )
        raise
