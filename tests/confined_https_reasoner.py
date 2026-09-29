"""Fixed reasoning-side relay child; it never receives broker/source credentials."""

import os
import socket
import sys

if __name__ == "__main__":
    sys.path.insert(0, "/app/src")
    sys.path.insert(0, "/work")

from isolation_lab import CHECKS, connectable, inspect_child, readable

from orion.pilot.broker_contract import MAX_FRAME, decode, exact, observations_from
from orion.understanding.role_checkpoint import _json

EXTRA_CHECKS = (
    "broker_configuration_denied",
    "source_denied",
    "signed_route_denied",
    "tls_private_key_denied",
    "broker_environment_denied",
    "broker_direct_tcp_denied",
    "broker_variables_cleared",
)


def child():
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
        raise ValueError("reasoner bootstrap denied")
    scope = exact(
        decode(raw), ("message", "isolation", "denied_paths", "approved_port", "approved_host",
                      "expected")
    )
    denied = exact(scope["denied_paths"], ("configuration", "source", "route", "tls_key"))
    checks = inspect_child(scope["isolation"])
    checks.update(
        broker_configuration_denied=not readable(denied["configuration"]),
        source_denied=not readable(denied["source"]),
        signed_route_denied=not readable(denied["route"]),
        tls_private_key_denied=not readable(denied["tls_key"]),
        broker_environment_denied=not readable(f"/proc/{scope['isolation']['parent']}/environ"),
        broker_direct_tcp_denied=not connectable(
            socket.AF_INET6 if ":" in scope["approved_host"] else socket.AF_INET,
            (scope["approved_host"], scope["approved_port"])
        ),
        broker_variables_cleared=not any(name.startswith("BROKER_") for name in os.environ),
    )
    if set(checks) != set(CHECKS + EXTRA_CHECKS) or any(
        value is not True for value in checks.values()
    ):
        raise ValueError("reasoner custody denied")
    print(_json({"checks": checks, "request": scope["message"]}), flush=True)
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
        raise ValueError("broker response denied")
    response = decode(raw)
    if (
        type(response) is not dict
        or response.get("status") != "admitted"
        or response.get("execution_allowed") is not False
        or response.get("allow_live_customer_access") is not False
        or response.get("source_requests") != 1
    ):
        raise ValueError("admitted read response required")
    observations = observations_from(response.get("observations"))
    expected = exact(scope["expected"], ("tenant_id", "authorization_id", "count"))
    if (
        len(observations) != expected["count"]
        or any(item.evidence.tenant_id != expected["tenant_id"] for item in observations)
        or any(
            item.evidence.payload["provenance"]["authorization_id"] != expected["authorization_id"]
            for item in observations
        )
    ):
        raise ValueError("canonical observation scope changed")
    try:
        observations[0].evidence.payload["record"]["reasoner_mutation"] = True
    except TypeError:
        immutable = True
    else:
        immutable = False
    if not immutable:
        raise ValueError("canonical observation mutable")
    result = {
        "observation_ids": [str(item.observation_id) for item in observations],
        "evidence_ids": [item.evidence.evidence_id for item in observations],
        "authorization_id": expected["authorization_id"],
        "canonical_immutable": True,
        "execution_allowed": False,
    }
    print(_json({"reasoner_result": result}), flush=True)
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--child"]:
        raise SystemExit(2)
    try:
        raise SystemExit(child())
    except Exception:  # noqa: BLE001 - never disclose private paths or broker response data
        raise SystemExit(2) from None
