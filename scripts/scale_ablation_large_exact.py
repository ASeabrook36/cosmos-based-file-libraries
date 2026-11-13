"""Private exhaustive references for the large-library pagination repair.

No service calls or query changes. A returned Cosmos candidate union is reduced
only after an exhaustive scan proves that it contains the reference top ten.
The original observation is never mutated. NumPy is an offline experiment tool.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from scripts import scale_probe
from scripts.canary import chunk_id

REPO = Path(__file__).resolve().parents[1]
DIMENSIONS = 3072
PREFIX_ROWS = 244773
PREFIX_BYTES = 16303644419
K = 10
BATCH = 256
BOUNDARY_TOLERANCE = 1e-10
NEW = ("large-p050000", "large-p100000", "large-p010000", "large-p025000")
SIZES = dict(zip(NEW, (50000, 100000, 10000, 25000), strict=True)) | {"scale-p01": 1000, "scale-p10": 5000}
MODELS = ("float16", "originalFloat64", "float16CorpusFtz")


class OracleError(ValueError):
    """A content-free failure; raw inputs remain private."""


def _require(condition, message):
    if not condition:
        raise OracleError(message)


def _read(path):
    return json.loads(Path(path).read_text())


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _sha(path, limit=None):
    h, remaining = hashlib.sha256(), limit
    with Path(path).open("rb") as handle:
        while remaining is None or remaining:
            block = handle.read(8 * 1024 * 1024 if remaining is None else min(remaining, 8 * 1024 * 1024))
            if not block:
                break
            h.update(block)
            if remaining is not None:
                remaining -= len(block)
    _require(remaining in (None, 0), "bound input ended early")
    return h.hexdigest()


def _private(path):
    p = Path(path).resolve()
    _require(not any((v / ".git").exists() for v in (p, *p.parents)), "oracle state must be outside checkouts")
    return p


def _selected(config, state):
    status = _read(state / "status.json") if (state / "status.json").exists() else {}
    chosen = status.get("selectedLibraries", config.get("selected", list(NEW)))
    _require(
        isinstance(chosen, list)
        and chosen[:2] == list(NEW[:2])
        and len(set(chosen)) == len(chosen)
        and all(v in NEW for v in chosen),
        "invalid selected oracle libraries",
    )
    return chosen


def _bindings(config, state):
    manifest = _read(config["inputManifest"])
    selected = _selected(config, state)
    _require(manifest.get("complete") is True and manifest.get("seed") == 20261029, "input preparation is incomplete")
    source = _read(state / "binding.json")
    _require(
        source.get("configSha256") == hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        and source.get("inputManifestSha256") == _sha(config["inputManifest"]),
        "original measurement input binding changed",
    )
    _require(
        _read(state / "question-vector-hash.json").get("sha256") == _sha(state / "question-vectors.json"),
        "frozen query-vector binding changed",
    )
    current_probe = Path(scale_probe.__file__)
    _require(
        source.get("sourceFiles", {}).get("scripts/scale_probe.py") == _sha(current_probe),
        "historical assignment implementation differs from the frozen run",
    )
    return (
        manifest,
        selected,
        {
            "configSha256": _digest(config),
            "inputManifestSha256": _sha(config["inputManifest"]),
            "queryVectorsSha256": _sha(state / "question-vectors.json"),
            "originalPrefixSha256": manifest["hashes"]["originalPrefixSha256"],
            "prefixRows": PREFIX_ROWS,
            "prefixBytes": PREFIX_BYTES,
            "selectedLibraries": selected,
            "payloadSha256": {lib: manifest["libraries"][lib]["sha256"] for lib in selected},
            "assignmentImplementationSha256": _sha(current_probe),
            "oracleImplementationSha256": _sha(__file__),
        },
    )


def _prefix_plan():
    # Source offsets follow 1,2,3,4. The actual 1,2,4,3 load order does not
    # change which source row belongs to a library.
    result = []
    for stage in (1, 2, 3, 4):
        expected = scale_probe.stage_line_range(stage)
        _require(expected.start == len(result), "historical source ranges are not contiguous")
        result.extend(scale_probe.assignment(stage))
    _require(len(result) == PREFIX_ROWS, "historical source population differs")
    return result


def _valid_id(identifier, library, count):
    match = re.fullmatch(re.escape(library) + r"-d([0-9]{4,}):1:([0-9])", identifier)
    if match is None:
        return False
    document, chunk = map(int, match.groups())
    return identifier == chunk_id(f"{library}-d{document:04d}", 1, chunk) and document * 10 + chunk < count


class _Top:
    def __init__(self, questions):
        self.values = [[] for _ in range(questions)]

    def add(self, identifiers, distances):
        for q in range(distances.shape[1]):
            scores = distances[:, q]
            take = min(K + 1, len(scores))
            picked = np.argpartition(scores, take - 1)[:take]
            self.values[q] = sorted(self.values[q] + [(float(scores[i]), identifiers[i]) for i in picked])[: K + 1]


def _queries(vectors, libraries):
    queries = {}
    for lib in libraries:
        q = np.asarray(vectors[lib], dtype=np.float64)
        _require(q.shape == (20, DIMENSIONS) and np.all(np.isfinite(q)), "query vectors are invalid")
        _require(np.array_equal(q, q.astype(np.float16).astype(np.float64)), "query cache is not float16 rounded")
        _require(not np.any((np.abs(q) > 0) & (np.abs(q) < 2**-14)), "query cache contains subnormal values")
        norm = np.linalg.norm(q, axis=1)
        _require(np.all(np.isfinite(norm)) and np.all(norm > 0), "query norm is invalid")
        queries[lib] = q / norm[:, None]
    queries["global"] = queries["scale-p01"][:1]
    return queries


def _oracle_hash(oracle):
    return _digest({key: value for key, value in oracle.items() if key != "oracleSha256"})


def build_oracle(config: dict, state: Path) -> dict:
    state = _private(state)
    destination = state / "offline-exact.json"
    if destination.exists():
        oracle = _read(destination)
        validate_oracle(config, state, oracle)
        return oracle
    manifest, selected, bindings = _bindings(config, state)
    libraries = ["scale-p01", "scale-p10", *selected]
    queries = _queries(_read(state / "question-vectors.json"), libraries)
    tops = {model: {lib: _Top(len(q)) for lib, q in queries.items()} for model in MODELS}
    plan = _prefix_plan()
    counts = Counter(lib for lib, _, _ in plan)
    counts.update({lib: SIZES[lib] for lib in selected})
    seen, observed, subnormals = set(), Counter(), 0
    norm_min, norm_max = math.inf, 0.0
    batch = []
    started = datetime.fromisoformat(manifest["preparationStartedAt"].replace("Z", "+00:00"))
    deadline = started + timedelta(hours=12)

    def flush():
        nonlocal batch, subnormals, norm_min, norm_max
        if not batch:
            return
        _require(datetime.now(UTC) < deadline, "offline reference build reached the original twelve-hour limit")
        matrix = np.asarray([v[2] for v in batch], dtype=np.float64)
        _require(matrix.shape == (len(batch), DIMENSIONS) and np.all(np.isfinite(matrix)), "corpus vectors are invalid")
        with np.errstate(over="ignore", invalid="ignore"):
            rounded = matrix.astype(np.float16).astype(np.float64)
        _require(np.all(np.isfinite(rounded)), "corpus vectors exceed float16 range")
        small = (np.abs(rounded) > 0) & (np.abs(rounded) < 2**-14)
        subnormals += int(np.count_nonzero(small))
        ftz = rounded.copy()
        ftz[small] = 0
        identifiers = [v[1] for v in batch]
        labels = [v[0] for v in batch]
        for model, values in zip(MODELS, (rounded, matrix, ftz), strict=True):
            norms = np.linalg.norm(values, axis=1)
            _require(np.all(np.isfinite(norms)) and np.all(norms > 0), "corpus norm is invalid")
            if model == "float16":
                norm_min, norm_max = min(norm_min, float(norms.min())), max(norm_max, float(norms.max()))
            distances = 1 - (values @ queries["global"].T) / norms[:, None]
            _require(np.all(np.isfinite(distances)), "cosine calculation is non-finite")
            tops[model]["global"].add(identifiers, distances)
            for lib in set(labels) & set(libraries):
                indices = [i for i, label in enumerate(labels) if label == lib]
                distances = 1 - (values[indices] @ queries[lib].T) / norms[indices, None]
                _require(np.all(np.isfinite(distances)), "cosine calculation is non-finite")
                tops[model][lib].add([identifiers[i] for i in indices], distances)
        batch = []

    def add(lib, identifier, vector):
        _require(lib in counts and _valid_id(identifier, lib, counts[lib]), "unexpected corpus item identity")
        _require(identifier not in seen, "duplicate corpus item identity")
        seen.add(identifier)
        observed[lib] += 1
        batch.append((lib, identifier, vector))
        if len(batch) == BATCH:
            flush()

    prefix_hash, prefix_bytes = hashlib.sha256(), 0
    prior = config["prior"]
    with Path(prior["corpus"]).open("rb") as handle:
        for lib, document, chunk in plan:
            raw = handle.readline()
            _require(bool(raw), "historical corpus prefix ended early")
            prefix_hash.update(raw)
            prefix_bytes += len(raw)
            row = json.loads(raw)
            add(lib, chunk_id(document, 1, chunk), row[prior.get("vectorField", "embedding")])
    _require(prefix_bytes == PREFIX_BYTES, "historical corpus prefix byte count changed")
    _require(prefix_hash.hexdigest() == bindings["originalPrefixSha256"], "historical corpus prefix changed")
    for lib in selected:
        entry, h = manifest["libraries"][lib], hashlib.sha256()
        _require(entry["size"] == SIZES[lib], "new library size differs")
        with Path(entry["payloadFile"]).open("rb") as handle:
            for raw in handle:
                h.update(raw)
                body = json.loads(raw)
                _require(body.get("libraryId") == lib, "new payload has incorrect ownership")
                add(lib, body["id"], body["embedding"])
        _require(h.hexdigest() == bindings["payloadSha256"][lib], "new payload changed")
    flush()
    _require(observed == counts, "exhaustive population counts differ")
    references = {}
    for lib in queries:
        references[lib] = []
        for q, best in enumerate(tops["float16"][lib].values):
            _require(len(best) == K + 1, "reference population is too small")
            primary_ids = [item[1] for item in best[:K]]
            variants = {}
            for model in MODELS[1:]:
                alt = tops[model][lib].values[q]
                variants[model] = {
                    "result_ids": [item[1] for item in alt[:K]],
                    "boundaryMargin": alt[K][0] - alt[K - 1][0],
                    "sameTopTenSet": set(primary_ids) == {item[1] for item in alt[:K]},
                }
            references[lib].append(
                {
                    "queryOrdinal": q,
                    "result_ids": primary_ids,
                    "top11": [{"id": item[1], "cosineDistance": item[0]} for item in best],
                    "boundaryMargin": best[K][0] - best[K - 1][0],
                    "boundaryTolerance": BOUNDARY_TOLERANCE,
                    "numericVariants": variants,
                }
            )
    margins = [v["boundaryMargin"] for refs in references.values() for v in refs]
    variant_changes = sum(
        not all(v["sameTopTenSet"] for v in ref["numericVariants"].values())
        for refs in references.values()
        for ref in refs
    )
    oracle = {
        "kind": "private-offline-exhaustive-cosine-reference-v1",
        "bindings": bindings,
        "scopeSizes": dict(counts),
        "references": references,
        "safe": {
            "method": "float16 corpus rounding without FTZ; frozen FTZ query cache; exhaustive float64 cosine",
            "dimensions": DIMENSIONS,
            "k": K,
            "topRetained": K + 1,
            "populationVectors": sum(counts.values()),
            "populationLibraries": len(counts),
            "measuredLibraries": len(libraries),
            "libraryReferenceQuestions": 20 * len(libraries),
            "globalReferenceQuestions": 1,
            "sourcePrefixRows": PREFIX_ROWS,
            "sourcePrefixBytes": prefix_bytes,
            "sourcePrefixSha256": prefix_hash.hexdigest(),
            "queryVectorsSha256": bindings["queryVectorsSha256"],
            "payloadSha256": {str(SIZES[lib]): bindings["payloadSha256"][lib] for lib in selected},
            "corpusNormRange": [norm_min, norm_max],
            "roundedCorpusSubnormalValues": subnormals,
            "librarySizeHistogram": {str(size): count for size, count in sorted(Counter(counts.values()).items())},
            "minimumBoundaryMargin": min(margins),
            "boundaryTolerance": BOUNDARY_TOLERANCE,
            "referencesWithNumericModelTopTenDifferences": variant_changes,
            "variants": list(MODELS),
            "networkCalls": 0,
            "databaseMutations": 0,
        },
    }
    oracle["oracleSha256"] = _oracle_hash(oracle)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".offline-exact-", dir=state)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(oracle, handle, sort_keys=True, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)
    return oracle


def validate_oracle(config: dict, state: Path, oracle: dict) -> None:
    state = _private(state)
    manifest, selected, bindings = _bindings(config, state)
    _require(oracle.get("oracleSha256") == _oracle_hash(oracle), "oracle content digest differs")
    _require(oracle.get("bindings") == bindings, "oracle input bindings changed")
    _require(
        _sha(config["prior"]["corpus"], PREFIX_BYTES) == bindings["originalPrefixSha256"], "historical prefix changed"
    )
    for lib in selected:
        _require(
            _sha(manifest["libraries"][lib]["payloadFile"]) == bindings["payloadSha256"][lib], "new payload changed"
        )
    libraries = ["scale-p01", "scale-p10", *selected]
    _require(set(oracle.get("references", {})) == {*libraries, "global"}, "oracle scope coverage differs")
    _require(
        sum(oracle["scopeSizes"].values()) == PREFIX_ROWS + sum(SIZES[v] for v in selected), "oracle population differs"
    )
    for lib, refs in oracle["references"].items():
        _require(len(refs) == (1 if lib == "global" else 20), "oracle question coverage differs")
        for q, ref in enumerate(refs):
            _require(
                ref["queryOrdinal"] == q
                and len(ref["result_ids"]) == K
                and len(set(ref["result_ids"])) == K
                and len(ref["top11"]) == K + 1,
                "oracle reference structure differs",
            )


def derive_reference(rawrow: dict, oracle: dict) -> dict:
    _require(oracle.get("oracleSha256") == _oracle_hash(oracle), "oracle content digest differs")
    _require(
        rawrow.get("kind") == "exact" and rawrow.get("run") == 0 and not rawrow.get("error"),
        "not a successful exact observation",
    )
    lib, q = rawrow.get("library"), rawrow.get("query")
    _require(
        lib in oracle["references"] and type(q) is int and 0 <= q < len(oracle["references"][lib]),
        "unknown reference scope",
    )
    expected_shape = "global" if lib == "global" else "routed"
    _require(
        rawrow.get("shape") == expected_shape and rawrow.get("arm") in {"split", "unsplit"}, "reference route differs"
    )
    for key in ("request_charge", "retrieved_document_count", "milliseconds"):
        value = rawrow.get(key)
        _require(
            type(value) in (int, float) and math.isfinite(value) and value >= 0, "reference metrics are incomplete"
        )
    _require(
        type(rawrow.get("http_responses")) is int and rawrow["http_responses"] > 0, "reference responses are incomplete"
    )
    ids = rawrow.get("result_ids")
    _require(
        isinstance(ids, list) and len(ids) >= K and all(isinstance(v, str) for v in ids) and len(set(ids)) == len(ids),
        "reference candidate identities are incomplete or duplicated",
    )
    for identifier in ids:
        owner = identifier.rsplit("-d", 1)[0]
        _require(
            owner in oracle["scopeSizes"]
            and _valid_id(identifier, owner, oracle["scopeSizes"][owner])
            and (lib == "global" or owner == lib),
            "reference candidate ownership differs",
        )
    ref = oracle["references"][lib][q]
    _require(
        ref["boundaryMargin"] > ref["boundaryTolerance"], "reference top-ten boundary is tied or numerically unresolved"
    )
    _require(set(ref["result_ids"]) <= set(ids), "Cosmos candidate union omits exhaustive reference results")
    bindings = rawrow.get("result_bindings", [])
    by_id = {v.get("id"): v for v in bindings if isinstance(v, dict)}
    _require(not bindings or len(bindings) == len(ids) and set(by_id) == set(ids), "reference result bindings differ")
    for binding in by_id.values():
        if lib != "global" and "libraryId" in binding:
            _require(binding["libraryId"] == lib, "reference declared ownership differs")
    result = copy.deepcopy(rawrow)
    result["result_ids"] = list(ref["result_ids"])
    result["result_bindings"] = [copy.deepcopy(by_id[v]) for v in ref["result_ids"]] if bindings else []
    result["referenceDerivation"] = {
        "method": "exhaustive-local-top-ten-confirmed-in-fully-drained-Cosmos-union",
        "oracleSha256": oracle["oracleSha256"],
        "rawRowSha256": _digest(rawrow),
        "rawCandidateCount": len(ids),
        "derivedCount": K,
        "rawWasAlreadyExactTopTen": len(ids) == K and set(ids) == set(ref["result_ids"]),
        "boundaryMargin": ref["boundaryMargin"],
        "boundaryTolerance": ref["boundaryTolerance"],
        "numericModelTopTenSetsAgree": all(v["sameTopTenSet"] for v in ref["numericVariants"].values()),
        "numericVariantCandidateCoverage": {
            model: set(value["result_ids"]) <= set(ids) for model, value in ref["numericVariants"].items()
        },
        "numericModelBoundaryMargins": {
            model: value["boundaryMargin"] for model, value in ref["numericVariants"].items()
        },
        "rawObservationMutated": False,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    try:
        oracle = build_oracle(_read(_private(args.config)), _private(args.state))
        print(json.dumps(oracle["safe"], sort_keys=True))
    except Exception as exc:
        print(json.dumps({"complete": False, "errorType": type(exc).__name__}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
