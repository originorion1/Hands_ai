# Local HTTPS broker laboratory

Issue #151 adds a fixed test-only HTTPS source experiment on top of PR #150.
Production broker/transport, authorization, admission, journal and release gates
remain unchanged. No source scan exemption or production network client is added.

Run with the existing development environment and local `openssl` executable:

```
python3 tools/local_https_broker_probe.py
```

The fixed runner launches only `tests/https_broker_lab.py`, with a 60-second limit,
bounded output, cleared environment and no operator URL, adapter or activation
parameter. Loopback socket denial or unavailable development dependencies produce
BLOCKED, not PASS. This is not the kernel-isolation probe.

## Actual composition

The fixture-only `HTTPSBroker` subclasses the existing broker at its fixed sealed
worker seam. The existing application API authenticates its read token and scope,
checks expiry/revocation and durably reserves an attempt before the fixture makes
any connection. The test supervisor verifies an owner-signed fixture profile using
the existing authentication utility; the profile binds the broker configuration,
loopback port and pinned certificate. It cannot supply a general destination.

The client always connects directly to numeric `127.0.0.1`, issues GET `/fixture`,
and has no redirects, proxy handling, arbitrary URLs, caller-supplied headers or
body. TLS requires certificate verification and hostname checking, with TLS 1.2
minimum. Tests generate temporary private self-signed certificates solely for the
local source; no certificate key or credential is committed. Trust-anchor mutation
rejects. The synthetic credential is sent only inside the verified TLS channel.

Only status 200, one bounded Content-Length, and an exact-length body are admitted.
Redirects, transfer coding, compression, ambiguous lengths and partial/oversized
responses reject. The socket timeout is one second. The received body is limited
to half the existing response reservation; wire-body plus normalized worker reply
must fit the whole reservation. The journal still reports worker-reply bytes, not
TLS/framing overhead. No claim of complete network-byte telemetry is made.

A digest check binds the received body to the fixture's approved source content.
The owner source file is deleted before acquisition. Received bytes are placed in
a private temporary file and normalized by the unchanged sealed source worker,
then re-admitted by the existing canonical supervisor path. The file is deleted
after worker completion. Observations retain existing scope/provenance. This is a
synthetic envelope protocol, not an ERP HTTP API or arbitrary response converter.

## Executable evidence

Eight real loopback cases cover both existing local record encodings, redirect,
oversized/partial response, timeout, wrong hostname and untrusted certificate.
Every case checks unauthorized requests before source I/O, one reserved attempt,
replay denial, external revocation and restart with preserved attempt count.
TLS-rejected cases must receive zero HTTP requests, including zero credentials,
at the server. Success uses actual received bytes and canonical observations.
A deliberately configured proxy cannot redirect this fixed direct client.

Separate deterministic tests use OpenSSL MemoryBIO handshakes to verify trust,
hostname and encrypted credential bytes without sockets. Policy tests exercise
routing/framing rejection before body access. Broker-composition tests substitute
only local bytes at the test transport seam, run the actual sealed worker, and
check pending reservations, admission and post-response expiry/revocation. These
substitution tests are not counted as real HTTPS or containment proof.

## Limits and remaining gates

All networking and source servers live under tests. The test-only subclass is
trusted code; production configuration cannot select it. A subclass is not a new
production plugin mechanism or mandatory egress boundary. The owner/issuer,
profile signer, certificate generator, test server and interpreter remain trusted.
The fixture runs brokers in fresh subprocesses but does not confine the reasoner
or broker with OS namespaces; prior WSL namespace proof does not automatically
compose into this HTTPS profile. Production destinations, protected credential
custody, mandatory OS egress policy, real certificate lifecycle, protected audit,
interruptible revocation and complete deployed supervision remain unproven.

Issue #157 separately composes this test transport with a private broker/source
network namespace. This profile itself remains an unconfined TLS laboratory.

PASS means only the fixed local HTTPS experiment completed. All release gates
remain closed: `execution_allowed=false`, `allow_live_customer_access=false`,
`live_ready=false`, `production_containment=NOT PROVEN`. No customer connection,
write authority, promotion or merge is performed.
