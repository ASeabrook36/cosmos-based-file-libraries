# Approved full routed suite after the delayed sentinel changes

**Separately approved by the user on 2026-11-13. One supplementary epoch only.**
The authorized parent run is
complete and its recorded results remain valid observations of their respective
times. Its delayed q0 sentinels changed RU materially without changing reported
modes, documents retrieved or recall. They cannot substitute for the initial
twenty-question summaries or establish a settled full-suite advantage.

The approved protocol explicitly does not authorize a new full benchmark after
a changed sentinel. This proposal is a separate supplementary measurement epoch,
not an automatic rerun or replacement of the parent experiment.

## Fixed work

Run the same frozen twenty questions three times in each arm for all six measured
libraries: 1k, 5k, 10k, 25k, 50k and 100k vectors. That is **720 routed ANN calls**.
Then repeat the original fixed global ANN question three times per arm: **six
global calls**, for **726 ANN calls total**. Alternate arm order using the
parent's frozen parity schedule. Run this full declared set once; do not repeat
selectively according to favorable outcomes.

Use the unmodified application SQL, projection, TOP 10, float16 query vectors,
library equality and explicit partition prefix for routed calls. The global
control retains its original id-only projection and omits equality and prefix.
Preserve all index defaults and use the already validated exact-reference sets
for recall. There are **no new exact queries, embedding/model calls, writes,
loads, resources, index changes or no-prefix diagnostics**.

Immediately before queries, verify fresh total/per-library inventory, stored
policies and one physical range per arm against the parent final inventory:
429,773 vectors / 1,018 libraries. Stop on unexpected state. Queries must begin
after the parent's frozen last-write upper bound plus twelve hours, which is
2026-11-13 18:40:45.773705 UTC. A later epoch is reported with its actual query
ages; it is not backdated to the original late check.

## Isolation and limits

Use a separate private state directory outside the parent directory and every
checkout. Its status, rows, inventory receipt, binding and ledger are separate.
Bind the epoch to the parent record SHA256
`6df1337e5d54e583b81ba7eee5469d8b67b8b9cd05ab9242bb82952879e17e19`,
the parent end-state file-hash manifest, frozen input/reference/source hashes,
this proposal and the supplementary driver. Validate frozen sources and the
offline exact oracle before execution. Acquire the existing parent run lock
without writing its contents. Preserve **every parent raw file, status, budget,
record and table byte**; compare the parent snapshot again after the new calls.
Any parent change is a reported failure, not something to repair automatically.

The supplement has two new ceilings: **one million additional visible RU**
(illustratively $0.25 at $0.25/million RU) and **thirty minutes elapsed**, with a
new timer that includes validation, inventory, queries and retries. This is a
specific supplementary exception to the old twelve-hour elapsed limit, while
remaining inside the original extension's $20 allowance and reserve policy.
Seed the separate ledger with the parent's 35,008,768.41 known RU; never edit
the parent ledger. Apply the existing 64-million-known-RU reserve threshold to
the combined total, and reserve 300,000 RU before each query within the new
one-million-RU threshold. The threshold observes visible charges and is not a
provider-enforced billing ceiling. Retain charges and partial/error observations
if a request overruns or times out; do not silently reset the epoch or its clock.

Using the maximum observed routed cost/time per arm and library, all 720 routed
calls forecast 71,727.6 RU / 268.4 seconds. Doubling those figures and adding the
maximum observed global calls yields roughly **205,602 RU ($0.0514)** and
**16.3 minutes including four minutes for inventory**. Plan for **10–20 minutes**;
these are extrapolations rather than upper bounds. Stop at the new guards if
actual behavior differs. Approval does not authorize another favorable-outcome
rerun or an increased limit.

## Reviewable implementation and outcome

The [supplementary driver](../scripts/scale_ablation_large_late_suite.py) defaults
to a local read-only dry run showing the 726-call plan, source/parent bindings
and limits. `--execute` is reserved for use after the user's separate approval.
Focused tests cover coverage/order, ledger/time guards, cached-row validation
and isolation. No supplementary service call has been made in preparing this
proposal.

After any approved execution, independently validate all 726 observations and
their unchanged eligible-set exact references, recompute twenty-question RU,
documents, latency and recall summaries, and apply the same final-5k absolute
benefit comparisons. Compare this new epoch with the initial twenty-question
epoch and both q0 sentinel epochs. Report all observations and their ages;
do not overwrite or relabel the parent results as settled. One additional epoch
can test whether the changed q0 observations extend to the full question set;
it cannot establish that the workload is stationary indefinitely. Preserve the
global quality, numerical-reference, synthetic-membership and one-build/range
limits. The original review-before-push instructions still apply. This approval
does not authorize pushing, publication or database deletion.
