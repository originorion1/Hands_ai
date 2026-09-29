# Reviewed historical read window

Issue #123 adds optional restrictions to ERPNextHistoricalSampleAdapter.
ReviewedReadWindow binds exact tenant, company, resource, immutable fields,
a source Date field, inclusive dates and timezone-aware expiry. No wildcards.
It narrows an existing authorization; constructing it never grants permission.

With read_window supplied, discover checks expiry/scope before acquisition,
adds date filters to the existing bounded GET, validates every returned date,
and checks expiry again before returning observations. Existing company,
submitted-status, fields, size and redirect protections remain in force.
Out-of-window rows fail the entire batch; no partial observations are returned.
The clock and local process are trusted. Expiry during an in-flight request
cannot cancel that request; its results are rejected. Server-side filtering is
requested, not trusted. Rejected data may have reached transient memory, but
this adapter does not persist it.

The allowlist must first be checked against metadata and actual source semantics
for excluded data, including payroll-derived fields in other datasets. Name
matching does not establish semantic exclusion. This change provides no catalog
scanner, semantic payroll classifier, authorization grant or live launcher.
The parameter defaults to None for existing callers; they do not gain these
restrictions automatically. The customer pilot must not use those callers until
reviewed scope binding and launcher propagation are separately verified.

Source Date fields only: timestamps, undated masters and records requiring
other business-date semantics are unsupported by this window. Do not silently
substitute creation time. Historical scope is not universal all-dataset support.
No customer-specific URL, identity, credential or scope is committed here.

Verification uses fake responses: inclusive boundaries, ignored server filters,
malformed dates, scope mismatch before requests and expiry before/after reads.
Independent review is required before live use. No business reads were executed.
