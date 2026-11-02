"""Historical model-window attestations survive intentional deployment cleanup."""

import copy
from datetime import UTC, datetime, timedelta

import pytest

from scripts.retrieval_r2_offline import window_binding


def envelope(captured):
    return {
        "capturedAt": captured.isoformat(),
        "account": {"id": "/accounts/test", "properties": {"endpoint": "https://example.test"}},
        "deployment": {
            "id": "/accounts/test/deployments/embed",
            "sku": {"name": "GlobalStandard", "capacity": 1},
            "properties": {
                "model": {"format": "OpenAI", "name": "text-embedding-3-large", "version": "1"},
                "provisioningState": "Succeeded",
                "versionUpgradeOption": "NoAutoUpgrade",
            },
        },
    }


def test_historical_receipts_bind_to_measurement_not_today():
    started = datetime(2026, 1, 1, tzinfo=UTC)
    finished = started + timedelta(minutes=5)
    before, after = envelope(started - timedelta(seconds=10)), envelope(finished + timedelta(seconds=5))
    kwargs = {
        "started": started,
        "finished": finished,
        "endpoint": "https://example.test",
        "deployment": "embed",
        "model": "text-embedding-3-large",
        "revision": "1",
    }
    assert len(window_binding(before, after, **kwargs)["bindingSha256"]) == 64
    wrong = copy.deepcopy(after)
    wrong["deployment"]["sku"]["capacity"] = 2
    with pytest.raises(ValueError, match="SKU changed"):
        window_binding(before, wrong, **kwargs)
    with pytest.raises(ValueError, match="adjacent"):
        window_binding(before, envelope(finished + timedelta(hours=1)), **kwargs)
    with pytest.raises(ValueError, match="stale"):
        window_binding(envelope(started - timedelta(hours=1)), after, **kwargs)
    with pytest.raises(ValueError, match="follows"):
        window_binding(envelope(started + timedelta(seconds=1)), after, **kwargs)
