# Full late-suite measurement tables

This is a separate approved epoch; the initial record remains unchanged.

## Routed twenty-question suite

Each value is the median of question-level three-repeat medians; recall is averaged over the full eligible question/repeat set.

| Vectors | Arm | RU | RU run range | Documents read | Latency ms | Recall@10 | Observed index | Status |
|---|---|---|---|---|---|---|---|---|
| 1000 | split | 12.22 | 12.22 to 12.22 | 50 | 125.4 | 1 | QuantizedFlat | accepted |
| 1000 | unsplit | 70.51 | 70.51 to 70.51 | 1,000 | 221.5 | 1 | QuantizedFlat | accepted |
| 5000 | split | 34.5 | 34.5 to 34.5 | 50 | 190.6 | 0.995 | QuantizedFlat | accepted |
| 5000 | unsplit | 143.26 | 143.26 to 143.26 | 50 | 192.35 | 0.995 | QuantizedFlat | accepted |
| 10000 | split | 12.335 | 10.685 to 14.55 | 50 | 127.95 | 0.995 | DiskANN | accepted |
| 10000 | unsplit | 289.85 | 266.76 to 289.85 | 50 | 524.3 | 0.99 | DiskANN | accepted |
| 25000 | split | 17.365 | 16.795 to 17.695 | 50 | 136.25 | 0.995 | DiskANN | accepted |
| 25000 | unsplit | 135.825 | 129.4 to 140.205 | 50 | 261.85 | 0.987 | DiskANN | accepted |
| 50000 | split | 21.53 | 19.58 to 23.23 | 50 | 138.1 | 0.98 | DiskANN | accepted |
| 50000 | unsplit | 88.205 | 85.545 to 89.415 | 50 | 201.4 | 0.985 | DiskANN | accepted |
| 100000 | split | 26.56 | 26.17 to 27.365 | 50 | 139.95 | 0.995 | DiskANN | accepted |
| 100000 | unsplit | 58.505 | 56.04 to 60.215 | 50 | 179.1 | 0.99 | DiskANN | accepted |

## Shared global control

One fixed global question, three repeats per arm. This observation is shared across library sizes and is not a matched scope-omission effect.

| Arm | RU | Documents read | Latency ms | Recall@10 | Observed index | Status |
|---|---|---|---|---|---|---|
| split | 20,686.91 | 429,773 | 61,644.1 | 1 | no utilized vector index, missing headers | PARTIAL DATA HEADERS (usable) |
| unsplit | 13.89 | 50 | 306.4 | 0.9 | DiskANN, missing headers | RECALL FLOOR FAILURE |

## Absolute benefit versus this epoch's 5k control

Savings = unsplit RU minus split RU. Conservative repeat endpoints are descriptive, not confidence intervals.

| Vectors | 5k saving RU | Saving RU | Change % | Saving ratio range | Classification |
|---|---|---|---|---|---|
| 50000 | 108.76 | 66.675 | -38.695 | 0.573 to 0.642 | shrunk |
| 100000 | 108.76 | 31.945 | -70.628 | 0.264 to 0.313 | shrunk |
| 10000 | 108.76 | 277.515 | 155.163 | 2.319 to 2.567 | grown |
| 25000 | 108.76 | 118.46 | 8.919 | 1.027 to 1.135 | held |

## Initial to full-late drift

The aggregate change and matched question/repeat changes are distinct summaries of the same frozen pairs.

| Vectors / scope | Arm | Initial RU | Full-late RU | RU change % | Paired RU change % range | Documents change % | Latency change % | Mode change | Quality accepted, initial / late |
|---|---|---|---|---|---|---|---|---|---|
| 1000 | split | 12.22 | 12.22 | 0 | 0 to 0 | 0 | 16.381 | False | True / True |
| 1000 | unsplit | 70.51 | 70.51 | 0 | 0 to 0 | 0 | 21.47 | False | True / True |
| 5000 | split | 34.5 | 34.5 | 0 | -0.29 to 0.291 | 0 | 17.982 | False | True / True |
| 5000 | unsplit | 143.26 | 143.26 | 0 | -0.07 to 0.07 | 0 | 13.514 | False | True / True |
| 10000 | split | 8.68 | 12.335 | 42.108 | -5.877 to 143.237 | 0 | 11.942 | False | True / True |
| 10000 | unsplit | 292.08 | 289.85 | -0.763 | -53.598 to 4.214 | 0 | 2.945 | False | True / True |
| 25000 | split | 9.17 | 17.365 | 89.368 | 0.436 to 175.655 | 0 | 45.956 | False | True / True |
| 25000 | unsplit | 146.73 | 135.825 | -7.432 | -26.229 to 11.408 | 0 | -2.44 | False | True / True |
| 50000 | split | 29.89 | 21.53 | -27.969 | -70.199 to 11.069 | 0 | 23.469 | False | True / True |
| 50000 | unsplit | 90.86 | 88.205 | -2.922 | -32.678 to 9.925 | 0 | 9.934 | False | True / True |
| 100000 | split | 32.53 | 26.56 | -18.352 | -64.96 to 10.491 | 0 | 14.292 | False | True / True |
| 100000 | unsplit | 60.605 | 58.505 | -3.465 | -25.702 to 11.073 | 0 | 22.044 | False | True / True |
| global | split | 20,681.92 | 20,686.91 | 0.024 | 0.012 to 0.024 | 0 | 14.691 | False | True / True |
| global | unsplit | 23.86 | 13.89 | -41.785 | -46.007 to -2.305 | 0 | 22.462 | False | False / False |

## Variation across frozen questions

Question-level ranges preserve outliers; full per-question repeat values are in the JSON record.

| Vectors / scope | Arm | Question median RU range | All-repeat RU range | Individual recall range | Questions with recall below 1 |
|---|---|---|---|---|---|
| 1000 | split | 12.21 to 12.23 | 12.21 to 12.23 | 1 to 1 | 0 |
| 1000 | unsplit | 70.5 to 70.53 | 70.5 to 70.53 | 1 to 1 | 0 |
| 5000 | split | 34.4 to 34.52 | 34.39 to 34.52 | 0.9 to 1 | 1 |
| 5000 | unsplit | 143.15 to 143.28 | 143.15 to 143.28 | 0.9 to 1 | 1 |
| 10000 | split | 9.33 to 14.82 | 8.53 to 21.04 | 0.9 to 1 | 1 |
| 10000 | unsplit | 135.81 to 391.61 | 105.49 to 403.43 | 0.9 to 1 | 2 |
| 25000 | split | 11.78 to 23.72 | 9.22 to 25.25 | 0.9 to 1 | 1 |
| 25000 | unsplit | 101.16 to 211.74 | 93.97 to 217.75 | 0.8 to 1 | 2 |
| 50000 | split | 13.94 to 26.3 | 10.05 to 29.75 | 0.8 to 1 | 3 |
| 50000 | unsplit | 56.84 to 105.4 | 56.25 to 125.27 | 0.9 to 1 | 3 |
| 100000 | split | 12.53 to 31.07 | 11.92 to 33.23 | 0.9 to 1 | 1 |
| 100000 | unsplit | 38.39 to 80.85 | 35.44 to 83.94 | 0.9 to 1 | 2 |
| global | split | 20,686.91 to 20,686.91 | 20,684.39 to 20,689.41 | 1 to 1 | 0 |
| global | unsplit | 13.89 to 13.89 | 11.63 to 23.31 | 0.9 to 0.9 | 1 |

## Question-zero sentinel comparisons

These are the same single question and repeat ordinals, separately from the full twenty-question result.

| Vectors / scope | Arm | Parent window | Parent RU | Full-late q0 RU | RU change % | Mode change | Current status |
|---|---|---|---|---|---|---|---|
| 1000 | split | immediate | 12.23 | 12.23 | 0 | False | accepted |
| 1000 | split | late | 12.23 | 12.23 | 0 | False | accepted |
| 1000 | unsplit | immediate | 70.52 | 70.52 | 0 | False | accepted |
| 1000 | unsplit | late | 70.52 | 70.52 | 0 | False | accepted |
| 5000 | split | immediate | 34.51 | 34.51 | 0 | False | accepted |
| 5000 | split | late | 34.51 | 34.51 | 0 | False | accepted |
| 5000 | unsplit | immediate | 143.17 | 143.27 | 0.07 | False | accepted |
| 5000 | unsplit | late | 143.27 | 143.27 | 0 | False | accepted |
| 10000 | split | immediate | 8.53 | 10.17 | 19.226 | False | accepted |
| 10000 | split | late | 12.19 | 10.17 | -16.571 | False | accepted |
| 10000 | unsplit | immediate | 236.49 | 135.81 | -42.573 | False | accepted |
| 10000 | unsplit | late | 249.67 | 135.81 | -45.604 | False | accepted |
| 25000 | split | immediate | 9.23 | 13.72 | 48.646 | False | accepted |
| 25000 | split | late | 19 | 13.72 | -27.789 | False | accepted |
| 25000 | unsplit | immediate | 113.43 | 115.05 | 1.428 | False | accepted |
| 25000 | unsplit | late | 124.19 | 115.05 | -7.36 | False | accepted |
| 50000 | split | immediate | 28.41 | 20.6 | -27.49 | False | accepted |
| 50000 | split | late | 10.18 | 20.6 | 102.358 | False | accepted |
| 50000 | unsplit | immediate | 109.2 | 105.4 | -3.48 | False | accepted |
| 50000 | unsplit | late | 130.4 | 105.4 | -19.172 | False | accepted |
| 100000 | split | immediate | 28.71 | 24.75 | -13.793 | False | accepted |
| 100000 | split | late | 27.47 | 24.75 | -9.902 | False | accepted |
| 100000 | unsplit | immediate | 55.09 | 54.09 | -1.815 | False | accepted |
| 100000 | unsplit | late | 60.79 | 54.09 | -11.022 | False | accepted |
| global | split | immediate | 20,681.9 | 20,686.91 | 0.024 | False | PARTIAL DATA HEADERS (usable) |
| global | split | late | 20,686.9 | 20,686.91 | 0 | False | PARTIAL DATA HEADERS (usable) |
| global | unsplit | immediate | 23.28 | 13.89 | -40.335 | False | RECALL FLOOR FAILURE |
| global | unsplit | late | 23.93 | 13.89 | -41.956 | False | RECALL FLOOR FAILURE |

## Scientific limits

- This is one additional epoch on one physical range and one index build per arm; three repeats are descriptive, not independent workloads or confidence intervals.
- Each size has one assigned nonnested library and a different question set at fixed total inventory. Size, selectivity, content and query difficulty are not independently varied.
- The full-suite absolute RU benefit uses this epoch's own 5,000-vector control; the initial epoch's comparison is preserved separately.
- Matched question/repeat drift is an observed time-window change, not evidence of a causal maturity mechanism or permanent settling, even when serving modes and documents read are unchanged.
- Question-zero sentinel comparisons cover one fixed question per scope and remain separate from the twenty-question summaries.
- The global control omits library equality and partition prefix, has a different eligible answer set, and uses one fixed question. A recall-floor failure precludes a quality-equivalent cost conclusion.
- No prefix-omission diagnostic was repeated; this epoch adds no evidence about implicit partition pruning or the earlier shape comparison.
- Client connectivity or sleep transitions were reported between parent windows. Client latency across epochs also depends on network, connection, cache and phase timing and cannot be attributed solely to index aging.
- Large-library flatness as unrelated background grows remains untested; all new large-library epochs use the same total inventory.
