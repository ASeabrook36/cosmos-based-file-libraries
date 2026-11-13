## Paired vector-shard ablation measurements

The split arm sets the vector index's `vectorIndexShardKey` to `["/libraryId"]`; the unsplit arm removes only that property. Both arms' library-scoped filtered searches keep `c.libraryId = @libraryId` and `partition_key=[libraryId]`. Each arm also has a global unfiltered control that omits both the library equality and the partition-key prefix. These are four cases: split/filtered, unsplit/filtered, split/global and unsplit/global. The main shard-key comparison holds the filtered query and its routing fixed while changing the vector-index policy.

RU is per search. Latency is client elapsed milliseconds, including retries; documents read means Cosmos retrieved-document count. Filtered values are medians over questions of their three-repeat medians. Recall is the mean over all ANN repeats against exact search. An asterisk marks an incomplete summary; available observations are still shown. Missing values are unavailable, never zero-filled.

### Stage overview

Filtered ranges are the minimum–maximum of the ten individual probe medians, not pooled-query medians or repeat ranges. Recall ranges likewise span the ten probe means. Each control value is the median of three repetitions of one fixed unfiltered question. Index modes are the union of observed utilized-index reports. Missing HTTP-response headers do not erase an observed mode: partial response diagnostics marks that gap without assigning it to a request type. Unavailable means there is no index evidence.

| Stage | Arm | Search | RU | Client ms | Documents read | Mean recall@10 | Observed index |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | split | library-scoped filtered probe range | 12.03–34.21 | 90.4–134.7 | 50–50 | 0.9950–1.0000 | QuantizedFlat [/libraryId] |
| 1 | split | global unfiltered control median | 1,203.87 | 2,534.3 | 25,000 | not measured | no utilized vector index reported; partial response diagnostics |
| 1 | unsplit | library-scoped filtered probe range | 70.42–143.03 | 106.0–174.8 | 50–1,000 | 0.9950–1.0000 | QuantizedFlat [no shard key] |
| 1 | unsplit | global unfiltered control median | 15.38 | 144.6 | 50 | not measured | DiskANN [no shard key]; partial response diagnostics |
| 2 | split | library-scoped filtered probe range | 12.22–34.50 | 90.8–135.3 | 50–50 | 0.9950–1.0000 | QuantizedFlat [/libraryId] |
| 2 | split | global unfiltered control median | 8,501.07 | 25,597.9 | 176,599 | not measured | no utilized vector index reported; partial response diagnostics |
| 2 | unsplit | library-scoped filtered probe range | 70.42–143.26 | 113.7–182.8 | 50–1,000 | 0.9950–1.0000 | QuantizedFlat [no shard key] |
| 2 | unsplit | global unfiltered control median | 20.20 | 263.4 | 50 | not measured | DiskANN [no shard key]; partial response diagnostics |
| 3 | split | library-scoped filtered probe range | 12.22–34.50 | 92.1–137.6 | 50–50 | 0.9950–1.0000 | QuantizedFlat [/libraryId] |
| 3 | split | global unfiltered control median | 11,779.51 | 31,997.6 | 244,773 | not measured | no utilized vector index reported; partial response diagnostics |
| 3 | unsplit | library-scoped filtered probe range | 70.42–143.26 | 106.7–175.2 | 50–1,000 | 0.9950–1.0000 | QuantizedFlat [no shard key] |
| 3 | unsplit | global unfiltered control median | 22.81 | 256.7 | 50 | not measured | DiskANN [no shard key]; partial response diagnostics |

### Every filtered probe

Signed RU drift is relative to the same probe and arm at stage 1; stage-1 values are the reference (+0.000%). Index entries include their reported shard-key paths.

| Stage | Probe (vectors) | Arm | RU | RU drift vs stage 1 | Documents read | Client ms | Mean recall@10 | Observed index |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | scale-p01 (1,000) | split | 12.03 | +0.000% | 50 | 90.5 | 1.0000 | QuantizedFlat [/libraryId] |
| 1 | scale-p02 (1,000) | split | 12.12 | +0.000% | 50 | 90.4 | 0.9950 | QuantizedFlat [/libraryId] |
| 1 | scale-p03 (1,500) | split | 14.90 | +0.000% | 50 | 96.3 | 0.9950 | QuantizedFlat [/libraryId] |
| 1 | scale-p04 (1,500) | split | 14.89 | +0.000% | 50 | 97.0 | 0.9950 | QuantizedFlat [/libraryId] |
| 1 | scale-p05 (2,000) | split | 17.69 | +0.000% | 50 | 102.7 | 1.0000 | QuantizedFlat [/libraryId] |
| 1 | scale-p06 (2,500) | split | 20.48 | +0.000% | 50 | 107.7 | 0.9950 | QuantizedFlat [/libraryId] |
| 1 | scale-p07 (3,000) | split | 23.16 | +0.000% | 50 | 113.8 | 1.0000 | QuantizedFlat [/libraryId] |
| 1 | scale-p08 (3,500) | split | 25.95 | +0.000% | 50 | 118.8 | 0.9950 | QuantizedFlat [/libraryId] |
| 1 | scale-p09 (4,000) | split | 28.64 | +0.000% | 50 | 124.2 | 0.9950 | QuantizedFlat [/libraryId] |
| 1 | scale-p10 (5,000) | split | 34.21 | +0.000% | 50 | 134.7 | 0.9950 | QuantizedFlat [/libraryId] |
| 1 | scale-p01 (1,000) | unsplit | 70.51 | +0.000% | 1,000 | 172.7 | 1.0000 | QuantizedFlat [no shard key] |
| 1 | scale-p02 (1,000) | unsplit | 70.42 | +0.000% | 1,000 | 174.8 | 1.0000 | QuantizedFlat [no shard key] |
| 1 | scale-p03 (1,500) | unsplit | 142.80 | +0.000% | 50 | 106.2 | 0.9950 | QuantizedFlat [no shard key] |
| 1 | scale-p04 (1,500) | unsplit | 142.77 | +0.000% | 50 | 106.0 | 0.9950 | QuantizedFlat [no shard key] |
| 1 | scale-p05 (2,000) | unsplit | 142.83 | +0.000% | 50 | 109.9 | 1.0000 | QuantizedFlat [no shard key] |
| 1 | scale-p06 (2,500) | unsplit | 142.91 | +0.000% | 50 | 116.9 | 0.9950 | QuantizedFlat [no shard key] |
| 1 | scale-p07 (3,000) | unsplit | 142.94 | +0.000% | 50 | 121.0 | 1.0000 | QuantizedFlat [no shard key] |
| 1 | scale-p08 (3,500) | unsplit | 143.03 | +0.000% | 50 | 127.5 | 0.9950 | QuantizedFlat [no shard key] |
| 1 | scale-p09 (4,000) | unsplit | 142.93 | +0.000% | 50 | 133.2 | 0.9950 | QuantizedFlat [no shard key] |
| 1 | scale-p10 (5,000) | unsplit | 142.97 | +0.000% | 50 | 143.2 | 0.9950 | QuantizedFlat [no shard key] |
| 2 | scale-p01 (1,000) | split | 12.22 | +1.579% | 50 | 91.7 | 1.0000 | QuantizedFlat [/libraryId] |
| 2 | scale-p02 (1,000) | split | 12.22 | +0.825% | 50 | 90.8 | 0.9950 | QuantizedFlat [/libraryId] |
| 2 | scale-p03 (1,500) | split | 14.90 | +0.000% | 50 | 96.3 | 0.9950 | QuantizedFlat [/libraryId] |
| 2 | scale-p04 (1,500) | split | 14.71 | -1.242% | 50 | 97.5 | 0.9950 | QuantizedFlat [/libraryId] |
| 2 | scale-p05 (2,000) | split | 17.79 | +0.565% | 50 | 102.9 | 1.0000 | QuantizedFlat [/libraryId] |
| 2 | scale-p06 (2,500) | split | 20.48 | +0.024% | 50 | 107.9 | 0.9950 | QuantizedFlat [/libraryId] |
| 2 | scale-p07 (3,000) | split | 23.26 | +0.432% | 50 | 113.2 | 1.0000 | QuantizedFlat [/libraryId] |
| 2 | scale-p08 (3,500) | split | 26.05 | +0.405% | 50 | 119.1 | 0.9950 | QuantizedFlat [/libraryId] |
| 2 | scale-p09 (4,000) | split | 28.73 | +0.314% | 50 | 124.8 | 0.9950 | QuantizedFlat [/libraryId] |
| 2 | scale-p10 (5,000) | split | 34.50 | +0.848% | 50 | 135.3 | 0.9950 | QuantizedFlat [/libraryId] |
| 2 | scale-p01 (1,000) | unsplit | 70.51 | +0.000% | 1,000 | 180.0 | 1.0000 | QuantizedFlat [no shard key] |
| 2 | scale-p02 (1,000) | unsplit | 70.42 | +0.000% | 1,000 | 182.8 | 1.0000 | QuantizedFlat [no shard key] |
| 2 | scale-p03 (1,500) | unsplit | 142.80 | +0.000% | 50 | 114.7 | 0.9950 | QuantizedFlat [no shard key] |
| 2 | scale-p04 (1,500) | unsplit | 142.87 | +0.070% | 50 | 113.7 | 0.9950 | QuantizedFlat [no shard key] |
| 2 | scale-p05 (2,000) | unsplit | 142.83 | +0.000% | 50 | 119.7 | 1.0000 | QuantizedFlat [no shard key] |
| 2 | scale-p06 (2,500) | unsplit | 142.91 | +0.000% | 50 | 134.3 | 0.9950 | QuantizedFlat [no shard key] |
| 2 | scale-p07 (3,000) | unsplit | 143.04 | +0.070% | 50 | 139.2 | 1.0000 | QuantizedFlat [no shard key] |
| 2 | scale-p08 (3,500) | unsplit | 143.13 | +0.070% | 50 | 143.8 | 0.9950 | QuantizedFlat [no shard key] |
| 2 | scale-p09 (4,000) | unsplit | 142.93 | +0.000% | 50 | 147.3 | 0.9950 | QuantizedFlat [no shard key] |
| 2 | scale-p10 (5,000) | unsplit | 143.26 | +0.203% | 50 | 152.6 | 0.9950 | QuantizedFlat [no shard key] |
| 3 | scale-p01 (1,000) | split | 12.22 | +1.579% | 50 | 92.6 | 1.0000 | QuantizedFlat [/libraryId] |
| 3 | scale-p02 (1,000) | split | 12.22 | +0.825% | 50 | 92.1 | 0.9950 | QuantizedFlat [/libraryId] |
| 3 | scale-p03 (1,500) | split | 14.90 | +0.000% | 50 | 99.9 | 0.9950 | QuantizedFlat [/libraryId] |
| 3 | scale-p04 (1,500) | split | 15.10 | +1.376% | 50 | 98.6 | 0.9950 | QuantizedFlat [/libraryId] |
| 3 | scale-p05 (2,000) | split | 17.79 | +0.565% | 50 | 104.6 | 1.0000 | QuantizedFlat [/libraryId] |
| 3 | scale-p06 (2,500) | split | 20.48 | +0.024% | 50 | 109.8 | 0.9950 | QuantizedFlat [/libraryId] |
| 3 | scale-p07 (3,000) | split | 23.26 | +0.432% | 50 | 114.3 | 1.0000 | QuantizedFlat [/libraryId] |
| 3 | scale-p08 (3,500) | split | 26.06 | +0.424% | 50 | 121.5 | 0.9950 | QuantizedFlat [/libraryId] |
| 3 | scale-p09 (4,000) | split | 28.73 | +0.314% | 50 | 125.4 | 0.9950 | QuantizedFlat [/libraryId] |
| 3 | scale-p10 (5,000) | split | 34.50 | +0.848% | 50 | 137.6 | 0.9950 | QuantizedFlat [/libraryId] |
| 3 | scale-p01 (1,000) | unsplit | 70.51 | +0.000% | 1,000 | 175.2 | 1.0000 | QuantizedFlat [no shard key] |
| 3 | scale-p02 (1,000) | unsplit | 70.42 | +0.000% | 1,000 | 174.7 | 1.0000 | QuantizedFlat [no shard key] |
| 3 | scale-p03 (1,500) | unsplit | 142.80 | +0.000% | 50 | 106.7 | 0.9950 | QuantizedFlat [no shard key] |
| 3 | scale-p04 (1,500) | unsplit | 142.87 | +0.070% | 50 | 107.8 | 0.9950 | QuantizedFlat [no shard key] |
| 3 | scale-p05 (2,000) | unsplit | 142.93 | +0.067% | 50 | 112.2 | 1.0000 | QuantizedFlat [no shard key] |
| 3 | scale-p06 (2,500) | unsplit | 142.91 | +0.000% | 50 | 117.0 | 0.9950 | QuantizedFlat [no shard key] |
| 3 | scale-p07 (3,000) | unsplit | 143.03 | +0.066% | 50 | 123.2 | 1.0000 | QuantizedFlat [no shard key] |
| 3 | scale-p08 (3,500) | unsplit | 143.13 | +0.070% | 50 | 129.3 | 0.9950 | QuantizedFlat [no shard key] |
| 3 | scale-p09 (4,000) | unsplit | 142.93 | +0.000% | 50 | 133.5 | 0.9950 | QuantizedFlat [no shard key] |
| 3 | scale-p10 (5,000) | unsplit | 143.26 | +0.203% | 50 | 145.9 | 0.9950 | QuantizedFlat [no shard key] |

### Unfiltered controls

The control has neither library equality nor a partition-key prefix. Recall was not measured for this negative control. Signed RU drift is relative to that arm's stage-1 control.

| Stage | Arm | RU | RU drift vs stage 1 | Documents read | Client ms | Observed index |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | split | 1,203.87 | +0.000% | 25,000 | 2,534.3 | no utilized vector index reported; partial response diagnostics |
| 1 | unsplit | 15.38 | +0.000% | 50 | 144.6 | DiskANN [no shard key]; partial response diagnostics |
| 2 | split | 8,501.07 | +606.145% | 176,599 | 25,597.9 | no utilized vector index reported; partial response diagnostics |
| 2 | unsplit | 20.20 | +31.339% | 50 | 263.4 | DiskANN [no shard key]; partial response diagnostics |
| 3 | split | 11,779.51 | +878.470% | 244,773 | 31,997.6 | no utilized vector index reported; partial response diagnostics |
| 3 | unsplit | 22.81 | +48.309% | 50 | 256.7 | DiskANN [no shard key]; partial response diagnostics |

### Same-question omission comparison

Both requests use the same `scale-p01` question 0 vector. RU, documents and latency are medians of three repeats; the ratio divides the RU medians. The filtered request keeps the application projection, library equality and partition-key prefix. The global control projects ID only and omits both the equality and prefix. The ratio compares these complete request shapes within each index layout; it does not isolate the SQL predicate alone.

| Stage | Arm | Filtered RU | Global RU | Global ÷ filtered RU | Filtered documents | Global documents | Filtered client ms | Global client ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | split | 12.03 | 1,203.87 | 100.07× | 50 | 25,000 | 93.8 | 2,534.3 |
| 1 | unsplit | 70.52 | 15.38 | 0.22× | 1,000 | 50 | 174.9 | 144.6 |
| 2 | split | 12.23 | 8,501.07 | 695.10× | 50 | 176,599 | 98.9 | 25,597.9 |
| 2 | unsplit | 70.52 | 20.20 | 0.29× | 1,000 | 50 | 273.1 | 263.4 |
| 3 | split | 12.23 | 11,779.51 | 963.17× | 50 | 244,773 | 143.3 | 31,997.6 |
| 3 | unsplit | 70.52 | 22.81 | 0.32× | 1,000 | 50 | 307.1 | 256.7 |

### Unsplit versus split RU

Signed percentage difference is 100 × (unsplit RU − split RU) / split RU for the same probe and stage. Positive values mean the unsplit arm costs more.

| Probe (vectors) | Stage 1 | Stage 2 | Stage 3 |
| --- | --- | --- | --- |
| scale-p01 (1,000) | +486.118% | +477.005% | +477.005% |
| scale-p02 (1,000) | +481.023% | +476.268% | +476.268% |
| scale-p03 (1,500) | +858.389% | +858.389% | +858.389% |
| scale-p04 (1,500) | +858.510% | +871.244% | +846.159% |
| scale-p05 (2,000) | +707.405% | +702.867% | +703.401% |
| scale-p06 (2,500) | +597.973% | +597.803% | +597.803% |
| scale-p07 (3,000) | +517.185% | +514.961% | +514.940% |
| scale-p08 (3,500) | +451.175% | +449.338% | +449.233% |
| scale-p09 (4,000) | +399.057% | +397.494% | +397.494% |
| scale-p10 (5,000) | +317.919% | +315.246% | +315.246% |
