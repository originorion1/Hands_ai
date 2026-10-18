# Scientific reasoning validation

Illustrative **synthetic** holdout case `HOLDOUT-004` is a reviewer candidate, not an independently validated organizational finding. The hypothesis is that this latent organization has the designed issue. The alternative is that both observed flags are ordinary noise; different quality, timing, or local context could explain a real-world analogue, and this lab does not observe those factors. The falsifiable next test would acquire an independent authorized record or obtain an external outcome after the decision; neither occurred here. Causal attribution and financial exposure are unavailable.

Tenant `synthetic-holdout-004`, source `synthetic-utilities-holdout-004`, run `4b5d9419-dfdc-53a9-8978-24d1f886aed6` acquired two read-only source records on `2026-03-02T00:00:00+00:00`:

| Record | Observed answer | Immutable record SHA-256 | Evidence ID |
| --- | --- | --- | --- |
| `HOLDOUT-004-R4` | flag | `3bc926a967170fd6d2796fc9e44c2435af75300f08fe0b291030794d89dd9c75` | `156cf447-88c2-5843-ac06-a7fa67dca5cb` |
| `HOLDOUT-004-R5` | flag | `28a899787df8a1a8d504cc19ec26271cbfdd1d09d3407ca16b9f37d6eb20fca2` | `e732248d-f25a-5ea9-b870-7a2fcf376ad1` |

The training-derived prior is 0.4693877551. Applying the frozen F0 then F1 likelihood rows by Bayes' rule yields posterior 0.9049115689, so the fixed 0.8 candidate threshold requests review. Report digest `ad2af8d740097a62a7cf82c091282be039d1c9ad393c555e6934e28eed0c8030` binds run ID, status, posterior, and the ordered evidence IDs/digests. The post-seal scorer recomputes each source digest and report digest. The designed holdout label is positive, but no independent reviewer has adjudicated the record or its operational meaning. The strong heuristic selected exactly these same two records and reached the same posterior; there is no incremental discovery here. The existing policy acquired R2 clear and R4 flag, recognized contradiction, and abstained at posterior 0.6928529238.

Contradictory answers trigger abstention in the prototype, as does a posterior between 0.2 and 0.8. Missing evidence is recorded for abstentions. A single same-generator posterior is not a calibrated real-world confidence level. The current report lacks a complete explicit alternative-explanation test and source-period causal controls; therefore **SCIENTIFIC_REASONING = NOT_PROVEN** beyond traceable synthetic arithmetic and correct observed-vs-inferred labeling.
