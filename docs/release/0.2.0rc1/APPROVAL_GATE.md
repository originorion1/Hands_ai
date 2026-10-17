# Exact-artifact approval gate

The offline verifier is `scripts/verify_release_gate.py`. It requires an
operator-controlled manifest, artifacts, evidence records, trust store and
three detached approvals outside the source checkout. A clean checkout must
match the manifest's SHA and tree. All ten ORION wheel/source-archive hashes,
mandatory check records and known-risk record must match. Three distinct
trusted identities sign the same manifest: security, architecture and owner.
The source author cannot sign any of those roles. No real approval has been
issued for this draft.

The manifest's `version` names the release train. Its `component_versions`
maps each normalized distribution name to its exact package version. The
mapping must include exactly the Kernel, SDK, ERPNext connector, synthetic
connector and pilot compatibility package. Each version selects exactly one
wheel and one source archive; an additional package version is denied. This
allows the report-host ERPNext package to advance independently to
`0.2.0rc2` without relabeling unchanged Kernel and SDK bytes. A changed
artifact still requires a new source SHA/tree, rebuilt hash inventory, checks
and independent approvals.

The verifier does not generate an approval, check record or trust key. A
passing synthetic test exercises denial rules only and is never a release
decision. The current draft release manifests have pending checks and no
trusted real reviewer signatures, so promotion remains denied.
