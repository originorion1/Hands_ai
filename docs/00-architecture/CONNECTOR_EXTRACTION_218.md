# Connector extraction inventory and sequence (issue #218)

Baseline: `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`, tree
`1cfb49df43247ddc94ca9bed5f876cb3b52cbfe1`.

## Dependency inventory

The root `orion-core` wheel declares `cryptography>=43.0` and packages the entire
`src/orion` and `src/orion_http` trees. At baseline, it therefore owns 22
ERPNext-named Python modules: 21 in `orion.discovery` and
`orion.pilot.erpnext_candidate`. The separate `orion/pyproject.toml` is a
laboratory skeleton and does not build the runtime wheel. The Vercel deployment
builds a static frontend; the authenticated report host is a separate persistent
runtime.

`orion.kernel.OrionKernel` imports only the `DiscoveryAdapter` and
`EvidenceStore` ports and neutral contracts. That source boundary does not make
the installed distribution independent: `orion.demo` imports the ERPNext
discovery adapter, and pilot modules contain ERPNext imports, fixed protocol
names, or branches. The relevant pilot owners include `broker`,
`broker_contract`, `broker_worker`, `broker_metadata`, `custody`, `gateway`,
`deployment`, `deployment_profile`, `evidence_custody`, `production`, and
`readiness`. `discovery.retained_investigation_session` also imports several
ERPNext discovery helpers despite its neutral filename. No entry-point or
installed connector registry exists at this baseline.

The first extraction slice makes `orion-connector-erpnext-discovery` the sole
distribution owner of `orion.discovery.erpnext_adapter` in built wheels and
source archives while retaining its historical
import path and behavior. The source tree retains that path for existing source
tests. The connector build hook includes that one source file in both its wheel
and source archive; a wheel rebuilt from the source archive must contain the
same bytes. The core source archive excludes the adapter. Core-only installed startup uses shared HTTPS-origin and credential
reference helpers without importing the ERPNext adapter. The installed-runtime
suite checks this core-only state, then installs the connector wheel for the
historical ERPNext scenarios. This is a packaging boundary only; it does not
authorize customer reads or change the broker's fixed protocol rules.

## Migration sequence

1. Prove wheel ownership and the legacy adapter's unchanged fail-closed behavior
   with isolated installs and source tests (this slice).
2. Move vendor-neutral connector contracts to their own distribution, make the
   Kernel depend on it, and define a fixed, reviewed registration interface.
3. Move all ERPNext discovery and pilot implementations to the connector wheel.
   Replace generic pilot ERPNext branches and imports with authenticated,
   explicitly registered capabilities. Preserve existing protocol and evidence
   representations during the compatibility window.
4. Verify a Kernel-only install contains no ERPNext modules, runs connector-free,
   and accepts an independently installed second synthetic connector without a
   Kernel source edit. Run tenant, provenance, revocation, and denial regression
   suites on the exact integrated candidate before human review.

Until steps 2–4 pass, the complete physical Kernel/connector separation remains
open. A green build of this first slice alone cannot establish that property.

## Stacked source and artifact ownership candidate

The next draft slice moves all 21 `erpnext_*.py` discovery implementations,
the ERPNext candidate transport, retained investigation composition, legacy
aggregate report projection, and its report-host entry point to
`packages/erpnext-discovery/src`. The connector wheel and source archive own
these 25 modules. The Kernel wheel and source archive exclude them; its
metadata no longer advertises an ERPNext optional dependency. The historical
module paths remain available only after installing the connector package.
The source test layout extends the package paths to exercise the same legacy
imports without copying implementation files back into Kernel source.

The frozen learning evaluation pins the historical source path and its exact
SHA-256. A source-only symlink at that path resolves to the connector-owned
unchanged file. The Kernel wheel and source archive exclude the symlink and
file, so an installed Kernel has no ERPNext implementation. This preserves the
frozen source identity without modifying the evaluation protocol.

The generic pilot source still has ERPNext-specific branches in its broker,
gateway, deployment, custody, readiness, and policy modules. This artifact
boundary does not complete reviewed capability dispatch or process isolation.
The ERPNext connector package name also retains `discovery` for compatibility
with the earlier stack until a separate reviewed rename is qualified.

## Historical pilot compatibility distribution

`orion-erpnext-pilot-compat` owns the historical supervised pilot modules and
`orion.demo` under their old import paths, as well as the `orion-runtime`
console script. The Kernel distribution contains none of those modules or the
script. The compatibility package depends explicitly on the independently
installable ERPNext connector and the Kernel; its installed RECORD is checked
as the authority for historical pilot startup. Existing grants bound to a
previous Kernel artifact remain invalid for this new distribution and must be
reviewed afresh before any activation.

This keeps the old fail-closed ERPNext pilot readable and testable without
making its vendor-specific branches part of the Kernel. The Kernel-only
installation starts through `OrionKernel` and its neutral ports. The SDK's
registration record grants no runtime authority, and the synthetic connector
can supply tenant-bound observations through those ports without Kernel source
changes. A universal governed pilot dispatcher for future external connectors
is a separate implementation gate; the historical pilot compatibility package
must not be mistaken for that dispatcher or used to infer live readiness.
