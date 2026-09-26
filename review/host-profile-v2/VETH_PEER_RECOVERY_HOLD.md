# Staged veth peer recovery hold

This successor review bundle does not authorize host application or automatic veth deletion.
Once namespace or veth creation has been recorded, rollback makes a transaction-wide
hold decision before any cleanup. It retains the namespace, veth pair, namespace
nftables filter, host NAT and Docker rules, resolver, unit and receipt files, and
the consumed-grant marker. It reports `ROLLBACK_INCOMPLETE` with resource
categories and a sanitized cause code. Identity or placement uncertainty never
causes deletion. This deliberately leaves a partial state for manual recovery.

The disposable raw capture reports reciprocal names before the move and
reciprocal numeric `link_index` values after it. The verifier requires every
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
