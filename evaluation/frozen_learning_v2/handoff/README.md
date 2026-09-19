# V2 chronology-correction handoff

Use this successor only when the v1 package cannot honestly satisfy the
cross-domain chronology rule. Retain the original package and disclosures as
immutable evidence; conversion creates a new package and report and never
overwrites either input.

The author supplies the same operational material and sealed commitment, plus
the exposure disclosure and generation record as separate retained files. If
the original exact preparation time was not recorded, declare it `UNKNOWN`,
use `null` for its timestamp and explain why. Do not use a placeholder,
sentinel, conversion time or receipt time.

The evaluator records a local freeze receipt over the exact raw v2 package,
canonical material, sealed commitment, disclosure and generation hashes. A
separate reviewer then reviews that fixed evidence and returns an envelope
conforming to `../review.schema.json`. Review must occur no earlier than the
receipt and no later than controller run start. A digest, name, model, session
or `VERIFIED` string alone does not prove identity or independence.

The synthetic timeline is unchanged and does not represent real authorship or
review time. No handoff, review or external evaluation is performed by issue
#187. No customer data, networking, credentials, merge, activation or gate
promotion is authorized.

The pinned successor packet is the exact inventory in `MANIFEST.sha256`. It
includes the unchanged v1 dataset schema because the v2 schema reuses its
operational-material definitions; this is a schema dependency, not a change to
the v1 contract. Verify it from the repository root with:

```bash
sha256sum -c evaluation/frozen_learning_v2/handoff/MANIFEST.sha256
```
