#!/usr/bin/env python3
"""Exercise the Stage 1 private transport without touching product data.

The server binds one RFC1918 address, accepts only the configured peer, requires
TLS plus a short-lived HMAC envelope, and keeps an in-memory nonce replay
window.  The client emits aggregate timings and pass/fail evidence only; key
material and request bodies are never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import statistics
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
PROBE_PATH = "/v1/probe"
HEALTH_PATH = "/healthz"
MAX_BODY_BYTES = 1_048_576
KEY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
NONCE_RE = re.compile(r"^[a-f0-9]{32}$")
SIGNATURE_RE = re.compile(r"^[a-f0-9]{64}$")


class Stage1Error(RuntimeError):
    """A safe, code-only Stage 1 failure."""


class AuthenticationError(Stage1Error):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def validate_private_endpoint(address: str, peer: str, port: int) -> None:
    bind_ip = ipaddress.ip_address(address)
    peer_ip = ipaddress.ip_address(peer)
    if not bind_ip.is_private or bind_ip.is_loopback or bind_ip.is_unspecified:
        raise Stage1Error("bind_address_not_private")
    if not peer_ip.is_private or peer_ip.is_loopback or peer_ip.is_unspecified:
        raise Stage1Error("peer_address_not_private")
    if bind_ip == peer_ip:
        raise Stage1Error("bind_and_peer_must_differ")
    if port < 1024 or port > 65535:
        raise Stage1Error("probe_port_out_of_range")


def load_key(path: Path) -> bytes:
    key = path.read_bytes()
    if len(key) < 32:
        raise Stage1Error("hmac_key_too_short")
    return key


def canonical_request(
    method: str,
    path: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join(
        (method.upper(), path, key_id, timestamp, nonce, body_hash)
    ).encode("ascii")


def sign_request(
    key: bytes,
    method: str,
    path: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    return hmac.new(
        key,
        canonical_request(method, path, key_id, timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()


class ReplayWindow:
    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._seen: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def accept(self, key_id: str, nonce: str, now: float) -> bool:
        cutoff = now - self.ttl_seconds
        identity = (key_id, nonce)
        with self._lock:
            expired = [item for item, seen_at in self._seen.items() if seen_at < cutoff]
            for item in expired:
                del self._seen[item]
            if identity in self._seen:
                return False
            self._seen[identity] = now
            return True


@dataclass(frozen=True)
class AuthenticatedRequest:
    key_id: str
    body_sha256: str


class RequestAuthenticator:
    def __init__(
        self,
        keys: Mapping[str, bytes],
        *,
        max_clock_skew_seconds: int,
        replay_window_seconds: int,
    ):
        if not keys or any(not KEY_ID_RE.fullmatch(item) for item in keys):
            raise Stage1Error("invalid_key_ring")
        if max_clock_skew_seconds < 1 or replay_window_seconds < max_clock_skew_seconds:
            raise Stage1Error("invalid_auth_window")
        self.keys = dict(keys)
        self.max_clock_skew_seconds = max_clock_skew_seconds
        self.replay = ReplayWindow(replay_window_seconds)

    def authenticate(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now: float | None = None,
    ) -> AuthenticatedRequest:
        key_id = headers.get("X-Market-Key-ID", "")
        timestamp = headers.get("X-Market-Timestamp", "")
        nonce = headers.get("X-Market-Nonce", "")
        signature = headers.get("X-Market-Signature", "")
        if not KEY_ID_RE.fullmatch(key_id):
            raise AuthenticationError(401, "invalid_key_id")
        if not NONCE_RE.fullmatch(nonce):
            raise AuthenticationError(401, "invalid_nonce")
        if not SIGNATURE_RE.fullmatch(signature):
            raise AuthenticationError(401, "invalid_signature")
        try:
            timestamp_number = int(timestamp)
        except ValueError as exc:
            raise AuthenticationError(401, "invalid_timestamp") from exc
        current = time.time() if now is None else now
        if abs(current - timestamp_number) > self.max_clock_skew_seconds:
            raise AuthenticationError(401, "clock_skew")
        key = self.keys.get(key_id)
        if key is None:
            raise AuthenticationError(401, "unknown_key")
        expected = sign_request(
            key, method, path, key_id, timestamp, nonce, body
        )
        if not hmac.compare_digest(signature, expected):
            raise AuthenticationError(401, "invalid_signature")
        if not self.replay.accept(key_id, nonce, current):
            raise AuthenticationError(409, "replay_detected")
        return AuthenticatedRequest(
            key_id=key_id,
            body_sha256=hashlib.sha256(body).hexdigest(),
        )


class PrivateProbeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        peer_ip: str,
        authenticator: RequestAuthenticator,
    ):
        self.peer_ip = peer_ip
        self.authenticator = authenticator
        super().__init__(address, handler_class)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        connection, address = super().get_request()
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return connection, address


class PrivateProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "market-private-stage1"
    sys_version = ""

    @property
    def probe_server(self) -> PrivateProbeServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _write_json(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _peer_allowed(self) -> bool:
        if self.client_address[0] == self.probe_server.peer_ip:
            return True
        self._write_json(403, {"status": "denied", "code": "peer_not_allowed"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if not self._peer_allowed():
            return
        if urlsplit(self.path).path != HEALTH_PATH:
            self._write_json(404, {"status": "not_found"})
            return
        self._write_json(
            200,
            {"status": "ok", "schema_version": SCHEMA_VERSION},
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self._peer_allowed():
            return
        path = urlsplit(self.path).path
        if path != PROBE_PATH:
            self._write_json(404, {"status": "not_found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(400, {"status": "rejected", "code": "invalid_length"})
            return
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            self._write_json(413, {"status": "rejected", "code": "body_too_large"})
            return
        body = self.rfile.read(content_length)
        if self.headers.get("X-Stage1-Disconnect") == "1":
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        try:
            accepted = self.probe_server.authenticator.authenticate(
                "POST", path, self.headers, body
            )
        except AuthenticationError as exc:
            self._write_json(
                exc.status,
                {"status": "rejected", "code": exc.code},
            )
            return
        self._write_json(
            200,
            {
                "status": "accepted",
                "schema_version": SCHEMA_VERSION,
                "key_id": accepted.key_id,
                "body_sha256": accepted.body_sha256,
            },
        )


def make_headers(
    key_id: str,
    key: bytes,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
    signature_override: str | None = None,
    disconnect: bool = False,
) -> dict[str, str]:
    timestamp_text = str(int(time.time()) if timestamp is None else timestamp)
    nonce_value = nonce or secrets.token_hex(16)
    signature = sign_request(
        key,
        "POST",
        PROBE_PATH,
        key_id,
        timestamp_text,
        nonce_value,
        body,
    )
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(body)),
        "X-Market-Key-ID": key_id,
        "X-Market-Timestamp": timestamp_text,
        "X-Market-Nonce": nonce_value,
        "X-Market-Signature": signature_override or signature,
    }
    if disconnect:
        headers["X-Stage1-Disconnect"] = "1"
    return headers


def request(
    connection: http.client.HTTPSConnection,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    connection.request(method, path, body=body, headers=dict(headers or {}))
    response = connection.getresponse()
    raw = response.read()
    try:
        payload = json.loads(raw.decode()) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage1Error("invalid_probe_response") from exc
    if not isinstance(payload, dict):
        raise Stage1Error("invalid_probe_response")
    return response.status, payload


def connection_for(host: str, port: int, ca_cert: Path, timeout: float):
    context = ssl.create_default_context(cafile=str(ca_cert))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return http.client.HTTPSConnection(host, port, timeout=timeout, context=context)


def percentile(values: Sequence[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * ratio)))
    return ordered[index]


def run_probe(args: argparse.Namespace) -> dict[str, object]:
    validate_private_endpoint(args.host, args.local_private_ip, args.port)
    current_key = load_key(args.current_key_file)
    next_key = load_key(args.next_key_file)
    body = b"stage1-private-transport-probe"
    checks: dict[str, bool] = {}

    connection = connection_for(args.host, args.port, args.ca_cert, args.timeout)
    status, payload = request(connection, "GET", HEALTH_PATH)
    checks["health"] = status == 200 and payload.get("status") == "ok"
    tls_version = connection.sock.version() if connection.sock is not None else None
    tls_cipher = connection.sock.cipher()[0] if connection.sock is not None else None

    replay_nonce = secrets.token_hex(16)
    replay_headers = make_headers(
        args.current_key_id, current_key, body, nonce=replay_nonce
    )
    status, payload = request(
        connection, "POST", PROBE_PATH, body=body, headers=replay_headers
    )
    checks["current_key"] = status == 200 and payload.get("status") == "accepted"
    status, payload = request(
        connection, "POST", PROBE_PATH, body=body, headers=replay_headers
    )
    checks["replay_rejected"] = status == 409 and payload.get("code") == "replay_detected"

    bad_headers = make_headers(
        args.current_key_id,
        current_key,
        body,
        signature_override="0" * 64,
    )
    status, payload = request(
        connection, "POST", PROBE_PATH, body=body, headers=bad_headers
    )
    checks["bad_signature_rejected"] = status == 401 and payload.get("code") == "invalid_signature"

    stale_headers = make_headers(
        args.current_key_id,
        current_key,
        body,
        timestamp=int(time.time()) - args.clock_skew_seconds - 2,
    )
    status, payload = request(
        connection, "POST", PROBE_PATH, body=body, headers=stale_headers
    )
    checks["stale_clock_rejected"] = status == 401 and payload.get("code") == "clock_skew"

    future_headers = make_headers(
        args.current_key_id,
        current_key,
        body,
        timestamp=int(time.time()) + args.clock_skew_seconds + 2,
    )
    status, payload = request(
        connection, "POST", PROBE_PATH, body=body, headers=future_headers
    )
    checks["future_clock_rejected"] = status == 401 and payload.get("code") == "clock_skew"

    next_headers = make_headers(args.next_key_id, next_key, body)
    status, payload = request(
        connection, "POST", PROBE_PATH, body=body, headers=next_headers
    )
    checks["next_key_rotation"] = status == 200 and payload.get("key_id") == args.next_key_id

    retired_headers = make_headers("retired", current_key, body)
    status, payload = request(
        connection, "POST", PROBE_PATH, body=body, headers=retired_headers
    )
    checks["retired_key_rejected"] = status == 401 and payload.get("code") == "unknown_key"
    connection.close()

    disconnected = False
    connection = connection_for(args.host, args.port, args.ca_cert, args.timeout)
    try:
        request(
            connection,
            "POST",
            PROBE_PATH,
            body=body,
            headers=make_headers(
                args.current_key_id, current_key, body, disconnect=True
            ),
        )
    except (ConnectionError, http.client.HTTPException, OSError, ssl.SSLError):
        disconnected = True
    finally:
        connection.close()
    connection = connection_for(args.host, args.port, args.ca_cert, args.timeout)
    status, payload = request(
        connection,
        "POST",
        PROBE_PATH,
        body=body,
        headers=make_headers(args.current_key_id, current_key, body),
    )
    checks["reconnect"] = disconnected and status == 200 and payload.get("status") == "accepted"

    payload_body = b"x" * args.payload_bytes
    latencies_ms: list[float] = []
    started = time.perf_counter()
    for _ in range(args.requests):
        headers = make_headers(args.current_key_id, current_key, payload_body)
        one_started = time.perf_counter()
        status, payload = request(
            connection, "POST", PROBE_PATH, body=payload_body, headers=headers
        )
        latencies_ms.append((time.perf_counter() - one_started) * 1000)
        if status != 200 or payload.get("status") != "accepted":
            raise Stage1Error("throughput_request_rejected")
    elapsed = time.perf_counter() - started
    connection.close()
    transferred = args.requests * args.payload_bytes
    throughput_mib_s = transferred / max(elapsed, 0.000001) / (1024 * 1024)
    checks["throughput"] = throughput_mib_s >= args.minimum_throughput_mib_s
    checks["latency"] = percentile(latencies_ms, 0.95) <= args.maximum_p95_ms

    result: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "transport": {
            "tls_version": tls_version,
            "tls_cipher": tls_cipher,
            "request_count": args.requests,
            "payload_bytes": args.payload_bytes,
            "throughput_mib_s": round(throughput_mib_s, 3),
            "latency_ms": {
                "median": round(statistics.median(latencies_ms), 3),
                "p95": round(percentile(latencies_ms, 0.95), 3),
                "maximum": round(max(latencies_ms), 3),
            },
        },
    }
    return result


def serve(args: argparse.Namespace) -> int:
    validate_private_endpoint(args.bind, args.peer, args.port)
    keys = {
        args.current_key_id: load_key(args.current_key_file),
        args.next_key_id: load_key(args.next_key_file),
    }
    authenticator = RequestAuthenticator(
        keys,
        max_clock_skew_seconds=args.clock_skew_seconds,
        replay_window_seconds=args.replay_window_seconds,
    )
    server = PrivateProbeServer(
        (args.bind, args.port),
        PrivateProbeHandler,
        peer_ip=args.peer,
        authenticator=authenticator,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=args.cert, keyfile=args.cert_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(
        json.dumps(
            {
                "status": "ready",
                "schema_version": SCHEMA_VERSION,
                "private_bind": True,
                "peer_allowlist": True,
                "active_key_count": len(keys),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    server = subparsers.add_parser("serve")
    server.add_argument("--bind", required=True)
    server.add_argument("--peer", required=True)
    server.add_argument("--port", type=int, default=18443)
    server.add_argument("--cert", type=Path, required=True)
    server.add_argument("--cert-key", type=Path, required=True)
    server.add_argument("--current-key-id", default="stage1-current")
    server.add_argument("--current-key-file", type=Path, required=True)
    server.add_argument("--next-key-id", default="stage1-next")
    server.add_argument("--next-key-file", type=Path, required=True)
    server.add_argument("--clock-skew-seconds", type=int, default=30)
    server.add_argument("--replay-window-seconds", type=int, default=120)

    probe = subparsers.add_parser("probe")
    probe.add_argument("--host", required=True)
    probe.add_argument("--local-private-ip", required=True)
    probe.add_argument("--port", type=int, default=18443)
    probe.add_argument("--ca-cert", type=Path, required=True)
    probe.add_argument("--current-key-id", default="stage1-current")
    probe.add_argument("--current-key-file", type=Path, required=True)
    probe.add_argument("--next-key-id", default="stage1-next")
    probe.add_argument("--next-key-file", type=Path, required=True)
    probe.add_argument("--clock-skew-seconds", type=int, default=30)
    probe.add_argument("--requests", type=int, default=200)
    probe.add_argument("--payload-bytes", type=int, default=65536)
    probe.add_argument("--minimum-throughput-mib-s", type=float, default=2.0)
    probe.add_argument("--maximum-p95-ms", type=float, default=100.0)
    probe.add_argument("--timeout", type=float, default=5.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            return serve(args)
        result = run_probe(args)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "pass" else 1
    except (OSError, Stage1Error, ssl.SSLError) as exc:
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "status": "fail", "reason": str(exc)},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
