# Large-library measurement tables

Client elapsed milliseconds include retries. Ranges describe observed repeats, not confidence intervals.

Exact-reference repair validated all 322 retained candidate unions against exhaustive local float16 cosine truth; 86 contained more than ten candidates. Raw observations, SQL and routing remain unchanged.

The primary reference rounds published corpus values to float16 without corpus FTZ, then computes float64 cosine against the frozen FTZ query vectors. Original unrounded float64 corpus values and corpus FTZ are sensitivity checks, not replacement references.

Numeric sensitivity (originalFloat64): 1 of 121 reference questions have a different top-ten set from primary; minimum observed 10/11 margin 4.27712928e-06.

Numeric sensitivity (float16CorpusFtz): 0 of 121 reference questions have a different top-ten set from primary; minimum observed 10/11 margin 8.62322846e-06.

## Routed and global requests

Global rows repeat the SAME shared observation; they are neither independent per-library measurements nor matched scope-omission effects.

| Library vectors | Arm | Request | Questions | RU | RU run range | Documents read | Latency ms | Recall@10 | Observed index | Evidence status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1,000 | split | routed | 20 | 12.22 | 12.22 to 12.22 | 50 | 107.75 | 1 | QuantizedFlat | accepted |
| 1,000 | split | global (shared) | 1 | 20,681.92 | 20,681.89 to 20,684.42 | 429,773 | 53,748 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| 1,000 | unsplit | routed | 20 | 70.51 | 70.51 to 70.51 | 1,000 | 182.35 | 1 | QuantizedFlat | accepted |
| 1,000 | unsplit | global (shared) | 1 | 23.86 | 21.54 to 24.99 | 50 | 250.2 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |
| 5,000 | split | routed | 20 | 34.5 | 34.495 to 34.5 | 50 | 161.55 | 0.995 | QuantizedFlat | accepted |
| 5,000 | split | global (shared) | 1 | 20,681.92 | 20,681.89 to 20,684.42 | 429,773 | 53,748 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| 5,000 | unsplit | routed | 20 | 143.26 | 143.26 to 143.26 | 50 | 169.45 | 0.995 | QuantizedFlat | accepted |
| 5,000 | unsplit | global (shared) | 1 | 23.86 | 21.54 to 24.99 | 50 | 250.2 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |
| 10,000 | split | routed | 20 | 8.68 | 8.57 to 9.11 | 50 | 114.3 | 0.995 | DiskANN | accepted |
| 10,000 | split | global (shared) | 1 | 20,681.92 | 20,681.89 to 20,684.42 | 429,773 | 53,748 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| 10,000 | unsplit | routed | 20 | 292.08 | 289.07 to 294.285 | 50 | 509.3 | 0.992 | DiskANN | accepted |
| 10,000 | unsplit | global (shared) | 1 | 23.86 | 21.54 to 24.99 | 50 | 250.2 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |
| 25,000 | split | routed | 20 | 9.17 | 9.17 to 9.19 | 50 | 93.35 | 0.995 | DiskANN | accepted |
| 25,000 | split | global (shared) | 1 | 20,681.92 | 20,681.89 to 20,684.42 | 429,773 | 53,748 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| 25,000 | unsplit | routed | 20 | 146.73 | 141.445 to 148.185 | 50 | 268.4 | 0.99 | DiskANN | accepted |
| 25,000 | unsplit | global (shared) | 1 | 23.86 | 21.54 to 24.99 | 50 | 250.2 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |
| 50,000 | split | routed | 20 | 29.89 | 27.465 to 30.925 | 50 | 111.85 | 0.98 | DiskANN | accepted |
| 50,000 | split | global (shared) | 1 | 20,681.92 | 20,681.89 to 20,684.42 | 429,773 | 53,748 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| 50,000 | unsplit | routed | 20 | 90.86 | 89.085 to 89.39 | 50 | 183.2 | 0.985 | DiskANN | accepted |
| 50,000 | unsplit | global (shared) | 1 | 23.86 | 21.54 to 24.99 | 50 | 250.2 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |
| 100,000 | split | routed | 20 | 32.53 | 31.91 to 32.525 | 50 | 122.45 | 0.995 | DiskANN | accepted |
| 100,000 | split | global (shared) | 1 | 20,681.92 | 20,681.89 to 20,684.42 | 429,773 | 53,748 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| 100,000 | unsplit | routed | 20 | 60.605 | 59.315 to 61.515 | 50 | 146.75 | 0.99 | DiskANN | accepted |
| 100,000 | unsplit | global (shared) | 1 | 23.86 | 21.54 to 24.99 | 50 | 250.2 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |

## Matched prefix-on / prefix-off diagnostic

A capped diagnostic uses the first five frozen questions and the SAME five routed questions. The primary routed table continues to use twenty.

| Vectors | Arm | Route | Questions | RU | Documents read | Latency ms | Recall@10 | Index | Status |
|---|---|---|---|---|---|---|---|---|---|
| 5,000 | split | prefix | 20 | 34.5 | 50 | 161.55 | 0.995 | QuantizedFlat | accepted |
| 5,000 | split | no prefix | 20 | 33.78 | 50 | 228.4 | 0.995 | QuantizedFlat, missing headers | accepted |
| 5,000 | unsplit | prefix | 20 | 143.26 | 50 | 169.45 | 0.995 | QuantizedFlat | accepted |
| 5,000 | unsplit | no prefix | 20 | 142.54 | 50 | 249.45 | 0.995 | QuantizedFlat, missing headers | accepted |
| 50,000 | split | prefix | 20 | 29.89 | 50 | 111.85 | 0.98 | DiskANN | accepted |
| 50,000 | split | no prefix | 20 | 23.56 | 50 | 192.75 | 0.98 | DiskANN, missing headers | accepted |
| 50,000 | unsplit | prefix | 20 | 90.86 | 50 | 183.2 | 0.985 | DiskANN | accepted |
| 50,000 | unsplit | no prefix | 20 | 86.11 | 50 | 262.6 | 0.985 | DiskANN, missing headers | accepted |
| 100,000 | split | prefix | 20 | 32.53 | 50 | 122.45 | 0.995 | DiskANN | accepted |
| 100,000 | split | no prefix | 20 | 25.675 | 50 | 204.3 | 0.995 | DiskANN, missing headers | accepted |
| 100,000 | unsplit | prefix | 20 | 60.605 | 50 | 146.75 | 0.99 | DiskANN | accepted |
| 100,000 | unsplit | no prefix | 20 | 55.11 | 50 | 224.95 | 0.99 | DiskANN, missing headers | accepted |

## Absolute sharding benefit versus the final-inventory 5,000-vector control

Saving means unsplit RU minus split RU, using routed searches at the same final inventory.

| Vectors | Saving at 5k RU | Saving RU | Difference RU | Saving change % | Classification |
|---|---|---|---|---|---|
| 10,000 | 108.76 | 283.4 | 174.64 | 160.574 | grown |
| 25,000 | 108.76 | 137.56 | 28.8 | 26.48 | grown |
| 50,000 | 108.76 | 60.97 | -47.79 | -43.941 | shrunk |
| 100,000 | 108.76 | 28.075 | -80.685 | -74.186 | shrunk |

## Conservative observed savings intervals

Independent run-median endpoints bound observed savings; these are not confidence intervals.

| Vectors | 5k saving range RU | Saving range RU | Ratio interval | Saving change % interval |
|---|---|---|---|---|
| 10,000 | 108.76 to 108.765 | 279.96 to 285.715 | 2.574 to 2.627 | 157.399 to 162.702 |
| 25,000 | 108.76 to 108.765 | 132.255 to 139.015 | 1.216 to 1.278 | 21.597 to 27.818 |
| 50,000 | 108.76 to 108.765 | 58.16 to 61.925 | 0.535 to 0.569 | -46.527 to -43.063 |
| 100,000 | 108.76 to 108.765 | 26.79 to 29.605 | 0.246 to 0.272 | -75.369 to -72.78 |

## Small-probe drift

Fresh baseline and historical E68 are distinct observations.

| Vectors | Arm | E68 RU | Fresh RU | Final RU | Difference RU | RU drift % | Documents drift % | Latency drift % |
|---|---|---|---|---|---|---|---|---|
| 1,000 | split | 12.22 | 12.22 | 12.22 | 0 | 0 | 0 | -7.748 |
| 1,000 | unsplit | 70.51 | 70.51 | 70.51 | 0 | 0 | 0 | -3.288 |
| 5,000 | split | 34.5 | 34.5 | 34.5 | 0 | 0 | 0 | -0.676 |
| 5,000 | unsplit | 143.26 | 143.26 | 143.26 | 0 | 0 | 0 | 10.355 |

## Immediate and late sentinels

One fixed question per scope, three repeats, separately from the twenty-question result. Late ages use the conservative last-write upper bound.

| Scope vectors | Arm | Sentinel | RU | Documents read | Latency ms | Recall@10 | Index | Status |
|---|---|---|---|---|---|---|---|---|
| 1000 | split | immediate | 12.23 | 50 | 98.3 | 1 | QuantizedFlat | accepted |
| 1000 | split | late | 12.23 | 50 | 156.6 | 1 | QuantizedFlat | accepted |
| 1000 | unsplit | immediate | 70.52 | 1,000 | 182.3 | 1 | QuantizedFlat | accepted |
| 1000 | unsplit | late | 70.52 | 1,000 | 435.9 | 1 | QuantizedFlat | accepted |
| 5000 | split | immediate | 34.51 | 50 | 144.8 | 1 | QuantizedFlat | accepted |
| 5000 | split | late | 34.51 | 50 | 153 | 1 | QuantizedFlat | accepted |
| 5000 | unsplit | immediate | 143.17 | 50 | 165.9 | 1 | QuantizedFlat | accepted |
| 5000 | unsplit | late | 143.27 | 50 | 167.3 | 1 | QuantizedFlat | accepted |
| 10000 | split | immediate | 8.53 | 50 | 88.8 | 1 | DiskANN | accepted |
| 10000 | split | late | 12.19 | 50 | 103.8 | 1 | DiskANN | accepted |
| 10000 | unsplit | immediate | 236.49 | 50 | 379.2 | 1 | DiskANN | accepted |
| 10000 | unsplit | late | 249.67 | 50 | 398.8 | 1 | DiskANN | accepted |
| 25000 | split | immediate | 9.23 | 50 | 88.8 | 1 | DiskANN | accepted |
| 25000 | split | late | 19 | 50 | 97.4 | 1 | DiskANN | accepted |
| 25000 | unsplit | immediate | 113.43 | 50 | 192.9 | 1 | DiskANN | accepted |
| 25000 | unsplit | late | 124.19 | 50 | 238 | 1 | DiskANN | accepted |
| 50000 | split | immediate | 28.41 | 50 | 103 | 1 | DiskANN | accepted |
| 50000 | split | late | 10.18 | 50 | 115.6 | 1 | DiskANN | accepted |
| 50000 | unsplit | immediate | 109.2 | 50 | 214.7 | 1 | DiskANN | accepted |
| 50000 | unsplit | late | 130.4 | 50 | 264.1 | 1 | DiskANN | accepted |
| 100000 | split | immediate | 28.71 | 50 | 97.6 | 1 | DiskANN | accepted |
| 100000 | split | late | 27.47 | 50 | 124.4 | 1 | DiskANN | accepted |
| 100000 | unsplit | immediate | 55.09 | 50 | 129.8 | 1 | DiskANN | accepted |
| 100000 | unsplit | late | 60.79 | 50 | 172.8 | 1 | DiskANN | accepted |
| global | split | immediate | 20,681.9 | 429,773 | 50,996 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| global | split | late | 20,686.9 | 429,773 | 63,196 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| global | unsplit | immediate | 23.28 | 50 | 264.1 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |
| global | unsplit | late | 23.93 | 50 | 288.6 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |

## Scientific limits

- Three repeats describe observed runs, not confidence intervals or independent index builds.
- One physical range and one index build per arm were measured; concurrency and throughput were not tested.
- Each size has one assigned nonnested library and a different question set at one fixed final total inventory. Library size, selectivity, corpus content and query difficulty are not independently varied; their separate causal effects cannot be identified.
- The fresh small-library controls measure background-growth drift. The new large libraries were measured only at the final inventory, so their flatness as unrelated background grows is not established.
- The shared global control is id-only, omits filter and prefix, and has its own eligible answer set.
- Five-question diagnostic coverage, when triggered, is compared with the same frozen routed subset.
- The no-prefix diagnostic ran last in the main sequence; its latency comparison cannot separate request shape from phase order, cache state and index age.
- The WHERE equality constrains /libraryId, the first hierarchical partition-key component. Omitting the explicit prefix does not establish the absence of inferred partition pruning, especially with one physical range.
- Late sentinels cover one fixed question per scope, not the full twenty-question suite.
- A client connectivity or sleep transition was reported between the main and late phases; cross-time client-latency changes cannot be attributed solely to index aging.
- Stable scans without vector-specific build completion cannot separate a lasting planner choice from unfinished graph construction.
