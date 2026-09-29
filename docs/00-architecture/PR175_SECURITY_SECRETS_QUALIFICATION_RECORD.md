# PR #175 SECURITY and SECRETS qualification record

## Scope and decision boundary

This is one evidence record for draft PR #175. It binds the corrected installed
runtime to its source, wheel, installation-specific `RECORD`, exercised profile,
WSL host and commands. It contains only allowlisted metadata and redacted
observations. It does not contain environment values, credential-bearing command
lines, secret contents, customer identifiers or customer traffic.

The record does not approve a host, promote SECURITY or SECRETS, merge the PR, or
activate access. `LIVE_PILOT_READY=false` and `execution_allowed=false` remain
unchanged.

## Source and artifact identity

| Item | Bound value | Provenance |
|---|---|---|
| Runtime source commit | `b3a90e8bdcd363443371668ce0c59f0a62dd9297` | Commit `fix(pilot): close restart and rotation lifecycle gaps`; tree `dcd8882aa03f5b0481b11f9ace00a62139349b22`. |
| Assessment commit | `7d6d5f79ce3709b0bbcd8d702639c1f0fef789f6` | Direct child of the runtime source commit; tree `979d485a8ce00890e1ce85789d510fe1486ffa82`. `git diff --name-status b3a90e8..7d6d5f7` reports only `docs/00-architecture/SECURITY_SECRETS_CLOSURE_ASSESSMENT.md`; `git diff --quiet ... -- src pyproject.toml` succeeds. |
| Qualification publication | The Git commit containing this document. | This publication adds documentation only. The packaged `src/orion` and `pyproject.toml` inputs remain those of the runtime source commit. |
| Package | `orion-core 0.1.0` | `pyproject.toml` and installed artifact identity. |
| Corrected wheel SHA-256 | `982ee7c524d39edb144958ab5fdb8c4be6da62e049600fb66b83b881026cf112` | Recovered from four retained corrected wheels. A targeted build from assessment commit `7d6d5f7...` produced the same byte-identical wheel. |
| Console entry point | `orion-runtime = orion.pilot.deployment:main` | Wheel entry-point metadata and closed deployment profile. |

Source and ancestry were recovered with these read-only commands:

```text
git rev-parse HEAD
git rev-parse HEAD^{tree}
git rev-parse b3a90e8bdcd363443371668ce0c59f0a62dd9297^{tree}
git diff --name-status b3a90e8bdcd363443371668ce0c59f0a62dd9297..7d6d5f79ce3709b0bbcd8d702639c1f0fef789f6
git diff --quiet b3a90e8bdcd363443371668ce0c59f0a62dd9297..7d6d5f79ce3709b0bbcd8d702639c1f0fef789f6 -- src pyproject.toml
```

Targeted current-source build command:

```text
/tmp/orion-pr175-builder/bin/python -m pip wheel --no-deps --no-build-isolation --no-index --wheel-dir /tmp/orion-pr175-qualification-7d6d5f7 /mnt/c/Users/msi/projects/hands_ai
```

Result: exit 0, wheel size 283,993 bytes, SHA-256
`982ee7c524d39edb144958ab5fdb8c4be6da62e049600fb66b83b881026cf112`.
The build used Python 3.12.3 and Hatchling 1.27.0. It was performed only to bind
the documentation-only head to the already recovered corrected wheel; it did not
repeat runtime behavior.

## Installation and profile bindings

Installed `RECORD` digests are installation-specific because pip records the
generated console-script shebang and the local wheel URL. Profile digests are
exercise-specific because the closed profile contains absolute state/key
references. Neither difference is a credential generation. The stable wheel
SHA-256 above binds the common package content.

| Evidence context | Wheel SHA-256 | Runtime-reported installed `record_sha256` | Exercised `deployment_profile_sha256` | Status |
|---|---|---|---|---|
| Targeted current-head artifact inspection | `982ee7c...cf112` | `cb78dfd366ec89334c262b08b1cf9f05160f675c3a53c6b13af759111113e3a1` | Not applicable; inspection-only installation | 111 hashed installed files verified; `execution_allowed=false`. |
| Targeted current-head installed security case | `982ee7c...cf112` | `364c0eaa77087d2eaebe42600f8c782f13324ef069dfdd39baca2193729917cc` | `a369a9dc0186d3002b2beb7da320184e2178071e1418a7138b088019e514692f` | PASS. |
| Recovered restart-revalidation case | `982ee7c...cf112` | `ef5a4eeba3eae087b8960e4fc7a92ba6738df0b188a70df3be3fff1ba51a3f8d` | `5c60139a20ed80dd8e88bce9acf2511ffa33ff1883f317e93c5764e4462e83d2` | PASS; retained focused job. |
| Recovered cutoff-first rotation case | `982ee7c...cf112` | `ef5a4eeba3eae087b8960e4fc7a92ba6738df0b188a70df3be3fff1ba51a3f8d` | `5ad2d4be3d2491ff3c31b2282db7153bfbbc1936fc2b25deb82c13338e6d2225` | PASS; retained focused job. |

Current-head artifact inspection command:

```text
/tmp/orion-pr175-qualification-7d6d5f7/installed/bin/orion-runtime --artifact
```

Result: exit 0; package `orion-core 0.1.0`; 111 files verified; installed
`record_sha256=cb78dfd366ec89334c262b08b1cf9f05160f675c3a53c6b13af759111113e3a1`;
`LIVE_PILOT_READY=false`; `execution_allowed=false`.

The exercised security profile is version 1 and binds:

- the installed `orion-runtime` / isolated `orion.pilot.deployment` entry point;
- audit, evidence, authorization, gateway, acquisition and reasoning roles;
- role-specific read-only key references, with no key references for acquisition
  or reasoning;
- gateway-only network access to synthetic `192.0.2.2:44443`;
- denied redirects, proxies and alternate ports;
- validate-all-or-deny startup, cutoff-before-stop, restart revalidation and
  forbidden authority restoration.

The profile contains references and policy, not secret values. Its digest does
not identify credential generations.

## Host context

The current and retained installed evidence used the existing disposable WSL
environment:

| Allowlisted host item | Observed value | Evidence status |
|---|---|---|
| Distribution | Ubuntu 24.04.4 LTS | Recovered host inventory; not re-executed by the targeted security case. |
| Kernel | Linux `6.6.87.2-microsoft-standard-WSL2` | Recovered host inventory; not re-executed by the targeted security case. |
| Operator | uid/gid 1000, account `orion` | Recovered host inventory. |
| Disposable private storage | `/tmp`, Linux ext2/ext3 filesystem | Recovered host inventory. |
| Rootless namespace capability | `unshare --user --map-root-user --net true` exited 0 | Recovered one-shot capability probe. |
| Native tools | util-linux `unshare/nsenter 2.39.3`; bubblewrap 0.9.0; iproute2 6.1.0; nftables 1.0.9; curl 8.5.0; OpenSSL 3.0.13 | Recovered version-only inventory. |

The recovered inventory came from `uname -srvo`,
`stat -f -c '%T %m' /tmp`, `id`, `sed -n '1,12p' /etc/os-release`, each
tool's version command, and the one-shot
`unshare --user --map-root-user --net true` probe. No environment dump was
captured. Namespace identifiers observed during inventory were not retained.

The ordinary foreground sandbox cannot query host nft netlink. The installed
security case ran through the previously approved execution context and directly
read counters inside the disposable gateway namespace. That distinction is
preserved; foreground denial is not presented as a host capability failure.

## Redacted confinement, mount and network observations

Fresh targeted command:

```text
ORION_BUILD_PYTHON=/tmp/orion-pr175-builder/bin/python .venv/bin/python -m pytest -q -s tests/test_packaged_runtime.py::test_installed_runtime_against_unmodified_private_https_source[security]
```

Job `job-mu47sa1m-e83fe08d` completed with exit 0: **1 passed** in 34.55
seconds. Its emitted report was `PASS`, used the wheel, installed `RECORD` and
profile hashes listed above, reported IPv6 enabled, and retained only boolean
checks and named packet-counter deltas.

### Process and capability observations

| Role or surface | Redacted executed result |
|---|---|
| audit | user, pid and mount namespaces separated; network confined; effective capabilities dropped; proxy environment absent. |
| evidence | user, pid and mount namespaces separated; network confined; effective capabilities dropped; proxy environment absent. |
| authorization | user, pid and mount namespaces separated; network confined; effective capabilities dropped; proxy environment absent. |
| gateway | user, pid and mount namespaces separated; gateway private network confirmed; effective capabilities dropped; proxy environment absent. |
| acquisition | user, pid and mount namespaces separated; network confined; effective capabilities zero; proxy and control-credential environment names absent. |
| reasoning | actual process namespaces separated; effective capabilities zero; proxy and control-credential environment names absent; issuer, signing key, source credential, worker secret and custody storage paths inaccessible. |

No namespace inode, raw environment, command line, descriptor content or secret
value is retained in this record.

### Mount and custody observations

The executed security report confirms:

- the installed-prefix original custody path is denied at startup;
- protected original paths and undeclared role paths are absent from acquisition
  and reasoning;
- attempts to read protected role paths are denied;
- attempts to truncate, unlink or replace audit/evidence custody storage are
  denied from the untrusted application roles;
- protected custody processes are not visible inside the application PID jail,
  and their environment, memory, standard descriptor and root paths cannot be
  opened;
- acquisition and reasoning cannot weaken nft policy, enter an ancestor network
  namespace or create a usable nested source network.

These are executed access observations through the actual installed jail. They
are not an independent reviewer enumeration of every host-side mount or file
descriptor.

### Named network-counter observations

Each rejected probe incremented its dedicated nft counter by exactly one packet:

| Named counter | Delta |
|---|---:|
| `ipv4-unapproved` | 1 |
| `ipv4-alternate-port` | 1 |
| `ipv4-proxy` | 1 |
| `ipv6-unapproved` | 1 |
| `ipv6-alternate-port` | 1 |
| `ipv6-proxy` | 1 |
| `ipv4-mapped-ipv6` | 1 |
| `ipv6-other-family` | 1 |

The same report confirms the approved ordinary bearer path, unauthorized denial
before source I/O, no source I/O or connections from the application-compromise
cases, and unchanged audit/evidence history.

## Startup denial and lifecycle results

### Fresh targeted startup denial

Command:

```text
/tmp/orion-pr175-qualification-7d6d5f7/installed/bin/orion-runtime --serve /tmp/orion-pr175-qualification-7d6d5f7/nonexistent-manifest.json
```

Result: exit 2; `status=BLOCKED`; failure boundary `private_bytes`;
`LIVE_PILOT_READY=false`; `execution_allowed=false`; no role process started.
The response contains no private input.

### Recovered restart and rotation evidence

The following command was executed previously by retained job
`job-mu430z3r-700ac074`; it was not rerun for this record:

```text
ORION_BUILD_PYTHON=/tmp/orion-pr175-builder/bin/python .venv/bin/python -m pytest -q tests/test_packaged_runtime.py::test_installed_runtime_against_unmodified_private_https_source[restart_revalidation] tests/test_packaged_runtime.py::test_installed_runtime_against_unmodified_private_https_source[rotation]
```

Result: exit 0, **2 passed** in 54.69 seconds.

The restart case changed the installed source-credential metadata to mode
`0640`; restart returned blocked through existing cutoff, removed gateway
egress, terminated gateway/acquisition, performed zero further source I/O and
did not reconstruct custody services first.

The rotation case executed initial authorized acquisition, ignored an
interrupted staged file, invoked cutoff, atomically installed a compliant
`0600` replacement, restarted unarmed, required fresh scoped metadata/read
authorization, acquired with the replacement, received source HTTP 401 for the
obsolete generation, and preserved audit sequence/scope, consumed budgets,
durable stop and cutoff behavior. Neither credential generation appeared in the
profile or audit output.

The rotation result is specifically cutoff-first. If an authorized acquisition
is rejected after its durable attempt is reserved, the attempt remains pending
and restart fails closed. No cancellation, rollback or repair contract exists.

## Broader retained evidence

This section is recovered evidence, not fresh execution:

- `job-mu43a7m2-b280432f`: corrected working tree later committed as runtime
  source commit `b3a90e8...`; exit 0. Its exact sequence was
  `.venv/bin/python -m py_compile` on the four changed Python files,
  `.venv/bin/ruff check .`,
  `ORION_BUILD_PYTHON=/tmp/orion-pr175-builder/bin/python .venv/bin/python -m pytest -q`,
  `.venv/bin/python -m orion.demo`, `git diff --check`, and an
  `Orchestrator.source_capability_scan` limited to the five changed files.
  Pytest reported **1866 passed**; the demo retained
  `execution_allowed=false`; every command passed.
- GitHub Actions run `35107111054`: success at exact runtime source commit
  `b3a90e8...`, recovered with
  `gh run view 35107111054 --json headSha,conclusion,status,url`.
- GitHub Actions run `35109669413`: success at exact documentation-only
  assessment commit `7d6d5f7...`, recovered with
  `gh run view 35109669413 --json headSha,conclusion,status,url`.

Older PR #173 evidence is not used as current-artifact execution evidence in
this record.

## Remaining technical implementation, configuration and verification gaps

These gaps cannot be supplied by a reviewer signature:

1. **Independent rollback witness — missing technical capability.** Audit/evidence
   MACs and the accepted tip do not detect privileged rollback of both store and
   tip. Qualification requires an independently retained monotonic witness and
   an executed older-store-plus-tip restore that is rejected against it.
2. **Wheel-only launch control — missing host configuration.** No deployed
   service/unit policy yet fixes the immutable wheel, isolated entry point,
   manifest/profile path, absence of checkout mounts and absence of
   `PYTHONPATH` or general module/shell selectors.
3. **Secret-provider lifecycle — missing host/custody configuration.** The
   selected provider must demonstrate role ownership, `0600` single-link regular
   files, atomic replacement, staged/obsolete material removal, and issuer/worker
   key backup and rollback controls using synthetic values.
4. **Pending-attempt recovery — missing architectural contract for sources that
   can rotate independently.** The current constrained pilot is technically safe
   only when cutoff precedes source invalidation. Otherwise the fail-closed
   pending state has no recovery mechanism.
5. **Reviewer-side mount/descriptor enumeration — remaining verification gap.**
   The installed security case proves role-visible denial and process isolation,
   but an independent reviewer has not yet captured an allowlisted host-side
   enumeration of each role's mount targets and descriptor classes.

No additional implementation defect is established for the supported installed
synthetic boundary beyond the explicitly identified rollback-witness and
pending-recovery limitations.

## Reviewer decisions still required

These decisions cannot substitute for any technical gap above:

1. An independent security reviewer must accept or reject the exact artifact,
   confinement, launch-surface and network-counter evidence.
2. An independent secrets/custody reviewer must accept or reject the role
   custody, secret-provider lifecycle and cutoff-first rotation evidence.
3. The architecture authority must accept or reject the constrained-pilot
   cutoff-first operating contract and decide whether pending recovery requires
   a separate implementation issue.
4. After the technical gaps are actually closed, the human release authority
   must reconcile the unchanged SECURITY and SECRETS gates. No such gate decision
   is recorded here.

The existing architect comment accepts runtime source commit `b3a90e8...` for
human integration only. It is not an independent security/custody approval and
does not approve this qualification record, production use, customer access or
activation.
