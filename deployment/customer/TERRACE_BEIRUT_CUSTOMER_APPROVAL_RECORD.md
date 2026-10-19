# Customer administrator approval record — metadata-only pilot

**Status: TEMPLATE — unsigned; grants no access.** Complete through the
customer's approved authenticated channel. Do not put credentials, row values,
or unrelated customer identifiers in this record. Preserve the signed mapping
receipt privately and record only its digest here.

## Customer decisions

- Legal entity and exact ERP company key: pending customer-admin verification
- Canonical tenant membership mapping: pending owner verification
- Approved DocTypes: proposed `Purchase Order` and `Purchase Order Item`; both
  require customer confirmation
- Approved metadata schema fields per DocType: `fieldname`, `fieldtype`,
  `options`, `reqd`, `permlevel`; confirm or narrow each list
- Confirm metadata-schema-only purpose; no records, transaction history, writes,
  attachments, comments, free text, personnel, or payment credentials: pending
- Confirm destination hostname and TLS identity: pending independent check
- Confirm one UTC session window, no longer than 30 minutes: pending
- Confirm maximum 25 attempted requests, 64 KiB per response, and 256 KiB total
  projected evidence: pending
- Confirm proposed 1-day retention and named deletion operator: pending
- Customer emergency-stop contact and tested notification route: pending

## Decision

- Decision: **PENDING**
- Customer administrator name and role: pending
- Signed at (UTC): pending
- Signature over canonical packet digest: pending
- Company-mapping attestation digest: pending

This record is not an owner grant. A separate owner-signed one-use runtime
grant is required after company mapping, installed-runtime qualification, and
security review pass.
