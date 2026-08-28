# ORION v0.1 Discovery Interface

**Status:** Laboratory implementation contract
**Phase:** Step 4.0 — first live-system discovery
**Architectural rule:** ORION learns and understands systems; adapters provide observation/action surfaces only.

## Objective

Define the secure, system-independent boundary through which ORION can investigate a real business system without making the system itself ORION's domain model.

## Discovery surfaces

A Discovery Adapter may expose any legitimately available surface:

- metadata/schema
- documentation
- configuration
- source code
- API/resource descriptions
- UI structure
- workflows
- permissions
- events/logs/telemetry
- runtime outcomes
- other approved observation sources

Availability of an API is optional. No discovery contract may require an API to exist.

## Core pipeline

```text
External System
    -> Discovery Adapter
    -> Observation
    -> Evidence + Provenance
    -> Canonical System Understanding
    -> Knowledge / Pattern Learning
```

## Security boundary

Discovery is read/observe/analyze by default. Discovery credentials must be scoped to the minimum required permissions. Secrets are injected through runtime configuration and are never stored in source code, prompts, knowledge, observations, or the development ledger.

Discovery has no implicit execution authority. Any action capability must cross the normal Capability -> Policy -> Hand boundary.

## Adapter contract concepts

Every adapter should provide, where supported:

- system identity
- available discovery capabilities
- entity/schema discovery
- relationship discovery
- workflow discovery
- rule/permission discovery
- configuration discovery
- documentation/source references
- runtime observation hooks
- evidence references

Adapters return canonical ORION discovery objects rather than ERP/vendor-specific objects to the core.

## Investigation lifecycle

```text
Establish scope
  -> inspect capabilities
  -> collect structural observations
  -> collect governance observations
  -> collect behavioral observations
  -> correlate evidence
  -> identify unknowns/contradictions
  -> update System Understanding Model
  -> identify candidate patterns
```

## First-system rule

ERPNext is the first practical system adapter only. ERPNext-specific concepts, endpoints, DocTypes, authentication details, and implementation behavior remain inside the adapter boundary.

The ORION core must remain usable if the next target is a different ERP, CRM, warehouse system, custom application, legacy application, or a system with no conventional API.

## Acceptance criteria

The discovery interface is acceptable when:

1. an adapter can describe a system without exposing vendor-specific types to core code;
2. observations retain source, timestamp, scope, and provenance;
3. discovery can operate without write permission;
4. missing surfaces are represented as unavailable/unknown rather than fabricated;
5. contradictory evidence can coexist until resolved;
6. discovery cannot directly execute business actions;
7. customer-specific observations remain tenant-scoped;
8. the resulting model can be consumed by pattern learning without coupling to the source system.
