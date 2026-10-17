"""Render and validate the versioned, fail-closed release health snapshot."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DIMENSIONS = (
    "BUILD_HEALTH", "PACKAGE_HEALTH", "TEST_HEALTH", "SECURITY_HEALTH",
    "RUNTIME_HEALTH", "INTELLIGENCE_EVALUATION", "CUSTOMER_PILOT_READINESS",
    "PRODUCTION_RELEASE_READINESS",
)
BLOCKER_FIELDS = frozenset({
    "id", "priority", "status", "component", "root_cause", "evidence",
    "remediation", "owner", "verification",
})


def validate(record: dict) -> None:
    if record.get("schema") != "orion.release-health.v1":
        raise ValueError("unsupported release health schema")
    if any(not isinstance(record.get(field), str)
           or not re.fullmatch(r"[0-9a-f]{40}", record[field])
           for field in ("candidate_source_sha", "candidate_tree", "canonical_source_sha")):
        raise ValueError("missing exact source identity")
    dimensions = record.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
        raise ValueError("missing health dimension")
    for name, item in dimensions.items():
        if (not isinstance(item, dict) or item.get("status") not in
                {"PASS", "PARTIAL", "BLOCKED", "NOT_PROVEN", "NO_GO"}
                or not isinstance(item.get("evidence"), str)
                or not item["evidence"].strip()):
            raise ValueError(f"invalid health evidence: {name}")
    blockers = record.get("blockers")
    if not isinstance(blockers, list) or not blockers:
        raise ValueError("missing blocker register")
    ids = set()
    for blocker in blockers:
        if (not isinstance(blocker, dict) or set(blocker) != BLOCKER_FIELDS
                or any(not isinstance(value, str) or not value.strip()
                       for value in blocker.values())
                or blocker["priority"] not in {"P0", "P1", "P2", "P3"}
                or blocker["status"] not in {"OPEN", "RESOLVED"}
                or blocker["id"] in ids):
            raise ValueError("invalid or duplicate blocker")
        ids.add(blocker["id"])
    if record.get("execution_allowed") is not False or record.get("production_signing") is not False:
        raise ValueError("release health cannot grant execution")
    if record.get("customer_erp_access") is not False:
        raise ValueError("release health cannot grant customer access")
    if record.get("release_decision") == "RELEASE_CANDIDATE_QUALIFIED":
        if (any(blocker["priority"] in {"P0", "P1"} and blocker["status"] == "OPEN"
                for blocker in blockers)
                or any(dimensions[name]["status"] != "PASS"
                       for name in ("BUILD_HEALTH", "PACKAGE_HEALTH", "TEST_HEALTH",
                                    "SECURITY_HEALTH", "RUNTIME_HEALTH"))):
            raise ValueError("release decision contradicts open engineering gate")
    elif record.get("release_decision") != "RELEASE_CANDIDATE_BLOCKED":
        raise ValueError("unsupported release decision")


def render(record: dict) -> str:
    validate(record)
    lines = [
        "# ORION release health and blocker register",
        "",
        f"Snapshot: `{record['observed_utc']}`. Candidate source:",
        f"`{record['candidate_source_sha']}`; tree `{record['candidate_tree']}`.",
        f"Canonical: `{record['canonical_source_sha']}`. Decision:",
        f"`{record['release_decision']}`.",
        "",
        "This snapshot is evidence about draft branches, not a release approval.",
        "",
        "| Health dimension | Status | Evidence |",
        "|---|---|---|",
    ]
    for name in DIMENSIONS:
        item = record["dimensions"][name]
        lines.append(f"| {name} | {item['status']} | {item['evidence']} |")
    lines += ["", "| Blocker | Priority | Status | Component | Root cause and evidence | Remediation / owner | Verification |",
              "|---|---|---|---|---|---|---|"]
    for item in record["blockers"]:
        lines.append(
            f"| {item['id']} | {item['priority']} | {item['status']} | {item['component']} | "
            f"{item['root_cause']} {item['evidence']} | {item['remediation']} "
            f"Owner: {item['owner']}. | {item['verification']} |"
        )
    lines += ["", "All service identities and test results must be refreshed on the final",
              "integrated SHA and tree. `execution_allowed=false` remains enforced.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    output = render(json.loads(args.source.read_text()))
    if args.check:
        if args.check.read_text() != output:
            raise SystemExit("release health Markdown is stale")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
