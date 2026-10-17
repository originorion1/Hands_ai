# ORION 0.2.0rc1 draft release notes

This is an unmerged, synthetic-only release candidate. Its five independently
installable Python distributions are `originorion-core`,
`originorion-connector-sdk`, `originorion-connector-erpnext-discovery`,
`originorion-connector-synthetic`, and `originorion-erpnext-pilot-compat`.
Kernel, neutral SDK and synthetic connector use `0.2.0rc1`. The ERPNext
connector/report host and dependent historical pilot compatibility package
use `0.2.0rc3` after independently versioned recovery and transport fixes.
The Command Center and Stage A status expose release-train identity
`0.2.0rc1`; the existing v1 report and
status wire contracts remain unchanged.

The candidate stacks the physically separated connector work, atomic evidence
batching and snapshot custody, and the reviewed synthetic report authentication
and Vercel API boundary. Exact reviewed connector metadata accepts this
prerelease version without importing connector code or granting authority.

It does not include customer login, tenant onboarding, customer ERP access,
production signing, automatic execution, or verified business savings.
`execution_allowed=false` remains the invariant. The synthetic staging
deployment continues on its existing canonical source and is not upgraded by
this branch.

The source SHA, tree, per-component versions, wheel and archive hashes must
be read from a separately generated immutable manifest after the final source
commit. An earlier build or a green test on a parent PR is not qualification
for a changed tree.
