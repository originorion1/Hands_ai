"""Run the fixed private-net HTTPS broker laboratory; never enables egress."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def valid_report(value):
    if (
        type(value) is not dict
        or value.get("status") not in ("PASS", "FAIL", "BLOCKED")
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
    if (
        type(cases) is not list
        or len(cases) != 3
        or value.get("negative_controls") is not True
        or {(case.get("protocol"), case.get("behavior"), case.get("durable_control"))
            for case in cases if type(case) is dict}
        != {
            ("local_rows_v1", "ok", "revoke"),
            ("local_columns_v1", "ok", "stop"),
            ("local_rows_v1", "redirect", "revoke"),
        }
    ):
        return False
    for case in cases:
        budget = case.get("budget_after_restart")
        if (
            case.get("status") != "PASS"
            or case.get("owner_negative_controls") is not True
            or case.get("pre_io_denials") != 10
            or case.get("attempts_before_acquisition") != 0
            or case.get("source_requests_before_acquisition") != 0
            or case.get("source_requests") != 1
            or case.get("restart_source_requests") != 0
            or case.get("execution_allowed") is not False
            or case.get("live_ready") is not False
            or type(case.get("broker_checks")) is not dict
            or not case["broker_checks"]
            or not all(check is True for check in case["broker_checks"].values())
            or type(budget) is not dict
            or budget.get("attempts") != 1
            or type(budget.get("failures")) is not int
            or type(budget.get("reserved_bytes")) is not int
            or budget["reserved_bytes"] <= 0
        ):
            return False
        if case["behavior"] == "ok" and (
            case.get("observations") != 1
            or case.get("provenance_intact") is not True
            or case.get("reasoner_credentials_inaccessible") is not True
            or type(case.get("reasoner_checks")) is not dict
            or not case["reasoner_checks"]
            or not all(check is True for check in case["reasoner_checks"].values())
            or case.get("reasoner_result", {}).get("canonical_immutable") is not True
        ):
            return False
        if case["behavior"] == "redirect" and (
            case.get("observations") != 0 or budget["failures"] != 1
        ):
            return False
    return True


def run():
    report = {
        "status": "BLOCKED",
        "reason": "confined_https_probe_unavailable",
        "execution_allowed": False,
        "allow_live_customer_access": False,
        "live_ready": False,
        "production_containment": "NOT PROVEN",
    }
    process = None
    try:
        fixture = Path(__file__).resolve().parents[1] / "tests" / "confined_https_broker_lab.py"
        process = subprocess.Popen(
            [sys.executable, "-I", str(fixture)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            close_fds=True,
            start_new_session=True,
            cwd=fixture.parents[1],
            env={"PATH": os.defpath},
        )
        stdout, stderr = process.communicate(timeout=90)
        if process.returncode or stderr or len(stdout) > 65536:
            raise ValueError("invalid probe output")
        value = json.loads(stdout)
        if not valid_report(value):
            raise ValueError("invalid probe report")
        report = value
    except (OSError, TypeError, ValueError, subprocess.TimeoutExpired):
        pass
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit(2)
    raise SystemExit(run())
