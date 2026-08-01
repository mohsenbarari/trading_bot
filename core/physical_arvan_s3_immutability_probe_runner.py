"""Fail-closed local runner for the paired Arvan immutability probe.

This module is deliberately a *receipt boundary*, not a deployment runner.
It can call the already-separated FI-publisher / WA-IR-receiver client
factory only after a fresh opaque release seal, exact FI-to-IR binding, local
root-owned receipt directory, and an explicit opt-in have all been checked.
The only durable output is a canonical redacted receipt.  That receipt is
evidence of a disposable Object-Storage preflight observation; it is never a
publish, deployment, promotion, writer-failover, or Full-Matrix authority.

There is no import-time credential, SDK, S3, network, subprocess, Docker, or
SSH activity.  The live factory is invoked only by ``run`` after all local
admission checks have succeeded.  Its provider/client/credential errors are
collapsed to fixed local codes and are never persisted or returned.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from core import physical_arvan_immutability_preflight as _preflight
from core import physical_arvan_s3_separated_client_factory as _factory
from core import physical_release_seal_admission as _release_seal
from core.append_only_sync_delta_batch import CAMPAIGN_ID_RE, RELEASE_SHA_RE, SHA256_RE


__all__ = (
    "ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_DEFAULT_ENABLED",
    "ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_SCHEMA",
    "ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_SCHEMA",
    "DEFAULT_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_MAX_OBSERVATION_FRESHNESS_SECONDS",
    "DEFAULT_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_MAX_RELEASE_SEAL_FRESHNESS_SECONDS",
    "FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT",
    "MAX_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_BYTES",
    "PhysicalArvanS3ImmutabilityProbeReceipt",
    "PhysicalArvanS3ImmutabilityProbeRunnerError",
    "RootOwnedArvanS3ImmutabilityProbeRunner",
    "RootOwnedArvanS3ImmutabilityProbeRunnerConfig",
    "parse_physical_arvan_s3_immutability_probe_receipt",
    "validate_root_owned_arvan_s3_immutability_probe_runner_config",
)


ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_SCHEMA = (
    "gold-trade-physical-arvan-s3-immutability-probe-runner-v1"
)
ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_SCHEMA = (
    "gold-trade-physical-arvan-s3-immutability-probe-receipt-v1"
)
ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_DEFAULT_ENABLED = False

DEFAULT_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_MAX_RELEASE_SEAL_FRESHNESS_SECONDS = 180
DEFAULT_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_MAX_OBSERVATION_FRESHNESS_SECONDS = 300
MAX_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_BYTES = 16 * 1024

# This is intentionally a constant rather than a caller-configurable output
# directory.  Operators provision it root:root 0700 before an explicitly
# enabled collection.  Tests patch this module constant to a root-owned temp
# directory; production code cannot redirect a receipt to an arbitrary path.
FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT = Path(
    "/var/lib/trading-bot/physical-arvan-immutability-receipts"
)

_RUNNER_MODE = "sealed-fi-to-ir-paired-probe-redacted-receipt-only-v1"
_RECEIPT_STATUS = "observed-verified-redacted-durable-receipt"
_SOURCE_SITE = "webapp_fi"
_DESTINATION_SITE = "webapp_ir"
_DIRECT_CONTROL = "forbidden"
_TARGET_PREFIX = "arvan-s3-immutability-receipt-"
_TARGET_SUFFIX = ".json"
_TARGET_NAME_RE = re.compile(
    r"^arvan-s3-immutability-receipt-[0-9a-f]{64}\.json$", re.ASCII
)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "source_site",
        "destination_site",
        "sealed_release_descriptor_sha256",
        "arvan_binding_sha256",
        "observation_evidence_sha256",
        "retention_mode",
        "retention_days",
        "observed_at",
        "fi_publisher_identity_sha256",
        "ir_receiver_identity_sha256",
        "direct_fi_to_ir_control",
        "deployment_authorized",
        "promotion_authorized",
        "full_matrix_authorized",
        "receipt_sha256",
    }
)


class PhysicalArvanS3ImmutabilityProbeRunnerError(ValueError):
    """Fixed-code refusal that never exposes a provider, path, or secret."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanS3ImmutabilityProbeRunnerConfig:
    """Default-off policy for one local, non-authorizing probe receipt run."""

    schema: str = ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_SCHEMA
    campaign_id: str = ""
    sealed_release_descriptor: _release_seal.SealedPhysicalReleaseDescriptor | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    binding: _preflight.PhysicalArvanImmutabilityPreflightBinding | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    paired_factory: _factory.RootOwnedArvanS3SeparatedClientFactory | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    enabled: bool = ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_DEFAULT_ENABLED
    maximum_release_seal_freshness_seconds: int = (
        DEFAULT_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_MAX_RELEASE_SEAL_FRESHNESS_SECONDS
    )
    maximum_observation_freshness_seconds: int = (
        DEFAULT_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_MAX_OBSERVATION_FRESHNESS_SECONDS
    )
    mode: str = _RUNNER_MODE


@dataclass(frozen=True)
class PhysicalArvanS3ImmutabilityProbeReceipt:
    """Safe public receipt fields; explicitly not an operational authority."""

    schema: str
    status: str
    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    sealed_release_descriptor_sha256: str
    arvan_binding_sha256: str
    observation_evidence_sha256: str
    retention_mode: str
    retention_days: int
    observed_at: datetime
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    direct_fi_to_ir_control: str
    deployment_authorized: bool = False
    promotion_authorized: bool = False
    full_matrix_authorized: bool = False
    receipt_sha256: str = ""


@dataclass(frozen=True)
class _ConfigFacts:
    campaign_id: str
    sealed_release_descriptor: _release_seal.SealedPhysicalReleaseDescriptor
    binding: _preflight.PhysicalArvanImmutabilityPreflightBinding
    paired_factory: _factory.RootOwnedArvanS3SeparatedClientFactory
    maximum_release_seal_freshness_seconds: int
    maximum_observation_freshness_seconds: int


def _fail(code: str) -> None:
    raise PhysicalArvanS3ImmutabilityProbeRunnerError(code)


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID")
        result[key] = value
    return result


def _canonical_bytes(value: Mapping[str, Any], *, code: str) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        _fail(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp_text(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).isoformat().replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    normalised = _utc(parsed, code=code)
    if _timestamp_text(normalised, code=code) != value:
        _fail(code)
    return normalised


def _maximum(
    value: object,
    *,
    upper: int,
    code: str,
) -> int:
    if type(value) is not int or not 1 <= value <= upper:
        _fail(code)
    return value


def _config_facts(
    config: object,
    *,
    require_enabled: bool,
) -> _ConfigFacts:
    if type(config) is not RootOwnedArvanS3ImmutabilityProbeRunnerConfig:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CONFIG_INVALID")
    if config.schema != ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_SCHEMA:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CONFIG_INVALID")
    if type(config.enabled) is not bool:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CONFIG_INVALID")
    if require_enabled and config.enabled is not True:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_DISABLED")
    if config.mode != _RUNNER_MODE:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_MODE_INVALID")
    if type(config.campaign_id) is not str or CAMPAIGN_ID_RE.fullmatch(config.campaign_id) is None:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CONFIG_INVALID")
    if type(config.sealed_release_descriptor) is not _release_seal.SealedPhysicalReleaseDescriptor:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CONFIG_INVALID")
    if type(config.binding) is not _preflight.PhysicalArvanImmutabilityPreflightBinding:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CONFIG_INVALID")
    # A subclass could replace the carefully constrained collection method;
    # admit only the exact paired-factory implementation.
    if type(config.paired_factory) is not _factory.RootOwnedArvanS3SeparatedClientFactory:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_FACTORY_INVALID")
    return _ConfigFacts(
        campaign_id=config.campaign_id,
        sealed_release_descriptor=config.sealed_release_descriptor,
        binding=config.binding,
        paired_factory=config.paired_factory,
        maximum_release_seal_freshness_seconds=_maximum(
            config.maximum_release_seal_freshness_seconds,
            upper=_release_seal.MAX_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS,
            code="ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CONFIG_INVALID",
        ),
        maximum_observation_freshness_seconds=_maximum(
            config.maximum_observation_freshness_seconds,
            upper=_preflight.MAX_PHYSICAL_ARVAN_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS,
            code="ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CONFIG_INVALID",
        ),
    )


def validate_root_owned_arvan_s3_immutability_probe_runner_config(
    config: RootOwnedArvanS3ImmutabilityProbeRunnerConfig,
) -> RootOwnedArvanS3ImmutabilityProbeRunnerConfig:
    """Pure validation; it does not open a receipt, credential, SDK, or client."""

    facts = _config_facts(config, require_enabled=False)
    return RootOwnedArvanS3ImmutabilityProbeRunnerConfig(
        schema=ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_SCHEMA,
        campaign_id=facts.campaign_id,
        sealed_release_descriptor=facts.sealed_release_descriptor,
        binding=facts.binding,
        paired_factory=facts.paired_factory,
        enabled=config.enabled,
        maximum_release_seal_freshness_seconds=facts.maximum_release_seal_freshness_seconds,
        maximum_observation_freshness_seconds=facts.maximum_observation_freshness_seconds,
        mode=_RUNNER_MODE,
    )


def _require_root() -> None:
    try:
        is_root = os.geteuid() == 0
    except OSError:
        is_root = False
    if not is_root:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_ROOT_REQUIRED")


def _admit_seal_and_binding(
    *,
    facts: _ConfigFacts,
    now: datetime,
) -> tuple[
    _release_seal.SealedPhysicalReleaseDescriptor,
    _preflight.PhysicalArvanImmutabilityPreflightBinding,
]:
    """Take immutable local snapshots before the factory can open anything."""

    try:
        sealed = _release_seal.require_sealed_physical_release_descriptor(
            facts.sealed_release_descriptor,
            now=now,
            maximum_freshness_seconds=facts.maximum_release_seal_freshness_seconds,
        )
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_SEAL_INVALID")
    try:
        binding = _preflight._normalise_binding(facts.binding).binding
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_BINDING_INVALID")
    if (
        facts.campaign_id != sealed.campaign_id
        or binding.campaign_id != sealed.campaign_id
        or binding.release_sha != sealed.release_sha
        or binding.source_site != _SOURCE_SITE
        or binding.destination_site != _DESTINATION_SITE
    ):
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CAMPAIGN_BINDING_MISMATCH")
    # Do not hand a caller-owned object to the factory.  A fresh normalised
    # copy leaves the receipt verifier with its own immutable expected binding.
    return sealed, _preflight._normalise_binding(binding).binding


def _binding_digest(binding: _preflight.PhysicalArvanImmutabilityPreflightBinding) -> str:
    payload = {
        "schema": "gold-trade-physical-arvan-immutability-probe-binding-digest-v1",
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "route_binding_sha256": binding.route_binding_sha256,
        "endpoint": binding.endpoint,
        "region": binding.region,
        "bucket": binding.bucket,
        "minimum_retention_days": binding.minimum_retention_days,
    }
    return hashlib.sha256(
        _canonical_bytes(payload, code="ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_BINDING_INVALID")
    ).hexdigest()


def _target_name(
    *,
    sealed: _release_seal.SealedPhysicalReleaseDescriptor,
    binding_sha256: str,
) -> str:
    claim = {
        "schema": "gold-trade-physical-arvan-immutability-probe-receipt-claim-v1",
        "campaign_id": sealed.campaign_id,
        "release_sha": sealed.release_sha,
        "sealed_release_descriptor_sha256": sealed.descriptor_sha256,
        "arvan_binding_sha256": binding_sha256,
    }
    claim_digest = hashlib.sha256(
        _canonical_bytes(claim, code="ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_TARGET_INVALID")
    ).hexdigest()
    return _TARGET_PREFIX + claim_digest + _TARGET_SUFFIX


def _secure_receipt_root_fd() -> int:
    """Open the fixed root once, rejecting symlinks and insecure metadata."""

    root = FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or root == Path("/")
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ROOT_UNSAFE")
    descriptor = -1
    try:
        before = os.lstat(root)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o700
        ):
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ROOT_UNSAFE")
        if root.resolve(strict=True) != root:
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ROOT_UNSAFE")
        descriptor = os.open(
            root,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
        )
        after = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(after.st_mode)
            or after.st_uid != 0
            or stat.S_IMODE(after.st_mode) != 0o700
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ROOT_UNSAFE")
        return descriptor
    except PhysicalArvanS3ImmutabilityProbeRunnerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, RuntimeError):
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ROOT_UNSAFE")


def _safe_existing_target(root_fd: int, *, name: str) -> None:
    if _TARGET_NAME_RE.fullmatch(name) is None:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_TARGET_INVALID")
    try:
        metadata = os.lstat(name, dir_fd=root_fd)
    except FileNotFoundError:
        return
    except OSError:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_TARGET_UNSAFE")
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_TARGET_UNSAFE")
    _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ALREADY_EXISTS")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError:
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_WRITE_FAILED")
        if type(written) is not int or written <= 0:
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_WRITE_FAILED")
        view = view[written:]


def _secure_read_receipt(root_fd: int, *, name: str) -> bytes:
    """Read one root-owned canonical final leaf through the anchored dirfd."""

    if not hasattr(os, "O_NOFOLLOW"):
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_READ_FAILED")
    descriptor = -1
    try:
        before = os.lstat(name, dir_fd=root_fd)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_READ_FAILED")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_BYTES:
                _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_READ_FAILED")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_READ_FAILED")
        return b"".join(chunks)
    except PhysicalArvanS3ImmutabilityProbeRunnerError:
        raise
    except OSError:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_READ_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_READ_FAILED")


def _atomic_write_new_receipt(root_fd: int, *, name: str, payload: bytes) -> bytes:
    """Durably link one canonical receipt without ever replacing a prior leaf."""

    if (
        not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "link")
        or not hasattr(os, "fsync")
    ):
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_WRITE_FAILED")
    if not 1 <= len(payload) <= MAX_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_BYTES:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_INVALID")
    _safe_existing_target(root_fd, name=name)
    temporary = ""
    descriptor = -1
    try:
        for _ in range(4):
            candidate = "." + name + "." + secrets.token_hex(16) + ".tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=root_fd,
                )
            except FileExistsError:
                continue
            temporary = candidate
            break
        if descriptor < 0 or not temporary:
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_WRITE_FAILED")
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_WRITE_FAILED")
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ALREADY_EXISTS")
        os.unlink(temporary, dir_fd=root_fd)
        temporary = ""
        os.fsync(root_fd)
        observed = _secure_read_receipt(root_fd, name=name)
        if observed != payload:
            _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_READ_FAILED")
        return observed
    except PhysicalArvanS3ImmutabilityProbeRunnerError:
        raise
    except OSError:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary:
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except (FileNotFoundError, OSError):
                pass
        # A post-link failure leaves a durable final receipt by design.  It is
        # never overwritten, and a later explicit run will fail closed on it.


def _receipt_payload_without_digest(
    receipt: PhysicalArvanS3ImmutabilityProbeReceipt,
) -> dict[str, Any]:
    return {
        "schema": receipt.schema,
        "status": receipt.status,
        "campaign_id": receipt.campaign_id,
        "release_sha": receipt.release_sha,
        "source_site": receipt.source_site,
        "destination_site": receipt.destination_site,
        "sealed_release_descriptor_sha256": receipt.sealed_release_descriptor_sha256,
        "arvan_binding_sha256": receipt.arvan_binding_sha256,
        "observation_evidence_sha256": receipt.observation_evidence_sha256,
        "retention_mode": receipt.retention_mode,
        "retention_days": receipt.retention_days,
        "observed_at": _timestamp_text(
            receipt.observed_at,
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        ),
        "fi_publisher_identity_sha256": receipt.fi_publisher_identity_sha256,
        "ir_receiver_identity_sha256": receipt.ir_receiver_identity_sha256,
        "direct_fi_to_ir_control": receipt.direct_fi_to_ir_control,
        "deployment_authorized": receipt.deployment_authorized,
        "promotion_authorized": receipt.promotion_authorized,
        "full_matrix_authorized": receipt.full_matrix_authorized,
    }


def _receipt_bytes(receipt: PhysicalArvanS3ImmutabilityProbeReceipt) -> bytes:
    payload = _receipt_payload_without_digest(receipt)
    digest = hashlib.sha256(
        _canonical_bytes(payload, code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID")
    ).hexdigest()
    output = dict(payload)
    output["receipt_sha256"] = digest
    return _canonical_bytes(
        output, code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID"
    ) + b"\n"


def _receipt_from_mapping(value: object) -> PhysicalArvanS3ImmutabilityProbeReceipt:
    if type(value) is not dict or set(value) != _RECEIPT_FIELDS:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID")
    if (
        value["schema"] != ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_SCHEMA
        or value["status"] != _RECEIPT_STATUS
        or type(value["campaign_id"]) is not str
        or CAMPAIGN_ID_RE.fullmatch(value["campaign_id"]) is None
        or type(value["release_sha"]) is not str
        or RELEASE_SHA_RE.fullmatch(value["release_sha"]) is None
        or value["source_site"] != _SOURCE_SITE
        or value["destination_site"] != _DESTINATION_SITE
        or value["direct_fi_to_ir_control"] != _DIRECT_CONTROL
        or type(value["deployment_authorized"]) is not bool
        or type(value["promotion_authorized"]) is not bool
        or type(value["full_matrix_authorized"]) is not bool
        or value["deployment_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["full_matrix_authorized"] is not False
        or value["retention_mode"] not in _preflight.ARVAN_RETENTION_MODES
        or type(value["retention_days"]) is not int
        or not _preflight.MIN_PHYSICAL_ARVAN_RETENTION_DAYS
        <= value["retention_days"]
        <= _preflight.MAX_PHYSICAL_ARVAN_RETENTION_DAYS
    ):
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID")
    return PhysicalArvanS3ImmutabilityProbeReceipt(
        schema=ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_SCHEMA,
        status=_RECEIPT_STATUS,
        campaign_id=value["campaign_id"],
        release_sha=value["release_sha"],
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        sealed_release_descriptor_sha256=_sha256(
            value["sealed_release_descriptor_sha256"],
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        ),
        arvan_binding_sha256=_sha256(
            value["arvan_binding_sha256"],
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        ),
        observation_evidence_sha256=_sha256(
            value["observation_evidence_sha256"],
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        ),
        retention_mode=value["retention_mode"],
        retention_days=value["retention_days"],
        observed_at=_timestamp(
            value["observed_at"],
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        ),
        fi_publisher_identity_sha256=_sha256(
            value["fi_publisher_identity_sha256"],
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        ),
        ir_receiver_identity_sha256=_sha256(
            value["ir_receiver_identity_sha256"],
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        ),
        direct_fi_to_ir_control=_DIRECT_CONTROL,
        deployment_authorized=False,
        promotion_authorized=False,
        full_matrix_authorized=False,
        receipt_sha256=_sha256(
            value["receipt_sha256"],
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        ),
    )


def parse_physical_arvan_s3_immutability_probe_receipt(
    payload: bytes,
) -> PhysicalArvanS3ImmutabilityProbeReceipt:
    """Parse exactly one canonical, redacted, non-authorizing receipt."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_BYTES:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID")
    try:
        decoded = payload.decode("ascii")
        parsed = json.loads(decoded, object_pairs_hook=_strict_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID")
    receipt = _receipt_from_mapping(parsed)
    canonical = _receipt_bytes(receipt)
    if payload != canonical:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID")
    if receipt.receipt_sha256 != hashlib.sha256(
        _canonical_bytes(
            _receipt_payload_without_digest(receipt),
            code="ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID",
        )
    ).hexdigest():
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID")
    return receipt


def _receipt_from_verified(
    *,
    sealed: _release_seal.SealedPhysicalReleaseDescriptor,
    binding_sha256: str,
    verified: _preflight.VerifiedPhysicalArvanImmutabilityPreflight,
) -> PhysicalArvanS3ImmutabilityProbeReceipt:
    observation = verified.observation
    restrictions = observation.credential_restrictions
    if len(restrictions) != 3:
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_OBSERVATION_INVALID")
    fi, ir, witness = restrictions
    if (
        fi.role != "fi-publisher"
        or ir.role != "ir-receiver"
        or witness.role != "witness-controller"
        or type(fi.credential_identity_sha256) is not str
        or type(ir.credential_identity_sha256) is not str
    ):
        _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_OBSERVATION_INVALID")
    provisional = PhysicalArvanS3ImmutabilityProbeReceipt(
        schema=ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_SCHEMA,
        status=_RECEIPT_STATUS,
        campaign_id=sealed.campaign_id,
        release_sha=sealed.release_sha,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        sealed_release_descriptor_sha256=sealed.descriptor_sha256,
        arvan_binding_sha256=binding_sha256,
        observation_evidence_sha256=observation.evidence_sha256,
        retention_mode=observation.retention_mode,
        retention_days=observation.retention_days,
        observed_at=observation.observed_at,
        fi_publisher_identity_sha256=fi.credential_identity_sha256,
        ir_receiver_identity_sha256=ir.credential_identity_sha256,
        direct_fi_to_ir_control=_DIRECT_CONTROL,
        deployment_authorized=False,
        promotion_authorized=False,
        full_matrix_authorized=False,
        receipt_sha256="",
    )
    raw = _receipt_bytes(provisional)
    return parse_physical_arvan_s3_immutability_probe_receipt(raw)


class RootOwnedArvanS3ImmutabilityProbeRunner:
    """Default-off owner for one constrained paired-factory collection call."""

    def __init__(self, config: RootOwnedArvanS3ImmutabilityProbeRunnerConfig) -> None:
        # Constructor remains inert: it does not check a seal, open a receipt
        # root, read credentials, import an SDK, or collect a provider result.
        self._config = validate_root_owned_arvan_s3_immutability_probe_runner_config(
            config
        )

    def run(self, *, now: datetime) -> PhysicalArvanS3ImmutabilityProbeReceipt:
        """Collect once and persist only a canonical redacted receipt.

        This intentionally has no deployment or Full-Matrix side effect.  A
        future campaign gate must independently evaluate fresh evidence and
        all of its other requirements; this receipt itself grants nothing.
        """

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed_now = _utc(now, code="ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_CLOCK_INVALID")

        # No credential file or SDK can be reached before these local checks.
        sealed, binding = _admit_seal_and_binding(facts=facts, now=observed_now)
        binding_sha256 = _binding_digest(binding)
        name = _target_name(sealed=sealed, binding_sha256=binding_sha256)
        root_fd = _secure_receipt_root_fd()
        try:
            # Refuse reuse before the disposable provider probe is invoked.
            _safe_existing_target(root_fd, name=name)
            try:
                observation = facts.paired_factory.collect_immutability_preflight(
                    binding=binding,
                    observed_at=observed_now,
                )
            except Exception:
                _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_FACTORY_FAILED")
            try:
                verified = _preflight.verify_physical_arvan_immutability_preflight(
                    observation,
                    binding=binding,
                    now=observed_now,
                    maximum_evidence_age_seconds=(
                        facts.maximum_observation_freshness_seconds
                    ),
                )
            except Exception:
                _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_OBSERVATION_INVALID")
            receipt = _receipt_from_verified(
                sealed=sealed,
                binding_sha256=binding_sha256,
                verified=verified,
            )
            durable = _atomic_write_new_receipt(
                root_fd,
                name=name,
                payload=_receipt_bytes(receipt),
            )
            parsed = parse_physical_arvan_s3_immutability_probe_receipt(durable)
            if parsed != receipt:
                _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_READ_FAILED")
            return parsed
        finally:
            try:
                os.close(root_fd)
            except OSError:
                _fail("ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ROOT_UNSAFE")
