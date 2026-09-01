# ORION AI Orchestration Protocol

## Purpose

Define the standard workflow for using multiple AI systems on ORION while preserving one coordinated project direction and independent verification.

## Canonical workflow

```text
Human goal / approved issue
        |
        v
ChatGPT — ORCHESTRATOR
        |
        +--> Codex — BUILD / INVESTIGATE
        |        |
        |        v
        |   Tests / CI / Security
        |
        +--> Claude — ADVERSARIAL REVIEW
        |        |
        |        v
        |   Findings / negative cases
        |
        +--> Specialist models — TARGETED CHALLENGE
        |
        v
ChatGPT — SYNTHESIS / ROUTING
        |
        v
Codex — REMEDIATION
        |
        v
Deterministic verification
        |
        v
Human review / approval
        |
        v
Merge to protected main
```

## 1. Orchestrator rules

ChatGPT is the only project-level AI orchestrator.

The orchestrator must:

- maintain the current objective and scope;
- assign bounded tasks to the correct role;
- keep agents from duplicating authority;
- require evidence for material claims;
- reconcile disagreements explicitly;
- distinguish implementation facts from plans and assumptions;
- require deterministic verification before merge;
- escalate safety-critical or ambiguous decisions to the human owner.

The orchestrator does not override repository protections or human approval requirements.

## 2. Builder cycle

For an implementation task:

1. Inspect repository and relevant contracts.
2. Define the smallest bounded change.
3. Codex implements on an isolated branch.
4. Run focused tests.
5. Run the broader deterministic gates required by the task.
6. Produce a concise change summary and evidence.
7. Send the result for adversarial review when the change affects architecture, safety, tenant isolation, provenance, learning, authorization, ERP integration, or other high-risk boundaries.

## 3. Adversarial cycle

Claude or another independent reviewer should not merely restate the implementation.

The reviewer should attempt to falsify it by asking:

- What happens with malformed input?
- What happens with stale or contradictory evidence?
- Can tenant A influence tenant B?
- Can untrusted model output become authority?
- Can retries duplicate a side effect?
- Can replay produce a different decision?
- Can provenance be lost or forged?
- Can confidence increase without new evidence?
- Can an agent bypass the intended permission boundary?
- Does the documentation match the repository?
- What happens when an external API changes?
- What is the smallest failure that invalidates the claimed guarantee?

Findings must be classified as confirmed, suspected, disproven, or requiring an experiment.

## 4. Specialist model use

A second or third model is justified only when the question benefits from independent reasoning, such as:

- disputed architecture choices;
- difficult debugging;
- security threat modeling;
- research synthesis;
- evaluation-set design;
- model/provider comparison.

Parallel model use is not a goal by itself. Every additional model adds review and coordination cost.

## 5. Deterministic verification

AI claims are never sufficient evidence for release.

Where applicable, verification should include:

- unit tests;
- integration tests;
- property/invariant tests;
- type checking;
- linting;
- architecture/import-boundary checks;
- secret scanning;
- dependency/security scanning;
- container scanning;
- replay/evaluation tests;
- ERP adapter contract tests.

A failed deterministic gate blocks completion until dispositioned.

## 6. Safety-critical changes

For changes affecting authorization, execution, tenant isolation, provenance, credentials, policy, capability promotion, or customer-system writes:

- require an independent adversarial review;
- add explicit negative tests;
- verify fail-closed behavior;
- verify audit/provenance continuity;
- verify idempotency where side effects exist;
- require human approval before merge or authority expansion.

## 7. Decision records

Important tool or architecture decisions should record:

- problem;
- proposed solution;
- alternatives;
- evidence;
- risks;
- reversibility/migration path;
- decision owner;
- date/status.

Never encode a temporary AI recommendation as a permanent architecture decision without an explicit decision record.

## 8. Handoff format

AI-to-AI handoffs should be concise and evidence-based:

```text
TASK:
OBJECTIVE:
CURRENT REPOSITORY STATE:
CHANGES MADE:
FILES AFFECTED:
TESTS RUN:
TEST RESULTS:
SECURITY/SAFETY IMPACT:
KNOWN RISKS:
OPEN QUESTIONS:
REVIEW REQUEST:
```

## 9. Completion definition

A task is not complete because an AI says it is complete.

For implementation work, completion means:

- the requested behavior exists in the actual repository;
- tests exercise the relevant behavior;
- deterministic gates pass;
- adversarial findings are resolved or explicitly accepted;
- documentation accurately describes the current state;
- required human approval is obtained;
- the change is merged through the governed repository workflow.

## 10. Model replacement

Any AI participant may be replaced. The workflow, contracts, evidence requirements, tests, and governance must remain valid if Codex, Claude, ChatGPT, or any specialist model is replaced by another system.
