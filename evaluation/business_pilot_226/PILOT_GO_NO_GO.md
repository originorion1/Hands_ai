# First read-only pilot decision

**Decision: NO_GO_LIVE_ACCESS.** Offline preparation and synthetic validation are complete for canonical source `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`; no customer access, production grant, or recommendation execution occurred. The deployed Command Center and synthetic report staging remain separate from this work.

## Why the gate remains closed

1. The exact customer tenant, company, report baseline, schema, credential reference, legal basis, and private evidence location have not been supplied or reviewed for a new session.
2. The independently installable connector/SDK/compatibility stack is still a sequence of **draft, unmerged** PRs. Its passing tests do not authorize a customer connector session.
3. Synthetic comparison yields zero incremental validated discoveries over a simple missing-value report. The held-out review candidate is a synthetic false positive. There is no independent customer adjudication, measured analyst effort, verified financial impact, or inventory record evaluation.
4. Procurement/inventory outcome-learning improvement is unmeasured. The canonical composed learning cycle targets sales; the prediction ledger alone does not prove this pilot hypothesis.

## Owner decisions required before a new qualification attempt

- Select the exact customer tenant and company through a private channel, approve the legal basis and proposed bounded field/date scope, and confirm which fields are expected exceptions.
- Supply the customer's actual existing ERP reports and reviewer workflow to freeze the comparator before seeing ORION findings.
- Approve or revise the retention/deletion policy, private credential reference, TLS destination, request budget, revocation and emergency-stop operator.
- Review exact connector package/SDK identities, permissions and deployment profile; obtain an independent security/readiness review and issue a fresh, one-session, purpose-bound read-only grant only after every gate passes.
- Name independent domain reviewers and a minimum adjudication sample. Define any future inventory-record amendment and procurement/inventory outcome target separately.

Any authorization must fail closed on scope mismatch, unexpected permission, stale or missing provenance, clock rollback, expiry, revocation, unsupported connector capability, or evidence custody uncertainty. Historical grants do not carry over. The pilot must not access customer ERP or issue production signing material under this offline branch.
