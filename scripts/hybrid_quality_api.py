"""Loopback experiment API with authenticated captures of its actual loaded runtime.

Only the coordinator starts these APIs in an announced exclusive window. All
environment/config files stay private; this wrapper provisions and ingests nothing.
"""

import argparse
import hmac
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

import fli
from fli.api import create_app
from fli.config import Settings
from fli.structure_lab import digest
from scripts.hybrid_quality_replay import api_hashes, hashes, require, sha, validate_config
from scripts.retrieval_controls import private_path
from scripts.retrieval_i1 import BINDING_KEYS


def code_revision(repo):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


async def capture_source(app, config, arm, authorization):
    import httpx

    from scripts.hybrid_quality_direct import source_snapshot

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=config["indexUrls"][arm],
        headers={"Authorization": authorization},
        timeout=120,
    ) as client:
        return await source_snapshot(client, config["libraryId"], config["agent"]["id"], app.state.searcher.chunks._c)


def add_capture_route(app, config, arm, loaded):
    """Register only on this experiment app; never alter the application routes."""

    @app.get("/__hybrid_quality/runtime")
    async def runtime(request: Request):
        current = app.state.settings
        wanted = "Bearer " + current.dev_api_key
        if not hmac.compare_digest(request.headers.get("authorization", ""), wanted):
            raise HTTPException(status_code=401, detail="development authentication required")
        require(request.client and request.client.host in {"127.0.0.1", "::1"}, "runtime capture requires loopback")
        require(
            api_hashes() == loaded["apiSourceSha256"]
            and hashes() == loaded["measurementCodeSha256"]
            and sha(Path(__file__)) == loaded["launcherSha256"],
            "loaded code differs from current disk files",
        )
        require(code_revision(Path(loaded["sourceRoot"])) == loaded["gitRevision"], "running checkout revision changed")
        require(app.state.searcher.mode == arm == current.search_mode, "actual searcher mode drift")
        frozen = await capture_source(app, config, arm, wanted)
        binding = {key: str(getattr(current, key.lower())) for key in BINDING_KEYS}
        require(
            all(getattr(current, key.lower()) is not None for key in BINDING_KEYS), "missing actual runtime setting"
        )
        return {
            "schema": "fli.hybrid-quality.api-runtime.v1",
            **loaded,
            "capturedAt": datetime.now(UTC).isoformat(),
            "indexUrl": config["indexUrls"][arm],
            "settingsBinding": binding,
            "settingsBindingSha256": digest(binding),
            "effectiveSettings": {
                "searchMode": current.search_mode,
                "searchDefaultTop": current.search_default_top,
                "searchMaxTop": current.search_max_top,
                "embeddingDimensions": current.aoai_embedding_dimensions,
                "embeddingRevision": current.aoai_embedding_model_revision,
            },
            "authenticationMode": current.auth_mode,
            "environment": current.environment,
            "processId": os.getpid(),
            "declaredCodeRevision": current.ingestion_code_revision,
            "storedRowsSha256": digest(frozen["chunks"]),
            "sourceSnapshotSha256": digest(frozen),
        }

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--arm", choices=("vector", "hybrid"), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    repo = Path(__file__).resolve().parents[1]
    require(Path(sys.prefix).resolve() == (repo / ".venv").resolve(), "use measured worktree's own virtual environment")
    source = Path(fli.__file__).resolve().parent.parent.parent
    require(source == repo, "editable source belongs to a different worktree")
    config = json.loads(private_path(args.config).read_text())
    validate_config(config)
    endpoint = urlsplit(config["indexUrls"][args.arm])
    require(endpoint.hostname == "127.0.0.1", "experiment API binds IPv4 loopback only")
    revision = code_revision(repo)
    env = json.loads(private_path(args.environment).read_text())
    env.update(SEARCH_MODE=args.arm, FLI_CODE_REVISION=revision)
    os.environ.update(env)
    settings = Settings.from_env()
    require(settings.auth_mode == "dev" and settings.environment == "local", "local development capture only")
    require(settings.ingestion_code_revision == revision, "actual declared revision differs from loaded checkout")
    loaded = {
        "apiSourceSha256": api_hashes(),
        "measurementCodeSha256": hashes(),
        "launcherSha256": sha(Path(__file__)),
        "ownEnvironmentAndImportVerified": True,
        "sourceRoot": str(source),
        "pythonPrefix": str(Path(sys.prefix).resolve()),
        "gitRevision": revision,
    }
    app = add_capture_route(create_app(settings), config, args.arm, loaded)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=endpoint.port)


if __name__ == "__main__":
    main()
