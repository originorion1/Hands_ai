# Active evidence-request ordering

The eight deterministic synthetic cases cover missing and contradictory
evidence, stale documentation, an ambiguous no-answer case, a case favoring
the fixed sequence, one capability-limited case and one revoked case. Two
synthetic tenants and three evidence periods appear. Outcomes are stored in a
separate file with a later adjudication period. Each strategy freezes a plan
before loading outcomes. Authorization, reviewed request identifiers and a
per-case budget are enforced before any proposed request; no actual request
or connector call occurs. Revocation produces an empty plan.

Fixed invoice-first ordering resolves 4 of 6 cases with a useful authorized
request in 11 request units. Both symptom-first and value-ranked ordering
resolve 6 in nine request units. The two adaptive policies tie. These results
are only a designed workflow fixture; request value estimates and outcomes
were authored for the same small suite, and no independent calibration of
uncertainty reduction exists. Consequently the value-ranked policy has no
demonstrated advantage over the simpler symptom-first rule, and no runtime
implementation is justified. The case favoring fixed order is retained to
show the ranking can choose poorly.
