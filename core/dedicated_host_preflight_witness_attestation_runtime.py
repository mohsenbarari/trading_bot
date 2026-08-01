"""Root-only retrieval runtime for one pinned Witness preflight evidence item.

This is deliberately the *read* half of the separate WA-IR-to-Witness
attestation route.  It loads a fixed, root-owned Witness ledger policy and a
distinct Witness signing-key record, constructs the existing append-only
ledger, and returns only its selector-free pinned evidence.  It has no
ingress, network, SSH, Object-Storage, age, Docker, service, Writer-Witness,
promotion, or execution capability.

The root collector that calls this module is separately constrained to a
literal SSH command.  Importing this module and constructing its data objects
are inert; the public retrieval function remains default-off and root-only.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import dedicated_host_preflight_ir_witness_attestation as _attestation
from core import dedicated_host_preflight_witness_attestation_ledger as _ledger
from core.dedicated_host_preflight_receipt import canonical_json_bytes


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_WITNESS_ATTESTATION_RUNTIME_CONFIG_SCHEMA",
    "DEDICATED_HOST_PREFLIGHT_WITNESS_ATTESTATION_RUNTIME_DEFAULT_ENABLED",
    "FIXED_WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE",
    "FIXED_WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_FILE",
    "WITNESS_PREFLIGHT_ATTESTATION_KEY_PURPOSE",
    "WITNESS_PREFLIGHT_ATTESTATION_KEY_SCHEMA",
    "DedicatedHostPreflightWitnessAttestationRuntimeError",
    "RootOwnedWitnessPreflightAttestationRuntimeConfig",
    "collect_root_owned_witness_pinned_preflight_evidence",
    "load_root_owned_witness_preflight_attestation_ledger",
    "parse_root_owned_witness_preflight_attestation_runtime_config",
)


DEDICATED_HOST_PREFLIGHT_WITNESS_ATTESTATION_RUNTIME_CONFIG_SCHEMA = (
    "three-site-dedicated-host-preflight-witness-attestation-runtime-config-v1"
)
DEDICATED_HOST_PREFLIGHT_WITNESS_ATTESTATION_RUNTIME_DEFAULT_ENABLED = False

FIXED_WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/witness-wa-ir-attestation-runtime.json"
)
FIXED_WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/witness-wa-ir-attestation-key.json"
)

WITNESS_PREFLIGHT_ATTESTATION_KEY_SCHEMA = (
    "three-site-dedicated-host-preflight-witness-attestation-key-v1"
)
WITNESS_PREFLIGHT_ATTESTATION_KEY_PURPOSE = "dedicated-host-preflight-witness-evidence-key"
_MODE = "read-only"
_TRANSPORT = "local-selector-free-witness-ledger"
_DIRECT_FINLAND_TO_IR = "forbidden"
_MAX_CONFIG_BYTES = 16 * 1024
_MAX_KEY_BYTES = 4 * 1024
_MAX_ENTRIES = 32
_VERSION = 1
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
        "maximum_entries",
    }
)
_KEY_FIELDS = frozenset(
    {
        "schema",
        "version",
        "purpose",
        "algorithm",
        "private_key_base64",
        "public_key_sha256",
    }
)


class DedicatedHostPreflightWitnessAttestationRuntimeError(ValueError):
    """A fixed, redacted refusal from the local Witness retrieval runtime."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedWitnessPreflightAttestationRuntimeConfig:
    """Explicit default-off admission for the fixed local evidence read."""

    enabled: bool = DEDICATED_HOST_PREFLIGHT_WITNESS_ATTESTATION_RUNTIME_DEFAULT_ENABLED


def _fail(code: str) -> None:
    raise DedicatedHostPreflightWitnessAttestationRuntimeError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_INVALID")


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
        raise DedicatedHostPreflightWitnessAttestationRuntimeError(code) from exc


def parse_root_owned_witness_preflight_attestation_runtime_config(
    value: object,
) -> _ledger.RootOwnedWitnessPreflightAttestationLedgerConfig:
    """Parse only the non-secret fixed policy for one Witness ledger."""

    if type(value) is not dict or set(value) != _CONFIG_FIELDS:
        _fail("WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_INVALID")
    if (
        value["schema"] != DEDICATED_HOST_PREFLIGHT_WITNESS_ATTESTATION_RUNTIME_CONFIG_SCHEMA
        or value["version"] != _VERSION
        or value["enabled"] is not True
        or value["mode"] != _MODE
        or value["transport"] != _TRANSPORT
        or value["direct_finland_to_iran"] != _DIRECT_FINLAND_TO_IR
        or type(value["maximum_entries"]) is not int
        or not 1 <= value["maximum_entries"] <= _MAX_ENTRIES
    ):
        _fail("WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_INVALID")
    return _ledger.RootOwnedWitnessPreflightAttestationLedgerConfig(
        expected_request=_request(
            value["attestation_request"],
            code="WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_INVALID",
        ),
        expected_wa_ir_public_key=_public_key(
            value["wa_ir_public_key_base64"],
            code="WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_INVALID",
        ),
        expected_witness_public_key=_public_key(
            value["witness_public_key_base64"],
            code="WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_INVALID",
        ),
        enabled=True,
        maximum_entries=value["maximum_entries"],
    )


def _require_root() -> None:
    try:
        root = os.geteuid() == 0
    except OSError:
        root = False
    if not root:
        _fail("WITNESS_PREFLIGHT_ATTESTATION_ROOT_RUNTIME_REQUIRED")


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
    except DedicatedHostPreflightWitnessAttestationRuntimeError:
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
    except DedicatedHostPreflightWitnessAttestationRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _load_signer() -> Ed25519PrivateKey:
    raw = _read_root_owned_file(
        FIXED_WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE,
        exact_mode=0o400,
        maximum_bytes=_MAX_KEY_BYTES,
        code="WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE_UNSAFE",
    )
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DedicatedHostPreflightWitnessAttestationRuntimeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE_INVALID")
    if type(value) is not dict or raw != _canonical(
        value, code="WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE_INVALID"
    ) + b"\n":
        _fail("WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE_INVALID")
    if (
        set(value) != _KEY_FIELDS
        or value["schema"] != WITNESS_PREFLIGHT_ATTESTATION_KEY_SCHEMA
        or value["version"] != _VERSION
        or value["purpose"] != WITNESS_PREFLIGHT_ATTESTATION_KEY_PURPOSE
        or value["algorithm"] != "ed25519"
        or type(value["private_key_base64"]) is not str
        or type(value["public_key_sha256"]) is not str
    ):
        _fail("WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE_INVALID")
    try:
        private_raw = base64.b64decode(value["private_key_base64"].encode("ascii"), validate=True)
        signer = Ed25519PrivateKey.from_private_bytes(private_raw)
        public_raw = signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (UnicodeEncodeError, binascii.Error, TypeError, ValueError):
        _fail("WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE_INVALID")
    if value["public_key_sha256"] != hashlib.sha256(public_raw).hexdigest():
        _fail("WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE_INVALID")
    return signer


def load_root_owned_witness_preflight_attestation_ledger() -> _ledger.RootOwnedWitnessPreflightAttestationLedger:
    """Load fixed Witness policy/key and construct no transport capability."""

    _require_root()
    raw = _read_root_owned_file(
        FIXED_WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_FILE,
        exact_mode=0o600,
        maximum_bytes=_MAX_CONFIG_BYTES,
        code="WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_FILE_UNSAFE",
    )
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DedicatedHostPreflightWitnessAttestationRuntimeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_FILE_INVALID")
    if type(value) is not dict or raw != _canonical(
        value, code="WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_FILE_INVALID"
    ) + b"\n":
        _fail("WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_FILE_INVALID")
    try:
        config = parse_root_owned_witness_preflight_attestation_runtime_config(value)
        signer = _load_signer()
        return _ledger.RootOwnedWitnessPreflightAttestationLedger(
            config=config,
            witness_signer=signer,
        )
    except DedicatedHostPreflightWitnessAttestationRuntimeError:
        raise
    except _ledger.DedicatedHostPreflightWitnessAttestationLedgerError as exc:
        raise DedicatedHostPreflightWitnessAttestationRuntimeError(
            "WITNESS_PREFLIGHT_ATTESTATION_LEDGER_CONFIG_REJECTED"
        ) from exc


def collect_root_owned_witness_pinned_preflight_evidence(
    *,
    config: RootOwnedWitnessPreflightAttestationRuntimeConfig = RootOwnedWitnessPreflightAttestationRuntimeConfig(),
) -> bytes:
    """Return one already-persisted selector-free Witness evidence item only."""

    _require_root()
    if type(config) is not RootOwnedWitnessPreflightAttestationRuntimeConfig or config.enabled is not True:
        _fail("WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_DISABLED")
    ledger = load_root_owned_witness_preflight_attestation_ledger()
    try:
        return ledger.collect_pinned_evidence()
    except _ledger.DedicatedHostPreflightWitnessAttestationLedgerError as exc:
        raise DedicatedHostPreflightWitnessAttestationRuntimeError(
            "WITNESS_PREFLIGHT_ATTESTATION_EVIDENCE_UNAVAILABLE"
        ) from exc
