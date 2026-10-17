"""An incomplete local request cannot monopolize the single report worker."""

import http.client
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

from orion_http.report_server import AuthenticatedReportServer


class SyntheticHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


def test_idle_client_times_out_and_next_request_recovers():
    server = AuthenticatedReportServer(("127.0.0.1", 0), SyntheticHandler)
    assert server.request_queue_size == 16
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    idle = socket.create_connection(server.server_address, timeout=2)
    try:
        idle.sendall(b"GET / HTTP/1.1\r\nHost: ")
        started = time.monotonic()
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
        try:
            client.request("GET", "/")
            response = client.getresponse()
            assert response.status == 200
            assert response.read() == b"ok"
        finally:
            client.close()
        assert time.monotonic() - started < 3.5
    finally:
        idle.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_trickling_client_cannot_extend_absolute_connection_deadline():
    server = AuthenticatedReportServer(("127.0.0.1", 0), SyntheticHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    trickle = socket.create_connection(server.server_address, timeout=2)
    trickle.sendall(b"GET / HTTP/1.1\r\nHost: ")

    def drip():
        for _ in range(12):
            time.sleep(0.5)
            try:
                trickle.sendall(b"x")
            except OSError:
                return

    sender = threading.Thread(target=drip)
    sender.start()
    try:
        started = time.monotonic()
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=7)
        try:
            client.request("GET", "/")
            response = client.getresponse()
            assert response.status == 200
            assert response.read() == b"ok"
        finally:
            client.close()
        assert time.monotonic() - started < 6.5
    finally:
        trickle.close()
        sender.join(timeout=2)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()
