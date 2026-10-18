# Customer portal integration boundary

The customer portal can serve a persisted, evidence-validated report through
`/customer/api/report`. The application operator must inject a
`CustomerReportBinding` selected by the authenticated tenant. Its `commit_scope`
must serialize report publication with grant revocation; `is_active` is checked
before and after the source recomputes its custody-validated view. The portal
session commit lock independently serializes logout, tenant switching and
membership removal. No browser parameter selects a report or grant.

`ReviewedOIDCTokenExchange` performs an authorization-code token POST with PKCE
to an exact reviewed HTTPS endpoint. The caller pins issuer, client ID,
callback and ID-token verification keys; a confidential client secret is
resolved only from a protected `vault:` reference. This source tree includes
synthetic provider tests, but no customer IdP configuration or live login.

Customer authorization proposals remain review-only. `approval_packet`
checks the proposed tenant, company, DocType/field allowlist, 25-attempt ceiling,
30-minute duration and evidence volume against a `MetadataGrant`. An independent
owner signature over that exact packet is required before
`ApprovedMetadataGrantLedger` can activate it. The private ledger persists
revocation; its `lookup` and `commit_scope` feed the bounded metadata runner.
The connector transport is still injected and must be independently confined
to the reviewed destination. No customer ERP access is configured here.

The browser reads the exact persisted report, labels observed facts, inferred
findings, estimated exposure and independently verified savings separately,
and removes all scoped data after denial or interruption. The USD 60 finding
in automated tests is designed synthetic evidence, with no verified savings.

Open customer-release gates: reviewed real IdP and vault configuration,
customer-approved company/destination/field mapping and credential references,
fresh owner and customer authorization, qualified confined transport, stable
Linux exact-head suite, independent security review and separate deployment
approval. No ERP read, package publication or production activation is implied
by the synthetic tests.
