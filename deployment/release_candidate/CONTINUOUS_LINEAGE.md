# Offline continuous synthetic lineage acceptance

This acceptance path uses the installed synthetic connector `0.2.0rc2`, the
existing one-use pilot read permit, Kernel evidence and investigation
contracts, and the `orion.readonly-report.v2` report projection. It never
opens an ERP connection, grants execution, or changes the public staging host.

1. Build the five packages from one exact source SHA/tree, retain both wheels
   and sdists, and install the five wheels plus hash-locked external dependencies
   into a clean Python 3.12 environment. Check `pip check` and the installed
   `synthetic-records` entry point. Keep the previous artifact set for rollback.
2. In that environment, from the source checkout, run
   `python -m deployment.release_candidate.lineage_preview_drill
   --private-dir /private/unique-synthetic-run --authorize-synthetic-read`.
   The command prints a loopback URL, run/dataset IDs and digests, but never
   prints credentials. It creates an ephemeral identity key and TLS certificate.
3. Check `/api/ready` returns 200 with `execution_allowed=false`; report GET
   without a token, with a forged token and for another tenant must return
   401; the issued token must return 200. The public JSON must match the
   `view` in the private `report.json` byte-for-byte under sorted compact JSON.
4. Open the printed loopback URL in Chrome. Run `tests/system/browser_smoke.ps1`
   against it, passing the private token through `-TokenFromStdin` and the
   printed run ID/source digest through `-ExpectedRunId` and
   `-ExpectedSourceSha256`. Confirm the browser shows those identities and the
   acquired observation count, then clears all report data after a forged
   token is submitted. The same UI and API route modules are used by Vercel;
   this loopback drill does not claim the protected public Preview served the
   new report.
5. Stop the drill once. Its ephemeral identity key, bearer token, TLS key and
   service token are removed. Retain `source.json`, `evidence.json`,
   `witness.json`, `report.json`, `binding.json`, test logs and artifact hashes
   in owner-only custody. The report host checks the three original private
   artifacts on each read and returns 503 if any digest or lineage link fails.

The safe report view includes a unique run ID, dataset ID, source digest,
authorization ID, admitted evidence IDs and investigation/witness digests.
The private witness also retains source records, upstream evidence/observation
IDs, the read scope digest, observed time and investigation references. The
read permit is issued only by the existing launcher after a separate exact
`PilotAuthorization` lookup. Connector registration provides no grant.

The protected Vercel Preview and its current healthy staging backend remain
untouched by this drill. Showing the new report there requires a separately
authorized deployment/cutover; a local browser pass cannot be labeled a
protected-Preview pass.
