"""The index's own permission rules (spec §5 rules 2 and 3).

The platform decides who may use which library and puts a library's id into only the tools
it should. The index checks two immutable facts as a backstop:

* a personal library is bound to one agent for life, and answers only that agent;
* ``kind`` and ``boundAgentId`` never change after creation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fli.errors import ApiError, library_not_found

IMMUTABLE_LIBRARY_FIELDS = ("kind", "boundAgentId")

security_log = logging.getLogger("fli.security")


def check_search_allowed(library_id: str, library: Mapping[str, Any] | None, agent_id: str | None) -> None:
    """Raise unless ``agent_id`` may search ``library``. Never log query text (CLAUDE.md rule 12)."""
    if not agent_id or not agent_id.strip():
        raise ApiError(400, "missing_agent_id", "the X-Agent-Id header is required on every search")
    if library is None or library.get("status") == "deleting":
        raise library_not_found(library_id)
    if library.get("kind") == "personal" and library.get("boundAgentId") != agent_id:
        security_log.warning(
            "library_not_bound_to_agent",
            extra={"event": "library_not_bound_to_agent", "libraryId": library_id, "agentId": agent_id},
        )
        raise ApiError(403, "library_not_bound_to_agent", "this personal library belongs to another agent")


def check_immutable(update: Mapping[str, Any]) -> None:
    """Refuse any update that names ``kind`` or ``boundAgentId``, even with the current value."""
    touched = [f for f in IMMUTABLE_LIBRARY_FIELDS if f in update]
    if touched:
        raise ApiError(400, "immutable_field", f"{', '.join(touched)} cannot be changed after creation")
