# Matched test: four separate libraries versus one combined library

A = the agent attached to all four individual libraries.
B = the agent attached only to the combined library containing the same four files.

Run exactly the same prompt against each agent. Main run: eight questions, sixteen answers. Optional checks: two additional questions.

## Before starting

- Wait until text and image processing are ready for the four individual copies and all four combined-library copies. Otherwise you are comparing different available evidence. Text-only availability is not image readiness.
- Use the same model, instructions, context budget and image-input setting for both agents. Enable image inputs for the visual/numeric tests. Attach only the intended libraries and use identical original file bytes.
- Start each answer with an empty conversation. In the current demo, changing the selected agent clears the displayed chat/history. Record the result BEFORE switching agents.
- Convenient main order: Q1 A, Q1 B, Q2 A, Q2 B, and so on. Each switch gives a clean conversation. For a repeat, reverse the order, B then A, to reduce first-run/cache effects.
- Do not paste the review checklist or expected answers into the agent. The questions below are the complete prompts.
- A single paired run is a functional check, not proof that one layout is faster, cheaper or has better recall. Repeat any surprising result before drawing a conclusion.

## Q1 — Localized text, Word

```text
According to the allotment planting guide, when should Swift first early potatoes be planted, and what spacing does it recommend? Give a concise answer and cite the file and location.
```

Checks a small fact without asking for a whole-file read.

## Q2 — Remembered embedded image, Word

```text
I remember a map of an allotment with some plots highlighted as being on a waiting list. Find that map, show it inline, and tell me which plots are highlighted. Include its source caption if one was identified, and cite the file and location.
```

Checks vague visual retrieval, the correct embedded picture, readable labels and honest caption handling.

## Q3 — Text plus embedded image, PowerPoint

```text
In the wetland restoration presentation, how much has the water table risen? Also find and show the trail-sign picture from that presentation and tell me the full circuit distance. Cite the relevant slides and distinguish information from the slide text from information on the sign.
```

Checks ordinary passages and a picture from the same deck without implying they are the same evidence.

## Q4 — Standalone chart and numeric accuracy, PNG

```text
Find and show the monthly rainfall chart for Glen Orrin. Which months were the wettest and driest, what were their rainfall totals, and what was the difference in millimetres? Cite the image file.
```

Checks standalone-image meaning, labels, numbers and arithmetic.

## Q5 — Remembered figure and stored caption, PDF

```text
I remember a diagram in the Future of Work report with circles about what humans can do and what machines could or should automate. Find and show that specific diagram with its associated caption if one was identified. Explain what the diagram means using the surrounding discussion, and cite the page. Distinguish the source caption from section headings or your own interpretation.
```

Checks the intended PDF figure rather than a same-subject distractor. A targeted page-image read is acceptable when semantic search does not supply the figure; reading the entire report is not required by this question.

## Q6 — Complete small document and figure context, Word

```text
Read the entire allotment planting guide, including all its sections. Summarize its recommendations for early spring, summer succession and winter cover. Show its site plan alongside the explanation where it is useful. Cite the file and locations, and disclose if you could not read any part in full.
```

Checks the complete-read path, later sections, useful image presentation and completeness disclosure. The displayed answer need not reproduce the internal section order exactly; inspect the read trace/returned sections if checking stored reading order.

## Q7 — Complete file → supporting passages, multiple sources

```text
Read the entire wetland restoration presentation, including its speaker notes, and summarize its history, restoration plan and results. Then retrieve only the relevant evidence from the other files to tell me the recommended potato planting date and spacing, and the wettest month in the rainfall chart. Show the presentation's trail-sign picture. Keep the findings attributed to their separate sources and cite each one.
```

Guided capability check: a full deck plus localized Word/PNG evidence in one turn. It is not an uncoached routing-quality test. The deck's actual all-sections content must arrive before a complete-read claim; exact cross-tool execution order is secondary to that coverage.

## Q8 — Passages → complete file → other passages, multiple sources

```text
First search for passages about seasonal planting so you can identify the relevant guide. Then read that guide in full and turn it into a spring-to-winter checklist, including its site plan. Finally, retrieve supporting passages from the wetland presentation stating how much land is to be rewetted and how much the water table has risen. Cite each source and disclose any limits on the complete read.
```

Guided reverse-direction check: search identifies a parent file, then a full read, then support from another source. This deliberately exercises tool composition rather than pretending to measure natural routing judgment.

## Optional Q9 — Unsupported connection / source separation

```text
Do the uploaded rainfall chart and wetland restoration presentation establish that the restoration project caused the rainfall pattern shown in the chart? Explain what the sources actually support, and what evidence would be needed to establish that connection. Cite the relevant sources.
```

Checks whether the model resists inventing a relationship between unrelated sources. It may use outside reasoning to explain the evidence required, but must label that reasoning and not invent a causal result from these files.

## Optional Q10 — Large complete-read budget handling

```text
Read the entire Future of Work report, including every available extracted section, and summarize its major themes. If the complete content or visual inputs cannot fit your available context budget, state the limitation clearly. Do not silently replace the requested complete read with selected passages or claim that omitted figures were inspected.
```

Keep the existing context budget; do not increase it just to force a pass. Either a genuinely complete response or an honest, explicit budget limitation can be correct. A full-text read can still have incomplete visual coverage, and native inspection of every image is not implied by complete text. This question may use substantially more tokens and time; run it after the main set.

## What to record for EACH answer

Use results-template.md. Record:

- Correct facts, matching image, correct caption/provenance, source citations and honest coverage: pass / partial / fail / not applicable.
- Cosmos RU reported for retrieval. Mark an incomplete/failed-call total as incomplete, not zero. The demo excludes tool-definition and UI metadata reads from this figure.
- Cumulative actual model input tokens and output tokens separately; total tokens if useful. These are cost units, not file-size estimates. Cached input is a subset of input, reasoning a subset of output: do not add them twice.
- Total response seconds, retrieval seconds, provider/rate-limit wait seconds if reported, tool calls and model calls. Use the expandable Performance breakdown to count calls.
- Tool operations used; flag unnecessary complete reads, redundant figure reads, unrelated images or unsupported claims. More calls are not automatically wrong when they improve the evidence.
- If comparing total operational cost, also collect query-embedding tokens from Library Performance. The Chat model-token total does not include those embedding calls. This is an answer-time comparison; ingestion embedding/vision tokens and write RU are separate one-time costs. For ingestion, compare the SUM across the four separate libraries with the combined library, not one individual library against the combined total.

The comparison includes the platform effect of more library-specific tool definitions and routing choices: A exposes more tools than B. It is not a pure Cosmos query-engine benchmark. Both configurations still use one-library-scoped queries; no cross-library ranked query or globally comparable score is implied. No default ranking or storage changes are being made by this test.

## Minimum useful run

Run Q1–Q4 against A and B first (eight answers). Continue with Q5–Q8 to add the PDF and mixed-retrieval checks. Finish with Q9/Q10 only if wanted. Review answer quality before declaring a cost/latency winner. Report approximate small-set success rates rather than calling this broad recall@k.
