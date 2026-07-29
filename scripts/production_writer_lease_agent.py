#!/usr/bin/env python3
"""Host-level Writer Witness lease agent for the legacy 2c08 production app.

It never touches databases, volumes, images, or routes.  Writer mode starts
or stops only ``app`` and ``sync_worker``.  Passive observer mode starts
nothing and may stop only configured ``bot``/``sync_worker`` plus ``app`` if
that host explicitly declares it writable.  The FI writer and Bot-FI follower
therefore fail closed before the local term can expire, while IR promotion can
start its isolated app stack after receiving the next term.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
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
from typing import Any
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


AGENT_SCHEMA = "production-writer-lease-agent-v1"
WITNESS_AUTH_VERSION = 1
WITNESS_TRANSITION_PATH = "/v1/writer-witness/transitions"
WITNESS_STATUS_PATH = "/v1/writer-witness/status"
MAX_FILE_BYTES = 128 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")


class ProductionWriterLeaseAgentError(RuntimeError):
    pass


class WriterWitnessUnavailable(ProductionWriterLeaseAgentError):
    """A transport/server outage that may retain a still-safe local term."""


@dataclass(frozen=True)
class RuntimeConfig:
    compose_file: Path
    env_file: Path | None
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
class AgentConfig:
    mode: str
    site: str
    lease_file: Path | None
    runtime: RuntimeConfig
    witness: WitnessConfig


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


def _load_config(path: Path) -> AgentConfig:
    if os.geteuid() != 0:
        raise ProductionWriterLeaseAgentError("writer lease agent must run as root")
    try:
        raw = json.loads(_secure_text(path, label="writer lease agent config"), object_pairs_hook=_strict_object)
    except ProductionWriterLeaseAgentError:
        raise
    except Exception as exc:
        raise ProductionWriterLeaseAgentError("writer lease agent config is invalid") from exc
    fields = {"schema", "mode", "site", "lease_file", "runtime", "witness"}
    if not isinstance(raw, dict) or set(raw) != fields or raw.get("schema") != AGENT_SCHEMA:
        raise ProductionWriterLeaseAgentError("writer lease agent config schema is invalid")
    mode = raw.get("mode")
    if mode not in {"writer", "observer"}:
        raise ProductionWriterLeaseAgentError("writer lease agent mode is invalid")
    site = str(raw.get("site") or "").strip().lower()
    if site not in WEBAPP_SITES:
        raise ProductionWriterLeaseAgentError("writer lease agent site is invalid")
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
    runtime_fields = {"compose_file", "env_file", "services"}
    if not isinstance(runtime_raw, dict) or set(runtime_raw) != runtime_fields:
        raise ProductionWriterLeaseAgentError("managed runtime config is invalid")
    compose_file = _absolute(runtime_raw.get("compose_file"), label="compose file")
    env_value = runtime_raw.get("env_file")
    env_file = _absolute(env_value, label="runtime env file") if env_value is not None else None
    services_raw = runtime_raw.get("services")
    if (
        not isinstance(services_raw, list)
        or not services_raw
        or not all(isinstance(service, str) for service in services_raw)
        or len(set(services_raw)) != len(services_raw)
        or set(services_raw) not in (
            {"app", "sync_worker"},
            {"bot", "sync_worker"},
            {"app", "bot", "sync_worker"},
        )
    ):
        raise ProductionWriterLeaseAgentError(
            "managed runtime contains an unsupported writable service scope"
        )
    if mode == "writer" and set(services_raw) != {"app", "sync_worker"}:
        raise ProductionWriterLeaseAgentError("writer mode must manage app and sync_worker")
    if mode == "observer" and set(services_raw) not in (
        {"bot", "sync_worker"},
        {"app", "bot", "sync_worker"},
    ):
        raise ProductionWriterLeaseAgentError(
            "observer mode must manage bot and sync_worker, with app only when writable"
        )
    runtime = RuntimeConfig(
        compose_file=compose_file,
        env_file=env_file,
        services=tuple(sorted(services_raw)),
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
    return AgentConfig(
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
    )


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


def _compose(config: AgentConfig, *, action: str) -> None:
    if action not in {"start", "stop"}:
        raise ProductionWriterLeaseAgentError("managed runtime action is invalid")
    command = ["/usr/bin/docker", "compose", "-f", str(config.runtime.compose_file)]
    if config.runtime.env_file is not None:
        command.extend(["--env-file", str(config.runtime.env_file)])
    if action == "start":
        command.extend(["up", "-d", "--no-deps", "--no-recreate", *config.runtime.services])
    else:
        command.extend(["stop", "--timeout", "15", *config.runtime.services])
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=90,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProductionWriterLeaseAgentError("managed runtime command failed") from exc
    if result.returncode != 0:
        raise ProductionWriterLeaseAgentError("managed runtime command was rejected")


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
    _write_lease(lease_file, proof=proof)
    return proof


def _start_scoped_runtime(config: AgentConfig) -> None:
    try:
        _compose(config, action="start")
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


def _new_proof_path(path: Path) -> Path:
    safe_path = _absolute(str(path), label="promotion proof output")
    try:
        os.lstat(safe_path)
    except FileNotFoundError:
        return safe_path
    raise ProductionWriterLeaseAgentError("promotion proof output already exists")


def activate_from_snapshot(
    config: AgentConfig,
    *,
    action: str,
    operation_id: str,
    restore_receipt: Path,
    proof_output: Path,
    expected_source_generation: str | None = None,
    expected_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Acquire a next Witness term, start only app/sync, and emit routing proof."""

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
    output_path = _new_proof_path(proof_output)
    proof = _acquire_proof(
        config,
        operation_id=operation,
        purpose="IR promotion" if action == "promote_ir" else "FI failback",
        allow_live_local_recovery=True,
    )
    try:
        # The receipt may age during a Witness round trip.  Verify it again
        # immediately before the only runtime start operation.
        snapshot = parse_restore_receipt(
            payload,
            action=action,
            expected_source_generation=expected_source_generation,
        )
        if expected_receipt_sha256 is not None and snapshot.receipt_sha256 != expected_receipt_sha256:
            raise ProductionWriterLeaseAgentError("snapshot restore receipt changed during promotion")
        _start_scoped_runtime(config)
        promotion_proof = build_promotion_proof(
            action=action,
            operation_id=operation,
            snapshot=snapshot,
            witness_proof=proof,
        )
        _write_new_json(output_path, payload=promotion_proof, label="promotion proof")
    except Exception:
        _best_effort_stop(config)
        raise
    return {
        "status": "activated",
        "action": action,
        "site": config.site,
        "writer_epoch": proof["writer_epoch"],
        "lease_expires_at": proof["expires_at"],
        "snapshot_age_seconds": snapshot.snapshot_age_seconds,
        "proof_sha256": promotion_proof["proof_sha256"],
    }


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


def guard(config: AgentConfig, *, once: bool) -> dict[str, Any]:
    while True:
        if config.mode == "observer":
            try:
                epoch, expires_at = _observe_active_writer_term(config)
                result = {
                    "status": "observed",
                    "site": config.site,
                    "writer_epoch": epoch,
                    "lease_expires_at": expires_at.isoformat(),
                }
            except (WriterWitnessUnavailable, ProductionWriterLeaseAgentError):
                # Bot-FI is deliberately stricter than the WebApp writers:
                # without a fresh Witness observation it must not keep a
                # legacy direct sync path alive after FI loses authority.
                _best_effort_stop(config)
                raise
            if once:
                return result
            time.sleep(config.witness.renew_interval_seconds)
            continue
        try:
            lease, remaining = _local_lease_safety(config)
        except ProductionWriterLeaseAgentError:
            _best_effort_stop(config)
            raise
        try:
            result = renew_once(config)
        except WriterWitnessUnavailable as renewal_error:
            # A transient Witness failure does not itself fence a still-valid
            # local term.  Re-check after the failed request and stop only
            # when the root-owned term has reached its safety margin.
            try:
                lease, remaining = _local_lease_safety(config)
            except ProductionWriterLeaseAgentError:
                _best_effort_stop(config)
                raise renewal_error
            result = {
                "status": "renewal_degraded",
                "site": config.site,
                "writer_epoch": lease.writer_epoch,
                "lease_expires_at": lease.expires_at.isoformat(),
                "seconds_remaining": max(0, int(remaining)),
            }
            if not once:
                _emit_event("renewal_degraded", **result)
        except ProductionWriterLeaseAgentError:
            _best_effort_stop(config)
            raise
        if once:
            return result
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
    promote.add_argument("--proof-output", required=True, type=Path)
    promote_watch_parser = subparsers.add_parser("promote-watch")
    promote_watch_parser.add_argument("--restore-receipt", required=True, type=Path)
    promote_watch_parser.add_argument("--proof-directory", required=True, type=Path)
    promote_watch_parser.add_argument("--poll-seconds", type=int, default=2)
    promote_watch_parser.add_argument("--once", action="store_true")
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
            proof_output=args.proof_output,
        )
    if args.action == "promote-watch":
        return promote_watch(
            config,
            restore_receipt=args.restore_receipt,
            proof_directory=args.proof_directory,
            poll_seconds=args.poll_seconds,
            once=bool(args.once),
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
