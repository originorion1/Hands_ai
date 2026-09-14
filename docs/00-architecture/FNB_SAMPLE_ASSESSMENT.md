# Evidence-qualified F&B sample assessment

Issue #139 extends PR #138 without changing admission, semantic evaluation,
checkpoint, broker or release authority. `orion.business.fnb` consumes the existing
`SemanticStudy`, requires the exact reviewed `FNB_RULES`, and composes the existing
semantic review/audit. It has no acquisition, transport, credential or action API.

## What the increment proves

A local fixture discovers six opaque schemas, admits fourteen business records
and ninety-two independently rooted instrument observations, and reconstructs
identified samples through the existing semantic engine. No operator supplies
business field mappings. The fixture's source encoder knows its own schema; the
assessment does not receive that encoding. Tests also pass both local broker
protocols through actual supervised child processes before assessment.

The reviewed vocabulary has eight numeric, one date and three relationship
roles. It deliberately supports **USD, kg and serving** measurements only.
Independent instrument channels must establish those dimensions and process
roles. Another currency/unit is UNKNOWN, not an automatic conversion. This is a
small explicit F&B domain policy, not autonomous invention of a business ontology.
Instrument meaning, root identity and collection independence remain trusted
external contracts. Synthetic witnesses prove the evaluator's behavior, not the
reliability of a real organization's instruments.

Normalization requires a validated claim AND independent witnesses for the
particular subject and every required evidence class. Validating two examples
never labels all later rows. Identity re-acquisition does not inflate totals;
conflicting identity content rejects. Joins use resource plus identity. Unsupported
references, ambiguous dates, negative/reversal semantics, missing recipes and
unwitnessed records cannot silently become complete transactions.

## Single canonical artifact

`assess_restaurant(study, tenant_id=..., company=..., source_id=...)` returns a
JSON-compatible assessment with resource coverage, observed facts, normalized
cells, independently supported relationships, sample period, semantic states,
contradictions, evidence index, existing audit/checkpoint identity, findings,
unknowns and proposal-only recommendations. The evidence index preserves source,
tenant/company, acquisition time, original observation ID and admission provenance.
Collector roots and evidence classes remain in the canonical semantic audit.

The artifact is an output, not an authenticated import/request/grant. Mutating its
JSON cannot alter the underlying study or create authority. Recompute from the
external admitted archive; do not accept a caller-supplied assessment as truth.
The assessment rechecks the semantic audit identity after composition.

`owner_report` renders this artifact without another calculation or model call.
OBSERVED_FACT, VALIDATED_SEMANTIC_ROLE, INFERRED_CONCLUSION, HYPOTHESIS, PREDICTION
and RECOMMENDATION remain explicit. Confidence is semantic witness agreement,
not a calibrated probability. Every finding has evidence, affected scope/period,
reasoning, uncertainty and an investigation proposal. Action authority is NONE.
The existing semantic graph owns claim identities and revision status; the
assessment does not create a parallel knowledge store or promote findings to facts.

## Synthetic result and limits

The fixture supports $365 gross sales, a $21 first-to-last observed day increase,
$52 / 13 kg purchases, 7.8 kg recipe-based consumption, $31.20 theoretical food
cost, a -25 kg count change and 3 kg recorded waste. These are **sample results**,
not assertions of complete revenue, accounting COGS, profit or physical loss.

Opening + receipts - closing - theoretical consumption - recorded waste yields
27.2 kg, or $108.80 at the observed ingredient cost, **only under an explicit
inclusive-date, complete-movements scenario**. Missing movements, count timing,
recipe validity and counting errors remain alternative explanations. This is a
review hypothesis, never a fraud/leakage conclusion or booked financial impact.

Three consecutive observed sales dates permit an arithmetic-mean baseline of
$121.666... for the next comparable sample day. Its observed $100–$144 range is
not a confidence or prediction interval. No missing day is filled with zero.
Coverage, stationarity, seasonality, future accuracy and outcome learning are
unproven. No forecast deployment or prediction ledger is claimed here.

Labor/attendance, taxes, refunds, transfers, complete BOM coverage, price-effective
periods, opening/closing timestamps and full-ledger coverage are missing. The
current calculations do not prove general restaurant understanding beyond the
reviewed evidence contracts and admitted samples.

## Reproduction

From the repository root with dev dependencies installed:

```
PYTHONPATH=src:tests python -m fnb_lab
python -m pytest -q tests/test_fnb_assessment.py
PYTHONPATH=src python tools/semantic_discovery_gate.py
```

The first command is explicitly a **test-fixture laboratory**, not a live CLI.
It prints the canonical JSON followed by the owner rendering. Tests cover fresh
interpreter restoration through the existing reference-only semantic checkpoint;
missing/mutated evidence and changed policy reject. Restart grants no authority.

Full regression includes existing expiry/revocation, budget/rate/stop, broker,
provenance, tenant and release-denial tests. Successful offline results do not
alter the live readiness matrix. Production custody, mandatory egress isolation,
real instrument attestation and deployment controls remain blocked. No customer
connection, write interface, grant issuer or production activation is added.

## Next evidence for a real pilot

Before financial or loss conclusions, independently establish bounded ledger
coverage, stock count timestamps, unit/currency consistency, adjustments/returns,
and recipe/price validity. Collection still requires separate human-issued
record authority and the unresolved deployment release gates to pass.
