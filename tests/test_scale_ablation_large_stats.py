"""Large-library statistics keep quality, routing subsets and savings separate."""

import copy
import json

import pytest

from scripts.scale_ablation_large_stats import (
    compare_arms,
    compare_routes,
    compare_size_benefit,
    compare_small_drift,
    summarize_shape,
    summarize_subset,
)


def report(mode="QuantizedFlat"):
    return {"UtilizedVectorIndexes": [{"IndexSpec": {"VectorIndexType": mode, "ShardKeys": ["/libraryId"]}}]}


def fixture(charges=((10, 10, 10),), *, library="probe", explicit_scan=False):
    ann, references = [], []
    for query, repeats in enumerate(charges):
        ids = [f"{library}-d0000:1:{i}" for i in range(10)]
        for run, charge in enumerate(repeats):
            ann.append(
                {
                    "query": query,
                    "run": run,
                    "request_charge": charge,
                    "retrieved_document_count": 50,
                    "milliseconds": 100,
                    "result_ids": ids.copy(),
                    "error": None,
                    "http_responses": 1,
                    "index_utilizations": [{} if explicit_scan else report()],
                }
            )
        references.append(
            {
                "query": query,
                "run": 0,
                "request_charge": 340,
                "retrieved_document_count": 5000,
                "milliseconds": 600,
                "result_ids": ids.copy(),
                "error": None,
            }
        )
    return ann, references


def summary(charges=(10, 10, 10), *, size=5000, inventory=429773, **kwargs):
    ann, exact = fixture((charges,))
    return summarize_shape(
        ann, exact, query_ordinals=(0,), library_id="probe", library_size=size, inventory_vectors=inventory, **kwargs
    )


def test_primary_statistic_is_median_over_questions_of_repeat_medians():
    ann, exact = fixture(((1, 2, 100), (4, 5, 6)))
    result = summarize_shape(ann, exact, query_ordinals=(0, 1), library_id="probe")
    assert result["ruMedian"] == 3.5
    assert result["ruRunMedians"] == [2.5, 3.5, 53]
    assert result["qualityAccepted"]
    assert result["referenceRetrievedMedian"] == 5000


def test_explicit_no_vector_index_is_usable_but_missing_report_is_not():
    ann, exact = fixture(explicit_scan=True)
    result = summarize_shape(ann, exact, query_ordinals=(0,), library_id="probe")
    assert result["qualityAccepted"]
    assert result["indexDiagnostics"]["indexReportsWithoutUtilizedVectorIndex"] == 3
    assert not result["indexDiagnostics"]["indexReportsComplete"]
    ann[1]["index_utilizations"] = [None]
    result = summarize_shape(ann, exact, query_ordinals=(0,), library_id="probe")
    assert not result["qualityAccepted"]


def test_http_gaps_remain_visible_without_erasing_usable_data_response_evidence():
    ann, exact = fixture()
    for row in ann:
        row["http_responses"] = 2
        row["response_metrics"] = [
            {"retrieved": None, "indexUtilization": None},
            {"retrieved": 50, "indexUtilization": report()},
        ]
    result = summarize_shape(ann, exact, query_ordinals=(0,), library_id="probe")
    diagnostics = result["indexDiagnostics"]
    assert result["qualityAccepted"]
    assert diagnostics["indexHeadersMissing"] == 3
    assert diagnostics["responsesWithoutRetrievedMetric"] == 3
    assert diagnostics["responsesWithRetrievedMetric"] == 3
    assert diagnostics["missingIndexHeaderOnResponsesWithRetrievedMetric"] == 0


def test_frozen_five_question_subset_uses_shared_exact_references():
    ann, exact = fixture(tuple((q + 1, q + 1, q + 1) for q in range(20)))
    routed_subset = summarize_subset(
        ann, exact, query_ordinals=range(5), library_id="probe", library_size=5000, inventory_vectors=429773
    )
    no_prefix = summarize_shape(
        ann[:15],
        exact,
        query_ordinals=range(5),
        library_id="probe",
        query_shape="noPrefix",
        library_size=5000,
        inventory_vectors=429773,
    )
    assert routed_subset["qualityAccepted"] and no_prefix["qualityAccepted"]
    assert routed_subset["sourceAnnRowCount"] == 60
    assert routed_subset["searches"] == 15 and routed_subset["referenceSearches"] == 5
    assert routed_subset["ruMedian"] == no_prefix["ruMedian"] == 3
    assert no_prefix["queryOrdinals"] == [0, 1, 2, 3, 4]
    assert not compare_arms(routed_subset, no_prefix)["sameMeasurementContext"]
    contrast = compare_routes(routed_subset, no_prefix)
    assert contrast["sameMeasurementContext"] and contrast["qualityAccepted"]
    assert contrast["ruRatio"] == 1
    routed_all = summarize_shape(ann, exact, library_id="probe", library_size=5000, inventory_vectors=429773)
    assert not compare_routes(routed_all, no_prefix)["qualityAccepted"]


def test_route_comparison_rejects_another_library_even_at_the_same_size():
    ann, exact = fixture(library="another")
    other = summarize_shape(
        ann,
        exact,
        query_ordinals=(0,),
        library_id="another",
        library_size=5000,
        inventory_vectors=429773,
        query_shape="no-prefix",
    )
    assert other["qualityAccepted"]
    assert not compare_routes(summary(), other)["sameMeasurementContext"]


@pytest.mark.parametrize("fault", ["missing-repeat", "duplicate", "missing-exact", "ownership", "metrics", "recall"])
def test_incomplete_or_low_quality_rows_never_receive_a_cost_verdict(fault):
    ann, exact = fixture()
    if fault == "missing-repeat":
        ann.pop()
    elif fault == "duplicate":
        ann[1] = copy.deepcopy(ann[0])
    elif fault == "missing-exact":
        exact.clear()
    elif fault == "ownership":
        ann[0]["result_ids"][0] = "another-d0000:1:0"
    elif fault == "metrics":
        ann[0]["milliseconds"] = None
    else:
        ann[1]["result_ids"][0] = "probe-d9999:1:0"
    result = summarize_shape(
        ann, exact, query_ordinals=(0,), library_id="probe", library_size=5000, inventory_vectors=429773
    )
    assert not result["qualityAccepted"]
    assert compare_arms(summary(), result)["classification"] is None


def test_strong_ru_benefit_requires_twofold_gap_between_repeat_ranges():
    split = summary((10, 11, 12))
    assert compare_arms(split, summary((24, 25, 26)))["classification"] == "strong-benefit"
    borderline = compare_arms(split, summary((20, 23, 24)))
    assert borderline["ruRatio"] > 2
    assert not borderline["strongShardingBenefit"]
    assert borderline["classification"] == "other"


def test_ru_similarity_requires_each_corresponding_repeat_ratio_in_band():
    assert compare_arms(summary(), summary((8, 10, 12)))["noMaterialObservedRuDifference"]
    result = compare_arms(summary(), summary((7, 10, 13)))
    assert result["ruRatio"] == 1
    assert not result["noMaterialObservedRuDifference"]


def test_absolute_benefit_growth_is_separate_from_the_split_ratio():
    result = compare_size_benefit(
        summary(), summary((20, 20, 20)), summary((100, 100, 100), size=100000), summary((130, 130, 130), size=100000)
    )
    assert result["baselineSavingsRu"] == 10
    assert result["savingsRu"] == 30
    assert result["savingsRatioToBaseline"] == 3
    assert result["savingsChangePct"] == 200
    assert result["classification"] == "grown"


@pytest.mark.parametrize(("large", "classification"), [(11, "held"), (7, "shrunk"), (12, "held")])
def test_absolute_benefit_held_and_shrunk_bands(large, classification):
    result = compare_size_benefit(
        summary(), summary((20, 20, 20)), summary((100, 100, 100), size=50000), summary((100 + large,) * 3, size=50000)
    )
    assert result["classification"] == classification


def test_overlapping_repeat_savings_make_central_growth_borderline():
    result = compare_size_benefit(
        summary(), summary((18, 20, 22)), summary((100, 100, 100), size=50000), summary((112, 113, 114), size=50000)
    )
    assert result["observedChangeBand"] == "grown"
    assert result["classification"] == "borderline"


def test_savings_intervals_use_independent_endpoints_rather_than_correlated_run_pairs():
    result = compare_size_benefit(
        summary((10, 20, 30)),
        summary((20, 30, 40)),
        summary((100, 110, 120), size=50000),
        summary((130, 140, 150), size=50000),
    )
    assert result["baselinePairedRunSavingsRu"] == [10, 10, 10]
    assert result["pairedRunSavingsRu"] == [30, 30, 30]
    assert result["baselineRepeatSavingsRange"] == [-10, 30]
    assert result["repeatSavingsRange"] == [10, 50]
    assert result["repeatSavingsRatioRange"] is None
    assert result["observedChangeBand"] == "grown"
    assert result["classification"] == "borderline"


def test_four_quotients_bound_negative_large_savings_without_reversing_endpoints():
    result = compare_size_benefit(
        summary((10, 11, 12)),
        summary((20, 21, 22)),
        summary((100, 101, 102), size=50000),
        summary((95, 96, 97), size=50000),
    )
    assert result["baselineRepeatSavingsRange"] == [8, 12]
    assert result["repeatSavingsRange"] == [-7, -3]
    assert result["repeatSavingsRatioRange"] == [-0.875, -0.25]
    assert result["repeatSavingsChangePctRange"] == [-187.5, -125]
    assert result["classification"] == "shrunk"


def test_negative_baseline_has_absolute_numbers_and_no_benefit_growth_verdict():
    result = compare_size_benefit(
        summary((20, 20, 20)),
        summary(),
        summary((100, 100, 100), size=50000),
        summary((95, 95, 95), size=50000),
    )
    assert result["baselineSavingsRu"] == -10
    assert result["savingsRu"] == -5 and result["savingsDeltaRu"] == 5
    assert result["repeatSavingsRatioRange"] == [0.5, 0.5]
    assert result["savingsRatioToBaseline"] is None
    assert result["classification"] is None


def test_nonpositive_baseline_preserves_absolute_effect_but_has_no_growth_verdict():
    result = compare_size_benefit(
        summary(), summary(), summary((100, 100, 100), size=50000), summary((130, 130, 130), size=50000)
    )
    assert result["baselineSavingsRu"] == 0
    assert result["savingsDeltaRu"] == 30
    assert result["savingsRatioToBaseline"] is None
    assert result["classification"] is None


def test_size_growth_requires_common_final_inventory_and_complete_quality():
    result = compare_size_benefit(
        summary(),
        summary((20, 20, 20)),
        summary((100, 100, 100), size=50000, inventory=394773),
        summary((130, 130, 130), size=50000, inventory=394773),
    )
    assert result["savingsDeltaRu"] == 20
    assert not result["sameFinalInventory"]
    assert result["classification"] is None
    incomplete = summary()
    incomplete["qualityAccepted"] = False
    incomplete["ruRunMedians"] = [None, None, None]
    assert (
        compare_size_benefit(incomplete, summary((20, 20, 20)), summary(size=50000), summary(size=50000))[
            "classification"
        ]
        is None
    )


def test_small_control_drift_retains_the_actual_number_below_the_band():
    result = compare_small_drift(summary(), summary((10.1, 10.1, 10.1), inventory=429773))
    assert result["ruDelta"] == 0.1
    assert result["ruDifferencePct"] == 1
    assert result["ruStableWithin20Pct"]


def test_changing_modes_are_reported_and_limit_the_cost_classification():
    ann, exact = fixture()
    ann[2]["index_utilizations"] = [report("DiskANN")]
    result = summarize_shape(
        ann, exact, query_ordinals=(0,), library_id="probe", library_size=5000, inventory_vectors=429773
    )
    assert result["qualityAccepted"]
    assert not result["modeSignatureStableAcrossRuns"]
    assert compare_arms(summary(), result)["classification"] is None


def test_public_summary_never_copies_ids_text_bindings_or_vectors():
    ann, exact = fixture()
    for row in [*ann, *exact]:
        row.update(
            {"text": "private-content-marker", "queryVector": [0.123456789], "endpoint": "private-endpoint-marker"}
        )
    value = json.dumps(summarize_shape(ann, exact, query_ordinals=(0,), library_id="probe"))
    assert "private-content-marker" not in value and "private-endpoint-marker" not in value
    assert "probe-d0000" not in value and "queryVector" not in value


def test_global_recall_is_available_without_library_ownership_gate():
    ann, exact = fixture()
    result = summarize_shape(ann, exact, query_ordinals=(0,), query_shape="global", inventory_vectors=429773)
    assert result["qualityAccepted"] and result["recallAt10Mean"] == 1
    assert not result["ownershipChecked"]
