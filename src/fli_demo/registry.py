"""Agents and their attached libraries: the platform's data, so it lives here and never in the index (E23)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path.home() / ".fli-demo" / "state.json"
OFFICIAL, PERSONAL = "official", "personal"


class Registry:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.environ.get("DEMO_STATE_FILE") or DEFAULT_PATH)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"agents": {}}
        return json.loads(self.path.read_text())

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.path)

    def agents(self) -> list[dict[str, Any]]:
        return list(self._load()["agents"].values())

    def get(self, agent_id: str) -> dict[str, Any]:
        return self._load()["agents"][agent_id]

    def create(
        self,
        kind: str,
        name: str,
        instructions: str = "",
        personal_library_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in (OFFICIAL, PERSONAL):
            raise ValueError(f"unknown agent kind {kind!r}")
        state = self._load()
        agent = {
            "id": agent_id or str(uuid.uuid4()),
            "kind": kind,
            "name": name,
            "instructions": instructions,
            "libraryIds": [personal_library_id] if kind == PERSONAL and personal_library_id else [],
        }
        state["agents"][agent["id"]] = agent
        self._save(state)
        return agent

    def attach(self, agent_id: str, library_id: str) -> None:
        state = self._load()
        ids = state["agents"][agent_id]["libraryIds"]
        if library_id not in ids:
            ids.append(library_id)
        self._save(state)

    def detach(self, agent_id: str, library_id: str) -> None:
        state = self._load()
        agent = state["agents"][agent_id]
        agent["libraryIds"] = [i for i in agent["libraryIds"] if i != library_id]
        self._save(state)

    def delete(self, agent_id: str) -> None:
        state = self._load()
        state["agents"].pop(agent_id, None)
        self._save(state)


def create_personal_agent(registry: Registry, client: Any, name: str, instructions: str = "") -> dict[str, Any]:
    """A personal agent and its one personal library. The agent id is fixed first because the library is bound to it."""
    agent_id = str(uuid.uuid4())
    library = client.create_library("personal", f"{name} — notes", "", bound_agent_id=agent_id)
    return registry.create(PERSONAL, name, instructions, personal_library_id=library["id"], agent_id=agent_id)
