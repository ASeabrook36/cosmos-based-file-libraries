import copy
import json

from scripts.scale_ablation_report import ARMS, PROBES, STAGES, main, render_tables


def receipt():
    record = {
        "stages": {},
        "stageDrift": {},
        "controlStageDrift": {},
        "unsplitVersusSplit": {},
        "privateMetadata": {"endpoint": "PRIVATE_SENTINEL", "sourceText": "CONTENT_SENTINEL"},
    }
    for stage in STAGES:
        record["stages"][stage] = {}
        record["unsplitVersusSplit"][stage] = {}
        for arm in ARMS:
            index = {"type": "QuantizedFlat", "shardKeys": ["/libraryId"] if arm == "split" else []}
            base = {
                "complete": True,
                "ruMedian": 12.34,
                "msMedian": 123.45,
                "retrievedMedian": 50,
                "recallAt10Mean": 0.998333,
                "indexReportsComplete": True,
                "vectorIndexes": [index],
            }
            record["stages"][stage][arm] = {
                "probes": {probe: dict(base, ruMedian=12.34 + i) for i, (probe, _) in enumerate(PROBES)},
                "control": dict(base, ruMedian=1234.56, msMedian=2345.67, retrievedMedian=25000),
            }
            if stage != "1":
                record["stageDrift"].setdefault(stage, {})[arm] = {
                    probe: {"ruDriftPct": -0.123456} for probe, _ in PROBES
                }
                record["controlStageDrift"].setdefault(stage, {})[arm] = {"ruDriftPct": 234.56789}
        record["unsplitVersusSplit"][stage] = {probe: {"ruDriftPct": 22.34567} for probe, _ in PROBES}
    return record


def tables(markdown):
    groups = []
    for section in markdown.split("### ")[1:]:
        lines = [line for line in section.splitlines() if line.startswith("| ")]
        if lines:
            groups.append(lines)
    return groups


def test_tables_preserve_fixed_coverage_and_render_numeric_drift():
    markdown = render_tables(receipt())
    overview, filtered, controls, differences = tables(markdown)
    assert [len(table) - 2 for table in (overview, filtered, controls, differences)] == [12, 60, 6, 10]
    assert "12.34–21.34" in overview[2]
    assert "not pooled-query medians" in markdown
    assert "-0.123%" in markdown
    assert "+234.568%" in markdown
    assert "+22.346%" in markdown
    assert "+0.000%" in markdown
    assert "scale-p10 (5,000)" in markdown
    assert "QuantizedFlat [/libraryId]" in markdown
    assert "QuantizedFlat [no shard key]" in markdown
    assert 'vectorIndexShardKey` to `["/libraryId"]`' in markdown
    assert "`c.libraryId = @libraryId` and `partition_key=[libraryId]`" in markdown
    assert "split/filtered, unsplit/filtered, split/global and unsplit/global" in markdown
    assert "library-scoped filtered probe range" in overview[2]
    assert "global unfiltered control median" in overview[3]
    assert "PRIVATE_SENTINEL" not in markdown
    assert "CONTENT_SENTINEL" not in markdown
    assert "Matched-question measurements are unavailable" in markdown


def test_missing_values_stay_unavailable_and_missing_probe_is_not_hidden():
    record = receipt()
    del record["stages"]["2"]["unsplit"]["probes"]["scale-p10"]
    record["stages"]["3"]["split"]["control"]["ruMedian"] = None
    record["stages"]["1"]["split"]["control"]["msMedian"] = 0
    del record["stageDrift"]["2"]["unsplit"]["scale-p10"]
    markdown = render_tables(record)
    overview, filtered, controls, _ = tables(markdown)
    assert len(filtered) - 2 == 60
    assert any("unsplit*" in row and "unavailable (9/10)" in row for row in overview)
    missing = next(row for row in filtered if row.startswith("| 2 | scale-p10") and "unsplit*" in row)
    assert missing.count("unavailable") == 6
    assert any("| 3 | split | unavailable |" in row for row in controls)
    assert any("| 1 | split |" in row and "| 0.0 |" in row for row in controls)


def test_index_reports_preserve_mixed_modes_and_reported_absence():
    record = receipt()
    summary = record["stages"]["3"]["unsplit"]["probes"]["scale-p01"]
    summary["vectorIndexes"].append({"type": "DiskANN", "shardKeys": []})
    control = record["stages"]["3"]["unsplit"]["control"]
    control.update(
        vectorIndexes=[],
        indexReportsComplete=False,
        expectedIndexResponses=3,
        reportedIndexResponses=3,
        indexHeadersPresent=3,
        indexHeadersMissing=0,
        indexReportsWithoutUtilizedVectorIndex=3,
    )
    markdown = render_tables(record)
    assert "DiskANN [no shard key]; QuantizedFlat [no shard key]" in markdown
    controls = tables(markdown)[2]
    row = next(row for row in controls if row.startswith("| 3 | unsplit |"))
    assert "no utilized vector index reported" in row
    assert "unavailable" not in row
    assert "partial response diagnostics" not in row

    control.update(expectedIndexResponses=6, indexHeadersMissing=3)
    observed_mode = record["stages"]["3"]["split"]["control"]
    observed_mode.update(
        vectorIndexes=[{"type": "DiskANN", "shardKeys": []}],
        indexReportsComplete=False,
        expectedIndexResponses=6,
        reportedIndexResponses=3,
        indexHeadersPresent=3,
        indexHeadersMissing=3,
    )
    controls = tables(render_tables(record))[2]
    no_index_row = next(row for row in controls if row.startswith("| 3 | unsplit |"))
    mode_row = next(row for row in controls if row.startswith("| 3 | split |"))
    assert "no utilized vector index reported; partial response diagnostics" in no_index_row
    assert "DiskANN [no shard key]; partial response diagnostics" in mode_row
    assert "unavailable" not in no_index_row + mode_row


def test_cli_writes_the_same_content_without_mutating_receipt(tmp_path):
    record = receipt()
    before = copy.deepcopy(record)
    source, output = tmp_path / "receipt.json", tmp_path / "tables.md"
    source.write_text(json.dumps(record))
    assert main(["--record", str(source), "--out", str(output)]) == 0
    assert output.read_text() == render_tables(record)
    assert record == before


def test_matched_question_table_uses_its_own_medians_and_does_not_fill_missing_metrics():
    record = receipt()
    record["matchedControlQuestion"] = {
        "probeOrdinal": 1,
        "questionOrdinal": 0,
        "repeats": 3,
        "stages": {
            stage: {
                arm: {
                    "filtered": {"ruMedian": 10, "retrievedMedian": 50, "msMedian": 100},
                    "global": {"ruMedian": 200, "retrievedMedian": 500, "msMedian": 300},
                }
                for arm in ARMS
            }
            for stage in STAGES
        },
    }
    record["matchedControlQuestion"]["stages"]["2"]["unsplit"]["filtered"]["ruMedian"] = None
    record["matchedControlQuestion"]["stages"]["3"]["split"]["filtered"]["ruMedian"] = 0
    markdown = render_tables(record)
    matched = tables(markdown)[3]
    assert len(matched) - 2 == 6
    assert "| 1 | split | 10.00 | 200.00 | 20.00× | 50 | 500 | 100.0 | 300.0 |" in matched
    assert "| 2 | unsplit | unavailable | 200.00 | unavailable | 50 | 500 | 100.0 | 300.0 |" in matched
    assert "| 3 | split | 0.00 | 200.00 | unavailable | 50 | 500 | 100.0 | 300.0 |" in matched
    assert "global control projects ID only and omits both the equality and prefix" in markdown
    assert "does not isolate the SQL predicate alone" in markdown
