# Dataset-author instructions

You are preparing synthetic organizational evaluation material for a frozen
learner. Read only the supplied public packet. Do not inspect the repository,
learner implementation, internal fixtures or tests, prior ORION outcomes or
scores, or proposed semantic answers. Do not execute ORION or trial-score the
material.

## Disclose authorship and exposure

Record your actual identity and role, preparation and freeze times, generating
process, seed, source materials, and all exposure to ORION source, protocol, or
results. State whether AI assisted preparation and what it could see. Never
invent a human reviewer, accreditation, independence basis, chronology, or
`VERIFIED` status.

The supplied protocol reveals the supported product question and fixed methods.
Describe that exposure accurately. Independent authorship is a separation of
preparation and evidence, not ignorance of the protocol. Synthetic collectors
are not real independent organizations; disclose how collector domains and
facts were generated and why any asserted separation is meaningful.

## Construct and freeze the material

- Create opaque development and evaluation environments within the pinned
  schema. Do not encode business roles or answers in identifiers or metadata.
- Use meaningfully different operational representations, such as flat records
  versus related header/detail resources, with realistic ambiguous numeric,
  date, and reference fields.
- Ground process evidence in your own synthetic operational model. Do not copy
  mappings or observations from ORION fixtures.
- Supply bounded operational records, never target totals or evaluator labels.
- Include truthful record lineage and at least two genuinely distinct collector
  domains as required by the public contract. Renaming the same fact or source
  does not create independence.
- Freeze the historical material, all four future release stages, and a
  separately retained expected-results oracle before any learner execution.
- Derive the oracle from your own operational model, not from ORION output.
  Keep the generation method, seed, oracle, and their hashes outside learner
  inputs.
- Follow the frozen two-development/two-evaluation cohort design. Do not claim
  statistical significance, economic value, production readiness, or
  real-organization generalization from four synthetic cohorts.

Use the technical identity, company, date, authorization-reference, anchor, and
lineage fields exactly as documented in `WIRE_CONTRACT.md`. They are trusted
adapter/controller mechanics. They must describe true synthetic scope and
provenance and must not be used to tell the learner which opaque business field
is the answer.

## Chronology constraint

The frozen v1 preflight compares real authorship/review timestamps and the
synthetic experiment clock in one UTC ordering:

```text
prepared_at <= discovery_evidence_cutoff <= fixed_at
            <= reviewed_at <= evaluation_clock_start
```

Use truthful actual preparation, freeze, and review timestamps. Choose the
synthetic cutoff and evaluation clock so this ordering is true; do not backdate
or future-date authorship or review events. Historical records and instrument
observations must be on or before the cutoff. The four outcome releases must be
exactly one, two, three, and four days after `evaluation_clock_start`.

This constraint means an arbitrary historical or far-future synthetic epoch may
not conform to v1. A conforming package remains possible by anchoring the
synthetic clock around the actual freeze/review chronology. If your intended
dataset cannot truthfully satisfy this, stop and report the concrete v1
incompatibility; do not alter the protocol or fabricate times.

## Deliverables

Return `package.json`, the sealed expected-results file, the exposure/authorship
and generation record, and a manifest binding all files. Set review status to
`NOT_PROVEN` until a real separate review occurs. Review evidence is produced by
the reviewer, not by you. Validate JSON syntax and the public wire contract
only; do not run the learner.

Before review, required review fields must explicitly describe that no reviewer
is assigned and must not be presented as evidence. The final package becomes
eligible for independent preflight only after the separate reviewer supplies
the bound review-evidence file and truthful `VERIFIED` fields.
