# ORION v0.1 AI Orchestration Contract

**Status:** Accepted for Laboratory experimentation
**Phase:** Laboratory — ORION v0.1
**Objective:** Use multiple AI systems strategically while keeping ORION provider-independent and governed.

## 1. Principle

AI platforms are interchangeable engineering and reasoning resources. ORION SHALL NOT hard-code its architecture around a single provider or model.

## 2. Roles

- **Codex / coding agents:** primary implementation, refactoring, tests, repository engineering.
- **Claude / independent reasoning reviewer:** adversarial architecture, security, failure-mode, and implementation review.
- **ORION reasoning layer:** orchestration, decomposition, epistemic reasoning, decision synthesis, and workflow coordination.
- **Specialist models:** task-specific extraction, classification, research, or other capabilities when evaluation demonstrates an advantage.

These roles are defaults, not permanent provider dependencies.

## 3. Model Registry

The future Model Registry SHALL record provider, model/version, capabilities, quality measurements, latency, cost, failure characteristics, security classification, permitted task classes, and evaluation history.

## 4. Agent Registry

The Agent Registry SHALL record agent identifier/version, purpose, declared capabilities, knowledge scopes, model policy, authority requirements, evaluation history, and lifecycle status.

## 5. Routing

A future Model Router SHALL select models according to task requirements, policy, measured capability, cost/latency constraints, and risk. Domain code SHALL NOT call provider SDKs directly.

## 6. Adversarial review

Material architecture or high-risk implementation changes SHOULD receive an independent review path. Agreement between models is not proof of correctness; deterministic tests, policy checks, evidence, and system-state verification remain authoritative where applicable.

## 7. Evaluation loop

```text
Task
 -> Decompose
 -> Select agent/model
 -> Execute
 -> Verify
 -> Evaluate outcome
 -> Update model/agent profile
```

## 8. Training/improvement principle

ORION SHALL prefer improved tools, context, retrieval, workflows, evaluation, and routing before fine-tuning. Fine-tuning is an optimization option, not a prerequisite for agent learning.

## 9. Laboratory use

The Laboratory may compare models, prompts, agent designs, retrieval strategies, and orchestration approaches. Successful experiments require measurable evaluation before promotion to Shadow.

## 10. Security

No external model or coding agent receives unrestricted production credentials. Secrets remain outside prompts, logs, ledgers, source control, and model context unless explicitly required by a controlled secret-handling mechanism.

## 11. Future ORION workforce

The architecture SHALL support multiple specialized agents and models without requiring kernel redesign. Agents interact through contracts, events, knowledge services, capabilities, and governed Hands.

## 12. Non-goals for v0.1

Do not build a complex multi-model distributed platform merely for completeness. Implement only the minimum registry/router interfaces needed to preserve provider independence while achieving the three-day prototype objective.
