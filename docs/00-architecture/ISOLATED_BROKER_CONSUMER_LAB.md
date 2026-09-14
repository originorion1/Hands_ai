# Isolated semantic consumer and broker laboratory

Issue #147 composes the existing namespace profile, local broker, canonical
metadata admission, RoleStudy, SemanticStudy, semantic review and checkpoint.
It does not add a customer connector, grant issuer or production activation path.

Run from a checkout with the existing development dependencies installed:

```
python3 tools/isolated_broker_probe.py
```

The interpreter running the tool must have pytest installed because the trusted
owner reuses existing synthetic test fixtures. The confined consumer imports only
ORION's standard-library runtime; development dependencies are not mounted there.
There is no operator-provided endpoint, schema mapping, credential or code selector.

The owner first executes the real kernel-isolation prerequisite. An unavailable
prerequisite returns BLOCKED without starting the integrated experiment. For each
existing local row/column encoding it then:

1. Admits opaque synthetic metadata through the existing metadata launcher.
2. Starts and independently arms the existing broker with a bounded synthetic grant.
3. Starts the fixed ORION consumer inside the existing kernel namespace profile,
   adding only read-only ORION source and a fixed test helper to its mounts.
4. Relays bounded application frames to the ordinary broker API. It does not sign
   application controls or promote proposals. The consumer receives the scoped
   read bearer token, but no issuer key or source credential.
5. Checks denied tenant/write/unsigned-control requests before attempted reads,
   one authorized two-record read and denied replay with unchanged attempt count.
6. Feeds the actual broker observations into RoleStudy and SemanticStudy. Independent
   process evidence is deliberately absent, so business roles remain UNKNOWN.
7. Recomputes the consumer's proposed semantic checkpoint/review outside the consumer
   against the owner's actual admitted observations. Consumer claims are not trusted
   as evidence or authorization.
8. Revokes externally, stops and restarts the broker using its retained journal tip,
   then starts a fresh consumer with the same reference-only checkpoint/archive.
   The canonical review identity must match and further reads must be denied before
   another attempt. Restart must leave the broker unarmed and revocation effective.

The consumer also attempts access to synthetic broker source/config/secret files,
the journal, broker process environment and host loopback/Unix listeners. It invokes
the legacy injected-reader path against the synthetic source: a parsing error after
reading bytes is not accepted as denied access. Host negative controls establish
that those resources exist and are reachable before confinement. Source paths are
only test targets, not application adapter configuration. No real data is used.

Two unconfined smoke cases execute the complete broker/consumer/restart pipeline
but must FAIL containment. These tests are not an alternate launcher mode: the CLI
has no isolation-bypass option and always requires the kernel prerequisite. The
owner verifies broker traces, observations, canonical checkpoint state and the
unchanged audit canary rather than relying solely on consumer-reported success.

Pipe frames are limited to 64 KiB and eight exchanges with a ten-second frame
deadline. Child termination/invalid output fails the experiment. The outer tool
has a 60-second deadline. These are test-harness limits, not a deployed supervisor.

PASS means only this fixed local broker/consumer profile ran successfully. Existing
readiness gates are unchanged. Metadata is still admitted on the trusted owner side,
not through an isolated metadata broker. The owner, fixture collectors, interpreter,
repository, bubblewrap and kernel remain trusted. The owner holds archive/checkpoint
references in memory; the broker journal persists only through local test restarts.
Production custody, durable services, permitted HTTPS egress, asynchronous stop and
complete second-protocol discovery remain unproven. Both formats are local record
encodings, not two ERP implementations. No business-semantic validation is claimed
from this deliberately UNKNOWN case.

The current hosted execution environment cannot create the prerequisite local
sockets. Integrated OS proof therefore remains BLOCKED here. An earlier operator
run passed the standalone profile on WSL, but that does not verify this new
integration: it must be run independently on that host at the new exact commit.
