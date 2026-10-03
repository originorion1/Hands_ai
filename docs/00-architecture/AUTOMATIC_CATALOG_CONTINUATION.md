# Automatic catalog continuation

Issue #125. Metadata names only; no transaction sampling or scope promotion.
The initial permission-retry path counted names but did not retain them. This
continuation therefore starts catalog pagination again at the first name while
preserving the four spent GETs. It uses keyset pagination, 99 names per request,
at most four requests per invocation and the original cumulative 100 GET cap.

It opens the existing private metadata-preflight SQLite ledger, requires the
failed attempt-four company-discovered state for first activation, and stores
names, completion, scope binding, expiry and request claim in that same ledger.
No new/reset allowance. Exact-count full pages require another request to show
completion. Catalog changes during traversal are not snapshot-isolated: names
inserted before the cursor can be missed; completion means traversal exhaustion,
not proof of an immutable catalog. A future refresh needs separately scoped work.

Reservations commit before transport. A failed or interrupted in-flight request
stays claimed and needs investigation; reruns cannot silently reuse its budget.
Expired authority, wrong scope, malformed/repeated pages, competing admin-catalog
continuation or exhausted budget prevent further acquisition. Clock/database and
local configuration are trusted. Names remain private; console output is counts.
A resolved expiry argument narrows the caller's existing authorization, never
creates or extends it. Server read permissions remain required.

After independent review, from this feature checkout with the existing ORION
metadata environment and original state directory, invoke:

```bash
PYTHONPATH=src python -m orion.discovery.catalog_continuation \
  --execute --expires-at '<existing-approved-ISO8601-expiry-with-offset>'
```

Replace the placeholder with the actual already approved expiry; do not derive
permission from this documentation. Reuse the same command to continue a partial
catalog. Completed traversal makes no new request. Do not change state directories
or expiry to bypass a stopped run. Keep credentials out of command arguments.

This removes the manual catalog-export step. It does not classify business
fields, approve scopes, produce evidence records, start a soak or claim payroll
exclusion. Retained names are input to subsequent bounded metadata assessment,
not a ReviewedAdministratorCatalog. Existing human/independent review gates are
unchanged. Development/tests made no live customer request.
