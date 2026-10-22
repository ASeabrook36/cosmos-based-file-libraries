"""Caller check (spec §5 rule 1).

The prototype trusts the platform by deployment: the API listens locally and every request
carries the static ``DEV_API_KEY`` as a bearer token. This says nothing about users or
libraries; the platform decides those. Entra bearer validation replaces it in Phase 5, and
only this module changes. ``AUTH_MODE=dev`` is refused outside ``ENVIRONMENT=local`` by
``Settings.check`` (CLAUDE.md rule 13).
"""

from __future__ import annotations

import hmac

from fastapi import Request

from fli.config import ConfigError, Settings
from fli.errors import ApiError


def check_auth_supported(settings: Settings) -> None:
    """Refuse to start in a mode that is not built, rather than serve without a caller check."""
    if settings.auth_mode != "dev":
        raise ConfigError("AUTH_MODE=entra is not built until Phase 5; run locally with AUTH_MODE=dev")


def require_caller(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries the configured key."""
    expected = request.app.state.settings.dev_api_key or ""
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    # compare_digest on bytes: constant time, and safe for non-ASCII input
    if not expected or scheme.lower() != "bearer" or not hmac.compare_digest(presented.encode(), expected.encode()):
        raise ApiError(401, "unauthorized", "a valid bearer key is required")
