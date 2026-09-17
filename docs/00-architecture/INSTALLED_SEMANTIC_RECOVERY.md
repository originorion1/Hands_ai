# Installed synthetic semantic recovery — issue #169

This increment connects the existing admitted evidence owner to canonical
`RoleStudy`, `SemanticStudy`, reference-only semantic checkpoints and
`SQLiteStudyCheckpointStore`. The checkpoint tables reside in the existing
`evidence.db`; no parallel archive, evaluator or graph-as-truth loader is added.
The existing protected process owns evaluation, lookup and persistence. Reasoning
and acquisition receive neither storage mounts nor control-plane keys.

## Bounded operator interface

Existing version-1 synthetic manifests remain unchanged. Version 2 requires the
same fields plus exactly one `semantic` object: `version: 1`, bounded `study_id`,
the packaged `SEMANTIC_EVALUATOR_VERSION`, and `semantic_policy_sha256()`.
Policy is the packaged rules and an explicitly empty reviewed instrument
registry. A manifest cannot supply rules, instruments, callbacks or origins.
The study scope is the canonical tenant/company/source already shared by the
separate metadata and record grants, not caller-selected output scope.

Explicitly enroll the witness once, then start the clean installed
`python -I -m orion.pilot.deployment --serve PRIVATE_MANIFEST` through its
existing private supervisor and profile-bound wheel-only launch contract.
Governed metadata discovery and separately armed record acquisition retain their
original authorization, budget, admission and provenance flow. Protected admission
publishes transport observations separately from `semantic_assessment`. The
assessment is canonical UNKNOWN, with graph node identities/status/provenance,
evidence references, scope and evaluator/policy identity. `AVAILABLE` means
evaluation, durable custody acceptance and retained-witness advancement succeeded,
not that business meaning is validated. `UNAVAILABLE` has no cached world model.

Before successful publication, sequence 1 is committed in the existing semantic
index and its digest/scope/config/archive bindings are accepted in the existing
authenticated evidence custody event chain and tip. Exact evaluation replay is
idempotent. Interrupted unaccepted index insertion fails closed. The assessment
distinguishes durable checkpoint sequence from semantic revision IDs: the latter
are empty because independent observations have not been admitted.

Startup restores from original protected archive evidence and fixed current
policy, re-evaluates conclusions, and compares the current structural cohort.
It does not re-arm acquisition. The fixed private command
`{"command":"semantic","mode":"restore"}` performs the same recovery;
`"evaluate"` attempts durable assessment from admitted originals without source
I/O. No command accepts claims or arbitrary archive selectors. Stop/revocation
and acquisition budgets remain owned by their existing independent services.
Missing, changed or retention-expired evidence, changed study/policy identity,
index tampering or unavailable custody never returns a cached validated graph.
Historical references/index entries remain after ordinary payload retention.

## Required unresolved evidence

The two-configuration installed contract admits schema and bounded records only.
It does not admit a reviewed `Instrument` registry, independent instrument
acquisition scopes, or original collector `Origin` lineage. Field names, model
assertions, repeated reads and invented origin strings cannot supply these.
Consequently independently grounded claims and contradictory semantic revision
through this installed flow are **BLOCKED**. New structural originals preserve
prior evidence/checkpoint history but make the frozen study unavailable; they
are not silently promoted to independent semantic revisions. A future scoped
architecture authorization must define independent instrument admission and
reviewed lineage before extending this flow. Existing canonical grounded
revision tests remain unit evidence, not an installed-lifecycle substitute.

The installed tests use an unmodified synthetic HTTPS source outside acquisition
namespaces, a clean wheel and the actual console entrypoint. They cover discovery,
separate record authorization, UNKNOWN persistence, replay, real supervisor
termination/fresh startup, retained budgets/no re-arm, emergency stop, failed
append, and missing/changed/expired archive originals. Existing WSL regressions
remain required; a skip is NOT PROVEN, never containment certification.

The host/operator, custody owner, clock and reviewed artifact remain trusted. The
separately retained witness now rejects simultaneous rollback of this evidence
database and accepted custody tip while the witness database, key and deployment
binding remain intact. It does not prove whole-host or snapshot rollback
protection; `ROLLBACK_WITNESS_CONTRACT.md` defines the exact claim. Production
collector-root and whole-host rollback attestations, commercial meaning and the
complete release-candidate evidence cannot be manufactured here. This does not
close the complete WORLD_MODEL gate, SECURITY or SECRETS. Every existing release
requirement/status/scanner remains unchanged; the historical WORLD_MODEL reason
is conservative, not an inventory of this implemented subset.

`LIVE_PILOT_READY=false`; `execution_allowed=false`. No customer access, customer
credentials, business-system writes, merge or activation is authorized.
