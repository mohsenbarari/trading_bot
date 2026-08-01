"""Root-owned, append-only Witness ledger for WA-IR preflight evidence.

This is the local Witness side of the narrow WA-IR -> Witness evidence route.
It accepts one already-delivered canonical envelope through an injected
transport boundary, verifies the pinned WA-IR signature and fresh campaign
binding, and durably appends a Witness-signed evidence record.  It does not
implement HTTP, SSH, Object Storage, age, a Writer-Witness term, promotion,
execution, or any cross-site control path.

The fixed state root must be provisioned beforehand.  An accepted envelope is
single-use: retrying its hash, attestation id, or nonce fails closed instead
of issuing a second Witness receipt.  A separate read-only command/transport
may later call ``collect_pinned_evidence``; this module deliberately gives it
no selector for arbitrary historical evidence.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import dedicated_host_preflight_ir_witness_attestation as _attestation


__all__ = (
    "FIXED_WITNESS_PREFLIGHT_ATTESTATION_LEDGER_STATE_ROOT",
    "WITNESS_PREFLIGHT_ATTESTATION_LEDGER_DEFAULT_ENABLED",
    "WITNESS_PREFLIGHT_ATTESTATION_LEDGER_SCHEMA",
    "DedicatedHostPreflightWitnessAttestationLedgerError",
    "RootOwnedWitnessPreflightAttestationLedger",
    "RootOwnedWitnessPreflightAttestationLedgerConfig",
    "WitnessPreflightAttestationIngress",
)


WITNESS_PREFLIGHT_ATTESTATION_LEDGER_SCHEMA = (
    "three-site-dedicated-host-preflight-witness-attestation-ledger-v1"
)
WITNESS_PREFLIGHT_ATTESTATION_LEDGER_DEFAULT_ENABLED = False

FIXED_WITNESS_PREFLIGHT_ATTESTATION_LEDGER_STATE_ROOT = Path(
    "/var/lib/trading-bot/dedicated-host-preflight/witness-wa-ir-attestation-ledger"
)

_STATE_VERSION = 1
_STATE_FILENAME = "witness-wa-ir-attestation-ledger.json"
_LOCK_FILENAME = "witness-wa-ir-attestation-ledger.lock"
_TEMP_PREFIX = ".witness-wa-ir-attestation-"
_MAX_STATE_BYTES = 1024 * 1024
_MAX_ENTRIES = 32
_ZERO_SHA256 = "0" * 64
_STATE_FIELDS = frozenset(
    {
        "schema",
        "version",
        "entries",
        "writer_authorized",
        "promotion_authorized",
        "execution_authorized",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "sequence",
        "attestation_sha256",
        "attestation_id",
        "nonce",
        "accepted_at",
        "evidence",
        "evidence_sha256",
        "previous_entry_sha256",
        "entry_sha256",
    }
)


class DedicatedHostPreflightWitnessAttestationLedgerError(ValueError):
    """A fixed redacted refusal from the local Witness evidence ledger."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WitnessPreflightAttestationIngress(Protocol):
    """The only injection point a future WA-IR->Witness transport may use."""

    def accept_wa_ir_attestation(
        self,
        *,
        canonical_envelope: bytes,
        now: datetime,
    ) -> bytes: ...


@dataclass(frozen=True)
class RootOwnedWitnessPreflightAttestationLedgerConfig:
    """Default-off non-secret policy for exactly one pinned WA-IR request."""

    expected_request: _attestation.ParsedWaIrWitnessAttestationRequest | None = None
    expected_wa_ir_public_key: bytes = b""
    expected_witness_public_key: bytes = b""
    enabled: bool = WITNESS_PREFLIGHT_ATTESTATION_LEDGER_DEFAULT_ENABLED
    maximum_entries: int = _MAX_ENTRIES


@dataclass(frozen=True)
class _Facts:
    request: _attestation.ParsedWaIrWitnessAttestationRequest
    wa_ir_public_key: bytes
    witness_public_key: bytes
    maximum_entries: int


@dataclass(frozen=True)
class _Entry:
    sequence: int
    attestation_sha256: str
    attestation_id: str
    nonce: str
    accepted_at: datetime
    canonical_evidence: bytes
    evidence_sha256: str
    previous_entry_sha256: str
    entry_sha256: str


def _fail(code: str) -> None:
    raise DedicatedHostPreflightWitnessAttestationLedgerError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DedicatedHostPreflightWitnessAttestationLedgerError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("WITNESS_PREFLIGHT_LEDGER_STATE_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("WITNESS_PREFLIGHT_LEDGER_STATE_JSON_INVALID")


def _sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        or value == _ZERO_SHA256
    ):
        _fail(code)
    return value


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat().replace("+00:00", "Z") != value:
        _fail(code)
    return normalized


def _render_timestamp(value: datetime, *, code: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, TypeError, ValueError):
        _fail(code)
    return value


def _request(value: object, *, code: str) -> _attestation.ParsedWaIrWitnessAttestationRequest:
    if type(value) is not _attestation.ParsedWaIrWitnessAttestationRequest:
        _fail(code)
    try:
        result = _attestation.parse_wa_ir_witness_attestation_request(value.canonical_request)
    except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
        raise DedicatedHostPreflightWitnessAttestationLedgerError(code) from exc
    if result != value:
        _fail(code)
    return result


def _facts(value: object, *, witness_signer: object) -> _Facts:
    if type(value) is not RootOwnedWitnessPreflightAttestationLedgerConfig or value.enabled is not True:
        _fail("WITNESS_PREFLIGHT_LEDGER_DISABLED")
    request = _request(value.expected_request, code="WITNESS_PREFLIGHT_LEDGER_CONFIG_INVALID")
    wa_ir_public_key = _public_key(
        value.expected_wa_ir_public_key,
        code="WITNESS_PREFLIGHT_LEDGER_CONFIG_INVALID",
    )
    witness_public_key = _public_key(
        value.expected_witness_public_key,
        code="WITNESS_PREFLIGHT_LEDGER_CONFIG_INVALID",
    )
    if type(value.maximum_entries) is not int or not 1 <= value.maximum_entries <= _MAX_ENTRIES:
        _fail("WITNESS_PREFLIGHT_LEDGER_CONFIG_INVALID")
    if not isinstance(witness_signer, Ed25519PrivateKey):
        _fail("WITNESS_PREFLIGHT_LEDGER_SIGNER_INVALID")
    try:
        actual_public_key = witness_signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (TypeError, ValueError):
        _fail("WITNESS_PREFLIGHT_LEDGER_SIGNER_INVALID")
    if actual_public_key != witness_public_key:
        _fail("WITNESS_PREFLIGHT_LEDGER_SIGNER_INVALID")
    return _Facts(
        request=request,
        wa_ir_public_key=wa_ir_public_key,
        witness_public_key=witness_public_key,
        maximum_entries=value.maximum_entries,
    )


def _require_root() -> None:
    try:
        root = os.geteuid() == 0
    except OSError:
        root = False
    if not root:
        _fail("WITNESS_PREFLIGHT_LEDGER_ROOT_RUNTIME_REQUIRED")


def _validate_ancestors(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_ROOT_UNSAFE")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("WITNESS_PREFLIGHT_LEDGER_PLATFORM_UNSUPPORTED")
    descriptor = -1
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                _fail("WITNESS_PREFLIGHT_LEDGER_STATE_ROOT_UNSAFE")
    except DedicatedHostPreflightWitnessAttestationLedgerError:
        raise
    except OSError:
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_ROOT_UNSAFE")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_state_root() -> int:
    root = FIXED_WITNESS_PREFLIGHT_ATTESTATION_LEDGER_STATE_ROOT
    _validate_ancestors(root)
    try:
        before = os.lstat(root)
        resolved = root.resolve(strict=True)
    except OSError:
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_ROOT_UNSAFE")
    if (
        resolved != root
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) != 0o700
    ):
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_ROOT_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        after = os.lstat(root)
        fingerprint = (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_nlink)
        if (
            (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid, opened.st_nlink) != fingerprint
            or (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_nlink) != fingerprint
        ):
            _fail("WITNESS_PREFLIGHT_LEDGER_STATE_ROOT_UNSAFE")
        return descriptor
    except DedicatedHostPreflightWitnessAttestationLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_ROOT_UNSAFE")


def _safe_file_metadata(
    root_fd: int,
    name: str,
    *,
    maximum_bytes: int | None,
    missing_ok: bool,
    code: str,
) -> os.stat_result | None:
    try:
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        _fail(code)
    except OSError:
        _fail(code)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (maximum_bytes is not None and not 1 <= metadata.st_size <= maximum_bytes)
    ):
        _fail(code)
    return metadata


def _fsync(descriptor: int, *, code: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        _fail(code)


def _open_lock(root_fd: int) -> int:
    descriptor = -1
    try:
        flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(
                _LOCK_FILENAME,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=root_fd,
            )
            os.fchmod(descriptor, 0o600)
            _fsync(descriptor, code="WITNESS_PREFLIGHT_LEDGER_LOCK_FSYNC_FAILED")
            _fsync(root_fd, code="WITNESS_PREFLIGHT_LEDGER_DIRECTORY_FSYNC_FAILED")
        except FileExistsError:
            descriptor = os.open(_LOCK_FILENAME, flags, dir_fd=root_fd)
        before = _safe_file_metadata(
            root_fd,
            _LOCK_FILENAME,
            maximum_bytes=None,
            missing_ok=False,
            code="WITNESS_PREFLIGHT_LEDGER_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_file_metadata(
            root_fd,
            _LOCK_FILENAME,
            maximum_bytes=None,
            missing_ok=False,
            code="WITNESS_PREFLIGHT_LEDGER_LOCK_UNSAFE",
        )
        if (
            before is None
            or after is None
            or (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid, opened.st_nlink)
            != (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_nlink)
            or (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_nlink)
            != (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_nlink)
        ):
            _fail("WITNESS_PREFLIGHT_LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except DedicatedHostPreflightWitnessAttestationLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("WITNESS_PREFLIGHT_LEDGER_LOCK_UNSAFE")


@contextmanager
def _locked_root() -> Iterator[int]:
    root_fd = _open_state_root()
    lock_fd = -1
    try:
        lock_fd = _open_lock(root_fd)
        yield root_fd
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
        try:
            os.close(root_fd)
        except OSError:
            pass


def _entry_unsigned(entry: _Entry) -> dict[str, Any]:
    evidence = json.loads(entry.canonical_evidence.decode("ascii"))
    return {
        "sequence": entry.sequence,
        "attestation_sha256": entry.attestation_sha256,
        "attestation_id": entry.attestation_id,
        "nonce": entry.nonce,
        "accepted_at": _render_timestamp(entry.accepted_at, code="WITNESS_PREFLIGHT_LEDGER_ENTRY_INVALID"),
        "evidence": evidence,
        "evidence_sha256": entry.evidence_sha256,
        "previous_entry_sha256": entry.previous_entry_sha256,
    }


def _entry_mapping(entry: _Entry) -> dict[str, Any]:
    return {**_entry_unsigned(entry), "entry_sha256": entry.entry_sha256}


def _state_mapping(entries: tuple[_Entry, ...]) -> dict[str, Any]:
    return {
        "schema": WITNESS_PREFLIGHT_ATTESTATION_LEDGER_SCHEMA,
        "version": _STATE_VERSION,
        "entries": [_entry_mapping(entry) for entry in entries],
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }


def _parse_entry(value: object, *, facts: _Facts, code: str) -> _Entry:
    if not isinstance(value, Mapping) or set(value) != _ENTRY_FIELDS:
        _fail(code)
    sequence = value["sequence"]
    if type(sequence) is not int or sequence < 1:
        _fail(code)
    attestation_sha256 = _sha256(value["attestation_sha256"], code=code)
    attestation_id = value["attestation_id"]
    nonce = value["nonce"]
    if type(attestation_id) is not str or type(nonce) is not str:
        _fail(code)
    accepted_at = _timestamp(value["accepted_at"], code=code)
    evidence_value = value["evidence"]
    canonical_evidence = _canonical(evidence_value, code=code) + b"\n"
    if len(canonical_evidence) > _attestation.MAX_WA_IR_WITNESS_ATTESTATION_BYTES * 2:
        _fail(code)
    evidence_sha256 = _sha256(value["evidence_sha256"], code=code)
    if evidence_sha256 != hashlib.sha256(canonical_evidence).hexdigest():
        _fail(code)
    try:
        verified = _attestation.verify_witness_preflight_evidence(
            canonical_evidence=canonical_evidence,
            expected_request=facts.request,
            expected_wa_ir_public_key=facts.wa_ir_public_key,
            expected_witness_public_key=facts.witness_public_key,
            now=accepted_at,
        )
    except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
        raise DedicatedHostPreflightWitnessAttestationLedgerError(code) from exc
    if (
        verified.wa_ir_attestation.envelope_sha256 != attestation_sha256
        or verified.wa_ir_attestation.attestation_id != attestation_id
        or verified.wa_ir_attestation.nonce != nonce
        or verified.accepted_at != accepted_at
    ):
        _fail(code)
    previous = value["previous_entry_sha256"]
    if type(previous) is not str or len(previous) != 64 or any(
        character not in "0123456789abcdef" for character in previous
    ):
        _fail(code)
    entry = _Entry(
        sequence=sequence,
        attestation_sha256=attestation_sha256,
        attestation_id=attestation_id,
        nonce=nonce,
        accepted_at=accepted_at,
        canonical_evidence=canonical_evidence,
        evidence_sha256=evidence_sha256,
        previous_entry_sha256=previous,
        entry_sha256=_sha256(value["entry_sha256"], code=code),
    )
    if entry.entry_sha256 != hashlib.sha256(
        _canonical(_entry_unsigned(entry), code=code)
    ).hexdigest():
        _fail(code)
    return entry


def _parse_state(raw: bytes | None, *, facts: _Facts) -> tuple[_Entry, ...]:
    if raw is None:
        return ()
    if not raw or len(raw) > _MAX_STATE_BYTES:
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_INVALID")
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DedicatedHostPreflightWitnessAttestationLedgerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_INVALID")
    if type(value) is not dict or raw != _canonical(value, code="WITNESS_PREFLIGHT_LEDGER_STATE_INVALID") + b"\n":
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_INVALID")
    if (
        set(value) != _STATE_FIELDS
        or value["schema"] != WITNESS_PREFLIGHT_ATTESTATION_LEDGER_SCHEMA
        or value["version"] != _STATE_VERSION
        or value["writer_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["execution_authorized"] is not False
        or not isinstance(value["entries"], list)
        or len(value["entries"]) > facts.maximum_entries
    ):
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_INVALID")
    entries = tuple(
        _parse_entry(item, facts=facts, code="WITNESS_PREFLIGHT_LEDGER_STATE_INVALID")
        for item in value["entries"]
    )
    previous = _ZERO_SHA256
    seen_attestation_sha256es: set[str] = set()
    seen_ids: set[str] = set()
    seen_nonces: set[str] = set()
    for sequence, entry in enumerate(entries, start=1):
        if (
            entry.sequence != sequence
            or entry.previous_entry_sha256 != previous
            or entry.attestation_sha256 in seen_attestation_sha256es
            or entry.attestation_id in seen_ids
            or entry.nonce in seen_nonces
        ):
            _fail("WITNESS_PREFLIGHT_LEDGER_STATE_INVALID")
        previous = entry.entry_sha256
        seen_attestation_sha256es.add(entry.attestation_sha256)
        seen_ids.add(entry.attestation_id)
        seen_nonces.add(entry.nonce)
    return entries


def _read_state(root_fd: int, *, facts: _Facts) -> tuple[_Entry, ...]:
    metadata = _safe_file_metadata(
        root_fd,
        _STATE_FILENAME,
        maximum_bytes=_MAX_STATE_BYTES,
        missing_ok=True,
        code="WITNESS_PREFLIGHT_LEDGER_STATE_UNSAFE",
    )
    if metadata is None:
        return ()
    descriptor = -1
    try:
        descriptor = os.open(
            _STATE_FILENAME,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        opened = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
            opened.st_size,
        ) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_nlink,
            metadata.st_size,
        ):
            _fail("WITNESS_PREFLIGHT_LEDGER_STATE_UNSAFE")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                _fail("WITNESS_PREFLIGHT_LEDGER_STATE_UNSAFE")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail("WITNESS_PREFLIGHT_LEDGER_STATE_UNSAFE")
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
        ) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_nlink,
            metadata.st_size,
        ):
            _fail("WITNESS_PREFLIGHT_LEDGER_STATE_UNSAFE")
        return _parse_state(b"".join(chunks), facts=facts)
    except DedicatedHostPreflightWitnessAttestationLedgerError:
        raise
    except OSError:
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_UNSAFE")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_state(root_fd: int, *, entries: tuple[_Entry, ...]) -> None:
    raw = _canonical(_state_mapping(entries), code="WITNESS_PREFLIGHT_LEDGER_STATE_INVALID") + b"\n"
    if not 1 <= len(raw) <= _MAX_STATE_BYTES:
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_INVALID")
    temporary_name = _TEMP_PREFIX + secrets.token_hex(16)
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        created = True
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                _fail("WITNESS_PREFLIGHT_LEDGER_STATE_WRITE_FAILED")
            offset += written
        _fsync(descriptor, code="WITNESS_PREFLIGHT_LEDGER_STATE_FSYNC_FAILED")
        os.close(descriptor)
        descriptor = -1
        metadata = _safe_file_metadata(
            root_fd,
            temporary_name,
            maximum_bytes=_MAX_STATE_BYTES,
            missing_ok=False,
            code="WITNESS_PREFLIGHT_LEDGER_STATE_WRITE_FAILED",
        )
        if metadata is None or metadata.st_size != len(raw):
            _fail("WITNESS_PREFLIGHT_LEDGER_STATE_WRITE_FAILED")
        os.replace(
            temporary_name,
            _STATE_FILENAME,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        created = False
        _fsync(root_fd, code="WITNESS_PREFLIGHT_LEDGER_DIRECTORY_FSYNC_FAILED")
    except DedicatedHostPreflightWitnessAttestationLedgerError:
        raise
    except OSError:
        _fail("WITNESS_PREFLIGHT_LEDGER_STATE_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if created:
            try:
                os.unlink(temporary_name, dir_fd=root_fd)
            except OSError:
                pass


class RootOwnedWitnessPreflightAttestationLedger:
    """One default-off append-only Witness ledger with a fixed root."""

    def __init__(
        self,
        *,
        config: RootOwnedWitnessPreflightAttestationLedgerConfig,
        witness_signer: Ed25519PrivateKey,
    ) -> None:
        _require_root()
        self._facts = _facts(config, witness_signer=witness_signer)
        self._witness_signer = witness_signer

    def accept_wa_ir_attestation(
        self,
        *,
        canonical_envelope: bytes,
        now: datetime,
    ) -> bytes:
        """Verify, single-use admit, persist, and Witness-sign one envelope."""

        try:
            verified = _attestation.verify_wa_ir_witness_attestation_envelope(
                canonical_envelope=canonical_envelope,
                expected_request=self._facts.request,
                expected_wa_ir_public_key=self._facts.wa_ir_public_key,
                now=now,
            )
        except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
            raise DedicatedHostPreflightWitnessAttestationLedgerError(
                "WITNESS_PREFLIGHT_LEDGER_ENVELOPE_INVALID"
            ) from exc
        with _locked_root() as root_fd:
            entries = _read_state(root_fd, facts=self._facts)
            if (
                any(entry.attestation_sha256 == verified.envelope_sha256 for entry in entries)
                or any(entry.attestation_id == verified.attestation_id for entry in entries)
                or any(entry.nonce == verified.nonce for entry in entries)
            ):
                _fail("WITNESS_PREFLIGHT_LEDGER_REPLAYED")
            if len(entries) >= self._facts.maximum_entries:
                _fail("WITNESS_PREFLIGHT_LEDGER_CAPACITY_EXHAUSTED")
            try:
                canonical_evidence = _attestation.build_witness_preflight_evidence(
                    wa_ir_attestation=verified,
                    witness_signer=self._witness_signer,
                    accepted_at=now,
                )
                evidence = _attestation.verify_witness_preflight_evidence(
                    canonical_evidence=canonical_evidence,
                    expected_request=self._facts.request,
                    expected_wa_ir_public_key=self._facts.wa_ir_public_key,
                    expected_witness_public_key=self._facts.witness_public_key,
                    now=now,
                )
            except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
                raise DedicatedHostPreflightWitnessAttestationLedgerError(
                    "WITNESS_PREFLIGHT_LEDGER_EVIDENCE_INVALID"
                ) from exc
            previous = entries[-1].entry_sha256 if entries else _ZERO_SHA256
            provisional = _Entry(
                sequence=len(entries) + 1,
                attestation_sha256=verified.envelope_sha256,
                attestation_id=verified.attestation_id,
                nonce=verified.nonce,
                accepted_at=evidence.accepted_at,
                canonical_evidence=canonical_evidence,
                evidence_sha256=evidence.evidence_sha256,
                previous_entry_sha256=previous,
                entry_sha256="",
            )
            entry = _Entry(
                **{
                    **provisional.__dict__,
                    "entry_sha256": hashlib.sha256(
                        _canonical(_entry_unsigned(provisional), code="WITNESS_PREFLIGHT_LEDGER_ENTRY_INVALID")
                    ).hexdigest(),
                }
            )
            updated = (*entries, entry)
            _write_state(root_fd, entries=updated)
            reread = _read_state(root_fd, facts=self._facts)
            if reread != updated:
                _fail("WITNESS_PREFLIGHT_LEDGER_STATE_WRITE_FAILED")
            return canonical_evidence

    def collect_pinned_evidence(self) -> bytes:
        """Return only evidence for this fixed request; no caller selector exists."""

        with _locked_root() as root_fd:
            entries = _read_state(root_fd, facts=self._facts)
            matching = tuple(
                entry
                for entry in entries
                if entry.attestation_id == self._facts.request.attestation_id
                and entry.nonce == self._facts.request.nonce
            )
            if len(matching) != 1:
                _fail("WITNESS_PREFLIGHT_LEDGER_PINNED_EVIDENCE_MISSING")
            return matching[0].canonical_evidence
