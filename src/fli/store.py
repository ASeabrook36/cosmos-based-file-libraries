"""Library and document records in Cosmos (spec §4.1, §4.2).

Two implementations of one small interface: ``Cosmos*`` for real accounts and ``Memory*``
for tests and local experiments. Each method is one Cosmos operation, so its cost is visible:
point reads by id and partition key, single-partition queries for a library's documents, and
one cross-partition query for the platform's library list (rare, and over a small container).

Point reads take an optional ``raw_response_hook`` so search can meter them with its other
Cosmos calls (spec §6.3 ``usage``).

Replacements are conditional on the record's ``_etag``. A concurrent writer makes the replace
raise ``Conflict``; callers re-read and re-apply their change.

Chunks are written by the worker and read by search; they are not here.
"""

from __future__ import annotations

import copy
import itertools
import re
from collections.abc import Callable
from typing import Any, Protocol

from fli.query import RenderedQuery

Record = dict[str, Any]
ResponseHook = Callable[[Any], None]  # azure-core raw_response_hook: called with each pipeline response

SYSTEM_FIELDS = ("_rid", "_self", "_etag", "_attachments", "_ts")


class Conflict(Exception):
    """The record changed since it was read (etag mismatch), or already exists on create."""


def public(record: Record) -> Record:
    """A record without Cosmos's system fields, for API responses."""
    return {k: v for k, v in record.items() if k not in SYSTEM_FIELDS}


class LibraryStore(Protocol):
    async def get(self, library_id: str, *, raw_response_hook: ResponseHook | None = None) -> Record | None: ...
    async def create(self, record: Record) -> Record: ...
    async def replace(self, record: Record) -> Record: ...
    async def list(self, *, kind: str | None, status: str | None) -> list[Record]: ...
    async def delete(self, library_id: str) -> None: ...


class DocumentStore(Protocol):
    async def discover(self, library_id: str, query: RenderedQuery, cost: ResponseHook) -> list[Record]: ...
    async def get(
        self, library_id: str, document_id: str, *, raw_response_hook: ResponseHook | None = None
    ) -> Record | None: ...
    async def create(self, record: Record) -> Record: ...
    async def replace(self, record: Record) -> Record: ...
    async def list(self, library_id: str, *, status: str | None, facet: str | None) -> list[Record]: ...
    async def delete(self, library_id: str, document_id: str) -> None: ...


def library_list_query(kind: str | None, status: str | None) -> tuple[str, list[dict[str, Any]]]:
    clauses, params = [], []
    if kind is not None:
        clauses.append("c.kind = @kind")
        params.append({"name": "@kind", "value": kind})
    if status is not None:
        clauses.append("c.status = @status")
        params.append({"name": "@status", "value": status})
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return f"SELECT * FROM c{where}", params


def document_list_query(library_id: str, status: str | None, facet: str | None) -> tuple[str, list[dict[str, Any]]]:
    clauses = ["c.libraryId = @libraryId"]
    params: list[dict[str, Any]] = [{"name": "@libraryId", "value": library_id}]
    if status is not None:
        clauses.append("c.status = @status")
        params.append({"name": "@status", "value": status})
    if facet is not None:
        clauses.append("ARRAY_CONTAINS(c.facets, @facet)")
        params.append({"name": "@facet", "value": facet})
    return f"SELECT * FROM c WHERE {' AND '.join(clauses)}", params


class _CosmosContainer:
    def __init__(self, container: Any) -> None:  # azure.cosmos.aio.ContainerProxy
        self._c = container

    async def read(self, item_id: str, partition_key: str, hook: ResponseHook | None = None) -> Record | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        metering = {"raw_response_hook": hook} if hook else {}
        try:
            return await self._c.read_item(item_id, partition_key=partition_key, **metering)
        except CosmosResourceNotFoundError:
            return None

    async def create(self, record: Record) -> Record:
        from azure.cosmos.exceptions import CosmosResourceExistsError

        try:
            return await self._c.create_item(record)
        except CosmosResourceExistsError as exc:
            raise Conflict(record["id"]) from exc

    async def replace(self, record: Record) -> Record:
        from azure.core import MatchConditions
        from azure.cosmos.exceptions import CosmosAccessConditionFailedError

        body = public(record)
        try:
            return await self._c.replace_item(
                record["id"], body, etag=record.get("_etag"), match_condition=MatchConditions.IfNotModified
            )
        except CosmosAccessConditionFailedError as exc:
            raise Conflict(record["id"]) from exc

    async def delete(self, item_id: str, partition_key: str) -> None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            await self._c.delete_item(item_id, partition_key=partition_key)
        except CosmosResourceNotFoundError:  # already gone: a retried purge
            pass

    async def query(
        self, sql: str, params: list[dict[str, Any]], partition_key: str | None, hook: ResponseHook | None = None
    ) -> list[Record]:
        metering = {"raw_response_hook": hook} if hook else {}
        items = self._c.query_items(sql, parameters=params, partition_key=partition_key, **metering)
        return [item async for item in items]


class CosmosLibraryStore:
    def __init__(self, container: Any) -> None:
        self._c = _CosmosContainer(container)

    async def get(self, library_id: str, *, raw_response_hook: ResponseHook | None = None) -> Record | None:
        return await self._c.read(library_id, library_id, raw_response_hook)

    async def create(self, record: Record) -> Record:
        return await self._c.create(record)

    async def replace(self, record: Record) -> Record:
        return await self._c.replace(record)

    async def list(self, *, kind: str | None, status: str | None) -> list[Record]:
        sql, params = library_list_query(kind, status)
        return await self._c.query(sql, params, None)  # cross-partition: the attach screen only

    async def delete(self, library_id: str) -> None:
        await self._c.delete(library_id, library_id)


class CosmosDocumentStore:
    def __init__(self, container: Any) -> None:
        self._c = _CosmosContainer(container)

    async def discover(self, library_id: str, query: RenderedQuery, cost: ResponseHook) -> list[Record]:
        return await self._c.query(query.sql, query.parameters, library_id, cost)

    async def get(
        self, library_id: str, document_id: str, *, raw_response_hook: ResponseHook | None = None
    ) -> Record | None:
        return await self._c.read(document_id, library_id, raw_response_hook)

    async def create(self, record: Record) -> Record:
        return await self._c.create(record)

    async def replace(self, record: Record) -> Record:
        return await self._c.replace(record)

    async def list(self, library_id: str, *, status: str | None, facet: str | None) -> list[Record]:
        sql, params = document_list_query(library_id, status, facet)
        return await self._c.query(sql, params, library_id)

    async def delete(self, library_id: str, document_id: str) -> None:
        await self._c.delete(document_id, library_id)


class _Memory:
    """Records keyed by (partition, id), with Cosmos-like etags."""

    _etags = itertools.count(1)

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], Record] = {}

    def _stamp(self, record: Record) -> Record:
        stored = copy.deepcopy(public(record))
        stored["_etag"] = f'"{next(self._etags)}"'
        return stored

    def read(self, pk: str, item_id: str) -> Record | None:
        item = self.items.get((pk, item_id))
        return copy.deepcopy(item) if item is not None else None

    def create(self, pk: str, record: Record) -> Record:
        if (pk, record["id"]) in self.items:
            raise Conflict(record["id"])
        self.items[(pk, record["id"])] = self._stamp(record)
        return self.read(pk, record["id"])  # type: ignore[return-value]

    def replace(self, pk: str, record: Record) -> Record:
        current = self.items.get((pk, record["id"]))
        if current is None or current["_etag"] != record.get("_etag"):
            raise Conflict(record["id"])
        self.items[(pk, record["id"])] = self._stamp(record)
        return self.read(pk, record["id"])  # type: ignore[return-value]


class MemoryLibraryStore:
    def __init__(self) -> None:
        self.m = _Memory()

    async def get(self, library_id: str, *, raw_response_hook: ResponseHook | None = None) -> Record | None:
        return self.m.read(library_id, library_id)

    async def create(self, record: Record) -> Record:
        return self.m.create(record["libraryId"], record)

    async def replace(self, record: Record) -> Record:
        return self.m.replace(record["libraryId"], record)

    async def list(self, *, kind: str | None, status: str | None) -> list[Record]:
        return [
            copy.deepcopy(r)
            for r in self.m.items.values()
            if (kind is None or r.get("kind") == kind) and (status is None or r.get("status") == status)
        ]

    async def delete(self, library_id: str) -> None:
        self.m.items.pop((library_id, library_id), None)


class MemoryDocumentStore:
    def __init__(self) -> None:
        self.m = _Memory()

    async def discover(self, library_id: str, query: RenderedQuery, cost: ResponseHook) -> list[Record]:
        params = {p["name"]: p["value"] for p in query.parameters}

        def selected(doc: Record) -> bool:
            if doc.get("status") == "deleting" or doc["id"] <= params.get("@after", ""):
                return False
            for prefix, field in (("au", "author"), ("ft", "fileType"), ("name", "fileName")):
                values = [v for k, v in params.items() if re.fullmatch(rf"@{prefix}\d+", k)]
                if prefix == "name":
                    values = [v.casefold() for v in values]
                    value = (doc.get(field) or "").casefold()
                else:
                    value = doc.get(field)
                if values and value not in values:
                    return False
            if params.get("@contains", "").casefold() not in (doc.get("fileName") or "").casefold():
                return False
            if any(v not in doc.get("facets", []) for k, v in params.items() if re.fullmatch(r"@tag\d+", k)):
                return False
            if "@pFrom" in params and not (
                doc.get("periodStart")
                and doc.get("periodEnd")
                and doc["periodStart"] <= params["@pTo"]
                and doc["periodEnd"] >= params["@pFrom"]
            ):
                return False
            for lo, hi, field in (("@aFrom", "@aTo", "authoredAt"), ("@uFrom", "@uTo", "uploadedAt")):
                if lo in params and not (doc.get(field) and params[lo] <= doc[field] < params[hi]):
                    return False
            return True

        rows = [copy.deepcopy(d) for (lib, _), d in self.m.items.items() if lib == library_id and selected(d)]
        return sorted(rows, key=lambda d: d["id"])[: int(query.sql.split()[2])]

    async def get(
        self, library_id: str, document_id: str, *, raw_response_hook: ResponseHook | None = None
    ) -> Record | None:
        return self.m.read(library_id, document_id)

    async def create(self, record: Record) -> Record:
        return self.m.create(record["libraryId"], record)

    async def replace(self, record: Record) -> Record:
        return self.m.replace(record["libraryId"], record)

    async def list(self, library_id: str, *, status: str | None, facet: str | None) -> list[Record]:
        return [
            copy.deepcopy(r)
            for (pk, _), r in self.m.items.items()
            if pk == library_id
            and (status is None or r.get("status") == status)
            and (facet is None or facet in r.get("facets", []))
        ]

    async def delete(self, library_id: str, document_id: str) -> None:
        self.m.items.pop((library_id, document_id), None)
