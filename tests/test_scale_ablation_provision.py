"""Provisioning guards against fakes; these tests never authenticate or create Azure resources."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from scripts.scale_ablation_provision import (
    ARMS,
    REPO,
    ProvisionError,
    chunks_definition,
    provision,
    targets,
    validate_config,
    wait_created,
)


def config():
    return {
        "cosmosAccountResourceId": (
            "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-group/"
            "providers/Microsoft.DocumentDB/databaseAccounts/example-account"
        ),
        "arms": {"split": {"database": "example-ablation-split"}, "unsplit": {"database": "example-ablation-unsplit"}},
    }


class FakeManagement:
    def __init__(self, settings):
        self.resources = {settings["cosmosAccountResourceId"]: {"location": "example-region"}}
        self.puts = []

    async def get(self, resource_id):
        return self.resources.get(resource_id)

    async def request(self, method, resource_id, **kwargs):
        assert method == "PUT" and "/sqlDatabases/" in resource_id
        assert kwargs["headers"] == {"If-None-Match": "*"}
        assert resource_id not in self.resources
        body = copy.deepcopy(kwargs["json"])
        self.puts.append((resource_id, body))
        self.resources[resource_id] = body
        return SimpleNamespace(status_code=200, headers={})


def test_policies_differ_only_by_shard_key_and_keep_canonical_source_unchanged():
    canonical = json.loads((REPO / "cosmos" / "chunks.container.json").read_text())
    split, unsplit = (chunks_definition(arm) for arm in ARMS)
    assert split == canonical
    shard = split["indexingPolicy"]["vectorIndexes"][0].pop("vectorIndexShardKey")
    assert shard == ["/libraryId"] and split == unsplit
    assert chunks_definition("split") == canonical
    with pytest.raises(ValueError, match="unknown arm"):
        chunks_definition("other")


@pytest.mark.parametrize("name", ["fli", "fli-scale", "../scope", "scope?x=1", "scope/child"])
def test_reserved_and_unsafe_database_names_are_refused(name):
    settings = config()
    settings["arms"]["split"]["database"] = name
    with pytest.raises(ProvisionError):
        validate_config(settings)


def test_pair_preflight_refuses_unowned_resource_before_any_creation(tmp_path):
    settings = config()
    management = FakeManagement(settings)
    target = targets(settings)[4]  # The second database must be checked before the first is created.
    management.resources[target["resourceId"]] = {"properties": {"resource": target["definition"]}}
    with pytest.raises(ProvisionError, match="already exists without this run's ownership"):
        asyncio.run(provision(settings, tmp_path, management))
    assert management.puts == []


def test_creates_only_new_pair_and_resumes_without_mutation(tmp_path, capsys):
    settings = config()
    management = FakeManagement(settings)
    receipt = asyncio.run(provision(settings, tmp_path, management))
    assert len(management.puts) == 8
    assert all(entry["createdByThisRun"] and entry["status"] == "verified" for entry in receipt["resources"].values())
    assert all(body["properties"]["options"] == {} for _, body in management.puts)
    assert (tmp_path / "provisioning-private.json").stat().st_mode & 0o777 == 0o600
    for arm in ARMS:
        assert json.loads((tmp_path / f"{arm}-chunks.container.json").read_text()) == chunks_definition(arm)
    asyncio.run(provision(settings, tmp_path, management))
    assert len(management.puts) == 8
    output = capsys.readouterr().out
    assert "subscriptions" not in output and "example-account" not in output and "example-ablation" not in output


def test_resumed_resource_readback_cannot_hide_wrong_shard_or_vector_settings(tmp_path):
    settings = config()
    management = FakeManagement(settings)
    asyncio.run(provision(settings, tmp_path, management))
    target = targets(settings)[-1]
    body = management.resources[target["resourceId"]]
    body["properties"]["resource"]["indexingPolicy"]["vectorIndexes"][0]["vectorIndexShardKey"] = []
    with pytest.raises(ProvisionError, match="stored definition differs"):
        asyncio.run(provision(settings, tmp_path, management))
    assert len(management.puts) == 8


def test_disappeared_verified_scope_is_not_recreated(tmp_path):
    settings = config()
    management = FakeManagement(settings)
    asyncio.run(provision(settings, tmp_path, management))
    del management.resources[targets(settings)[-1]["resourceId"]]
    with pytest.raises(ProvisionError, match="disappeared"):
        asyncio.run(provision(settings, tmp_path, management))
    assert len(management.puts) == 8


@pytest.mark.parametrize("operation_status", ["Succeeded", "Failed", "Running"])
def test_async_creation_checks_operation_then_actual_readback_and_has_bounded_wait(operation_status):
    target = targets(config())[-1]
    elapsed = 0

    class Management:
        reads = 0

        async def request(self, method, operation):
            assert method == "GET"
            return SimpleNamespace(status_code=200, content=b"json", json=lambda: {"status": operation_status})

        async def get(self, resource_id):
            self.reads += 1
            return {"properties": {"resource": target["definition"]}}

    async def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    management = Management()
    call = wait_created(
        management,
        target,
        {"operationUrl": "https://management.azure.com/example-operation"},
        timeout=2,
        sleep=sleep,
        clock=lambda: elapsed,
    )
    if operation_status == "Succeeded":
        asyncio.run(call)
        assert management.reads == 1
    else:
        with pytest.raises(ProvisionError, match="failed" if operation_status == "Failed" else "timed out"):
            asyncio.run(call)
        assert management.reads == 0
