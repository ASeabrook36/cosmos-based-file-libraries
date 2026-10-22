from __future__ import annotations

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from fli.api import create_app
from fli.auth import require_caller
from fli.config import ConfigError, Settings

KEY = "local-dev-key"


def client() -> TestClient:
    app = create_app(Settings.from_env({"ENVIRONMENT": "local", "AUTH_MODE": "dev", "DEV_API_KEY": KEY}))

    @app.get("/protected", dependencies=[Depends(require_caller)])
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_correct_key_is_accepted():
    assert client().get("/protected", headers={"Authorization": f"Bearer {KEY}"}).json() == {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": KEY},
        {"Authorization": "Basic " + KEY},
        {"Authorization": "Bearer "},
    ],
)
def test_missing_or_wrong_key_is_401_in_the_standard_error_shape(headers):
    response = client().get("/protected", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "unauthorized", "message": "a valid bearer key is required"}}


def test_non_ascii_credentials_do_not_crash():
    assert client().get("/protected", headers={"Authorization": "Bearer ключ".encode()}).status_code == 401


def test_healthz_needs_no_key():
    assert client().get("/healthz").status_code == 200


def test_entra_mode_refuses_to_start_rather_than_serve_without_a_check():
    with pytest.raises(ConfigError, match="Phase 5"):
        create_app(Settings.from_env({"ENVIRONMENT": "demo"}))


def test_dev_mode_still_refused_outside_local():
    with pytest.raises(ConfigError, match="ENVIRONMENT=local"):
        Settings.from_env({"ENVIRONMENT": "demo", "AUTH_MODE": "dev", "DEV_API_KEY": KEY})


def test_a_server_with_no_key_configured_rejects_everything():
    test_client = client()
    test_client.app.state.settings = Settings(environment="local", auth_mode="dev", dev_api_key=None)
    assert test_client.get("/protected", headers={"Authorization": "Bearer "}).status_code == 401
