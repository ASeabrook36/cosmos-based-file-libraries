"""Retained context and fresh restore have different reference lifetimes/costs."""

import json

import pytest

from scripts import granular_reference_history_measurement as history
from scripts.granular_reference_measurement import count_json, omit, prepare_local_cases, run_case


def pick(cases, label):
    return next(case for case in cases if case["case"] == label)


def payloads(capture):
    return [json.loads(m["content"]) for m in capture["messages"] if m.get("role") == "tool"]


@pytest.mark.parametrize("mode", history.MODES)
async def test_shortened_component_keeps_current_labels_refreshes_info_and_rereads_exact_scope(mode):
    env, cases, _, _ = await prepare_local_cases()
    try:
        case = pick(cases, "small_paragraph")
        expected = env.index.read_documents(env.lib, "reader", **case["requests"][-1])["evidence"]
        result = history.run_shortened(env, case, expected, mode)
        assert len(result["initialCaptures"]) == 3 and len(result["continuedCaptures"]) == 2
        assert len(result["actualRequests"]) == 3 and result["failedLocalReferenceCalls"] == 0
        assert len(payloads(result["continuedCaptures"][0])) == 1
        doc = result["refreshedEvidence"]["documents"][0]
        assert doc["fileName"] == "source.docx" and doc["sections"][0]["heading"] == "Exceptions"
        assert doc["sections"][0]["citation"] == expected["documents"][0]["sections"][0]["citation"]
        assert doc["processingCoverage"]["sourceStructureLimitations"]
        assert omit(result["actualRequests"][1], {"maxTokens"}) == omit(result["actualRequests"][2], {"maxTokens"})
        metric = history._metric(result)
        assert metric["cumulativeMessagesAndToolsTokensLocal"] == sum(count_json(row) for row in result["captures"])
        assert metric["continuationOnly"]["cumulativeSchemaJsonTokensLocal"] > 0
        assert "renderer fixture" in metric["boundary"]
        if mode == "navigation":
            assert result["diagnostics"]["closed"]
            assert "documentInfo" not in result["beforeShorteningEvidence"]["documents"][0]
            assert doc["documentInfo"] and result["documentInfoRefreshJsonDeltaTokensLocal"] > 0
            resolution = result["diagnostics"]["resolutions"][-1]
            assert resolution["originalArguments"]["reference"] == doc["sections"][0]["actions"]["read"]
            assert resolution["resolvedArguments"]["documents"] == case["requests"][-1]["documents"]
        else:
            assert result["documentInfoRefreshJsonDeltaTokensLocal"] == 0
    finally:
        env.client.close()


async def test_fair_prose_restore_uses_same_visible_route_source_and_requests_in_all_formats():
    env, cases, _, _ = await prepare_local_cases()
    try:
        case = pick(cases, "original_outline_route")
        expected = env.index.read_documents(env.lib, "reader", **case["requests"][-1])["evidence"]
        requests, source = None, None
        for mode in history.MODES:
            result = history.run_restore(env, case, expected, mode, "prose_only")
            assert len(result["captures"]) == 7 and len(result["actualRequests"]) == 6
            assert result["failedLocalReferenceCalls"] == 0
            initial_history = [m for m in result["captures"][0]["messages"] if m["role"] == "assistant"]
            assert initial_history == [{"role": "assistant", "content": history.SUMMARY}]
            assert result["actualRequests"][0] == {"operation": "discover", "arguments": {"fileNames": ["source.docx"]}}
            current = omit(result["actualRequests"], {"maxTokens"})
            identity = history._source_identity(result["canonicalEvidence"])
            if requests is None:
                requests, source = current, identity
            else:
                assert current == requests and identity == source
    finally:
        env.client.close()


async def test_expired_reference_failure_is_local_content_free_and_counted_before_explicit_reacquisition():
    env, cases, _, _ = await prepare_local_cases()
    try:
        seed = run_case(env, pick(cases, "small_paragraph"), "navigation")
        old = seed["visibleFinalEvidence"]["documents"][0]["sections"][0]["actions"]["read"]
        assert seed["diagnostics"]["closed"]
        result = history.run_restore(
            env,
            pick(cases, "original_outline_route"),
            seed["canonicalEvidence"],
            "navigation",
            "expired_reference",
            expired_reference=old,
        )
        assert len(result["captures"]) == 8 and len(result["actualRequests"]) == 6
        assert result["failedLocalReferenceCalls"] == 1
        failure = payloads(result["captures"][1])[0]
        assert failure["error"] == "navigation_reference_unavailable" and not failure["complete"]
        assert old not in json.dumps(failure) and env.pin["documentId"] not in json.dumps(failure)
        assert "Do not sample" not in json.dumps(failure)
        assert result["actualRequests"][0]["operation"] == "discover"
        assert result["diagnostics"]["closed"]
        # Invalid reference makes no API call and is not registered as a valid resolution.
        assert len(result["diagnostics"]["resolutions"]) == 6
        final_ref = result["visibleFinalEvidence"]["documents"][0]["sections"][0]["actions"]["read"]
        assert final_ref != old
        metric = history._metric(result)
        assert metric["uniqueToolReplies"] == 7
        assert metric["cumulativeMessagesAndToolsTokensLocal"] == sum(count_json(row) for row in result["captures"])
    finally:
        env.client.close()


@pytest.mark.parametrize("mode", history.MODES)
async def test_retained_full_selector_restore_preserves_all_pins_and_measures_one_direct_api_read(mode):
    env, cases, _, _ = await prepare_local_cases()
    try:
        case = pick(cases, "original_outline_route")
        expected = env.index.read_documents(env.lib, "reader", **case["requests"][-1])["evidence"]
        result = history.run_restore(env, case, expected, mode, "retained_selector")
        assert len(result["actualRequests"]) == 1 and len(result["captures"]) == 2
        assert result["actualRequests"][0]["arguments"]["documents"] == case["requests"][-1]["documents"]
        summary = next(m["content"] for m in result["captures"][0]["messages"] if m["role"] == "assistant")
        assert json.dumps(case["requests"][-1], ensure_ascii=False) in summary
        assert history._metric(result)["cumulativeSystemInstructionsTextTokensLocal"] > 0
    finally:
        env.client.close()


async def test_private_report_includes_setup_refresh_failure_and_actual_transcripts_without_provider_claims(tmp_path):
    output = tmp_path / "history"
    report = await history.measure(private_output=output)
    assert len(report["measurements"]) == 10
    assert report["newCloudCalls"] == report["newModelCalls"] == 0
    assert report["providerUsage"] is report["cosmosRu"] is None
    assert "not minimal" in report["method"] and "matched-information comparison" in report["method"]
    assert report["expiredReferenceAcquisitionSetup"]["cumulativeMessagesAndToolsTokensLocal"] > 0
    hashes = {row["sourceBodyAndCitationIdentitySha256"] for row in report["measurements"]}
    assert len(hashes) == 1
    for metric in report["measurements"]:
        captured = json.loads((output / f"{metric['case']}-{metric['presentation']}.json").read_text())
        assert captured["captures"] and captured["actualRequests"]
        assert metric["cumulativeMessagesAndToolsTokensLocal"] == sum(count_json(row) for row in captured["captures"])
    assert json.loads((output / "measurements.json").read_text()) == report
    with pytest.raises(FileExistsError):
        await history.measure(private_output=output)


def test_unsupported_route_or_expired_non_navigation_refuses_before_any_api_work():
    with pytest.raises(ValueError, match="unsupported"):
        history.run_restore(None, {}, {}, "compact", "unknown")
    with pytest.raises(ValueError, match="requires"):
        history.run_restore(None, {}, {}, "compact", "expired_reference", expired_reference="old")
