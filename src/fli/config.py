"""Configuration from the environment (spec §10).

Only the guards live here in Phase 0. Endpoints are read but not required yet, because the
API and worker do nothing with them until Phase 1.

Managed identity only (CLAUDE.md rule 13): there is no setting for a key or connection
string to any Azure data plane. ``DEV_API_KEY`` authenticates callers of this API in local
development and is refused anywhere else.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Environment = Literal["local", "demo", "prod"]
AuthMode = Literal["entra", "dev"]

ENVIRONMENTS: tuple[Environment, ...] = ("local", "demo", "prod")
AUTH_MODES: tuple[AuthMode, ...] = ("entra", "dev")
# Recorded on libraries and chunks for rebuilds (spec §4.1, E16). The deployment name is a setting;
# the model behind it is fixed by the vector policy.
EMBEDDING_MODEL = "text-embedding-3-large"


class ConfigError(RuntimeError):
    pass


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    environment: Environment
    auth_mode: AuthMode = "entra"
    dev_api_key: str | None = None
    api_audience: str | None = None
    api_tenant_id: str | None = None
    caller_app_role: str = "Platform.Caller"
    cosmos_endpoint: str | None = None
    cosmos_database: str = "fli"
    blob_account_url: str | None = None
    blob_container: str = "files"
    servicebus_namespace: str | None = None
    servicebus_queue: str = "ingest"
    aoai_endpoint: str | None = None
    aoai_embedding_deployment: str | None = None
    aoai_embedding_dimensions: int = 3072
    aoai_vision_deployment: str | None = None
    aoai_chat_deployment: str | None = None
    docintel_endpoint: str | None = None
    ocr_max_pages: int = 200
    upload_max_bytes: int = 104_857_600
    image_min_pixels: int = 16_384
    image_min_bytes: int = 10_240
    image_max_per_document: int = 50
    ingest_max_concurrent_sessions: int = 8
    cosmos_write_concurrency: int = 2  # worker-wide; 2 suits serverless (evidence.md E10)
    search_default_top: int = 8
    search_max_top: int = 20

    @property
    def servicebus_host(self) -> str | None:
        """The namespace's host name. Accepts a bare namespace or the deployment's full host name."""
        ns = self.servicebus_namespace
        if not ns:
            return None
        ns = ns.removeprefix("sb://").removeprefix("https://").rstrip("/")
        return ns if "." in ns else f"{ns}.servicebus.windows.net"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        environment = env.get("ENVIRONMENT", "")
        if environment not in ENVIRONMENTS:
            raise ConfigError(f"ENVIRONMENT must be one of {', '.join(ENVIRONMENTS)}")
        auth_mode = env.get("AUTH_MODE", "entra") or "entra"
        if auth_mode not in AUTH_MODES:
            raise ConfigError(f"AUTH_MODE must be one of {', '.join(AUTH_MODES)}")
        settings = cls(
            environment=environment,  # type: ignore[arg-type]
            auth_mode=auth_mode,  # type: ignore[arg-type]
            dev_api_key=env.get("DEV_API_KEY") or None,
            api_audience=env.get("API_AUDIENCE") or None,
            api_tenant_id=env.get("API_TENANT_ID") or None,
            caller_app_role=env.get("CALLER_APP_ROLE") or "Platform.Caller",
            cosmos_endpoint=env.get("COSMOS_ENDPOINT") or None,
            cosmos_database=env.get("COSMOS_DATABASE") or "fli",
            blob_account_url=env.get("BLOB_ACCOUNT_URL") or None,
            blob_container=env.get("BLOB_CONTAINER") or "files",
            servicebus_namespace=env.get("SERVICEBUS_NAMESPACE") or None,
            servicebus_queue=env.get("SERVICEBUS_QUEUE") or "ingest",
            aoai_endpoint=env.get("AOAI_ENDPOINT") or None,
            aoai_embedding_deployment=env.get("AOAI_EMBEDDING_DEPLOYMENT") or None,
            aoai_embedding_dimensions=_int(env, "AOAI_EMBEDDING_DIMENSIONS", 3072),
            aoai_vision_deployment=env.get("AOAI_VISION_DEPLOYMENT") or None,
            aoai_chat_deployment=env.get("AOAI_CHAT_DEPLOYMENT") or None,
            docintel_endpoint=env.get("DOCINTEL_ENDPOINT") or None,
            ocr_max_pages=_int(env, "OCR_MAX_PAGES", 200),
            upload_max_bytes=_int(env, "UPLOAD_MAX_BYTES", 104_857_600),
            image_min_pixels=_int(env, "IMAGE_MIN_PIXELS", 16_384),
            image_min_bytes=_int(env, "IMAGE_MIN_BYTES", 10_240),
            image_max_per_document=_int(env, "IMAGE_MAX_PER_DOCUMENT", 50),
            ingest_max_concurrent_sessions=_int(env, "INGEST_MAX_CONCURRENT_SESSIONS", 8),
            cosmos_write_concurrency=_int(env, "COSMOS_WRITE_CONCURRENCY", 2),
            search_default_top=_int(env, "SEARCH_DEFAULT_TOP", 8),
            search_max_top=_int(env, "SEARCH_MAX_TOP", 20),
        )
        settings.check()
        return settings

    def check(self) -> None:
        """Refuse configurations that must never run (CLAUDE.md rule 13, spec §10)."""
        if self.auth_mode == "dev":
            if self.environment != "local":
                raise ConfigError("AUTH_MODE=dev is refused unless ENVIRONMENT=local")
            if not self.dev_api_key:
                raise ConfigError("AUTH_MODE=dev requires DEV_API_KEY")
        if self.aoai_embedding_dimensions != 3072:
            # cosmos/chunks.container.json fixes the vector dimension at creation (E16).
            raise ConfigError("AOAI_EMBEDDING_DIMENSIONS must equal the chunks vector policy (3072)")
        if self.cosmos_write_concurrency < 1:
            raise ConfigError("COSMOS_WRITE_CONCURRENCY must be at least 1")
        if not 1 <= self.search_default_top <= self.search_max_top <= 20:
            raise ConfigError("need 1 <= SEARCH_DEFAULT_TOP <= SEARCH_MAX_TOP <= 20")
