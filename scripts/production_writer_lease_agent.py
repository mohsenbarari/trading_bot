#!/usr/bin/env python3
"""Host-level Writer Witness lease agent for the legacy 2c08 production app.

The normal FI writer starts only ``app`` and ``sync_worker``.  The passive
Bot-FI observer starts nothing and may stop only its explicitly configured
``bot``/``sync_worker`` scope.  WA-IR is deliberately different: after a
fresh, hash-bound restore receipt it can stop only the validated no-network
snapshot database container and start its own isolated ``db``/``redis``/``app``
Compose project.  It never starts a legacy direct-sync worker, and a failed
promotion returns the verified snapshot DB to its no-network warm state.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import time
from typing import Any, Iterator, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.production_writer_lease import LEASE_SCHEMA, WEBAPP_SITES, load_production_writer_lease
from core.production_snapshot_promotion import (
    SnapshotPromotionError,
    build_promotion_proof,
    canonical_json_bytes,
    loads_strict_receipt,
    parse_restore_receipt,
    validate_promotion_proof,
)
from scripts.manage_webapp_ir_release_provenance import (  # noqa: E402
    ReleaseProvenanceError,
    load_installed_release_receipt,
)


AGENT_SCHEMA = "production-writer-lease-agent-v1"
WITNESS_AUTH_VERSION = 1
WITNESS_TRANSITION_PATH = "/v1/writer-witness/transitions"
WITNESS_STATUS_PATH = "/v1/writer-witness/status"
MAX_FILE_BYTES = 128 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
DOCKER_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DOCKER_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
DOCKER_FULL_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
DOCKER_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DOCKER_IMAGE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,511}$")
WA_IR_PROMOTION_PROJECT_NAME = "trading_bot_wa_ir_promoted_2c08"
WA_IR_PROMOTION_PROFILE = "promoted"
WA_IR_EMERGENCY_LEASE_DURATION_SECONDS = 60
WA_IR_EMERGENCY_SAFETY_MARGIN_SECONDS = 15
WA_IR_EMERGENCY_RENEW_INTERVAL_SECONDS = 10
WA_IR_PROMOTION_HEALTH_TIMEOUT_SECONDS = 20
WA_IR_PROMOTION_HEALTH_POLL_SECONDS = 2
WA_IR_PROMOTION_LOCK_TIMEOUT_SECONDS = 30
WA_IR_APPLICATION_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
WA_IR_PROMOTED_COMPOSE_FILE = (
    REPO_ROOT / "deploy/production/docker-compose.webapp-ir-promoted-2c08.yml"
).resolve()
WA_IR_RUNTIME_BINDING_SCHEMA = "gold-trade-wa-ir-promoted-runtime-binding-v1"
WA_IR_RUNTIME_BINDING_FIELDS = frozenset(
    {
        "schema",
        "promotion_proof_sha256",
        "snapshot_id",
        "source_generation",
        "release_sha",
        "snapshot_restore_receipt_sha256",
        "snapshot_stage_receipt_sha256",
        "epoch",
        "lease_id",
        "containers",
        "binding_sha256",
    }
)
WA_IR_RUNTIME_CONTAINER_BINDING_FIELDS = frozenset(
    {
        "container_id",
        "image",
        "image_id",
        "labels_sha256",
        "volume_names",
        "restart_policy",
    }
)
WA_IR_RUNTIME_IMAGE_ENV = {
    "db": "WA_IR_POSTGRES_IMAGE",
    "redis": "WA_IR_REDIS_IMAGE",
    "app": "WA_IR_APP_IMAGE",
}


class ProductionWriterLeaseAgentError(RuntimeError):
    pass


class WriterWitnessUnavailable(ProductionWriterLeaseAgentError):
    """A transport/server outage that may retain a still-safe local term."""


@dataclass(frozen=True)
class RuntimeConfig:
    compose_file: Path
    env_file: Path | None
    selection_env_file: Path | None
    services: tuple[str, ...]


@dataclass(frozen=True)
class WitnessConfig:
    base_url: str
    key_id: str
    site: str
    secret: str
    public_key: str
    ca_bundle: str | None
    timeout_seconds: float
    lease_duration_seconds: int
    safety_margin_seconds: int
    renew_interval_seconds: int


@dataclass(frozen=True)
class ReleaseProvenanceConfig:
    receipt_path: Path
    application_release_root: Path


@dataclass(frozen=True)
class AgentConfig:
    mode: str
    site: str
    lease_file: Path | None
    runtime: RuntimeConfig
    witness: WitnessConfig
    release_provenance: ReleaseProvenanceConfig | None


@dataclass(frozen=True)
class PromotionRuntimeSelection:
    db_volume: str
    uploads_volume: str
    audit_volume: str
    db_container: str
    compose_project: str
    redis_volume: str
    release_sha: str


@dataclass(frozen=True)
class PromotedRuntimeContainerBinding:
    container_id: str
    image: str
    image_id: str
    labels_sha256: str
    volume_names: tuple[str, ...]
    restart_policy: str


@dataclass(frozen=True)
class PromotedRuntimeBinding:
    promotion_proof_sha256: str
    snapshot_id: str
    source_generation: str
    release_sha: str
    snapshot_restore_receipt_sha256: str
    snapshot_stage_receipt_sha256: str
    epoch: int
    lease_id: str
    containers: Mapping[str, PromotedRuntimeContainerBinding]
    binding_sha256: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionWriterLeaseAgentError("agent input contains duplicate JSON keys")
        result[key] = value
    return result


def _secure_read(path: Path, *, label: str, max_size: int = MAX_FILE_BYTES) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionWriterLeaseAgentError(f"cannot securely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > max_size
        ):
            raise ProductionWriterLeaseAgentError(f"{label} is not an owner-only regular file")
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_size:
            raise ProductionWriterLeaseAgentError(f"{label} is oversized")
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ProductionWriterLeaseAgentError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _secure_public_read(path: Path, *, label: str, max_size: int = MAX_FILE_BYTES) -> bytes:
    """Read a root-owned public trust file without requiring secrecy.

    CA bundles are normally world-readable but must never be writable by a
    non-owner.  Secrets/configuration continue to use ``_secure_read``.
    """

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionWriterLeaseAgentError(f"cannot securely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > max_size
        ):
            raise ProductionWriterLeaseAgentError(f"{label} is not an owner-controlled regular file")
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_size:
            raise ProductionWriterLeaseAgentError(f"{label} is oversized")
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ProductionWriterLeaseAgentError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _secure_text(path: Path, *, label: str, max_size: int = MAX_FILE_BYTES) -> str:
    try:
        return _secure_read(path, label=label, max_size=max_size).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProductionWriterLeaseAgentError(f"{label} is not UTF-8") from exc


def _absolute(value: Any, *, label: str) -> Path:
    text = str(value or "")
    path = Path(text)
    if not PATH_RE.fullmatch(text) or ".." in path.parts:
        raise ProductionWriterLeaseAgentError(f"{label} must be an absolute closed path")
    return path


def _load_ir_release_provenance(value: Any) -> ReleaseProvenanceConfig:
    fields = {"receipt", "application_release_sha", "application_release_root"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ProductionWriterLeaseAgentError("WebApp-IR release provenance config is invalid")
    receipt_path = _absolute(value.get("receipt"), label="WebApp-IR release provenance receipt")
    application_release_root = _absolute(
        value.get("application_release_root"),
        label="WebApp-IR application release root",
    )
    if value.get("application_release_sha") != WA_IR_APPLICATION_RELEASE_SHA:
        raise ProductionWriterLeaseAgentError("WebApp-IR application release SHA is not the fixed legacy 2c08 release")
    return ReleaseProvenanceConfig(
        receipt_path=receipt_path,
        application_release_root=application_release_root,
    )


def _read_promotion_application_values(path: Path) -> dict[str, str]:
    """Extract only the two Compose inputs that choose the application source."""

    required = {"RELEASE_SHA", "WA_IR_APPLICATION_RELEASE_ROOT"}
    values: dict[str, str] = {}
    for raw_line in _secure_text(
        path,
        label="WA-IR promotion runtime environment",
        max_size=MAX_FILE_BYTES,
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in required and key != key.strip():
            raise ProductionWriterLeaseAgentError("WA-IR promotion application runtime value is invalid")
        if key not in required:
            continue
        if key in values:
            raise ProductionWriterLeaseAgentError("WA-IR promotion application runtime value is duplicated")
        if not value or value != value.strip() or "$" in value:
            raise ProductionWriterLeaseAgentError("WA-IR promotion application runtime value is invalid")
        values[key] = value
    if set(values) != required:
        raise ProductionWriterLeaseAgentError("WA-IR promotion application runtime values are incomplete")
    return values


def _verify_ir_runtime_application_binding(config: AgentConfig) -> None:
    """Reject mutable Compose inputs unless they equal the receipt identity."""

    if config.mode != "writer" or config.site != "webapp_ir":
        return
    if config.release_provenance is None or config.runtime.env_file is None:
        raise ProductionWriterLeaseAgentError("WebApp-IR writer requires receipt-bound application provenance")
    try:
        installed = load_installed_release_receipt(config.release_provenance.receipt_path)
    except ReleaseProvenanceError as exc:
        raise ProductionWriterLeaseAgentError(f"WebApp-IR release provenance receipt is invalid: {exc}") from exc
    application = installed["application"]
    if (
        application["release_sha"] != WA_IR_APPLICATION_RELEASE_SHA
        or application["release_root"] != str(config.release_provenance.application_release_root)
    ):
        raise ProductionWriterLeaseAgentError(
            "WebApp-IR release provenance receipt does not bind this application release root"
        )
    values = _read_promotion_application_values(config.runtime.env_file)
    if (
        values["RELEASE_SHA"] != WA_IR_APPLICATION_RELEASE_SHA
        or values["WA_IR_APPLICATION_RELEASE_ROOT"] != str(config.release_provenance.application_release_root)
    ):
        raise ProductionWriterLeaseAgentError(
            "WA-IR promotion runtime environment does not bind the receipt application release"
        )


def _verify_ir_selection_environment(config: AgentConfig) -> None:
    """The generated candidate selector must not override any app input."""

    if config.mode != "writer" or config.site != "webapp_ir" or config.runtime.selection_env_file is None:
        return
    path = config.runtime.selection_env_file
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProductionWriterLeaseAgentError("cannot inspect WA-IR runtime selection environment") from exc
    allowed = {
        "WA_IR_CANDIDATE_AUDIT_VOLUME",
        "WA_IR_CANDIDATE_DB_VOLUME",
        "WA_IR_CANDIDATE_UPLOADS_VOLUME",
        "WA_IR_REDIS_VOLUME_NAME",
    }
    seen: set[str] = set()
    for raw_line in _secure_text(
        path,
        label="WA-IR runtime selection environment",
        max_size=MAX_FILE_BYTES,
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ProductionWriterLeaseAgentError("WA-IR runtime selection environment is malformed")
        key, value = line.split("=", 1)
        if key not in allowed or key in seen or not value or value != value.strip() or "$" in value:
            raise ProductionWriterLeaseAgentError("WA-IR runtime selection environment is not an exact generated selector")
        seen.add(key)
    if seen != allowed:
        raise ProductionWriterLeaseAgentError("WA-IR runtime selection environment is not an exact generated selector")


def _load_config(path: Path) -> AgentConfig:
    if os.geteuid() != 0:
        raise ProductionWriterLeaseAgentError("writer lease agent must run as root")
    try:
        raw = json.loads(_secure_text(path, label="writer lease agent config"), object_pairs_hook=_strict_object)
    except ProductionWriterLeaseAgentError:
        raise
    except Exception as exc:
        raise ProductionWriterLeaseAgentError("writer lease agent config is invalid") from exc
    base_fields = {"schema", "mode", "site", "lease_file", "runtime", "witness"}
    if not isinstance(raw, dict) or not base_fields.issubset(raw) or set(raw) - (base_fields | {"release_provenance"}) or raw.get("schema") != AGENT_SCHEMA:
        raise ProductionWriterLeaseAgentError("writer lease agent config schema is invalid")
    mode = raw.get("mode")
    if mode not in {"writer", "observer"}:
        raise ProductionWriterLeaseAgentError("writer lease agent mode is invalid")
    site = str(raw.get("site") or "").strip().lower()
    if site not in WEBAPP_SITES:
        raise ProductionWriterLeaseAgentError("writer lease agent site is invalid")
    is_ir_writer = mode == "writer" and site == "webapp_ir"
    expected_fields = base_fields | ({"release_provenance"} if is_ir_writer else set())
    if set(raw) != expected_fields:
        raise ProductionWriterLeaseAgentError("writer lease agent config schema is invalid")
    release_provenance = _load_ir_release_provenance(raw["release_provenance"]) if is_ir_writer else None
    lease_value = raw.get("lease_file")
    if mode == "writer":
        lease_file: Path | None = _absolute(lease_value, label="lease file")
    else:
        # Bot-FI is a passive observer.  It must never receive a copied lease
        # through a second transport path: a current authenticated Witness
        # status is the sole authority, and any unavailable/mismatched status
        # fences only the configured bot/sync services.
        if lease_value is not None:
            raise ProductionWriterLeaseAgentError("observer mode must not configure a local lease file")
        if site != "webapp_fi":
            raise ProductionWriterLeaseAgentError("observer mode is allowed only for Bot-FI")
        lease_file = None

    runtime_raw = raw.get("runtime")
    runtime_fields = {"compose_file", "env_file", "selection_env_file", "services"}
    if not isinstance(runtime_raw, dict) or set(runtime_raw) != runtime_fields:
        raise ProductionWriterLeaseAgentError("managed runtime config is invalid")
    compose_file = _absolute(runtime_raw.get("compose_file"), label="compose file")
    env_value = runtime_raw.get("env_file")
    env_file = _absolute(env_value, label="runtime env file") if env_value is not None else None
    selection_value = runtime_raw.get("selection_env_file")
    selection_env_file = (
        _absolute(selection_value, label="runtime selection env file")
        if selection_value is not None
        else None
    )
    services_raw = runtime_raw.get("services")
    if (
        not isinstance(services_raw, list)
        or not services_raw
        or not all(isinstance(service, str) for service in services_raw)
        or len(set(services_raw)) != len(services_raw)
    ):
        raise ProductionWriterLeaseAgentError(
            "managed runtime contains an unsupported writable service scope"
        )
    if mode == "writer" and site == "webapp_fi" and services_raw != ["app", "sync_worker"]:
        raise ProductionWriterLeaseAgentError("WebApp-FI writer must manage only app and sync_worker")
    if mode == "writer" and site == "webapp_ir" and services_raw != ["db", "redis", "app"]:
        raise ProductionWriterLeaseAgentError(
            "WebApp-IR writer must manage only the isolated db, redis, and app stack"
        )
    if mode == "observer" and services_raw not in (
        ["bot", "sync_worker"],
        ["app", "bot", "sync_worker"],
    ):
        raise ProductionWriterLeaseAgentError(
            "observer mode must manage bot and sync_worker, with app only when writable"
        )
    if mode == "observer" and selection_env_file is not None:
        raise ProductionWriterLeaseAgentError("observer mode must not configure a runtime selection env file")
    if mode == "writer" and site == "webapp_ir" and selection_env_file is None:
        raise ProductionWriterLeaseAgentError(
            "WebApp-IR writer mode requires a runtime selection env file"
        )
    if mode == "writer" and site == "webapp_ir":
        if compose_file.resolve() != WA_IR_PROMOTED_COMPOSE_FILE:
            raise ProductionWriterLeaseAgentError(
                "WebApp-IR writer must use the pinned isolated promotion compose file"
            )
        if env_file is None:
            raise ProductionWriterLeaseAgentError(
                "WebApp-IR writer requires its root-only promotion environment"
            )
    runtime = RuntimeConfig(
        compose_file=compose_file,
        env_file=env_file,
        selection_env_file=selection_env_file,
        services=tuple(services_raw),
    )

    witness_raw = raw.get("witness")
    witness_fields = {
        "url", "key_id", "secret_file", "public_key_file", "ca_bundle",
        "timeout_seconds", "lease_duration_seconds", "safety_margin_seconds",
        "renew_interval_seconds",
    }
    if not isinstance(witness_raw, dict) or set(witness_raw) != witness_fields:
        raise ProductionWriterLeaseAgentError("Witness config fields are invalid")
    base_url = str(witness_raw.get("url") or "").rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ProductionWriterLeaseAgentError("Witness URL must be a root HTTPS URL")
    key_id = str(witness_raw.get("key_id") or "").strip()
    if not key_id or len(key_id) > 64:
        raise ProductionWriterLeaseAgentError("Witness client key id is invalid")
    secret = _secure_text(
        _absolute(witness_raw.get("secret_file"), label="Witness secret file"),
        label="Witness secret",
        max_size=16 * 1024,
    ).strip()
    if len(secret.encode("utf-8")) < 32:
        raise ProductionWriterLeaseAgentError("Witness secret is invalid")
    public_key = _secure_text(
        _absolute(witness_raw.get("public_key_file"), label="Witness public key file"),
        label="Witness public key",
        max_size=16 * 1024,
    ).strip()
    _decode_base64(public_key, expected_length=32, label="Witness public key")
    ca_bundle_raw = witness_raw.get("ca_bundle")
    ca_bundle = str(_absolute(ca_bundle_raw, label="Witness CA bundle")) if ca_bundle_raw else None
    if ca_bundle:
        _secure_public_read(Path(ca_bundle), label="Witness CA bundle", max_size=1024 * 1024)
    timeout = witness_raw.get("timeout_seconds")
    duration = witness_raw.get("lease_duration_seconds")
    margin = witness_raw.get("safety_margin_seconds")
    interval = witness_raw.get("renew_interval_seconds")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0.1 <= float(timeout) <= 10
        or type(duration) is not int
        or type(margin) is not int
        or type(interval) is not int
        or duration < 30
        or margin < 5
        or interval < 1
        or interval + margin >= duration
    ):
        raise ProductionWriterLeaseAgentError("Witness lease timing is unsafe")
    if mode == "writer" and site == "webapp_ir" and (
        duration != WA_IR_EMERGENCY_LEASE_DURATION_SECONDS
        or margin != WA_IR_EMERGENCY_SAFETY_MARGIN_SECONDS
        or interval != WA_IR_EMERGENCY_RENEW_INTERVAL_SECONDS
    ):
        raise ProductionWriterLeaseAgentError(
            "WebApp-IR must use the pinned 60/15/10 emergency Witness lease timing"
        )
    config = AgentConfig(
        mode=mode,
        site=site,
        lease_file=lease_file,
        runtime=runtime,
        witness=WitnessConfig(
            base_url=base_url,
            key_id=key_id,
            site=site,
            secret=secret,
            public_key=public_key,
            ca_bundle=ca_bundle,
            timeout_seconds=float(timeout),
            lease_duration_seconds=duration,
            safety_margin_seconds=margin,
            renew_interval_seconds=interval,
        ),
        release_provenance=release_provenance,
    )
    _verify_ir_runtime_application_binding(config)
    _verify_ir_selection_environment(config)
    return config


def _decode_base64(value: str, *, expected_length: int, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ProductionWriterLeaseAgentError(f"{label} is not valid base64") from exc
    if len(decoded) != expected_length:
        raise ProductionWriterLeaseAgentError(f"{label} has an invalid length")
    return decoded


def _canonical_request(
    *, method: str, path: str, timestamp: int, request_id: str, site: str, body: bytes
) -> bytes:
    return "\n".join(
        (
            f"writer-witness-auth-v{WITNESS_AUTH_VERSION}",
            method.upper(),
            path,
            str(timestamp),
            request_id,
            site,
            hashlib.sha256(body).hexdigest(),
        )
    ).encode("utf-8")


def _headers(config: WitnessConfig, *, method: str, path: str, body: bytes, request_id: str) -> dict[str, str]:
    if not request_id or len(request_id) > 64:
        raise ProductionWriterLeaseAgentError("Witness request id is invalid")
    timestamp = int(datetime.now(timezone.utc).timestamp())
    signature = hmac.new(
        config.secret.encode("utf-8"),
        _canonical_request(
            method=method,
            path=path,
            timestamp=timestamp,
            request_id=request_id,
            site=config.site,
            body=body,
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Writer-Witness-Key-Id": config.key_id,
        "X-Writer-Witness-Site": config.site,
        "X-Writer-Witness-Timestamp": str(timestamp),
        "X-Writer-Witness-Request-Id": request_id,
        "X-Writer-Witness-Signature": signature,
    }


def _ssl_context(config: WitnessConfig) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=config.ca_bundle) if config.ca_bundle else ssl.create_default_context()


def _request_json(
    config: WitnessConfig,
    *,
    method: str,
    path: str,
    request_id: str,
    body: bytes = b"",
) -> tuple[int, dict[str, Any]]:
    request = urlrequest.Request(
        config.base_url + path,
        data=body if method == "POST" else None,
        headers=_headers(config, method=method, path=path, body=body, request_id=request_id),
        method=method,
    )
    try:
        with urlrequest.urlopen(request, timeout=config.timeout_seconds, context=_ssl_context(config)) as response:
            status = response.status
            raw = response.read(MAX_FILE_BYTES)
    except urlerror.HTTPError as exc:
        status = exc.code
        raw = exc.read(MAX_FILE_BYTES)
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise WriterWitnessUnavailable("Writer Witness is unreachable") from exc
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except Exception as exc:
        raise ProductionWriterLeaseAgentError("Writer Witness returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ProductionWriterLeaseAgentError("Writer Witness returned a non-object response")
    return status, payload


def _status(config: WitnessConfig, *, request_id: str) -> dict[str, Any]:
    status, payload = _request_json(
        config,
        method="GET",
        path=WITNESS_STATUS_PATH,
        request_id=request_id,
    )
    expected = {"contract_version", "accepted", "request_id", "witness_time", "state"}
    if status >= 500:
        raise WriterWitnessUnavailable("Writer Witness status is temporarily unavailable")
    if (
        status != 200
        or set(payload) != expected
        or payload.get("contract_version") != 1
        or payload.get("accepted") is not True
        or payload.get("request_id") != request_id
        or not isinstance(payload.get("state"), dict)
    ):
        raise ProductionWriterLeaseAgentError("Writer Witness status is invalid")
    return payload


def _parse_time(value: Any, *, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProductionWriterLeaseAgentError(f"Witness {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionWriterLeaseAgentError(f"Witness {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ProductionWriterLeaseAgentError(f"Witness {field} lacks timezone")
    return parsed.astimezone(timezone.utc)


def _state(payload: dict[str, Any]) -> tuple[str | None, int, str | None, datetime | None]:
    state = payload.get("state")
    expected = {
        "holder_site", "writer_epoch", "lease_id", "lease_status", "issued_at", "expires_at", "transition_id"
    }
    if not isinstance(state, dict) or set(state) != expected:
        raise ProductionWriterLeaseAgentError("Writer Witness state is invalid")
    holder = state.get("holder_site")
    lease_id = state.get("lease_id")
    epoch = state.get("writer_epoch")
    lease_status = state.get("lease_status")
    if holder is not None and holder not in WEBAPP_SITES:
        raise ProductionWriterLeaseAgentError("Writer Witness holder site is invalid")
    if lease_id is not None and (not isinstance(lease_id, str) or not lease_id):
        raise ProductionWriterLeaseAgentError("Writer Witness lease id is invalid")
    if type(epoch) is not int or epoch < 0:
        raise ProductionWriterLeaseAgentError("Writer Witness epoch is invalid")
    issued_at = _parse_time(state.get("issued_at"), field="lease issue time")
    expires_at = _parse_time(state.get("expires_at"), field="lease expiry")
    transition_id = state.get("transition_id")
    if not isinstance(transition_id, str) or not transition_id or len(transition_id) > 128:
        raise ProductionWriterLeaseAgentError("Writer Witness transition id is invalid")
    if lease_status == "vacant":
        if holder is not None or lease_id is not None or issued_at is not None or expires_at is not None:
            raise ProductionWriterLeaseAgentError("Writer Witness vacant state is inconsistent")
    elif lease_status == "leased" or lease_status == "draining":
        if holder is None or lease_id is None or issued_at is None or expires_at is None or expires_at <= issued_at:
            raise ProductionWriterLeaseAgentError("Writer Witness lease state is inconsistent")
    else:
        raise ProductionWriterLeaseAgentError("Writer Witness lease status is invalid")
    return holder, epoch, lease_id, expires_at


def _transition(
    config: WitnessConfig,
    *,
    action: str,
    expected_epoch: int,
    expected_lease_id: str | None,
    request_id: str,
    reason: str,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "contract_version": 1,
            "action": action,
            "expected_epoch": expected_epoch,
            "expected_lease_id": expected_lease_id,
            "request_id": request_id,
            "reason": reason,
            "lease_duration_seconds": config.lease_duration_seconds,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    status, payload = _request_json(
        config,
        method="POST",
        path=WITNESS_TRANSITION_PATH,
        request_id=request_id,
        body=body,
    )
    if status >= 500:
        raise WriterWitnessUnavailable("Writer Witness transition is temporarily unavailable")
    if status != 200 or payload.get("contract_version") != 1 or payload.get("accepted") is not True:
        raise ProductionWriterLeaseAgentError("Writer Witness rejected the transition")
    if payload.get("request_id") != request_id or not isinstance(payload.get("state"), dict):
        raise ProductionWriterLeaseAgentError("Writer Witness transition response is invalid")
    return payload


def _validate_proof(
    proof: Any,
    *,
    config: WitnessConfig,
    expected_epoch: int,
) -> dict[str, Any]:
    """Verify the independent Witness signature before writing local authority."""

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise ProductionWriterLeaseAgentError("cryptography is required for Witness proof validation") from exc
    fields = {
        "version", "authority", "holder_site", "writer_epoch", "lease_id",
        "issued_at", "expires_at", "witness_transition_id", "signature",
    }
    if not isinstance(proof, dict) or set(proof) != fields:
        raise ProductionWriterLeaseAgentError("Witness proof fields are invalid")
    if (
        proof.get("version") != 1
        or proof.get("authority") != "webapp"
        or proof.get("holder_site") != config.site
        or proof.get("writer_epoch") != expected_epoch
    ):
        raise ProductionWriterLeaseAgentError("Witness proof target term is invalid")
    for field in ("lease_id", "witness_transition_id"):
        value = proof.get(field)
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
            raise ProductionWriterLeaseAgentError(f"Witness proof {field} is invalid")
    issued_at = _parse_time(proof.get("issued_at"), field="proof issue time")
    expires_at = _parse_time(proof.get("expires_at"), field="proof expiry")
    if issued_at is None or expires_at is None or expires_at <= issued_at:
        raise ProductionWriterLeaseAgentError("Witness proof lifetime is invalid")
    now = datetime.now(timezone.utc)
    if issued_at > now + timedelta(seconds=5):
        raise ProductionWriterLeaseAgentError("Witness proof issue time is in the future")
    if expires_at - issued_at > timedelta(seconds=config.lease_duration_seconds):
        raise ProductionWriterLeaseAgentError(
            "Witness proof lifetime exceeds configured lease duration"
        )
    if expires_at <= now + timedelta(seconds=config.safety_margin_seconds):
        raise ProductionWriterLeaseAgentError("Witness proof is too close to expiry")
    unsigned_fields = (
        "version", "authority", "holder_site", "writer_epoch", "lease_id",
        "issued_at", "expires_at", "witness_transition_id",
    )
    unsigned = {field: proof[field] for field in unsigned_fields}
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = _decode_base64(str(proof.get("signature") or ""), expected_length=64, label="Witness proof signature")
    try:
        Ed25519PublicKey.from_public_bytes(_decode_base64(config.public_key, expected_length=32, label="Witness public key")).verify(signature, encoded)
    except InvalidSignature as exc:
        raise ProductionWriterLeaseAgentError("Witness proof signature is invalid") from exc
    return dict(proof)


def _write_lease(path: Path, *, proof: dict[str, Any]) -> None:
    payload = {
        "schema": LEASE_SCHEMA,
        "holder_site": proof["holder_site"],
        "writer_epoch": proof["writer_epoch"],
        "lease_id": proof["lease_id"],
        "issued_at": proof["issued_at"],
        "expires_at": proof["expires_at"],
        "witness_transition_id": proof["witness_transition_id"],
        "proof_sha256": hashlib.sha256(
            json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary_name = f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    descriptor = -1
    try:
        metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ProductionWriterLeaseAgentError("lease directory is not owner controlled")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise ProductionWriterLeaseAgentError("lease write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def _write_new_json(path: Path, *, payload: dict[str, Any], label: str) -> None:
    encoded = canonical_json_bytes(payload) + b"\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary_name = f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    descriptor = -1
    try:
        metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ProductionWriterLeaseAgentError(f"{label} directory is not owner controlled")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise ProductionWriterLeaseAgentError(f"{label} write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ProductionWriterLeaseAgentError(f"{label} already exists") from exc
        os.unlink(temporary_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


@contextmanager
def _owner_lock(
    path: Path,
    *,
    label: str,
    nonblocking: bool,
    timeout_seconds: int | None = None,
) -> Iterator[bool]:
    """Take an advisory lock only inside an owner-controlled directory."""

    safe_path = _absolute(str(path), label=label)
    parent = safe_path.parent
    try:
        directory_fd = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ProductionWriterLeaseAgentError(f"cannot open {label} directory") from exc
    descriptor = -1
    try:
        metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ProductionWriterLeaseAgentError(f"{label} directory is not owner controlled")
        descriptor = os.open(
            safe_path.name,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        lock_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(lock_metadata.st_mode) & 0o077
            or lock_metadata.st_nlink != 1
        ):
            raise ProductionWriterLeaseAgentError(f"{label} is not an owner-only regular file")
        deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if nonblocking:
                    yield False
                    return
                if deadline is not None and time.monotonic() >= deadline:
                    raise ProductionWriterLeaseAgentError(f"{label} is busy")
                time.sleep(0.1)
                continue
            break
        yield True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


@contextmanager
def _ir_writer_transition_lock(config: AgentConfig, *, nonblocking: bool) -> Iterator[bool]:
    if config.mode != "writer" or config.site != "webapp_ir":
        yield True
        return
    lease_file = _writer_lease_file(config)
    with _owner_lock(
        lease_file.parent / "writer-transition.lock",
        label="WebApp-IR writer transition lock",
        nonblocking=nonblocking,
        timeout_seconds=None if nonblocking else WA_IR_PROMOTION_LOCK_TIMEOUT_SECONDS,
    ) as acquired:
        yield acquired


@contextmanager
def _standby_refresh_transition_lock(active_snapshot: Path) -> Iterator[None]:
    safe_pointer = _absolute(str(active_snapshot), label="active snapshot pointer")
    if safe_pointer.name != "active-snapshot.json":
        raise ProductionWriterLeaseAgentError("active snapshot pointer has an unexpected name")
    with _owner_lock(
        safe_pointer.parent / "refresh.lock",
        label="WA-IR snapshot refresh lock",
        nonblocking=False,
        timeout_seconds=WA_IR_PROMOTION_LOCK_TIMEOUT_SECONDS,
    ) as acquired:
        if not acquired:  # Defensive: blocking mode never yields False.
            raise ProductionWriterLeaseAgentError("WA-IR snapshot refresh lock is busy")
        yield


def _runtime_environment() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _run_runtime_command(
    command: list[str],
    *,
    label: str,
    timeout: int,
    capture_stdout: bool = False,
) -> str:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=capture_stdout,
            check=False,
            timeout=timeout,
            env=_runtime_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProductionWriterLeaseAgentError(f"{label} command failed") from exc
    if result.returncode != 0:
        raise ProductionWriterLeaseAgentError(f"{label} command was rejected")
    return result.stdout if capture_stdout and isinstance(result.stdout, str) else ""


def _compose_prefix(config: AgentConfig) -> list[str]:
    command = ["/usr/bin/docker", "compose"]
    if config.mode == "writer" and config.site == "webapp_ir":
        # Re-read both root-only inputs immediately before every Docker Compose
        # operation.  A stale same-named application root or a later env edit
        # must not reach the runtime merely because startup validation passed.
        _verify_ir_runtime_application_binding(config)
        _verify_ir_selection_environment(config)
        # Do not inherit the standby compose project from its environment.
        # Promotion can touch only this fixed, newly introduced project.
        command.extend(["--project-name", WA_IR_PROMOTION_PROJECT_NAME, "--profile", WA_IR_PROMOTION_PROFILE])
    command.extend(["-f", str(config.runtime.compose_file)])
    if config.runtime.env_file is not None:
        command.extend(["--env-file", str(config.runtime.env_file)])
    if config.runtime.selection_env_file is not None:
        command.extend(["--env-file", str(config.runtime.selection_env_file)])
    return command


def _compose_capture(config: AgentConfig, *, arguments: list[str], label: str, timeout: int = 30) -> str:
    return _run_runtime_command(
        [*_compose_prefix(config), *arguments],
        label=label,
        timeout=timeout,
        capture_stdout=True,
    )


def _compose(config: AgentConfig, *, action: str) -> None:
    if action not in {"start", "stop"}:
        raise ProductionWriterLeaseAgentError("managed runtime action is invalid")
    command = _compose_prefix(config)
    is_ir_promotion = config.mode == "writer" and config.site == "webapp_ir"
    if action == "start":
        if is_ir_promotion:
            # The profile has no sync_worker and Compose must start DB/Redis
            # dependencies for the exact candidate volumes.  It may not pull,
            # build, or recreate any existing container.
            command.extend(["up", "-d", "--no-recreate", *config.runtime.services])
        else:
            command.extend(["up", "-d", "--no-deps", "--no-recreate", *config.runtime.services])
    else:
        command.extend(["stop", "--timeout", "15", *config.runtime.services])
    _run_runtime_command(command, label="managed runtime", timeout=90)


def _wait_for_ir_app_health(config: AgentConfig) -> None:
    """Require the newly promoted local app to become healthy before routing."""

    if config.mode != "writer" or config.site != "webapp_ir":
        return
    deadline = time.monotonic() + WA_IR_PROMOTION_HEALTH_TIMEOUT_SECONDS
    last_state = "not-created"
    while True:
        container_id = _compose_capture(
            config,
            arguments=["ps", "--quiet", "app"],
            label="promoted app lookup",
        ).strip()
        if container_id:
            lines = container_id.splitlines()
            if len(lines) != 1 or not DOCKER_CONTAINER_ID_RE.fullmatch(lines[0]):
                raise ProductionWriterLeaseAgentError("promoted app lookup returned an invalid container id")
            try:
                last_state = _run_runtime_command(
                    [
                        "/usr/bin/docker",
                        "inspect",
                        "--format",
                        "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}",
                        lines[0],
                    ],
                    label="promoted app health",
                    timeout=30,
                    capture_stdout=True,
                ).strip()
            except ProductionWriterLeaseAgentError:
                last_state = "inspection-unavailable"
            else:
                if last_state == "running|healthy":
                    return
                if last_state.split("|", 1)[0] in {"dead", "exited", "removing"} or last_state.endswith("|unhealthy"):
                    raise ProductionWriterLeaseAgentError("promoted app became unhealthy before routing")
        if time.monotonic() >= deadline:
            raise ProductionWriterLeaseAgentError(
                f"promoted app did not become healthy before routing ({last_state})"
            )
        time.sleep(WA_IR_PROMOTION_HEALTH_POLL_SECONDS)


def _operation_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ProductionWriterLeaseAgentError("operation id must be a UUID") from exc


def _request_id(operation_id: str, action: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"production-writer-lease:{operation_id}:{action}"))


def _emit_event(event: str, **fields: Any) -> None:
    """Emit concise non-secret state for systemd journal collection."""

    print(
        json.dumps({"event": event, **fields}, ensure_ascii=True, sort_keys=True),
        file=sys.stderr,
        flush=True,
    )


def _best_effort_stop(config: AgentConfig) -> None:
    try:
        _compose(config, action="stop")
    except ProductionWriterLeaseAgentError:
        pass


def _local_lease_safety(config: AgentConfig) -> tuple[Any, float]:
    if config.lease_file is None:
        raise ProductionWriterLeaseAgentError("this operation requires a local writer lease")
    lease = load_production_writer_lease(config.lease_file)
    if lease.holder_site != config.site:
        raise ProductionWriterLeaseAgentError("local lease holder does not match configured site")
    remaining = (lease.expires_at - datetime.now(timezone.utc)).total_seconds()
    if remaining <= config.witness.safety_margin_seconds:
        raise ProductionWriterLeaseAgentError("local Writer Witness lease is stale or unsafe")
    return lease, remaining


def _require_writer_mode(config: AgentConfig) -> None:
    if config.mode != "writer":
        raise ProductionWriterLeaseAgentError("this command requires writer mode")


def _writer_lease_file(config: AgentConfig) -> Path:
    _require_writer_mode(config)
    if config.lease_file is None:
        raise ProductionWriterLeaseAgentError("writer mode requires a local lease file")
    return config.lease_file


def _observe_active_writer_term(config: AgentConfig) -> tuple[int, datetime]:
    """Confirm that the sole Writer Witness term is currently WebApp-FI.

    Bot-FI has no local writer authority and never receives a copied lease.
    It can only remain active after a fresh authenticated status check.  This
    avoids introducing an additional Object Storage replication path for a
    lease while fencing bot/sync immediately if Witness is unavailable.
    """

    status = _status(config.witness, request_id=str(uuid4()))
    holder, epoch, lease_id, expires_at = _state(status)
    if (
        status["state"].get("lease_status") != "leased"
        or holder != config.site
        or lease_id is None
        or expires_at is None
        or expires_at <= datetime.now(timezone.utc) + timedelta(seconds=config.witness.safety_margin_seconds)
    ):
        raise ProductionWriterLeaseAgentError("Writer Witness term is not active for this follower")
    return epoch, expires_at


def _acquire_proof(
    config: AgentConfig,
    *,
    operation_id: str,
    purpose: str,
    require_initial_vacant: bool = False,
    allow_live_local_recovery: bool = False,
    persist_lease: bool = True,
) -> dict[str, Any]:
    lease_file = _writer_lease_file(config)
    operation = _operation_uuid(operation_id)
    status = _status(config.witness, request_id=_request_id(operation, "status"))
    holder, epoch, lease_id, expires_at = _state(status)
    now = datetime.now(timezone.utc)
    if require_initial_vacant and (holder is not None or epoch != 0 or lease_id is not None or expires_at is not None):
        raise ProductionWriterLeaseAgentError("FI bootstrap is allowed only from an initial vacant Witness state")
    if expires_at is not None and expires_at > now:
        if (
            allow_live_local_recovery
            and holder == config.site
            and status["state"].get("lease_status") == "leased"
            and lease_id is not None
        ):
            recovery = _transition(
                config.witness,
                action="renew",
                expected_epoch=epoch,
                expected_lease_id=lease_id,
                request_id=_request_id(operation, "recovery-renew"),
                reason=f"production {purpose} recovery renewal {operation}",
            )
            proof = _validate_proof(
                recovery.get("proof"),
                config=config.witness,
                expected_epoch=epoch,
            )
            if proof.get("lease_id") != lease_id:
                raise ProductionWriterLeaseAgentError("recovery renewal changed the lease identity")
            if persist_lease:
                _write_lease(lease_file, proof=proof)
            return proof
        raise ProductionWriterLeaseAgentError("predecessor Writer Witness lease is still live")
    transition = _transition(
        config.witness,
        action="acquire",
        expected_epoch=epoch,
        expected_lease_id=lease_id,
        request_id=_request_id(operation, "acquire"),
        reason=f"production {purpose} writer transition {operation}",
    )
    proof = _validate_proof(
        transition.get("proof"),
        config=config.witness,
        expected_epoch=epoch + 1,
    )
    if persist_lease:
        _write_lease(lease_file, proof=proof)
    return proof


def _start_scoped_runtime(config: AgentConfig) -> None:
    try:
        _compose(config, action="start")
        _wait_for_ir_app_health(config)
    except ProductionWriterLeaseAgentError:
        _best_effort_stop(config)
        raise


def _acquire_proof_and_start(
    config: AgentConfig,
    *,
    operation_id: str,
    purpose: str,
    require_initial_vacant: bool = False,
    allow_live_local_recovery: bool = False,
) -> dict[str, Any]:
    proof = _acquire_proof(
        config,
        operation_id=operation_id,
        purpose=purpose,
        require_initial_vacant=require_initial_vacant,
        allow_live_local_recovery=allow_live_local_recovery,
    )
    _start_scoped_runtime(config)
    return proof


def bootstrap_fi_and_start(config: AgentConfig, *, operation_id: str) -> dict[str, Any]:
    """Acquire the initial normal FI term without a promotion receipt.

    This command exists only to establish the normal primary after the
    isolated FI stack and its watchdog have been installed.  Any IR promotion
    or return to FI must instead use a verified Object Storage restore receipt.
    """

    _require_writer_mode(config)
    if config.site != "webapp_fi":
        raise ProductionWriterLeaseAgentError("only WebApp-FI may bootstrap the normal writer term")
    proof = _acquire_proof_and_start(
        config,
        operation_id=operation_id,
        purpose="FI bootstrap",
        require_initial_vacant=True,
    )
    return {
        "status": "activated",
        "site": config.site,
        "writer_epoch": proof["writer_epoch"],
        "lease_expires_at": proof["expires_at"],
        "witness_proof_sha256": hashlib.sha256(canonical_json_bytes(proof)).hexdigest(),
    }


def _load_restore_receipt(path: Path) -> dict[str, Any]:
    safe_path = _absolute(str(path), label="snapshot restore receipt")
    raw = _secure_read(safe_path, label="snapshot restore receipt", max_size=MAX_FILE_BYTES)
    return loads_strict_receipt(raw)


def _docker_resource(value: Any, *, label: str, required_prefix: str) -> str:
    if not isinstance(value, str) or not DOCKER_RESOURCE_RE.fullmatch(value):
        raise ProductionWriterLeaseAgentError(f"{label} is invalid")
    if not value.startswith(required_prefix):
        raise ProductionWriterLeaseAgentError(f"{label} does not use the approved standby namespace")
    return value


def _load_promotion_runtime_selection(
    active_snapshot_path: Path,
    *,
    restore_receipt_path: Path,
    restore_receipt: dict[str, Any],
    snapshot: Any,
) -> PromotionRuntimeSelection:
    """Bind a fresh Witness receipt to the exact restored WA-IR candidate.

    The canonical receipt is deliberately non-secret and can be replaced as a
    newer snapshot arrives.  The separately root-owned active pointer is the
    only source of the Docker volume selection, so an old receipt can never
    start a newer, different candidate.
    """

    active_path = _absolute(str(active_snapshot_path), label="active snapshot pointer")
    payload = loads_strict_receipt(
        _secure_read(active_path, label="active snapshot pointer", max_size=MAX_FILE_BYTES)
    )
    if (
        payload.get("schema_version") != "gold-trade-snapshot-restore-receipt-v1"
        or payload.get("status") != "ready"
    ):
        raise ProductionWriterLeaseAgentError("active snapshot pointer is not a verified standby candidate")
    for field in (
        "source_site",
        "destination_site",
        "source_generation",
        "snapshot_id",
        "release_sha",
        "alembic_revision",
        "source_db_snapshot_started_at",
        "source_capture_completed_at",
        "published_at",
        "ready_at",
    ):
        if payload.get(field) != restore_receipt.get(field):
            raise ProductionWriterLeaseAgentError(
                f"active snapshot pointer does not bind the Witness receipt {field}"
            )
    audit = payload.get("audit")
    if not isinstance(audit, dict) or audit.get("status") != "verified":
        raise ProductionWriterLeaseAgentError("active snapshot pointer lacks verified audit evidence")
    binding = payload.get("witness_restore_receipt")
    binding_fields = {
        "path",
        "receipt_sha256",
        "stage_receipt_sha256",
        "source_generation",
        "snapshot_id",
    }
    if not isinstance(binding, dict) or set(binding) != binding_fields:
        raise ProductionWriterLeaseAgentError("active snapshot pointer lacks a valid Witness receipt binding")
    bound_path = _absolute(binding.get("path"), label="bound Witness restore receipt")
    expected_receipt_path = _absolute(str(restore_receipt_path), label="snapshot restore receipt")
    if bound_path != expected_receipt_path:
        raise ProductionWriterLeaseAgentError("active snapshot pointer binds a different Witness receipt path")
    if (
        binding.get("receipt_sha256") != snapshot.receipt_sha256
        or binding.get("stage_receipt_sha256") != snapshot.stage_receipt_sha256
        or binding.get("source_generation") != snapshot.source_generation
        or binding.get("snapshot_id") != snapshot.snapshot_id
    ):
        raise ProductionWriterLeaseAgentError("active snapshot pointer does not bind this fresh Witness receipt")
    candidate = payload.get("candidate")
    candidate_fields = {
        "generation",
        "db_volume",
        "uploads_volume",
        "audit_volume",
        "db_container",
        "compose_project",
    }
    if not isinstance(candidate, dict) or set(candidate) != candidate_fields:
        raise ProductionWriterLeaseAgentError("active snapshot candidate selection is invalid")
    generation = _docker_resource(
        candidate.get("generation"),
        label="active snapshot candidate generation",
        required_prefix="",
    )
    if generation != snapshot.snapshot_id:
        raise ProductionWriterLeaseAgentError("active snapshot candidate generation does not match its receipt")
    db_volume = _docker_resource(
        candidate.get("db_volume"),
        label="active snapshot database volume",
        required_prefix="trading_bot_wa_ir_pg_",
    )
    uploads_volume = _docker_resource(
        candidate.get("uploads_volume"),
        label="active snapshot uploads volume",
        required_prefix="trading_bot_wa_ir_uploads_",
    )
    audit_volume = _docker_resource(
        candidate.get("audit_volume"),
        label="active snapshot audit volume",
        required_prefix="trading_bot_wa_ir_audit_",
    )
    db_container = _docker_resource(
        candidate.get("db_container"),
        label="active snapshot database container",
        required_prefix="trading_bot_wa_ir_snapshot_db_",
    )
    compose_project = _docker_resource(
        candidate.get("compose_project"),
        label="active snapshot compose project",
        required_prefix="trading_bot_wa_ir_snapshot_",
    )
    redis_volume = _docker_resource(
        f"trading_bot_wa_ir_redis_{generation}",
        label="active snapshot Redis volume",
        required_prefix="trading_bot_wa_ir_redis_",
    )
    return PromotionRuntimeSelection(
        db_volume=db_volume,
        uploads_volume=uploads_volume,
        audit_volume=audit_volume,
        db_container=db_container,
        compose_project=compose_project,
        redis_volume=redis_volume,
        release_sha=snapshot.release_sha,
    )


def _write_promotion_runtime_selection(path: Path, *, selection: PromotionRuntimeSelection) -> None:
    """Atomically replace only the non-secret candidate Compose selection."""

    safe_path = _absolute(str(path), label="runtime selection env file")
    safe_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_fd = os.open(
        safe_path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary_name = f".{safe_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    descriptor = -1
    try:
        metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ProductionWriterLeaseAgentError("runtime selection directory is not owner controlled")
        try:
            existing = os.stat(safe_path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != os.geteuid()
            or stat.S_IMODE(existing.st_mode) & 0o077
            or existing.st_nlink != 1
        ):
            raise ProductionWriterLeaseAgentError("runtime selection file is not a root-only regular file")
        payload = (
            f"WA_IR_CANDIDATE_AUDIT_VOLUME={selection.audit_volume}\n"
            f"WA_IR_CANDIDATE_DB_VOLUME={selection.db_volume}\n"
            f"WA_IR_CANDIDATE_UPLOADS_VOLUME={selection.uploads_volume}\n"
            f"WA_IR_REDIS_VOLUME_NAME={selection.redis_volume}\n"
        ).encode("ascii")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ProductionWriterLeaseAgentError("runtime selection write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_name, safe_path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def _inspect_labels(container: str, *, label: str) -> dict[str, str]:
    raw = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{json .Config.Labels}}", container],
        label=label,
        timeout=30,
        capture_stdout=True,
    ).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProductionWriterLeaseAgentError(f"{label} labels are invalid") from exc
    if not isinstance(payload, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in payload.items()):
        raise ProductionWriterLeaseAgentError(f"{label} labels are invalid")
    return payload


def _inspect_mounts(container: str, *, label: str) -> list[dict[str, Any]]:
    raw = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{json .Mounts}}", container],
        label=label,
        timeout=30,
        capture_stdout=True,
    ).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProductionWriterLeaseAgentError(f"{label} mounts are invalid") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ProductionWriterLeaseAgentError(f"{label} mounts are invalid")
    return payload


def _snapshot_db_running(selection: PromotionRuntimeSelection) -> bool:
    """Verify that a candidate DB is the isolated one bound by the pointer."""

    container = _docker_resource(
        selection.db_container,
        label="selected snapshot database container",
        required_prefix="trading_bot_wa_ir_snapshot_db_",
    )
    labels = _inspect_labels(container, label="selected snapshot database")
    if (
        labels.get("com.goldtrade.webapp-ir.snapshot") != "true"
        or labels.get("com.goldtrade.webapp-ir.release") != selection.release_sha
        or labels.get("com.docker.compose.project") != selection.compose_project
        or labels.get("com.docker.compose.service") != "snapshot_db"
    ):
        raise ProductionWriterLeaseAgentError("selected snapshot database identity is invalid")
    mounts = _inspect_mounts(container, label="selected snapshot database")
    if len(mounts) != 1:
        raise ProductionWriterLeaseAgentError("selected snapshot database has an unexpected mount layout")
    mount = mounts[0]
    if (
        mount.get("Type") != "volume"
        or mount.get("Name") != selection.db_volume
        or mount.get("Destination") != "/var/lib/postgresql/data"
        or mount.get("RW") is not True
    ):
        raise ProductionWriterLeaseAgentError("selected snapshot database does not mount the bound volume")
    network_mode = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}", container],
        label="selected snapshot database network",
        timeout=30,
        capture_stdout=True,
    ).strip()
    if network_mode != "none":
        raise ProductionWriterLeaseAgentError("selected snapshot database is not network-fenced")
    running = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.State.Running}}", container],
        label="selected snapshot database state",
        timeout=30,
        capture_stdout=True,
    ).strip()
    if running == "true":
        return True
    if running == "false":
        return False
    raise ProductionWriterLeaseAgentError("selected snapshot database state is invalid")


def _stop_selected_snapshot_db(selection: PromotionRuntimeSelection) -> bool:
    if not _snapshot_db_running(selection):
        return False
    _run_runtime_command(
        ["/usr/bin/docker", "stop", "--time", "10", selection.db_container],
        label="selected snapshot database stop",
        timeout=20,
    )
    return True


def _best_effort_restart_selected_snapshot_db(selection: PromotionRuntimeSelection) -> None:
    try:
        if not _snapshot_db_running(selection):
            _run_runtime_command(
                ["/usr/bin/docker", "start", selection.db_container],
                label="selected snapshot database restart",
                timeout=45,
            )
            if not _snapshot_db_running(selection):
                raise ProductionWriterLeaseAgentError("selected snapshot database did not restart")
    except ProductionWriterLeaseAgentError:
        _emit_event("snapshot_database_rollback_failed", container=selection.db_container)


def _assert_promoted_container(
    container: str,
    *,
    service: str,
    required_volume_names: set[str],
) -> None:
    if not DOCKER_CONTAINER_ID_RE.fullmatch(container):
        raise ProductionWriterLeaseAgentError("promoted runtime returned an invalid container id")
    labels = _inspect_labels(container, label=f"promoted {service}")
    if (
        labels.get("com.docker.compose.project") != WA_IR_PROMOTION_PROJECT_NAME
        or labels.get("com.docker.compose.service") != service
    ):
        raise ProductionWriterLeaseAgentError("promoted runtime container identity is invalid")
    volume_names = {
        item.get("Name")
        for item in _inspect_mounts(container, label=f"promoted {service}")
        if item.get("Type") == "volume" and isinstance(item.get("Name"), str)
    }
    if not required_volume_names.issubset(volume_names):
        raise ProductionWriterLeaseAgentError("promoted runtime volumes do not match the selected snapshot")


def _assert_existing_promoted_runtime_matches_selection(
    config: AgentConfig,
    *,
    selection: PromotionRuntimeSelection,
) -> dict[str, str] | None:
    """Reject partial/stale promotion projects before `--no-recreate` can reuse them."""

    discovered: dict[str, str | None] = {}
    for service in ("db", "redis", "app"):
        output = _compose_capture(
            config,
            arguments=["ps", "--all", "--quiet", service],
            label=f"promoted {service} lookup",
        ).strip()
        if not output:
            discovered[service] = None
            continue
        lines = output.splitlines()
        if len(lines) != 1 or not DOCKER_CONTAINER_ID_RE.fullmatch(lines[0]):
            raise ProductionWriterLeaseAgentError("promoted runtime lookup returned an invalid container set")
        discovered[service] = lines[0]
    if all(container is None for container in discovered.values()):
        return None
    if any(container is None for container in discovered.values()):
        raise ProductionWriterLeaseAgentError("promoted runtime is partial and cannot be reused")
    _assert_promoted_container(
        str(discovered["db"]), service="db", required_volume_names={selection.db_volume}
    )
    _assert_promoted_container(
        str(discovered["redis"]), service="redis", required_volume_names={selection.redis_volume}
    )
    _assert_promoted_container(
        str(discovered["app"]),
        service="app",
        required_volume_names={selection.uploads_volume, selection.audit_volume},
    )
    return {service: str(container) for service, container in discovered.items()}


def _runtime_binding_path(proof_path: Path) -> Path:
    """Return the immutable recovery sidecar for one promotion proof."""

    safe_path = _absolute(str(proof_path), label="promotion proof path")
    if safe_path.suffix != ".json" or not safe_path.stem:
        raise ProductionWriterLeaseAgentError("promotion proof path cannot bind a runtime recovery record")
    return safe_path.with_name(f"{safe_path.stem}.runtime-binding.json")


def _runtime_binding_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(payload))).hexdigest()


def _promotion_image_references(config: AgentConfig) -> dict[str, str]:
    """Read only the three literal, preloaded promoted-runtime image refs.

    The root-only Compose environment also contains application secrets.  This
    parser deliberately extracts no value other than the three image refs and
    refuses interpolation, so a recovery cannot silently adopt a different
    local image through a mutable shell-style value.
    """

    if config.mode != "writer" or config.site != "webapp_ir" or config.runtime.env_file is None:
        raise ProductionWriterLeaseAgentError("WA-IR recovery requires a root-only promotion runtime environment")
    values: dict[str, str] = {}
    by_name = {name: service for service, name in WA_IR_RUNTIME_IMAGE_ENV.items()}
    for raw_line in _secure_text(
        config.runtime.env_file,
        label="WA-IR promotion runtime environment",
        max_size=MAX_FILE_BYTES,
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        service = by_name.get(key)
        if service is None:
            continue
        if service in values:
            raise ProductionWriterLeaseAgentError("WA-IR promotion runtime image is duplicated")
        if (
            not value
            or value != value.strip()
            or "$" in value
            or not DOCKER_IMAGE_REFERENCE_RE.fullmatch(value)
        ):
            raise ProductionWriterLeaseAgentError("WA-IR promotion runtime image reference is invalid")
        values[service] = value
    if set(values) != set(WA_IR_RUNTIME_IMAGE_ENV):
        raise ProductionWriterLeaseAgentError("WA-IR promotion runtime image references are incomplete")
    return values


def _required_promoted_volume_names(selection: PromotionRuntimeSelection) -> dict[str, tuple[str, ...]]:
    return {
        "db": (selection.db_volume,),
        "redis": (selection.redis_volume,),
        "app": tuple(sorted((selection.uploads_volume, selection.audit_volume))),
    }


def _inspect_promoted_container_binding(
    container: str,
    *,
    service: str,
    expected_image: str,
    expected_volume_names: tuple[str, ...],
    expected_container_id: str | None = None,
    expected_image_id: str | None = None,
    expected_labels_sha256: str | None = None,
    expected_restart_policy: str | None = None,
) -> PromotedRuntimeContainerBinding:
    """Inspect one exact existing container without invoking Compose.

    The caller gives either a Compose-discovered ID during initial promotion
    or the full ID persisted at that time.  Recovery always uses the latter,
    so Docker cannot choose a newly created container by name or project.
    """

    if service not in {"db", "redis", "app"}:
        raise ProductionWriterLeaseAgentError("promoted runtime service is invalid")
    if not DOCKER_IMAGE_REFERENCE_RE.fullmatch(expected_image):
        raise ProductionWriterLeaseAgentError("expected promoted image reference is invalid")
    if expected_container_id is not None and not DOCKER_FULL_CONTAINER_ID_RE.fullmatch(expected_container_id):
        raise ProductionWriterLeaseAgentError("expected promoted container id is invalid")
    full_id = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.Id}}", container],
        label=f"promoted {service} container id",
        timeout=30,
        capture_stdout=True,
    ).strip()
    if not DOCKER_FULL_CONTAINER_ID_RE.fullmatch(full_id):
        raise ProductionWriterLeaseAgentError("promoted runtime returned an invalid full container id")
    if expected_container_id is not None and full_id != expected_container_id:
        raise ProductionWriterLeaseAgentError("promoted runtime container id no longer matches its recovery binding")
    labels = _inspect_labels(full_id, label=f"promoted {service}")
    if (
        labels.get("com.docker.compose.project") != WA_IR_PROMOTION_PROJECT_NAME
        or labels.get("com.docker.compose.service") != service
    ):
        raise ProductionWriterLeaseAgentError("promoted runtime container identity is invalid")
    labels_sha256 = _runtime_binding_hash(labels)
    if expected_labels_sha256 is not None and labels_sha256 != expected_labels_sha256:
        raise ProductionWriterLeaseAgentError("promoted runtime labels no longer match its recovery binding")
    volume_names = tuple(
        sorted(
            item["Name"]
            for item in _inspect_mounts(full_id, label=f"promoted {service}")
            if item.get("Type") == "volume" and isinstance(item.get("Name"), str)
        )
    )
    if len(volume_names) != len(set(volume_names)) or volume_names != expected_volume_names:
        raise ProductionWriterLeaseAgentError("promoted runtime volumes do not exactly match the selected snapshot")
    image = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.Config.Image}}", full_id],
        label=f"promoted {service} image reference",
        timeout=30,
        capture_stdout=True,
    ).strip()
    if image != expected_image:
        raise ProductionWriterLeaseAgentError("promoted runtime image does not match the pinned recovery image")
    image_id = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.Image}}", full_id],
        label=f"promoted {service} image id",
        timeout=30,
        capture_stdout=True,
    ).strip()
    if not DOCKER_IMAGE_ID_RE.fullmatch(image_id):
        raise ProductionWriterLeaseAgentError("promoted runtime image id is invalid")
    if expected_image_id is not None and image_id != expected_image_id:
        raise ProductionWriterLeaseAgentError("promoted runtime image id no longer matches its recovery binding")
    restart_policy = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.HostConfig.RestartPolicy.Name}}", full_id],
        label=f"promoted {service} restart policy",
        timeout=30,
        capture_stdout=True,
    ).strip()
    if restart_policy != "no":
        raise ProductionWriterLeaseAgentError("promoted runtime restart policy must remain disabled")
    if expected_restart_policy is not None and restart_policy != expected_restart_policy:
        raise ProductionWriterLeaseAgentError("promoted runtime restart policy no longer matches its recovery binding")
    return PromotedRuntimeContainerBinding(
        container_id=full_id,
        image=image,
        image_id=image_id,
        labels_sha256=labels_sha256,
        volume_names=volume_names,
        restart_policy=restart_policy,
    )


def _promoted_container_state(container: PromotedRuntimeContainerBinding, *, service: str) -> str:
    state = _run_runtime_command(
        ["/usr/bin/docker", "inspect", "--format", "{{.State.Status}}|{{.State.Running}}", container.container_id],
        label=f"promoted {service} state",
        timeout=30,
        capture_stdout=True,
    ).strip()
    if state == "running|true":
        return "running"
    if state == "exited|false":
        return "exited"
    raise ProductionWriterLeaseAgentError("promoted runtime container is in an unsafe restart state")


def _promoted_container_health(container: PromotedRuntimeContainerBinding, *, service: str) -> str:
    return _run_runtime_command(
        [
            "/usr/bin/docker",
            "inspect",
            "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}",
            container.container_id,
        ],
        label=f"promoted {service} health",
        timeout=30,
        capture_stdout=True,
    ).strip()


def _wait_for_promoted_container_health(
    container: PromotedRuntimeContainerBinding,
    *,
    service: str,
) -> None:
    deadline = time.monotonic() + WA_IR_PROMOTION_HEALTH_TIMEOUT_SECONDS
    last_state = "not-started"
    while True:
        try:
            last_state = _promoted_container_health(container, service=service)
        except ProductionWriterLeaseAgentError:
            last_state = "inspection-unavailable"
        else:
            if last_state == "running|healthy":
                return
            if last_state.split("|", 1)[0] in {"dead", "exited", "removing"} or last_state.endswith("|unhealthy"):
                raise ProductionWriterLeaseAgentError(f"promoted {service} became unhealthy during recovery")
        if time.monotonic() >= deadline:
            raise ProductionWriterLeaseAgentError(
                f"promoted {service} did not become healthy during recovery ({last_state})"
            )
        time.sleep(WA_IR_PROMOTION_HEALTH_POLL_SECONDS)


def _wait_for_promoted_redis_running(container: PromotedRuntimeContainerBinding) -> None:
    deadline = time.monotonic() + WA_IR_PROMOTION_HEALTH_TIMEOUT_SECONDS
    while True:
        try:
            if _promoted_container_state(container, service="redis") == "running":
                return
        except ProductionWriterLeaseAgentError:
            pass
        if time.monotonic() >= deadline:
            raise ProductionWriterLeaseAgentError("promoted redis did not become running during recovery")
        time.sleep(WA_IR_PROMOTION_HEALTH_POLL_SECONDS)


def _start_bound_promoted_container(container: PromotedRuntimeContainerBinding, *, service: str) -> None:
    _run_runtime_command(
        ["/usr/bin/docker", "start", container.container_id],
        label=f"promoted {service} recovery start",
        timeout=45,
    )


def _best_effort_stop_bound_promoted_runtime(
    containers: Mapping[str, PromotedRuntimeContainerBinding],
) -> None:
    """Fence only the exact persisted IDs after a failed recovery attempt."""

    for service in ("app", "redis", "db"):
        container = containers.get(service)
        if container is None:
            continue
        try:
            _run_runtime_command(
                ["/usr/bin/docker", "stop", "--time", "15", container.container_id],
                label=f"promoted {service} recovery fence",
                timeout=25,
            )
        except ProductionWriterLeaseAgentError:
            _emit_event("promoted_runtime_recovery_fence_failed", service=service)


def _capture_promoted_runtime_binding(
    config: AgentConfig,
    *,
    selection: PromotionRuntimeSelection,
) -> dict[str, PromotedRuntimeContainerBinding]:
    """Capture the post-health, pre-existing container identities once."""

    discovered = _assert_existing_promoted_runtime_matches_selection(config, selection=selection)
    if discovered is None:
        raise ProductionWriterLeaseAgentError("promoted runtime is absent after successful activation")
    images = _promotion_image_references(config)
    volumes = _required_promoted_volume_names(selection)
    containers = {
        service: _inspect_promoted_container_binding(
            container_id,
            service=service,
            expected_image=images[service],
            expected_volume_names=volumes[service],
        )
        for service, container_id in discovered.items()
    }
    if set(containers) != {"db", "redis", "app"}:
        raise ProductionWriterLeaseAgentError("promoted runtime container binding is incomplete")
    if _promoted_container_state(containers["db"], service="db") != "running":
        raise ProductionWriterLeaseAgentError("promoted database is not running after activation")
    if _promoted_container_state(containers["redis"], service="redis") != "running":
        raise ProductionWriterLeaseAgentError("promoted redis is not running after activation")
    if _promoted_container_state(containers["app"], service="app") != "running":
        raise ProductionWriterLeaseAgentError("promoted app is not running after activation")
    if _promoted_container_health(containers["db"], service="db") != "running|healthy":
        raise ProductionWriterLeaseAgentError("promoted database is not healthy after activation")
    if _promoted_container_health(containers["app"], service="app") != "running|healthy":
        raise ProductionWriterLeaseAgentError("promoted app is not healthy after activation")
    return containers


def _write_promoted_runtime_binding(
    proof_path: Path,
    *,
    config: AgentConfig,
    selection: PromotionRuntimeSelection,
    proof: Mapping[str, Any],
) -> str:
    containers = _capture_promoted_runtime_binding(config, selection=selection)
    payload: dict[str, Any] = {
        "schema": WA_IR_RUNTIME_BINDING_SCHEMA,
        "promotion_proof_sha256": proof["proof_sha256"],
        "snapshot_id": proof["snapshot_id"],
        "source_generation": proof["source_generation"],
        "release_sha": proof["release_sha"],
        "snapshot_restore_receipt_sha256": proof["snapshot_restore_receipt_sha256"],
        "snapshot_stage_receipt_sha256": proof["snapshot_stage_receipt_sha256"],
        "epoch": proof["epoch"],
        "lease_id": proof["lease_id"],
        "containers": {
            service: {
                "container_id": container.container_id,
                "image": container.image,
                "image_id": container.image_id,
                "labels_sha256": container.labels_sha256,
                "volume_names": list(container.volume_names),
                "restart_policy": container.restart_policy,
            }
            for service, container in sorted(containers.items())
        },
    }
    payload["binding_sha256"] = _runtime_binding_hash(payload)
    _write_new_json(
        _runtime_binding_path(proof_path),
        payload=payload,
        label="promoted runtime recovery binding",
    )
    return str(payload["binding_sha256"])


def _assert_promotion_proof_matches_snapshot(proof: Mapping[str, Any], *, snapshot: Any) -> None:
    expected = {
        "snapshot_id": snapshot.snapshot_id,
        "source_generation": snapshot.source_generation,
        "release_sha": snapshot.release_sha,
        "alembic_revision": snapshot.alembic_revision,
        "snapshot_restore_receipt_sha256": snapshot.receipt_sha256,
        "snapshot_stage_receipt_sha256": snapshot.stage_receipt_sha256,
    }
    if any(proof.get(field) != value for field, value in expected.items()):
        raise ProductionWriterLeaseAgentError("promotion proof does not match the active snapshot")


def _load_promoted_runtime_binding(
    proof_path: Path,
    *,
    proof: Mapping[str, Any],
    snapshot: Any,
    selection: PromotionRuntimeSelection,
    config: AgentConfig,
) -> PromotedRuntimeBinding:
    binding_path = _runtime_binding_path(proof_path)
    try:
        payload = loads_strict_receipt(
            _secure_read(
                binding_path,
                label="promoted runtime recovery binding",
                max_size=MAX_FILE_BYTES,
            )
        )
    except SnapshotPromotionError as exc:
        raise ProductionWriterLeaseAgentError("promoted runtime recovery binding is invalid") from exc
    if set(payload) != WA_IR_RUNTIME_BINDING_FIELDS or payload.get("schema") != WA_IR_RUNTIME_BINDING_SCHEMA:
        raise ProductionWriterLeaseAgentError("promoted runtime recovery binding schema is invalid")
    binding_sha256 = payload.get("binding_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "binding_sha256"}
    if (
        not isinstance(binding_sha256, str)
        or not SHA256_RE.fullmatch(binding_sha256)
        or type(payload.get("epoch")) is not int
        or not isinstance(payload.get("lease_id"), str)
        or _runtime_binding_hash(unsigned) != binding_sha256
    ):
        raise ProductionWriterLeaseAgentError("promoted runtime recovery binding hash is invalid")
    expected = {
        "promotion_proof_sha256": proof.get("proof_sha256"),
        "snapshot_id": snapshot.snapshot_id,
        "source_generation": snapshot.source_generation,
        "release_sha": snapshot.release_sha,
        "snapshot_restore_receipt_sha256": snapshot.receipt_sha256,
        "snapshot_stage_receipt_sha256": snapshot.stage_receipt_sha256,
        "epoch": proof.get("epoch"),
        "lease_id": proof.get("lease_id"),
    }
    if any(payload.get(field) != value for field, value in expected.items()):
        raise ProductionWriterLeaseAgentError("promoted runtime recovery binding does not match the live promotion proof")
    images = _promotion_image_references(config)
    volumes = _required_promoted_volume_names(selection)
    raw_containers = payload.get("containers")
    if not isinstance(raw_containers, dict) or set(raw_containers) != {"db", "redis", "app"}:
        raise ProductionWriterLeaseAgentError("promoted runtime recovery binding containers are invalid")
    containers: dict[str, PromotedRuntimeContainerBinding] = {}
    for service in ("db", "redis", "app"):
        item = raw_containers[service]
        if not isinstance(item, dict) or set(item) != WA_IR_RUNTIME_CONTAINER_BINDING_FIELDS:
            raise ProductionWriterLeaseAgentError("promoted runtime recovery binding container is invalid")
        container_id = item.get("container_id")
        image = item.get("image")
        image_id = item.get("image_id")
        labels_sha256 = item.get("labels_sha256")
        volume_names = item.get("volume_names")
        restart_policy = item.get("restart_policy")
        if (
            not isinstance(container_id, str)
            or not DOCKER_FULL_CONTAINER_ID_RE.fullmatch(container_id)
            or image != images[service]
            or not isinstance(image_id, str)
            or not DOCKER_IMAGE_ID_RE.fullmatch(image_id)
            or not isinstance(labels_sha256, str)
            or not SHA256_RE.fullmatch(labels_sha256)
            or not isinstance(volume_names, list)
            or tuple(volume_names) != volumes[service]
            or not all(isinstance(name, str) and DOCKER_RESOURCE_RE.fullmatch(name) for name in volume_names)
            or restart_policy != "no"
        ):
            raise ProductionWriterLeaseAgentError("promoted runtime recovery binding container is unsafe")
        containers[service] = PromotedRuntimeContainerBinding(
            container_id=container_id,
            image=image,
            image_id=image_id,
            labels_sha256=labels_sha256,
            volume_names=tuple(volume_names),
            restart_policy=restart_policy,
        )
    return PromotedRuntimeBinding(
        promotion_proof_sha256=str(payload["promotion_proof_sha256"]),
        snapshot_id=str(payload["snapshot_id"]),
        source_generation=str(payload["source_generation"]),
        release_sha=str(payload["release_sha"]),
        snapshot_restore_receipt_sha256=str(payload["snapshot_restore_receipt_sha256"]),
        snapshot_stage_receipt_sha256=str(payload["snapshot_stage_receipt_sha256"]),
        epoch=int(payload["epoch"]),
        lease_id=str(payload["lease_id"]),
        containers=containers,
        binding_sha256=binding_sha256,
    )


def _inspect_bound_promoted_runtime(
    binding: PromotedRuntimeBinding,
    *,
    selection: PromotionRuntimeSelection,
) -> dict[str, str]:
    volumes = _required_promoted_volume_names(selection)
    states: dict[str, str] = {}
    for service in ("db", "redis", "app"):
        expected = binding.containers[service]
        actual = _inspect_promoted_container_binding(
            expected.container_id,
            service=service,
            expected_image=expected.image,
            expected_volume_names=volumes[service],
            expected_container_id=expected.container_id,
            expected_image_id=expected.image_id,
            expected_labels_sha256=expected.labels_sha256,
            expected_restart_policy=expected.restart_policy,
        )
        if actual != expected:
            raise ProductionWriterLeaseAgentError("promoted runtime no longer matches its recovery binding")
        states[service] = _promoted_container_state(actual, service=service)
    return states


def _assert_live_matching_promotion_lease(
    config: AgentConfig,
    *,
    proof: Mapping[str, Any],
) -> Any:
    lease, _remaining = _local_lease_safety(config)
    if (
        lease.writer_epoch != proof.get("epoch")
        or lease.lease_id != proof.get("lease_id")
        or lease.proof_sha256 != proof.get("witness_proof_sha256")
    ):
        raise ProductionWriterLeaseAgentError("local Writer Witness lease does not match the persisted promotion proof")
    return lease


def _assert_existing_ir_activation_is_safe(
    config: AgentConfig,
    *,
    restore_receipt_path: Path,
    restore_receipt: dict[str, Any],
    active_snapshot: Path,
    snapshot: Any,
    proof: Mapping[str, Any],
) -> None:
    """Revalidate a prior automatic activation before allowing any route stage.

    A promotion proof is immutable, but the controller may restart while its
    original short-term proof still exists.  Returning ``already_activated``
    therefore means the exact candidate is still selected, the same local
    Writer Witness term is safely live, and the isolated application is
    healthy now; it is never merely evidence that activation once succeeded.
    """

    if config.mode != "writer" or config.site != "webapp_ir":
        raise ProductionWriterLeaseAgentError("existing activation may be reused only on WebApp-IR writer")
    expected = {
        "snapshot_id": snapshot.snapshot_id,
        "source_generation": snapshot.source_generation,
        "release_sha": snapshot.release_sha,
        "alembic_revision": snapshot.alembic_revision,
        "snapshot_restore_receipt_sha256": snapshot.receipt_sha256,
        "snapshot_stage_receipt_sha256": snapshot.stage_receipt_sha256,
    }
    if any(proof.get(field) != value for field, value in expected.items()):
        raise ProductionWriterLeaseAgentError("existing promotion proof does not match the active snapshot")
    selection = _load_promotion_runtime_selection(
        active_snapshot,
        restore_receipt_path=restore_receipt_path,
        restore_receipt=restore_receipt,
        snapshot=snapshot,
    )
    lease, _remaining = _local_lease_safety(config)
    if lease.writer_epoch != proof.get("epoch") or lease.lease_id != proof.get("lease_id"):
        raise ProductionWriterLeaseAgentError("local Writer Witness lease does not match existing promotion proof")
    if _assert_existing_promoted_runtime_matches_selection(config, selection=selection) is None:
        raise ProductionWriterLeaseAgentError("existing promoted runtime is absent")
    _wait_for_ir_app_health(config)
    # Health probes take time.  Re-check the same local term immediately before
    # the coordinator is allowed to activate the listener or route traffic.
    final_lease, _remaining = _local_lease_safety(config)
    if final_lease.writer_epoch != proof.get("epoch") or final_lease.lease_id != proof.get("lease_id"):
        raise ProductionWriterLeaseAgentError("local Writer Witness lease changed during existing activation verification")
    try:
        validate_promotion_proof(dict(proof), now=datetime.now(timezone.utc))
    except SnapshotPromotionError as exc:
        raise ProductionWriterLeaseAgentError(
            "existing promotion proof is no longer safely live after health verification"
        ) from exc
    final_selection = _load_promotion_runtime_selection(
        active_snapshot,
        restore_receipt_path=restore_receipt_path,
        restore_receipt=restore_receipt,
        snapshot=snapshot,
    )
    if final_selection != selection:
        raise ProductionWriterLeaseAgentError("active snapshot selection changed during existing activation verification")
    if _assert_existing_promoted_runtime_matches_selection(config, selection=selection) is None:
        raise ProductionWriterLeaseAgentError("existing promoted runtime disappeared during health verification")


def _renew_activation_proof(
    config: AgentConfig,
    *,
    proof: dict[str, Any],
    operation_id: str,
) -> dict[str, Any]:
    """Mint a fresh term only after the isolated runtime is locally healthy."""

    epoch = proof.get("writer_epoch")
    lease_id = proof.get("lease_id")
    if type(epoch) is not int or not isinstance(lease_id, str) or not lease_id:
        raise ProductionWriterLeaseAgentError("activation proof is invalid")
    transition = _transition(
        config.witness,
        action="renew",
        expected_epoch=epoch,
        expected_lease_id=lease_id,
        request_id=_request_id(operation_id, "activation-renew"),
        reason=f"production activated Writer Witness renewal {operation_id}",
    )
    renewed = _validate_proof(transition.get("proof"), config=config.witness, expected_epoch=epoch)
    if renewed.get("lease_id") != lease_id:
        raise ProductionWriterLeaseAgentError("activation renewal changed the lease identity")
    return renewed


def _best_effort_drain_failed_promotion(
    config: AgentConfig,
    *,
    proof: dict[str, Any],
    operation_id: str,
) -> None:
    epoch = proof.get("writer_epoch")
    lease_id = proof.get("lease_id")
    if type(epoch) is not int or not isinstance(lease_id, str) or not lease_id:
        return
    try:
        _transition(
            config.witness,
            action="drain",
            expected_epoch=epoch,
            expected_lease_id=lease_id,
            request_id=_request_id(operation_id, "failed-promotion-drain"),
            reason=f"production failed Writer Witness promotion drain {operation_id}",
        )
    except ProductionWriterLeaseAgentError:
        _emit_event("failed_promotion_drain_failed", writer_epoch=epoch)


def _new_proof_path(path: Path) -> Path:
    safe_path = _absolute(str(path), label="promotion proof output")
    try:
        os.lstat(safe_path)
    except FileNotFoundError:
        return safe_path
    raise ProductionWriterLeaseAgentError("promotion proof output already exists")


def _activate_from_snapshot_locked(
    config: AgentConfig,
    *,
    action: str,
    operation_id: str,
    restore_receipt: Path,
    proof_output: Path,
    active_snapshot: Path | None = None,
    expected_source_generation: str | None = None,
    expected_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Run one transition while the WA-IR promotion/refresh locks are held."""

    _require_writer_mode(config)
    expected_target = "webapp_ir" if action == "promote_ir" else "webapp_fi" if action == "failback_fi" else None
    if expected_target is None or config.site != expected_target:
        raise ProductionWriterLeaseAgentError("snapshot transition does not match this host site")
    operation = _operation_uuid(operation_id)
    payload = _load_restore_receipt(restore_receipt)
    try:
        initial_snapshot = parse_restore_receipt(
            payload,
            action=action,
            expected_source_generation=expected_source_generation,
        )
    except SnapshotPromotionError as exc:
        raise ProductionWriterLeaseAgentError("snapshot restore receipt cannot support promotion") from exc
    if expected_receipt_sha256 is not None and initial_snapshot.receipt_sha256 != expected_receipt_sha256:
        raise ProductionWriterLeaseAgentError("snapshot restore receipt changed before promotion")
    if action == "promote_ir":
        if active_snapshot is None:
            raise ProductionWriterLeaseAgentError("IR promotion requires the bound active snapshot pointer")
        if config.runtime.selection_env_file is None:
            raise ProductionWriterLeaseAgentError("IR promotion requires a runtime selection env file")
        _load_promotion_runtime_selection(
            active_snapshot,
            restore_receipt_path=restore_receipt,
            restore_receipt=payload,
            snapshot=initial_snapshot,
        )
    elif active_snapshot is not None:
        raise ProductionWriterLeaseAgentError("only IR promotion may use an active snapshot pointer")
    output_path = _new_proof_path(proof_output)
    proof = _acquire_proof(
        config,
        operation_id=operation,
        purpose="IR promotion" if action == "promote_ir" else "FI failback",
        allow_live_local_recovery=True,
        # WA-IR does not persist local renewal authority until the new isolated
        # runtime is healthy.  Its guard shares the transition lock, so a crash
        # before that point fences the just-started local scope instead of
        # allowing a non-serving term to renew indefinitely.
        persist_lease=action != "promote_ir",
    )
    activation_proof: dict[str, Any] | None = proof if action == "promote_ir" else None
    selection: PromotionRuntimeSelection | None = None
    snapshot_stop_attempted = False
    runtime_binding_sha256: str | None = None
    try:
        # The receipt may age during a Witness round trip.  Verify it again
        # immediately before the only runtime start operation.
        latest_payload = _load_restore_receipt(restore_receipt)
        snapshot = parse_restore_receipt(
            latest_payload,
            action=action,
            expected_source_generation=expected_source_generation,
        )
        if snapshot.receipt_sha256 != initial_snapshot.receipt_sha256:
            raise ProductionWriterLeaseAgentError("snapshot restore receipt changed during promotion")
        if expected_receipt_sha256 is not None and snapshot.receipt_sha256 != expected_receipt_sha256:
            raise ProductionWriterLeaseAgentError("snapshot restore receipt changed during promotion")
        if action == "promote_ir":
            if active_snapshot is None or config.runtime.selection_env_file is None:  # Defensive for type narrowing.
                raise ProductionWriterLeaseAgentError("IR promotion active snapshot binding disappeared")
            selection = _load_promotion_runtime_selection(
                active_snapshot,
                restore_receipt_path=restore_receipt,
                restore_receipt=latest_payload,
                snapshot=snapshot,
            )
            _write_promotion_runtime_selection(
                config.runtime.selection_env_file,
                selection=selection,
            )
            _assert_existing_promoted_runtime_matches_selection(config, selection=selection)
            snapshot_stop_attempted = True
            _stop_selected_snapshot_db(selection)
        _start_scoped_runtime(config)
        if action == "promote_ir":
            if active_snapshot is None or selection is None:
                raise ProductionWriterLeaseAgentError("IR promotion selection disappeared before activation")
            # Re-read every root-owned binding after the local health gate.
            # The shared refresh lock rules out normal timer churn; this check
            # also catches manual/local corruption before a route proof exists.
            final_payload = _load_restore_receipt(restore_receipt)
            final_snapshot = parse_restore_receipt(
                final_payload,
                action=action,
                expected_source_generation=expected_source_generation,
            )
            if final_snapshot.receipt_sha256 != snapshot.receipt_sha256:
                raise ProductionWriterLeaseAgentError("snapshot restore receipt changed after local activation")
            final_selection = _load_promotion_runtime_selection(
                active_snapshot,
                restore_receipt_path=restore_receipt,
                restore_receipt=final_payload,
                snapshot=final_snapshot,
            )
            if final_selection != selection:
                raise ProductionWriterLeaseAgentError("active snapshot selection changed after local activation")
            proof = _renew_activation_proof(config, proof=proof, operation_id=operation)
            activation_proof = proof
            _write_lease(_writer_lease_file(config), proof=proof)
            _local_lease_safety(config)
            snapshot = final_snapshot
        promotion_proof = build_promotion_proof(
            action=action,
            operation_id=operation,
            snapshot=snapshot,
            witness_proof=proof,
        )
        _write_new_json(output_path, payload=promotion_proof, label="promotion proof")
        if action == "promote_ir":
            if selection is None:
                raise ProductionWriterLeaseAgentError("IR promotion runtime selection disappeared before recovery binding")
            # The sidecar is create-only and captures the exact existing
            # containers after the local health gate.  A later recovery never
            # asks Compose to discover or create a replacement runtime.
            runtime_binding_sha256 = _write_promoted_runtime_binding(
                output_path,
                config=config,
                selection=selection,
                proof=promotion_proof,
            )
    except Exception:
        _best_effort_stop(config)
        if action == "promote_ir" and activation_proof is not None:
            _best_effort_drain_failed_promotion(
                config,
                proof=activation_proof,
                operation_id=operation,
            )
        if action == "promote_ir" and selection is not None and snapshot_stop_attempted:
            _best_effort_restart_selected_snapshot_db(selection)
        raise
    return {
        "status": "activated",
        "action": action,
        "site": config.site,
        "writer_epoch": proof["writer_epoch"],
        "lease_expires_at": proof["expires_at"],
        "snapshot_age_seconds": promotion_proof["snapshot_age_seconds"],
        "proof_sha256": promotion_proof["proof_sha256"],
        "runtime_binding_sha256": runtime_binding_sha256,
    }


def activate_from_snapshot(
    config: AgentConfig,
    *,
    action: str,
    operation_id: str,
    restore_receipt: Path,
    proof_output: Path,
    active_snapshot: Path | None = None,
    expected_source_generation: str | None = None,
    expected_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Serialize WA-IR promotion against its lease guard and snapshot timer."""

    if action != "promote_ir":
        return _activate_from_snapshot_locked(
            config,
            action=action,
            operation_id=operation_id,
            restore_receipt=restore_receipt,
            proof_output=proof_output,
            active_snapshot=active_snapshot,
            expected_source_generation=expected_source_generation,
            expected_receipt_sha256=expected_receipt_sha256,
        )
    if active_snapshot is None:
        raise ProductionWriterLeaseAgentError("IR promotion requires the bound active snapshot pointer")
    with _ir_writer_transition_lock(config, nonblocking=False) as acquired:
        if not acquired:
            raise ProductionWriterLeaseAgentError("WebApp-IR writer transition is busy")
        with _standby_refresh_transition_lock(active_snapshot):
            return _activate_from_snapshot_locked(
                config,
                action=action,
                operation_id=operation_id,
                restore_receipt=restore_receipt,
                proof_output=proof_output,
                active_snapshot=active_snapshot,
                expected_source_generation=expected_source_generation,
                expected_receipt_sha256=expected_receipt_sha256,
            )


def _load_live_ir_promotion_proof(path: Path) -> dict[str, Any]:
    safe_path = _absolute(str(path), label="persisted promotion proof")
    try:
        payload = loads_strict_receipt(
            _secure_read(safe_path, label="persisted promotion proof", max_size=MAX_FILE_BYTES)
        )
        proof = validate_promotion_proof(payload, now=datetime.now(timezone.utc))
    except SnapshotPromotionError as exc:
        raise ProductionWriterLeaseAgentError("persisted promotion proof is invalid or no longer live") from exc
    if proof.get("action") != "promote_ir" or proof.get("target_site") != "webapp_ir":
        raise ProductionWriterLeaseAgentError("persisted promotion proof is not a WA-IR promotion")
    return proof


def _recover_promoted_runtime_locked(
    config: AgentConfig,
    *,
    restore_receipt: Path,
    active_snapshot: Path,
    promotion_proof: Path,
) -> dict[str, Any]:
    """Start only one previously-bound, stopped WA-IR promotion runtime.

    This path intentionally performs no Witness transition and never asks
    Compose to create, recreate, pull, build, or resolve a container.  It is
    a bounded recovery for a just-promoted runtime whose Docker daemon or
    existing containers stopped while the same local Witness term remains
    valid.
    """

    _require_writer_mode(config)
    if config.site != "webapp_ir":
        raise ProductionWriterLeaseAgentError("promoted-runtime recovery is allowed only on WebApp-IR")
    payload = _load_restore_receipt(restore_receipt)
    try:
        snapshot = parse_restore_receipt(payload, action="promote_ir")
    except SnapshotPromotionError as exc:
        raise ProductionWriterLeaseAgentError("snapshot restore receipt cannot support promoted-runtime recovery") from exc
    proof = _load_live_ir_promotion_proof(promotion_proof)
    _assert_promotion_proof_matches_snapshot(proof, snapshot=snapshot)
    selection = _load_promotion_runtime_selection(
        active_snapshot,
        restore_receipt_path=restore_receipt,
        restore_receipt=payload,
        snapshot=snapshot,
    )
    binding = _load_promoted_runtime_binding(
        promotion_proof,
        proof=proof,
        snapshot=snapshot,
        selection=selection,
        config=config,
    )
    lease = _assert_live_matching_promotion_lease(config, proof=proof)
    states = _inspect_bound_promoted_runtime(binding, selection=selection)
    if states["app"] != "exited":
        raise ProductionWriterLeaseAgentError("promoted app is not stopped; recovery refuses to restart a running container")
    if states["db"] not in {"running", "exited"} or states["redis"] not in {"running", "exited"}:
        raise ProductionWriterLeaseAgentError("promoted runtime is not in a recoverable stopped state")

    start_attempted = False
    try:
        if states["db"] == "exited":
            start_attempted = True
            _start_bound_promoted_container(binding.containers["db"], service="db")
        _wait_for_promoted_container_health(binding.containers["db"], service="db")
        if states["redis"] == "exited":
            start_attempted = True
            _start_bound_promoted_container(binding.containers["redis"], service="redis")
        _wait_for_promoted_redis_running(binding.containers["redis"])
        start_attempted = True
        _start_bound_promoted_container(binding.containers["app"], service="app")
        _wait_for_promoted_container_health(binding.containers["app"], service="app")

        # Health probes consume a meaningful part of the short emergency
        # lease.  Re-read every immutable/root-only binding before returning
        # success, and fence these exact IDs if anything changed or expired.
        final_payload = _load_restore_receipt(restore_receipt)
        try:
            final_snapshot = parse_restore_receipt(final_payload, action="promote_ir")
        except SnapshotPromotionError as exc:
            raise ProductionWriterLeaseAgentError(
                "snapshot restore receipt became invalid during promoted-runtime recovery"
            ) from exc
        if final_snapshot.receipt_sha256 != snapshot.receipt_sha256:
            raise ProductionWriterLeaseAgentError("snapshot restore receipt changed during promoted-runtime recovery")
        final_proof = _load_live_ir_promotion_proof(promotion_proof)
        if final_proof != proof:
            raise ProductionWriterLeaseAgentError("persisted promotion proof changed during promoted-runtime recovery")
        _assert_promotion_proof_matches_snapshot(final_proof, snapshot=final_snapshot)
        final_selection = _load_promotion_runtime_selection(
            active_snapshot,
            restore_receipt_path=restore_receipt,
            restore_receipt=final_payload,
            snapshot=final_snapshot,
        )
        if final_selection != selection:
            raise ProductionWriterLeaseAgentError("active snapshot selection changed during promoted-runtime recovery")
        final_binding = _load_promoted_runtime_binding(
            promotion_proof,
            proof=final_proof,
            snapshot=final_snapshot,
            selection=final_selection,
            config=config,
        )
        if final_binding != binding:
            raise ProductionWriterLeaseAgentError("runtime recovery binding changed during promoted-runtime recovery")
        final_lease = _assert_live_matching_promotion_lease(config, proof=final_proof)
        final_states = _inspect_bound_promoted_runtime(final_binding, selection=final_selection)
        if any(final_states[service] != "running" for service in ("db", "redis", "app")):
            raise ProductionWriterLeaseAgentError("promoted runtime did not remain running after recovery")
        if _promoted_container_health(final_binding.containers["db"], service="db") != "running|healthy":
            raise ProductionWriterLeaseAgentError("promoted database is unhealthy after recovery")
        if _promoted_container_health(final_binding.containers["app"], service="app") != "running|healthy":
            raise ProductionWriterLeaseAgentError("promoted app is unhealthy after recovery")
    except Exception:
        if start_attempted:
            _best_effort_stop_bound_promoted_runtime(binding.containers)
        raise
    return {
        "status": "recovered",
        "action": "recover-promoted-runtime",
        "site": "webapp_ir",
        "writer_epoch": lease.writer_epoch,
        "lease_expires_at": final_lease.expires_at.isoformat(),
        "proof_sha256": proof["proof_sha256"],
        "runtime_binding_sha256": binding.binding_sha256,
    }


def recover_promoted_runtime(
    config: AgentConfig,
    *,
    restore_receipt: Path,
    active_snapshot: Path,
    promotion_proof: Path,
) -> dict[str, Any]:
    """Explicit-only WA-IR restart recovery for a still-live promotion term."""

    if config.mode != "writer" or config.site != "webapp_ir":
        raise ProductionWriterLeaseAgentError("promoted-runtime recovery is allowed only on WebApp-IR writer")
    with _ir_writer_transition_lock(config, nonblocking=False) as acquired:
        if not acquired:
            raise ProductionWriterLeaseAgentError("WebApp-IR writer transition is busy")
        with _standby_refresh_transition_lock(active_snapshot):
            return _recover_promoted_runtime_locked(
                config,
                restore_receipt=restore_receipt,
                active_snapshot=active_snapshot,
                promotion_proof=promotion_proof,
            )


def _automatic_operation_id(*, action: str, receipt_sha256: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"production-writer-auto:{action}:{receipt_sha256}"))


def _automatic_proof_path(*, directory: Path, action: str, snapshot_id: str, receipt_sha256: str) -> Path:
    safe_directory = _absolute(str(directory), label="promotion proof directory")
    if action != "promote_ir" or not SHA256_RE.fullmatch(receipt_sha256):
        raise ProductionWriterLeaseAgentError("automatic promotion proof binding is invalid")
    return safe_directory / f"{action}-{snapshot_id}-{receipt_sha256}.json"


def _existing_automatic_proof(
    path: Path,
    *,
    config: AgentConfig,
    restore_receipt_path: Path,
    restore_receipt: dict[str, Any],
    active_snapshot: Path,
    snapshot: Any,
    receipt_sha256: str,
) -> dict[str, Any] | None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    try:
        payload = loads_strict_receipt(
            _secure_read(path, label="existing promotion proof", max_size=MAX_FILE_BYTES)
        )
        proof = validate_promotion_proof(payload, now=datetime.now(timezone.utc))
    except SnapshotPromotionError as exc:
        raise ProductionWriterLeaseAgentError("existing promotion proof is invalid") from exc
    if (
        proof.get("action") != "promote_ir"
        or proof.get("target_site") != "webapp_ir"
        or proof.get("snapshot_restore_receipt_sha256") != receipt_sha256
    ):
        raise ProductionWriterLeaseAgentError("existing promotion proof does not match this receipt")
    _assert_existing_ir_activation_is_safe(
        config,
        restore_receipt_path=restore_receipt_path,
        restore_receipt=restore_receipt,
        active_snapshot=active_snapshot,
        snapshot=snapshot,
        proof=proof,
    )
    return {
        "status": "already_activated",
        "action": "promote_ir",
        "site": "webapp_ir",
        "writer_epoch": proof["epoch"],
        "lease_expires_at": proof["lease_expires_at"],
        "snapshot_age_seconds": proof["snapshot_age_seconds"],
        "proof_sha256": proof["proof_sha256"],
    }


def promote_watch(
    config: AgentConfig,
    *,
    restore_receipt: Path,
    active_snapshot: Path,
    proof_directory: Path,
    poll_seconds: int,
    once: bool,
) -> dict[str, Any]:
    """Wait for one fresh FI->IR receipt and promote exactly once.

    The operation UUID is a deterministic UUIDv5 of the receipt hash.  A
    restarted service therefore does not mint a second operation for the same
    immutable receipt, and a successful create-only proof ends the watcher.
    """

    _require_writer_mode(config)
    if config.site != "webapp_ir":
        raise ProductionWriterLeaseAgentError("automatic promotion is allowed only on WebApp-IR")
    if type(poll_seconds) is not int or not 1 <= poll_seconds <= 30:
        raise ProductionWriterLeaseAgentError("automatic promotion poll interval is invalid")
    safe_directory = _absolute(str(proof_directory), label="promotion proof directory")
    last_error: tuple[str, str] | None = None
    while True:
        try:
            payload = _load_restore_receipt(restore_receipt)
            snapshot = parse_restore_receipt(payload, action="promote_ir")
            operation_id = _automatic_operation_id(
                action="promote_ir", receipt_sha256=snapshot.receipt_sha256
            )
            proof_output = _automatic_proof_path(
                directory=safe_directory,
                action="promote_ir",
                snapshot_id=snapshot.snapshot_id,
                receipt_sha256=snapshot.receipt_sha256,
            )
            existing = _existing_automatic_proof(
                proof_output,
                config=config,
                restore_receipt_path=restore_receipt,
                restore_receipt=payload,
                active_snapshot=active_snapshot,
                snapshot=snapshot,
                receipt_sha256=snapshot.receipt_sha256,
            )
            if existing is not None:
                return existing
            return activate_from_snapshot(
                config,
                action="promote_ir",
                operation_id=operation_id,
                restore_receipt=restore_receipt,
                proof_output=proof_output,
                active_snapshot=active_snapshot,
                expected_receipt_sha256=snapshot.receipt_sha256,
            )
        except (ProductionWriterLeaseAgentError, SnapshotPromotionError) as exc:
            if once:
                raise
            error_key = (type(exc).__name__, str(exc))
            if error_key != last_error:
                _emit_event(
                    "waiting_for_safe_promotion",
                    action="promote_ir",
                    error_class=type(exc).__name__,
                    reason=str(exc),
                )
                last_error = error_key
            time.sleep(poll_seconds)


def renew_once(config: AgentConfig, *, request_id: str | None = None) -> dict[str, Any]:
    lease_file = _writer_lease_file(config)
    lease = load_production_writer_lease(lease_file)
    if lease.holder_site != config.site:
        raise ProductionWriterLeaseAgentError("local lease holder does not match configured site")
    transition = _transition(
        config.witness,
        action="renew",
        expected_epoch=lease.writer_epoch,
        expected_lease_id=lease.lease_id,
        request_id=request_id or str(uuid4()),
        reason="automatic production writer lease renewal",
    )
    proof = _validate_proof(
        transition.get("proof"), config=config.witness, expected_epoch=lease.writer_epoch
    )
    if proof.get("lease_id") != lease.lease_id:
        raise ProductionWriterLeaseAgentError("Witness renewal changed the lease identity")
    _write_lease(lease_file, proof=proof)
    return {
        "status": "renewed",
        "site": config.site,
        "writer_epoch": proof["writer_epoch"],
        "lease_expires_at": proof["expires_at"],
    }


def drain_and_stop(config: AgentConfig, *, operation_id: str) -> dict[str, Any]:
    lease_file = _writer_lease_file(config)
    operation = _operation_uuid(operation_id)
    lease = load_production_writer_lease(lease_file)
    if lease.holder_site != config.site:
        raise ProductionWriterLeaseAgentError("local lease holder does not match configured site")
    _transition(
        config.witness,
        action="drain",
        expected_epoch=lease.writer_epoch,
        expected_lease_id=lease.lease_id,
        request_id=_request_id(operation, "drain"),
        reason=f"production controlled writer drain {operation}",
    )
    _compose(config, action="stop")
    return {"status": "drained", "site": config.site, "writer_epoch": lease.writer_epoch}


def _guard_iteration(config: AgentConfig, *, emit_degraded: bool) -> dict[str, Any]:
    if config.mode == "observer":
        try:
            epoch, expires_at = _observe_active_writer_term(config)
            return {
                "status": "observed",
                "site": config.site,
                "writer_epoch": epoch,
                "lease_expires_at": expires_at.isoformat(),
            }
        except (WriterWitnessUnavailable, ProductionWriterLeaseAgentError):
            # Bot-FI is deliberately stricter than the WebApp writers:
            # without a fresh Witness observation it must not keep a legacy
            # direct-sync path alive after FI loses authority.
            raise
    try:
        lease, remaining = _local_lease_safety(config)
    except ProductionWriterLeaseAgentError:
        raise
    try:
        return renew_once(config)
    except WriterWitnessUnavailable as renewal_error:
        # A transient Witness failure does not itself fence a still-valid local
        # term.  Re-check after the failed request and stop only when it has
        # reached its safety margin.
        try:
            lease, remaining = _local_lease_safety(config)
        except ProductionWriterLeaseAgentError:
            raise renewal_error
        result = {
            "status": "renewal_degraded",
            "site": config.site,
            "writer_epoch": lease.writer_epoch,
            "lease_expires_at": lease.expires_at.isoformat(),
            "seconds_remaining": max(0, int(remaining)),
        }
        if emit_degraded:
            _emit_event("renewal_degraded", **result)
        return result
    except ProductionWriterLeaseAgentError:
        raise


def guard(config: AgentConfig, *, once: bool) -> dict[str, Any]:
    while True:
        try:
            with _ir_writer_transition_lock(config, nonblocking=True) as acquired:
                if acquired:
                    result = _guard_iteration(config, emit_degraded=not once)
                else:
                    result = {
                        "status": "promotion_in_progress",
                        "site": config.site,
                    }
        except ProductionWriterLeaseAgentError:
            _best_effort_stop(config)
            raise
        if once:
            return result
        if result["status"] == "promotion_in_progress":
            # Do not stop or renew while the promotion controller holds its
            # owner-only lock.  A process crash releases flock automatically.
            time.sleep(1)
        else:
            time.sleep(config.witness.renew_interval_seconds)


def _public_status(config: AgentConfig) -> dict[str, Any]:
    if config.mode == "observer":
        status = _status(config.witness, request_id=str(uuid4()))
        holder, epoch, _lease_id, expires_at = _state(status)
        return {
            "status": "ok",
            "mode": config.mode,
            "site": config.site,
            "holder_site": holder,
            "writer_epoch": epoch,
            "lease_expires_at": expires_at.isoformat() if expires_at is not None else None,
        }
    lease = load_production_writer_lease(_writer_lease_file(config))
    return {
        "status": "ok",
        "mode": config.mode,
        "site": config.site,
        "holder_site": lease.holder_site,
        "writer_epoch": lease.writer_epoch,
        "lease_expires_at": lease.expires_at.isoformat(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("status")
    bootstrap = subparsers.add_parser("bootstrap-fi")
    bootstrap.add_argument("--operation-id", required=True)
    promote = subparsers.add_parser("promote")
    promote.add_argument("--operation-id", required=True)
    promote.add_argument("--restore-receipt", required=True, type=Path)
    promote.add_argument("--active-snapshot", required=True, type=Path)
    promote.add_argument("--proof-output", required=True, type=Path)
    promote_watch_parser = subparsers.add_parser("promote-watch")
    promote_watch_parser.add_argument("--restore-receipt", required=True, type=Path)
    promote_watch_parser.add_argument("--active-snapshot", required=True, type=Path)
    promote_watch_parser.add_argument("--proof-directory", required=True, type=Path)
    promote_watch_parser.add_argument("--poll-seconds", type=int, default=2)
    promote_watch_parser.add_argument("--once", action="store_true")
    recover = subparsers.add_parser(
        "recover-promoted-runtime",
        help="explicitly start only the exact stopped WA-IR containers bound to a live promotion proof",
    )
    recover.add_argument("--restore-receipt", required=True, type=Path)
    recover.add_argument("--active-snapshot", required=True, type=Path)
    recover.add_argument("--promotion-proof", required=True, type=Path)
    failback = subparsers.add_parser("failback")
    failback.add_argument("--operation-id", required=True)
    failback.add_argument("--restore-receipt", required=True, type=Path)
    failback.add_argument("--expected-source-generation", required=True)
    failback.add_argument("--proof-output", required=True, type=Path)
    drain = subparsers.add_parser("drain")
    drain.add_argument("--operation-id", required=True)
    guard_parser = subparsers.add_parser("guard")
    guard_parser.add_argument("--once", action="store_true")
    subparsers.add_parser("renew")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = _load_config(args.config)
    if args.action == "status":
        return _public_status(config)
    if args.action == "bootstrap-fi":
        return bootstrap_fi_and_start(config, operation_id=args.operation_id)
    if args.action == "promote":
        return activate_from_snapshot(
            config,
            action="promote_ir",
            operation_id=args.operation_id,
            restore_receipt=args.restore_receipt,
            active_snapshot=args.active_snapshot,
            proof_output=args.proof_output,
        )
    if args.action == "promote-watch":
        return promote_watch(
            config,
            restore_receipt=args.restore_receipt,
            active_snapshot=args.active_snapshot,
            proof_directory=args.proof_directory,
            poll_seconds=args.poll_seconds,
            once=bool(args.once),
        )
    if args.action == "recover-promoted-runtime":
        return recover_promoted_runtime(
            config,
            restore_receipt=args.restore_receipt,
            active_snapshot=args.active_snapshot,
            promotion_proof=args.promotion_proof,
        )
    if args.action == "failback":
        return activate_from_snapshot(
            config,
            action="failback_fi",
            operation_id=args.operation_id,
            restore_receipt=args.restore_receipt,
            proof_output=args.proof_output,
            expected_source_generation=args.expected_source_generation,
        )
    if args.action == "drain":
        return drain_and_stop(config, operation_id=args.operation_id)
    if args.action == "renew":
        return renew_once(config)
    return guard(config, once=bool(args.once))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        payload: dict[str, Any] = {"status": "blocked", "error_class": type(exc).__name__}
        if isinstance(exc, (ProductionWriterLeaseAgentError, SnapshotPromotionError)):
            payload["reason"] = str(exc)
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
