"""mTLS/HMAC HTTP service for the private Market Fact receiver."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Mapping

from .market_fact_receiver import (
    DEFAULT_PAYLOAD_RETENTION_SECONDS,
    RECEIVER_SCHEMA,
    ReceiverCompactionError,
    apply_fact_batch,
    compact_consumed_payloads,
    connect_receiver,
    load_adapter_watermark,
    receiver_metrics,
    record_rejection,
)
from .private_market_transport import (
    FACT_PATH,
    HEALTH_PATH,
    MAX_WIRE_BYTES,
    MarketAuthenticationError,
    MarketTransportError,
    authenticate_request,
    decode_document,
    read_key,
    server_tls_context,
)


class MarketFactReceiverServiceError(RuntimeError):
    """A safe receiver service failure."""


def _bounded_integer(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise MarketFactReceiverServiceError(
            "market_fact_receiver_compaction_config_invalid"
        ) from exc
    if not minimum <= value <= maximum:
        raise MarketFactReceiverServiceError(
            "market_fact_receiver_compaction_config_invalid"
        )
    return value


class _ReceiverServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        database_path: Path,
        allowed_peer_ip: str,
        keys: Mapping[str, bytes],
    ) -> None:
        self.database_path = database_path
        self.allowed_peer_ip = allowed_peer_ip
        self.keys = dict(keys)
        super().__init__(address, _ReceiverHandler)


class _ReceiverHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MarketFactReceiver/1.0"
    sys_version = ""

    @property
    def receiver(self) -> _ReceiverServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _respond(self, status: int, value: Mapping[str, object]) -> None:
        body = (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _peer_allowed(self, *, health: bool = False) -> bool:
        peer = str(self.client_address[0])
        if peer == self.receiver.allowed_peer_ip:
            return True
        if health and peer in {"127.0.0.1", "::1"}:
            return True
        self._respond(403, {"status": "REJECTED", "reason_code": "PEER_NOT_ALLOWED"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if self.path != HEALTH_PATH:
            self._respond(404, {"status": "NOT_FOUND"})
            return
        if not self._peer_allowed(health=True):
            return
        connection = connect_receiver(self.receiver.database_path)
        try:
            metrics = receiver_metrics(connection)
        finally:
            connection.close()
        self._respond(200, {"status": "live-ready", **metrics})

    def do_POST(self) -> None:  # noqa: N802
        if not self._peer_allowed():
            return
        if self.path != FACT_PATH:
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
        connection = connect_receiver(self.receiver.database_path)
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
                encoding = authenticate_request(
                    connection,
                    method="POST",
                    path=FACT_PATH,
                    headers=self.headers,
                    body=body,
                    keys=self.receiver.keys,
                )
                connection.commit()
            except MarketAuthenticationError as exc:
                connection.rollback()
                record_rejection(
                    connection,
                    reason_code=exc.reason_code,
                    body_hash=body_hash,
                )
                self._respond(
                    exc.status,
                    {"status": "REJECTED", "reason_code": exc.reason_code},
                )
                return
            try:
                document = decode_document(body, encoding)
            except MarketTransportError as exc:
                reason = str(exc).upper()
                record_rejection(
                    connection,
                    reason_code=reason,
                    body_hash=body_hash,
                )
                self._respond(422, {"status": "REJECTED", "reason_code": reason})
                return
            status, response = apply_fact_batch(connection, document)
            if status != 200:
                reasons = response.get("rejection_reason_codes")
                reason = (
                    str(reasons[0])
                    if isinstance(reasons, list) and reasons
                    else str(response.get("reason_code", "BATCH_REJECTED"))
                )
                record_rejection(
                    connection,
                    reason_code=reason,
                    body_hash=body_hash,
                )
            self._respond(status, response)
        except (OSError, sqlite3.Error):
            self._respond(
                503,
                {"status": "REJECTED", "reason_code": "DURABLE_STORE_UNAVAILABLE"},
            )
        finally:
            connection.close()


def run_market_fact_receiver_service(
    *,
    role: str,
    mode: str,
    release_sha: str,
    state_directory: Path,
    stop: threading.Event,
) -> int:
    if role != "market-fact-receiver" or mode != "live":
        raise MarketFactReceiverServiceError("market_fact_receiver_role_or_mode_invalid")
    host = os.environ.get("MARKET_PIPELINE_LISTEN_HOST", "0.0.0.0").strip()
    port = int(os.environ.get("MARKET_PIPELINE_LISTEN_PORT", "9443"))
    peer = os.environ.get("MARKET_PIPELINE_ALLOWED_PEER_IP", "").strip()
    if not peer:
        raise MarketFactReceiverServiceError("market_fact_receiver_peer_required")
    active_id = os.environ.get("MARKET_HMAC_ACTIVE_KEY_ID", "active-v1").strip()
    previous_id = os.environ.get("MARKET_HMAC_PREVIOUS_KEY_ID", "previous-v1").strip()
    keys = {
        active_id: read_key(
            os.environ.get(
                "MARKET_HMAC_ACTIVE_PATH", "/run/secrets/market_hmac_active"
            )
        ),
        previous_id: read_key(
            os.environ.get(
                "MARKET_HMAC_PREVIOUS_PATH", "/run/secrets/market_hmac_previous"
            )
        ),
    }
    tls = server_tls_context(
        ca=os.environ.get(
            "MARKET_TRANSPORT_CA_PATH", "/run/secrets/market_transport_ca"
        ),
        cert=os.environ.get(
            "MARKET_TRANSPORT_CERT_PATH", "/run/secrets/market_bot_transport_cert"
        ),
        key=os.environ.get(
            "MARKET_TRANSPORT_KEY_PATH", "/run/secrets/market_bot_transport_key"
        ),
    )
    database_path = state_directory / "market-fact-receiver.sqlite3"
    watermark_path = state_directory / "adapter-consumption-watermark.json"
    retention_seconds = _bounded_integer(
        "MARKET_PIPELINE_RECEIVER_PAYLOAD_RETENTION_SECONDS",
        DEFAULT_PAYLOAD_RETENTION_SECONDS,
        minimum=3_600,
        maximum=604_800,
    )
    compaction_batch_rows = _bounded_integer(
        "MARKET_PIPELINE_RECEIVER_COMPACTION_BATCH_ROWS",
        2_000,
        minimum=1,
        maximum=10_000,
    )
    probe = connect_receiver(database_path)
    probe.close()
    server = _ReceiverServer(
        (host, port),
        database_path=database_path,
        allowed_peer_ip=peer,
        keys=keys,
    )
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.timeout = 0.25
    from .private_pipeline_foundation import atomic_json_write, utc_text

    started_at = utc_text()
    last_compaction_check = 0.0
    compaction_state: dict[str, object] = {
        "status": "AWAITING_WATERMARK",
        "retention_seconds": retention_seconds,
        "last_delivery_payloads": 0,
        "last_latest_payloads": 0,
    }

    try:
        while not stop.is_set():
            connection = connect_receiver(database_path)
            try:
                monotonic_now = time.monotonic()
                if monotonic_now - last_compaction_check >= 5.0:
                    last_compaction_check = monotonic_now
                    try:
                        watermark = load_adapter_watermark(watermark_path)
                        report = compact_consumed_payloads(
                            connection,
                            applied_checkpoints=watermark,
                            retention_seconds=retention_seconds,
                            max_rows=compaction_batch_rows,
                        )
                        compaction_state = {
                            "status": "READY",
                            "retention_seconds": retention_seconds,
                            "last_delivery_payloads": report.delivery_payloads,
                            "last_latest_payloads": report.latest_payloads,
                        }
                    except ReceiverCompactionError as exc:
                        compaction_state = {
                            "status": "BLOCKED",
                            "retention_seconds": retention_seconds,
                            "reason_code": str(exc),
                            "last_delivery_payloads": 0,
                            "last_latest_payloads": 0,
                        }
                    except sqlite3.Error:
                        compaction_state = {
                            "status": "BLOCKED",
                            "retention_seconds": retention_seconds,
                            "reason_code": "receiver_compaction_storage_unavailable",
                            "last_delivery_payloads": 0,
                            "last_latest_payloads": 0,
                        }
                metrics = receiver_metrics(connection)
            finally:
                connection.close()
            atomic_json_write(
                state_directory / "health.json",
                {
                    "schema": RECEIVER_SCHEMA,
                    "role": role,
                    "mode": mode,
                    "release_sha": release_sha,
                    "pid": os.getpid(),
                    "started_at_utc": started_at,
                    "updated_at_utc": utc_text(),
                    "status": "live-ready",
                    "durable_write": True,
                    "private_transport_only": True,
                    "payload_compaction": compaction_state,
                    **metrics,
                },
            )
            server.handle_request()
    finally:
        server.server_close()
    return 0
