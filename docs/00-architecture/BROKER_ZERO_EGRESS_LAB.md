# Broker zero-egress laboratory

This fixed experiment runs the existing local read-only Broker and its sealed
worker inside a bubblewrap namespace. It extends the consumer-isolation work;
it does not replace that profile or enable HTTPS egress.

Run from the repository with the development dependencies installed:

```sh
python3 tools/broker_namespace_probe.py
```

The launcher has no URL, command, mount, credential, or activation arguments.
The owner creates only synthetic files, credentials and local negative-control
listeners. The broker receives its private bootstrap through the owner's pipe,
not through application requests, command arguments or its launch environment.
The bootstrap is trusted fixture composition, not an authenticated production
secret service. The broker necessarily knows its own synthetic source and MAC
keys; the experiment tests denial of an *unrelated* owner secret.

The profile reuses `namespace_command` with an unprivileged owner UID/GID so
private file ownership and the existing journal checks remain valid. It retains
`--unshare-all`, cleared environment, dropped capabilities, read-only runtime
and code, and PID/mount/user/network namespaces. Only the dedicated broker state
directory is writable outside ephemeral storage. Config and synthetic source are
individual read-only mounts; unrelated owner files and Unix listeners are absent.
No external network route is provisioned. Root owners are BLOCKED.

Before entering the profile the owner verifies the denied targets are accessible.
Inside the broker process, custody witnesses run before Broker initialization.
Missing, false or malformed witnesses abort. Only then can the existing broker
start unarmed and accept its existing authenticated control/request messages.
The experiment exercises two record encodings, one admitted canonical observation,
pre-attempt denials for writes/scope/token/URL/credential/proposal/review misuse,
replay denial, and revoked fresh-process restart with retained attempt accounting.
Source/config bytes and the unrelated audit canary must remain unchanged.

The fixed outer launcher bounds runtime and terminates its process group on
expiry. Missing kernel/runtime prerequisites return BLOCKED, never a fallback to
an unconfined broker. An unconfined child is tested to deny before broker startup.

## Evidence limits

A test-suite pass is not a successful namespace experiment. The probe must return
PASS on the actual target host; BLOCKED records no broker containment proof.
The two synthetic record encodings are not two live ERP protocols. Metadata and
HTTPS remain covered by earlier, separate laboratories. This profile permits no
HTTPS acquisition: narrowly permitted broker-side HTTPS egress is the next
unproven boundary, and must not be implemented by sharing the host network.

The issuer, supervisor, fixture bootstrap and broker implementation remain trusted.
The broker can modify its own journal; this is not a protected external audit
service or proof against a compromised broker forging semantic evidence. There
is no production secret custody, operator authentication service, deployed
supervision, retention service, or customer configuration. Runtime read-only
mounts are not a general hostile-code sandbox certification.

Production modules, authorization, admission, scanners and release gates are
unchanged. `execution_allowed=false`, `allow_live_customer_access=false`,
`live_ready=false`, `production_containment=NOT PROVEN` remain mandatory.

## Existing-audit witness

The shared audit write probe opens the target with `O_WRONLY | O_APPEND` and
without `O_CREAT`. A hidden owner audit must not be confused with a newly
created same-path file in the writable private filesystem. Existing writable
audit targets still cause a failed check and a changed owner canary. Regression
tests exercise both cases with real filesystem I/O; they do not replace the
required host namespace experiment.
