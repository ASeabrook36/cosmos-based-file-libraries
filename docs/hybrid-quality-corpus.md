# Fresh hybrid-quality confirmation corpus

The [frozen pack](testing/hybrid-quality/README.md) provides eight new, complete
public DOCX originals and 48 source-authored confirmation questions. It is a
purposeful UK procurement-contract pilot with overlapping terminology and genuine
contract templates. It supports a controlled hybrid-versus-vector-only comparison;
it is not a representative enterprise accuracy estimate or legal advice.

The originals contain about **148,863 cl100k body tokens** by a local, deduplicated
OOXML count. The sum of per-file `ceil(tokens / 5000)` estimates is **32**. This is
not a Microsoft extraction or published-chunk measurement: provider layout,
markup and overlap can change the actual count. Record the actual census before
retrieval and confirm that top eight cannot simply cover the whole library.
The coordinator decides the declared adequacy gate before any outcome is seen.

There are **40 answerable and eight unanswerable questions**, covering exact
terms, names, identifiers, acronyms, section references, paraphrases, concepts,
exceptions and five cross-document comparisons. Tags overlap; the manifest
reports each family count. All questions are fresh confirmation cases. R0/I1
sources and consumed tasks are unchanged. The new original hashes are disjoint
from R0's hashes. No retrieval or model-answer outcome informed source selection,
question writing or labels.

The task source labels bind **104 original regions and 62 required facts** to
original DOCX hashes. Gold is reconstructed from paragraph/table coordinates and
opaque character spans in normalized original text. Text inside inline Word
content controls is included: relying only on `python-docx` paragraph `.text`
would omit some original cross-reference text. Empty strings are never valid
required evidence. Actual contract parties, prices, selected drafting values and
completed test results are appropriately absent from these templates; absence
questions have explicit missing-answer criteria and no recall denominator.

Required full-region and fact-anchor coverage are delivery diagnostics. Answer
quality is assessed against the meaning requested by the question, including its
conditions and exceptions, rather than verbatim reproduction of every surrounding
sentence in a gold region. Unmatched hits remain **unjudged**, not automatically
irrelevant. General precision and unsupported-answer judgments require independent
original-source review. A source/file hit alone is insufficient evidence that its
required passage or fact reached the model.

The public pack contains questions, original hashes, source URLs, metadata,
coordinates and text hashes. Original bodies, reconstructed facts, answers and
runtime identifiers remain outside the repository. Replay must pass only `case`
and `question` to the answering model; labels, answerability, source IDs and family
tags are scoring inputs. Keep the frozen answering model, 200k context allowance,
output limit, ingestion recipe, selector adapter, embedding configuration, search
limits and other comparison controls unchanged.

Reconstruct or verify with the commands in the pack README. Network acquisition
is explicit and hash-checked; existing originals and gold are never overwritten.
No automatic setup provisions cloud resources or reads credentials. This package
changes no production ingestion, retrieval, permission or lifecycle behavior.
