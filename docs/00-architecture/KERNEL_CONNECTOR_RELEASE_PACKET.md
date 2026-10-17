# Kernel/connector integration packet

Exact canonical base and eight draft heads appear in
`ARCHITECTURE_INDEPENDENT_REVIEW.md`. All adjacent ancestry checks passed.
Proposed human merge order is
`#219 → #220 → #223 → #224 → #225 → #235 → #240 → #241`.
After each merge, compare the new canonical tree with the reviewed candidate
tree, refresh package hashes and re-run checks if the tree differs. The two
P0 evidence-integrity fixes are mandatory; stopping at #235 is unsafe.

At #235 the clean artifact checks show physical Kernel independence and
metadata-only registration. The #240/#241 hosted full suites passed on their
exact heads. Those checks do not prove the final merge tree or authorize
network access, connector execution, customer data, production signing or
release promotion. A registered connector remains a capability candidate;
the Kernel authorization envelope is still required for each use.

Open integration gates: independent exact-tree security/architecture review,
approved release manifest and trust store, stable-Linux full suite on the
final tree, release version compatibility, deployment/recovery qualification,
and owner merge decision. Existing staging remains on its prior canonical
installation. No automatic merge is requested.
