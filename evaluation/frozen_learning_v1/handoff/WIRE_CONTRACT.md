# Public package wire contract

The JSON schema is authoritative for object shape, bounds, identifier patterns,
and allowed values. The frozen protocol is authoritative for the question,
method, ordering, scoring, and claim limits. This document explains mechanics
needed to encode an independently designed synthetic environment; it does not
provide a business-field mapping or expected answer.

## Opaque structural material

- Opaque identities match the schema pattern and must not contain semantic
  labels. Synthetic source origins use the prescribed credential-free
  `https://s-<opaque>.synthetic.test` form.
- A resource declares only opaque field IDs and structural kinds. Each
  historical batch identifies its resource, authorization reference,
  provenance source, identity field, company field, date field, and bounded
  flat records.
- `identity_field`, `company_field`, and `date_field` are trusted adapter scope
  declarations. Their values establish record uniqueness, exact company scope,
  and admission date. They are not evidence that another opaque field has a
  business role.
- `authorization_id` values are references. They are never grants. The trusted
  evaluator constructs separate bounded synthetic grants after validation.
- All records in one batch have one exact field set. Historical dates are no
  later than the discovery cutoff; released outcome dates equal the matching
  committed cohort date.
- Development and evaluation must have materially different resource/kind
  topology and distinct source, instrument, authorization, and outcome-source
  domains. Adding an irrelevant field or renaming a source is not meaningful
  structural difference.

## Canonical semantic anchors

Instrument records use exactly these public protocol-mechanics fields:

| Field | Mechanical meaning |
| --- | --- |
| `id` | Unique instrument observation identity. |
| `partition` | Exact package company identity. |
| `on` | ISO-8601 observation date, no later than the discovery cutoff. |
| `subject_source` | Exact environment source being observed. |
| `subject_resource` | One declared opaque resource being observed. |
| `subject_id` | Independently meaningful subject within that resource. |
| `evidence_class` | One class declared by the instrument. |
| `channel` | Operational process channel observed by the author's model. |
| `dimension` | Property of that channel supported by the observation. |
| `value` | Bounded scalar observation value. |
| `related_resource` | Related opaque resource when the observed process supports one; otherwise the author's truthful non-answer scalar. |
| `replaces` | Prior observation identity when this is a correction; otherwise the author's truthful non-answer scalar. |

Aggregate-class records additionally contain `component_a` and `component_b`,
the independently observed components supporting the aggregate statement. Do
not learn required channel/dimension combinations from implementation or tests;
derive records from the author's operational model and accept `UNKNOWN` if the
frozen public rules do not support a unique interpretation.

Each instrument record has one matching `origins` entry. `roots` are pairs of
`[collector independence domain, original fact identity]`, not random UUIDs,
URLs, sessions, or copies. Reused original facts retain the same root. `parents`
are bounded UUID references for derived evidence and may not manufacture new
roots. The author and reviewer must retain the generation evidence supporting
these lineage claims.

## Staged outcomes and evaluator-only material

The package contains exactly `development-1`, `development-2`, `evaluation-1`,
and `evaluation-2`. Each release contains one flat batch or a related
header/detail pair and bounded operational records—not a precomputed total.
Future stages remain with the trusted evaluator until the runner observes a
matching durable prediction commitment. The sealed expected-results file,
generation record, review material, and expected score stay outside learner
inputs and persisted learner state.

Package authorization references do not authorize reads. Running preflight does
not execute package code, modules, or callables, and the package must contain
JSON data only.

## Public validation commands

Syntax validation does not prove authorship, review, independence, semantics, or
product performance:

```bash
python -m json.tool package.json >/dev/null
PYTHONPATH=src .venv/bin/python tools/frozen_learning_evaluation.py \
  preflight package.json --review-evidence review-evidence.json
```

The second command is for the trusted evaluator after the real review artifact
exists. Authors and reviewers must not use repeated preflight or execution as a
feedback loop for tuning data or answers.

A successful V1 preflight reports
`VALIDATED_V1_MATERIAL_EXECUTION_UNSUPPORTED`. V1 review JSON binds validation
evidence but never supplies execution authority. Preserve the original files and
use the explicit V1-to-V2 converter for any supported execution workflow; V2's
freeze, review and separately trusted exact-review approval remain mandatory.
