# Frozen evaluation handoff packet

This directory defines the only material prepared for an external dataset author
and reviewer under issue #184. No handoff has been sent. Independent material
and review remain **BLOCKED** until real, separate people or organizations return
the required artifacts under their actual identities and roles.

## Pinned packet

Supply exactly these public materials, detached from the repository:

- `../protocol.json`, renamed or retained as `protocol.json`;
- `../dataset.schema.json`, renamed or retained as `dataset.schema.json`;
- `AUTHOR_INSTRUCTIONS.md`;
- `REVIEWER_INSTRUCTIONS.md`;
- `WIRE_CONTRACT.md`; and
- this `README.md`;
- plus `MANIFEST.sha256` as the transfer inventory.

Verify every hashed file against `MANIFEST.sha256` before handoff. The manifest paths
are repository-relative so verification can run from the repository root:

```bash
sha256sum -c evaluation/frozen_learning_v1/handoff/MANIFEST.sha256
```

Do not supply the repository, learner implementation, internal tests or
fixtures, prior outcomes or scores, proposed semantic answers, or repository
result discussions. The protocol necessarily discloses the supported question,
normalization representations, prediction methods, revision rule, and scoring
method. This is protocol-aware independent preparation, not total ignorance of
ORION. A new model, agent, session, seed, branch, or source label is not by
itself independent authorship or review.

## Required returned bundle

The author returns, without running ORION:

1. `package.json`, conforming to the pinned schema and protocol;
2. a sealed expected-results file retained outside learner access;
3. an exposure/authorship and generation record with actual preparation and
   freeze chronology, method, seed, inputs, and hashes; and
4. a manifest binding those artifacts.

An actual separate reviewer then returns the retained review record and, only
when their evidence supports it, the exact review-evidence JSON described in
`REVIEWER_INSTRUCTIONS.md`. The runner's digest checks bind files; they do not
authenticate a person, organization, chronology, or independence claim.

This V1 packet now supports validation and explicit conversion only. Even when
the package and review evidence pass public-contract preflight, they do not
authorize V1 execution. A trusted evaluator must preserve the V1 originals and
use the explicit converter to create a V2 envelope; supported independent
execution then requires V2 freeze/review evidence and separate trusted approval
of the exact review artifact. No customer data, credentials, production network,
business writes, execution authority, merge, activation, or release promotion
is authorized.

`execution_allowed=false`, `allow_live_customer_access=false`, and
`LIVE_PILOT_READY=false` remain fixed.
