# Governed customer value ledger, offline slice

Issue #246. Source dependency: benchmark PR #232 at
`ef67bfe3a6c3ab41391c208957deb0d7d4842c49`.

Run `python -m evaluation.intelligence_benchmark_230.value_ledger` from a
clean installation with the benchmark dependencies. The command reads the
unchanged `results.json` and emits the result in `value_ledger_result.json`.
The source fixture is synthetic: eight designed positives, one ORION
missing-document finding, zero incremental validated discoveries, zero
verified avoidable cost, and zero realized customer savings. The $40.50 and
$4.50 queue review costs are assumptions; a smaller ORION queue also misses
seven positives, so their difference is not economic gain. ORION investigation,
onboarding, support, subscription, and provider delivery costs are unmeasured.
Net value and ROI remain null.

`Finding` pins an evidence SHA-256 and tenant. A nonzero customer cost review
requires a trusted reviewer Ed25519 signature over a domain-separated,
tenant-bound statement and a review evidence digest. A nonzero realized
outcome requires a second trusted signer, distinct from the reviewer, and a
separate outcome digest. Trust keys must come from an operator-controlled
store outside this repository. This offline slice has no production trust
store. Synthetic findings reject nonzero verified or realized values and
reject ROI scenarios. Outcomes and reviews cannot be overwritten within one
ledger instance. Every amount uses decimal cent validation.

For customer data, `roi_scenario` subtracts conventional realized savings,
analyst review, investigation, onboarding, support, and subscription costs
from signed realized savings. It divides net incremental value by those
incremental costs when nonzero. The provider gross-margin scenario compares
subscription revenue with a supplied delivery-cost assumption. All resulting
scenario values carry `ESTIMATED_SCENARIO`; supplied costs and conventional
outcomes are not independently verified by this module.

This is an offline in-memory accounting boundary, not a production ledger.
It lacks durable append-only custody, trusted key lifecycle, external outcome
adjudication, customer data authorization, and a real cost feed. Those gates
remain open before any customer value claim or activation.
