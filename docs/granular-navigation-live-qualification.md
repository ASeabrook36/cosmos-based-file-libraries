# Bounded granular navigation qualification

This is the live follow-through to source-navigation candidate `01a934c`.
The candidate remains separate from context-accounting repair `4470804`, index
versioning and completed retrieval experiments. No application deployment,
provider switch, main merge or larger confirmation study is part of this run.

The reviewed coordinator composes the normal API, worker, stores, IndexClient and
agent locally. Cosmos, Blob and model calls use the existing isolated Azure
resources and Entra credentials. Every API service is explicitly injected;
there is no Service Bus sender or receiver. An exclusive private ledger records
exact ownership before writes. Unknown outcomes prohibit automatic cleanup.
Deletes verify source/generation/body hashes and use conditional ETag operations.

The prepared limits remain three libraries, four documents, five generations,
64 cumulative chunks, 50,000 local embedding input tokens, 20 logical embedding
requests with at most three attempts each, six answering turns, 36 logical model
calls and 48 physical attempts. Provider token reservations remain capped at
2M input and 200k output. Execution and cleanup have separate 30/10-minute windows.
The embedding guard conservatively includes search inputs in its token ceiling.

The six sealed cases exercise paragraph/containing section, table cell and list
qualification, section/subtree distinction, selected region/full document,
Markdown source block and unavailable locator disclosure. Source pins are supplied
in their task context. This tests controlled navigation; it is not an unbiased
test of free-form document discovery or retrieval recall. The missing old passage
mapping in the sixth task is hypothetical, rather than an actual malformed live
index entry.

Answering uses the existing pinned deployment observed at preflight, with vector
search, compact responses, handles and legacy context accounting. The 200k context
allowance and 8k maximum output are unchanged. A separate conservative 45k-per-minute
request pacing guard leaves at most 37k locally estimated serialized input per
request; its refusal is distinct from a context or retrieval failure. Provider
usage and missing-attempt reservations are reported separately. Local source,
selector and envelope token measurements are non-additive counterfactual estimates.
Reported retrieval RU covers observed instrumented responses; it is not a complete
Azure bill. Tiny fixtures cannot establish ANN index engagement or production cost.

Before mutations, the coordinator verifies actual container policies/incarnation,
the prior canary, current scoped reads, protected inventories and fresh pinned
embedding/answering deployment identity and SKU. A new resource would require a
new canary. Protected inventory changes fail isolation; the runner never repairs
or deletes a protected scope.

Before cleanup, it stops the loopback listener and joins active requests/model
work. It archives private evidence, inventories every owned row and blob, invokes
the normal fenced lifecycle through a separate in-process client, and independently
verifies point-read 404s, scoped TOP 1 emptiness and exact Blob-prefix absence.
Absence and protected preservation are checked again after 60 seconds. Private
archives are hash-verified outside Git. Soft delete/backups and indefinitely late
external writers are outside this proof.

The entry points are `scripts/granular_live_preflight.py` (read-only preflight) and
`scripts/granular_live.py` (single-use execution/cleanup). Runtime configuration,
sources, ownership ledgers, actual responses and provider receipts stay private.
The runner refuses a dirty checkout and freezes its source hashes. Public result
receipts contain sanitized measurements and hashes only.

Hosted authentication/execution, broker redelivery, broad concurrency, long-source
fit and larger answer-quality confirmation remain separate gates. Local runner
tests prove harness mechanics, not live Azure behavior or model judgment.
