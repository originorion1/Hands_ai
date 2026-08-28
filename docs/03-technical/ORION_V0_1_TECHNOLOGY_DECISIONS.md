# ORION v0.1 Technology Decisions

**Status:** Accepted for Laboratory implementation
**Current horizon:** 2026 prototype
**Architectural horizon:** 2036

## Decision summary

| Area | v0.1 choice | Boundary / replacement rule |
|---|---|---|
| Primary language | Python 3.14 | Domain contracts remain language-oriented, not framework-oriented. Python 3.13 remains a fallback if a dependency requires it. |
| API/runtime | FastAPI + Pydantic 2 | HTTP is an adapter; domain/core does not depend on FastAPI. |
| Database | PostgreSQL 18 | Access through repository/unit-of-work boundaries; schema changes via migrations. |
| Vector retrieval | PostgreSQL extension capability, introduced only when required | Vector search is a service capability, not the knowledge model itself. |
| ORM / SQL | SQLAlchemy 2 + Alembic | Persistence implementation remains replaceable behind repository contracts. |
| Async execution | Python asyncio initially | Queue/workflow infrastructure is deferred until measured workload requires it. |
| Event model | Typed internal domain events + durable event records in PostgreSQL | External broker remains an adapter option; no broker dependency in core. |
| HTTP client | httpx | External calls remain in adapters. |
| Validation/settings | Pydantic 2 + pydantic-settings | Configuration is externalized; secrets never live in source. |
| Testing | pytest + contract/integration/e2e layers | Tests enforce architecture and behavior, not implementation details. |
| Packaging | `pyproject.toml` | One installable ORION package; avoid premature microservices. |
| Dependency management | uv | Lock dependencies for reproducible environments. |
| Containers | Docker / Compose for local and Shadow packaging | Kubernetes is not required for v0.1. Preserve deployment interfaces for future orchestration. |
| Observability | structured logging + OpenTelemetry-compatible instrumentation | Telemetry must not contain secrets or customer-sensitive payloads by default. |
| AI providers | provider adapters behind ModelPort | No provider SDK in core/services. |
| ERPNext | dedicated adapter + Hand implementation | No ERPNext concepts in generic core/domain contracts. |

## 1. Python

Python 3.14 is the primary runtime because it is a current stable release in 2026 and fits ORION's AI, integration, automation, and rapid-prototyping requirements. Python 3.14.7 is currently released; Python 3.13 remains an operational fallback where third-party compatibility requires it. The project must pin a supported minor/runtime version rather than tracking an unbounded interpreter range.

## 2. API and application runtime

FastAPI is the initial HTTP boundary. Its current documentation explicitly recommends pinning the version used by an application and testing upgrades; ORION will therefore pin compatible FastAPI/Pydantic versions rather than accepting unconstrained upgrades. citeturn0search1

FastAPI SHALL NOT appear in `core/`. API routes translate external requests into application commands/queries and return contract-defined responses.

## 3. PostgreSQL

PostgreSQL 18 is the v0.1 database baseline. PostgreSQL 18.6 is the current supported minor release as of this decision, and the project supports major versions for five years. citeturn0search7turn0search10

PostgreSQL is deliberately selected as the initial convergence point for transactional state, durable events, evidence metadata, knowledge metadata, and initial retrieval needs. PostgreSQL supports structured and unstructured data types and can be extended for vector search, allowing ORION to avoid premature multi-database architecture. citeturn0search11

PostgreSQL 19 is not the v0.1 baseline because the current release information still identifies PostgreSQL 18 as current stable while 19 is in the release transition. citeturn0search2turn0search6

## 4. ORM and migrations

SQLAlchemy 2 is the persistence implementation and Alembic is the migration mechanism. Repositories and unit-of-work interfaces remain the stable application boundary. Raw SQL is permitted where justified by performance or database-specific functionality, but must remain inside infrastructure/persistence code.

## 5. Event architecture

v0.1 starts with typed in-process domain events and durable event records. We do not introduce Kafka, NATS, Temporal, or another distributed workflow platform before the workload demonstrates the need.

The contract must nevertheless preserve event identity, correlation ID, causation ID, tenant scope, schema version, timestamp, actor/agent identity, and idempotency semantics so a future broker/workflow engine can be introduced without rewriting domain behavior.

## 6. AI model architecture

ORION uses a ModelPort/adapter design. Provider-specific clients are isolated under `adapters/models/`. Model selection belongs to the future Model Router and must consider task capability, cost, latency, policy, risk, and measured performance.

OpenAI, Anthropic/Claude, local models, and other providers are therefore integrations, not ORION architectural dependencies. Current OpenAI documentation exposes multiple models and the Responses API; ORION should use provider adapters rather than embedding a specific provider API in domain code. citeturn0search8

## 7. Agent architecture

Agents are application-level components using contracts, services, tools/capabilities, knowledge scopes, and model policies. Agents do not directly access arbitrary database tables, credentials, or external systems.

## 8. ERPNext integration

ERPNext is the first customer-system adapter. The adapter translates between ORION's canonical representations and ERPNext-specific objects/API behavior. Execution occurs through governed Hands. Read/discovery capabilities precede write capabilities.

## 9. Security and secrets

Secrets are supplied through the deployment secret mechanism and are never committed to Git, embedded in prompts, or written to the Development Ledger. Production credentials are never handed directly to a reasoning model when a narrowly scoped capability can perform the required operation.

## 10. Testing

Every core contract receives unit tests. Adapter contracts receive contract tests. PostgreSQL-backed behavior receives integration tests. End-to-end tests exercise the first real workflow in a controlled environment. Architecture tests must prevent prohibited dependency direction.

## 11. Deployment

Docker is the initial packaging boundary. Docker Compose is sufficient for local Laboratory environments and early Shadow environments. Kubernetes/distributed orchestration is explicitly deferred until measured requirements justify it.

## 12. Observability

Structured logs, metrics, traces, correlation IDs, event IDs, agent/model versions, and action IDs are first-class. Sensitive payloads are excluded or redacted by default. Every execution path should be reconstructable from metadata and evidence without exposing customer secrets.

## 13. 2036 compatibility

These choices are intentionally conservative convergence choices. The architecture preserves replacement points for:

- database/storage topology
- event broker/workflow engine
- model providers
- agent runtime
- ERP/system adapters
- deployment orchestration
- retrieval engines
- observability backend

The 2036 objective is not to pre-install every future technology. It is to avoid making today's implementation inseparable from any one technology.

## 14. Explicit non-decisions

The following are deliberately not selected for v0.1 without evidence:

- Kubernetes
- Kafka/NATS
- Temporal
- dedicated graph database
- dedicated vector database
- microservice decomposition
- model fine-tuning infrastructure
- distributed agent marketplace
- autonomous code modification in production

These remain future options behind appropriate contracts.
