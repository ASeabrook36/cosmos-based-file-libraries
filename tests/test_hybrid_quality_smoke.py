from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from scripts.hybrid_quality_smoke import cleanup_owned, item_key, same_row


def item(name, container="chunks"):
    return {
        "container": container,
        "body": {
            "id": name,
            "libraryId": "owned",
            "documentId": "doc",
            "text": "genuine private source fixture",
            "smokeOwner": "run",
        },
    }


class Container:
    def __init__(self, database, name):
        self.db, self.name = database, name

    async def read_item(self, name, *, partition_key):
        assert partition_key == (["owned", "doc"] if self.name == "chunks" else "owned")
        if name in self.db.read_errors:
            raise self.db.read_errors[name]
        row = self.db.rows.get((self.name, name))
        if row is None:
            raise ResourceNotFoundError("absent", status_code=404)
        return deepcopy(row)

    async def delete_item(self, name, *, partition_key, etag, match_condition):
        assert partition_key == (["owned", "doc"] if self.name == "chunks" else "owned")
        assert match_condition == MatchConditions.IfNotModified
        assert etag == self.db.rows[(self.name, name)]["_etag"]
        self.db.deletes.append((self.name, name))
        if self.db.conflict:
            raise HttpResponseError("etag changed", status_code=412)
        del self.db.rows[(self.name, name)]

    async def query_items(self, sql, *, parameters, partition_key):
        assert sql == "SELECT TOP 1 c.id, c.libraryId FROM c WHERE c.libraryId = @libraryId"
        assert parameters == [{"name": "@libraryId", "value": "owned"}]
        assert partition_key == ["owned"]
        self.db.queries += 1
        if self.db.query_error:
            raise self.db.query_error
        for (container, name), body in self.db.rows.items():
            if container == "chunks":
                yield {"id": name, "libraryId": body["libraryId"]}
                break


class Database:
    def __init__(self, items):
        self.rows = {(r["container"], r["body"]["id"]): {**r["body"], "_etag": "version-1"} for r in items}
        self.read_errors, self.deletes = {}, []
        self.conflict = False
        self.query_error = None
        self.queries = 0

    def get_container_client(self, name):
        return Container(self, name)


def ledger(items, attempted=None):
    return {
        "libraryId": "owned",
        "rows": items,
        "attemptedIds": attempted if attempted is not None else [r["body"]["id"] for r in items],
    }


async def test_all_rows_prechecked_before_any_delete(tmp_path):
    rows = [item("one"), item("two"), item("third")]
    database = Database(rows)
    database.read_errors["third"] = HttpResponseError("service unavailable", status_code=500)
    with pytest.raises(HttpResponseError):
        await cleanup_owned(database, tmp_path, ledger(rows), 30, sleep=AsyncMock())
    assert database.deletes == []
    assert len(database.rows) == 3


async def test_changed_row_refuses_all_deletion(tmp_path):
    rows = [item("one"), item("two")]
    database = Database(rows)
    database.rows[("chunks", "two")]["text"] = "replacement content"
    with pytest.raises(ValueError, match="row changed"):
        await cleanup_owned(database, tmp_path, ledger(rows), 30, sleep=AsyncMock())
    assert database.deletes == []


async def test_unattempted_existing_row_is_not_adopted(tmp_path):
    rows = [item("one"), item("two")]
    database = Database(rows)
    with pytest.raises(ValueError, match="unattempted row"):
        await cleanup_owned(database, tmp_path, ledger(rows, ["one"]), 30, sleep=AsyncMock())
    assert database.deletes == []


async def test_etag_change_stops_cleanup_without_retry(tmp_path):
    rows = [item("one"), item("two")]
    database = Database(rows)
    database.conflict = True
    with pytest.raises(HttpResponseError):
        await cleanup_owned(database, tmp_path, ledger(rows), 30, sleep=AsyncMock())
    assert database.deletes == [("chunks", "one")]
    assert len(database.rows) == 2


async def test_late_owned_row_fails_absence(tmp_path):
    rows = [item("one")]
    database = Database(rows)

    async def late(_seconds):
        database.rows[("chunks", "one")] = {**rows[0]["body"], "_etag": "new-late-version"}

    with pytest.raises(ValueError, match="remains after cleanup"):
        await cleanup_owned(database, tmp_path, ledger(rows), 30, sleep=late)
    assert database.deletes == [("chunks", "one")]
    assert database.rows


async def test_unknown_scoped_row_stops_without_broad_delete(tmp_path):
    rows = [item("one")]
    database = Database([*rows, item("unknown")])
    with pytest.raises(ValueError, match="unknown owned-prefix"):
        await cleanup_owned(database, tmp_path, ledger(rows), 30, sleep=AsyncMock())
    assert database.deletes == [("chunks", "one")]
    assert ("chunks", "unknown") in database.rows


async def test_failed_inventory_is_never_empty(tmp_path):
    rows = [item("one")]
    database = Database(rows)
    database.query_error = HttpResponseError("bad policy", status_code=500)
    with pytest.raises(HttpResponseError):
        await cleanup_owned(database, tmp_path, ledger(rows), 30, sleep=AsyncMock())
    assert database.queries == 1


async def test_successful_cleanup_uses_etags_and_two_independent_checks(tmp_path):
    rows = [item("lib", "libraries"), item("doc", "documents"), item("chunk")]
    database = Database(rows)
    sleep = AsyncMock()
    result = await cleanup_owned(database, tmp_path, ledger(rows), 30, sleep=sleep)
    assert result == {
        "expectedRows": 3,
        "deletedRows": 3,
        "point404Count": 3,
        "scopedRows": 0,
        "latePoint404Count": 3,
        "lateScopedRows": 0,
        "lateRecheckSeconds": 30,
    }
    assert database.queries == 2
    sleep.assert_awaited_once_with(30)
    assert not database.rows


async def test_partial_write_unknown_result_can_be_recovered_only_when_payload_matches(tmp_path):
    rows = [item("one"), item("two")]
    database = Database(rows[:1])
    result = await cleanup_owned(database, tmp_path, ledger(rows, ["one"]), 30, sleep=AsyncMock())
    assert result["deletedRows"] == 1
    assert result["point404Count"] == 2


def test_exact_row_validation_preserves_payload_fields():
    expected = item("one")["body"]
    assert same_row({**expected, "_etag": "provider-added"}, expected)
    assert not same_row({**expected, "smokeOwner": "somebody-else"}, expected)
    assert item_key(item("one")) == ["owned", "doc"]
    assert item_key(item("one", "documents")) == "owned"
