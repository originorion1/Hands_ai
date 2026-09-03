# ORION Agent Workflow

GitHub is ORION's canonical task queue and audit trail. Root `AGENTS.md` supplies durable repository instructions; each GitHub issue supplies the bounded implementation contract.

## State machine

```text
architect issue
  -> Codex feature branch
  -> Codex implementation and verification report
  -> ChatGPT review
  -> remediation if blocked
  -> human fast-forward merge
  -> ChatGPT remote verification
  -> close issue
  -> next ledger or task
```

ChatGPT owns architecture, orchestration, and adjudication. Codex implements the issue and reports deterministic evidence but does not merge or promote its work. A human retains merge authority.

## Automation lanes

### Offline/code lane

Repository inspection, bounded feature-branch changes, deterministic tests, local fixture demos, static analysis, and review preparation may be automated aggressively. They must remain inside the issue scope and repository guardrails.

### Live/customer lane

Customer data, credentials, live ERP systems, network access, writes, studies, and execution always require a separate, explicit human authorization ledger. An issue, implementation capability, successful offline test, or architectural approval does not grant that permission.

## Milestone boundary

This workflow is intentionally repository-native. Do not add a service, daemon, agent framework, queue, database, or GitHub Action for orchestration in this milestone. The durable instructions and GitHub templates are sufficient to make each handoff persistent, reproducible, and auditable.
