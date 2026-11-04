import copy

import pytest

from scripts.hybrid_pilot_source_audit import classify, complete_inventory


def test_inventory_cannot_silently_omit_an_attempt():
    inventory = [{"blobPath": "start", "bytes": 1}, {"blobPath": "finish", "bytes": 2}]
    rows = [{**item, "archiveFile": str(i), "sha256": "hash"} for i, item in enumerate(inventory)]
    complete_inventory(rows, inventory)
    with pytest.raises(ValueError):
        complete_inventory(rows[:1], inventory)
    with pytest.raises(ValueError):
        complete_inventory(rows[::-1], inventory)


def recovered():
    return [
        {
            "executionId": "job",
            "sourceSha256": "source",
            "status": "error",
            "errorType": "ServiceBusError",
            "chunkWriteChargeComplete": True,
            "chunkWriteChargeKnown": 4,
            "finishedAt": "2026-11-04T01:00:00+00:00",
        },
        {"status": "published", "startedAt": "2026-11-04T01:01:00+00:00"},
    ]


def test_recovered_publication_retains_error_category():
    assert classify(recovered(), {"executionId": "job"}, "source", {"executionId": "job"}).startswith(
        "committed-generation-with-terminal-error"
    )


@pytest.mark.parametrize("mutation", ["writes", "other_error", "source", "job", "late_write", "early", "ambiguous"])
def test_recovery_requires_independent_complete_evidence(mutation):
    attempts = recovered()
    metrics = {"executionId": "job"}
    if mutation == "writes":
        attempts[0]["chunkWriteChargeComplete"] = False
    elif mutation == "other_error":
        attempts[0]["errorType"] = "ValueError"
    elif mutation == "source":
        attempts[0]["sourceSha256"] = "foreign"
    elif mutation == "job":
        metrics["executionId"] = "foreign"
    elif mutation == "late_write":
        attempts[1]["chunkWriteChargeKnown"] = 0
    elif mutation == "early":
        attempts[1]["startedAt"] = "2026-11-03T01:00:00+00:00"
    elif mutation == "ambiguous":
        attempts.append(copy.deepcopy(attempts[0]))
    with pytest.raises(ValueError):
        classify(attempts, {"executionId": "job"}, "source", metrics)
