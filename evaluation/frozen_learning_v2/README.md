# Frozen learning evaluation v2

This successor contract corrects only the chronology envelope from v1. The
frozen learner, operational material projection, synthetic experiment clock,
question selection, normalization, prediction, revision and scoring remain
unchanged.

The two clock domains are independent:

- Synthetic time orders historical evidence, prediction commitments and the
  four releases at `evaluation_clock_start + 1/2/3/4 days`.
- Real controller time orders receipt/freeze observation, separate review and
  run start. It is never compared with the synthetic timestamps.

An original preparation time may be `UNKNOWN` only with `timestamp: null` and
a reason. A local receipt does not repair or certify that unknown history.
Hashes bind bytes and canonical material; neither hashes nor local timestamps
authenticate a person, organization, independence claim or trusted time.

## Identities

Canonical JSON is UTF-8, keys sorted, compact comma/colon separators, and
non-finite numbers forbidden. `dataset_material_sha256` hashes exactly the
`timeline`, `learner_inputs`, `outcome_releases`, and
`evaluator_only_commitment` object. The v2 `dataset_id` is `dataset-` plus the
first 32 hex characters of SHA-256 over canonical
`{package_version, protocol, material_sha256}`. The receipt separately binds
the exact raw package-file SHA-256.

The receipt binds the raw package, canonical material, sealed oracle
commitment, exposure disclosure and generation record. The reviewer signs a
separate logical envelope that binds that receipt and material; this avoids a
circular package/review digest. The evaluator never parses the sealed oracle.

## Commands

Convert a retained v1 envelope without overwriting it:

```bash
python3 tools/frozen_learning_evaluation.py convert original-package.json \
  --protocol evaluation/frozen_learning_v2/protocol.json \
  --source-protocol evaluation/frozen_learning_v1/protocol.json \
  --exposure-disclosure exposure.json --generation-record generation.json \
  --unknown-preparation-reason "Original exact preparation time was not retained" \
  --output package-v2.json --conversion-report conversion-report.json
```

Create the bounded controller receipt after the v2 package is fixed:

```bash
python3 tools/frozen_learning_evaluation.py freeze-receipt package-v2.json \
  --protocol evaluation/frozen_learning_v2/protocol.json \
  --exposure-disclosure exposure.json --generation-record generation.json \
  --output freeze-receipt.json
```

Preflight only after a separate review envelope exists:

```bash
python3 tools/frozen_learning_evaluation.py preflight package-v2.json \
  --protocol evaluation/frozen_learning_v2/protocol.json \
  --freeze-receipt freeze-receipt.json --review-evidence review.json \
  --exposure-disclosure exposure.json --generation-record generation.json
```

Do not run an external evaluation until the actual independent materials and
review evidence pass this preflight. Candidate-host qualification remains
unresolved. `execution_allowed=false`, `allow_live_customer_access=false`, and
`LIVE_PILOT_READY=false`.
