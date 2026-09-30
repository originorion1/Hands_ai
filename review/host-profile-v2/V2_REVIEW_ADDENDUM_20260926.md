# Host profile v2 review addendum — 2026-09-26

Status: review only. Collision clearance remains false. This is not an apply procedure, host receipt, qualification result, or authorization to prepare subjects.

## Inspection evidence

`HOST_INSPECTION_20260926.json` records the operator-pasted read-only root inspection. It found no host private-/30 address or route hit, pilot interface, matching nftables object, or named v2 namespace; no loaded metadata service instance was listed. The `Accept=yes` socket was inactive and disabled; the template unit-file state was static. These are point-in-time observations, not production route or egress qualification. The root-only network marker digest is operator-reported; it was not independently read in this review process.

Preserved historical remnants, never to be deleted or rewritten by v2 or its rollback:

- `/home/orion/.orion/deployment-prep/route-b-pr205/host-base-namespace.inode` (present, SHA-256 `f1da5f0db59de5e9d4bbe7ba53ec6a8f0fbb9fca2500a653b5e186761eccc452`)
- `/etc/orion-pilot/network-provisioned` (present, operator-reported SHA-256 `f1da5f0db59de5e9d4bbe7ba53ec6a8f0fbb9fca2500a653b5e186761eccc452`)
- `/etc/netns/orion-pilot-f7c7955` (present directory)

## Proposed v2 identity and preservation rule

The v2 review design uses exclusive **new persistent names**:

| Resource | Proposed v2 name or path | Inspection |
| --- | --- | --- |
| Network namespace and handle | `orion-pilot-fc4cb43c-v2`, `/run/netns/orion-pilot-fc4cb43c-v2` | absent |
| Namespace inode observation marker | `/home/orion/.orion/deployment-prep/route-b-pr205/host-base-namespace-v2.inode` | absent |
| Network-provisioned marker | `/etc/orion-pilot/network-provisioned-v2` | absent |
| Per-namespace resolver directory | `/etc/netns/orion-pilot-fc4cb43c-v2` | absent |
| Host NAT table | `orion_pilot_nat_v2_fc4cb43c` | no matching nftables object reported |
| Namespace filter table | `orion_pilot_v2_fc4cb43c` | proposed; namespace does not yet exist |
| Host firewall comment | `orion-pilot-fc4cb43c-v2` | no matching nftables object reported |

Any future procedure must check all of these names absent immediately before exclusive creation, reject symlinks and pre-existing files, and record the new namespace inode only after creation in a separate runtime receipt. The profile must not predeclare an inode. On failure, rollback may remove only v2-owned resources whose identity is proven by that receipt. No v2 step may invoke the old scripts' cleanup modes or remove, truncate, replace, or change the three historical remnants above.

The pinned route fixture still uses the historically used *ephemeral* interface names `orionp0` and `orionp1` and the private `10.254.205.0/30`. They were absent in this point-in-time host inspection, but are not new names. Their reuse requires a fresh no-collision check at any future application. Changing `orionp1` would invalidate the pinned route JSON and require a new route-only probe and profile review. The existing socket and service template names are likewise retained in the current profile; their exact future binding and backup are review gates below. Thus exclusive naming is confirmed for proposed v2 persistent namespace, marker, resolver, and nft identities, not for every interface or unit name.

## Pinned route evidence

The complete successful route-only probe JSON line has SHA-256 `c1836d81ed25730f8efd8582e599224256e7fd3e8f4a4bc206317118830e22c0`. The unchanged `HOST_PROFILE_V2.json` has SHA-256 `f876b58e0e7c26bd41cf6a50cfa7a789519fafad7fd70938ba826206789d7058`. Its four route arrays equal the raw probe arrays as full JSON objects, including absent fields: IPv4 main 2, IPv4 all-table 7, IPv6 main 1, IPv6 all-table 3. Both observed `fe80::/64` entries explicitly have `scope=global`; no `ifindex` appeared, so no field is excluded from equality. This is disposable fixture evidence, not a production host route receipt.

## Remaining review gates

1. Owner review of the v2 resource names and the explicit reuse of `orionp0`, `orionp1`, the private /30, and the existing unit names. If all names must be new, the current pinned route evidence and service binding need revision before any host procedure.
2. Review an exact stopped-unit and socket-activation diff, including `Accept=yes` instance discovery, session binding, all state/write paths, backup of any changed unit file, and a fail-closed check that customer units remain stopped and disabled. The installed template is still the retained prior binding; no unit has been changed here.
3. Before any future host application, repeat root collision inspection and establish an authenticated runtime receipt for the newly observed namespace inode, exact IPv4/IPv6 main and all-table routes, disabled in-namespace forwarding, resolver, artifact and custody, firewall, egress, and stopped service binding. Preserve omitted route fields as absent.
4. Review the complete non-executable host procedure and rollback identity rules. Only after separate authorization could the previously allowed DNS/TLS-only qualification refresh be considered. No DNS/TLS or customer HTTP was run for this addendum.
5. Keep packet preparation, signing, enrollment, service start, and launch outside this review. The original session remains owner-attested unused with authenticated metadata attempt count unknown; the consumed predecessor and three authenticated unconsumed sessions remain separate.
