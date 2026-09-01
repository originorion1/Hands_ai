# ORION v0.1 — AI Engineering & Tooling Strategy

## Status

Approved engineering workflow baseline for ORION v0.1.

## Purpose

ORION is developed as a multi-agent engineering project, but no AI tool is authoritative over architecture, safety, implementation, or release decisions. The repository, tests, CI results, and explicit human approvals remain the source of truth.

## Core Principle

**Use different agents for different cognitive jobs. Do not ask one agent to build, approve, and certify its own work.**

```text
                         HUMAN OWNER
                             │
                       final decisions
                             │
              ┌──────────────┼──────────────┐
              │              │              │
            CODEX          CLAUDE         OTHER MODEL
            BUILD          ATTACK          CHALLENGE
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                       GITHUB / PR / CI
                             │
                   tests + security gates
                             │
                             ▼
                         ORION CODE
```

## 1. Tool Roles

### Codex — primary engineering agent

Use Codex as the default implementation and repository-engineering agent.

Responsibilities:

- implement bounded issues;
- inspect and modify the repository;
- write and run tests;
- perform refactors and migrations;
- prepare commits/PRs;
- investigate CI failures;
- work in isolated branches/worktrees where possible.

Codex must not self-certify safety-critical changes.

### Claude Web — adversarial architecture and safety reviewer

Use Claude Web as an independent reviewer, especially before merging major boundaries.

Responsibilities:

- attack architecture assumptions;
- search for contradictions between docs and code;
- challenge tenant isolation;
- challenge provenance and learning claims;
- challenge authorization/promotion logic;
- identify hidden coupling and premature complexity;
- attempt to construct failure cases the builder missed.

Claude's output is evidence for investigation, not authority.

### Claude Code — optional deep terminal agent

If access becomes available, use Claude Code for repository-scale investigation, bounded implementation, and adversarial experiments. Its subagents, hooks, skills, and MCP integrations make it particularly useful for repeatable engineering workflows. It remains a peer to Codex, not a replacement for the review process.

### Gemini / other frontier models — independent challenger

Use a second independent model selectively for disputed architecture questions, difficult debugging, research synthesis, and alternative designs. The purpose is disagreement detection, not model accumulation.

### OpenHands / OpenCode / Cline / Aider — optional open/model-agnostic agents

These are useful as experimental or fallback engineering harnesses. They should not become mandatory ORION infrastructure unless a measured workflow advantage justifies them.

## 2. Non-AI Engineering Controls

AI agents are only one layer. ORION should also use deterministic tooling.

### Source control

- GitHub is the source of truth.
- `main` is protected.
- Significant changes go through PRs.
- Laboratory work happens on isolated branches.
- Every architectural change has an ADR or documented decision.

### CI / quality

Minimum gates:

- unit and property tests;
- integration tests;
- lint/type checks;
- dependency/security scanning;
- secret scanning;
- architecture/import-boundary checks;
- coverage for critical safety invariants.

### Security tooling

Evaluate and adopt where justified:

- GitHub CodeQL for semantic code security;
- Semgrep for fast policy/security rules;
- Gitleaks or equivalent for secret detection;
- Dependabot/Renovate for dependency updates;
- Trivy or equivalent for container/dependency scanning.

No scanner result is treated as proof of safety; high-risk paths require adversarial review and tests.

## 3. AI-Specific Guardrails

### Repository instructions

Maintain machine-readable project instructions for agents, including:

- architecture boundaries;
- prohibited anti-patterns;
- test commands;
- security rules;
- tenant-isolation rules;
- branch/PR workflow;
- definition of done.

Prefer versioned repository instructions over repeating critical rules in chat.

### Skills and subagents

Create narrowly scoped agent skills for recurring tasks such as:

- architecture review;
- ERPNext adapter review;
- tenant-isolation review;
- provenance review;
- test generation;
- security review;
- migration review.

A skill must describe its authority limits and required evidence.

### Hooks / automated checks

Where supported, use hooks or CI to prevent obvious unsafe actions, for example:

- writing secrets;
- modifying protected architecture contracts without review;
- changing authorization code without required tests;
- bypassing tests;
- writing directly to production credentials/configuration.

## 4. ORION Engineering Loop

Every meaningful feature follows this loop:

```text
1. Human/architecture issue
        ↓
2. Codex investigation + implementation plan
        ↓
3. Codex implementation on isolated branch
        ↓
4. Automated tests + static/security checks
        ↓
5. Claude adversarial review
        ↓
6. Fix findings
        ↓
7. Re-run deterministic gates
        ↓
8. PR review / human approval
        ↓
9. Merge
        ↓
10. Record decision, evidence and result
```

For safety-critical changes, add an independent second-model review and explicit negative tests.

## 5. Research and Evaluation Systems

Use external research tools for technology selection, but never introduce infrastructure because an AI recommends it.

For every major tool/system proposal record:

- problem being solved;
- alternatives considered;
- measurable benefit;
- operational cost;
- lock-in risk;
- security/privacy implications;
- migration/replacement path;
- evidence from ORION workload.

Maintain a small internal benchmark suite for ORION-specific work rather than relying only on generic coding benchmarks.

## 6. Learning-System Evaluation

ORION's own learning claims require a separate evaluation discipline.

Never equate:

```text
more observations → understanding
validated hypothesis → knowledge
knowledge → capability
confidence → authorization
```

Each transition must have explicit criteria, provenance, tests, and failure handling.

Build evaluation datasets that include:

- normal cases;
- ambiguous cases;
- contradictory evidence;
- stale evidence;
- tenant-confusion attempts;
- malformed ERP responses;
- permission failures;
- duplicate events;
- replay scenarios;
- adversarial prompts/tool outputs.

## 7. ERPNext Tooling Strategy

Keep ERPNext-specific knowledge at the adapter boundary.

Use:

- official ERPNext/Frappe documentation and source as primary references;
- a disposable/staging ERPNext environment;
- API contract tests;
- schema/DocType discovery tests;
- pagination/retry/error tests;
- permission-boundary tests;
- fixture-based replay tests.

Do not allow an LLM to infer that an ERPNext operation is safe merely because an API call succeeded.

## 8. Observability and Agent Traceability

Every AI-assisted engineering operation that changes ORION should be reconstructable from:

- issue/task;
- branch/commit;
- agent/tool used;
- relevant instructions/skill version;
- tests executed;
- review findings;
- final human decision.

For runtime ORION, use structured telemetry and correlation IDs. Evaluate OpenTelemetry-compatible tracing and an LLM/agent observability system only when runtime complexity justifies it.

## 9. Optional Advanced Systems to Evaluate Later

These are candidates, not commitments:

- MCP for controlled tool/resource access;
- OpenHands/OpenCode for model-agnostic agent experiments;
- browser automation for ERP UI discovery when APIs are insufficient;
- local models through Ollama/vLLM for privacy-sensitive auxiliary tasks;
- OpenTelemetry for distributed runtime tracing;
- Langfuse or an equivalent evaluation/LLM-observability system when model-call volume justifies it;
- dedicated evaluation harnesses for regression and learning-quality measurement.

Do not introduce a system until a concrete ORION requirement and measurable benefit exist.

## 10. Tool Selection Matrix

| Function | Default | Independent challenger | Deterministic gate |
|---|---|---|---|
| Architecture | Codex | Claude Web | ADR + tests |
| Implementation | Codex | Claude | CI |
| Adversarial audit | Claude Web | second model when needed | security/negative tests |
| Repository investigation | Codex | Claude | reproducible commands |
| ERPNext research | ChatGPT/web + official docs | Claude/Gemini | adapter contract tests |
| Security | security tools + Codex | Claude | CodeQL/Semgrep/secret scan |
| QA | Codex | Claude | pytest + integration/property tests |
| Technology research | ChatGPT/web | Claude/Gemini | ADR with evidence |
| Runtime observability | OpenTelemetry candidate | independent review | telemetry tests |
| Agent/tool integration | MCP candidate | security review | allowlist/policy gates |

## 11. What We Should NOT Add Yet

Do not add an AI swarm merely because multi-agent systems are fashionable.

Do not add Kubernetes, Kafka/NATS, Temporal, vector databases, graph databases, microservices, fine-tuning infrastructure, autonomous production code modification, or a distributed agent marketplace without measured requirements.

Do not add multiple coding agents to every task. Parallelism is useful only when tasks are genuinely separable and merge/review cost is lower than the time saved.

## 12. 2036 Compatibility

The durable layer is the workflow and contracts, not any particular AI vendor.

ORION must be able to replace:

- the coding agent;
- the model provider;
- the evaluation model;
- the observability platform;
- the ERP adapter;
- the execution runtime;

without changing the stable domain contracts unnecessarily.

The repository therefore records **roles and interfaces**, not assumptions that one vendor will remain dominant through 2036.

## 13. Decision

For v0.1, the approved default is:

**Codex = builder/engineering lead**

**Claude Web = adversarial architect/reviewer**

**ChatGPT = architecture coordination + research**

**GitHub + CI + security tooling = objective control layer**

**Additional models/tools = targeted challengers, not permanent dependencies**

The objective is not to use the most AI tools. The objective is to create the highest-confidence engineering loop with the fewest unnecessary moving parts.
