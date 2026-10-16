# Connector registration contract, version 1

`orion-connector-sdk` is a separate distribution. It has no Kernel or vendor
dependency. The Kernel distribution depends on this contract package; the
ERPNext discovery and synthetic connector distributions depend on both.

An installed connector advertises an `orion.connectors.v1` entry point.
Registration requires an independently reviewed exact name, normalized
distribution name, distribution version, entry point value, and contract
version. `discover()` reads package metadata and returns an inert registration
record. It never calls `EntryPoint.load()`, imports connector code, supplies
credentials, selects a tenant, opens a network connection, or grants execution.
An absent, duplicated, changed, or incompatible entry point is rejected.
Authorization and process isolation remain separate runtime requirements.

The synthetic connector is an independently installable second distribution.
It implements the existing Kernel discovery port without changing Kernel
source. The Kernel validates its complete observation batch before writing
evidence, so a wrong-tenant observation leaves the evidence store unchanged.

The ERPNext discovery connector continues to own the historical
`orion.discovery.erpnext_adapter` import path in installed environments. This
is the existing first-slice compatibility arrangement. Its other source
modules and generic pilot branches still require extraction before physical
Kernel/connector independence can be claimed.
