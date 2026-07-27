#!/usr/bin/env python3
"""Create one fresh, operation-bound internal DR certificate authority.

The private key is generated and retained only in the controller's root-only
operation directory.  This producer never contacts a host, starts a service,
or reads material from an earlier campaign.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)


ATTESTATION_SCHEMA = "production-shadow-dr-ca-attestation-v1"
STATE_SCHEMA = "production-shadow-dr-ca-generation-state-v1"
SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
KEY_NAME = "ca.key"
CERTIFICATE_NAME = "ca.crt"
ATTESTATION_NAME = "dr-ca-attestation.json"
STATE_NAME = "dr-ca-generation-state.json"
LOCK_NAME = "dr-ca-generation.lock"
VALIDITY_DAYS = 120
NOT_BEFORE_SKEW = timedelta(minutes=5)
MAX_ATTESTATION_AGE = timedelta(hours=24)
MAX_FUTURE_SKEW = timedelta(minutes=5)
MIN_REMAINING_VALIDITY = timedelta(days=30)
MAX_FILE_BYTES = 1024 * 1024
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SERIAL_RE = re.compile(r"^[1-9a-f][0-9a-f]{0,39}$")
UTC_SECONDS_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
STATE_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "generated_at",
        "not_before",
        "not_after",
        "serial_hex",
    }
)
ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "ca_sha256",
        "ca_subject",
        "ca_serial_hex",
        "not_before",
        "not_after",
        "generated_at",
        "private_key_mode",
        "private_key_retained_on_controller",
        "old_tls_material_reused",
    }
)
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)


class DrCaError(RuntimeError):
    """Raised when fresh operation-bound CA creation cannot be proven safe."""


Checkpoint = Callable[[str], None]


def _noop_checkpoint(_phase: str) -> None:
    return


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DrCaError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _canonical_operation_id(value: Any) -> str:
    if not isinstance(value, str):
        raise DrCaError("operation id must be a canonical UUIDv4")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise DrCaError("operation id must be a canonical UUIDv4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise DrCaError("operation id must be a canonical UUIDv4")
    return value


def _validate_release_sha(value: Any) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise DrCaError("release SHA must be a full lowercase Git SHA")
    return value


def _utc_seconds(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc_seconds(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or UTC_SECONDS_RE.fullmatch(value) is None:
        raise DrCaError(f"{label} is not an exact UTC-seconds timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise DrCaError(f"{label} is invalid") from exc


def _serial_for(
    operation_id: str,
    release_sha: str,
    generated_at: str,
) -> int:
    seed = f"{operation_id}:{release_sha}:{generated_at}".encode("ascii")
    value = int.from_bytes(hashlib.sha256(seed).digest()[:20], "big") >> 1
    return value or 1


def _canonical_paths(operation_id: str) -> dict[str, Path]:
    operation_root = SECRET_ROOT_PREFIX / operation_id
    tls_root = operation_root / "tls"
    return {
        "operation_root": operation_root,
        "tls_root": tls_root,
        "state": tls_root / STATE_NAME,
        "key": tls_root / KEY_NAME,
        "certificate": tls_root / CERTIFICATE_NAME,
        "attestation": tls_root / ATTESTATION_NAME,
        "lock": operation_root / LOCK_NAME,
    }


def _assert_directory(
    path: Path,
    *,
    owner_uid: int,
    exact_mode: int | None,
    label: str,
) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise DrCaError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or (
            exact_mode is not None
            and stat.S_IMODE(metadata.st_mode) != exact_mode
        )
        or (
            exact_mode is None
            and stat.S_IMODE(metadata.st_mode) & 0o022
        )
    ):
        raise DrCaError(f"{label} is not a trusted real directory")


def _ensure_private_child(
    parent: Path,
    child: Path,
    *,
    owner_uid: int,
    label: str,
) -> None:
    _assert_directory(
        parent,
        owner_uid=owner_uid,
        exact_mode=None,
        label=f"{label} parent",
    )
    if child.parent != parent:
        raise DrCaError(f"{label} path is not canonical")
    try:
        child.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise DrCaError(f"{label} could not be created") from exc
    _assert_directory(
        child,
        owner_uid=owner_uid,
        exact_mode=0o700,
        label=label,
    )


def _ensure_operation_directories(
    paths: dict[str, Path],
    *,
    owner_uid: int,
) -> None:
    _assert_directory(
        SECRET_ROOT_PREFIX,
        owner_uid=owner_uid,
        exact_mode=None,
        label="production shadow secret root",
    )
    _ensure_private_child(
        SECRET_ROOT_PREFIX,
        paths["operation_root"],
        owner_uid=owner_uid,
        label="operation secret directory",
    )
    _ensure_private_child(
        paths["operation_root"],
        paths["tls_root"],
        owner_uid=owner_uid,
        label="operation TLS directory",
    )


@contextmanager
def _operation_lock(path: Path, *, owner_uid: int):
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DrCaError("operation CA lock file is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise DrCaError("operation CA lock is unavailable") from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _read_strict_json(
    path: Path,
    *,
    label: str,
    owner_uid: int,
) -> tuple[dict[str, Any], bytes]:
    raw = read_secure_bytes(
        path,
        label=label,
        owner_uid=owner_uid,
        max_size=MAX_FILE_BYTES,
    )
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except DrCaError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise DrCaError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict) or raw != _canonical_json(value):
        raise DrCaError(f"{label} is not canonical JSON")
    return value, raw


def _publish_or_verify(
    path: Path,
    payload: bytes,
    *,
    label: str,
    owner_uid: int,
) -> str:
    if path.exists() or path.is_symlink():
        observed = read_secure_bytes(
            path,
            label=label,
            owner_uid=owner_uid,
            max_size=MAX_FILE_BYTES,
        )
        if observed != payload:
            raise DrCaError(f"existing {label} differs; overwrite is forbidden")
        return "reused"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=0o600,
            max_size=MAX_FILE_BYTES,
        )
    except SecureFileError as exc:
        raise DrCaError(f"{label} could not be published create-only") from exc
    return "created"


def _new_state(
    *,
    operation_id: str,
    release_sha: str,
    now: datetime,
) -> dict[str, Any]:
    generated = now.astimezone(timezone.utc).replace(microsecond=0)
    generated_at = _utc_seconds(generated)
    not_before = generated - NOT_BEFORE_SKEW
    not_after = generated + timedelta(days=VALIDITY_DAYS)
    serial = _serial_for(operation_id, release_sha, generated_at)
    return {
        "schema": STATE_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "generated_at": generated_at,
        "not_before": _utc_seconds(not_before),
        "not_after": _utc_seconds(not_after),
        "serial_hex": format(serial, "x"),
    }


def _validate_state(
    state: dict[str, Any],
    *,
    operation_id: str,
    release_sha: str,
) -> dict[str, Any]:
    if (
        set(state) != STATE_FIELDS
        or state.get("schema") != STATE_SCHEMA
        or state.get("operation_id") != operation_id
        or state.get("release_sha") != release_sha
        or not isinstance(state.get("serial_hex"), str)
        or SERIAL_RE.fullmatch(state["serial_hex"]) is None
    ):
        raise DrCaError("operation CA generation state identity is invalid")
    generated = _parse_utc_seconds(
        state["generated_at"],
        label="generation state generated_at",
    )
    not_before = _parse_utc_seconds(
        state["not_before"],
        label="generation state not_before",
    )
    not_after = _parse_utc_seconds(
        state["not_after"],
        label="generation state not_after",
    )
    expected_serial = _serial_for(
        operation_id,
        release_sha,
        state["generated_at"],
    )
    if (
        int(state["serial_hex"], 16) != expected_serial
        or generated - not_before != NOT_BEFORE_SKEW
        or not_after - generated != timedelta(days=VALIDITY_DAYS)
    ):
        raise DrCaError("operation CA generation state values are invalid")
    return state


def _validate_state_freshness(
    state: dict[str, Any],
    *,
    now: datetime,
) -> None:
    current = now.astimezone(timezone.utc)
    generated = _parse_utc_seconds(
        state["generated_at"],
        label="generation state generated_at",
    )
    not_before = _parse_utc_seconds(
        state["not_before"],
        label="generation state not_before",
    )
    not_after = _parse_utc_seconds(
        state["not_after"],
        label="generation state not_after",
    )
    if (
        generated > current + MAX_FUTURE_SKEW
        or current - generated > MAX_ATTESTATION_AGE
        or not_before > current
        or not_after - current < MIN_REMAINING_VALIDITY
    ):
        raise DrCaError(
            "operation CA generation state is stale, future-dated, or expiring"
        )


def _load_or_create_state(
    path: Path,
    *,
    operation_id: str,
    release_sha: str,
    owner_uid: int,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    if path.exists() or path.is_symlink():
        state, _ = _read_strict_json(
            path,
            label="operation CA generation state",
            owner_uid=owner_uid,
        )
        return (
            _validate_state(
                state,
                operation_id=operation_id,
                release_sha=release_sha,
            ),
            "reused",
        )
    state = _new_state(
        operation_id=operation_id,
        release_sha=release_sha,
        now=now,
    )
    publication = _publish_or_verify(
        path,
        _canonical_json(state),
        label="operation CA generation state",
        owner_uid=owner_uid,
    )
    return state, publication


def _load_private_key(
    payload: bytes,
) -> ec.EllipticCurvePrivateKey:
    if (
        not payload
        or len(payload) > MAX_FILE_BYTES
        or payload.count(b"-----BEGIN PRIVATE KEY-----") != 1
        or payload.count(b"-----END PRIVATE KEY-----") != 1
    ):
        raise DrCaError("operation CA private key encoding is invalid")
    try:
        key = serialization.load_pem_private_key(payload, password=None)
    except (TypeError, ValueError) as exc:
        raise DrCaError("operation CA private key is unreadable") from exc
    if (
        not isinstance(key, ec.EllipticCurvePrivateKey)
        or not isinstance(key.curve, ec.SECP256R1)
    ):
        raise DrCaError("operation CA private key algorithm differs")
    return key


def _load_or_create_private_key(
    path: Path,
    *,
    owner_uid: int,
) -> tuple[ec.EllipticCurvePrivateKey, bytes, str]:
    if path.exists() or path.is_symlink():
        payload = read_secure_bytes(
            path,
            label="operation CA private key",
            owner_uid=owner_uid,
            max_size=MAX_FILE_BYTES,
        )
        return _load_private_key(payload), payload, "reused"
    key = ec.generate_private_key(ec.SECP256R1())
    payload = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    publication = _publish_or_verify(
        path,
        payload,
        label="operation CA private key",
        owner_uid=owner_uid,
    )
    return key, payload, publication


def _expected_subject(operation_id: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(
                NameOID.COMMON_NAME,
                f"trading-bot-production-shadow-dr-{operation_id}",
            )
        ]
    )


def _certificate_bytes(
    *,
    key: ec.EllipticCurvePrivateKey,
    state: dict[str, Any],
    operation_id: str,
) -> bytes:
    subject = _expected_subject(operation_id)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(int(state["serial_hex"], 16))
        .not_valid_before(
            _parse_utc_seconds(
                state["not_before"],
                label="certificate not_before",
            )
        )
        .not_valid_after(
            _parse_utc_seconds(
                state["not_after"],
                label="certificate not_after",
            )
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=1),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def _certificate_time(
    certificate: x509.Certificate,
    *,
    after: bool,
) -> datetime:
    modern = (
        "not_valid_after_utc"
        if after
        else "not_valid_before_utc"
    )
    if hasattr(certificate, modern):
        return getattr(certificate, modern)
    legacy = (
        certificate.not_valid_after
        if after
        else certificate.not_valid_before
    )
    return legacy.replace(tzinfo=timezone.utc)


def _validate_certificate(
    payload: bytes,
    *,
    key: ec.EllipticCurvePrivateKey,
    state: dict[str, Any],
    operation_id: str,
) -> x509.Certificate:
    if (
        not payload
        or len(payload) > MAX_FILE_BYTES
        or any(marker in payload for marker in PRIVATE_KEY_MARKERS)
        or payload.count(b"-----BEGIN CERTIFICATE-----") != 1
        or payload.count(b"-----END CERTIFICATE-----") != 1
    ):
        raise DrCaError("operation CA certificate encoding is invalid")
    try:
        certificate = x509.load_pem_x509_certificate(payload)
        basic = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        )
        usage = certificate.extensions.get_extension_for_class(
            x509.KeyUsage
        )
    except (ValueError, x509.ExtensionNotFound) as exc:
        raise DrCaError("operation CA certificate is invalid") from exc
    expected_subject = _expected_subject(operation_id)
    public_expected = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_observed = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if (
        certificate.subject != expected_subject
        or certificate.issuer != expected_subject
        or certificate.serial_number != int(state["serial_hex"], 16)
        or _utc_seconds(_certificate_time(certificate, after=False))
        != state["not_before"]
        or _utc_seconds(_certificate_time(certificate, after=True))
        != state["not_after"]
        or public_observed != public_expected
        or basic.critical is not True
        or basic.value.ca is not True
        or basic.value.path_length != 1
        or usage.critical is not True
        or usage.value.key_cert_sign is not True
        or usage.value.crl_sign is not True
        or usage.value.digital_signature is not True
    ):
        raise DrCaError("operation CA certificate contract differs")
    try:
        certificate.verify_directly_issued_by(certificate)
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise DrCaError("operation CA certificate self-signature is invalid") from exc
    return certificate


def _load_or_create_certificate(
    path: Path,
    *,
    key: ec.EllipticCurvePrivateKey,
    state: dict[str, Any],
    operation_id: str,
    owner_uid: int,
) -> tuple[x509.Certificate, bytes, str]:
    if path.exists() or path.is_symlink():
        payload = read_secure_bytes(
            path,
            label="operation CA certificate",
            owner_uid=owner_uid,
            max_size=MAX_FILE_BYTES,
        )
        return (
            _validate_certificate(
                payload,
                key=key,
                state=state,
                operation_id=operation_id,
            ),
            payload,
            "reused",
        )
    payload = _certificate_bytes(
        key=key,
        state=state,
        operation_id=operation_id,
    )
    publication = _publish_or_verify(
        path,
        payload,
        label="operation CA certificate",
        owner_uid=owner_uid,
    )
    return (
        _validate_certificate(
            payload,
            key=key,
            state=state,
            operation_id=operation_id,
        ),
        payload,
        publication,
    )


def _attestation(
    *,
    operation_id: str,
    release_sha: str,
    state: dict[str, Any],
    certificate: x509.Certificate,
    certificate_bytes: bytes,
) -> dict[str, Any]:
    return {
        "schema": ATTESTATION_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "ca_sha256": hashlib.sha256(certificate_bytes).hexdigest(),
        "ca_subject": certificate.subject.rfc4514_string(),
        "ca_serial_hex": format(certificate.serial_number, "x"),
        "not_before": state["not_before"],
        "not_after": state["not_after"],
        "generated_at": state["generated_at"],
        "private_key_mode": "0600",
        "private_key_retained_on_controller": True,
        "old_tls_material_reused": False,
    }


def _validate_attestation(
    document: dict[str, Any],
    *,
    expected: dict[str, Any],
) -> None:
    if set(document) != ATTESTATION_FIELDS or document != expected:
        raise DrCaError("operation CA attestation differs from generated material")


def generate_dr_ca(
    *,
    operation_id: str,
    release_sha: str,
    owner_uid: int = 0,
    now: datetime | None = None,
    checkpoint: Checkpoint = _noop_checkpoint,
) -> dict[str, Any]:
    operation_id = _canonical_operation_id(operation_id)
    release_sha = _validate_release_sha(release_sha)
    paths = _canonical_paths(operation_id)
    _ensure_operation_directories(paths, owner_uid=owner_uid)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise DrCaError("current time must be timezone-aware")
    with _operation_lock(paths["lock"], owner_uid=owner_uid):
        state, state_publication = _load_or_create_state(
            paths["state"],
            operation_id=operation_id,
            release_sha=release_sha,
            owner_uid=owner_uid,
            now=current,
        )
        _validate_state_freshness(state, now=current)
        checkpoint("after-state")
        key, key_bytes, key_publication = _load_or_create_private_key(
            paths["key"],
            owner_uid=owner_uid,
        )
        checkpoint("after-key")
        certificate, certificate_bytes, certificate_publication = (
            _load_or_create_certificate(
                paths["certificate"],
                key=key,
                state=state,
                operation_id=operation_id,
                owner_uid=owner_uid,
            )
        )
        checkpoint("after-certificate")
        attestation = _attestation(
            operation_id=operation_id,
            release_sha=release_sha,
            state=state,
            certificate=certificate,
            certificate_bytes=certificate_bytes,
        )
        if paths["attestation"].exists() or paths["attestation"].is_symlink():
            observed, observed_bytes = _read_strict_json(
                paths["attestation"],
                label="operation CA attestation",
                owner_uid=owner_uid,
            )
            _validate_attestation(observed, expected=attestation)
            if observed_bytes != _canonical_json(attestation):
                raise DrCaError("operation CA attestation bytes differ")
            attestation_publication = "reused"
        else:
            attestation_publication = _publish_or_verify(
                paths["attestation"],
                _canonical_json(attestation),
                label="operation CA attestation",
                owner_uid=owner_uid,
            )
        checkpoint("after-attestation")

        # Re-read every output after publication.  No result is trusted from
        # pre-publication in-memory bytes alone.
        observed_key = read_secure_bytes(
            paths["key"],
            label="operation CA private key",
            owner_uid=owner_uid,
            max_size=MAX_FILE_BYTES,
        )
        if observed_key != key_bytes:
            raise DrCaError("operation CA private key changed after publication")
        observed_certificate = read_secure_bytes(
            paths["certificate"],
            label="operation CA certificate",
            owner_uid=owner_uid,
            max_size=MAX_FILE_BYTES,
        )
        _validate_certificate(
            observed_certificate,
            key=_load_private_key(observed_key),
            state=state,
            operation_id=operation_id,
        )
        observed_attestation, observed_attestation_bytes = _read_strict_json(
            paths["attestation"],
            label="operation CA attestation",
            owner_uid=owner_uid,
        )
        _validate_attestation(observed_attestation, expected=attestation)

    return {
        "schema": ATTESTATION_SCHEMA,
        "status": "fresh-operation-dr-ca-ready",
        "operation_id": operation_id,
        "release_sha": release_sha,
        "certificate_path": str(paths["certificate"]),
        "certificate_sha256": hashlib.sha256(
            observed_certificate
        ).hexdigest(),
        "attestation_path": str(paths["attestation"]),
        "attestation_sha256": hashlib.sha256(
            observed_attestation_bytes
        ).hexdigest(),
        "generated_at": state["generated_at"],
        "not_after": state["not_after"],
        "private_key_path": str(paths["key"]),
        "private_key_exported": False,
        "private_key_retained_on_controller": True,
        "old_tls_material_reused": False,
        "publication": {
            "state": state_publication,
            "private_key": key_publication,
            "certificate": certificate_publication,
            "attestation": attestation_publication,
        },
        "live_io_performed": False,
        "services_mutated": False,
        "routes_mutated": False,
        "data_mutated": False,
        "object_storage_mutated": False,
    }


def confirmation_phrase(operation_id: str, release_sha: str) -> str:
    return (
        "CREATE-FRESH-PRODUCTION-SHADOW-DR-CA:"
        f"{operation_id}:{release_sha}"
    )


def build_plan(operation_id: str, release_sha: str) -> dict[str, Any]:
    operation_id = _canonical_operation_id(operation_id)
    release_sha = _validate_release_sha(release_sha)
    paths = _canonical_paths(operation_id)
    return {
        "status": "planned",
        "operation_id": operation_id,
        "release_sha": release_sha,
        "required_confirmation": confirmation_phrase(
            operation_id,
            release_sha,
        ),
        "private_key_path": str(paths["key"]),
        "certificate_path": str(paths["certificate"]),
        "attestation_path": str(paths["attestation"]),
        "private_key_exported": False,
        "old_tls_material_reused": False,
        "live_io_performed": False,
        "services_mutated": False,
        "routes_mutated": False,
        "data_mutated": False,
        "object_storage_mutated": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        if os.geteuid() != 0:
            raise DrCaError("production shadow DR CA producer must run as root")
        args = build_parser().parse_args(
            sys.argv[1:] if argv is None else argv
        )
        plan = build_plan(args.operation_id, args.release_sha)
        if not args.apply:
            result = plan
        else:
            if args.confirm != plan["required_confirmation"]:
                raise DrCaError(
                    "fresh operation CA confirmation is missing or stale"
                )
            result = generate_dr_ca(
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                owner_uid=0,
            )
    except (DrCaError, SecureFileError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "live_io_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
