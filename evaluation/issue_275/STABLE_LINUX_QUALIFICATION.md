# Stable Linux exact-head gate

Issue #275 requires a clean Ubuntu 24.04 Python 3.12 full pytest run with all tests enabled. The current workstation is Ubuntu user space on a Microsoft WSL2 kernel. Its Docker daemon uses the same kernel. Neither environment is stable Linux qualification for this mission. GitHub Actions is disabled and is not authorized to be enabled. No other authorized stable Linux host was available at packet preparation.

When an owner supplies an already authorized stable host, check out the exact committed issue #275 head in a clean worktree, record SHA and tree, build and hash all five wheels and sdists, install the artifacts in an isolated environment, and run package closure, Ruff, `py_compile`, full `pytest`, Node/browser checks, recovery, `execution_allowed=false` demo, diff check and capability/evidence scans. Preserve full logs, environment identity, wheel/sdist hashes, test exit codes and browser source/run digests. A changed tree requires rerunning the gate. Keep GitHub Actions disabled unless separately authorized, and restore its prior state if authorization is later granted for a temporary run.

Local WSL2 tests and Docker tests may be diagnostic evidence, but cannot set `ENGINEERING_QUALIFICATION=PASS`. This document does not authorize a host purchase, Actions change or deployment.
