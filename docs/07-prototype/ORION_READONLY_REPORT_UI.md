# ORION read-only report interface

## Scope and provenance

This local interface presents one completed, aggregate live-session report. It
has no ERP client, execution route, mutation route, recommendation route, or
authority transition.

The visual frontend is carried forward from the reviewed local command-center
source at commit `8f0b8f21b7b83c1d8aebf962fbd35ad92aa90ea4` (tree
`c2a61e81c295803514c93978d7d443c8ad81696e`). The tracked version replaces the
old browser file-import parser with a same-origin, versioned projection owned
by `orion.presentation.report_interface`. Canonical report validation remains
owned by `orion.discovery.erpnext_live_session`.

## Private binding

The server accepts one owner-only (`0600`) JSON binding by absolute path:

```json
{
  "binding_version": "orion.readonly-report.v1",
  "report_path": "/absolute/private/path/report.json",
  "report_sha256": "<lowercase SHA-256>",
  "tenant_id": "<private tenant reference>"
}
```

The binding and report must be regular, singly linked files owned by the
current operating-system user. Symbolic-link traversal, an unexpected schema,
an unsafe report, and a digest mismatch all fail closed. Tenant identity and
filesystem paths are used only for the local binding and are not included in
the browser response.

Run the installed entry point:

```text
orion-report-ui --binding /absolute/private/path/binding.json --port 8765
```

The listener binds only to `127.0.0.1`. Open `http://127.0.0.1:8765/`, then
select **Connect report**. The only data route is `GET /api/v1/report`; static
files are served from a fixed allowlist. There is no fallback dataset.

## Verification boundary

Python tests exercise private-file, digest, schema, authority, loopback HTTP,
host, cross-site, method, and safe-error boundaries. The Node DOM test covers
connection success, failure without fallback, unsafe-view rejection, and the
existing local interaction states. A real browser engine was not installed in
the verification environment, so rendered layout, browser accessibility-tree
behavior, speech permissions, and engine-specific Content Security Policy
behavior still require an independent browser pass before pilot use.
