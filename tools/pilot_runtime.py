"""Single runtime acceptance entrypoint; synthetic verification is never activation."""

import json
import sys

from confined_https_broker_probe import _run_fixture
from kernel_destination_https_probe import REASONER_CHECKS

from orion.pilot.readiness import release_report

CUSTODY_CHECKS = {kind + "_separated" for kind in ("net", "user", "pid", "mnt")} | {
    "capabilities_dropped",
    "environment_cleared",
}
BROKER_CHECKS = (
    CUSTODY_CHECKS
    | {"configured_private_network", "firewall_mutation_denied"}
    | {
        name + "_inaccessible"
        for name in ("issuer_key", "audit_key", "source_secret", "source_data", "tls_key")
    }
    | {
        name + "_" + operation + "_denied"
        for name in ("history", "anchor")
        for operation in ("rewrite", "truncate", "unlink", "rollback")
    }
)


def valid_report(value):
    if (
        type(value) is not dict
        or value.get("status") not in ("PASS", "FAIL", "BLOCKED")
        or value.get("production_containment") != "NOT PROVEN"
        or any(
            value.get(k) is not False
            for k in ("live_ready", "execution_allowed", "allow_live_customer_access")
        )
    ):
        return False
    if value["status"] != "PASS":
        return True
    cases = value.get("cases")
    endings = {"stop", "revoke", "audit-loss", "auth-loss", "pending-stop"}
    if (
        type(cases) is not list
        or len(cases) != len(endings)
        or any(type(c) is not dict for c in cases)
        or {c.get("ending") for c in cases} != endings
    ):
        return False
    for case in cases:
        if (
            case.get("status") != "PASS"
            or case.get("live_ready") is not False
            or case.get("execution_allowed") is not False
            or case.get("source_outside_broker") is not True
            or type(case.get("ipv6_enabled")) is not bool
            or case.get("protocol")
            != ("local_columns_v1" if case.get("ending") == "revoke" else "local_rows_v1")
        ):
            return False
        checks = case.get("checks")
        expected = {"audit", "auth", "broker"} | (
            {"reasoner"} if case["ending"] in ("stop", "revoke") else set()
        )
        if case["ending"] == "stop":
            expected.add("emergency_stop")
        if case["ending"] in ("audit-loss", "auth-loss"):
            expected.add("restart_denial")
        if type(checks) is not dict or set(checks) != expected:
            return False
        if any(
            type(c) is not dict or not c or any(v is not True for v in c.values())
            for c in checks.values()
        ):
            return False
        names = {
            "audit": CUSTODY_CHECKS,
            "auth": CUSTODY_CHECKS,
            "broker": BROKER_CHECKS,
            "reasoner": REASONER_CHECKS,
            "emergency_stop": {"kernel_egress_removed", "broker_terminated"},
            "restart_denial": {"pending_custody_refuses_authority"},
        }
        if any(set(checks[name]) != names[name] for name in checks):
            return False
        reachable = case.get("reachable")
        kernel = case.get("kernel_denials")
        targets = {"ipv4-unapproved", "ipv4-alternate-port", "ipv4-proxy"}
        if case["ipv6_enabled"]:
            targets |= {
                "ipv6-unapproved",
                "ipv6-alternate-port",
                "ipv6-proxy",
                "ipv4-mapped-ipv6",
                "ipv4-other-family"
                if case["protocol"] == "local_columns_v1"
                else "ipv6-other-family",
            }
        if (
            type(reachable) is not dict
            or set(reachable) != targets | {"approved"}
            or reachable.get("approved") is not True
            or any(v is not True for v in reachable.values())
            or type(kernel) is not list
            or not kernel
            or any(type(k) is not dict or type(k.get("target")) is not str for k in kernel)
            or {k["target"] for k in kernel} != set(reachable) - {"approved"}
            or any(
                type(k.get("rejects")) is not int
                or k["rejects"] <= 0
                or k.get("counter")
                != "deny_"
                + (
                    "ipv4_unapproved"
                    if k["target"] == "ipv4-mapped-ipv6"
                    else k["target"].replace("-", "_")
                )
                for k in kernel
            )
        ):
            return False
        acquired = case["ending"] in ("stop", "revoke")
        if type(case.get("read_source_io")) is not int or case["read_source_io"] != int(acquired):
            return False
        budget = case.get("budget_after_restart")
        if (
            type(budget) is not dict
            or type(budget.get("attempts")) is not int
            or budget["attempts"] != 1
            or type(budget.get("reserved_bytes")) is not int
            or budget["reserved_bytes"] != 65536
        ):
            return False
        if not acquired and (
            budget.get("stopped") is not True
            if case["ending"] == "pending-stop"
            else budget.get("pending") is not True
        ):
            return False
        if acquired and (
            case.get("metadata_source_io") != 4
            or case.get("provenance_intact") is not True
            or case.get("interpretation") != "UNKNOWN"
            or case.get("reasoner", {}).get("canonical_immutable") is not True
            or case.get("budget_after_restart", {}).get("attempts") != 1
            or case.get("budget_after_restart", {}).get("reserved_bytes") != 65536
        ):
            return False
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--verify-synthetic"]:
        return _run_fixture("pilot_runtime_deployment.py", valid_report, timeout=180)
    if argv not in ([], ["--health"], ["--start"]):
        return 2
    report = release_report()
    report.update(
        status="startup_denied" if argv == ["--start"] else "blocked",
        runtime="supervised_metadata_first_read_only",
        custody_available=False,
        deployed=False,
        LIVE_PILOT_READY=False,
    )
    print(json.dumps(report, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
