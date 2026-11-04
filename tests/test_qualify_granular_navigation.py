"""Offline qualification controls, evidence integrity and bounded local mechanics."""

import json
from types import SimpleNamespace

import pytest

from scripts import qualify_granular_navigation as q


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    # Parent integration can still edit source while focused tests run. Production
    # prepare/verify use the complete actual source inventory, tested separately.
    monkeypatch.setattr(q, "source_hashes", lambda: {"fixture.py": "a" * 64})
    state = tmp_path / "qualification"
    public = q.prepare(state)
    return state, public


def test_prepare_freezes_bounded_sources_without_cloud_or_private_identifiers(prepared):
    state, public = prepared
    root, plan = q.verify(state)
    assert root == state
    assert public["status"] == "prepared_not_executed"
    assert public["cloudCalls"] == public["modelCalls"] == 0
    assert public["liveExecutorImplemented"] is False
    assert public["limits"] == q.LIMITS
    assert str(state) not in json.dumps(public)
    assert plan["baseline"]["main"] == q.MAIN
    assert plan["baseline"]["sourceOnlyRepair"] == q.SOURCE_REPAIR
    assert plan["baseline"]["excludedContextRepair"] == q.CONTEXT_REPAIR
    assert plan["contextAccounting"] == "legacy-v1"
    assert plan["searchMode"] == "vector"
    assert plan["recipe"]["id"] == "custom-granular-v1"
    assert len(plan["sources"]) == 3
    assert set(q.FILES) == {item["path"] for item in plan["sources"]}
    assert all((state / name).stat().st_mode & 0o777 == 0o600 for name in (*q.FILES, "plan.json"))
    assert plan["livePrerequisites"] == list(q.LIVE_GATES)
    assert plan["cleanupContract"] == list(q.CLEANUP)
    assert plan["largerConfirmationLaunched"] is False


def test_authored_fixture_preserves_list_and_qualifier_boundaries(prepared):
    from fli.source_fidelity import original_blocks

    state, _ = prepared
    blocks, _ = original_blocks((state / "source.docx").read_bytes())
    cell = next(
        block for block in blocks if block["coordinate"].get("row") == 1 and block["coordinate"].get("column") == 1
    )
    assert len(cell["paragraphs"]) == 4
    assert cell["paragraphs"][1]["numbering"] is not None
    assert cell["paragraphs"][2]["numbering"] is not None
    assert cell["paragraphs"][-1]["numbering"] is None
    assert cell["paragraphs"][-1]["text"].startswith("These steps apply only")


def test_prepare_and_private_artifacts_never_overwrite(prepared):
    state, _ = prepared
    before = (state / "plan.json").read_bytes()
    with pytest.raises(ValueError, match="new private directory"):
        q.prepare(state)
    with pytest.raises(FileExistsError):
        q.save_new(state / "plan.json", {"different": True})
    assert (state / "plan.json").read_bytes() == before


@pytest.mark.parametrize("change", ["source", "code", "recipe", "limits", "context", "retrieval", "escape", "live"])
def test_verify_refuses_drift_before_any_local_or_live_run(prepared, monkeypatch, change):
    state, _ = prepared
    plan_path = state / "plan.json"
    plan = json.loads(plan_path.read_text())
    if change == "source":
        (state / "source.md").write_text("changed")
    elif change == "code":
        monkeypatch.setattr(q, "source_hashes", lambda: {"fixture.py": "b" * 64})
    elif change == "recipe":
        plan["recipe"]["chunking"]["targetTokens"] += 1
    elif change == "limits":
        plan["limits"]["modelTurns"] += 1
    elif change == "context":
        plan["contextAccounting"] = "envelope-v2"
    elif change == "retrieval":
        plan["searchMode"] = "hybrid"
    elif change == "escape":
        plan["sources"][0]["path"] = "../source.docx"
    else:
        plan["liveExecutorImplemented"] = True
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        q.verify(state)
    assert not (state / "local-started.json").exists()


def test_prepare_refuses_any_git_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(ValueError, match="outside every Git checkout"):
        q.prepare(checkout / "private")


def test_source_inventory_binds_helper_core_services_and_policies():
    hashes = q.source_hashes()
    assert hashes["scripts/qualify_granular_navigation.py"] == q.sha(q.__file__)
    assert "src/fli/worker/handlers.py" in hashes
    assert "src/fli_demo/agent.py" in hashes
    assert "cosmos/chunks.container.json" in hashes
    assert "tests/fakes.py" in hashes
    assert all(len(value) == 64 for value in hashes.values())


def test_no_live_command_can_be_launched(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", [q.__file__, "qualify-local", "--state", "unused", "--live"])
    with pytest.raises(SystemExit) as result:
        q.main()
    assert result.value.code == 2
    assert "unrecognized arguments: --live" in capsys.readouterr().err


async def test_foreign_local_message_is_retained_and_never_processed():
    seen = []

    async def handle(message):
        seen.append(message)

    queue = SimpleNamespace(sent=[("foreign", {"libraryId": "foreign", "type": "purgeLibrary"})])
    services = SimpleNamespace(queue=queue, chunks=SimpleNamespace(items={}))
    drain = q.LocalQueueCeiling(services, SimpleNamespace(handle=handle), ["owned"])
    with pytest.raises(ValueError, match="foreign local message"):
        await drain.drain()
    assert not seen and queue.sent


async def test_local_queue_ceiling_retains_unprocessed_owned_message():
    queue = SimpleNamespace(sent=[("owned", {"libraryId": "owned", "type": "purgeLibrary"})])
    services = SimpleNamespace(queue=queue, chunks=SimpleNamespace(items={}))
    drain = q.LocalQueueCeiling(services, None, ["owned"])
    drain.processed = 100
    with pytest.raises(ValueError, match="local message ceiling"):
        await drain.drain()
    assert queue.sent


async def test_local_publication_navigation_lifecycle_and_private_captures(prepared):
    state, _ = prepared
    result = await q.qualify_local(state)
    assert result["status"] == "passed" and result["live"] is False
    assert result["cloudCalls"] == result["modelCalls"] == 0
    assert result["providerUsage"] is result["cloudRequestCharge"] is None
    assert result["localCleanupPassed"]
    assert result["privateToolResponsesSha256"] == q.sha(state / "local-tool-responses.json")
    captures = json.loads((state / "local-tool-responses.json").read_text())
    assert len(captures) == result["capturedToolReplies"]
    assert any(item["role"] == "source.docx:outline" for item in captures)
    assert any(item["role"] == "source.md:selected" for item in captures)
    assert "privateToolResponsesSha256" in json.loads((state / "local-receipt.json").read_text())
    with pytest.raises(ValueError, match="single-use"):
        await q.qualify_local(state)
