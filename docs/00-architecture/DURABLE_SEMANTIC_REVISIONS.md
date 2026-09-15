# Bounded durable semantic revision recovery — issue #167

This offline increment composes `SQLiteStudyCheckpointStore`, `SemanticStudy`,
version-2 `checkpoint_semantic` / `restore_semantic`, and `world_model()`. It does
not serialize a graph as truth, implement another evaluator, or copy evidence
into a second archive. Legacy metadata `StudyCheckpoint` persistence is unchanged.

## Lifecycle

Create the existing SQLite store in an operator-owned local directory. After
admission into the independently supplied canonical archive and semantic
evaluation, call `append_semantic(study, study_id=..., sequence=1)`. Subsequent
checkpoints must extend that study's history consecutively. Exact replay of the
same sequence/content is idempotent; conflicting replay or a history fork fails.
The index is bounded to 100 checkpoints per tenant/company/source/study; each
canonical semantic checkpoint retains the existing 64 KiB limit. At capacity,
append fails without removing history. This is not a global disk quota.

For restart, open the same database (optionally read-only) and call
`restore_semantic(tenant_id=..., company=..., source_id=..., study_id=...,
instruments=..., rules=..., evidence_lookup=...)`. Supply reviewed current policy
and original evidence, acquisition scopes and collector origins independently.
Recovery verifies the index chain and canonical checkpoint before recomputing
claims and projecting the graph. No index entry returns `None`, not validated
knowledge. An incompatible version, policy, changed dependency or unavailable
archive reference raises a fail-closed recovery error; do not publish a cached
validated graph in response. Retention expiry must be expressed by the archive
lookup returning unavailable. Recovery does not invent an archive expiry policy.

Legitimate contradictory observations require new evidence identities and the
existing explicit replacement/origin contract. Append retains earlier checkpoint
and evidence references, and restart reproduces the revised current belief and
historical claim nodes. In-place mutation is not a new revision.

Authorization identifiers and acquisition scopes are historical provenance only.
Recovery neither creates grants nor retrieves credentials. Continuing collection
requires separate existing authorization and admission; a recovered request is
not a permit.

## Evidence and trust limits

`tests/test_durable_semantic_revisions.py` compares canonical graph attributes,
statuses, evidence references, node/revision/relationship identities after actual
SQLite persistence and a fresh interpreter from a clean installed wheel (`-I`,
no checkout or fixture import in the child). Existing synthetic archive decoding
is reused solely in test input. Contract tests cover index replay, conflicts,
scope substitution, history extension, boundedness, corruption and transaction
failure. Existing semantic tests remain the evaluator authority.

The filesystem/index, original evidence archive, scope/origin registry, reviewed
policy and interpreter remain trusted. Checksums detect inconsistent content;
they are not publisher authentication or an independent monotonic witness.
Simultaneous privileged index/hash rollback is NOT PROVEN. No protected pilot
service custody or live deployment attestation is added by this offline index.
Missing historical dependencies fail recovery even if some newer observations
remain available; history is not silently discarded.

The bounded durable semantic index requirement is addressed, not the complete
WORLD_MODEL release requirement. Production custody/rollback attestation,
validated commercial semantics, complete runtime integration and independent
release-candidate evidence remain outstanding. All existing release contracts,
statuses and scanner rules are preserved, including the conservative historical
`world_model_durable_revision_index_missing` reason; it must not be interpreted as
an up-to-date inventory of this bounded increment. SECURITY and SECRETS remain
unresolved. No new containment investigation or security payload is introduced;
restricted security verification is NOT PROVEN by this work.

`LIVE_PILOT_READY=false`; `execution_allowed=false`. No merge, activation,
customer system/data/credential access or external business-system writes.
