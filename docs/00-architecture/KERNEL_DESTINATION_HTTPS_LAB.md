# Kernel destination-restricted synthetic HTTPS laboratory

Issue #159 continues draft PR #158. It adds a fixed test laboratory, not a
production networking capability or release-gate attestation.

```sh
python3 tools/kernel_destination_https_probe.py
```

The launcher accepts no endpoint, proxy, credential, command, mount or activation
argument. Required namespace, veth, bridge, nftables or TLS capabilities missing
on the actual host produce `BLOCKED`; no unconfined or application-only fallback
exists. Run outside a workspace socket-denying sandbox on the synthetic WSL host.

## Boundary and executable witnesses

An unprivileged host owner creates a disposable user namespace and a new private
network fabric. A bridge and two veth pairs connect the control fabric to distinct
source and broker network namespaces. There is no host interface, host network
namespace descriptor, default route, NAT, forwarding relay or production source.
All addresses are fixed RFC 5737 IPv4 / private IPv6 laboratory addresses.

The source runs in its own bubblewrap mount/user/PID profile with no writable
persistent mount. It holds the ephemeral TLS private key and serves synthetic
records. Separate fixed byte-canary listeners occupy the unapproved address,
approved address's alternate port and proxy port. They cannot relay traffic.
Control connections positively reach every tested listener; unapproved/alternate/
proxy listeners answer a byte challenge. Enabled IPv6 and IPv4-mapped IPv6 are
covered. Disabled IPv6 is reported, not silently counted as proven.

Before broker startup, trusted setup installs an nftables `inet` output-hook
default-drop chain. Exactly one numeric destination, TCP port `44443`, is accepted;
there is no broad established, loopback, DNS, UDP or IPv6 exception. Permanent
fixture neighbor entries avoid needing an ND egress exception. Destination-specific
reject counters precede the unconditional catch-all reject. The broker inherits
only this configured private network namespace, then bubblewrap unshares all other
supported namespaces, clears environment/descriptors and drops ALL capabilities.
Bubblewrap 0.9 has no network-namespace-FD option, so this profile explicitly
lists non-network namespaces; it never uses `--share-net` or joins host networking.

Broker UID 0 maps through private user namespaces to the unprivileged host owner;
it is not host root. It cannot remove its firewall, read the source file/private
key or write its read-only configuration, signed route or certificate. Only the
dedicated durable journal is mounted writable. Setup retains namespace-local
capabilities and remains trusted; it is not a network acquisition helper.

Before instantiating any broker or HTTP policy, a child uses raw `socket.connect`
for each positively reachable denied tuple. After EACH attempt, the owner reads
that tuple's named kernel counter and requires an increase, no connection, and no
approved-counter increase before acknowledging the next attempt. A generic
counter, timeout alone, absent listener or application URL rejection cannot pass.
On this WSL kernel a filtered connect may time out rather than return EACCES;
the per-tuple kernel-counter increase and reachable control supply the proof.

The existing signed TLS policy now supports a signed numeric fixture host while
retaining the original loopback default. Existing authorization, pre-I/O durable
budget reservation, exact received-byte normalization, sealed worker and canonical
admission are unchanged. Two record encodings acquire over IPv4 and enabled IPv6;
both redirect variants target an actually reachable unapproved listener and deny
without follow-up. Caller-selected proxy routes and synthetic HTTP/HTTPS/SOCKS
proxy environment entries cannot route acquisition. Ten unauthorized shapes cause
zero attempts, HTTP requests or approved-destination kernel traffic.

A separately confined, zero-egress reasoning child gets only its existing request
capability and canonical observations, not broker/source credentials, configuration,
signed route, source file, TLS key, parent environment or direct source access.
Immutable tenant/authorization provenance remains intact. Each case starts three
fresh broker namespaces with freshly installed/verifiable policies: initial read;
restart before stop proving exhausted budgets do not reset; restart after durable
stop/revocation proving rearm/read deny. Journal heads are pinned on restart and
attempt/failure/reserved-byte accounting remains exact.

## Limits and release status

Owner/issuer, fixture setup, profile signer, broker and journal key remain trusted.
This proves a fixed synthetic kernel destination boundary, not production routing,
hostile issuer resistance, independent credential/audit custody, customer readiness
or two ERP protocols. No production source, scanner, admission, authorization or
release gate is changed. Independent adversarial review and human merge authority
remain required. `execution_allowed=false`, `allow_live_customer_access=false`,
`LIVE_PILOT_READY=false`, `live_ready=false`, `production_containment=NOT PROVEN`.
