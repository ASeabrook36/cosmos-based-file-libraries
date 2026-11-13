# Large-library exact-reference pagination repair

This addendum records a validation repair after the frozen large-library protocol
started. The original protocol, driver, application query, inputs, thresholds,
arm order, budget and clock remain as recorded at launch.

## Observation retained

The first routed, forced-exact query for the 25,000-vector library in the unsplit
arm returned twenty distinct candidates across two HTTP responses despite
`SELECT TOP 10`. It read 25,000 documents, cost 1,687.80 RU and took 5,215.1 ms.
The cardinality guard stopped execution. Its raw observation and charge are
retained. Taking its first ten candidates would be incorrect: an independent
exhaustive cosine calculation found only eight of the correct ten there.

The installed Python client's default asynchronous execution context concatenates
continuation results. Its query-plan pipeline has separate vector ordering and
TOP aggregation. The observation is consistent with missing aggregation across
partial responses; it does not establish that every vector query follows that
path. Adding a score projection alone does not select that pipeline.

## Repair fixed before resuming

Keep every Cosmos query exactly as frozen, including forced-exact SQL and routing.
Do not truncate candidate lists or replace first-call measurements with new calls.
Instead calculate an independent exhaustive reference from the exact published
vectors assigned to each measured library, rounded to the stored float16 type,
and the frozen float16 question vectors. Compute cosine distances using float64
arithmetic over every eligible vector, with finite-value and nonzero-norm checks.
Include the complete final container for the global reference. Verify the
original corpus prefix and each new payload against their frozen hashes.

For **every** Cosmos exact-reference observation, including the pre-load baseline
and observations that already returned ten candidates, require its complete
candidate union to contain all ten independently calculated nearest neighbors.
Require a tenth-to-eleventh distance margin greater than `1e-10`. Missing candidates,
ambiguous boundaries, errors, incomplete diagnostics or ownership failures stop
the run. Preserve the full raw candidate list and derive a separate reference
with the independently verified ten IDs. Never apply this correction to an ANN
application search or control: its returned cardinality must itself be valid.

Bind the offline references, repair sources and this addendum separately to the
original measurement binding. Keep both versions of provenance and the original
guard failure. Revalidate cached observations on resume. Report the number of
references checked, how many needed candidate merging, their raw candidate-count
range, numerical boundary margins and the hashes needed to reproduce the repair.

This repairs reference aggregation; it changes no data, index policy, measured
application SQL, partition routing, question set, repeat count, phase order or
predeclared comparison. All original queries and costs remain in the record.
The twelve-hour initial deadline and the delayed-sentinel time measured from the
last write are unchanged. The additional local calculation and pause are part
of the initial elapsed time. Differences in offline and service floating-point
arithmetic remain a limit, bounded by the complete candidate-set checks and
reported boundary margins.

Also calculate references using the original published values and using rounded
corpus values with subnormals flushed to zero. Report any changes in the top-ten
sets, boundary margins and candidate-union coverage under these sensitivity
calculations. These checks describe numerical limits; the declared primary
reference uses rounded float16 corpus values and the frozen query cache.

## Client sources inspected

- [Python client 4.17.1 asynchronous dispatcher](https://github.com/Azure/azure-sdk-for-python/blob/azure-cosmos_4.17.1/sdk/cosmos/azure-cosmos/azure/cosmos/_execution_context/aio/execution_dispatcher.py)
- [Python client 4.17.1 vector ordering aggregator](https://github.com/Azure/azure-sdk-for-python/blob/azure-cosmos_4.17.1/sdk/cosmos/azure-cosmos/azure/cosmos/_execution_context/aio/non_streaming_order_by_aggregator.py)
- [Cosmos query pagination](https://learn.microsoft.com/en-us/cosmos-db/query/pagination)

These sources explain the repair design. The retained responses and exhaustive
checks provide the evidence for this run.
