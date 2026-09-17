# Installed independent evidence and semantic revision (issue #171)

This is a bounded synthetic, read-only integration above PR #170. It is not
production activation or independent collector attestation. `LIVE_PILOT_READY`
and `execution_allowed` remain false; existing release requirements are unchanged.

## Versioned deployment contract

Manifest versions 1 and 2 retain their original two-operation behavior. Version 3
requires semantic configuration version 2, the ordered `metadata` / `read` pair,
and one to eight fixed `instrument_0` ... `instrument_7` acquisition operations.
Each operation has its own canonical grant, configuration digest, admission,
request journal, budget and durable controls. Metadata and business-read grants
cannot authorize an instrument. Instruments require EXPERIMENT evidence and the
canonical anchored flat-record protocol. All routes use the existing protected
credential-use gateway and its one kernel-approved HTTPS destination; there is
no caller-supplied URL, transport, policy, callable or archive selector.

The semantic configuration explicitly supplies canonical Instrument descriptors
and a separately reviewed collector registry. Startup validates the policy and
scope agreement before starting custody. The registry binds source, resource and
original record identity to original collector-domain/fact roots and optional
parent anchors. Source text cannot supply or override those roots. Repeated
immutable snapshots can have different separately authorized operations for the
same instrument; explicit replacement records retain their original roots.

## One canonical lifecycle

Authorized metadata discovery precedes separately authorized structural reads.
Both are admitted and retained by EvidenceCustody. RuntimeSemanticCustody obtains
original observations and acquisition scopes from that same archive, constructs
the existing RoleStudy and SemanticStudy, and initially publishes competing
UNKNOWN hypotheses. The semantic planner proposes missing evidence only: it
cannot issue grants, arm an operation, acquire evidence or execute an action.

Separately authorized instrument reads use the existing broker read worker and
admission contract. Semantic custody resolves reviewed lineage to retained
original observation identities and submits acquisition batches in custody order
to SemanticStudy. No new evaluator, grant issuer, evidence archive or persistence
system is introduced. The structural cohort remains frozen: changed structural
records require a new study, not an independent-evidence revision.

SQLiteStudyCheckpointStore appends canonical reference-only semantic checkpoints
in the existing evidence database. Each accepted revision is also pinned in the
existing authenticated custody history, binding scope, policy, evaluator,
registry, dependencies and predecessor envelope. Successful durable publication
follows append and custody acceptance. Exact replay does not append a checkpoint.
Conflicting replay, forks, changed originals, incompatible policy and unaccepted
checkpoint rows fail closed. An append/acceptance failure or pending new evidence
cannot publish an older assessment as current successfully recovered knowledge.

The assessment includes canonical claims, revision history, evidence references,
verified lineage, graph relationships and separately authorized next-evidence
requirements. A fresh installed process reconstructs beliefs and history from
original evidence, not a graph-as-truth loader. Missing, changed, expired or
retention-unavailable dependencies produce UNAVAILABLE without a stale validated
graph. Recovery performs no source I/O, creates no grants or credentials, and
does not re-arm acquisition. Stop, revocation and budgets remain durable.

The version-2 assessment graph encoding factors repeated tenant/schema/default
fields into explicit `node_defaults` and `relationship_defaults`; a node's missing
key defaults to its node ID. History indexes retained historical claim nodes, and
lineage indexes complete acquisition scopes by digest. Expanding these defaults
retains the canonical graph without duplicating it beyond the existing frame
limit. Neither this output encoding nor its indexes are restoration inputs.

## What the synthetic rule establishes

The existing `monetary_measure` rule requires anchored process evidence on the
`settled_transfer` channel and aggregate evidence on `reconciled_transfer`, both
with currency dimension, sufficient subjects, genuinely distinct reviewed roots,
and no contradictory or ambiguous observations. The integrated fixtures use
opaque structural identifiers and two synthetic subjects. Agreement validates a
sampled measurement interpretation under that explicit rule, not revenue,
accounting classification, commercial truth or predictive behavior.

Later, separately admitted explicit replacements retain collector roots and
correct the sampled values. Canonical current claims change while prior evidence,
claim history and checkpoints remain. Other unsupported roles, relationships or
ambiguous dates remain UNKNOWN; confidence and structural resemblance cannot
promote them. One insufficient independent stream leaves monetary interpretation
UNKNOWN and reports the additional evidence and separate grant required.

Different URLs and record IDs do not establish independence. Re-reading,
transforming or copying a fact retains shared roots and cannot create a second
witness. Derived evidence must have exactly its retained parents' roots: it cannot
invent roots. A reviewed registry is a trust dependency, not a truth oracle;
synthetic registry correctness does not prove real-world collector independence.
Cryptographic integrity establishes neither truth nor independence.

## Bounds, operation and remaining gates

The runtime permits at most eight instruments/acquisition operations, 100 reviewed
registry entries, eight roots and parents per entry, lineage depth eight, 25
observations per canonical batch, 100 semantic observations/checkpoints, and a
64 KiB bounded assessment. Existing row, byte, request, rate, retention and attempt
limits apply independently to every operation. Capacity exhaustion denies further
publication or acquisition; it does not trigger unlimited retries or collection.

An operator prepares a private, versioned synthetic manifest and reviewed registry,
installs the verified wheel, explicitly enrolls its retained witness once, and
starts `orion-runtime --serve MANIFEST` using the existing private network fabric.
Startup is unarmed. Explicitly arm metadata,
admit discovery, then separately arm and read structural/instrument operations.
The existing `semantic` evaluate/restore commands publish knowledge only. Use the
existing stop command for durable emergency stop; restarting does not undo it.
This procedure is synthetic verification, not permission for customer access.

Trusted dependencies remain the reviewed policy/registry, host/operator, custody
services, clock and installed artifact. The retained witness detects rollback of
the evidence database and accepted tip only while its separate database, key and
deployment binding remain intact; it does not protect against whole-host rollback
or compromised trusted custody. It does not resume restricted security experiments.
SECURITY and SECRETS and all
other unresolved critical gates retain their complete existing requirements.
Bounded synthetic semantic revision closes neither full WORLD_MODEL nor
SEMANTIC_UNDERSTANDING, PROVENANCE or production-readiness attestation. Any eventual
customer activation requires independent satisfaction of every applicable gate
and separate explicit authority; readiness never activates access automatically.
