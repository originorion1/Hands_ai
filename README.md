# ORION Core

ORION is a small, vendor-neutral laboratory kernel for governed enterprise
system learning and shadow-only decision support. The v0.1 vertical slice
observes authorized system data through replaceable adapters, retains evidence
provenance and tenant scope, projects observed facts into a system graph, and
can propose a non-executing human-review action from validated customer
knowledge.

The project deliberately has no production ERP credentials, write adapters, or
customer data. ERPNext is represented only by a read-only adapter; future
systems can implement the same discovery port.

## AI entry gate — READ FIRST

ORION is developed and reviewed by multiple AI systems with deliberately
separated responsibilities. **ChatGPT is the project-level AI orchestrator.**
Codex is the primary builder; Claude is the primary adversarial reviewer;
additional models are bounded specialists; tests, CI, and security tooling are
the deterministic verification layer; authorized humans retain final authority.

Before modifying, reviewing, or proposing changes, every AI agent must read:

1. `docs/ORION_CONSTITUTION.md`
2. `docs/ORION_AI_AGENT_CONTRACT.md`
3. `docs/ORION_AI_ORCHESTRATION_PROTOCOL.md`
4. the relevant architecture/security/task documents
5. the actual repository state — never assume documentation means code exists

### Non-negotiable rules

- Reasoning is not authorization.
- Capability is not permission.
- Proposal is not approval.
- Approval is not execution.
- Execution attempt is not successful execution.
- Tenant boundaries and provenance are mandatory.
- Safety-critical behavior fails closed.
- AI output is never trusted merely because a model produced it.
- No agent may self-certify safety-critical work.
- No agent may silently weaken governance, safety, tenant, provenance, or authorization boundaries.

### Standard AI workflow

```text
Human goal
   ↓
ChatGPT — orchestrate
   ↓
Codex — investigate / build
   ↓
Tests + CI + security checks
   ↓
Claude — adversarial review
   ↓
Codex — remediate
   ↓
Deterministic verification
   ↓
Human approval
   ↓
Protected main
```

The durable ORION dependency is the contracts and governance model, not any
particular AI vendor. AI systems may be replaced without changing ORION's
stable domain contracts unnecessarily.

## Local demonstration

With Python 3.12 and the development dependencies installed, run:

```powershell
python -m pytest -q
python -m orion.demo
```

The demo uses in-memory ERPNext-shaped fixture data and always reports
`"execution_allowed": false`.
