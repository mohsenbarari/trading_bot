"""Root-only local WA-IR attester for Witness preflight evidence.

The attester has no network, Object Storage, age, SSH, Docker, service, or
Writer-Witness capability.  It opens only its two fixed root-owned inputs:
one non-secret canonical campaign request and one dedicated preflight signing
key record.  A caller supplies the already canonical v2 WA-IR receipt and
receives a signed envelope.  Delivery to Witness is intentionally an injected
future boundary, never hidden in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import stat

from core import dedicated_host_preflight_ir_witness_attestation as _attestation


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_IR_WITNESS_ATTESTATION_RUNTIME_DEFAULT_ENABLED",
    "FIXED_WA_IR_WITNESS_ATTESTATION_KEY_FILE",
    "FIXED_WA_IR_WITNESS_ATTESTATION_REQUEST_FILE",
    "DedicatedHostPreflightIrWitnessAttestationRuntimeError",
    "RootOwnedWaIrWitnessAttestationRuntimeConfig",
    "attest_root_owned_wa_ir_preflight_receipt",
    "load_root_owned_wa_ir_witness_attestation_request",
    "load_root_owned_wa_ir_witness_attestation_signer",
)


DEDICATED_HOST_PREFLIGHT_IR_WITNESS_ATTESTATION_RUNTIME_DEFAULT_ENABLED = False

# These files intentionally differ from the WA-IR age identity, Object
# Storage credential, and Writer-Witness key locations.  The key record's
# schema/purpose is separately checked before it is used.
FIXED_WA_IR_WITNESS_ATTESTATION_REQUEST_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/wa-ir-witness-attestation-request.json"
)
FIXED_WA_IR_WITNESS_ATTESTATION_KEY_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/wa-ir-witness-attestation-key.json"
)

_MAX_REQUEST_BYTES = _attestation.MAX_WA_IR_WITNESS_ATTESTATION_REQUEST_BYTES
_MAX_KEY_BYTES = _attestation.MAX_WA_IR_WITNESS_ATTESTATION_KEY_BYTES


class DedicatedHostPreflightIrWitnessAttestationRuntimeError(ValueError):
    """A redacted refusal from the root-only WA-IR attester."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedWaIrWitnessAttestationRuntimeConfig:
    """Default-off local policy; it has no caller-selectable file paths."""

    enabled: bool = DEDICATED_HOST_PREFLIGHT_IR_WITNESS_ATTESTATION_RUNTIME_DEFAULT_ENABLED


def _fail(code: str) -> None:
    raise DedicatedHostPreflightIrWitnessAttestationRuntimeError(code)


def _require_root() -> None:
    try:
        is_root = os.geteuid() == 0
    except OSError:
        is_root = False
    if not is_root:
        _fail("WA_IR_WITNESS_ATTESTATION_ROOT_RUNTIME_REQUIRED")


def _fixed_path(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        part in {"", ".", ".."} for part in value.parts[1:]
    ):
        _fail(code)
    return value


def _validate_ancestors(path: Path, *, code: str) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail(code)
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
                _fail(code)
    except DedicatedHostPreflightIrWitnessAttestationRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_root_owned_file(
    path: Path,
    *,
    exact_mode: int,
    maximum_bytes: int,
    code: str,
) -> bytes:
    path = _fixed_path(path, code=code)
    _validate_ancestors(path, code=code)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != exact_mode
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
        )
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
            opened.st_size,
        ) != identity:
            _fail(code)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                _fail(code)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
        ) != identity:
            _fail(code)
        return b"".join(chunks)
    except DedicatedHostPreflightIrWitnessAttestationRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _require_enabled(config: object) -> RootOwnedWaIrWitnessAttestationRuntimeConfig:
    if type(config) is not RootOwnedWaIrWitnessAttestationRuntimeConfig or config.enabled is not True:
        _fail("WA_IR_WITNESS_ATTESTATION_RUNTIME_DISABLED")
    return config


def load_root_owned_wa_ir_witness_attestation_request() -> _attestation.ParsedWaIrWitnessAttestationRequest:
    """Load only the fixed root-owned non-secret campaign request."""

    _require_root()
    raw = _read_root_owned_file(
        FIXED_WA_IR_WITNESS_ATTESTATION_REQUEST_FILE,
        exact_mode=0o600,
        maximum_bytes=_MAX_REQUEST_BYTES,
        code="WA_IR_WITNESS_ATTESTATION_REQUEST_FILE_UNSAFE",
    )
    try:
        return _attestation.parse_wa_ir_witness_attestation_request(raw)
    except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
        raise DedicatedHostPreflightIrWitnessAttestationRuntimeError(
            "WA_IR_WITNESS_ATTESTATION_REQUEST_FILE_INVALID"
        ) from exc


def load_root_owned_wa_ir_witness_attestation_signer():
    """Load one dedicated 0400 key record without returning raw key material."""

    _require_root()
    raw = _read_root_owned_file(
        FIXED_WA_IR_WITNESS_ATTESTATION_KEY_FILE,
        exact_mode=0o400,
        maximum_bytes=_MAX_KEY_BYTES,
        code="WA_IR_WITNESS_ATTESTATION_KEY_FILE_UNSAFE",
    )
    try:
        return _attestation.parse_wa_ir_witness_attestation_key_record(raw)
    except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
        raise DedicatedHostPreflightIrWitnessAttestationRuntimeError(
            "WA_IR_WITNESS_ATTESTATION_KEY_FILE_INVALID"
        ) from exc


def attest_root_owned_wa_ir_preflight_receipt(
    *,
    canonical_receipt: bytes,
    config: RootOwnedWaIrWitnessAttestationRuntimeConfig = RootOwnedWaIrWitnessAttestationRuntimeConfig(),
    now: datetime | None = None,
) -> bytes:
    """Sign one supplied canonical v2 WA-IR receipt; no delivery occurs here."""

    _require_root()
    _require_enabled(config)
    request = load_root_owned_wa_ir_witness_attestation_request()
    signer = load_root_owned_wa_ir_witness_attestation_signer()
    observed_now = datetime.now(timezone.utc) if now is None else now
    try:
        return _attestation.build_wa_ir_witness_attestation_envelope(
            request=request,
            canonical_receipt=canonical_receipt,
            signer=signer,
            issued_at=observed_now,
        )
    except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
        raise DedicatedHostPreflightIrWitnessAttestationRuntimeError(
            "WA_IR_WITNESS_ATTESTATION_ENVELOPE_REJECTED"
        ) from exc
