"""Fixed kernel destination proof. No endpoint, route, proxy or activation arguments."""

import sys

from confined_https_broker_probe import _run_fixture

BROKER_CHECKS = {
    "secret_denied",
    "audit_write_denied",
    "tcp_denied",
    "unix_denied",
    "parent_environment_denied",
    "environment_cleared",
    "descriptors_closed",
    "network_namespace_changed",
    "pid_namespace_changed",
    "mount_namespace_changed",
    "user_namespace_changed",
    "capabilities_dropped",
    "unprivileged_uid",
    "configuration_readonly",
    "route_readonly",
    "certificate_readonly",
    "source_file_inaccessible",
    "source_private_key_inaccessible",
    "source_namespace_distinct",
    "configured_network_retained",
    "firewall_mutation_denied",
}
REASONER_CHECKS = (
    BROKER_CHECKS
    - {
        "configuration_readonly",
        "route_readonly",
        "certificate_readonly",
        "source_file_inaccessible",
        "source_private_key_inaccessible",
        "source_namespace_distinct",
        "configured_network_retained",
        "firewall_mutation_denied",
    }
) | {
    "broker_configuration_denied",
    "source_denied",
    "signed_route_denied",
    "tls_private_key_denied",
    "broker_environment_denied",
    "broker_direct_tcp_denied",
    "broker_variables_cleared",
}


def true_checks(value, expected):
    return type(value) is dict and set(value) == expected and all(v is True for v in value.values())


def valid_case(case):
    if (
        type(case) is not dict
        or type(case.get("ipv6_enabled")) is not bool
        or case.get("protocol") not in ("local_rows_v1", "local_columns_v1")
        or case.get("behavior") not in ("ok", "redirect")
        or case.get("durable_control") not in ("stop", "revoke")
    ):
        return False
    ipv6 = case["ipv6_enabled"]
    host = (
        "fd42:6f72:696f::2" if ipv6 and case.get("protocol") == "local_columns_v1" else "192.0.2.2"
    )
    names = {"ipv4-unapproved", "ipv4-alternate-port", "ipv4-proxy"}
    controls = {"approved-ipv4"}
    if ipv6:
        names |= {"ipv6-unapproved", "ipv6-alternate-port", "ipv6-proxy", "ipv4-mapped-ipv6"}
        names.add("ipv4-other-family" if ":" in host else "ipv6-other-family")
        controls.add("approved-ipv6")
    if (
        case.get("status") != "PASS"
        or case.get("host") != host
        or case.get("execution_allowed") is not False
        or case.get("live_ready") is not False
        or not true_checks(case.get("broker_checks"), BROKER_CHECKS)
        or not true_checks(case.get("reachable_controls"), controls | names)
        or any(
            case.get(name) is not True
            for name in (
                "source_outside_broker",
                "budget_restart_exhaustion_denied",
                "provenance_intact",
            )
        )
    ):
        return False
    for name, expected in (
        ("pre_io_denials", 10),
        ("source_requests_before_acquisition", 0),
        ("source_requests", 1),
        ("restart_source_requests", 0),
        ("unapproved_connections_after_controls", 0),
        ("unauthorized_approved_packets", 0),
        ("restart_approved_packets", 0),
    ):
        if type(case.get(name)) is not int or case[name] != expected:
            return False
    if (
        type(case.get("approved_acquisition_packets")) is not int
        or case["approved_acquisition_packets"] <= 0
    ):
        return False
    runs = case.get("kernel_direct_denials")
    if type(runs) is not list or len(runs) != 3:
        return False
    for run in runs:
        if (
            type(run) is not list
            or len(run) != len(names)
            or any(type(item) is not dict or type(item.get("direct")) is not str for item in run)
            or {item.get("direct") for item in run} != names
        ):
            return False
        for item in run:
            counter = "deny_" + (
                "ipv4_unapproved"
                if item["direct"] == "ipv4-mapped-ipv6"
                else item["direct"].replace("-", "_")
            )
            if (
                set(item) != {"direct", "connected", "errno", "counter", "kernel_rejections"}
                or item["connected"] is not False
                or item["counter"] != counter
                or type(item["kernel_rejections"]) is not int
                or item["kernel_rejections"] <= 0
                or (
                    item["errno"] is not None
                    and (type(item["errno"]) is not int or item["errno"] <= 0)
                )
            ):
                return False
    budget = case.get("budget_after_restart")
    redirect = case.get("behavior") == "redirect"
    if (
        type(budget) is not dict
        or set(budget) != {"attempts", "failures", "reserved_bytes"}
        or any(type(v) is not int for v in budget.values())
        or budget != {"attempts": 1, "failures": int(redirect), "reserved_bytes": 65536}
        or type(case.get("observations")) is not int
        or case["observations"] != (0 if redirect else 1)
    ):
        return False
    return not (
        not redirect
        and (
            not true_checks(case.get("reasoner_checks"), REASONER_CHECKS)
            or type(case.get("reasoner_result")) is not dict
            or case["reasoner_result"].get("canonical_immutable") is not True
            or case["reasoner_result"].get("execution_allowed") is not False
        )
    )


def valid_report(value):
    if (
        type(value) is not dict
        or value.get("status") not in ("PASS", "BLOCKED", "FAIL")
        or value.get("production_containment") != "NOT PROVEN"
        or any(
            value.get(name) is not False
            for name in ("execution_allowed", "allow_live_customer_access", "live_ready")
        )
    ):
        return False
    if value["status"] != "PASS":
        return True
    cases = value.get("cases")
    return (
        value.get("negative_controls") is True
        and value.get("rootless_setup") is True
        and type(cases) is list
        and len(cases) == 4
        and all(valid_case(case) for case in cases)
        and {(case["protocol"], case["behavior"], case["durable_control"]) for case in cases}
        == {
            ("local_rows_v1", "ok", "revoke"),
            ("local_columns_v1", "ok", "stop"),
            ("local_rows_v1", "redirect", "revoke"),
            ("local_columns_v1", "redirect", "stop"),
        }
        and len({case["ipv6_enabled"] for case in cases}) == 1
    )


def run():
    return _run_fixture("kernel_destination_https_lab.py", valid_report, timeout=180)


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit(2)
    raise SystemExit(run())
