"""REST client for the index API, authenticated with the static local key (``AUTH_MODE=dev``)."""

from __future__ import annotations

import os
from typing import Any

import httpx


class IndexError_(RuntimeError):  # noqa: N801 — avoids shadowing the builtin IndexError
    """The index answered with an error body ``{"error": {"code", "message"}}``."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class IndexClient:
    def __init__(self, base_url: str, api_key: str, *, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=120)
        self.http.headers["Authorization"] = f"Bearer {api_key}"

    @classmethod
    def from_env(cls) -> IndexClient:
        key = os.environ.get("DEV_API_KEY")
        if not key:
            raise RuntimeError("DEV_API_KEY is not set")
        return cls(os.environ.get("INDEX_API_URL", "http://127.0.0.1:8000"), key)

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        if response.is_success:
            return response.json() if response.content else None
        try:
            error = response.json()["error"]
            raise IndexError_(response.status_code, error["code"], error["message"])
        except (ValueError, KeyError, TypeError):
            raise IndexError_(response.status_code, "unexpected", response.text[:200]) from None

    def list_libraries(self, kind: str | None = None) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/libraries", params={"kind": kind} if kind else None)["libraries"]

    def get_library(self, library_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/libraries/{library_id}")

    def create_library(
        self, kind: str, name: str, description: str = "", bound_agent_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": kind, "name": name, "description": description}
        if bound_agent_id:
            body["boundAgentId"] = bound_agent_id
        return self._call("POST", "/v1/libraries", json=body)

    def update_description(self, library_id: str, description: str) -> dict[str, Any]:
        return self._call("PATCH", f"/v1/libraries/{library_id}", json={"description": description})

    def update_library(self, library_id: str, **fields: Any) -> dict[str, Any]:
        """``name``, ``description``, ``autoAcceptTagProposals``: only the fields given change."""
        return self._call("PATCH", f"/v1/libraries/{library_id}", json=fields)

    def propose_description(self, library_id: str) -> bool:
        """Queue a new description proposal; it appears in the library's ``descriptionProposal``."""
        self._call("POST", f"/v1/libraries/{library_id}/description:propose")
        return True

    def upload(self, library_id: str, file_name: str, data: bytes, content_type: str | None = None) -> dict[str, Any]:
        files = {"file": (file_name, data, content_type or "application/octet-stream")}
        return self._call("POST", f"/v1/libraries/{library_id}/documents", files=files)

    def list_documents(self, library_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/v1/libraries/{library_id}/documents")["documents"]

    def replace_document(
        self, library_id: str, document_id: str, file_name: str, data: bytes, content_type: str | None = None
    ) -> dict[str, Any]:
        files = {"file": (file_name, data, content_type or "application/octet-stream")}
        return self._call("PUT", f"/v1/libraries/{library_id}/documents/{document_id}/content", files=files)

    def delete_document(self, library_id: str, document_id: str) -> None:
        self._call("DELETE", f"/v1/libraries/{library_id}/documents/{document_id}")

    def delete_library(self, library_id: str) -> None:
        self._call("DELETE", f"/v1/libraries/{library_id}")

    def tool(self, library_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/libraries/{library_id}/tool")

    def tools(self, library_id: str, agent_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/v1/libraries/{library_id}/tools", headers={"X-Agent-Id": agent_id})["tools"]

    def discover_documents(self, library_id: str, agent_id: str, **selectors: Any) -> dict[str, Any]:
        return self._call(
            "POST", f"/v1/libraries/{library_id}/documents:discover", json=selectors, headers={"X-Agent-Id": agent_id}
        )

    def read_documents(self, library_id: str, agent_id: str, **selection: Any) -> dict[str, Any]:
        return self._call(
            "POST", f"/v1/libraries/{library_id}/documents:read", json=selection, headers={"X-Agent-Id": agent_id}
        )

    def read_images(self, library_id: str, agent_id: str, **selection: Any) -> dict[str, Any]:
        return self._call(
            "POST", f"/v1/libraries/{library_id}/documents:images", json=selection, headers={"X-Agent-Id": agent_id}
        )

    def library_metrics(self, library_id: str, agent_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/libraries/{library_id}/metrics", headers={"X-Agent-Id": agent_id})

    def update_document(self, library_id: str, document_id: str, **fields: Any) -> dict[str, Any]:
        """``author``, ``periodStart``, ``periodEnd``, ``tags``, ``acceptProposals``, ``dismissProposals``.

        Only the fields given change.
        """
        return self._call("PATCH", f"/v1/libraries/{library_id}/documents/{document_id}", json=fields)

    def resolve_proposals(
        self, library_id: str, document_id: str, accept: list[str], dismiss: list[str] | None = None, **fields: Any
    ) -> dict[str, Any]:
        """Accept or dismiss proposals (``key=value``, ``period``, ``author``), with any edited fields."""
        body = {**fields, "acceptProposals": accept, "dismissProposals": dismiss or []}
        return self.update_document(library_id, document_id, **body)

    def search(
        self, library_id: str, agent_id: str, query: str, top: int = 8, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """The library id is the path's, the agent id the header's: never a model-supplied argument."""
        body: dict[str, Any] = {"query": query, "top": top, "includeNeighbors": True}
        if filters:
            body["filters"] = filters
        return self._call("POST", f"/v1/libraries/{library_id}/search", json=body, headers={"X-Agent-Id": agent_id})
