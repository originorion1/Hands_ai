# Reviewer instructions

Review the frozen author package, generation/oracle record, manifest, and actual
exposure chronology independently of the preparer. Use your actual identity and
role. Disclose AI assistance and what it could access. Do not invent identity,
accreditation, chronology, source independence, or review evidence.

## Review obligations

Verify and record an explicit acceptance or rejection for each of these facts:

- the preparer and generating process are identified and their prior ORION
  source/protocol/result exposure is accurately disclosed;
- the package, future stages, oracle, generation record, and manifest were
  genuinely fixed before any learner execution;
- raw SHA-256 bindings reproduce from the retained files;
- no internal ORION fixture, proposed semantic mapping, prior result, target
  total, evaluator label, or unreleased future record leaks into learner input;
- development and evaluation representations are meaningfully different, not
  merely renamed or padded with irrelevant fields;
- technical identity/company/date declarations describe adapter scope and do
  not encode semantic answers;
- canonical anchor records and origin lineage describe the author's operational
  model faithfully, including the claimed collector/fact roots;
- operational arithmetic, units, cohorts, dates, release ordering, and sealed
  oracle agree with the author's independent model; and
- the real authorship/review times and synthetic cutoff/start obey the frozen v1
  chronology without fabricated timestamps.

Do not change records, the oracle, mappings, thresholds, or expected results to
help ORION. Reject unsupported independence or lineage claims. Keep the oracle,
unreleased stages, generation record, and evaluator-only commitments outside
learner access.

## Runner review-evidence document

Only after the review supports it may the package declare review status
`VERIFIED`. Produce a separate JSON object containing exactly:

```json
{
  "review_version": "orion-independent-review-evidence-v1",
  "dataset_id": "dataset-...",
  "protocol_sha256": "92c5dcd63238dcd4429b3ea563b6413592e8277595f3be652db0fa13e5a746c1",
  "dataset_material_sha256": "...",
  "prepared_by": "...",
  "fixed_at": "...",
  "reviewed_by": "...",
  "reviewed_at": "...",
  "review_scope": "..."
}
```

The raw SHA-256 of this exact review-evidence file becomes
`authorship.review.evidence_sha256` in `package.json`. The dataset/material,
protocol, people, and timestamps must match the package and preflight result.
The reviewer must differ from the preparer. A digest proves file equality only;
it does not authenticate either identity or establish that review occurred.

Do not issue this file if the evidence is incomplete or rejected. Keep review
`NOT_PROVEN`, identify the exact failed obligation, and return the evidence
needed for correction without trial-running ORION.
