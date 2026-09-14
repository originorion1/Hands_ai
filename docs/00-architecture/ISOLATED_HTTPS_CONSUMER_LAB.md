# Isolated consumer with local HTTPS records

Issue #153 composes the existing namespace profile, brokered metadata discovery,
local HTTPS broker fixture, canonical admission, semantic review and checkpoint.
No production source, authorization, transport selector, source scan or release
gate is changed. The HTTPS source and supervisor remain test-only components.

```
python3 tools/isolated_https_broker_probe.py
```

Use the project's development virtualenv, local OpenSSL and the existing Linux
namespace prerequisites. The fixed tool has no endpoint, credential, adapter,
command or unconfined override. It reuses the existing probe launcher with an
explicit fixed HTTPS profile. An unavailable kernel/socket prerequisite returns
BLOCKED before starting metadata brokers, HTTPS sources or consumers.

For each existing record encoding the owner:

1. Creates an opaque synthetic organization and separately scoped metadata/record
   grants. The consumer starts without schema and without issuer/source secrets.
2. Starts the existing local TLS fixture with ephemeral certificate/key and an
   authenticated loopback profile. Deletes the broker's source cache. A separate
   protected copy of the synthetic source is a custody canary, never an input to
   record acquisition. This prevents a missing-file check from masquerading as
   source isolation.
3. Proves owner-side access to the actual HTTPS listener, profile, key and canary.
   These negative controls must succeed even though consumer access must fail.
4. Runs the fixed consumer inside the unchanged namespace profile. Only read-only
   ORION source and the fixed inspection helper are mounted. The TLS worker/server
   implementation and its key/profile are not mounted there.
5. Relays the consumer's canonical metadata request to the metadata broker, then
   its independently granted record request to the HTTPS broker. Metadata-token
   record escalation, tenant substitution, writes and unsigned controls reject.
6. Requires exactly one authenticated HTTPS GET, two canonical record observations,
   three metadata attempts and one record attempt. The source cache must remain
   absent. Actual received bytes still pass through the existing sealed worker.
7. Recomputes the consumer's semantic review/checkpoint from the owner's admitted
   evidence. Independent business-role evidence is absent, so UNKNOWN is retained.
   A forged consumer review is rejected rather than treated as an attestation.
8. Revokes and restarts both brokers, restores a fresh consumer from references,
   and requires the same canonical review identity. Both renewed metadata and
   record reads reject; budgets and the server's one-GET count remain unchanged.

The consumer additionally probes the real TLS listener with a direct TCP attempt,
and tries to read its signed route profile and private key. It retains the prior
source/config/journal/secret, parent-environment, legacy-adapter and namespace
checks. A bare TCP negative-control connection never sends a credential and does
not count as an HTTP record read. The server retains only bounded request outcome
booleans, not authorization headers.

Two unconfined cases run the real HTTPS/semantic/restart composition but must FAIL
containment, while their pipeline checks pass. They explicitly demonstrate that
profile, key, source and listener remain accessible without the namespace profile.
These are deterministic regression tests, not an operator activation option.

PASS means only this fixed composed local profile ran on the tested host. Broker
and source remain owner-side trusted processes; no mandatory broker egress sandbox
or production credential service is established. Metadata uses the existing local
schema protocol, not HTTPS metadata. The two HTTPS record encodings are not two
ERP implementations. OS containment, TLS and semantic proofs are reported at their
actual scopes; this does not validate commercial roles or universal discovery.

All live gates remain closed. `execution_allowed=false`,
`allow_live_customer_access=false`, `live_ready=false`, and
`production_containment=NOT PROVEN` are preserved. No customer data/credentials,
real customer endpoint, write authority, merge or activation is used.
