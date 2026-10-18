# ORION Live Watcher v0

The v0 watcher is an observation-only terminal stream over the existing
autonomous shadow-soak semantic boundaries. It is not a controller, authority
surface, evidence store, or second source of truth.

Run the offline synthetic demonstration:

```bash
python -m orion.observability.demo
```

The demonstration performs one local fake-reader cycle and prints sanitized
structural events. It performs no network operation and reports
`execution_allowed=false` and `writes=0` on every line.

## Event contract

`ActivityEvent` is immutable and contains only safe operational fields: event
type/time, ORION-generated run and cycle identity, structural study target,
bounds and aggregate counts, fixed reason codes, persistence verification, and
downstream-authority booleans fixed to false. It has no tenant, company, record,
payload, credential, URL, document, or provenance identifier field.

The shadow soak emits events in actual semantic order:

```text
run_started
objective_loaded
proposal_selected
authorization_checked
read_started
read_completed
validation_completed
evidence_sink_started
evidence_persisted
reassessment_started
next_proposal_selected
...
run_stopped
```

Denied authorization emits `authorization_denied` and never `read_started`.
Reader, validation, or persistence failure emits only a fixed `safe_failure`
category. Raw exception text is never placed in an event.

## Isolation

The sink is optional. With no sink, the runtime follows the pre-watcher path.
Event construction, clock, stream, and sink failures are contained at the
observability boundary and cannot add reads, persist evidence, alter a
checkpoint, grant authority, or perform a write. The terminal watcher exposes
only a callable event sink and a text stream; it has no control callbacks.

The current implementation intentionally adds no async runtime, service,
database, telemetry vendor, WebSocket server, or UI framework. A future Command
Center may consume the same neutral events through another adapter without
changing learning or authority semantics.
