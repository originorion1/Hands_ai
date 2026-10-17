# Independent offline release gate (issue #247)

`scripts/verify_release_gate.py` is a fail-closed promotion check. It does not
issue approvals or publish artifacts. The release operator supplies:

- A clean checkout at the exact `source_sha` and Git tree in the manifest.
- The ten expected `0.2.0rc1` wheels and source archives in one artifact directory.
- A manifest with the SHA-256 of every artifact and `PASS` for build,
  dependency integrity, vulnerability assessment, architecture review,
  security review, compatibility, full tests, deployment recovery and rollback.
- A separate hashed evidence record for every check and the known-risk register.
- A trust store held separately from the candidate repository. It binds each
  trusted Ed25519 public key to one reviewer identity and one role.
- Three detached approval files, signed by distinct trusted security,
  architecture and release-owner identities. None may be the source author.

An approval signs the SHA-256 of the exact manifest, its role, trusted key ID,
reviewer identity and `APPROVE` decision. Any change to the manifest, checkout,
artifacts or approvals invalidates promotion. The operator must authenticate
the trust store and maintain its custody outside the candidate artifact set;
passing `--trust` to an attacker-controlled file would defeat the trust model.
Private keys must never enter the repository or automated build environment.

Run after independent checks and approval, from a trusted operator host:

```sh
python scripts/verify_release_gate.py \
  --manifest /private/release/manifest.json \
  --artifacts /private/release/artifacts \
  --trust /private/owner/trusted-reviewers.json \
  --approvals /private/release/approvals \
  --source /private/release/clean-source \
  --records /private/release/records
```

Exit status 0 and `release_gate: PASS` mean only that the supplied evidence
and trusted signatures match. They do not establish customer consent, production
readiness or independent review by themselves. This draft has no real reviewer
approval; its final release decision remains denied until those records exist.
