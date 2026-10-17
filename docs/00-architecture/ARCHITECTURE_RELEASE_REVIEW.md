# Architecture release review

## Dependency graph and authority

The reviewed draft dependency chain is canonical -> #219 -> #220 -> #223 ->
#224 -> #225 -> issue #229. PR #228 is a separate canonical-based offline
business evaluation. PRs #221 and #222 concern external staging and are not
dependencies of this connector stack. All connector PRs remain draft and
unmerged. The integration maintainer must review each base/head and complete
diff before merging; a historical passing run is evidence only for its exact
head and tree.

## Release gates

The package boundary is wheel and source-archive ownership, embedded metadata,
clean installation, actual import behavior, registry behavior, and negative
authorization tests. No ERPNext network access or production signing is part
of this review. A newly registered connector has capability metadata, no
permission. Tenant authorization, bounded transport, source provenance,
revocation, and retained evidence must remain fail-closed.

Issue #229 closes the known public package-name collision by renaming the
distributions and requiring a reviewed offline hash manifest. It does not
create public namespace ownership. Release qualification still needs exact-head
full pytest on a stable Linux runner and independent security review. GitHub
Actions is disabled; it may be enabled only after a separate owner decision.
The verdict remains **BLOCKED** until those gates pass. Customer pilot and
production release are separate decisions and remain **NO_GO**.
