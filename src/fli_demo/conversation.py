"""Trusted demo-session ownership for opt-in, metadata-only source bookmarks.

The signing key is separate from the signed checkpoint and never enters the
conversation transcript. Only Streamlit session state owns them; this helper
does not add disk storage or infer authority from restored messages.
"""

from __future__ import annotations

import secrets
import time
from contextlib import contextmanager

OWNER = "_source_bookmark_owner"
AUTHORITY = "_source_bookmark_authority"
CHECKPOINT = "_source_bookmark_checkpoint"
BLOCKED = "_source_bookmark_blocked"


class BookmarkSessionError(ValueError):
    """The user must explicitly start a new bookmark-owning conversation."""


def _clear_bookmarks(state):
    for name in (OWNER, AUTHORITY, CHECKPOINT, BLOCKED):
        state.pop(name, None)


def prepare_conversation(state, actor_id, target, *, enabled=False, new_chat=False):
    """Rotate chat and bookmark ownership on explicit reset or platform change."""
    if new_chat or "chat" not in state or state.get("chat_agent") != actor_id or state.get("chat_target") != target:
        state.update(chat=[], chat_agent=actor_id, chat_target=target, history=[], image_bytes={}, evidence_ledger=[])
        _clear_bookmarks(state)
    if not enabled:
        _clear_bookmarks(state)
    elif OWNER not in state and not any(name in state for name in (AUTHORITY, CHECKPOINT, BLOCKED)):
        state[OWNER] = {
            "conversationId": secrets.token_hex(16),
            "actorId": actor_id,
            "target": target,
            "started": False,
        }
        state[AUTHORITY] = secrets.token_bytes(32)


@contextmanager
def source_bookmark_turn(state, actor_id, target, *, enabled=False, clock=time.time):
    """Restore trusted state per turn, checkpoint even after failures, then close.

    Absolute expiry belongs to the signed checkpoint. Losing its key, a binding
    mismatch, corrupt state or expiry cannot silently mint replacement authority.
    ``prepare_conversation(..., new_chat=True)`` explicitly starts a new owner.
    """
    if not enabled:
        yield {}
        return

    from fli_demo.source_bookmarks import SourceBookmarkError, SourceBookmarkStore

    owner, key = state.get(OWNER), state.get(AUTHORITY)
    if (
        state.get(BLOCKED)
        or not isinstance(owner, dict)
        or set(owner) != {"conversationId", "actorId", "target", "started"}
        or owner.get("actorId") != actor_id
        or owner.get("target") != target
        or not isinstance(owner.get("conversationId"), str)
        or not owner["conversationId"]
        or type(owner.get("started")) is not bool
        or owner["started"] != (CHECKPOINT in state)
        or not isinstance(key, bytes)
        or len(key) != 32
    ):
        raise BookmarkSessionError(
            "Source bookmark ownership is unavailable. Start a new chat to acquire sources again."
        )

    binding = {
        "authority_key": key,
        "conversation_id": owner["conversationId"],
        "actor_id": actor_id,
        "target": target,
        "clock": clock,
    }
    try:
        if CHECKPOINT in state:
            store = SourceBookmarkStore.restore(state[CHECKPOINT], **binding)
        else:
            store = SourceBookmarkStore(**binding)
    except (SourceBookmarkError, TypeError, ValueError):
        state[BLOCKED] = True
        raise BookmarkSessionError(
            "Source bookmarks are unavailable or expired. Start a new chat to acquire sources again."
        ) from None

    failed = False
    try:
        # Mark first use before exposing the store. A lost checkpoint must never
        # restart its absolute TTL under the same conversation authority.
        state[CHECKPOINT] = store.checkpoint()
        owner["started"] = True
        yield {"presentation": "navigation", "bookmark_store": store, "conversation_id": owner["conversationId"]}
    except BaseException:
        failed = True
        raise
    finally:
        try:
            state[CHECKPOINT] = store.checkpoint()
        except (SourceBookmarkError, TypeError, ValueError):
            state[BLOCKED] = True
            if not failed:
                raise BookmarkSessionError(
                    "Source bookmarks could not be preserved. Start a new chat to acquire sources again."
                ) from None
        finally:
            store.close()
