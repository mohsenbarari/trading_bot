"""Fail-closed local bridge from the WA-FI helper container to base-backup handoff.

The older ``physical_wa_fi_postgres_base_backup_capture_command`` deliberately
pins a host ``pg_basebackup`` binary.  That is not an acceptable data-plane
dependency for the physical three-site architecture, so this module does not
adapt, call, or relax that legacy boundary.  Instead it gives the reviewed,
digest-pinned non-root helper-container one narrow local bridge to the
already-existing ``PhysicalWalBaseBackupCompletedArtifact`` / verified-handoff
control types.

The bridge is default-off.  It has no Docker, subprocess, PostgreSQL, TCP,
SSH, Object-Storage, credential, uploader, release, or peer-control code.
Only the reviewed helper receives the injected runner; this module never
constructs a runner.  A successful result is a root-owned local capture plus
canonical redacted evidence.  It is explicitly not an upload, a replay
receipt, a release seal, a launch permit, a Writer permit, a promotion permit,
or a Full-Matrix result.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
from core.physical_postgres_deployment_scaffold import canonical_json_bytes as _manifest_canonical_json_bytes
from core.physical_postgres_strict_runtime_installation_gate import (
    PhysicalPostgresStrictRuntimeInstallationRequest,
    VerifiedPhysicalPostgresStrictRuntimeInstallations,
    require_physical_postgres_strict_runtime_installation_request,
    require_verified_physical_postgres_strict_runtime_installations,
)
from core.physical_wa_fi_postgres_helper_container import (
    PhysicalWaFiPostgresHelperContainerCaptureRequest,
    PhysicalWaFiPostgresHelperContainerError,
    PhysicalWaFiPostgresHelperContainerResult,
    PhysicalWaFiPostgresHelperContainerRunner,
    execute_wa_fi_postgres_helper_container_capture,
)
from core.physical_wal_base_backup_spool import (
    MAX_PHYSICAL_BASE_BACKUP_BYTES,
    PhysicalWalBaseBackupCompletedArtifact,
    PhysicalWalBaseBackupManifestBinding,
    PhysicalWalBaseBackupSpoolError,
    VerifiedPhysicalWalBaseBackupBinding,
    authorize_physical_wal_base_backup_binding,
    require_verified_physical_wal_base_backup_binding,
)


__all__ = (
    "FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT",
    "FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_EVIDENCE_ROOT",
    "PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_ATTESTATION_SCHEMA",
    "PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_COMPLETION_SCHEMA",
    "PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_DEFAULT_ENABLED",
    "PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_SCHEMA",
    "PhysicalWaFiPostgresHelperCaptureBridgeConfig",
    "PhysicalWaFiPostgresHelperCaptureBridgeControl",
    "PhysicalWaFiPostgresHelperCaptureBridgeError",
    "PhysicalWaFiPostgresHelperCaptureBridgeHandoff",
    "build_physical_wa_fi_postgres_helper_capture_bridge_control",
    "canonical_physical_wa_fi_postgres_helper_capture_completion_bytes",
    "execute_physical_wa_fi_postgres_helper_capture_bridge",
    "require_physical_wa_fi_postgres_helper_capture_bridge_handoff",
)


PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-helper-capture-bridge-v1"
)
PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_ATTESTATION_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-helper-capture-attestation-v1"
)
PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_COMPLETION_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-helper-capture-completion-v1"
)
PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_DEFAULT_ENABLED = False

# These are deliberately separate from the helper's own fixed policy and
# from every legacy capture/spool path.  An installer must create both roots
# before any runtime call; the bridge never creates a broad parent itself.
FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT = Path(
    "/var/lib/trading-bot/physical-postgres/primary/helper-base-backup-captures"
)
FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_EVIDENCE_ROOT = Path(
    "/var/lib/trading-bot/physical-postgres/primary/helper-base-backup-evidence"
)

_DEFAULT_STATUS = "captured-helper-container-not-uploaded"
_ATTESTATION_VERSION = 1
_COMPLETION_VERSION = 1
_MAX_COMPLETION_BYTES = 64 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_CAPTURE_CHILD_PREFIX = "helper-capture-"
_RECEIPT_FILE_PREFIX = "helper-capture-"
_FIXED_ARTIFACT_NAME = "base.tar"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SAFE_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_CONTROL_CAPABILITY = object()
_HANDOFF_CAPABILITY = object()

_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "status",
        "campaign_id",
        "release_sha",
        "deployment_manifest_lock_sha256",
        "deployment_route_binding_sha256",
        "base_backup_manifest_binding_sha256",
        "strict_installation_request_sha256",
        "strict_installation_binding_sha256",
        "strict_runtime_attestation_sha256es",
        "bridge_control_sha256",
        "writer_witness",
        "helper",
        "artifact",
        "captured_at",
        "direct_fi_to_ir_ssh",
        "direct_fi_to_ir_scp",
        "direct_fi_to_ir_postgres_control",
        "not_an_object_storage_upload",
        "not_a_release_authorization",
        "not_a_launch_authorization",
        "not_a_writer_authorization",
        "not_a_promotion_authorization",
        "not_a_full_matrix_authorization",
    }
)
_COMPLETION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "status",
        "capture_attestation",
        "capture_attestation_sha256",
        "base_backup_route_binding_sha256",
        "object_storage_handoff_performed",
        "not_an_object_storage_upload",
        "not_a_release_authorization",
        "not_a_launch_authorization",
        "not_a_writer_authorization",
        "not_a_promotion_authorization",
        "not_a_full_matrix_authorization",
    }
)
_WRITER_WITNESS_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "writer_term_sha256",
    }
)
_HELPER_FIELDS = frozenset(
    {
        "configuration_sha256",
        "installation_attestation_sha256",
        "capture_configuration_sha256",
        "deployment_manifest_lock_sha256",
        "local_base_backup_auth_preflight_sha256",
        "invocation_sha256",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {"filename", "plaintext_sha256", "plaintext_bytes", "completion_attestation_sha256"}
)


class PhysicalWaFiPostgresHelperCaptureBridgeError(RuntimeError):
    """A fixed-code local capture/evidence bridge failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaFiPostgresHelperCaptureBridgeControl:
    """Opaque, fresh, attested input for exactly one local helper capture.

    The retained strict-installation request/observation and Witness proof are
    intentionally repr-suppressed.  They are revalidated before and after the
    injected helper call; direct construction cannot mint this capability.
    """

    strict_installation_request: PhysicalPostgresStrictRuntimeInstallationRequest = field(
        repr=False
    )
    strict_installation_observation: VerifiedPhysicalPostgresStrictRuntimeInstallations = field(
        repr=False
    )
    manifest_binding: PhysicalWalBaseBackupManifestBinding
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm = field(repr=False)
    capture_configuration_sha256: str
    bridge_control_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWaFiPostgresHelperCaptureBridgeConfig:
    """Default-off input; fixed roots are intentionally not caller-selectable."""

    control: PhysicalWaFiPostgresHelperCaptureBridgeControl | None = None
    enabled: bool = PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalWaFiPostgresHelperCaptureBridgeHandoff:
    """Local handoff facts consumable by a future reviewed spool coordinator.

    ``capture_source_root`` and ``completed_artifact`` intentionally match the
    existing base-backup spool's input concepts.  This object does not call
    that spool or an uploader; a caller must independently choose/validate
    the spool configuration and Object-Storage handoff.
    """

    capture_source_root: Path
    completed_artifact: PhysicalWalBaseBackupCompletedArtifact
    verified_base_backup_binding: VerifiedPhysicalWalBaseBackupBinding = field(
        repr=False
    )
    bridge_control: PhysicalWaFiPostgresHelperCaptureBridgeControl = field(repr=False)
    helper_evidence: "_HelperFacts" = field(repr=False)
    captured_at: datetime
    helper_invocation_sha256: str
    capture_attestation_sha256: str
    completion_receipt_path: Path
    completion_receipt_sha256: str
    canonical_completion_receipt: bytes = field(repr=False)
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ControlFacts:
    request: PhysicalPostgresStrictRuntimeInstallationRequest
    observation: VerifiedPhysicalPostgresStrictRuntimeInstallations
    manifest_binding: PhysicalWalBaseBackupManifestBinding
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    capture_configuration_sha256: str
    manifest_binding_sha256: str
    bridge_control_sha256: str


@dataclass(frozen=True)
class _HelperFacts:
    capture_source_root: Path
    artifact_path: Path
    artifact_sha256: str
    artifact_bytes: int
    configuration_sha256: str
    installation_attestation_sha256: str
    capture_configuration_sha256: str
    deployment_manifest_lock_sha256: str
    local_base_backup_auth_preflight_sha256: str
    invocation_sha256: str


def _fail(code: str) -> None:
    raise PhysicalWaFiPostgresHelperCaptureBridgeError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _strict_object(pairs: list[tuple[str, Any]], *, code: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(code)
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _safe_id(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or _SAFE_ID_RE.fullmatch(value) is None
        or value != value.strip()
        or "\x00" in value
        or _URL_OR_SECRET_RE.search(value) is not None
    ):
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat() != value:
        _fail(code)
    return normalized


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _manifest_binding_mapping(value: PhysicalWalBaseBackupManifestBinding) -> dict[str, object]:
    return {
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "baseline_generation_id": value.baseline_generation_id,
        "database_system_identifier": value.database_system_identifier,
        "timeline_id": value.timeline_id,
        "wal_segment_size_bytes": value.wal_segment_size_bytes,
        "baseline_wal_lsn": value.baseline_wal_lsn,
        "wal_chain_start_lsn": value.wal_chain_start_lsn,
        "base_backup_end_lsn": value.base_backup_end_lsn,
        "destination_age_recipient": value.destination_age_recipient,
        "object_storage_namespace": value.object_storage_namespace,
    }


def _writer_term_sha256(term: VerifiedObjectDeltaRoleMatrixWitnessedTerm) -> str:
    """Match the physical deployment manifest's writer-term projection."""

    try:
        return hashlib.sha256(
            _manifest_canonical_json_bytes(
                {
                    "holder_site": term.holder_site,
                    "writer_epoch": term.writer_epoch,
                    "writer_lease_id": term.writer_lease_id,
                    "witness_transition_id": term.witness_transition_id,
                    "term_proof_sha256": term.proof_sha256,
                }
            )
        ).hexdigest()
    except Exception:
        _fail("HELPER_CAPTURE_BRIDGE_TERM_INVALID")


def _preflight_manifest_binding(
    *,
    manifest_binding: object,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    now: datetime,
) -> PhysicalWalBaseBackupManifestBinding:
    """Use the established base-backup normalizer before the helper can run."""

    try:
        preflight = authorize_physical_wal_base_backup_binding(
            manifest_binding=manifest_binding,
            completed_artifact=PhysicalWalBaseBackupCompletedArtifact(
                artifact_name="helper-preflight.tar",
                plaintext_sha256="1" * 64,
                plaintext_bytes=1,
                completion_attestation_sha256="2" * 64,
            ),
            witnessed_term=witnessed_term,
            now=now,
        )
    except PhysicalWalBaseBackupSpoolError:
        _fail("HELPER_CAPTURE_BRIDGE_MANIFEST_OR_TERM_INVALID")
    return preflight.manifest_binding


def _strict_request_and_observation(
    *,
    request: object,
    observation: object,
    now: datetime,
) -> tuple[
    PhysicalPostgresStrictRuntimeInstallationRequest,
    VerifiedPhysicalPostgresStrictRuntimeInstallations,
]:
    try:
        normalized_request = require_physical_postgres_strict_runtime_installation_request(
            request
        )
        normalized_observation = require_verified_physical_postgres_strict_runtime_installations(
            observation,
            request=normalized_request,
            now=now,
        )
    except Exception:
        _fail("HELPER_CAPTURE_BRIDGE_STRICT_RUNTIME_NOT_ATTESTED")
    return normalized_request, normalized_observation


def _live_term(value: object, *, now: datetime) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    try:
        return require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("HELPER_CAPTURE_BRIDGE_TERM_INVALID")


def _control_sha256(
    *,
    request: PhysicalPostgresStrictRuntimeInstallationRequest,
    observation: VerifiedPhysicalPostgresStrictRuntimeInstallations,
    manifest_binding_sha256: str,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    capture_configuration_sha256: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_SCHEMA,
                "strict_installation_request_sha256": request.request_sha256,
                "strict_installation_binding_sha256": request.installation_binding_sha256,
                "strict_runtime_attestation_sha256es": [
                    [component, digest]
                    for component, digest in observation.attestation_sha256es
                ],
                "strict_runtime_verified_at": observation.verified_at.isoformat(),
                "strict_runtime_expires_at": observation.expires_at.isoformat(),
                "manifest_binding_sha256": manifest_binding_sha256,
                "writer_witness": {
                    "holder_site": term.holder_site,
                    "writer_epoch": term.writer_epoch,
                    "writer_lease_id": term.writer_lease_id,
                    "witness_transition_id": term.witness_transition_id,
                    "witnessed_term_proof_sha256": term.proof_sha256,
                },
                "capture_configuration_sha256": capture_configuration_sha256,
            },
            code="HELPER_CAPTURE_BRIDGE_CONTROL_INVALID",
        )
    ).hexdigest()


def _control_facts(
    value: object,
    *,
    now: datetime,
) -> _ControlFacts:
    if (
        type(value) is not PhysicalWaFiPostgresHelperCaptureBridgeControl
        or value._capability is not _CONTROL_CAPABILITY
    ):
        _fail("HELPER_CAPTURE_BRIDGE_CONTROL_INVALID")
    request, observation = _strict_request_and_observation(
        request=value.strict_installation_request,
        observation=value.strict_installation_observation,
        now=now,
    )
    term = _live_term(value.witnessed_term, now=now)
    manifest_binding = _preflight_manifest_binding(
        manifest_binding=value.manifest_binding,
        witnessed_term=term,
        now=now,
    )
    capture_configuration_sha256 = _sha256(
        value.capture_configuration_sha256,
        code="HELPER_CAPTURE_BRIDGE_CONTROL_INVALID",
    )
    if (
        request.campaign_id != manifest_binding.campaign_id
        or request.release_sha != manifest_binding.release_sha
        or manifest_binding.source_site != "webapp_fi"
        or manifest_binding.destination_site != "webapp_ir"
        or term.holder_site != "webapp_fi"
        or request.writer_term_sha256 != _writer_term_sha256(term)
    ):
        _fail("HELPER_CAPTURE_BRIDGE_BINDING_MISMATCH")
    for digest in (
        request.manifest_lock_sha256,
        request.route_binding_sha256,
        request.request_sha256,
        request.installation_binding_sha256,
    ):
        _sha256(digest, code="HELPER_CAPTURE_BRIDGE_CONTROL_INVALID")
    manifest_binding_sha256 = hashlib.sha256(
        _canonical(
            _manifest_binding_mapping(manifest_binding),
            code="HELPER_CAPTURE_BRIDGE_CONTROL_INVALID",
        )
    ).hexdigest()
    expected_control_sha256 = _control_sha256(
        request=request,
        observation=observation,
        manifest_binding_sha256=manifest_binding_sha256,
        term=term,
        capture_configuration_sha256=capture_configuration_sha256,
    )
    if value.bridge_control_sha256 != expected_control_sha256:
        _fail("HELPER_CAPTURE_BRIDGE_CONTROL_INVALID")
    return _ControlFacts(
        request=request,
        observation=observation,
        manifest_binding=manifest_binding,
        witnessed_term=term,
        capture_configuration_sha256=capture_configuration_sha256,
        manifest_binding_sha256=manifest_binding_sha256,
        bridge_control_sha256=expected_control_sha256,
    )


def build_physical_wa_fi_postgres_helper_capture_bridge_control(
    *,
    strict_installation_request: PhysicalPostgresStrictRuntimeInstallationRequest,
    strict_installation_observation: VerifiedPhysicalPostgresStrictRuntimeInstallations,
    manifest_binding: PhysicalWalBaseBackupManifestBinding,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    capture_configuration_sha256: str,
    now: datetime,
) -> PhysicalWaFiPostgresHelperCaptureBridgeControl:
    """Bind only currently attested strict runtime and live Witness facts.

    This does no filesystem or process work.  It cannot issue a release seal
    or substitute for one; it only rejects a mismatch before a future local
    helper capture is attempted.
    """

    observed_now = _utc(now, code="HELPER_CAPTURE_BRIDGE_CLOCK_INVALID")
    request, observation = _strict_request_and_observation(
        request=strict_installation_request,
        observation=strict_installation_observation,
        now=observed_now,
    )
    term = _live_term(witnessed_term, now=observed_now)
    normalized_manifest = _preflight_manifest_binding(
        manifest_binding=manifest_binding,
        witnessed_term=term,
        now=observed_now,
    )
    capture_sha256 = _sha256(
        capture_configuration_sha256,
        code="HELPER_CAPTURE_BRIDGE_CONTROL_INVALID",
    )
    if (
        request.campaign_id != normalized_manifest.campaign_id
        or request.release_sha != normalized_manifest.release_sha
        or normalized_manifest.source_site != "webapp_fi"
        or normalized_manifest.destination_site != "webapp_ir"
        or term.holder_site != "webapp_fi"
        or request.writer_term_sha256 != _writer_term_sha256(term)
    ):
        _fail("HELPER_CAPTURE_BRIDGE_BINDING_MISMATCH")
    manifest_binding_sha256 = hashlib.sha256(
        _canonical(
            _manifest_binding_mapping(normalized_manifest),
            code="HELPER_CAPTURE_BRIDGE_CONTROL_INVALID",
        )
    ).hexdigest()
    control_sha256 = _control_sha256(
        request=request,
        observation=observation,
        manifest_binding_sha256=manifest_binding_sha256,
        term=term,
        capture_configuration_sha256=capture_sha256,
    )
    result = PhysicalWaFiPostgresHelperCaptureBridgeControl(
        strict_installation_request=request,
        strict_installation_observation=observation,
        manifest_binding=normalized_manifest,
        witnessed_term=term,
        capture_configuration_sha256=capture_sha256,
        bridge_control_sha256=control_sha256,
    )
    object.__setattr__(result, "_capability", _CONTROL_CAPABILITY)
    return result


def _fixed_root(value: object, *, code: str) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or any(_SAFE_PATH_COMPONENT_RE.fullmatch(part) is None for part in value.parts[1:])
    ):
        _fail(code)
    return value


def _validate_ancestors(path: Path, *, code: str) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail(code)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            sticky_root_parent = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or (mode & 0o022 and not sticky_root_parent)
            ):
                _fail(code)
    except PhysicalWaFiPostgresHelperCaptureBridgeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _secure_root(value: object, *, code: str) -> Path:
    path = _fixed_root(value, code=code)
    _validate_ancestors(path, code=code)
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _roots_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False


def _fresh_capture_root(parent: Path) -> Path:
    for _ in range(8):
        child = parent / (_CAPTURE_CHILD_PREFIX + secrets.token_hex(16))
        try:
            child.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError:
            _fail("HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT_UNSAFE")
        try:
            metadata = os.lstat(child)
            resolved = child.resolve(strict=True)
        except OSError:
            _fail("HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT_UNSAFE")
        if (
            resolved == child
            and stat.S_ISDIR(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == 0
            and metadata.st_gid == 0
            and stat.S_IMODE(metadata.st_mode) == 0o700
        ):
            return resolved
        _fail("HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT_UNSAFE")
    _fail("HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT_UNSAFE")


def _file_fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
        metadata.st_nlink,
    )


def _secure_artifact_digest(
    *,
    source_root: Path,
    artifact_path: object,
) -> tuple[Path, str, int]:
    if not isinstance(artifact_path, Path) or artifact_path != source_root / _FIXED_ARTIFACT_NAME:
        _fail("HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID")
    try:
        root_metadata = os.lstat(source_root)
        root_resolved = source_root.resolve(strict=True)
        metadata = os.lstat(artifact_path)
        resolved = artifact_path.resolve(strict=True)
    except OSError:
        _fail("HELPER_CAPTURE_BRIDGE_ARTIFACT_UNSAFE")
    if (
        root_resolved != source_root
        or not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or root_metadata.st_uid != 0
        or root_metadata.st_gid != 0
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
        or resolved != artifact_path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= MAX_PHYSICAL_BASE_BACKUP_BYTES
        or not hasattr(os, "O_NOFOLLOW")
    ):
        _fail("HELPER_CAPTURE_BRIDGE_ARTIFACT_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(
            artifact_path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        fingerprint = _file_fingerprint(metadata)
        if _file_fingerprint(opened) != fingerprint:
            _fail("HELPER_CAPTURE_BRIDGE_ARTIFACT_UNSAFE")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_PHYSICAL_BASE_BACKUP_BYTES:
                _fail("HELPER_CAPTURE_BRIDGE_ARTIFACT_UNSAFE")
            digest.update(chunk)
        if total != metadata.st_size or _file_fingerprint(os.fstat(descriptor)) != fingerprint:
            _fail("HELPER_CAPTURE_BRIDGE_ARTIFACT_UNSAFE")
        return artifact_path, digest.hexdigest(), total
    except PhysicalWaFiPostgresHelperCaptureBridgeError:
        raise
    except OSError:
        _fail("HELPER_CAPTURE_BRIDGE_ARTIFACT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _helper_facts(
    *,
    result: object,
    source_root: Path,
    control: _ControlFacts,
) -> _HelperFacts:
    if type(result) is not PhysicalWaFiPostgresHelperContainerResult:
        _fail("HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID")
    if (
        result.capture_configuration_sha256 != control.capture_configuration_sha256
        or result.deployment_manifest_lock_sha256 != control.request.manifest_lock_sha256
    ):
        _fail("HELPER_CAPTURE_BRIDGE_BINDING_MISMATCH")
    fields = {
        "configuration_sha256": _sha256(
            result.configuration_sha256, code="HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID"
        ),
        "installation_attestation_sha256": _sha256(
            result.installation_attestation_sha256,
            code="HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID",
        ),
        "capture_configuration_sha256": _sha256(
            result.capture_configuration_sha256,
            code="HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID",
        ),
        "deployment_manifest_lock_sha256": _sha256(
            result.deployment_manifest_lock_sha256,
            code="HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID",
        ),
        "local_base_backup_auth_preflight_sha256": _sha256(
            result.local_base_backup_auth_preflight_sha256,
            code="HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID",
        ),
        "invocation_sha256": _sha256(
            result.invocation_sha256, code="HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID"
        ),
    }
    artifact_path, artifact_sha256, artifact_bytes = _secure_artifact_digest(
        source_root=source_root,
        artifact_path=result.collected_artifact_path,
    )
    if (
        result.collected_artifact_sha256 != artifact_sha256
        or type(result.collected_artifact_bytes) is not int
        or result.collected_artifact_bytes != artifact_bytes
    ):
        _fail("HELPER_CAPTURE_BRIDGE_HELPER_RESULT_INVALID")
    return _HelperFacts(
        capture_source_root=source_root,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        artifact_bytes=artifact_bytes,
        **fields,
    )


def _attestation_mapping(
    *,
    control: _ControlFacts,
    helper: _HelperFacts,
    completion_attestation_sha256: str | None,
    captured_at: datetime,
) -> dict[str, object]:
    """Build the non-self-referential core bound by the completed artifact."""

    artifact: dict[str, object] = {
        "filename": _FIXED_ARTIFACT_NAME,
        "plaintext_sha256": helper.artifact_sha256,
        "plaintext_bytes": helper.artifact_bytes,
    }
    if completion_attestation_sha256 is not None:
        artifact["completion_attestation_sha256"] = completion_attestation_sha256
    # The first pass intentionally omits completion_attestation_sha256.  Its
    # digest becomes that value, avoiding a receipt/route-binding hash cycle.
    if completion_attestation_sha256 is None:
        artifact["completion_attestation_sha256"] = ""
    return {
        "schema": PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_ATTESTATION_SCHEMA,
        "version": _ATTESTATION_VERSION,
        "status": _DEFAULT_STATUS,
        "campaign_id": control.request.campaign_id,
        "release_sha": control.request.release_sha,
        "deployment_manifest_lock_sha256": control.request.manifest_lock_sha256,
        "deployment_route_binding_sha256": control.request.route_binding_sha256,
        "base_backup_manifest_binding_sha256": control.manifest_binding_sha256,
        "strict_installation_request_sha256": control.request.request_sha256,
        "strict_installation_binding_sha256": control.request.installation_binding_sha256,
        "strict_runtime_attestation_sha256es": [
            [component, digest]
            for component, digest in control.observation.attestation_sha256es
        ],
        "bridge_control_sha256": control.bridge_control_sha256,
        "writer_witness": {
            "holder_site": control.witnessed_term.holder_site,
            "writer_epoch": control.witnessed_term.writer_epoch,
            "writer_lease_id": control.witnessed_term.writer_lease_id,
            "witness_transition_id": control.witnessed_term.witness_transition_id,
            "witnessed_term_proof_sha256": control.witnessed_term.proof_sha256,
            "writer_term_sha256": control.request.writer_term_sha256,
        },
        "helper": {
            "configuration_sha256": helper.configuration_sha256,
            "installation_attestation_sha256": helper.installation_attestation_sha256,
            "capture_configuration_sha256": helper.capture_configuration_sha256,
            "deployment_manifest_lock_sha256": helper.deployment_manifest_lock_sha256,
            "local_base_backup_auth_preflight_sha256": helper.local_base_backup_auth_preflight_sha256,
            "invocation_sha256": helper.invocation_sha256,
        },
        "artifact": artifact,
        "captured_at": captured_at.isoformat(),
        "direct_fi_to_ir_ssh": False,
        "direct_fi_to_ir_scp": False,
        "direct_fi_to_ir_postgres_control": False,
        "not_an_object_storage_upload": True,
        "not_a_release_authorization": True,
        "not_a_launch_authorization": True,
        "not_a_writer_authorization": True,
        "not_a_promotion_authorization": True,
        "not_a_full_matrix_authorization": True,
    }


def _capture_attestation(
    *, control: _ControlFacts, helper: _HelperFacts, captured_at: datetime
) -> tuple[dict[str, object], str]:
    # Hash a deterministic core without the derived completion hash itself.
    unsigned = _attestation_mapping(
        control=control,
        helper=helper,
        completion_attestation_sha256=None,
        captured_at=captured_at,
    )
    # The placeholder is not part of the attested schema.  Delete it before
    # hashing and then bind the resulting hash in the complete evidence.
    unsigned_artifact = dict(unsigned["artifact"])
    del unsigned_artifact["completion_attestation_sha256"]
    unsigned["artifact"] = unsigned_artifact
    digest = hashlib.sha256(
        _canonical(unsigned, code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
    ).hexdigest()
    complete = _attestation_mapping(
        control=control,
        helper=helper,
        completion_attestation_sha256=digest,
        captured_at=captured_at,
    )
    return complete, digest


def _completion_mapping(
    *,
    capture_attestation: Mapping[str, object],
    capture_attestation_sha256: str,
    base_backup_route_binding_sha256: str,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_COMPLETION_SCHEMA,
        "version": _COMPLETION_VERSION,
        "status": _DEFAULT_STATUS,
        "capture_attestation": dict(capture_attestation),
        "capture_attestation_sha256": capture_attestation_sha256,
        "base_backup_route_binding_sha256": base_backup_route_binding_sha256,
        "object_storage_handoff_performed": False,
        "not_an_object_storage_upload": True,
        "not_a_release_authorization": True,
        "not_a_launch_authorization": True,
        "not_a_writer_authorization": True,
        "not_a_promotion_authorization": True,
        "not_a_full_matrix_authorization": True,
    }


def canonical_physical_wa_fi_postgres_helper_capture_completion_bytes(
    value: Mapping[str, object],
) -> bytes:
    """Canonical receipt encoder only; it performs no filesystem operation."""

    if not isinstance(value, Mapping):
        _fail("HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
    return _canonical(
        dict(value), code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID"
    ) + b"\n"


def _read_secure_file(path: Path, *, maximum_bytes: int, code: str) -> bytes:
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
        or before.st_gid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        fingerprint = _file_fingerprint(before)
        if _file_fingerprint(opened) != fingerprint:
            _fail(code)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) or _file_fingerprint(os.fstat(descriptor)) != fingerprint:
            _fail(code)
        return b"".join(chunks)
    except PhysicalWaFiPostgresHelperCaptureBridgeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_directory(directory: Path, *, code: str) -> None:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(descriptor)
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_atomic_receipt(*, evidence_root: Path, payload: bytes) -> tuple[Path, str]:
    if not 1 <= len(payload) <= _MAX_COMPLETION_BYTES:
        _fail("HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    filename = _RECEIPT_FILE_PREFIX + payload_sha256 + ".json"
    destination = evidence_root / filename
    try:
        existing = os.lstat(destination)
    except FileNotFoundError:
        existing = None
    except OSError:
        _fail("HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE")
    if existing is not None:
        if _read_secure_file(
            destination,
            maximum_bytes=_MAX_COMPLETION_BYTES,
            code="HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE",
        ) != payload:
            _fail("HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE")
        return destination, payload_sha256

    temporary = evidence_root / (
        "." + filename + "." + secrets.token_hex(16) + ".tmp"
    )
    descriptor = -1
    linked = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if type(written) is not int or written <= 0:
                _fail("HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, destination, follow_symlinks=False)
            linked = True
        except FileExistsError:
            if _read_secure_file(
                destination,
                maximum_bytes=_MAX_COMPLETION_BYTES,
                code="HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE",
            ) != payload:
                _fail("HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE")
        os.unlink(temporary)
        temporary = Path("")
        _fsync_directory(evidence_root, code="HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE")
    except PhysicalWaFiPostgresHelperCaptureBridgeError:
        raise
    except OSError:
        _fail("HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary != Path("") and not linked:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    if _read_secure_file(
        destination,
        maximum_bytes=_MAX_COMPLETION_BYTES,
        code="HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE",
    ) != payload:
        _fail("HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE")
    return destination, payload_sha256


def _parse_completion(payload: object) -> dict[str, Any]:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_COMPLETION_BYTES:
        _fail("HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
    try:
        value = json.loads(
            payload[:-1].decode("ascii", "strict") if payload.endswith(b"\n") else "",
            object_pairs_hook=lambda pairs: _strict_object(
                pairs, code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID"
            ),
            parse_constant=_reject_json_constant,
        )
    except PhysicalWaFiPostgresHelperCaptureBridgeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
    if type(value) is not dict or canonical_physical_wa_fi_postgres_helper_capture_completion_bytes(value) != payload:
        _fail("HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
    return _exact_mapping(
        value, fields=_COMPLETION_FIELDS, code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID"
    )


def _validate_completion_shape(
    *,
    payload: bytes,
    expected_attestation_sha256: str,
    expected_route_binding_sha256: str,
) -> None:
    completion = _parse_completion(payload)
    attestation = _exact_mapping(
        completion["capture_attestation"],
        fields=_ATTESTATION_FIELDS,
        code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID",
    )
    artifact = _exact_mapping(
        attestation["artifact"],
        fields=_ARTIFACT_FIELDS,
        code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID",
    )
    writer = _exact_mapping(
        attestation["writer_witness"],
        fields=_WRITER_WITNESS_FIELDS,
        code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID",
    )
    helper = _exact_mapping(
        attestation["helper"],
        fields=_HELPER_FIELDS,
        code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID",
    )
    unsigned_attestation = dict(attestation)
    unsigned_artifact = dict(artifact)
    del unsigned_artifact["completion_attestation_sha256"]
    unsigned_attestation["artifact"] = unsigned_artifact
    calculated_attestation_sha256 = hashlib.sha256(
        _canonical(unsigned_attestation, code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
    ).hexdigest()
    if (
        completion["schema"] != PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_COMPLETION_SCHEMA
        or completion["version"] != _COMPLETION_VERSION
        or completion["status"] != _DEFAULT_STATUS
        or _sha256(
            completion["capture_attestation_sha256"],
            code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID",
        )
        != expected_attestation_sha256
        or calculated_attestation_sha256 != expected_attestation_sha256
        or _sha256(
            completion["base_backup_route_binding_sha256"],
            code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID",
        )
        != expected_route_binding_sha256
        or completion["object_storage_handoff_performed"] is not False
        or any(
            completion[field] is not True
            for field in (
                "not_an_object_storage_upload",
                "not_a_release_authorization",
                "not_a_launch_authorization",
                "not_a_writer_authorization",
                "not_a_promotion_authorization",
                "not_a_full_matrix_authorization",
            )
        )
        or attestation["schema"] != PHYSICAL_WA_FI_POSTGRES_HELPER_CAPTURE_ATTESTATION_SCHEMA
        or attestation["version"] != _ATTESTATION_VERSION
        or attestation["status"] != _DEFAULT_STATUS
        or artifact["filename"] != _FIXED_ARTIFACT_NAME
        or _sha256(artifact["plaintext_sha256"], code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
        is None
        or type(artifact["plaintext_bytes"]) is not int
        or artifact["plaintext_bytes"] < 1
        or _sha256(
            artifact["completion_attestation_sha256"],
            code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID",
        )
        != expected_attestation_sha256
        or writer["holder_site"] != "webapp_fi"
        or type(writer["writer_epoch"]) is not int
        or writer["writer_epoch"] < 1
        or not all(
            _safe_id(writer[field], code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
            for field in ("writer_lease_id", "witness_transition_id")
        )
        or not all(
            _sha256(writer[field], code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
            for field in ("witnessed_term_proof_sha256", "writer_term_sha256")
        )
        or not all(
            _sha256(helper[field], code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
            for field in _HELPER_FIELDS
        )
        or _timestamp(attestation["captured_at"], code="HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")
        is None
        or any(
            attestation[field] is not False
            for field in (
                "direct_fi_to_ir_ssh",
                "direct_fi_to_ir_scp",
                "direct_fi_to_ir_postgres_control",
            )
        )
        or any(
            attestation[field] is not True
            for field in (
                "not_an_object_storage_upload",
                "not_a_release_authorization",
                "not_a_launch_authorization",
                "not_a_writer_authorization",
                "not_a_promotion_authorization",
                "not_a_full_matrix_authorization",
            )
        )
    ):
        _fail("HELPER_CAPTURE_BRIDGE_COMPLETION_INVALID")


def _normalise_config(value: object) -> PhysicalWaFiPostgresHelperCaptureBridgeControl:
    if type(value) is not PhysicalWaFiPostgresHelperCaptureBridgeConfig:
        _fail("HELPER_CAPTURE_BRIDGE_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("HELPER_CAPTURE_BRIDGE_DISABLED")
    if type(value.control) is not PhysicalWaFiPostgresHelperCaptureBridgeControl:
        _fail("HELPER_CAPTURE_BRIDGE_CONFIG_INVALID")
    return value.control


def _recheck_clock(clock: Callable[[], datetime], *, code: str) -> datetime:
    try:
        return _utc(clock(), code=code)
    except PhysicalWaFiPostgresHelperCaptureBridgeError:
        raise
    except Exception:
        _fail(code)


def execute_physical_wa_fi_postgres_helper_capture_bridge(
    *,
    config: PhysicalWaFiPostgresHelperCaptureBridgeConfig,
    now: datetime,
    completion_recheck_clock: Callable[[], datetime] | None,
    helper_runner: PhysicalWaFiPostgresHelperContainerRunner | None,
) -> PhysicalWaFiPostgresHelperCaptureBridgeHandoff:
    """Run only the injected helper and issue local, non-authorizing evidence.

    No uploader, Object-Storage client, direct FI-to-IR control, or legacy
    host ``pg_basebackup`` runner is accepted here.  The future handoff owner
    may feed the returned artifact/binding into the existing spool only after
    its own independent policy and fresh term checks.
    """

    control_value = _normalise_config(config)
    observed_now = _utc(now, code="HELPER_CAPTURE_BRIDGE_CLOCK_INVALID")
    if (
        completion_recheck_clock is None
        or not callable(completion_recheck_clock)
        or helper_runner is None
        or not callable(getattr(helper_runner, "run", None))
    ):
        _fail("HELPER_CAPTURE_BRIDGE_DEPENDENCIES_INVALID")
    if os.geteuid() != 0:
        _fail("HELPER_CAPTURE_BRIDGE_ROOT_RUNTIME_REQUIRED")
    control = _control_facts(control_value, now=observed_now)
    capture_parent = _secure_root(
        FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT,
        code="HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT_UNSAFE",
    )
    evidence_root = _secure_root(
        FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_EVIDENCE_ROOT,
        code="HELPER_CAPTURE_BRIDGE_EVIDENCE_ROOT_UNSAFE",
    )
    if _roots_overlap(capture_parent, evidence_root):
        _fail("HELPER_CAPTURE_BRIDGE_ROOTS_OVERLAP")
    capture_source_root = _fresh_capture_root(capture_parent)
    helper_request = PhysicalWaFiPostgresHelperContainerCaptureRequest(
        capture_configuration_sha256=control.capture_configuration_sha256,
        capture_output_root=capture_source_root,
        writer_epoch=control.witnessed_term.writer_epoch,
        writer_lease_id=control.witnessed_term.writer_lease_id,
        witness_transition_id=control.witnessed_term.witness_transition_id,
        witnessed_term_proof_sha256=control.witnessed_term.proof_sha256,
    )
    try:
        helper_result = execute_wa_fi_postgres_helper_container_capture(
            (), request=helper_request, runner=helper_runner
        )
    except PhysicalWaFiPostgresHelperContainerError:
        _fail("HELPER_CAPTURE_BRIDGE_HELPER_CAPTURE_FAILED")
    except Exception:
        _fail("HELPER_CAPTURE_BRIDGE_HELPER_CAPTURE_FAILED")
    completion_now = _recheck_clock(
        completion_recheck_clock, code="HELPER_CAPTURE_BRIDGE_COMPLETION_CLOCK_INVALID"
    )
    if completion_now < observed_now:
        _fail("HELPER_CAPTURE_BRIDGE_COMPLETION_CLOCK_INVALID")
    completion_control = _control_facts(control_value, now=completion_now)
    if completion_control != control:
        _fail("HELPER_CAPTURE_BRIDGE_CONTROL_CHANGED")
    helper = _helper_facts(
        result=helper_result,
        source_root=capture_source_root,
        control=completion_control,
    )
    attestation, attestation_sha256 = _capture_attestation(
        control=completion_control,
        helper=helper,
        captured_at=completion_now,
    )
    completed_artifact = PhysicalWalBaseBackupCompletedArtifact(
        artifact_name=_FIXED_ARTIFACT_NAME,
        plaintext_sha256=helper.artifact_sha256,
        plaintext_bytes=helper.artifact_bytes,
        completion_attestation_sha256=attestation_sha256,
    )
    try:
        verified_binding = authorize_physical_wal_base_backup_binding(
            manifest_binding=completion_control.manifest_binding,
            completed_artifact=completed_artifact,
            witnessed_term=completion_control.witnessed_term,
            now=completion_now,
        )
    except PhysicalWalBaseBackupSpoolError:
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_BINDING_INVALID")
    completion = _completion_mapping(
        capture_attestation=attestation,
        capture_attestation_sha256=attestation_sha256,
        base_backup_route_binding_sha256=verified_binding.route_binding_sha256,
    )
    completion_bytes = canonical_physical_wa_fi_postgres_helper_capture_completion_bytes(
        completion
    )
    _validate_completion_shape(
        payload=completion_bytes,
        expected_attestation_sha256=attestation_sha256,
        expected_route_binding_sha256=verified_binding.route_binding_sha256,
    )
    receipt_path, receipt_sha256 = _write_atomic_receipt(
        evidence_root=evidence_root, payload=completion_bytes
    )
    result = PhysicalWaFiPostgresHelperCaptureBridgeHandoff(
        capture_source_root=helper.capture_source_root,
        completed_artifact=completed_artifact,
        verified_base_backup_binding=verified_binding,
        bridge_control=control_value,
        helper_evidence=helper,
        captured_at=completion_now,
        helper_invocation_sha256=helper.invocation_sha256,
        capture_attestation_sha256=attestation_sha256,
        completion_receipt_path=receipt_path,
        completion_receipt_sha256=receipt_sha256,
        canonical_completion_receipt=completion_bytes,
    )
    object.__setattr__(result, "_capability", _HANDOFF_CAPABILITY)
    return result


def require_physical_wa_fi_postgres_helper_capture_bridge_handoff(
    value: object,
    *,
    now: datetime,
) -> PhysicalWaFiPostgresHelperCaptureBridgeHandoff:
    """Recheck one local handoff without uploading, launching, or promoting."""

    if (
        type(value) is not PhysicalWaFiPostgresHelperCaptureBridgeHandoff
        or value._capability is not _HANDOFF_CAPABILITY
    ):
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    observed_now = _utc(now, code="HELPER_CAPTURE_BRIDGE_CLOCK_INVALID")
    control = _control_facts(value.bridge_control, now=observed_now)
    captured_at = _utc(value.captured_at, code="HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    helper = value.helper_evidence
    if (
        type(helper) is not _HelperFacts
        or helper.capture_source_root != value.capture_source_root
        or helper.artifact_path != value.capture_source_root / _FIXED_ARTIFACT_NAME
        or helper.capture_configuration_sha256 != control.capture_configuration_sha256
        or helper.deployment_manifest_lock_sha256 != control.request.manifest_lock_sha256
        or value.helper_invocation_sha256 != helper.invocation_sha256
        or any(
            _sha256(getattr(helper, field), code="HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
            is None
            for field in (
                "configuration_sha256",
                "installation_attestation_sha256",
                "capture_configuration_sha256",
                "deployment_manifest_lock_sha256",
                "local_base_backup_auth_preflight_sha256",
                "invocation_sha256",
            )
        )
    ):
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    if (
        value.capture_attestation_sha256
        != value.completed_artifact.completion_attestation_sha256
        or value.helper_invocation_sha256 == ""
        or _sha256(value.helper_invocation_sha256, code="HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
        is None
        or _sha256(
            value.capture_attestation_sha256, code="HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID"
        )
        is None
        or _sha256(
            value.completion_receipt_sha256, code="HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID"
        )
        != hashlib.sha256(value.canonical_completion_receipt).hexdigest()
    ):
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    artifact_path, artifact_sha256, artifact_bytes = _secure_artifact_digest(
        source_root=value.capture_source_root,
        artifact_path=value.capture_source_root / _FIXED_ARTIFACT_NAME,
    )
    if (
        artifact_path != value.capture_source_root / _FIXED_ARTIFACT_NAME
        or artifact_sha256 != value.completed_artifact.plaintext_sha256
        or artifact_bytes != value.completed_artifact.plaintext_bytes
        or helper.artifact_sha256 != artifact_sha256
        or helper.artifact_bytes != artifact_bytes
    ):
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    try:
        binding = require_verified_physical_wal_base_backup_binding(
            value.verified_base_backup_binding, now=observed_now
        )
    except PhysicalWalBaseBackupSpoolError:
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    if (
        binding.manifest_binding != control.manifest_binding
        or binding.completed_artifact != value.completed_artifact
        or binding.witnessed_term != control.witnessed_term
    ):
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    evidence_root = _secure_root(
        FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_EVIDENCE_ROOT,
        code="HELPER_CAPTURE_BRIDGE_EVIDENCE_ROOT_UNSAFE",
    )
    expected_path = evidence_root / (
        _RECEIPT_FILE_PREFIX + value.completion_receipt_sha256 + ".json"
    )
    if value.completion_receipt_path != expected_path:
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    stored = _read_secure_file(
        expected_path,
        maximum_bytes=_MAX_COMPLETION_BYTES,
        code="HELPER_CAPTURE_BRIDGE_RECEIPT_UNSAFE",
    )
    if stored != value.canonical_completion_receipt:
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    expected_attestation, expected_attestation_sha256 = _capture_attestation(
        control=control,
        helper=helper,
        captured_at=captured_at,
    )
    if expected_attestation_sha256 != value.capture_attestation_sha256:
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    expected_completion = canonical_physical_wa_fi_postgres_helper_capture_completion_bytes(
        _completion_mapping(
            capture_attestation=expected_attestation,
            capture_attestation_sha256=expected_attestation_sha256,
            base_backup_route_binding_sha256=binding.route_binding_sha256,
        )
    )
    if stored != expected_completion:
        _fail("HELPER_CAPTURE_BRIDGE_HANDOFF_INVALID")
    _validate_completion_shape(
        payload=stored,
        expected_attestation_sha256=value.capture_attestation_sha256,
        expected_route_binding_sha256=binding.route_binding_sha256,
    )
    return value
