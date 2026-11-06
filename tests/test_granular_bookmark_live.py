"""Surviving-bookmark six-turn runner: local stores and fake provider only."""

import copy
import json
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import granular_bookmark_live as runner
from scripts import granular_reference_repair_live as old
from scripts.granular_pacing import PacingRefusal
from tests.test_granular_followup import hashes, write
from tests.test_granular_live_models import Clock, binding
from tests.test_granular_reference_live import prior  # noqa: F401
from tests.test_granular_reference_repair_live import earlier as presentation_prior  # noqa: F401
from tests.test_responses_chat import call, message, reasoning, response


@pytest.fixture
def earlier(tmp_path, presentation_prior, monkeypatch):  # noqa: F811
    old_state = tmp_path / "repair-state"
    old.prepare(
        old_state, presentation_prior[0], prior_manifest=presentation_prior[1], prior_snapshot=presentation_prior[2]
    )
    previous = tmp_path / "repair-previous"
    shutil.copytree(old_state, previous / "live-check")
    write(previous / "live-check/binding-after.json", {"binding": binding()})
    write(
        previous / "live-check/execution-result.json",
        {
            "providerClosed": True,
            "postchecks": {"passed": True},
            "qualification": {"completedTurns": 6, "completedSourceTurns": 4},
            "cloudDataResourcesCreated": 0,
        },
    )
    manifest = tmp_path / "repair-previous-manifest.json"
    write(manifest, {"files": hashes(previous)})
    snapshot = tmp_path / "repair-previous-snapshot.json"
    write(snapshot, {"files": runner.inventory(previous)})
    repo = tmp_path / "source-bookmark-repo"
    write(
        repo / "records/granular-reference-contract-repair-20261105.json",
        {"privateEvidence": {"reviewedFinalArchive": {"manifestSha256": runner.sha(manifest)}}},
    )
    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "code_hashes", lambda: {"frozen": "a" * 64})
    return previous, manifest, snapshot


@pytest.fixture
def prepared(tmp_path, earlier):
    root = tmp_path / "qualification"
    runner.prepare(root, earlier[0], prior_manifest=earlier[1], prior_snapshot=earlier[2])
    return root


def test_prepare_freezes_exact_six_turn_policy_anchors_and_old_evidence_without_cloud(prepared, earlier, monkeypatch):
    monkeypatch.setattr(runner, "management_get", lambda *a: pytest.fail("cloud"))
    controls = runner.verify(prepared, prior_inventory=True)
    assert controls["order"] == [f"{c}/{s}" for c in runner.CELLS for s in runner.STAGES]
    assert (
        controls["caseA"]["question"]
        == old.cases(runner.read(prepared / "fixture/source-bindings.json"))[0]["question"]
    )
    assert controls["meterLimits"]["logicalCalls"] == 60 and controls["roundLimits"] == {
        "A": 12,
        "acknowledgment": 3,
        "B": 12,
    }
    assert controls["acknowledgment"] == runner.ACKNOWLEDGMENT and controls["followupTemplate"] == runner.FOLLOWUP
    assert "not a matched-prose-only" in controls["retentionPolicy"]
    assert controls["applicationBookmarkState"]["ttlSeconds"] == 86400
    assert controls["applicationBookmarkState"]["maxEntries"] == 32
    assert controls["caseA"] == runner.read(prepared / "prior-qualification-controls.json")["caseA"]
    assert runner.inventory(earlier[0]) == runner.read(earlier[2])["files"]
    assert (prepared / "fixture/source.docx").read_bytes() == (
        earlier[0] / "live-check/fixture/source.docx"
    ).read_bytes()


@pytest.mark.parametrize("changed", ["old", "controls", "source", "code"])
def test_freeze_and_whole_prior_inventory_drift_fail_closed(prepared, earlier, monkeypatch, changed):
    if changed == "old":
        (earlier[0] / "new-file.txt").write_text("not silently ignored")
    elif changed == "code":
        monkeypatch.setattr(runner, "code_hashes", lambda: {"other": True})
    else:
        path = prepared / ("controls.json" if changed == "controls" else "fixture/source.docx")
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed|drift"):
        runner.verify(prepared, prior_inventory=True)


def test_prior_manifest_and_archive_file_drift_prevent_preparation(tmp_path, earlier):
    source = earlier[0] / "live-check/fixture/source.docx"
    source.write_bytes(source.read_bytes() + b"changed")
    write(earlier[2], {"files": runner.inventory(earlier[0])})
    with pytest.raises(ValueError, match="archive input changed"):
        runner.prepare(tmp_path / "no", earlier[0], prior_manifest=earlier[1], prior_snapshot=earlier[2])
    assert not (tmp_path / "no").exists()


def delivered(payload, *, status="returned", logical=1, call_id="delivered"):
    return {
        "status": status,
        "logicalCall": logical,
        "messages": [{"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}],
    }


def payload(kind="source", navigation=False, token="exact"):
    action = "read" if kind == "source" else "outline"
    row = (
        {"text": "Original source.", "textSource": "extracted_source_text"}
        if kind == "source"
        else {"title": "A heading"}
    )
    value = (
        token
        if navigation
        else {
            "mode": "selected" if kind == "source" else "outline",
            "documents": [{"documentId": token, "generation": 1, "contentRevision": 2}],
        }
    )
    row.update({"actions": {action: "expiring-action"}} if navigation else {action + "Selector": value})
    document = {"fileName": "source.docx", "sections" if kind == "source" else "outline": [row]}
    if navigation and kind == "source":
        document["sourceBookmark"] = {"read": {"bookmark": token}, "scope": "exact completed selection"}
    return {
        "mode": "selected" if kind == "source" else "outline",
        "documents": [document],
    }


@pytest.mark.parametrize("cell", runner.CELLS)
def test_bookmark_only_uses_successful_actual_wire_exact_action_and_no_hidden_lookup(cell):
    nav = cell == "navigation"
    first, last, failed = (
        payload(navigation=nav, token="first"),
        payload(navigation=nav, token="last"),
        payload(navigation=nav, token="never-delivered"),
    )
    calls = [delivered(first), delivered(last, logical=2), delivered(failed, logical=3, status="failed")]
    before = copy.deepcopy(calls)
    mark = runner.delivered_bookmark(calls, cell)
    assert mark["rawAction"] == ({"bookmark": "last"} if nav else last["documents"][0]["sections"][0]["readSelector"])
    assert mark["logicalCallDelivered"] == 2 and mark["originalSourceBookmark"]
    assert calls == before and not mark["selectionUsesHiddenRegistry"]
    question = runner.followup_question({"fileName": "source.docx"}, mark)
    assert json.dumps(mark["readRequest"], sort_keys=True) in question
    assert "First try that bookmark" in question


def test_outline_and_expiring_action_never_substitute_for_missing_source_bookmark():
    mark = runner.delivered_bookmark([delivered(payload("outline", True))], "navigation")
    assert not mark["available"] and not mark["originalSourceBookmark"]
    source = payload(navigation=True)
    del source["documents"][0]["sourceBookmark"]
    assert not runner.delivered_bookmark([delivered(source)], "navigation")["available"]
    missing = runner.delivered_bookmark([delivered(payload(), status="failed")], "compact")
    assert not missing["available"] and missing["readRequest"] is None
    assert "No usable" in runner.followup_question({"fileName": "source.docx"}, missing)


def test_navigation_bookmark_keeps_completed_multiatom_selection_not_first_row_action():
    source = payload(navigation=True, token="whole-original-selection")
    source["documents"][0]["sections"].append(
        {"text": "A second source atom.", "actions": {"read": "second-expiring-action"}}
    )
    mark = runner.delivered_bookmark([delivered(source)], "navigation")
    assert mark["readRequest"] == {"bookmark": "whole-original-selection"}
    assert "whole original" in runner.BOOKMARK_POLICY
    assert "exact original completed source selection" in mark["scope"]
    assert "expiring-action" not in json.dumps(mark)


@pytest.mark.parametrize("name", ["contextAllowance", "outputAllowance", "meterLimits", "acknowledgment", "model"])
def test_prior_comparison_control_drift_is_not_accepted(name):
    prior_controls = old.preparation_controls()
    runner.unchanged_controls(prior_controls)
    prior_controls[name] = "different"
    with pytest.raises(ValueError, match="prior control differs: " + name):
        runner.unchanged_controls(prior_controls)


def test_provider_boundaries_hard_guard_and_archive_remain_single_use(prepared, monkeypatch):
    def fail(*args, **kwargs):
        raise PacingRefusal("shared hard guard")

    monkeypatch.setattr(runner, "run_turn", fail)
    with pytest.raises(PacingRefusal):
        runner.run_qualification(
            prepared, provider=SimpleNamespace(max_retries=0), deployment="chosen", binding=binding(), deadline=10**12
        )
    result = runner.read(prepared / "qualification-result.json")
    assert result["completedTurns"] == 0 and len(result["uncompletedDeclaredTurns"]) == 6
    assert result["provider"]["physicalResponsesAttempts"] == 0 and result["cosmosRu"] is None
    assert runner.read(prepared / "compact/A/source-check.json")["passed"]
    with pytest.raises(FileExistsError):
        runner.run_qualification(
            prepared, provider=SimpleNamespace(max_retries=0), deployment="chosen", binding=binding(), deadline=10**12
        )


def test_hard_failure_in_navigation_closes_owner_and_discards_trusted_state(prepared, monkeypatch):
    from fli_demo.agent import Turn

    owners, states = [], []
    real_owner = runner.source_bookmark_turn

    @contextmanager
    def tracked_owner(state, *args, **kwargs):
        with real_owner(state, *args, **kwargs) as arguments:
            if arguments:
                owners.append(arguments["bookmark_store"])
                states.append(state)
            yield arguments

    def fail_navigation(*args, **kwargs):
        if kwargs.get("bookmark_store") is not None:
            raise PacingRefusal("navigation hard guard")
        return Turn("No original source was read.", [], [])

    monkeypatch.setattr(runner, "source_bookmark_turn", tracked_owner)
    monkeypatch.setattr(runner, "run_turn", fail_navigation)
    with pytest.raises(PacingRefusal, match="navigation hard guard"):
        runner.run_qualification(
            prepared, provider=SimpleNamespace(max_retries=0), deployment="chosen", binding=binding(), deadline=10**12
        )
    result = runner.read(prepared / "qualification-result.json")
    assert result["completedTurns"] == 3 and result["activeTurn"] == "navigation/A"
    assert len(owners) == len(states) == 1 and states[0] == {}
    assert owners[0].closed and owners[0]._authority == b"" and owners[0]._entries == {}
    assert result["applicationBookmarkStateDiscarded"]
    assert result["applicationBookmarkClosures"] == [
        {"cell": "navigation", "stage": "A", "closed": True, "authorityKeyPersisted": False}
    ]
    assert runner.read(prepared / "navigation/A/source-check.json")["passed"]
    assert not (prepared / "navigation/acknowledgment").exists()


def test_navigation_admission_failure_before_stage_directory_preserves_primary_error(prepared, monkeypatch):
    from fli_demo.agent import Turn

    real_verify = runner.verify

    def admission(root):
        result = real_verify(root)
        if (root / "compact/B/summary.json").exists():
            raise PacingRefusal("first navigation admission")
        return result

    monkeypatch.setattr(runner, "verify", admission)
    monkeypatch.setattr(runner, "run_turn", lambda *a, **kw: Turn("No source read.", [], []))
    with pytest.raises(PacingRefusal, match="first navigation admission"):
        runner.run_qualification(
            prepared, provider=SimpleNamespace(max_retries=0), deployment="chosen", binding=binding(), deadline=10**12
        )
    result = runner.read(prepared / "qualification-result.json")
    assert result["completedTurns"] == 3 and result["errorType"] == "PacingRefusal"
    assert result["activeTurn"] == "navigation/A" and result["applicationBookmarkStateDiscarded"]
    assert result["applicationBookmarkClosures"] == []
    assert not (prepared / "navigation/A").exists()
    assert runner.read(prepared / "navigation/application-bookmark-state-discarded.json")["cleared"]


@pytest.mark.parametrize("refuse_a", [False, True])
def test_real_normal_agent_six_turns_surviving_bookmark_and_all_usage_included(prepared, monkeypatch, refuse_a):
    source = runner.read(prepared / "fixture/source-bindings.json")["docx"]
    pin = {k: source[k] for k in ("documentId", "generation", "contentRevision")}
    clock, pacers, requests = Clock(), [], []
    actual_pacer = runner.Pacer
    real_owner, owned_states, owned_stores = runner.source_bookmark_turn, [], []

    @contextmanager
    def tracked_owner(state, *args, **kwargs):
        with real_owner(state, *args, **kwargs) as arguments:
            if arguments:
                owned_states.append(state)
                owned_stores.append(arguments["bookmark_store"])
            yield arguments

    monkeypatch.setattr(runner, "source_bookmark_turn", tracked_owner)

    def paced(deadline, *, policy):
        p = actual_pacer(deadline, policy=policy, clock=clock, sleep=clock.sleep)
        pacers.append(p)
        return p

    monkeypatch.setattr(runner, "Pacer", paced)

    def create(**request):
        requests.append(copy.deepcopy(request))
        user = [x["content"] for x in request["input"] if x.get("role") == "user"][-1]
        if user == runner.ACKNOWLEDGMENT:
            return response(message("Acknowledged."))
        if refuse_a and user == runner.read(prepared / "controls.json")["caseA"]["question"]:
            return response(message("I did not receive the original source and cannot verify this rule."))
        outputs = [x for x in request["input"] if x.get("type") == "function_call_output"]
        tool = next(t["name"] for t in request["tools"] if t["name"].startswith("read_documents_"))
        if not outputs:
            arguments = (
                json.loads(user.split("Retained read request:\n", 1)[1])
                if "Retained read request:" in user
                else {"mode": "outline", "documents": [pin]}
            )
        else:
            evidence = json.loads(outputs[-1]["output"])
            if evidence.get("error"):
                assert evidence["error"] == "navigation_reference_unavailable"
                assert pin["documentId"] in json.dumps(request["input"])
                arguments = {"mode": "outline", "documents": [pin]}
            elif evidence["mode"] == "selected":
                row = evidence["documents"][0]["sections"][0]
                assert "Do not sample a damaged vessel." in row["text"]
                return response(message("Do not sample a damaged vessel. (" + row["citation"] + ")"))
            else:
                rows = evidence["documents"][0]["outline"]
                chosen = next(
                    (
                        r
                        for r in rows
                        if r.get("navigationPreview", {}).get("prefix") == "Do not sample a damaged vessel."
                    ),
                    None,
                )
                action = "read" if chosen else "outline"
                chosen = chosen or next(
                    r for r in rows if r.get("heading") in {"Operating rules", "Sampling", "Exceptions"}
                )
                arguments = (
                    {"reference": chosen["actions"][action]} if "actions" in chosen else chosen[action + "Selector"]
                )
        n = len(requests)
        item = call(f"c{n}")
        item.update(name=tool, arguments=json.dumps(arguments))
        return response(reasoning(f"rs{n}"), item)

    result = runner.run_qualification(
        prepared,
        provider=SimpleNamespace(max_retries=0, responses=SimpleNamespace(create=create)),
        deployment="chosen",
        binding=binding(),
        deadline=5000,
    )
    assert result["completedTurns"] == 6 and result["completedSourceTurns"] == 4
    expected_calls = 16 if refuse_a else 18
    assert len(pacers) == 1 and result["provider"]["physicalResponsesAttempts"] == len(requests) == expected_calls
    assert result["provider"]["knownInputTokens"] == 100 * expected_calls
    assert result["provider"]["knownOutputTokens"] == 50 * expected_calls
    for cell in runner.CELLS:
        a = runner.read(prepared / cell / "A/turn.json")
        ack = runner.read(prepared / cell / "acknowledgment/turn.json")
        b = runner.read(prepared / cell / "B/turn-input.json")
        assert [m["role"] for m in b["history"]] == ["user", "assistant", "user", "assistant"]
        assert b["history"][1]["content"] == a["answer"] and b["history"][3]["content"] == ack["answer"]
        assert all("tool_calls" not in m for m in b["history"])
        assert "Acknowledged." in b["history"][3]["content"]
        assert runner.read(prepared / cell / "retained-bookmark.json")["originalSourceBookmark"] is (not refuse_a)
    nav = runner.read(prepared / "navigation/B/turn.json")
    errors = [
        json.loads(m["content"])
        for m in nav["messages"]
        if m["role"] == "tool" and json.loads(m["content"]).get("error")
    ]
    assert errors == []
    assert runner.read(prepared / "navigation/B/presentation-diagnostics.json")["closed"]
    assert len(runner.read(prepared / "navigation/B/presentation-diagnostics.json")["resolutions"]) == (
        5 if refuse_a else 1
    )
    assert result["usageByPresentation"]["compact"]["logicalCallsStarted"] == (8 if refuse_a else 9)
    assert result["usageByPresentation"]["navigation"]["logicalCallsStarted"] == (8 if refuse_a else 9)
    assert result["applicationBookmarkClosures"] == [
        {"cell": "navigation", "stage": stage, "closed": True, "authorityKeyPersisted": False}
        for stage in runner.STAGES
    ]
    assert result["applicationBookmarkStateDiscarded"] is True
    assert len(owned_states) == len(owned_stores) == 3
    assert all(state is owned_states[0] and state == {} for state in owned_states)
    assert len({id(store) for store in owned_stores}) == 3
    assert all(store.closed and store._authority == b"" and store._entries == {} for store in owned_stores)
    snapshots = [
        runner.read(prepared / "navigation" / stage / f"application-bookmarks-{when}.json")
        for stage in runner.STAGES
        for when in ("before", "after")
    ]
    first = snapshots[0]["payload"]
    for snapshot in snapshots:
        assert set(snapshot) == {"schema", "payload", "signature"}
        p = snapshot["payload"]
        assert {k: p[k] for k in ("binding", "createdAt", "expiresAt")} == {
            k: first[k] for k in ("binding", "createdAt", "expiresAt")
        }
        assert p["expiresAt"] - p["createdAt"] == 86400 and p["maxEntries"] == 32
        assert "authority" not in json.dumps(snapshot).lower()
        assert "Do not sample a damaged vessel." not in json.dumps(snapshot)
    assert snapshots[1] == snapshots[2] == snapshots[3] == snapshots[4]
    if not refuse_a:
        mark = runner.read(prepared / "navigation/retained-bookmark.json")
        token = mark["readRequest"]["bookmark"]
        assert [entry["token"] for entry in snapshots[1]["payload"]["entries"]] == [token]
        for stage in ("acknowledgment", "B"):
            first_input = runner.read(prepared / "navigation" / stage / "model-input-01.json")
            system = "\n".join(m["content"] for m in first_input["messages"] if m["role"] == "system")
            assert "Saved source selection metadata (not fresh source evidence)" in system
            assert token in system
            assert "Do not sample a damaged vessel." not in system


@pytest.mark.parametrize("failure", ["stale", "dirty", "postcheck"])
def test_execution_admission_close_postcheck_and_archive_preserve_primary_error(
    prepared, tmp_path, monkeypatch, failure
):
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
        lambda args, **kw: (" M x" if failure == "dirty" else "") if "status" in args else "a" * 40,
    )
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    events = []

    def bound(root, role):
        events.append(role)
        if role == "after":
            raise ValueError("secondary postcheck")
        return binding()

    def failed(*a, **kw):
        events.append("model")
        raise PacingRefusal("primary guard")

    monkeypatch.setattr(runner, "current_binding", bound)
    monkeypatch.setattr(runner, "run_qualification", failed)
    monkeypatch.setattr(runner, "actual_client", lambda endpoint: SimpleNamespace(close=lambda: events.append("close")))
    if failure != "postcheck":
        with pytest.raises(ValueError):
            runner.execute(prepared, tmp_path / "archive")
        assert not events
    else:
        with pytest.raises(PacingRefusal, match="primary"):
            runner.execute(prepared, tmp_path / "archive")
        assert events == ["before", "model", "close", "after"]
        result = runner.read(prepared / "execution-result.json")
        assert result["providerClosed"] and not result["postchecks"]["passed"]
        assert (tmp_path / "archive/execution-result.json").exists()


def test_compact_A_messages_and_tools_equal_frozen_baseline_normal_adapter(prepared, monkeypatch):
    """Execute old/new adapters on the same local publication and chosen route."""
    import subprocess
    import sys
    from types import ModuleType

    from fli_demo.agent import Reply, ToolCall

    previous = ModuleType("fli_demo._frozen_compact_qualification")
    previous.__package__ = "fli_demo"
    monkeypatch.setitem(sys.modules, previous.__name__, previous)
    repo = __import__("pathlib").Path(__file__).resolve().parents[1]
    code = subprocess.check_output(["git", "show", runner.BASELINE + ":src/fli_demo/agent.py"], cwd=repo, text=True)
    exec(compile(code, "frozen_compact_agent.py", "exec"), previous.__dict__)
    controls = runner.verify(prepared)
    source = controls["caseA"]["sourceBinding"]
    pin = {k: source[k] for k in ("documentId", "generation", "contentRevision")}
    captures = []
    for invoke in (previous.run_turn, runner.run_turn):
        observed = []

        def chat(messages, tools, observed=observed):
            observed.append(copy.deepcopy({"messages": messages, "tools": tools}))
            tool = next(t["function"]["name"] for t in tools if t["function"]["name"].startswith("read_documents_"))
            if len(observed) == 1:
                request = {"mode": "outline", "documents": [pin]}
            else:
                payload = json.loads(messages[-1]["content"])
                document = payload["documents"][0]
                if payload["mode"] == "selected":
                    return Reply("Do not sample a damaged vessel. (" + document["sections"][0]["citation"] + ")")
                rows = document["outline"]
                target = next(
                    (
                        r
                        for r in rows
                        if r.get("navigationPreview", {}).get("prefix") == "Do not sample a damaged vessel."
                    ),
                    None,
                )
                if target:
                    request = target["readSelector"]
                else:
                    request = next(
                        r for r in rows if r.get("heading") in {"Operating rules", "Sampling", "Exceptions"}
                    )["outlineSelector"]
            return Reply(None, [ToolCall("read-" + str(len(observed)), tool, json.dumps(request))])

        with runner.local_index(prepared) as index:
            invoke(
                chat,
                index,
                {
                    "id": "reference-qualification",
                    "libraryIds": [source["libraryId"]],
                    "instructions": runner.AGENT_INSTRUCTIONS,
                },
                [],
                controls["caseA"]["question"],
                context_token_budget=runner.CONTEXT,
                max_rounds=12,
                image_inputs=False,
            )
        captures.append(observed)
    assert len(captures[0]) == len(captures[1]) == 6
    assert captures[0] == captures[1]
