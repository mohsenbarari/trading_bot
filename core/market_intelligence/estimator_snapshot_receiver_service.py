"""mTLS/HMAC private endpoint for estimator snapshots and web views."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Mapping

from .estimator_snapshot_receiver import (
    SNAPSHOT_RECEIVER_SCHEMA,
    EstimatorSnapshotReceiverError,
    apply_estimator_snapshot,
    connect_snapshot_receiver,
    read_web_snapshot_view,
    record_snapshot_rejection,
    snapshot_receiver_metrics,
)
from .private_market_transport import (
    HEALTH_PATH,
    MAX_WIRE_BYTES,
    SNAPSHOT_CURRENT_PATH,
    SNAPSHOT_PATH,
    MarketAuthenticationError,
    MarketTransportError,
    authenticate_request,
    decode_document,
    read_key,
    server_tls_context,
)


class EstimatorSnapshotReceiverServiceError(RuntimeError):
    """Safe service failure."""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        database_path: Path,
        snapshot_root: Path,
        events_path: Path,
        allowed_peer_ip: str,
        keys: Mapping[str, bytes],
    ) -> None:
        self.database_path = database_path
        self.snapshot_root = snapshot_root
        self.events_path = events_path
        self.allowed_peer_ip = allowed_peer_ip
        self.keys = dict(keys)
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "EstimatorSnapshotReceiver/1.0"
    sys_version = ""

    @property
    def receiver(self) -> _Server:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _respond(self, status: int, value: Mapping[str, object]) -> None:
        body = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _peer_allowed(self, *, local_ok: bool = False) -> bool:
        peer = str(self.client_address[0])
        if peer == self.receiver.allowed_peer_ip or (
            local_ok and peer in {"127.0.0.1", "::1"}
        ):
            return True
        self._respond(403, {"status": "REJECTED", "reason_code": "PEER_NOT_ALLOWED"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if not self._peer_allowed(local_ok=True):
            return
        if self.path == HEALTH_PATH:
            connection = connect_snapshot_receiver(self.receiver.database_path)
            try:
                metrics = snapshot_receiver_metrics(connection)
            finally:
                connection.close()
            self._respond(200, {"status": "live-ready", **metrics})
            return
        if self.path.startswith(SNAPSHOT_CURRENT_PATH):
            lane = (
                "PRIVATE_PRIMARY"
                if "feed_mode=PRIVATE_PRIMARY" in self.path
                else "PRIVATE_SHADOW"
            )
            filename = (
                "latest-private-primary.json"
                if lane == "PRIVATE_PRIMARY"
                else "latest-private-shadow.json"
            )
            try:
                view = read_web_snapshot_view(self.receiver.snapshot_root / filename)
            except EstimatorSnapshotReceiverError:
                self._respond(503, {"status": "UNAVAILABLE", "reason_code": "SNAPSHOT_MISSING"})
                return
            self._respond(200, view)
            return
        self._respond(404, {"status": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._peer_allowed():
            return
        if self.path != SNAPSHOT_PATH:
            self._respond(404, {"status": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 1 <= length <= MAX_WIRE_BYTES:
            self._respond(413, {"status": "REJECTED", "reason_code": "SIZE_LIMIT"})
            return
        body = self.rfile.read(length)
        body_hash = hashlib.sha256(body).hexdigest()
        connection = connect_snapshot_receiver(self.receiver.database_path)
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
                encoding = authenticate_request(
                    connection,
                    method="POST",
                    path=SNAPSHOT_PATH,
                    headers=self.headers,
                    body=body,
                    keys=self.receiver.keys,
                )
                connection.commit()
            except MarketAuthenticationError as exc:
                connection.rollback()
                record_snapshot_rejection(
                    connection, reason_code=exc.reason_code, body_hash=body_hash
                )
                self._respond(exc.status, {"status": "REJECTED", "reason_code": exc.reason_code})
                return
            try:
                document = decode_document(body, encoding)
            except MarketTransportError as exc:
                reason = str(exc).upper()
                record_snapshot_rejection(connection, reason_code=reason, body_hash=body_hash)
                self._respond(422, {"status": "REJECTED", "reason_code": reason})
                return
            status, response = apply_estimator_snapshot(
                connection,
                document,
                snapshot_root=self.receiver.snapshot_root,
                publication_events_path=self.receiver.events_path,
            )
            if status != 200:
                record_snapshot_rejection(
                    connection,
                    reason_code=str(response.get("reason_code", "SNAPSHOT_REJECTED")),
                    body_hash=body_hash,
                )
            self._respond(status, response)
        except (OSError, sqlite3.Error):
            self._respond(503, {"status": "REJECTED", "reason_code": "DURABLE_STORE_UNAVAILABLE"})
        finally:
            connection.close()


def run_estimator_snapshot_receiver_service(
    *, role: str, mode: str, release_sha: str, state_directory: Path, stop: threading.Event
) -> int:
    if role != "estimator-snapshot-receiver" or mode != "live":
        raise EstimatorSnapshotReceiverServiceError("snapshot_receiver_role_or_mode_invalid")
    peer = os.environ.get("MARKET_PIPELINE_ALLOWED_PEER_IP", "").strip()
    if not peer:
        raise EstimatorSnapshotReceiverServiceError("snapshot_receiver_peer_required")
    snapshot_root = Path(
        os.environ.get("MARKET_PIPELINE_SNAPSHOT_ROOT", "/var/lib/market-data/snapshots")
    )
    snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    database_path = state_directory / "estimator-snapshot-receiver.sqlite3"
    probe = connect_snapshot_receiver(database_path)
    probe.close()
    keys = {
        os.environ.get("MARKET_HMAC_ACTIVE_KEY_ID", "active-v1").strip(): read_key(
            os.environ.get("MARKET_HMAC_ACTIVE_PATH", "/run/secrets/market_hmac_active")
        ),
        os.environ.get("MARKET_HMAC_PREVIOUS_KEY_ID", "previous-v1").strip(): read_key(
            os.environ.get("MARKET_HMAC_PREVIOUS_PATH", "/run/secrets/market_hmac_previous")
        ),
    }
    tls = server_tls_context(
        ca=os.environ.get("MARKET_TRANSPORT_CA_PATH", "/run/secrets/market_transport_ca"),
        cert=os.environ.get("MARKET_TRANSPORT_CERT_PATH", "/run/secrets/market_web_transport_cert"),
        key=os.environ.get("MARKET_TRANSPORT_KEY_PATH", "/run/secrets/market_web_transport_key"),
    )
    server = _Server(
        (
            os.environ.get("MARKET_PIPELINE_LISTEN_HOST", "0.0.0.0"),
            int(os.environ.get("MARKET_PIPELINE_LISTEN_PORT", "9443")),
        ),
        database_path=database_path,
        snapshot_root=snapshot_root,
        events_path=state_directory / "snapshot-publication-events.jsonl",
        allowed_peer_ip=peer,
        keys=keys,
    )
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.timeout = 0.25
    from .private_pipeline_foundation import atomic_json_write

    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        while not stop.is_set():
            connection = connect_snapshot_receiver(database_path)
            try:
                metrics = snapshot_receiver_metrics(connection)
            finally:
                connection.close()
            atomic_json_write(
                state_directory / "health.json",
                {
                    "schema": SNAPSHOT_RECEIVER_SCHEMA,
                    "role": role,
                    "mode": mode,
                    "release_sha": release_sha,
                    "pid": os.getpid(),
                    "started_at_utc": started,
                    "updated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "status": "live-ready",
                    "private_transport_only": True,
                    **metrics,
                },
            )
            server.handle_request()
    finally:
        server.server_close()
    return 0


__all__ = ["EstimatorSnapshotReceiverServiceError", "run_estimator_snapshot_receiver_service"]
