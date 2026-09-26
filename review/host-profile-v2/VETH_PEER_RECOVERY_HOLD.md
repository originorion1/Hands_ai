# Staged veth peer recovery hold

This successor review bundle does not authorize host application or automatic veth deletion.
Once namespace or veth creation has been recorded, rollback makes a transaction-wide
hold decision before any cleanup. It issues no deletion for the recorded namespace,
veth pair, namespace nftables filter, host NAT and Docker rules, resolver, unit and
receipt files, or consumed-grant marker. It reports `ROLLBACK_INCOMPLETE` with a
sanitized cause code. `rollback_unresolved_categories` names recorded journal kinds
and failed proof steps, **not observed surviving resources**. The legacy correction
receipt field `retained_v2_resource_names` has the same limited meaning. An operator
must inspect actual presence and protections before planning manual recovery. Identity
or placement uncertainty never causes deletion.

The disposable raw capture reports reciprocal names before the move and
reciprocal numeric `link_index` values after it. Matching raw sysfs `ifindex` and
`iflink` text was captured in each endpoint's network and mount namespace; the
capture tool rejects a sysfs/JSON mismatch. The verifier requires every
available peer reference to agree with recorded indexes and reciprocal sysfs
reads. Those checks are separate snapshots. A link can be replaced
after the final check and before a later name-based delete. Linux `RTM_DELLINK`
supports an interface-index selector, but it does not atomically compare the
verified peer name, reciprocal indexes, and namespace placement in the same
delete operation. Index targeting alone is not a proof against replacement.

Any future automated deletion requires a separately reviewed mechanism that
**explicitly enforces a maintenance window or equivalent serialization**: no
other actor with network-administration capability may rename, move, delete,
or recreate either endpoint or the namespace from the first identity read
through deletion acknowledgment. A script-local lock alone does not enforce
this against other host actors. If that condition cannot be established and
enforced, retain the resources for manual recovery. Do not infer absolute
no-unverified-delete safety from this patch or its mock tests.

Primary references:

- Linux rt-link netlink specification: https://docs.kernel.org/6.10/networking/netlink_spec/rt_link.html
- Linux `rtnl_dellink` implementation: https://code.googlesource.com/linux/torvalds/linux/+/4df22ca85d3d73f9822b1a354bb56dd1872180cd/net/core/rtnetlink.c#3121
