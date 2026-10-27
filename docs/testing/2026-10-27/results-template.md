# Separate versus combined library results

A agent name: __________    B agent name: __________
Model: __________    Context budget: __________    Image inputs: __________
All eight document copies text/images ready: __________    Round/date: __________

Quality: pass / partial / fail. Enter ? for unreported metrics. Save before switching agents: the current demo clears the displayed conversation when selecting another agent.

| Test | Agent | Quality | Image/caption | Coverage/citations | Retrieval RU | Model input tokens | Model output tokens | Total s | Retrieval s | Tools / model calls | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | A | | | | | | | | | | |
| Q1 | B | | | | | | | | | | |
| Q2 | A | | | | | | | | | | |
| Q2 | B | | | | | | | | | | |
| Q3 | A | | | | | | | | | | |
| Q3 | B | | | | | | | | | | |
| Q4 | A | | | | | | | | | | |
| Q4 | B | | | | | | | | | | |
| Q5 | A | | | | | | | | | | |
| Q5 | B | | | | | | | | | | |
| Q6 | A | | | | | | | | | | |
| Q6 | B | | | | | | | | | | |
| Q7 | A | | | | | | | | | | |
| Q7 | B | | | | | | | | | | |
| Q8 | A | | | | | | | | | | |
| Q8 | B | | | | | | | | | | |
| Q9 | A | | | | | | | | | | |
| Q9 | B | | | | | | | | | | |
| Q10 | A | | | | | | | | | | |
| Q10 | B | | | | | | | | | | |

Optional extra measurements: query-embedding tokens, cached-input subset, rate-limit wait, and whether RU reporting was incomplete. Do not add cached tokens to input tokens again. Ingestion costs are separate.

## Review after the run

- [ ] Compare correctness and correct-image success first.
- [ ] Compare paired RU and input/output tokens; flag missing measurements.
- [ ] Compare total time alongside provider wait/cache effects.
- [ ] Repeat surprising pairs in reverse agent order.
- [ ] Record redundant reads, unsupported claims and incomplete coverage.
- [ ] Keep conclusions limited to this small corpus and these two platform configurations.
