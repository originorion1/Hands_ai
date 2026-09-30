# Distinct v2 systemd pair — review only

This addendum accompanies `HOST_PROFILE_V2_DISTINCT_PAIR_REVIEW.json`. It proposes two new unit names and a new Unix socket path. Neither unit file exists in `/etc/systemd/system`, and no unit or host state was changed by this review.

## Verified binding rule

The installed host reports systemd 255. The installed `orion-pilot-metadata.socket` has `Accept=yes`, `MaxConnections=1`, is inactive and disabled, and has no `Service=` override. systemd v255 documentation states that `Accept=yes` instantiates the same-basename `@.service` template once per accepted connection. `Service=` can redirect only an `Accept=no` socket. Thus the old socket would address `orion-pilot-metadata@.service`, not a distinct v2 template. The generated instance ID is not the ORION session ID. An inactive or disabled socket cannot currently accept connections, but its static binding rule still matters if it is ever activated.

Source: https://github.com/systemd/systemd/blob/v255/man/systemd.socket.xml

The proposed pair is `orion-pilot-metadata-v2.socket` plus `orion-pilot-metadata-v2@.service`, with `ListenStream=/run/orion-pilot-v2/operator.sock`. The `Accept=yes` name rule pairs them without `Service=`. The installed old pair remains untouched. The proposed socket must remain disabled and inactive, with no active v2 service instance, while under review.

## Exact proposed content differences

The diffs below compare installed file contents to proposed *new* file contents; they do not instruct replacement of the old files. All unshown lines are unchanged. The proposed service retains `StandardInput=socket`, `StandardOutput=inherit`, `User=orion-pilot`, `ProtectSystem=strict`, `Restart=no`, and its existing capability restrictions. Its only configured `ReadWritePaths` becomes the new session directory; exact child state and custody paths remain a gate.

Service template:

```diff
--- installed/orion-pilot-metadata@.service
+++ proposed/orion-pilot-metadata-v2@.service
@@ -1,5 +1,5 @@
 [Unit]
-Description=ORION bounded metadata pilot (manual exact-session activation only)
+Description=ORION bounded metadata pilot (socket-activated exact-session v2)
 After=network-online.target
 Wants=network-online.target
 
@@ -13,14 +13,14 @@
 WorkingDirectory=/
 Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
 UMask=0077
-NetworkNamespacePath=/run/netns/orion-pilot-f7c7955
-BindReadOnlyPaths=/etc/netns/orion-pilot-f7c7955/hosts:/etc/hosts
-ExecStart=/opt/orion-pilot/releases/cac68bf5b74b0fec27ea6be5e9374a8a31ac5476/bin/python -I -m orion.pilot.deployment --serve /var/lib/orion-pilot/sessions/orion-metadata-133d6533f23532585dd9f385/manifest.json
+NetworkNamespacePath=/run/netns/orion-pilot-fc4cb43c-v2
+BindReadOnlyPaths=/etc/netns/orion-pilot-fc4cb43c-v2/hosts:/etc/hosts
+ExecStart=/opt/orion-pilot/releases/cac68bf5b74b0fec27ea6be5e9374a8a31ac5476/bin/python -I -m orion.pilot.deployment --serve /var/lib/orion-pilot/sessions/orion-metadata-fc4cb43c1468d83a558b589a/manifest.json
 NoNewPrivileges=yes
 CapabilityBoundingSet=
 AmbientCapabilities=
 ProtectSystem=strict
-ReadWritePaths=/var/lib/orion-pilot/sessions/orion-metadata-133d6533f23532585dd9f385
+ReadWritePaths=/var/lib/orion-pilot/sessions/orion-metadata-fc4cb43c1468d83a558b589a
 ProtectHome=yes
 KillMode=control-group
 Restart=no
```

Socket:

```diff
--- installed/orion-pilot-metadata.socket
+++ proposed/orion-pilot-metadata-v2.socket
@@ -1,8 +1,8 @@
 [Unit]
-Description=ORION root-only exact-session operator channel (candidate, not installed)
+Description=ORION root-only exact-session operator channel v2
 
 [Socket]
-ListenStream=/run/orion-pilot/operator.sock
+ListenStream=/run/orion-pilot-v2/operator.sock
 Accept=yes
 SocketMode=0600
 SocketUser=root
```

The new socket keeps `SocketMode=0600`, root ownership, `DirectoryMode=0700`, `MaxConnections=1`, and `RemoveOnStop=yes`. Its parent directory and socket node are distinct from the old `/run/orion-pilot/operator.sock` path.

## Durable two-GET budget is separate

`MaxConnections=1` caps simultaneous service instances only. It does not cap lifetime connections, HTTP requests, or metadata GET attempts. The approved scope remains two total customer metadata GET attempts for `orion-metadata-fc4cb43c1468d83a558b589a`. Before any customer I/O, a separate durable, fail-closed budget/audit binding must account for attempts across every socket-created instance and restart, including uncertain outcomes. This review has not verified that binding or an authenticated attempt count; it grants no customer I/O. The original `orion-metadata-669453aa21170485fbb1f294` remains owner-attested unused with authenticated metadata attempt count unknown.

## Preservation and remaining gates

Collision clearance remains false. Preserve without deleting or rewriting the old namespace inode marker, `/etc/orion-pilot/network-provisioned`, `/etc/netns/orion-pilot-f7c7955`, and both installed metadata unit files. The old service/socket SHA-256 values and operator-pasted inspection remain in the earlier review record. The v2 namespace inode remains a future runtime observation, never a predeclared profile value.

Owner decisions:

1. Approve or reject distinct v2 socket/template names and `/run/orion-pilot-v2/operator.sock` as the replacement pairing. The existing profile is preserved; the distinct-pair variant is a proposal only.
2. Decide whether to reuse `orionp0`/`orionp1` and the private /30. Distinct link names require a new route-only probe and repinned exact route JSON; reused names require a fresh collision check.
3. Approve the proposed fixed v2 manifest path, retained interpreter artifact, and the new session-directory write allowance after exact custody and child-state-path review.

Application and qualification gates after owner decisions:

1. Review exact unit contents and hashes, check all proposed names and paths absent immediately before exclusive creation, and keep the old pair and historical remnants untouched. A separate authorized host procedure must define rollback limited to proven v2-owned resources.
2. Verify both sockets disabled and inactive and all metadata service instances inactive; enumerate actual generated template instances by wildcard, not by session ID. Validate the new pair's static `Accept=yes` mapping and synthetic socket behavior without customer I/O, if separately authorized.
3. Obtain an authenticated runtime host receipt for the fresh namespace inode, exact IPv4/IPv6 main and all-table routes, disabled forwarding, resolver, artifact/custody, firewall, egress, and stopped service binding. Keep `collision_clearance=false` until this evidence is reviewed.
4. Verify the separate durable two-total-GET enforcement and audited attempt accounting before any customer I/O. A later time-bound DNS/TLS-only qualification requires its own authorization and does not satisfy the GET-budget gate.
5. Packet subjects, signing, enrollment, service start, and launch remain outside this review.
