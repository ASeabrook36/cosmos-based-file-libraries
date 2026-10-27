# Portable retrieval evidence and replay pack

This pack preserves the development-relevant material from the issue #2 smoke checks, two eight-question manual runs and three casual-reference cases (the last case has three turns). It was committed at the user's request for reproducible development and review without relying on uncommitted local files.

## Entry points

- [Current retrieval contract](../../agent-retrieval.md), [specification](../../spec.md) and [build plan](../../build-plan.md): implementation and development status.
- [Evidence review](../../consults/2026-10-27-agent-retrieval-test-review.md): measured results and limits.
- [Original strategy](../../consults/2026-10-27-agent-retrieval-strategy.md): rationale for the subsequently implemented capability package; [planned roadmap](../../consults/2026-10-27-retrieval-roadmap.md): future work.
- [Exact original fixture files and hashes](../../../tests/fixtures/retrieval-review/README.md).
- [Matched questions](matched-questions.md), [reviewer checklist](reviewer-checklist.md) and [results template](results-template.md). Only questions 1–8 were run in the recorded pair; questions 9–10 were optional and have no recorded outcome.
- [Exact casual cases and reviewer findings](casual-cases.json). Preserve the spelling/ambiguity during replay. Do not inject reviewer findings or expected answers into the agent.

## Reproduce the scenarios

Use an authorized deployment configured through the [infrastructure guide](../../../infra/README.md) and [demo setup](../../../README.md#run-the-demo). Keep its credentials/settings outside Git. Upload the unchanged [fixture bytes](../../../tests/fixtures/retrieval-review/README.md); create one library per file and a combined library for the paired comparison. Resolve fresh document/image IDs from tools; sanitized historical placeholders are not live selectors.

The original pair used default agent instructions, the same deployed chat model, a 48,000-token context budget and enabled image inputs. Each eight-question suite ran consecutively in one conversation, separate libraries first. The casual suite started a new conversation and retained preceding casual turns. Independent image descriptions and different model/tool paths prevent treating these runs as a controlled library-layout benchmark. Keep the same source/model/budget/question order for future comparisons and state any differences.

The four-format smoke harness is documented by `python -m scripts.phase3b_exit --help`. The opt-in `scripts/evaluate_agent_retrieval.py` replays against existing libraries without ingestion/index writes. Keep harness state and raw captures outside the repository, publish sanitized findings, and retain content-free routine telemetry. Source comparison and failure review can also use the committed answers, original files and source-region responses offline. Do not hard-code these questions, pages or expected answers into production routing.

## Captured outputs

- [Separate-library visible conversation](captures/separate/visible-chat.txt) and [per-turn model/tool metrics](captures/separate/turns.json).
- [Combined-library visible conversation](captures/combined/visible-chat.txt) and [per-turn model/tool metrics](captures/combined/turns.json).
- [Casual case 1](captures/casual-01/visible-chat.txt).
- [Casual case 2](captures/casual-02/visible-chat.txt) and [expanded retrieval traces](captures/casual-02/expanded-traces.txt).
- [Final three-turn case, with preceding context](captures/casual-final/visible-chat.txt), [expanded traces](captures/casual-final/expanded-traces.txt), [new questions](captures/casual-final/final-questions.json) and [per-turn metrics](captures/casual-final/turns.json).
- [Initial real-model smoke answer 1](captures/initial-smoke/answer-1.json) and [answer 2](captures/initial-smoke/answer-2.json), including the model messages that the harness retained. Signed image links are redacted.

These are observed outputs, including errors and overstatements, not gold answers. The ordinary journals did not retain complete raw model/tool traffic for the manual runs. Visible captures and metrics cannot reconstruct unlogged native image inputs; do not claim otherwise. The current user-assistant conversation history was retained between turns while tool results/native inputs were not. No hidden extra accuracy grade is implied.

## Visual and source evidence

[assets](assets/) contains the four initial fixture renderings, the three additional report figures finally shown, and complete source-page renders 26/46/55 used to check directly relevant context/memory evidence. Full source fidelity is available from the unchanged report fixture; captured browser renderings can be scaled or cropped by the viewport.

Key examples: [map](assets/map.png), [sign](assets/sign.png), [rainfall](assets/rainfall.png), [augmentation diagram](assets/augmentation-diagram.png), [effect-size panels](assets/effect-size-panels.png), [employment panels](assets/headcount-panels.png), [occupation scatter](assets/occupation-scatter.png), [source page 26](assets/source-page-26.png), [source page 46](assets/source-page-46.png), [source page 55](assets/source-page-55.png).

[diagnostics](diagnostics/) preserves the separate read-only probes: one grouped versus eight ungrouped results for the original verbose query, focused topic searches, the targeted memory index check, source token counts and the complete projected service-level read. The ungrouped verbose search still favoured introductory material; these outputs do not prove that flipping one switch alone solves the question. Diagnostic calls are excluded from original user-run totals.

## Sanitization and interpretation

Runtime library/agent/document identifiers and user-chosen test-agent/library labels were replaced with role labels. Tool names are correspondingly normalized. Opaque placeholders preserve relationships where useful but are **not live IDs** and must not be used for API calls. Deployment settings, local paths, credentials and signed links are omitted/redacted. Original prompts, source filenames, answers, numerical observations and public/synthetic source content are preserved for replay. Explicitly retaining this user-authorized test corpus does not change the application's no-content-logging policy.

The compact/full-response artifacts preserve the distinctions between generated descriptions, source captions, source body text and model interpretation. Do not turn a generated description or an observed model answer into a verified source fact. Read the report's actual pages before making statistical claims. Source assets retain their original attribution; no new reuse permission is asserted.

[manifest.json](manifest.json) provides byte sizes/hashes for exported artifacts. Summary records are in [records](../../../records/README.md); the day review links their measured scope and caveats. Obtain fresh authorized settings and resolve fresh IDs when replaying against a deployment; historical runtime credentials/IDs are not required.

Subsequent user-authorized implementation and [four iterative frozen-corpus replays](agent-fixes/README.md) preserve the new capabilities, actual answers, measured costs, regressions and remaining model-quality gaps. See the [current contract](../../agent-retrieval.md) and E49.
