"""Configuration guards (CLAUDE.md rule 13, spec §10)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fli.api import create_app
from fli.config import ConfigError, Settings

LOCAL_DEV = {"ENVIRONMENT": "local", "AUTH_MODE": "dev", "DEV_API_KEY": "k"}


def test_defaults_follow_the_spec():
    s = Settings.from_env({"ENVIRONMENT": "demo"})
    assert s.auth_mode == "entra"
    assert s.cosmos_database == "fli" and s.blob_container == "files" and s.servicebus_queue == "ingest"
    assert s.aoai_embedding_dimensions == 3072
    assert (s.search_default_top, s.search_max_top) == (8, 20)
    assert s.cosmos_write_concurrency == 2


@pytest.mark.parametrize("environment", ["demo", "prod"])
def test_dev_auth_is_refused_outside_local(environment):
    with pytest.raises(ConfigError, match="ENVIRONMENT=local"):
        Settings.from_env({"ENVIRONMENT": environment, "AUTH_MODE": "dev", "DEV_API_KEY": "k"})


def test_dev_auth_runs_locally_with_a_key():
    assert Settings.from_env({"ENVIRONMENT": "local", "AUTH_MODE": "dev", "DEV_API_KEY": "k"}).auth_mode == "dev"
    with pytest.raises(ConfigError, match="DEV_API_KEY"):
        Settings.from_env({"ENVIRONMENT": "local", "AUTH_MODE": "dev"})


@pytest.mark.parametrize("env", [{}, {"ENVIRONMENT": "staging"}, {"ENVIRONMENT": "local", "AUTH_MODE": "key"}])
def test_unknown_environment_or_auth_mode_is_refused(env):
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_embedding_dimensions_must_match_the_vector_policy():
    with pytest.raises(ConfigError, match="3072"):
        Settings.from_env({"ENVIRONMENT": "demo", "AOAI_EMBEDDING_DIMENSIONS": "1536"})


def test_healthz():
    client = TestClient(create_app(Settings.from_env(LOCAL_DEV)))
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "value,host",
    [
        ("fli-demo", "fli-demo.servicebus.windows.net"),
        ("fli-demo.servicebus.windows.net", "fli-demo.servicebus.windows.net"),  # infra/main.bicep's output
        ("sb://fli-demo.servicebus.windows.net/", "fli-demo.servicebus.windows.net"),
    ],
)
def test_servicebus_host_accepts_a_namespace_or_its_host_name(value, host):
    assert Settings.from_env({"ENVIRONMENT": "demo", "SERVICEBUS_NAMESPACE": value}).servicebus_host == host
