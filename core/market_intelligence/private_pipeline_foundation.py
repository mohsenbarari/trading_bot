"""Fail-closed Docker entrypoint for the private market-data pipeline.

Stages 4-10 promote the isolated capture, processing, fact transport, adapter,
estimator snapshot and return-path roles. Telegram capture still requires
release-bound session authority; every promotion mode remains explicit and no
service receives product authority merely by entering live runtime mode.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import errno
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import threading
import time
from typing import Any, Iterator, Mapping, Sequence
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import ValidationError

from .private_pipeline_contracts import (
    EstimatorSnapshotV1,
)


FOUNDATION_SCHEMA = "market_pipeline_foundation/1.0"
LIVE_NOT_IMPLEMENTED_EXIT = 78
DEFAULT_STATE_ROOT = Path("/var/lib/market-data/state")
DEFAULT_SESSION_ROOT = Path("/var/lib/market-data/session")
DEFAULT_MIGRATION = Path(
    "/app/deploy/market-data/migrations/0001_market_archive.up.sql"
)
RELEASE_SHA = re.compile(r"^[0-9a-f]{8,64}$")
ROLE_CODE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
MAX_FIXTURE_BODY_BYTES = 2 * 1024 * 1024

WEB_ROLES = frozenset(
    {
        "market-capture-account1",
        "market-capture-account2",
        "market-capture-external",
        "market-processor",
        "market-fact-sync-worker",
        "estimator-snapshot-receiver",
    }
)
BOT_ROLES = frozenset(
    {
        "market-fact-receiver",
        "market-store-adapter",
        "coin-estimator",
        "estimator-snapshot-sender",
    }
)
ROLES = WEB_ROLES | BOT_ROLES
CAPTURE_ROLES = frozenset(
    {"market-capture-account1", "market-capture-account2"}
)
EXTERNAL_CAPTURE_ROLES = frozenset({"market-capture-external"})
LIVE_ROLES = CAPTURE_ROLES | EXTERNAL_CAPTURE_ROLES | {
    "market-processor",
    "market-fact-sync-worker",
    "market-fact-receiver",
    "market-store-adapter",
    "coin-estimator",
    "estimator-snapshot-sender",
    "estimator-snapshot-receiver",
}
RECEIVER_ROLES = frozenset(
    {"market-fact-receiver", "estimator-snapshot-receiver"}
)


class FoundationError(RuntimeError):
    """A safe, non-sensitive foundation failure."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def safe_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = (safe_json(value) + "\n").encode("utf-8")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[int]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise FoundationError("role_owner_lock_already_held") from exc
            raise
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield descriptor
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def state_root() -> Path:
    return Path(os.environ.get("MARKET_PIPELINE_STATE_ROOT", str(DEFAULT_STATE_ROOT)))


def session_root() -> Path:
    return Path(
        os.environ.get("MARKET_PIPELINE_SESSION_ROOT", str(DEFAULT_SESSION_ROOT))
    )


def role_state(role: str) -> Path:
    return state_root() / role


def validate_role(role: str) -> None:
    if role not in ROLES or not ROLE_CODE.fullmatch(role):
        raise FoundationError("unknown_market_pipeline_role")


def validate_fixture_environment(role: str) -> tuple[str, str]:
    validate_role(role)
    mode = os.environ.get("MARKET_PIPELINE_MODE", "disabled").strip().lower()
    if mode not in {"fixture", "live"}:
        raise FoundationError("runtime_mode_not_available_at_stage10")
    if mode == "live" and role not in LIVE_ROLES:
        raise FoundationError("role_live_mode_not_available_at_stage10")
    release_sha = os.environ.get("MARKET_PIPELINE_RELEASE_SHA", "").strip().lower()
    image_revision = os.environ.get("MARKET_PIPELINE_IMAGE_REVISION", "").strip().lower()
    if not RELEASE_SHA.fullmatch(release_sha):
        raise FoundationError("release_sha_invalid")
    if release_sha != image_revision:
        raise FoundationError("release_sha_image_revision_mismatch")
    if os.geteuid() == 0:
        raise FoundationError("runtime_must_not_run_as_root")

    root = role_state(role)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    probe = root / ".durable-write-probe"
    atomic_json_write(
        probe,
        {
            "schema": FOUNDATION_SCHEMA,
            "role": role,
            "release_sha": release_sha,
        },
    )
    probe.unlink()
    _fsync_directory(root)

    if role in CAPTURE_ROLES:
        sessions = session_root()
        if not sessions.is_dir() or not os.access(sessions, os.W_OK):
            raise FoundationError("capture_session_mount_not_writable")
    return mode, release_sha


class Heartbeat:
    def __init__(self, role: str, mode: str, release_sha: str) -> None:
        self.role = role
        self.mode = mode
        self.release_sha = release_sha
        self.started_at = utc_text()
        self.path = role_state(role) / "health.json"

    def write(self, *, status: str = "fixture-ready") -> None:
        atomic_json_write(
            self.path,
            {
                "schema": FOUNDATION_SCHEMA,
                "role": self.role,
                "mode": self.mode,
                "release_sha": self.release_sha,
                "pid": os.getpid(),
                "started_at_utc": self.started_at,
                "updated_at_utc": utc_text(),
                "status": status,
                "durable_write": True,
            },
        )


def initialize_market_store_fixture(role: str, release_sha: str) -> None:
    if role != "market-store-adapter":
        return
    path = Path(
        os.environ.get(
            "MARKET_PIPELINE_MARKET_STORE_PATH",
            "/var/lib/market-data/market-store/market-store.sqlite",
        )
    )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stage3_foundation_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                release_sha TEXT NOT NULL,
                initialized_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO stage3_foundation_state(singleton, release_sha, initialized_at_utc)
            VALUES (1, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET release_sha = excluded.release_sha
            """,
            (release_sha, utc_text()),
        )
        connection.commit()
    finally:
        connection.close()


def owner_lock_paths(role: str) -> tuple[Path, ...]:
    paths = [role_state(role) / "owner.lock"]
    if role in CAPTURE_ROLES:
        paths.append(session_root() / "owner.lock")
    if role == "market-store-adapter":
        market_store_path = Path(
            os.environ.get(
                "MARKET_PIPELINE_MARKET_STORE_PATH",
                "/var/lib/market-data/market-store/market-store.sqlite",
            )
        )
        paths.append(market_store_path.parent / "owner.lock")
    return tuple(paths)


def receiver_database(role: str) -> sqlite3.Connection:
    from .market_fact_receiver import connect_receiver

    root = role_state(role)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = connect_receiver(root / "fixture-receiver.sqlite")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshot_receipts (
            snapshot_version INTEGER PRIMARY KEY,
            snapshot_id TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            received_at_utc TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def apply_fact_batch(role: str, document: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    from .market_fact_receiver import apply_fact_batch as apply_to_receiver

    connection = receiver_database(role)
    try:
        return apply_to_receiver(connection, document)
    finally:
        connection.close()


def apply_estimator_snapshot(
    role: str, document: Mapping[str, Any]
) -> tuple[int, dict[str, Any]]:
    try:
        snapshot = EstimatorSnapshotV1.model_validate(document)
    except ValidationError:
        return 422, {"status": "REJECTED", "reason_code": "CONTRACT_INVALID"}
    connection = receiver_database(role)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT MAX(snapshot_version) FROM snapshot_receipts"
        ).fetchone()
        latest = int(row[0]) if row and row[0] is not None else 0
        if snapshot.snapshot_version < latest:
            connection.rollback()
            return 409, {
                "status": "REJECTED",
                "reason_code": "SNAPSHOT_VERSION_REGRESSION",
            }
        connection.execute(
            """
            INSERT INTO snapshot_receipts(
                snapshot_version, snapshot_id, payload_json, received_at_utc
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(snapshot_version) DO NOTHING
            """,
            (
                snapshot.snapshot_version,
                snapshot.snapshot_id,
                snapshot.model_dump_json(),
                utc_text(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    atomic_json_write(
        role_state(role) / "latest-estimator-snapshot.json",
        snapshot.model_dump(mode="json"),
    )
    return 200, {
        "status": "ACK",
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_version": snapshot.snapshot_version,
    }


class FixtureReceiverHandler(BaseHTTPRequestHandler):
    server_version = "MarketFoundation/1.0"

    def log_message(self, _format: str, *_arguments: object) -> None:
        return

    @property
    def role(self) -> str:
        return str(getattr(self.server, "market_role"))

    def _respond(self, status: int, document: Mapping[str, Any]) -> None:
        payload = (safe_json(document) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler interface
        if self.path != "/healthz":
            self._respond(404, {"status": "NOT_FOUND"})
            return
        self._respond(200, {"status": "fixture-ready", "role": self.role})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler interface
        expected_path = {
            "market-fact-receiver": "/fixture/market-facts",
            "estimator-snapshot-receiver": "/fixture/estimator-snapshot",
        }[self.role]
        if self.path != expected_path:
            self._respond(404, {"status": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 1 <= length <= MAX_FIXTURE_BODY_BYTES:
            self._respond(413, {"status": "REJECTED", "reason_code": "SIZE_LIMIT"})
            return
        try:
            document = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._respond(400, {"status": "REJECTED", "reason_code": "JSON_INVALID"})
            return
        if not isinstance(document, dict):
            self._respond(422, {"status": "REJECTED", "reason_code": "OBJECT_REQUIRED"})
            return
        if self.role == "market-fact-receiver":
            status, response = apply_fact_batch(self.role, document)
        else:
            status, response = apply_estimator_snapshot(self.role, document)
        self._respond(status, response)


def serve_receiver(role: str, heartbeat: Heartbeat, stop: threading.Event) -> None:
    listen_host = os.environ.get("MARKET_PIPELINE_LISTEN_HOST", "0.0.0.0")
    listen_port = int(os.environ.get("MARKET_PIPELINE_LISTEN_PORT", "9443"))
    server = ThreadingHTTPServer((listen_host, listen_port), FixtureReceiverHandler)
    server.timeout = 1.0
    setattr(server, "market_role", role)
    try:
        while not stop.is_set():
            heartbeat.write()
            server.handle_request()
    finally:
        server.server_close()


def run_service(role: str) -> int:
    mode, release_sha = validate_fixture_environment(role)
    stop = threading.Event()

    def stop_service(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, stop_service)
    signal.signal(signal.SIGINT, stop_service)
    with ExitStack() as locks:
        for path in owner_lock_paths(role):
            locks.enter_context(exclusive_lock(path))
        try:
            if role in CAPTURE_ROLES:
                from .private_capture import CaptureRuntimeError
                from .private_capture_service import run_capture_service

                try:
                    return run_capture_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except CaptureRuntimeError as exc:
                    raise FoundationError(str(exc)) from exc
            if role in EXTERNAL_CAPTURE_ROLES:
                from .external_quote_capture import (
                    ExternalQuoteCaptureError,
                    run_external_capture_service,
                )

                try:
                    return run_external_capture_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except ExternalQuoteCaptureError as exc:
                    raise FoundationError(str(exc)) from exc
            if role == "market-processor":
                from .coin_group_calibration_corpus import (
                    CoinGroupCalibrationCorpusError,
                )
                from .coin_group_feedback import CoinGroupFeedbackError
                from .coin_prediction_anchors import CoinPredictionAnchorError
                from .private_coin_processor import (
                    CoinProcessorError,
                    run_coin_processor_service,
                )

                try:
                    return run_coin_processor_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except (
                    CoinGroupCalibrationCorpusError,
                    CoinGroupFeedbackError,
                    CoinPredictionAnchorError,
                    CoinProcessorError,
                ) as exc:
                    raise FoundationError(str(exc)) from exc
            if role == "market-fact-sync-worker" and mode == "live":
                from .market_fact_sync import (
                    MarketFactSyncError,
                    run_market_fact_sync_service,
                )

                try:
                    return run_market_fact_sync_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except MarketFactSyncError as exc:
                    raise FoundationError(str(exc)) from exc
            if role == "market-fact-receiver" and mode == "live":
                from .market_fact_receiver_service import (
                    MarketFactReceiverServiceError,
                    run_market_fact_receiver_service,
                )

                try:
                    return run_market_fact_receiver_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except MarketFactReceiverServiceError as exc:
                    raise FoundationError(str(exc)) from exc
            if role == "market-store-adapter" and mode == "live":
                from .market_fact_adapter import (
                    MarketFactAdapterError,
                    run_market_fact_adapter_service,
                )

                try:
                    return run_market_fact_adapter_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except MarketFactAdapterError as exc:
                    raise FoundationError(str(exc)) from exc
            if role == "coin-estimator" and mode == "live":
                from .estimator_snapshot_runtime import (
                    EstimatorSnapshotRuntimeError,
                    run_coin_estimator_service,
                )

                try:
                    return run_coin_estimator_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except EstimatorSnapshotRuntimeError as exc:
                    raise FoundationError(str(exc)) from exc
            if role == "estimator-snapshot-sender" and mode == "live":
                from .estimator_snapshot_runtime import (
                    EstimatorSnapshotRuntimeError,
                    run_estimator_snapshot_sender_service,
                )

                try:
                    return run_estimator_snapshot_sender_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except EstimatorSnapshotRuntimeError as exc:
                    raise FoundationError(str(exc)) from exc
            if role == "estimator-snapshot-receiver" and mode == "live":
                from .estimator_snapshot_receiver_service import (
                    EstimatorSnapshotReceiverServiceError,
                    run_estimator_snapshot_receiver_service,
                )

                try:
                    return run_estimator_snapshot_receiver_service(
                        role=role,
                        mode=mode,
                        release_sha=release_sha,
                        state_directory=role_state(role),
                        stop=stop,
                    )
                except EstimatorSnapshotReceiverServiceError as exc:
                    raise FoundationError(str(exc)) from exc
            initialize_market_store_fixture(role, release_sha)
            heartbeat = Heartbeat(role, mode, release_sha)
            heartbeat.write(status="fixture-starting")
            if role in RECEIVER_ROLES:
                serve_receiver(role, heartbeat, stop)
            else:
                while not stop.wait(1.0):
                    heartbeat.write()
            heartbeat.write(status="fixture-stopped")
        finally:
            stop.set()
    return 0


def run_healthcheck(role: str, max_age_seconds: float) -> int:
    validate_role(role)
    path = role_state(role) / "health.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(document["updated_at_utc"].replace("Z", "+00:00"))
        age = (utc_now() - updated.astimezone(timezone.utc)).total_seconds()
        if document.get("role") != role:
            raise FoundationError("heartbeat_not_ready")
        if role in CAPTURE_ROLES:
            if document.get("schema") != "market_capture_engine/1.0":
                raise FoundationError("capture_heartbeat_schema_invalid")
            mode = document.get("mode")
            if document.get("status") != f"{mode}-ready" or mode not in {
                "fixture",
                "live",
            }:
                raise FoundationError("capture_heartbeat_not_ready")
            if set((document.get("sources") or {})) != {
                "market-capture-account1": {
                    "MELTED_PRIMARY_FLOW",
                    "MELTED_AGGREGATE",
                    "MELTED_FLOW",
                    "USD_HERAT",
                    "XAUUSD",
                },
                "market-capture-account2": {"GROUP_1", "GROUP_2"},
            }[role]:
                raise FoundationError("capture_heartbeat_source_inventory_invalid")
        elif role in EXTERNAL_CAPTURE_ROLES:
            if document.get("schema") != "external_quote_capture/1.0":
                raise FoundationError("external_capture_heartbeat_schema_invalid")
            mode = document.get("mode")
            if mode not in {"fixture", "live"} or document.get("status") != f"{mode}-ready":
                raise FoundationError("external_capture_heartbeat_not_ready")
            if set(document.get("sources") or {}) != {
                "WALLEX_PUBLIC_API",
                "BINANCE_PAXG_PUBLIC_API",
            }:
                raise FoundationError("external_capture_source_inventory_invalid")
        elif role == "market-processor":
            if document.get("schema") != "market_processor/4.0":
                raise FoundationError("processor_heartbeat_schema_invalid")
            mode = document.get("mode")
            expected_status = (
                "live-shadow-ready" if mode == "live" else "fixture-ready"
            )
            if (
                mode not in {"fixture", "live"}
                or document.get("status") != expected_status
            ):
                raise FoundationError("processor_heartbeat_not_ready")
            processor_sources = set(document.get("sources") or {})
            all_sources = {
                "GROUP_1",
                "GROUP_2",
                "MELTED_PRIMARY_FLOW",
                "MELTED_AGGREGATE",
                "MELTED_FLOW",
                "USD_HERAT",
                "XAUUSD",
                "WALLEX_PUBLIC_API",
                "BINANCE_PAXG_PUBLIC_API",
            }
            if (
                (mode == "live" and processor_sources != all_sources)
                or (
                    mode == "fixture"
                    and (
                        not {"GROUP_1", "GROUP_2"}.issubset(processor_sources)
                        or not processor_sources.issubset(all_sources)
                    )
                )
            ):
                raise FoundationError("processor_heartbeat_source_inventory_invalid")
            if document.get("shadow_only") is not True:
                raise FoundationError("processor_shadow_boundary_invalid")
            causal = document.get("last_projection_causal_inputs")
            if not isinstance(causal, dict) or not {
                "feedback_rows",
                "prediction_rows_seen",
                "prediction_rows_rejected",
                "anchors",
            }.issubset(causal):
                raise FoundationError("processor_causal_input_health_invalid")
        elif role == "market-fact-sync-worker" and document.get("mode") == "live":
            if (
                document.get("schema") != "market_fact_sync/1.0"
                or document.get("status") != "live-ready"
                or document.get("private_transport_only") is not True
                or not isinstance(document.get("queue_depth"), int)
            ):
                raise FoundationError("market_fact_sync_heartbeat_invalid")
        elif role == "market-fact-receiver" and document.get("mode") == "live":
            if (
                document.get("schema") != "market_fact_receiver/1.0"
                or document.get("status") != "live-ready"
                or document.get("private_transport_only") is not True
                or not isinstance(document.get("streams"), dict)
            ):
                raise FoundationError("market_fact_receiver_heartbeat_invalid")
        elif role == "market-store-adapter" and document.get("mode") == "live":
            if (
                document.get("schema") != "market_fact_adapter/1.0"
                or document.get("status") != "live-ready"
                or document.get("feed_mode")
                not in {"LEGACY", "PRIVATE_SHADOW", "PRIVATE_PRIMARY"}
                or not isinstance(document.get("streams"), dict)
                or document.get("consuming_private_facts")
                is not (document.get("feed_mode") != "LEGACY")
            ):
                raise FoundationError("market_fact_adapter_heartbeat_invalid")
        elif role == "coin-estimator" and document.get("mode") == "live":
            if (
                document.get("schema") != "coin_estimator_runtime/1.0"
                or document.get("status") != "live-ready"
                or document.get("feed_mode")
                not in {"LEGACY", "PRIVATE_SHADOW", "PRIVATE_PRIMARY"}
            ):
                raise FoundationError("coin_estimator_heartbeat_invalid")
        elif role == "estimator-snapshot-sender" and document.get("mode") == "live":
            if (
                document.get("schema") != "estimator_snapshot_sender/1.0"
                or document.get("status") not in {"live-ready", "live-degraded"}
                or document.get("private_transport_only") is not True
            ):
                raise FoundationError("snapshot_sender_heartbeat_invalid")
        elif role == "estimator-snapshot-receiver" and document.get("mode") == "live":
            if (
                document.get("schema") != "estimator_snapshot_receiver/1.0"
                or document.get("status") != "live-ready"
                or document.get("private_transport_only") is not True
                or not isinstance(document.get("lanes"), dict)
            ):
                raise FoundationError("snapshot_receiver_heartbeat_invalid")
        elif document.get("status") != "fixture-ready":
            raise FoundationError("heartbeat_not_ready")
        if not 0 <= age <= max_age_seconds:
            raise FoundationError("heartbeat_stale")
        pid = int(document["pid"])
        os.kill(pid, 0)
        if role in RECEIVER_ROLES and document.get("mode") != "live":
            port = int(os.environ.get("MARKET_PIPELINE_LISTEN_PORT", "9443"))
            with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                if response.status != 200:
                    raise FoundationError("receiver_health_not_ready")
    except (
        FoundationError,
        FileNotFoundError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        OSError,
        URLError,
    ):
        return 1
    return 0


def _read_secret(path: str, *, label: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise FoundationError(f"{label}_secret_empty")
    return value


def run_migration(migration_path: Path) -> int:
    import psycopg2

    password = _read_secret(
        os.environ.get(
            "MARKET_POSTGRES_PASSWORD_FILE",
            "/run/secrets/market_postgres_password",
        ),
        label="postgres_password",
    )
    try:
        connection = psycopg2.connect(
            host=os.environ.get("MARKET_POSTGRES_HOST", "market-database"),
            port=int(os.environ.get("MARKET_POSTGRES_PORT", "5432")),
            user=os.environ.get("MARKET_POSTGRES_USER", "market_data"),
            password=password,
            dbname=os.environ.get("MARKET_POSTGRES_DB", "market_archive"),
            connect_timeout=5,
            application_name="market-pipeline-migration",
        )
    except psycopg2.Error as exc:
        raise FoundationError("market_migration_database_failure") from exc
    try:
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('market_data.schema_migrations')")
                table = cursor.fetchone()[0]
                if table is not None:
                    cursor.execute(
                        "SELECT 1 FROM market_data.schema_migrations WHERE version = 1"
                    )
                    if cursor.fetchone() is not None:
                        print(safe_json({"status": "already_current", "version": 1}))
                        return 0
                cursor.execute(migration_path.read_text(encoding="utf-8"))
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'market_data'"
                )
                table_count = int(cursor.fetchone()[0])
                if table_count != 23:
                    raise FoundationError("migration_table_count_mismatch")
        except psycopg2.Error as exc:
            raise FoundationError("market_migration_database_failure") from exc
    finally:
        connection.close()
    print(safe_json({"status": "applied", "version": 1, "table_count": 23}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    service = commands.add_parser(
        "service", help="run one market role; live authority remains stage-gated"
    )
    service.add_argument("--role", choices=sorted(ROLES), required=True)
    health = commands.add_parser("healthcheck", help="validate durable role health")
    health.add_argument("--role", choices=sorted(ROLES), required=True)
    health.add_argument("--max-age-seconds", type=float, default=30.0)
    migrate = commands.add_parser("migrate", help="apply the isolated archive migration")
    migrate.add_argument("--migration", type=Path, default=DEFAULT_MIGRATION)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "service":
            return run_service(args.role)
        if args.command == "healthcheck":
            if not 1 <= args.max_age_seconds <= 300:
                parser.error("--max-age-seconds must be between 1 and 300")
            return run_healthcheck(args.role, args.max_age_seconds)
        if args.command == "migrate":
            return run_migration(args.migration)
    except FoundationError as exc:
        print(safe_json({"status": "fail", "reason_code": str(exc)}), file=sys.stderr)
        return LIVE_NOT_IMPLEMENTED_EXIT
    except (OSError, ValueError, sqlite3.Error):
        print(
            safe_json({"status": "fail", "reason_code": "runtime_dependency_failure"}),
            file=sys.stderr,
        )
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
