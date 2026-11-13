"""Render public paired-ablation metrics as Markdown, without interpreting results.

Only the known stage, arm, probe and measurement fields are rendered. Source
metadata, runtime identifiers and private raw state are never copied into tables.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

STAGES = ("1", "2", "3")
ARMS = ("split", "unsplit")
PROBES = tuple(
    (f"scale-p{i:02d}", size) for i, size in enumerate((1000, 1000, 1500, 1500, 2000, 2500, 3000, 3500, 4000, 5000), 1)
)
UNAVAILABLE = "unavailable"


def _get(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _format(value: Any, places: int = 2) -> str:
    if not _number(value):
        return UNAVAILABLE
    return f"{value:,.{places}f}"


def _documents(value: Any) -> str:
    if not _number(value):
        return UNAVAILABLE
    return f"{value:,.0f}" if value == int(value) else _format(value)


def _percent(value: Any) -> str:
    return f"{value:+,.3f}%" if _number(value) else UNAVAILABLE


def _range(values: Sequence[Any], places: int = 2, *, documents: bool = False) -> str:
    valid = [value for value in values if _number(value)]
    if len(valid) != len(values) or not valid:
        return f"{UNAVAILABLE} ({len(valid)}/{len(values)})" if valid else UNAVAILABLE
    formatter = _documents if documents else lambda value: _format(value, places)
    return f"{formatter(min(valid))}–{formatter(max(valid))}"


def _cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _indexes(summaries: Sequence[Any]) -> str:
    specs: set[tuple[str, tuple[str, ...]]] = set()
    missing = False
    no_utilized_index = False
    for summary in summaries:
        entries = _get(summary, "vectorIndexes")
        expected = _get(summary, "expectedIndexResponses")
        headers_complete = (
            type(expected) is int
            and expected > 0
            and _get(summary, "reportedIndexResponses") == expected
            and _get(summary, "indexHeadersPresent") == expected
            and _get(summary, "indexHeadersMissing") == 0
        )
        without_index = _get(summary, "indexReportsWithoutUtilizedVectorIndex")
        reports_without_index = _number(without_index) and without_index > 0
        no_utilized_index |= reports_without_index
        missing |= not isinstance(entries, list) or (not entries and not reports_without_index)
        for entry in entries if isinstance(entries, list) else []:
            kind, shards = _get(entry, "type"), _get(entry, "shardKeys")
            if not isinstance(kind, str) or not kind or not isinstance(shards, list):
                missing = True
                continue
            if not all(isinstance(path, str) for path in shards):
                missing = True
                continue
            specs.add((kind, tuple(shards)))
        missing |= _get(summary, "indexReportsComplete") is not True and not (
            headers_complete and reports_without_index
        )
    modes = [f"{kind} [{', '.join(shards) if shards else 'no shard key'}]" for kind, shards in sorted(specs)]
    if no_utilized_index:
        modes.append("no utilized vector index reported")
    if missing and modes:
        modes.append("partial response diagnostics")
    elif not modes:
        modes.append(UNAVAILABLE)
    return "; ".join(modes)


def _arm_label(arm: str, summaries: Sequence[Any]) -> str:
    return arm + ("*" if any(_get(summary, "complete") is not True for summary in summaries) else "")


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def _stage_drift(record: Mapping[str, Any], stage: str, arm: str, probe: str | None) -> Any:
    if stage == "1":
        path = ("control",) if probe is None else ("probes", probe)
        baseline = _get(record, "stages", "1", arm, *path, "ruMedian")
        return 0.0 if _number(baseline) and baseline > 0 else None
    path = ("controlStageDrift", stage, arm) if probe is None else ("stageDrift", stage, arm, probe)
    return _get(record, *path, "ruDriftPct")


def _matched_control_table(record: Mapping[str, Any]) -> str:
    stages = _get(record, "matchedControlQuestion", "stages")
    if not isinstance(stages, Mapping):
        return "Matched-question measurements are unavailable: this receipt does not include matched-control metadata."
    rows = []
    for stage in STAGES:
        for arm in ARMS:
            filtered, global_control = _get(stages, stage, arm, "filtered"), _get(stages, stage, arm, "global")
            filtered_ru, global_ru = _get(filtered, "ruMedian"), _get(global_control, "ruMedian")
            ratio = (
                global_ru / filtered_ru
                if _number(filtered_ru) and filtered_ru > 0 and _number(global_ru) and global_ru >= 0
                else None
            )
            rows.append(
                [
                    stage,
                    arm,
                    _format(filtered_ru),
                    _format(global_ru),
                    _format(ratio) + "×" if _number(ratio) else UNAVAILABLE,
                    _documents(_get(filtered, "retrievedMedian")),
                    _documents(_get(global_control, "retrievedMedian")),
                    _format(_get(filtered, "msMedian"), 1),
                    _format(_get(global_control, "msMedian"), 1),
                ]
            )
    return _table(
        [
            "Stage",
            "Arm",
            "Filtered RU",
            "Global RU",
            "Global ÷ filtered RU",
            "Filtered documents",
            "Global documents",
            "Filtered client ms",
            "Global client ms",
        ],
        rows,
    )


def render_tables(record: Mapping[str, Any]) -> str:
    """Render fixed-coverage tables; missing measurements remain unavailable."""
    overview: list[list[str]] = []
    filtered: list[list[str]] = []
    controls: list[list[str]] = []
    for stage in STAGES:
        for arm in ARMS:
            probes = [_get(record, "stages", stage, arm, "probes", probe) for probe, _ in PROBES]
            overview.append(
                [
                    stage,
                    _arm_label(arm, probes),
                    "library-scoped filtered probe range",
                    _range([_get(probe, "ruMedian") for probe in probes]),
                    _range([_get(probe, "msMedian") for probe in probes], 1),
                    _range([_get(probe, "retrievedMedian") for probe in probes], documents=True),
                    _range([_get(probe, "recallAt10Mean") for probe in probes], 4),
                    _indexes(probes),
                ]
            )
            control = _get(record, "stages", stage, arm, "control")
            overview.append(
                [
                    stage,
                    _arm_label(arm, [control]),
                    "global unfiltered control median",
                    _format(_get(control, "ruMedian")),
                    _format(_get(control, "msMedian"), 1),
                    _documents(_get(control, "retrievedMedian")),
                    "not measured",
                    _indexes([control]),
                ]
            )
            controls.append(
                [
                    stage,
                    _arm_label(arm, [control]),
                    _format(_get(control, "ruMedian")),
                    _percent(_stage_drift(record, stage, arm, None)),
                    _documents(_get(control, "retrievedMedian")),
                    _format(_get(control, "msMedian"), 1),
                    _indexes([control]),
                ]
            )
            for (probe, size), summary in zip(PROBES, probes, strict=True):
                filtered.append(
                    [
                        stage,
                        f"{probe} ({size:,})",
                        _arm_label(arm, [summary]),
                        _format(_get(summary, "ruMedian")),
                        _percent(_stage_drift(record, stage, arm, probe)),
                        _documents(_get(summary, "retrievedMedian")),
                        _format(_get(summary, "msMedian"), 1),
                        _format(_get(summary, "recallAt10Mean"), 4),
                        _indexes([summary]),
                    ]
                )
    cross_arm = [
        [f"{probe} ({size:,})"]
        + [_percent(_get(record, "unsplitVersusSplit", stage, probe, "ruDriftPct")) for stage in STAGES]
        for probe, size in PROBES
    ]
    return (
        "\n\n".join(
            [
                "## Paired vector-shard ablation measurements",
                'The split arm sets the vector index\'s `vectorIndexShardKey` to `["/libraryId"]`; the unsplit arm '
                "removes only that property. Both arms' library-scoped filtered searches keep "
                "`c.libraryId = @libraryId` and `partition_key=[libraryId]`. Each arm also has a global unfiltered "
                "control that omits both the library equality and the partition-key prefix. These are four cases: "
                "split/filtered, unsplit/filtered, split/global and unsplit/global. The main shard-key comparison "
                "holds the filtered query and its routing fixed while changing the vector-index policy.",
                "RU is per search. Latency is client elapsed milliseconds, including retries; documents read means "
                "Cosmos retrieved-document count. Filtered values are medians over questions of their three-repeat "
                "medians. Recall is the mean over all ANN repeats against exact search. An asterisk marks an "
                "incomplete summary; available observations are still shown. Missing values are unavailable, "
                "never zero-filled.",
                "### Stage overview",
                "Filtered ranges are the minimum–maximum of the ten individual probe medians, not pooled-query "
                "medians or repeat ranges. Recall ranges likewise span the ten probe means. Each control value is "
                "the median of three repetitions of one fixed unfiltered question. Index modes are the union of "
                "observed utilized-index reports. Missing HTTP-response headers do not erase an observed mode: "
                "partial response diagnostics marks that gap without assigning it to a request type. Unavailable "
                "means there is no index evidence.",
                _table(
                    ["Stage", "Arm", "Search", "RU", "Client ms", "Documents read", "Mean recall@10", "Observed index"],
                    overview,
                ),
                "### Every filtered probe",
                "Signed RU drift is relative to the same probe and arm at stage 1; stage-1 values are the reference "
                "(+0.000%). Index entries include their reported shard-key paths.",
                _table(
                    [
                        "Stage",
                        "Probe (vectors)",
                        "Arm",
                        "RU",
                        "RU drift vs stage 1",
                        "Documents read",
                        "Client ms",
                        "Mean recall@10",
                        "Observed index",
                    ],
                    filtered,
                ),
                "### Unfiltered controls",
                "The control has neither library equality nor a partition-key prefix. Recall was not measured for "
                "this negative control. Signed RU drift is relative to that arm's stage-1 control.",
                _table(
                    ["Stage", "Arm", "RU", "RU drift vs stage 1", "Documents read", "Client ms", "Observed index"],
                    controls,
                ),
                "### Same-question omission comparison",
                "Both requests use the same `scale-p01` question 0 vector. RU, documents and latency are medians "
                "of three repeats; the ratio divides the RU medians. The filtered request keeps the application "
                "projection, library equality and partition-key "
                "prefix. The global control projects ID only and omits both the equality and prefix. The ratio "
                "compares these complete request shapes within each index layout; it does not isolate the SQL "
                "predicate alone.",
                _matched_control_table(record),
                "### Unsplit versus split RU",
                "Signed percentage difference is 100 × (unsplit RU − split RU) / split RU for the same probe and "
                "stage. Positive values mean the unsplit arm costs more.",
                _table(["Probe (vectors)", "Stage 1", "Stage 2", "Stage 3"], cross_arm),
            ]
        )
        + "\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record", type=Path, required=True, help="sanitized paired-ablation JSON receipt")
    parser.add_argument("--out", type=Path, required=True, help="Markdown output file")
    args = parser.parse_args(argv)
    record = json.loads(args.record.read_text())
    if not isinstance(record, dict):
        parser.error("--record must contain a JSON object")
    args.out.write_text(render_tables(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
