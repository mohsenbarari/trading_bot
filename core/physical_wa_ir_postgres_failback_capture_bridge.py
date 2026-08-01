"""Root-only local capture bridge for the IR-to-FI physical failback route.

This boundary is intentionally independent of the WA-FI helper capture
bridge.  While ``webapp_ir`` holds the live Witness term it can ask one
injected *local* capture runner to create a transaction-consistent base-backup
artifact beneath a fixed private root, prove the returned file's immutable
identity, and feed it only to the separate IR failback handoff protocol.

It contains no PostgreSQL, Docker, shell, socket, SSH, network, Object
Storage client, credential, traffic, promotion, or Full-Matrix operation.
The runner is injected by a root-owned deployment adapter and receives only a
canonical local capture invocation; it never receives a caller-selected
command, peer, URL, environment, or secret.  The bridge's success is an
archive/recovery handoff record, never a remote-apply, writer, promotion, or
traffic assertion.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    canonical_json_bytes,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.physical_ir_to_fi_object_storage_failback_preflight import (
    PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
    require_verified_physical_ir_to_fi_object_storage_failback_preflight,
)
from core.physical_wal_base_backup_spool import (
    DEFAULT_SPOOL_RESERVE_BYTES,
    MAX_PHYSICAL_BASE_BACKUP_BYTES,
    PhysicalWalBaseBackupCompletedArtifact,
    PhysicalWalBaseBackupManifestBinding,
    PhysicalWalBaseBackupSpoolConfig,
    PhysicalWalBaseBackupSpoolError,
    PhysicalWalBaseBackupSpoolResult,
    PhysicalWalBaseBackupUploader,
    VerifiedPhysicalWalBaseBackupBinding,
    authorize_physical_wal_base_backup_binding,
    capture_physical_wal_base_backup,
    require_verified_physical_wal_base_backup_binding,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
)


__all__ = (
    "PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_DEFAULT_ENABLED",
    "PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA",
    "PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_STATUS",
    "PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_STATUS_BLOCKED",
    "PhysicalWaIrPostgresFailbackBaseBackupHandoff",
    "PhysicalWaIrPostgresFailbackCaptureArtifact",
    "PhysicalWaIrPostgresFailbackCaptureBridgeError",
    "PhysicalWaIrPostgresFailbackCaptureBridgeResult",
    "PhysicalWaIrPostgresFailbackCaptureInvocation",
    "PhysicalWaIrPostgresFailbackCaptureRunner",
    "RootOwnedWaIrPostgresFailbackCaptureBridge",
    "RootOwnedWaIrPostgresFailbackCaptureBridgeConfig",
    "run_root_owned_wa_ir_postgres_failback_capture_bridge",
    "validate_root_owned_wa_ir_postgres_failback_capture_bridge_config",
)


PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-failback-capture-bridge-v1"
)
PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_DEFAULT_ENABLED = False
PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_STATUS = (
    "captured-published-archive-recovery-only"
)
PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_STATUS_BLOCKED = "blocked"

_SOURCE_SITE = "webapp_ir"
_DESTINATION_SITE = "webapp_fi"
_RUNTIME_MODE = "root-owned-wa-ir-local-failback-capture-v1"
_MAX_PATH_LENGTH = 4096
_MAX_ARTIFACT_NAME_BYTES = 256
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class PhysicalWaIrPostgresFailbackCaptureBridgeError(ValueError):
    """A fixed redacted refusal from the local IR capture bridge."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaIrPostgresFailbackCaptureInvocation:
    """Canonical non-secret input for one local, IR-owned capture operation."""

    schema: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    source_site: str
    destination_site: str
    object_storage_namespace: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    capture_root: Path
    invocation_sha256: str


@dataclass(frozen=True)
class PhysicalWaIrPostgresFailbackCaptureArtifact:
    """Runner-reported public identity of one already-written local artifact."""

    artifact_name: str
    plaintext_sha256: str
    plaintext_bytes: int
    completion_attestation_sha256: str


class PhysicalWaIrPostgresFailbackCaptureRunner(Protocol):
    """The only local capture surface exposed by this bridge."""

    def capture_consistent_failback_base_backup(
        self,
        *,
        invocation: PhysicalWaIrPostgresFailbackCaptureInvocation,
    ) -> PhysicalWaIrPostgresFailbackCaptureArtifact:
        """Write one immutable artifact beneath ``invocation.capture_root``."""


class PhysicalWaIrPostgresFailbackBaseBackupHandoff(Protocol):
    """The reverse-only base-backup handoff surface; normal FI handoff differs."""

    def base_backup_uploader(
        self,
        *,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    ) -> PhysicalWalBaseBackupUploader:
        """Return only the term-bound IR-to-FI reverse uploader protocol."""


@dataclass(frozen=True)
class RootOwnedWaIrPostgresFailbackCaptureBridgeConfig:
    """Default-off root policy with no command, peer, URL, or secret field."""

    schema: str = PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA
    reverse_handoff: PhysicalWaIrPostgresFailbackBaseBackupHandoff | None = field(
        default=None, repr=False, compare=False
    )
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig | None = field(
        default=None, repr=False, compare=False
    )
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight | None = field(
        default=None, repr=False, compare=False
    )
    manifest_binding: PhysicalWalBaseBackupManifestBinding | None = field(
        default=None, repr=False, compare=False
    )
    capture_root: Path | None = field(default=None, repr=False, compare=False)
    spool_root: Path | None = field(default=None, repr=False, compare=False)
    maximum_base_backup_bytes: int = 0
    spool_reserve_bytes: int = DEFAULT_SPOOL_RESERVE_BYTES
    enabled: bool = PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    destination_site: str = _DESTINATION_SITE
    object_storage_namespace: str = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    runtime_mode: str = _RUNTIME_MODE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalWaIrPostgresFailbackCaptureBridgeResult:
    """Capture/publish result which deliberately grants no role authority."""

    schema: str
    status: str
    reason_codes: tuple[str, ...]
    capture_artifact: PhysicalWaIrPostgresFailbackCaptureArtifact | None = None
    verified_binding: VerifiedPhysicalWalBaseBackupBinding | None = field(
        default=None, repr=False
    )
    spool_result: PhysicalWalBaseBackupSpoolResult | None = None
    promotion_authorized: bool = False
    writer_authorized: bool = False
    traffic_switch_authorized: bool = False
    full_matrix_authorized: bool = False


@dataclass(frozen=True)
class _Facts:
    handoff: PhysicalWaIrPostgresFailbackBaseBackupHandoff
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight
    manifest: PhysicalWalBaseBackupManifestBinding
    capture_root: Path
    spool_root: Path
    maximum_base_backup_bytes: int
    spool_reserve_bytes: int


def _fail(code: str) -> None:
    raise PhysicalWaIrPostgresFailbackCaptureBridgeError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("WA_IR_FAILBACK_CAPTURE_ROOT_REQUIRED")
    except OSError:
        _fail("WA_IR_FAILBACK_CAPTURE_ROOT_REQUIRED")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _private_root(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    if len(str(value)) > _MAX_PATH_LENGTH:
        _fail(code)
    try:
        metadata = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _handoff(value: object) -> PhysicalWaIrPostgresFailbackBaseBackupHandoff:
    if value is None or not callable(getattr(value, "base_backup_uploader", None)):
        _fail("WA_IR_FAILBACK_CAPTURE_REVERSE_HANDOFF_INVALID")
    return value


def _inert_config(
    value: object,
) -> RootOwnedWaIrPostgresFailbackCaptureBridgeConfig:
    if type(value) is not RootOwnedWaIrPostgresFailbackCaptureBridgeConfig:
        _fail("WA_IR_FAILBACK_CAPTURE_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA
        or type(value.enabled) is not bool
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.runtime_mode != _RUNTIME_MODE
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
        or type(value.preflight_config) is not PhysicalIrToFiObjectStorageFailbackPreflightConfig
        or type(value.preflight) is not VerifiedPhysicalIrToFiObjectStorageFailbackPreflight
        or type(value.manifest_binding) is not PhysicalWalBaseBackupManifestBinding
        or not isinstance(value.capture_root, Path)
        or not isinstance(value.spool_root, Path)
    ):
        _fail("WA_IR_FAILBACK_CAPTURE_CONFIG_INVALID")
    _handoff(value.reverse_handoff)
    _positive(
        value.maximum_base_backup_bytes,
        maximum=MAX_PHYSICAL_BASE_BACKUP_BYTES,
        code="WA_IR_FAILBACK_CAPTURE_MAXIMUM_BYTES_INVALID",
    )
    if type(value.spool_reserve_bytes) is not int or value.spool_reserve_bytes < 1:
        _fail("WA_IR_FAILBACK_CAPTURE_SPOOL_RESERVE_INVALID")
    return value


def validate_root_owned_wa_ir_postgres_failback_capture_bridge_config(
    config: RootOwnedWaIrPostgresFailbackCaptureBridgeConfig,
) -> RootOwnedWaIrPostgresFailbackCaptureBridgeConfig:
    """Inert shape validation; it opens neither a capture root nor a runner."""

    return _inert_config(config)


def _facts(
    config: RootOwnedWaIrPostgresFailbackCaptureBridgeConfig,
    *,
    now: datetime,
    require_enabled: bool,
) -> _Facts:
    checked = _inert_config(config)
    if require_enabled and checked.enabled is not True:
        _fail("WA_IR_FAILBACK_CAPTURE_DISABLED")
    assert checked.preflight_config is not None and checked.preflight is not None
    try:
        preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            checked.preflight,
            config=checked.preflight_config,
            now=now,
        )
    except Exception:
        _fail("WA_IR_FAILBACK_CAPTURE_PREFLIGHT_INVALID_OR_STALE")
    manifest = checked.manifest_binding
    assert manifest is not None
    if (
        manifest.source_site != _SOURCE_SITE
        or manifest.destination_site != _DESTINATION_SITE
        or manifest.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or manifest.campaign_id != preflight.binding.campaign_id
        or manifest.release_sha != preflight.binding.release_sha
    ):
        _fail("WA_IR_FAILBACK_CAPTURE_ROUTE_BINDING_MISMATCH")
    capture_root = _private_root(
        checked.capture_root,
        code="WA_IR_FAILBACK_CAPTURE_ROOT_UNSAFE",
    )
    spool_root = _private_root(
        checked.spool_root,
        code="WA_IR_FAILBACK_CAPTURE_SPOOL_ROOT_UNSAFE",
    )
    if (
        capture_root == spool_root
        or capture_root.is_relative_to(spool_root)
        or spool_root.is_relative_to(capture_root)
    ):
        _fail("WA_IR_FAILBACK_CAPTURE_ROOTS_OVERLAP")
    return _Facts(
        handoff=_handoff(checked.reverse_handoff),
        preflight_config=checked.preflight_config,
        preflight=preflight,
        manifest=manifest,
        capture_root=capture_root,
        spool_root=spool_root,
        maximum_base_backup_bytes=checked.maximum_base_backup_bytes,
        spool_reserve_bytes=checked.spool_reserve_bytes,
    )


def _term(value: object, *, now: datetime) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("WA_IR_FAILBACK_CAPTURE_TERM_INVALID_OR_STALE")
    if term.holder_site != _SOURCE_SITE:
        _fail("WA_IR_FAILBACK_CAPTURE_TERM_ROUTE_INVALID")
    return term


def _same_term(
    left: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    right: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
) -> bool:
    return (
        left.holder_site,
        left.writer_epoch,
        left.writer_lease_id,
        left.witness_transition_id,
        left.proof_sha256,
    ) == (
        right.holder_site,
        right.writer_epoch,
        right.writer_lease_id,
        right.witness_transition_id,
        right.proof_sha256,
    )


def _invocation(
    *,
    facts: _Facts,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
) -> PhysicalWaIrPostgresFailbackCaptureInvocation:
    manifest = facts.manifest
    if (
        type(manifest.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(manifest.campaign_id) is None
        or type(manifest.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(manifest.release_sha) is None
    ):
        _fail("WA_IR_FAILBACK_CAPTURE_ROUTE_BINDING_MISMATCH")
    payload: dict[str, Any] = {
        "schema": PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA,
        "campaign_id": manifest.campaign_id,
        "release_sha": manifest.release_sha,
        "baseline_generation_id": manifest.baseline_generation_id,
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "object_storage_namespace": PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.proof_sha256,
        "capture_root": str(facts.capture_root),
    }
    try:
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError):
        _fail("WA_IR_FAILBACK_CAPTURE_INVOCATION_INVALID")
    return PhysicalWaIrPostgresFailbackCaptureInvocation(
        schema=PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA,
        campaign_id=manifest.campaign_id,
        release_sha=manifest.release_sha,
        baseline_generation_id=manifest.baseline_generation_id,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        witnessed_term_proof_sha256=term.proof_sha256,
        capture_root=facts.capture_root,
        invocation_sha256=digest,
    )


def _capture_artifact(
    value: object,
    *,
    capture_root: Path,
    maximum_bytes: int,
) -> PhysicalWalBaseBackupCompletedArtifact:
    if type(value) is not PhysicalWaIrPostgresFailbackCaptureArtifact:
        _fail("WA_IR_FAILBACK_CAPTURE_RUNNER_RESULT_INVALID")
    if (
        type(value.artifact_name) is not str
        or _ARTIFACT_NAME_RE.fullmatch(value.artifact_name) is None
        or len(value.artifact_name.encode("ascii", "strict")) > _MAX_ARTIFACT_NAME_BYTES
    ):
        _fail("WA_IR_FAILBACK_CAPTURE_RUNNER_RESULT_INVALID")
    digest = _sha256(value.plaintext_sha256, code="WA_IR_FAILBACK_CAPTURE_RUNNER_RESULT_INVALID")
    attestation = _sha256(
        value.completion_attestation_sha256,
        code="WA_IR_FAILBACK_CAPTURE_RUNNER_RESULT_INVALID",
    )
    size = _positive(
        value.plaintext_bytes,
        maximum=maximum_bytes,
        code="WA_IR_FAILBACK_CAPTURE_RUNNER_RESULT_INVALID",
    )
    path = capture_root / value.artifact_name
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_FAILBACK_CAPTURE_ARTIFACT_UNSAFE")
    descriptor = -1
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(descriptor)
        if (
            resolved != path
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
            or metadata.st_dev != before.st_dev
            or metadata.st_ino != before.st_ino
            or metadata.st_size != size
        ):
            _fail("WA_IR_FAILBACK_CAPTURE_ARTIFACT_UNSAFE")
        observed = hashlib.sha256()
        total = 0
        while total < size:
            chunk = os.read(descriptor, min(1024 * 1024, size - total))
            if not chunk:
                _fail("WA_IR_FAILBACK_CAPTURE_ARTIFACT_UNSAFE")
            observed.update(chunk)
            total += len(chunk)
        if os.read(descriptor, 1):
            _fail("WA_IR_FAILBACK_CAPTURE_ARTIFACT_UNSAFE")
        after = os.fstat(descriptor)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or observed.hexdigest() != digest
        ):
            _fail("WA_IR_FAILBACK_CAPTURE_ARTIFACT_UNSAFE")
    except PhysicalWaIrPostgresFailbackCaptureBridgeError:
        raise
    except OSError:
        _fail("WA_IR_FAILBACK_CAPTURE_ARTIFACT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return PhysicalWalBaseBackupCompletedArtifact(
        artifact_name=value.artifact_name,
        plaintext_sha256=digest,
        plaintext_bytes=size,
        completion_attestation_sha256=attestation,
    )


class RootOwnedWaIrPostgresFailbackCaptureBridge:
    """Inert construction plus one local capture-and-reverse-publish operation."""

    def __init__(
        self,
        config: RootOwnedWaIrPostgresFailbackCaptureBridgeConfig,
        *,
        clock: Callable[[], datetime] | None,
    ) -> None:
        self._config = validate_root_owned_wa_ir_postgres_failback_capture_bridge_config(config)
        self._clock = clock

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("WA_IR_FAILBACK_CAPTURE_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="WA_IR_FAILBACK_CAPTURE_CLOCK_INVALID")
        except PhysicalWaIrPostgresFailbackCaptureBridgeError:
            raise
        except Exception:
            _fail("WA_IR_FAILBACK_CAPTURE_CLOCK_INVALID")

    def run(
        self,
        *,
        current_witnessed_term: object,
        runner: object,
    ) -> PhysicalWaIrPostgresFailbackCaptureBridgeResult:
        """Capture locally, then publish only through the reverse handoff seam."""

        try:
            _require_root()
            started = self._now()
            facts = _facts(self._config, now=started, require_enabled=True)
            term = _term(current_witnessed_term, now=started)
            invocation = _invocation(facts=facts, term=term)
            method = getattr(runner, "capture_consistent_failback_base_backup", None)
            if not callable(method):
                _fail("WA_IR_FAILBACK_CAPTURE_RUNNER_INVALID")
            try:
                reported = method(invocation=invocation)
            except PhysicalWaIrPostgresFailbackCaptureBridgeError:
                raise
            except Exception:
                _fail("WA_IR_FAILBACK_CAPTURE_RUNNER_FAILED")
            artifact = _capture_artifact(
                reported,
                capture_root=facts.capture_root,
                maximum_bytes=facts.maximum_base_backup_bytes,
            )
            try:
                binding = authorize_physical_wal_base_backup_binding(
                    manifest_binding=facts.manifest,
                    completed_artifact=artifact,
                    witnessed_term=term,
                    now=started,
                )
                uploader = facts.handoff.base_backup_uploader(current_witnessed_term=term)
                if not callable(getattr(uploader, "upload", None)):
                    _fail("WA_IR_FAILBACK_CAPTURE_REVERSE_HANDOFF_INVALID")
                spool = capture_physical_wal_base_backup(
                    config=PhysicalWalBaseBackupSpoolConfig(
                        source_root=facts.capture_root,
                        spool_root=facts.spool_root,
                        maximum_base_backup_bytes=facts.maximum_base_backup_bytes,
                        spool_reserve_bytes=facts.spool_reserve_bytes,
                    ),
                    verified_binding=binding,
                    uploader=uploader,
                    now=started,
                    term_recheck_clock=self._now,
                )
            except PhysicalWaIrPostgresFailbackCaptureBridgeError:
                raise
            except PhysicalWalBaseBackupSpoolError:
                _fail("WA_IR_FAILBACK_CAPTURE_SPOOL_OR_HANDOFF_FAILED")
            except Exception:
                _fail("WA_IR_FAILBACK_CAPTURE_SPOOL_OR_HANDOFF_FAILED")
            completed = self._now()
            if completed < started:
                _fail("WA_IR_FAILBACK_CAPTURE_CLOCK_INVALID")
            completed_facts = _facts(self._config, now=completed, require_enabled=True)
            completed_term = _term(current_witnessed_term, now=completed)
            if (
                not _same_term(term, completed_term)
                or completed_facts.manifest != facts.manifest
                or completed_facts.preflight.binding != facts.preflight.binding
            ):
                _fail("WA_IR_FAILBACK_CAPTURE_BINDING_CHANGED")
            try:
                require_verified_physical_wal_base_backup_binding(binding, now=completed)
            except Exception:
                _fail("WA_IR_FAILBACK_CAPTURE_TERM_INVALID_OR_STALE")
            return PhysicalWaIrPostgresFailbackCaptureBridgeResult(
                schema=PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_STATUS,
                reason_codes=(),
                capture_artifact=PhysicalWaIrPostgresFailbackCaptureArtifact(
                    artifact_name=artifact.artifact_name,
                    plaintext_sha256=artifact.plaintext_sha256,
                    plaintext_bytes=artifact.plaintext_bytes,
                    completion_attestation_sha256=artifact.completion_attestation_sha256,
                ),
                verified_binding=binding,
                spool_result=spool,
                promotion_authorized=False,
                writer_authorized=False,
                traffic_switch_authorized=False,
                full_matrix_authorized=False,
            )
        except PhysicalWaIrPostgresFailbackCaptureBridgeError as exc:
            return PhysicalWaIrPostgresFailbackCaptureBridgeResult(
                schema=PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_STATUS_BLOCKED,
                reason_codes=(exc.code,),
            )
        except Exception:
            return PhysicalWaIrPostgresFailbackCaptureBridgeResult(
                schema=PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_STATUS_BLOCKED,
                reason_codes=("WA_IR_FAILBACK_CAPTURE_UNEXPECTED_FAILURE",),
            )


def run_root_owned_wa_ir_postgres_failback_capture_bridge(
    *,
    config: RootOwnedWaIrPostgresFailbackCaptureBridgeConfig,
    current_witnessed_term: object,
    runner: object,
    now: datetime,
) -> PhysicalWaIrPostgresFailbackCaptureBridgeResult:
    """One-shot convenience wrapper for the default-off local bridge."""

    return RootOwnedWaIrPostgresFailbackCaptureBridge(config, clock=lambda: now).run(
        current_witnessed_term=current_witnessed_term,
        runner=runner,
    )
