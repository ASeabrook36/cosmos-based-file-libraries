"""Shared fail-closed checks for experiments descended from the frozen R0 pack.

Management receipts and source snapshots are private operator evidence, not signed
attestations. These checks bind them to the actual configured endpoint/deployment
and reject stale, mismatched or incomplete artifacts before accepting comparisons.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

R0_COMMIT = "a449dc01e5acde6391bfc1aa50ca07670d54097d"
PACK_HASHES = {
    "manifest.json": "ef9e8ae4037903e8c18a1db09dbb7410030be53ab6c35e6549218ae6803ed1cf",
    "tasks.json": "997a7090c3cca5dacef760a5d0a241eeed4f90168ae427877ec70b4e51deaab5",
}
AGENT_INSTRUCTIONS = (
    "Use the attached library to answer the question. Read original source evidence, "
    "distinguish source statements from inference, preserve stated conditions and "
    "limitations, and cite the correct source file and location. Do not infer an "
    "answer from a generated description. If the requested evidence is unavailable, "
    "say precisely what could not be verified."
)
ANSWER_CONTROLS = {
    "contextAllowancePerRun": 200000,
    "reasoningEffort": "medium",
    "maxOutputTokens": 8000,
    "maxAttempts": 3,
    "transport": "responses",
    "store": False,
    "automaticTruncation": False,
}


def read(path):
    return json.loads(Path(path).read_text())


def private_path(path):
    """Reject every Git checkout, including linked worktrees and resolved symlinks."""
    path = Path(path).expanduser().resolve()
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            raise ValueError("runtime data must remain outside every Git checkout")
    return path


def frozen_pack(directory):
    directory = Path(directory)
    for name, expected in PACK_HASHES.items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen R0 pack bytes changed; name a new experiment instead")
    return read(directory / "manifest.json"), read(directory / "tasks.json")


def answer_controls(controls):
    for key, expected in ANSWER_CONTROLS.items():
        value = controls.get(key)
        if type(value) is not type(expected) or value != expected:
            raise ValueError("answering controls differ from frozen R0 comparison")
    model = controls.get("model", {})
    if model.get("model") != "gpt-6.1-sol" or model.get("version") != "2026-09-29":
        raise ValueError("answering model differs from frozen R0 comparison")
    if controls.get("agent", {}).get("instructions") != AGENT_INSTRUCTIONS:
        raise ValueError("agent instructions differ from frozen R0 comparison")


def endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("expected a private HTTPS service endpoint")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("expected service root endpoint")
    return parsed.hostname.lower(), parsed.port or 443


def deployment_binding(receipt, *, configured_endpoint, configured_deployment, model, revision, now=None):
    """Validate a fresh envelope containing actual ARM account/deployment GETs.

    Envelope: capturedAt (UTC ISO), account (ARM account GET), deployment (ARM
    deployment GET). Call again with a new envelope after every measured window;
    compare the returned model/SKU fingerprint before accepting the result.
    """
    captured = datetime.fromisoformat(receipt["capturedAt"])
    now = now or datetime.now(UTC)
    if captured.tzinfo is None or not 0 <= (now - captured).total_seconds() <= 900:
        raise ValueError("deployment receipt is stale or has an invalid timestamp")
    account, deployment = receipt["account"], receipt["deployment"]
    account_id, deployment_id = account["id"].rstrip("/"), deployment["id"]
    if deployment_id.lower() != (account_id + "/deployments/" + configured_deployment).lower():
        raise ValueError("deployment receipt does not bind the invoked deployment")
    properties = account["properties"]
    endpoints = [properties.get("endpoint"), *properties.get("endpoints", {}).values()]
    if endpoint(configured_endpoint) not in [endpoint(e) for e in endpoints if e]:
        raise ValueError("deployment account does not bind the invoked endpoint")
    actual = deployment["properties"]
    expected = {"format": "OpenAI", "name": model, "version": revision}
    if (
        actual.get("model") != expected
        or actual.get("versionUpgradeOption") != "NoAutoUpgrade"
        or actual.get("provisioningState") != "Succeeded"
        or not deployment.get("sku")
    ):
        raise ValueError("actual deployment settings differ from frozen controls")
    identity = json.dumps(
        {
            "id": deployment_id.lower(),
            "endpoint": endpoint(configured_endpoint),
            "model": expected,
            "sku": deployment["sku"],
        },
        sort_keys=True,
    )
    return {"model": expected, "sku": deployment["sku"], "bindingSha256": hashlib.sha256(identity.encode()).hexdigest()}


def verify_cell(directory, cases, expected_snapshot=None):
    """Reopen artifacts; a reported unchanged/complete boolean is not sufficient."""
    directory = Path(directory)
    status = read(directory / "run-status.json")
    before = read(directory / "source-snapshot.json")
    check = read(directory / "snapshot-check.json")
    if read(directory / "cases.json") != cases:
        raise ValueError("saved cases differ from frozen selection")
    if check.get("unchanged") is not True or before != check.get("before") or before != check.get("after"):
        raise ValueError("source snapshots changed or are inconsistent")
    if expected_snapshot is not None and before != expected_snapshot:
        raise ValueError("cell source snapshot differs from experiment binding")
    expected = {
        (case["case"], turn): question
        for case in cases
        for turn, question in enumerate(case.get("questions") or [case["question"]], 1)
    }
    rows = read(directory / "results.json")
    observed = set()
    for row in rows:
        key = row["case"], row["turn"]
        if type(row["turn"]) is not int or key in observed or row.get("question") != expected.get(key):
            raise ValueError("executed turn differs from frozen question matrix")
        observed.add(key)
    if (
        observed != set(expected)
        or status.get("status") != "complete"
        or type(status.get("completedTurns")) is not int
        or status["completedTurns"] != len(expected)
        or status.get("budget") != 200000
    ):
        raise ValueError("incomplete or unmatched cell cannot establish comparison results")
    return {"completedTurns": len(rows), "sourceArtifactsVerified": True, "questionsVerified": True}
