"""Regression tests for multi-day receiver stalls and bounded TLS workers."""

from http.server import BaseHTTPRequestHandler
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.market_intelligence.market_fact_receiver import (
    connect_receiver, initialize_receiver,
)
from core.market_intelligence.private_tls_server import PrivateTLSServer


class ReceiverInitializationTests(unittest.TestCase):
    def test_reopening_permanent_ledger_does_not_recount_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receiver.sqlite3"
            connection = connect_receiver(path)
            traced = []
            connection.set_trace_callback(traced.append)
            initialize_receiver(connection)
            self.assertFalse(any("COUNT(*)" in query.upper() for query in traced))
            connection.close()
            reopened = connect_receiver(path, initialize=False)
            self.assertEqual(reopened.execute(
                "SELECT delivery_count,fact_count FROM receiver_status_counts"
            ).fetchone()[:], (0, 0))
            reopened.close()

    def test_snapshot_http_health_identifies_its_schema(self):
        from core.market_intelligence import estimator_snapshot_receiver_service as service
        handler = object.__new__(service._Handler)
        handler.server = SimpleNamespace(
            allowed_peer_ip="10.0.0.1", database_path=Path("unused.sqlite"),
            expected_lane="PRIVATE_PRIMARY", stale_after_seconds=30,
            snapshot_root=Path("unused-snapshots"),
        )
        handler.client_address = ("127.0.0.1", 10000)
        handler.path = service.HEALTH_PATH
        handler._respond = MagicMock()
        with patch.object(service, "connect_snapshot_receiver") as connect, \
                patch.object(service, "snapshot_receiver_metrics", return_value={"snapshot_readiness": "READY"}):
            handler.do_GET()
        status, document = handler._respond.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(document["schema"], service.SNAPSHOT_RECEIVER_SCHEMA)
        connect.return_value.close.assert_called_once()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        return


class TLSIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(cls.root / "key.pem"), "-out", str(cls.root / "cert.pem"),
            "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost",
        ], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(self.root / "cert.pem"), str(self.root / "key.pem"))
        tls.verify_mode = ssl.CERT_REQUIRED
        tls.load_verify_locations(str(self.root / "cert.pem"))
        self.server = PrivateTLSServer(
            ("127.0.0.1", 0), _HealthHandler, tls_context=tls,
            request_timeout_seconds=0.4, max_workers=3,
        )
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.client = ssl.create_default_context(cafile=str(self.root / "cert.pem"))
        self.client.load_cert_chain(str(self.root / "cert.pem"), str(self.root / "key.pem"))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def get_health(self):
        connection = http.client.HTTPSConnection(
            "localhost", self.port, context=self.client, timeout=1
        )
        try:
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            self.assertEqual((response.status, response.read()), (200, b"ok"))
        finally:
            connection.close()

    def test_silent_tcp_and_partial_tls_do_not_block_other_clients(self):
        silent = socket.create_connection(("127.0.0.1", self.port), 1)
        partial = socket.create_connection(("127.0.0.1", self.port), 1)
        try:
            partial.sendall(b"\x16\x03\x01\x00")
            self.get_health()
            time.sleep(0.6)
            self.get_health()
        finally:
            silent.close()
            partial.close()

    def test_partial_http_body_releases_worker_after_timeout(self):
        raw = socket.create_connection(("127.0.0.1", self.port), 1)
        secured = self.client.wrap_socket(raw, server_hostname="localhost")
        try:
            secured.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n")
            self.get_health()
            time.sleep(0.6)
            self.get_health()
        finally:
            secured.close()

    def test_saturated_idle_workers_release_their_capacity(self):
        clients = [socket.create_connection(("127.0.0.1", self.port), 1) for _ in range(3)]
        try:
            time.sleep(0.7)
            self.get_health()
        finally:
            for client in clients:
                client.close()


if __name__ == "__main__":
    unittest.main()
