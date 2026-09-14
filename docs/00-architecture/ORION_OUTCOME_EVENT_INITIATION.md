# Offline outcome-event initiation

Status: laboratory implementation for issue #120; independent review pending.
This feature branch composes PR #117 head aa09aa16f1a4daa55cf85033e1094d281122d81c
and PR #119 head 4540fccb2beb093b4a703d29be6ab01eafa6bc4c without changing canonical.

## Run on WSL

From the existing Hands_ai checkout, create an isolated checkout once:

```bash
git fetch origin codex/offline-outcome-initiation
git worktree add --detach ../orion-outcome-lab origin/codex/offline-outcome-initiation
cd ../orion-outcome-lab
source ~/.venvs/orion-lab/bin/activate
PYTHONPATH="$PWD/src" python -m orion.learning.event_outcome
```

For subsequent runs, enter that directory and run the last two commands. The
explicit source path selects this checkout despite any editable installation
pointing at the canonical repository. No API key or additional dependency is
required. This is a finite demonstration, not a background service.

The command records a synthetic prediction, enqueues a bound outcome event,
retrieves its synthetic receipt, resolves it through the real SQLite ledger,
and acknowledges the queue claim. Expected: worker completed=1, ledger resolved=1,
Brier approximately 0.04, evidence_updates=0, economic_value=null and
execution_allowed=false. Temporary databases are deleted on exit.

## Trust boundary and interfaces

`OutcomeReceipt(event, outcome)` is an immutable binding, not authentication.
`OutcomeEventHandler(ledger, lookup)` accepts the existing neutral event/trigger
and calls `lookup(tenant_id, event.idempotency_key)`. The trusted offline lookup
must verify source authenticity, evidence membership/availability, receipt
binding and tenant access before returning a receipt. No live lookup adapter is
provided. Event fields never supply the boolean outcome or authority.

The handler requires the outcome event class, its exact deterministic route,
an exact receipt event match and a matching outcome tenant. It delegates
prediction existence, temporal validity, conflicts and exact replay to
`PredictionLedger.resolve`, preserving one semantic owner. It creates no new
observations or evidence and returns zero evidence updates. The application
selects this specialized handler only for outcome work; other event classes
fail closed and follow the worker's bounded retry/dead-letter behavior.

## Recovery guarantees and limits

If ledger commit succeeds before queue acknowledgement fails, redelivery of the
same receipt resolves idempotently. A conflicting outcome is rejected and never
overwrites the first outcome. The queue and ledger are separate transactions;
this is idempotent measurement, not general exactly-once handler execution.
Keep receipts immutable and retain them through the retry window. Missing
receipts consume bounded retries and may dead-letter; provisioning a production
recovery/operator workflow is outside this laboratory increment.

The database, clock and injected lookup implementation are trusted. This module
does not sandbox Python callbacks or grant an external-access envelope. Wire
only a separately reviewed authenticated offline evidence store for real data.
The fixture does not prove restaurant discovery, forecasting skill, learning
improvement, calibration, economic value or live readiness. Human overrides are
not automatically outcome truth.

## Verification and release gates

Tests cover queue-to-ledger resolution, acknowledgement interruption and replay,
wrong tenants, modified envelopes, missing receipts, early/future outcomes,
unknown predictions, invalid event/route, conflicts and deterministic demo.
Run full pytest, Ruff, compilation, all offline demos and source/capability scans.
Independent review and maintainer merge remain required. A real restaurant trial
also needs a separately authorized data/export scope; none is introduced here.

## Cohort-safe measurement

This composition includes issue #122. For multiple targets or model versions,
call ledger.score(tenant, target_definition=target, model_version=model).
Both selectors are required together; ambiguous implicit pooling fails closed.
The demonstration contains one cohort and retains its existing command/output.
