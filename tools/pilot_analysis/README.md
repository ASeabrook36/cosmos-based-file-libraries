# Versioned pilot delivery analysis

These files preserve the exact offline helpers accepted during the four-cell model pilot. The measurement producer and answering controls remain frozen. The helpers are outside `scripts/` because they were reviewed separately after local context stops exposed a limitation in the producer's final-wire equality verifier.

The normal path still runs the original verifier. The narrow corrected path proves that actual provider input is an exact prefix of locally captured tool replies, reconstructs the original context guard, and credits only the replies actually submitted. Locally retrieved but unsubmitted source text remains excluded from answer-evidence scoring; its cost and latency remain recorded.

- `analysis_v2.py` preserves the first accepted no-content refusal analysis.
- `analysis_v3.py` additionally proves the original discovery disposition on a no-content refusal.
- `analysis_v4.py` additionally requires an explicitly accepted, hash-bound source-provenance sidecar for the proven successful outline/search local-stop shape. It rechecks all raw input, independent-review and source-artifact hashes, plus tool budgets and the final guard. An arbitrary source-bearing tail remains an error.
- `prepare_reviews.py` creates provisional blinded packets and later verifies their byte identity against finalization.

Prior v1 packets must pass the unchanged original verifier. Prior v2/v3 packets require the exact pinned historical helper and acceptance bytes and must pass that historical verifier. Final review verification still compares regenerated packet and source bytes. No historical packet, raw answer or scoring rubric is rewritten.

Private raw inputs and the actual acceptance/sidecar receipts belong in the private evidence archive. CLI invocations must use the frozen producer checkout and its Python environment; each operation fails if the producer hash or acceptance binding differs. Use `--help` for arguments. Source-bearing v4 analysis requires `--case-proof` as well as the accepted analysis receipt. Preparing new proof receipts does not authorize additional model calls.

The committed regression tests use generated fictional source text, tool outputs, provider usage, temporary files and approvals. They do not access the captured experiment, private corpus, credentials or a cloud service. Unit compatibility tests preserve the actual historical helper source hashes, while replacing only the approval hash with a generated fixture receipt; this exercises approval binding without publishing private receipts.

```sh
.venv/bin/python -m pytest -q tests/test_pilot_analysis_v3.py tests/test_pilot_analysis_v4.py
.venv/bin/ruff check tools/pilot_analysis tests/test_pilot_analysis_v3.py tests/test_pilot_analysis_v4.py
```
