"""Serve a synthetic, isolated ORION browser fixture with a private test token.

For manual browser verification only. The fixture has no ERP connector, customer
identifier, production token, or live evidence. Stop it after testing.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from test_authenticated_report_server import _fixture, _tls_certificate, _token

from deployment.local_preview import create_server as create_application_server
from deployment.vercel.build_static import main as build_static
from orion.presentation.authenticated_report_server import create_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated ORION browser fixture")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="orion-synthetic-browser-") as temporary:
        root = Path(temporary)
        source, policy, signing_key, _ = _fixture(root)
        cert_path, key_path = _tls_certificate(root)
        token_path = root / "access-token"
        token_path.write_text(_token(signing_key), encoding="ascii")
        token_path.chmod(0o600)
        build_static()
        host = create_server(source, policy, port=0)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))
        host.socket = context.wrap_socket(host.socket, server_side=True)
        os.environ["SSL_CERT_FILE"] = str(cert_path)
        os.environ["ORION_REPORT_BACKEND_URL"] = (
            f"https://localhost:{host.server_port}/api/v1/report"
        )
        app = create_application_server(port=args.port)
        host_thread = threading.Thread(target=host.serve_forever, daemon=True)
        host_thread.start()
        print(json.dumps({
            "url": f"http://localhost:{app.server_port}/",
            "token_file": str(token_path),
            "source": "synthetic_offline_fixture",
        }), flush=True)
        try:
            app.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.server_close()
            host.shutdown()
            host.server_close()
            host_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
