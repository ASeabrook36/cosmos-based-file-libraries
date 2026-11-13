"""Create an isolated split/unsplit database pair, preserving every other resource.

All configuration, ownership receipts and generated policies remain in private paths.
Only acknowledged creations in the same receipt may be resumed. This command has no
cleanup operation; retiring the disposable databases is a separate operator decision.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.canary import definition_mismatches  # noqa: E402
from scripts.retrieval_r0_corpus import private_path  # noqa: E402

API_VERSION = "2025-10-15"
ARM_ORIGIN = "https://management.azure.com"
ARMS = ("split", "unsplit")
CONTAINERS = ("libraries", "documents", "chunks")
ACCOUNT_ID = re.compile(
    r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/?#]+/providers/"
    r"Microsoft\.DocumentDB/databaseAccounts/[a-z0-9]+(?:-[a-z0-9]+)*",
    re.IGNORECASE,
)
DATABASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,119}")


class ProvisionError(RuntimeError):
    """An error safe to print without private connection settings."""


def chunks_definition(arm: str) -> dict[str, Any]:
    if arm not in ARMS:
        raise ValueError("unknown arm")
    definition = json.loads((REPO / "cosmos" / "chunks.container.json").read_text())
    if arm == "unsplit":
        del definition["indexingPolicy"]["vectorIndexes"][0]["vectorIndexShardKey"]
    return definition


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_private(path: Path, value: Any) -> None:
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".provision-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    account = config.get("cosmosAccountResourceId", "")
    if not isinstance(account, str) or not ACCOUNT_ID.fullmatch(account):
        raise ProvisionError("private config must identify exactly one existing Cosmos account")
    arms = config.get("arms")
    if not isinstance(arms, dict) or set(arms) != set(ARMS):
        raise ProvisionError("private config must contain split and unsplit arms")
    names = []
    for arm in ARMS:
        name = arms[arm].get("database") if isinstance(arms[arm], dict) else None
        if not isinstance(name, str) or not DATABASE_NAME.fullmatch(name):
            raise ProvisionError(f"{arm}: database must be a safe, explicit disposable name")
        if name.lower() in {"fli", "fli-scale"}:
            raise ProvisionError(f"{arm}: production and historical database names are reserved")
        names.append(name)
    if names[0].lower() == names[1].lower():
        raise ProvisionError("split and unsplit databases must differ")
    return {"cosmosAccountResourceId": account, "arms": {arm: {"database": arms[arm]["database"]} for arm in ARMS}}


def targets(config: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for arm in ARMS:
        database = config["arms"][arm]["database"]
        resource_id = f"{config['cosmosAccountResourceId']}/sqlDatabases/{database}"
        result.append({"arm": arm, "kind": "database", "resourceId": resource_id, "definition": {"id": database}})
        for name in CONTAINERS:
            definition = (
                chunks_definition(arm)
                if name == "chunks"
                else json.loads((REPO / "cosmos" / f"{name}.container.json").read_text())
            )
            result.append(
                {"arm": arm, "kind": name, "resourceId": f"{resource_id}/containers/{name}", "definition": definition}
            )
    return result


def assert_owned(target: dict[str, Any], receipt: dict[str, Any], exists: bool) -> None:
    entry = receipt.get("resources", {}).get(target["resourceId"], {})
    if exists and not entry.get("createdByThisRun"):
        raise ProvisionError(f"{target['arm']} {target['kind']}: target already exists without this run's ownership")
    if entry and entry.get("definitionSha256") != digest(target["definition"]):
        raise ProvisionError(f"{target['arm']} {target['kind']}: saved policy differs; refusing changes")
    if not exists and entry.get("status") == "verified":
        raise ProvisionError(f"{target['arm']} {target['kind']}: a verified resource disappeared; refusing replacement")


def verify_resource(target: dict[str, Any], body: dict[str, Any]) -> None:
    actual = body.get("properties", {}).get("resource")
    problems = definition_mismatches(target["definition"], actual)
    if target["kind"] == "chunks" and target["arm"] == "unsplit" and isinstance(actual, dict):
        if any("vectorIndexShardKey" in index for index in actual.get("indexingPolicy", {}).get("vectorIndexes", [])):
            problems.append("unexpected shard key")
    if problems:
        raise ProvisionError(f"{target['arm']} {target['kind']}: stored definition differs from expected policy")


class Management:
    def __init__(self, client: Any, credential: Any):
        self.client, self.credential = client, credential

    async def request(self, method: str, resource: str, **kwargs: Any) -> Any:
        if method not in {"GET", "PUT"}:
            raise ProvisionError("unsupported management operation")
        if resource.startswith("/"):
            url = f"{ARM_ORIGIN}{resource}?api-version={API_VERSION}"
        else:
            parsed = urlsplit(resource)
            if parsed.scheme != "https" or parsed.netloc != "management.azure.com" or parsed.fragment:
                raise ProvisionError("management operation returned an unexpected polling origin")
            url = resource
        token = await self.credential.get_token(f"{ARM_ORIGIN}/.default")
        headers = {"Authorization": f"Bearer {token.token}"} | kwargs.pop("headers", {})
        try:
            return await self.client.request(method, url, headers=headers, **kwargs)
        except Exception as exc:
            raise ProvisionError(
                "management transport failed; inspect private ownership state before retrying"
            ) from exc

    async def get(self, resource_id: str) -> dict[str, Any] | None:
        response = await self.request("GET", resource_id)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ProvisionError(f"management read failed with HTTP {response.status_code}")
        return response.json()


async def wait_created(
    management: Management,
    target: dict[str, Any],
    entry: dict[str, Any],
    *,
    timeout: float = 900,
    sleep: Any = asyncio.sleep,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    deadline = clock() + timeout
    operation = entry.get("operationUrl")
    operation_done = not operation
    while True:
        if operation and not operation_done:
            response = await management.request("GET", operation)
            if response.status_code not in {200, 202}:
                raise ProvisionError(f"{target['arm']} {target['kind']}: operation poll HTTP {response.status_code}")
            result = response.json() if response.content else {}
            status = str(result.get("status", "")).lower()
            if status in {"failed", "canceled", "cancelled"}:
                raise ProvisionError(f"{target['arm']} {target['kind']}: asynchronous create failed")
            operation_done = status == "succeeded" or (
                response.status_code == 200 and isinstance(result.get("properties", {}).get("resource"), dict)
            )
        if operation_done:
            body = await management.get(target["resourceId"])
            if body is not None:
                state = str(body.get("properties", {}).get("provisioningState", "succeeded")).lower()
                if state in {"failed", "canceled", "cancelled"}:
                    raise ProvisionError(f"{target['arm']} {target['kind']}: resource provisioning failed")
                if state == "succeeded":
                    verify_resource(target, body)
                    return body
        if clock() >= deadline:
            raise ProvisionError(f"{target['arm']} {target['kind']}: creation wait timed out; resources retained")
        await sleep(min(5, max(0, deadline - clock())))


async def provision(config: dict[str, Any], state: Path, management: Management) -> dict[str, Any]:
    config = validate_config(config)
    state = private_path(state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt_path = state / "provisioning-private.json"
    plan = targets(config)
    binding = digest({"config": config, "policies": [target["definition"] for target in plan]})
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("bindingSha256") != binding or receipt.get("schema") != "fli.scale-ablation.provision.v1":
            raise ProvisionError("private state belongs to a different configuration or policy")
    else:
        receipt = {
            "schema": "fli.scale-ablation.provision.v1",
            "runId": str(uuid.uuid4()),
            "bindingSha256": binding,
            "config": config,
            "startedAt": now(),
            "resources": {},
        }
        write_private(receipt_path, receipt)
    for arm in ARMS:
        write_private(state / f"{arm}-chunks.container.json", chunks_definition(arm))
    account = await management.get(config["cosmosAccountResourceId"])
    if account is None or not account.get("location"):
        raise ProvisionError("existing account not found or its location is unavailable")
    # Inspect the complete pair before making any changes, including resumed scopes.
    for target in plan:
        body = await management.get(target["resourceId"])
        assert_owned(target, receipt, body is not None)
    for target in plan:
        body = await management.get(target["resourceId"])
        assert_owned(target, receipt, body is not None)
        entry = receipt["resources"].get(target["resourceId"])
        if entry is None or not entry.get("createdByThisRun"):
            entry = {
                "arm": target["arm"],
                "kind": target["kind"],
                "definitionSha256": digest(target["definition"]),
                "createdByThisRun": False,
                "status": "request-pending",
                "requestedAt": now(),
            }
            receipt["resources"][target["resourceId"]] = entry
            write_private(receipt_path, receipt)
            payload = {"location": account["location"], "properties": {"resource": target["definition"], "options": {}}}
            response = await management.request(
                "PUT", target["resourceId"], json=payload, headers={"If-None-Match": "*"}
            )
            entry["httpStatus"] = response.status_code
            if response.status_code not in {200, 201, 202}:
                entry["status"] = "request-failed"
                write_private(receipt_path, receipt)
                raise ProvisionError(
                    f"{target['arm']} {target['kind']}: create failed with HTTP {response.status_code}"
                )
            entry.update({"createdByThisRun": True, "status": "accepted", "acceptedAt": now()})
            operation = response.headers.get("azure-asyncoperation") or response.headers.get("location")
            if operation:
                entry["operationUrl"] = operation
            write_private(receipt_path, receipt)
            if response.status_code == 202 and not operation:
                raise ProvisionError(f"{target['arm']} {target['kind']}: accepted create omitted polling URL")
        if entry["status"] == "verified":
            verify_resource(target, body)
        else:
            await wait_created(management, target, entry)
            entry.update({"status": "verified", "verifiedAt": now()})
            write_private(receipt_path, receipt)
        print(f"{target['arm']} {target['kind']}: created and verified", flush=True)
    receipt["completedAt"] = now()
    write_private(receipt_path, receipt)
    return receipt


async def run(config_path: Path, state: Path) -> None:
    import httpx
    from azure.identity.aio import DefaultAzureCredential

    config = json.loads(private_path(config_path).read_text())
    logging.getLogger("azure").setLevel(logging.CRITICAL)
    async with DefaultAzureCredential() as credential, httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        await provision(config, state, Management(client, credential))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        asyncio.run(run(args.config, args.state))
    except (ProvisionError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, ProvisionError) else "private configuration or state could not be read"
        print(f"provisioning stopped: {message}", file=sys.stderr)
        return 1
    except Exception:
        print("provisioning stopped: authentication or runtime failure; resources retained", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
