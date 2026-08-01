"""Root-owned central verifier policy for Witness-returned WA-IR evidence.

This module has one narrow job: load the public, campaign-specific dual
signature verification pins from one fixed root-only file and construct the
configuration consumed by the separate literal Witness SSH adapter.  It does
not contain a WA-IR private key, Object-Storage credential, age identity,
locator, ingress transport, network client, subprocess, or deployment action.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any

from core import dedicated_host_preflight_ir_witness_attestation as _attestation
from core import dedicated_host_preflight_witness_evidence_pinned_ssh_delivery as _delivery
from core.dedicated_host_preflight_receipt import canonical_json_bytes


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_RUNTIME_CONFIG_SCHEMA",
    "DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_RUNTIME_DEFAULT_ENABLED",
    "FIXED_WITNESS_EVIDENCE_VERIFIER_CONFIG_FILE",
    "DedicatedHostPreflightWitnessEvidenceRuntimeError",
    "RootOwnedWitnessEvidenceVerifierRuntimeConfig",
    "load_root_owned_witness_evidence_delivery_config",
    "parse_root_owned_witness_evidence_verifier_runtime_config",
)


DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_RUNTIME_CONFIG_SCHEMA = (
    "three-site-dedicated-host-preflight-witness-evidence-verifier-runtime-v1"
)
DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_RUNTIME_DEFAULT_ENABLED = False

FIXED_WITNESS_EVIDENCE_VERIFIER_CONFIG_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/wa-ir-witness-evidence-verifier.json"
)

_MODE = "read-only"
_TRANSPORT = "pinned-ssh-witness-evidence-agent"
_DIRECT_FINLAND_TO_IR = "forbidden"
_VERSION = 1
_MAX_CONFIG_BYTES = 16 * 1024
_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "version",
        "enabled",
        "mode",
        "transport",
        "direct_finland_to_iran",
        "attestation_request",
        "wa_ir_public_key_base64",
        "witness_public_key_base64",
    }
)


class DedicatedHostPreflightWitnessEvidenceRuntimeError(ValueError):
    """A fixed, redacted central verifier provisioning refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedWitnessEvidenceVerifierRuntimeConfig:
    """Default-off wrapper; callers cannot choose an alternate config path."""

    enabled: bool = DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_RUNTIME_DEFAULT_ENABLED


def _fail(code: str) -> None:
    raise DedicatedHostPreflightWitnessEvidenceRuntimeError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("WITNESS_EVIDENCE_VERIFIER_CONFIG_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("WITNESS_EVIDENCE_VERIFIER_CONFIG_INVALID")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError, UnicodeError):
        _fail(code)


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not str or not value:
        _fail(code)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(raw) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(raw)
    except (ImportError, TypeError, ValueError):
        _fail(code)
    return raw


def _request(value: object, *, code: str) -> _attestation.ParsedWaIrWitnessAttestationRequest:
    if type(value) is not dict:
        _fail(code)
    try:
        return _attestation.parse_wa_ir_witness_attestation_request(
            _canonical(value, code=code) + b"\n"
        )
    except _attestation.DedicatedHostPreflightIrWitnessAttestationError as exc:
        raise DedicatedHostPreflightWitnessEvidenceRuntimeError(code) from exc


def parse_root_owned_witness_evidence_verifier_runtime_config(
    value: object,
) -> _delivery.PinnedSshWitnessEvidenceDeliveryConfig:
    """Parse fixed public verification material; no I/O or process occurs."""

    if type(value) is not dict or set(value) != _CONFIG_FIELDS:
        _fail("WITNESS_EVIDENCE_VERIFIER_CONFIG_INVALID")
    if (
        value["schema"] != DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_RUNTIME_CONFIG_SCHEMA
        or value["version"] != _VERSION
        or value["enabled"] is not True
        or value["mode"] != _MODE
        or value["transport"] != _TRANSPORT
        or value["direct_finland_to_iran"] != _DIRECT_FINLAND_TO_IR
    ):
        _fail("WITNESS_EVIDENCE_VERIFIER_CONFIG_INVALID")
    return _delivery.PinnedSshWitnessEvidenceDeliveryConfig(
        expected_request=_request(
            value["attestation_request"],
            code="WITNESS_EVIDENCE_VERIFIER_CONFIG_INVALID",
        ),
        expected_wa_ir_public_key=_public_key(
            value["wa_ir_public_key_base64"],
            code="WITNESS_EVIDENCE_VERIFIER_CONFIG_INVALID",
        ),
        expected_witness_public_key=_public_key(
            value["witness_public_key_base64"],
            code="WITNESS_EVIDENCE_VERIFIER_CONFIG_INVALID",
        ),
        enabled=True,
    )


def _require_root() -> None:
    try:
        root = os.geteuid() == 0
    except OSError:
        root = False
    if not root:
        _fail("WITNESS_EVIDENCE_VERIFIER_ROOT_RUNTIME_REQUIRED")


def _validate_ancestors(path: Path, *, code: str) -> None:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or ".." in path.parts
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
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
    except DedicatedHostPreflightWitnessEvidenceRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_root_owned_file(path: Path, *, code: str) -> bytes:
    _validate_ancestors(path, code=code)
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
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= _MAX_CONFIG_BYTES
    ):
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
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
            after.st_size,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        ) != identity:
            _fail(code)
        return b"".join(chunks)
    except DedicatedHostPreflightWitnessEvidenceRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def load_root_owned_witness_evidence_delivery_config(
    *,
    config: RootOwnedWitnessEvidenceVerifierRuntimeConfig = RootOwnedWitnessEvidenceVerifierRuntimeConfig(),
) -> _delivery.PinnedSshWitnessEvidenceDeliveryConfig:
    """Open exactly one root-only central verifier policy and require opt-in."""

    _require_root()
    if type(config) is not RootOwnedWitnessEvidenceVerifierRuntimeConfig or config.enabled is not True:
        _fail("WITNESS_EVIDENCE_VERIFIER_RUNTIME_DISABLED")
    raw = _read_root_owned_file(
        FIXED_WITNESS_EVIDENCE_VERIFIER_CONFIG_FILE,
        code="WITNESS_EVIDENCE_VERIFIER_CONFIG_FILE_UNSAFE",
    )
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DedicatedHostPreflightWitnessEvidenceRuntimeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("WITNESS_EVIDENCE_VERIFIER_CONFIG_FILE_INVALID")
    if type(value) is not dict or raw != _canonical(
        value, code="WITNESS_EVIDENCE_VERIFIER_CONFIG_FILE_INVALID"
    ) + b"\n":
        _fail("WITNESS_EVIDENCE_VERIFIER_CONFIG_FILE_INVALID")
    try:
        return parse_root_owned_witness_evidence_verifier_runtime_config(value)
    except DedicatedHostPreflightWitnessEvidenceRuntimeError:
        raise
