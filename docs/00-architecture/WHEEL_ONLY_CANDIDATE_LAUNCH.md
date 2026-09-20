# Wheel-only candidate-host launch contract

## Enforced application boundary

Deployment profile version 3 binds each legacy synthetic mutable runtime launch to:

- the installed distribution name, version, hashed `RECORD`, installation prefix
  and package root;
- the exact installed virtual-environment interpreter;
- isolated module mode, `-I -m orion.pilot.deployment`;
- one operation, `--enroll-witness` or `--serve`, followed by the profile-bound
  canonical absolute private-manifest path;
- working directory `/`; and
- rejection of `PYTHON*`, `LD_*`, `BASH_ENV`, `CDPATH`, `ENV` and `SHELLOPTS`
  launch variables.

The running deployment module must be the hashed file represented by the
installed distribution and must resolve below the active installed prefix. All
hashed `RECORD` paths must also resolve below that prefix. Editable installs,
checkout imports, escaped/symlinked distribution paths and altered installed
files are denied before witness enrollment, custody reconstruction or source
I/O.

Python's original process arguments, not a reconstructed application value, are
compared with the fixed command. Extra, omitted or reordered flags, another
module, non-isolated mode, the generated console script, a copied manifest,
another working directory or a disallowed loader/interpreter environment is not
a supported mutable launch. `orion-runtime --artifact` and `--health` remain
non-mutating inspection surfaces; the console script cannot enroll or serve.

The outer process validates the manifest/profile/artifact before opening the
rootless private supervisor. The internal handoff uses the same installed
interpreter and module, the same manifest, exactly two inherited kernel
namespace descriptors, a fixed argument order, working directory `/` and a
closed `PATH`-only environment. The existing supervisor then retains ownership
of role modules, mounts, namespaces, capabilities, egress, custody and lifecycle
cutoff. This contract does not duplicate those controls.

## Candidate-host launch record

After installing the approved wheel into `/operator/runtime`, an unprivileged
candidate-host service must fix this command literally (with the approved
manifest path substituted once):

```text
WorkingDirectory=/
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/operator/runtime/bin/python -I -m orion.pilot.deployment --serve /operator/private/runtime-manifest.json
```

The one-time enrollment command uses the identical interpreter, mode, working
directory, environment and manifest path:

```sh
cd /
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /operator/runtime/bin/python -I -m orion.pilot.deployment \
  --enroll-witness /operator/private/runtime-manifest.json
```

The host service must not use a shell, `%i`/environment-selected manifest,
`PYTHONPATH`, a checkout working directory, a generic module selector or the
console script for `--serve`. Qualification records the exact unit/configuration,
wheel SHA-256, installed `RECORD` digest, profile digest, interpreter/prefix,
manifest path, unprivileged account and required tool/kernel observations.

## Trust boundary and remaining host dependencies

These checks fail closed for application launch drift and ordinary
misconfiguration. They do not constrain a trusted host administrator who can
replace the interpreter, wheel, manifest/profile and service policy together,
alter mount or kernel state, or execute another program. They are not publisher
signature, secure/measured boot, whole-host rollback protection or independent
host attestation.

The candidate host must still provision and independently review the exact
service/unit, unprivileged identity, immutable approved wheel transfer, private
manifest/key/state locations, rootless user/network namespace support, bubblewrap,
`ip`, `nft`, `nsenter` and `curl`. Missing application or kernel controls deny
startup. `LIVE_PILOT_READY=false` and `execution_allowed=false` remain unchanged.

Issue #186 adds an explicit profile v4 only for the offline
`candidate_erpnext_read_only` manifest. It retains the same installed interpreter,
module, manifest, environment, filesystem, role, lifecycle, reserved-address and
kernel controls. Its sole profile addition is the fixed
`erpnext_read_only_v1` protocol marker. Profile v3 remains shape-compatible;
there is no silent migration, production destination, customer activation, or
allowlist widening. See `NATIVE_ERPNEXT_INSTALLED_QUALIFICATION.md`.
