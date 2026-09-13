# ORION Event-Driven Learning v1

This increment establishes the smallest durable event-driven core that can wake
ORION continuously without treating an event as truth or authority. It is a
single-process architecture seam, not a distributed deployment design.

## Boundaries

An `EnterpriseEvent` is immutable structural input with exact tenant, source,
provenance, event time, receipt time, subject, and correlation identity. It has
no customer payload, credentials, authorization envelope, recommendation,
promotion, or execution capability. Its source-scoped identity is hashed into a
stable versioned idempotency key so delimiter choices cannot cause collisions.

`route_enterprise_event` maps the fixed event class to one `LearningTrigger`:

| Event class | Trigger | Priority | External authority |
| --- | --- | ---: | --- |
| authority | policy reassessment | 100 | none |
| metadata | structural reassessment | 95 | none |
| configuration | structural reassessment | 90 | none |
| prediction outcome | offline learning | 85 | none |
| drift | governed-study candidate | 80 | required, not granted |
| anomaly | governed-study candidate | 75 | required, not granted |
| certified knowledge | offline learning | 70 | none |
| other neutral event | offline learning | 60 | none |

The queue rejects any adapter-supplied route or priority that differs from this
deterministic classification. A governed-study candidate is only a proposal for
the existing authorization boundary. It cannot invoke a capability by itself.

## Durable intake and backpressure

`SQLiteEventQueue` is a replaceable edge adapter with transactional enqueue,
stable deduplication, content-integrity hashes, deterministic priority order,
per-tenant and global active-work capacities, time-bounded leases, stale-lease
recovery, acknowledgement, retry release, and dead-letter state. Completed
identity keys remain durable so replay does not learn twice.

Capacity rejection is explicit (`tenant_backpressure` or
`global_backpressure`). An upstream webhook or bounded-polling adapter can
retry later; the core does not silently discard or deepen work during an event
storm. SQLite is suitable for this first local process. A later transport must
preserve these contracts rather than leak broker semantics into the core.

## Bounded worker

`run_event_worker` accepts only the neutral event and trigger, then stops at a
fixed claim budget. Each event has a fixed attempt budget, lease, retry delay,
and dead-letter outcome. A handler may update authenticated offline learning
state, ignore a signal, or return a governed-study proposal. Its result cannot
grant authorization or execution.

Handlers must be idempotent at the event identity boundary. If a process fails
after a handler commits its own durable state but before queue acknowledgement,
the lease will be replayed. The handler's state store must therefore record the
same idempotency key transactionally with its effect.

Tenant-scoped workers claim only that tenant's rows. The event database is a
queue, not an evidence or knowledge store; downstream authenticated evidence
and checkpoints remain the reconstructable learning source of truth.

## Offline demonstration

Run:

```bash
python -m orion.events.demo
```

The demo enqueues a synthetic record signal and drift signal, processes one as
offline learning and one as an authorization-required study candidate, and
reports zero external reads, zero external writes, and
`execution_allowed=false`. It opens no network connection and contains no live
adapter.

## Deferred edges

This increment deliberately does not add webhooks, CDC, vendor event schemas,
pollers, Kafka, Kubernetes, microservices, schedulers, live ERP access, or
customer writes. Those belong at governed adapters after measured throughput,
reliability, retention, and tenancy requirements justify them.
