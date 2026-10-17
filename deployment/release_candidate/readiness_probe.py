"""Local report-host monitor with a safe nonzero alert exit on denial."""

from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path

from orion_http.report_server import _read_service_token


def probe(*, port: int, service_token_file: Path) -> bool:
    if type(port) is not int or not 0 < port <= 65535:
        return False
    try:
        token = _read_service_token(service_token_file)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", "/ready", headers={"X-ORION-Service-Token": token})
            response = connection.getresponse()
            body = response.read(257)
        finally:
            connection.close()
        return (
            response.status == 200
            and len(body) <= 256
            and json.loads(body) == {"ready": True, "execution_allowed": False}
        )
    except (OSError, ValueError, http.client.HTTPException):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--service-token-file", type=Path, required=True)
    args = parser.parse_args()
    ready = probe(port=args.port, service_token_file=args.service_token_file)
    print(json.dumps({"report_ready": ready, "execution_allowed": False}, sort_keys=True))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
