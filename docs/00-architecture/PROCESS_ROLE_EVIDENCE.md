# Independently instrumented process-role evidence

This increment is stacked on PR #128 under issue #129. It changes neither its
pilot authorization boundary nor the canonical laboratory branch.

## Evidence boundary

`ProcessRoleStudy` composes `RoleStudy`, `Observation`, `Hypothesis`, `RoleClaim`,
`ObservationRequirement` and `GraphStore`. It consumes separately admitted local
experiment traces from a configured `TraceProtocol`; it has no collection,
execution, grant or network interface. The protocol configuration selects an
expected archive origin/resource/provenance source. It is **not authorization**.

A trace describes an independently instrumented event: subject source/resource/
identity, recording date, optional occurrence date, optional affected-resource/
identity. These protocol semantics are explicit trusted integration knowledge.
They are not inferred from opaque business field names, and the experiment does
not claim to discover the meaning of an arbitrary unlabelled trace format. A
separate origin alone does not prove independence or truth; instrumentation,
clock quality and subject correlation require independent review.

The fake business schema is discovered first through the existing metadata pilot.
Its resource and field identifiers are opaque. All discovered date fields compete
for event-date and recording-date interpretations; reference fields compete for
matches to the affected object reported by the trace. No restaurant vocabulary,
KPI, business field mapping or first-date default is used.

## Validation and limits

Two distinct source identities with matching business-record and independent
trace evidence can validate a **sample-scoped match**. Every supporting or
contradicting match carries both evidence IDs, plus schema provenance. A
counterexample invalidates the universal sample-match claim even when earlier
support remains. Confidence describes evidence support and is not a calibrated
probability or a promotion threshold.

Multiple matching fields, indistinguishable event/recording anchors, changing
subject versions without revision anchors, and the same affected identifier
reported in different resources withhold validation. Malformed or unsupported
field values do not establish roles. Unmatched trace subjects supply no support.
Target resources must have been discovered; matching an affected ID does not
establish that the field expresses ownership, that an object exists independently
of the instrument report, or that a causal relationship has been proven.

The graph contains current sample-scoped matches or contradictions. Commercial
meaning and causation remain unknown. Event/recording-date correlation does not
prove a field's intended meaning for all future records. Numeric business meaning
is still unresolved. The instrument's semantics and independently established
subject association are the essential additional evidence, not something the
reasoner manufactures.

## Governed next observation

The planner proposes a two-row temporal or affected-object trace sample with an
explicit window of at most 31 days and all necessary protocol/audit fields.
Every selected field needs a trusted public sensitivity classification. Missing
or sensitive classifications deny selection. A deterministic unresolved-claims
per field-cell heuristic selects within the budget; contradictions receive extra
weight and already observed windows are skipped. This is not optimal active
learning, and it does not yet plan subject-specific trace filters.

The proposal is not a grant and cannot execute. The local experiment separately
approves the exact trace fields, date window and row ceiling through
`launch_pilot_read`. Metadata grants, absent grants, expired grants and revoked
grants cannot collect traces. Each consumed observation and its original request
must match the trusted admission archive, including tenant/company/source/resource,
protocol fields, evidence kind/source, time bounds and provenance. Batch admission
is atomic, bounded to 25 rows and 100 retained trace observations. Claims recheck
the archived evidence and scope each time.

There is no production connector or archive wiring. This is not same-process
Python sandboxing. Process trace state is not yet included in role checkpoints;
reloading a base RoleStudy must not be described as restoring these trace claims.

## Deterministic experiment

Run `pytest -q tests/test_process_roles.py`. A local two-resource schema is
initially unfamiliar. A separate fake trace stream reports event/recording dates
and affected objects without reading business field values to manufacture the
answers. Reversing only business dates changes the candidate matching the event
anchor. New conflicting trace evidence invalidates previous support. Equal
anchors, target ambiguity and revision ambiguity remain unknown. No real system
is contacted, no customer data is used, and nothing is merged or activated.
