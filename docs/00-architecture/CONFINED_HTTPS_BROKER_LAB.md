# Confined synthetic HTTPS broker laboratory

Issue #157 composes the existing supervised broker, signed local TLS route,
canonical admission, durable attempt journal and bubblewrap profile. It adds no
production networking and changes no authorization, admission or release gate.

Run on the synthetic WSL/Linux laboratory host:

```sh
python3 tools/confined_https_broker_probe.py
```

The fixed launcher accepts no endpoint, credential, proxy, command, mount or
activation argument. Missing bubblewrap, namespace, socket, Python or OpenSSL
prerequisites return `BLOCKED`; there is no unconfined fallback.

## Boundary exercised

The trusted owner signs one route for numeric `127.0.0.1:44443`, GET
`/fixture`, a pinned ephemeral certificate and the existing broker configuration.
The synthetic TLS source and broker run together inside one newly created network
namespace. The command retains `--unshare-all`, never uses `--share-net`, clears
the environment, drops all capabilities, closes inherited descriptors, mounts
code/configuration/source/TLS material read-only and exposes only dedicated
journal state as writable. The source is not an unconfined networking helper.

The namespace contains only loopback. Runtime witnesses reject the owner network,
an unapproved loopback address, an unapproved port and the RFC 5737 TEST-NET
address. Existing route policy separately rejects caller-selected hosts, ports,
methods, targets and bodies. Proxy fields and environment routes cannot select a
transport. A redirect response consumes one reserved attempt and is rejected
without a second request or observation.

A separate reasoning child runs in its own unchanged namespace profile. The
owner relays its ordinary application request to the broker over bounded pipes.
The reasoning child receives no source credential, broker authentication key,
TLS private key, signed profile, source file, broker configuration, control MAC
or network access to the source. Owner-side negative controls prove these
synthetic assets and unrelated listeners exist before their denial is counted.

Authorized bytes pass the existing permit, durable pre-I/O reservation, TLS
policy, sealed normalization worker and canonical admission. Both existing local
record encodings retain tenant, authorization provenance, immutable observations
and `execution_allowed=false`. Unauthorized shapes, scopes and tokens are denied
with zero source requests and zero attempts. Replay is denied. Separate cases
durably revoke and stop the broker; a fresh broker namespace opened at the exact
journal head cannot rearm or issue another HTTPS request, and attempt, failure and
reserved-byte accounting remains unchanged.

## Evidence limits

This is one fixed synthetic loopback destination, not a production egress
implementation or a general destination firewall. The owner/issuer, fixture
composition, broker, source server, profile signer and journal key remain trusted.
The broker necessarily has its synthetic source credential and the TLS server
necessarily has its private key. Same-namespace hostile broker/source code is not
certified, and the broker still writes its own journal. Two local record formats
are not two ERP protocols. No customer endpoint, credential, DNS, CA lifecycle,
protected secret service, protected external audit, deployment supervisor or
retention control is supplied.

A passing test count is not host isolation proof; the operator probe itself must
return `PASS` on the target host. All live gates stay closed:
`execution_allowed=false`, `allow_live_customer_access=false`,
`live_ready=false`, and `production_containment=NOT PROVEN`.
