# Kernel and connector independence proof

Candidate stack: canonical `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd` ->
draft PRs #219, #220, #223, #224, #225 -> issue #229 package-identity branch.
The final exact head, tree, artifact hashes, and hosted qualification belong
in the PR record once this branch is frozen.

| Property | Artifact or test boundary |
|---|---|
| Kernel wheel contains no ERPNext implementation | Inspect `originorion_core` wheel members and imports; `tests/test_kernel_connector_boundary.py` scans source imports. |
| Kernel has no ERPNext package dependency | Inspect wheel `METADATA`; only `cryptography` and vendor-neutral SDK are runtime dependencies. |
| Kernel starts connector-free | Install only Kernel, SDK, and their external wheels in a clean venv; import `orion.kernel`. |
| ERPNext is independently installable/removable | Install connector wheel separately, then remove connector and pilot compatibility wheels; Kernel import and `pip check` must remain healthy. |
| Historical pilot is separately packaged | `originorion-erpnext-pilot-compat` owns `orion.pilot` and verifies four installed RECORDs. |
| Second connector needs no Kernel source edit | `originorion-connector-synthetic` registers through versioned entry points; registry tests verify discovery. |
| Registration grants no authority | `tests/test_connector_registry.py` asserts metadata-only registration and `execution_allowed=False`. |
| Connector failure remains fail-closed | Existing authorization, tenant and adapter failure regression tests must pass on exact head. |
| Evidence provenance survives extraction | Historical evidence and installed pilot tests must pass with connector and compatibility wheels installed. |
| Historical imports remain available | ERPNext and pilot modules are importable only after their independent wheels are installed. |

The Kernel source archive may include historical test files and prose. It must
contain no ERPNext implementation modules. The frozen source-only metadata
symlink is excluded from both Kernel artifacts; its target belongs to the
connector source. Process isolation and customer access remain separate
authorization decisions. Green package tests do not grant execution.
