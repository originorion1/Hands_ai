# Minimum evidence-backed organizational learning slice

Status: offline synthetic implementation for issue #180. Deployment
qualification remains **UNRESOLVED**. `execution_allowed=false`,
`allow_live_customer_access=false`, and `LIVE_PILOT_READY=false` remain fixed.

## Traceable component integration

This branch is stacked on PR #179 at
`9b8c6269bb15541e274b443a56852328c79b4344`. It integrates only the existing
canonical learning dependencies needed by this slice; it does not merge their
branches or create another ledger, event core, or outcome system.

| Contract | Original commit | Integrated commit | Reused implementation |
| --- | --- | --- | --- |
| Issue #118 prospective prediction ledger | `f4bc7742ac51ad900556fbd2f46b4212e267e69c` | `81b034318bf840285617fe3040371a94defe563a` | `orion.learning.prediction_ledger` |
| Issue #118 synthetic ledger exercise | `74227f21f9b3b51858abc137d172bdb55ba8db23` | `ce15a193736ff4b2641336e0df13f58f209835f2` | ledger demonstration and regression tests |
| Issues #117/#120 event core and outcome ingestion | `200834ca0ac9c41b6779dc8b89839fca14a5af3a` | `72d07512aee599bba8eb539a7a2444d822cdf8ba` | `orion.events`, `OutcomeEventHandler` |
| Issue #122 cohort-safe scoring | `3c93b93aca6001971ba6f88870ac3e71d6678b54` | `cbd1cfcdc24ce384f7baed9ca43ba8d5e3b3d457` | explicit target/model cohort selection |

The compatibility change extends the same append-only ledger records with an
explicit unit and an optional entity/location/time `BusinessCohort`, adds
immutable evidence-linked model-revision rows to that database, and adds paired
scoring that rejects different cohorts, outcomes, evidence, units, or horizons.
It also rejects duplicate target/model/cohort commitments and outcomes whose
cohort differs from the prediction. Existing constructors and stored prediction
payloads retain the default `binary` unit and optional-cohort compatibility.

## Executed boundary

`organizational_cycle.run_learning_cycle` composes, in order:

1. the existing synthetic F&B metadata/read admission, role study, semantic
   study, and assessment to discover two opaque organizations, with the later
   evaluation organization exposing a structurally different discovered schema;
2. deterministic selection of one independently validated `served_item`
   relationship and a supported sample-sales question whose target, threshold,
   predictions, and outcomes use the same discovery-validated dynamic unit;
3. durable baseline predictions bound to explicit tenant/company/one-day
   cohorts before the corresponding outcome acquisition callback is invoked;
4. separately authorized, admitted development-outcome reads containing bounded
   operational records rather than caller-supplied aggregate labels;
5. the existing durable event queue and trusted outcome handler;
6. an immutable Laplace revision derived from resolved outcome evidence;
7. frozen prior and revised predictions for the same later evaluation cohorts;
8. a different authorized source and structurally different discovered
   environment for the later evaluation outcomes;
9. evidence-supported normalization and aggregation of record values into each
   cohort outcome, retaining record evidence and source-record provenance;
10. unit-, horizon-, target-, evidence-, and cohort-paired Brier comparison; and
11. a real new Python process, launched after the first prediction commitment
    and before outcome I/O, that reconstructs pending canonical measurement
    state without an acquisition callback or restored authority.

The assessment's `next_day_sample_sales` value selects the question threshold.
It is explicitly recorded as **not prospective** because its historical target
date precedes its evidence observation time. Only the new ledger commitments,
made before the callbacks can expose future outcomes, are prospective.

The four aggregate totals remain `150`, `130`, `100`, and `140`, but they are
now derived from two admitted operational records per cohort. The assessment
records each normalized contribution and evidence identifier and carries the
source assessment's completeness limitations; it does not claim that the
bounded reads are complete business ledgers.

The fixed experiment rule is declared before evaluation: baseline probability
`0.5`; revised probability `(1 + observed positives) / (2 + resolved cases)`.
The synthetic development outcomes are `true, true`, producing `0.75`. The
subsequent unseen evaluation outcomes are `false, true`. On those same two
observations, the frozen baseline Brier loss is `0.25` and revised loss is
`0.3125`. Therefore the machine assessment reports:

- `learning_cycle_integration=true`;
- `unfamiliar_environment_evaluation.works=true`;
- `predictive_improvement=false`; and
- the failed `evaluation-1` prediction remains durably resolved rather than
  being omitted.

This is deliberate honest measurement, not a fixture tuned to manufacture
improvement. Structural difference is established by discovery-derived
topology, not by source strings or authorization IDs. Separate grants remain
mandatory but are explicitly not treated as evidence independence. The fixture
and engine share repository authorship, which the assessment discloses.
Synthetic success does not establish real-organization generalization,
operational-record completeness, calibration, economic value, production
containment, or live readiness.

## UNKNOWN and authority boundaries

Missing supported prediction evidence, a validated useful relationship, or a
consistent discovered unit returns `UNKNOWN` before a ledger database or
acquisition is created. Outcome records must pass the existing pilot
authorization and admission boundary with exact tenant, company, date, field,
source, and record limits. Normalization then rejects duplicate source records,
records outside the committed cohort, missing discovered fields, unbounded
batches, and cohort mismatches. Development and evaluation must use distinct
authorization/source domains. Events remain notifications, not truth or
permission.

Fresh-process recovery accepts only the durable ledger path and measurement
identities. It exposes no acquisition port and reports
`authority_restored=false`, `acquisition_available=false`, and
`execution_allowed=false`; continuation is separately authorized only after
that process exits. The later same-process completed-state reopen is named
`durable_completed_state_reopen` and explicitly reports
`process_restart_claimed=false`. A trusted local database and clock remain the
inherited ledger boundary. No customer access, credentials, network call,
write capability, promotion, activation, or deployment qualification is added.

## Verification

The focused integration test uses the actual F&B discovery assessment, actual
`launch_pilot_read` authorization/admission path, actual event queue and outcome
handler, and an actual subprocess recovery. It asserts commitment before source
I/O; explicit non-duplicate cohorts; bounded record aggregation and provenance;
structurally different development/evaluation discovery; separate access
domains; the measured non-improvement; recovery without authority; dynamic-unit
consistency; JSON serialization; UNKNOWN abstention; and all safety flags.
Ledger tests separately cover immutable revision replay/evidence/ordering,
duplicate and mismatched cohort rejection, and paired scoring. The prior
1,947-test result belongs to PR #181's earlier head and is not final-tree
evidence for these corrections. Human review and merge remain separate
decisions.
