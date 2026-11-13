# I1 selector integration merge candidate

This candidate starts at consolidated I1 `3980333ee8453af48c7133cab24522868701d1cc`
on `codex/i1-selector-integration`. It incorporates main's independent setup
commits through `1d66a5f6c02f00f09d85649a27e332c7cf7e7151` without changing main.
The integration preserves the accepted selector implementation and measured
agent prompt. It does not select an ingestion provider or alter storage policies.
The incremental change described here is relative to `3980333`. Merging this
candidate into main also brings the already consolidated modular-ingestion,
retrieval and I1 work; it is not an adapter-only diff against main.

## Resulting behavior

Normal `IndexClient` instances request `selectors=handles` consistently for tool
definitions, discovery, search, document reads and image inspection. The existing
tool guidance instructs the model to copy pinned selectors and use outlines when
source coordinates cannot support direct expansion. Evidence remains compact.
Returned source text, citations, completeness, permission checks and budgets
retain their existing contracts.

Explicit rollback is `DEMO_SELECTOR_MODE=legacy` followed by a demo-process
restart, or `IndexClient(..., selector_mode="legacy")`. An explicit mode passed
to `IndexClient.from_env` overrides the environment. Invalid configuration fails
instead of silently falling back. No runtime retrieval error drops pins or
switches to legacy mode. Custom adapters lacking selector support retain the
existing legacy compatibility behavior.

The public REST default remains legacy. The reference evidence encoding remains
optional. Historical R0, recovery and model-comparison CLIs explicitly select
legacy; P1/I1 factories already select each cell explicitly. Frozen replay
reproduction must still use its recorded commit: adding code here changes code
hashes and does not authorize bypassing replay guards or reusing consumed cases.

## Integration review and validation

Independent code reviews checked normal/default wiring, explicit fallback,
historical controls, authorized HTTP round trips and cumulative integration scope.
Main's setup work caused documentation add/add and content conflicts. Resolution
retains I1 history, main's environment-discovery documentation and its validation
receipt; `scripts/dev` keeps its executable bit and unchanged contents.

The [local validation receipt](../records/i1-selector-integration-20261102.json)
records 1,108 passing tests plus lint, formatting and privacy checks. New local
tests use the real API/client and scripted model replies with fake storage/providers. They cover pinned
search-to-outline-to-selected reads, library binding, stale/deleted/foreign
selectors, atomic budget refusal, bare-ID rejection and explicit legacy mode.
They establish integration mechanics rather than model judgment or real Azure
behavior. No live canary, cloud provisioning, held-out replay or billing
measurement is part of this work.

The original I1 result remains 18.8542783% lower measured provider input, below
its original 20% target, with four avoided recovered scope errors and no observed
quality loss on that bounded pack. This integration does not generalize those
results or claim new latency, recall or cost gains.

## Preserved boundaries and open gates

- The hybrid comparison retains its own branch, worktrees, frozen controls and
  resources. This candidate performs no cloud operations or writes there.
- Index versioning stays on its separate branch. The likely integration overlaps
  are shared documentation; no additional storage/worker changes are introduced
  beyond the consolidated I1 baseline.
- `current-v1` remains the default ingestion profile. The optional Microsoft
  adapter remains available, with provider ownership/quality/cost selection open.
- Hosted Entra authentication, real broker redelivery, sustained concurrent
  replacement/deletion, global revocation and large split-library search remain
  separate acceptance gates.
- The known M1 reconciliation limitation remains: scheduled orphan discovery
  targets a literal `files` Blob container rather than the configured container.
  This selector candidate neither repairs it nor claims production cleanup
  acceptance. Retain that issue for a separate lifecycle change.

Merge this candidate only as the reviewed prototype integration. When the API
already runs the consolidated I1 implementation, enabling selector handles needs
only an updated normal client process; no worker redeployment, data migration or
re-ingestion is needed for that selector change. An environment still on main
needs the full compatible API/adapter rollout and any applicable worker/config
updates described in the [restart guidance](../README.md#validate-image-support-and-prepare-a-change)
and [modular ingestion runbook](modular-ingestion.md). An older API must not be
assumed to implement selector handles merely because the client requests them.
No deployment was performed for this candidate.
