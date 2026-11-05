"""Demo session ownership survives prose trimming without reviving old actions."""

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fli_demo.conversation import (
    AUTHORITY,
    BLOCKED,
    CHECKPOINT,
    OWNER,
    BookmarkSessionError,
    prepare_conversation,
    source_bookmark_turn,
)

ACTOR, TARGET = "reader", "https://index.invalid/"


def prepared():
    state = {}
    prepare_conversation(state, ACTOR, TARGET, enabled=True)
    return state


def test_disabled_mode_keeps_normal_arguments_and_prose_without_bookmark_authority():
    state = {}
    prepare_conversation(state, ACTOR, TARGET)
    state["history"] = [{"role": "assistant", "content": "Prior prose."}]
    before = copy.deepcopy(state)
    with source_bookmark_turn(state, ACTOR, TARGET) as options:
        assert options == {}
    assert state == before and OWNER not in state and AUTHORITY not in state


@pytest.mark.parametrize("change", ["new_chat", "actor", "target"])
def test_explicit_new_chat_or_platform_change_rotates_authority_and_history(change):
    state = prepared()
    old_owner, old_key = copy.deepcopy(state[OWNER]), state[AUTHORITY]
    with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 100):
        pass
    state["history"] = [{"role": "assistant", "content": "Old prose."}]
    state["chat"] = [{"role": "user", "content": "Old question."}]
    state["image_bytes"] = {"old": b"image"}
    state["evidence_ledger"] = [{"old": "hint"}]
    prepare_conversation(
        state,
        "other-reader" if change == "actor" else ACTOR,
        "https://other.invalid/" if change == "target" else TARGET,
        enabled=True,
        new_chat=change == "new_chat",
    )
    assert state[OWNER]["conversationId"] != old_owner["conversationId"]
    assert state[AUTHORITY] != old_key and CHECKPOINT not in state
    assert not state["chat"] and not state["history"] and not state["image_bytes"] and not state["evidence_ledger"]


def test_disabling_opt_in_discards_authority_but_keeps_prose():
    state = prepared()
    old_key = state[AUTHORITY]
    state["history"] = [{"role": "assistant", "content": "Retained answer."}]
    prepare_conversation(state, ACTOR, TARGET, enabled=False)
    assert state["history"] and not any(k in state for k in (OWNER, AUTHORITY, CHECKPOINT, BLOCKED))
    prepare_conversation(state, ACTOR, TARGET, enabled=True)
    assert state[AUTHORITY] != old_key


def test_checkpoint_and_restore_keep_absolute_expiry_and_key_separate():
    state = prepared()
    with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 100) as options:
        first = options["bookmark_store"]
        assert options["presentation"] == "navigation"
        assert options["conversation_id"] == state[OWNER]["conversationId"]
    assert first.closed
    checkpoint = copy.deepcopy(state[CHECKPOINT])
    assert checkpoint["payload"]["createdAt"] == 100
    assert checkpoint["payload"]["expiresAt"] == 86500
    assert state[AUTHORITY].hex() not in json.dumps(checkpoint)
    with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 200) as options:
        second = options["bookmark_store"]
        assert second is not first and second.expires_at == 86500
    assert second.closed and state[CHECKPOINT] == checkpoint


@pytest.mark.parametrize("missing", [OWNER, AUTHORITY, CHECKPOINT])
def test_missing_protected_state_cannot_restart_authority_from_transcript(missing):
    state = prepared()
    with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 100):
        pass
    state["history"] = [{"role": "assistant", "content": json.dumps(state[CHECKPOINT])}]
    state.pop(missing)
    before = copy.deepcopy(state)
    prepare_conversation(state, ACTOR, TARGET, enabled=True)
    assert state == before
    with pytest.raises(BookmarkSessionError, match="Start a new chat"):
        with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 200):
            pytest.fail("lost state must not create a usable store")


def test_expiry_requires_explicit_new_owner_without_renewing_old_checkpoint():
    state = prepared()
    with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 100):
        pass
    owner, checkpoint = copy.deepcopy(state[OWNER]), copy.deepcopy(state[CHECKPOINT])
    with pytest.raises(BookmarkSessionError, match="expired"):
        with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 86500):
            pytest.fail("expiry must refuse")
    prepare_conversation(state, ACTOR, TARGET, enabled=True)
    assert state[OWNER] == owner and state[CHECKPOINT] == checkpoint and state[BLOCKED]
    prepare_conversation(state, ACTOR, TARGET, enabled=True, new_chat=True)
    with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 86501) as options:
        assert options["conversation_id"] != owner["conversationId"]
        assert options["bookmark_store"].created_at == 86501


def test_modified_signed_state_refuses_without_replacing_checkpoint():
    state = prepared()
    with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 100):
        pass
    state[CHECKPOINT]["payload"]["expiresAt"] += 3600
    tampered = copy.deepcopy(state[CHECKPOINT])
    with pytest.raises(BookmarkSessionError, match="unavailable"):
        with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 200):
            pytest.fail("invalid signature must refuse")
    assert state[BLOCKED] and state[CHECKPOINT] == tampered


def test_exception_preserves_valid_checkpoint_and_closes_transient_store():
    state = prepared()
    with pytest.raises(RuntimeError, match="model failed"):
        with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 100) as options:
            store = options["bookmark_store"]
            raise RuntimeError("model failed")
    assert store.closed and state[CHECKPOINT]["payload"]["createdAt"] == 100
    with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: 200):
        pass


def test_expiry_while_failing_does_not_mask_primary_failure_or_extend_state():
    state, now = prepared(), [100]
    with pytest.raises(RuntimeError, match="original failure"):
        with source_bookmark_turn(state, ACTOR, TARGET, enabled=True, clock=lambda: now[0]) as options:
            store = options["bookmark_store"]
            now[0] = 86501
            raise RuntimeError("original failure")
    assert store.closed and state[BLOCKED]
    assert state[CHECKPOINT]["payload"]["expiresAt"] == 86500


class Session(dict):
    def __getattr__(self, name):
        return self[name]


def page_turn(state, enabled, invoke):
    """Execute the actual page wrapper without starting Streamlit or a model."""
    page = Path(__file__).resolve().parents[1] / "demo/pages/3_Chat.py"
    node = next(n for n in ast.parse(page.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "chat_turn")
    notices = []
    namespace = {
        "st": SimpleNamespace(session_state=state, warning=notices.append),
        "source_bookmark_turn": source_bookmark_turn,
        "BookmarkSessionError": BookmarkSessionError,
        "target_of": lambda _: TARGET,
        "api": object(),
        "agent": {"id": ACTOR},
        "source_bookmarks": enabled,
        "run_turn": invoke,
        "model": lambda: "model-client",
        "names": {},
        "context_budget": 64000,
        "image_inputs": True,
        "os": SimpleNamespace(environ={}),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(page), "exec"), namespace)
    return namespace["chat_turn"], notices


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_chat_page_forwards_only_opted_in_bookmark_arguments(enabled):
    state, calls = Session(), []
    prepare_conversation(state, ACTOR, TARGET, enabled=enabled)

    def invoke(*args, **kwargs):
        calls.append((args, kwargs))
        return "turn"

    turn, notices = page_turn(state, enabled, invoke)
    assert turn("Question") == "turn" and not notices
    args, kwargs = calls[0]
    assert args[3] is state["history"]
    assert kwargs["context_token_budget"] == 64000 and kwargs["evidence_ledger"] is state["evidence_ledger"]
    if enabled:
        assert kwargs["presentation"] == "navigation"
        assert kwargs["bookmark_store"].closed
        assert kwargs["conversation_id"] == state[OWNER]["conversationId"] and CHECKPOINT in state
    else:
        assert set(kwargs) == {"context_token_budget", "image_tokens", "image_inputs", "evidence_ledger"}
        assert not any(k in state for k in (OWNER, AUTHORITY, CHECKPOINT))


def test_actual_chat_page_explains_lost_key_before_calling_model():
    state = Session(prepared())
    state.pop(AUTHORITY)
    turn, notices = page_turn(state, True, lambda *args, **kwargs: pytest.fail("must not invoke model"))
    assert turn("Continue") is None
    assert len(notices) == 1 and "Start a new chat" in notices[0]


@pytest.mark.parametrize("retention", ["shortened", "restored"])
async def test_normal_agent_reads_delivered_bookmark_after_history_trim_or_trusted_restore(retention, monkeypatch):
    from fli_demo.agent import Reply, ToolCall, run_turn
    from fli_demo.navigation_refs import target_of
    from tests.test_granular_navigation import OPENING, ready
    from tests.test_navigation_agent import read_name

    env = await ready()
    state, observed = {}, {}
    target = target_of(env.index)
    actor = {"id": ACTOR, "libraryIds": [env.lib]}
    prepare_conversation(state, ACTOR, target, enabled=True)

    def first(messages, tools):
        if messages[-1]["role"] != "tool":
            request = {"mode": "selected", "documents": [{**env.pin, "nodeIds": ["p2"]}]}
            return Reply(None, [ToolCall("read-original", read_name(tools), json.dumps(request))])
        payload = json.loads(messages[-1]["content"])
        document = payload["documents"][0]
        observed["bookmark"] = document["sourceBookmark"]["read"]
        observed["oldAction"] = document["sections"][0]["actions"]["read"]
        observed["source"] = document["sections"][0]["text"]
        return Reply(OPENING + " (procedure.docx, source paragraph 2)")

    with source_bookmark_turn(state, ACTOR, target, enabled=True, clock=lambda: 100) as options:
        a = run_turn(first, env.index, actor, [], "Read the opening rule.", image_inputs=False, **options)
    state["history"] = [
        {"role": "user", "content": "Read the opening rule."},
        {"role": "assistant", "content": a.answer},
        {"role": "user", "content": "Thanks."},
        {"role": "assistant", "content": "Understood."},
    ]
    assert len(state[CHECKPOINT]["payload"]["entries"]) == 1
    assert OPENING not in json.dumps(state[CHECKPOINT])
    checkpoint = copy.deepcopy(state[CHECKPOINT])
    if retention == "restored":
        # The signed metadata and trusted session key are separate from prose;
        # restoring the transcript alone cannot supply either binding.
        state = copy.deepcopy(state)
    state["history"] = state["history"][-2:]
    reads, seen = [], []
    original_read = env.index.read_documents

    def read_again(*args, **kwargs):
        reads.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
        return original_read(*args, **kwargs)

    monkeypatch.setattr(env.index, "read_documents", read_again)

    def followup(messages, tools):
        seen.append(copy.deepcopy(messages))
        if messages[-1]["role"] != "tool":
            return Reply(None, [ToolCall("read-bookmark", read_name(tools), json.dumps(observed["bookmark"]))])
        payload = json.loads(messages[-1]["content"])
        document = payload["documents"][0]
        assert document["sections"][0]["text"] == observed["source"]
        assert document["sections"][0]["actions"]["read"] != observed["oldAction"]
        assert payload["selectionComplete"] and not payload["documentComplete"]
        return Reply(OPENING + " (procedure.docx, source paragraph 2)")

    with source_bookmark_turn(state, ACTOR, target, enabled=True, clock=lambda: 200) as options:
        b = run_turn(
            followup,
            env.index,
            actor,
            state["history"],
            "Read the saved source again.",
            image_inputs=False,
            **options,
        )
    assert OPENING in b.answer and len(reads) == 1 and len(seen) == 2
    assert [m for m in seen[0] if m["role"] in {"user", "assistant"}][:-1] == state["history"]
    assert state[CHECKPOINT]["payload"]["expiresAt"] == checkpoint["payload"]["expiresAt"]
    assert state[CHECKPOINT]["payload"]["entries"] == checkpoint["payload"]["entries"]
