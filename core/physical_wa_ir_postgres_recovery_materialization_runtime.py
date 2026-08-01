"""Root-only WA-IR phase-3 PostgreSQL recovery materialization runtime.

This is the deliberately narrow follow-on to
``physical_wa_ir_postgres_recovery_pull_runtime``.  It consumes that runtime's
already staged exact artifacts, revalidates a fresh sealed release, live FI
Witness term, route, and replay evidence, then gives one injected *local*
Docker/PostgreSQL runner two fixed operations:

* materialize a detached PGDATA candidate through the existing FD-only
  bootstrap boundary; and
* inspect that candidate through an isolated Unix-socket-only recovery
  profile so the existing collector can mint fresh replay evidence.

The module contains no Docker, subprocess, socket, PostgreSQL, SSH, network,
or Object-Storage client.  A runner is always injected and never receives a
caller-selected command, environment, host, URL, credential, path, or network
profile.  A successful result is recovery evidence only.  It never promotes a
standby, authorizes a writer, changes traffic, or authorizes Full Matrix.

The pre-existing bootstrap boundary intentionally requires a current
replay-observed readback evidence *before* it will make the materializer
reachable.  This runtime preserves that safety property: callers must supply
that narrow admission evidence, and this runtime produces a newly collected
post-run evidence/receipt rather than pretending a fresh restore itself is
already a recovery proof.
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
import stat
from typing import Any, Protocol

from core.append_only_sync_delta_batch import CAMPAIGN_ID_RE, RELEASE_SHA_RE, canonical_json_bytes
from core import physical_release_seal_admission as _release_seal
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.physical_postgres_recovery_preflight import (
    PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryPreflightResult,
    PhysicalPostgresRecoveryReceiverReadbackEvidence,
    assess_physical_postgres_recovery_preflight,
)
from core.physical_postgres_recovery_readback_collector import (
    PhysicalPostgresRecoveryLocalInspection,
    PhysicalPostgresRecoveryReadbackInspectionRequest,
    PhysicalPostgresRecoveryReadbackRootConfig,
    collect_physical_postgres_recovery_receiver_readback,
)
from core.physical_postgres_standby_bootstrap_materialization import (
    PhysicalPostgresStandbyBootstrapMaterializationAck,
    PhysicalPostgresStandbyBootstrapMaterializationError,
    PhysicalPostgresStandbyBootstrapMaterializationPlan,
    PhysicalPostgresStandbyBootstrapMaterializationResult,
    PhysicalPostgresStandbyBootstrapRootConfig,
    materialize_physical_postgres_standby_bootstrap,
)
from core.physical_wa_ir_postgres_recovery_pull_runtime import (
    PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED,
    PhysicalWaIrPostgresRecoveryPullRedactedReceipt,
    PhysicalWaIrPostgresRecoveryPullResult,
)
from core.physical_wal_object_manifest import (
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
)


__all__ = (
    "DEFAULT_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_DEFAULT_ENABLED",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RECEIPT_SCHEMA",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS_BLOCKED",
    "PHYSICAL_WA_IR_POSTGRES_SOCKET_ONLY_RECOVERY_INPUT_SCHEMA",
    "PhysicalWaIrPostgresRecoveryMaterializationDurableEvidence",
    "PhysicalWaIrPostgresRecoveryMaterializationError",
    "PhysicalWaIrPostgresRecoveryMaterializationResult",
    "PhysicalWaIrPostgresSocketOnlyRecoveryDeployment",
    "PhysicalWaIrPostgresSocketOnlyRecoveryInspectionInvocation",
    "PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation",
    "PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs",
    "PhysicalWaIrPostgresSocketOnlyRecoveryRunner",
    "RootOwnedWaIrPostgresRecoveryMaterializationRuntime",
    "RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig",
    "render_wa_ir_postgres_socket_only_recovery_inputs",
    "run_root_owned_wa_ir_postgres_recovery_materialization",
    "validate_root_owned_wa_ir_postgres_recovery_materialization_runtime_config",
)


PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-recovery-materialization-runtime-v1"
)
PHYSICAL_WA_IR_POSTGRES_SOCKET_ONLY_RECOVERY_INPUT_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-socket-only-recovery-input-v1"
)
PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RECEIPT_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-recovery-materialization-receipt-v1"
)
PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_DEFAULT_ENABLED = False

PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS = (
    "recovery-replay-observed-not-promoted"
)
PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS_BLOCKED = "blocked"

DEFAULT_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_MAX_EVIDENCE_AGE_SECONDS = 120
_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_RELEASE_SEAL_AGE_SECONDS = 300
_SOURCE_SITE = "webapp_fi"
_RECEIVER_SITE = "webapp_ir"
_POSTGRES_IMAGE_ROLE = "postgres_15"
_POSTGRES_MAJOR = 15
_SOCKET_DIRECTORY = "/var/run/postgresql"
_SOCKET_PORT = 5432
_RUNTIME_MODE = "root-owned-wa-ir-socket-only-recovery-materialization-v1"
_RECEIPTS_DIRECTORY = "phase3-recovery-receipts"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IMAGE_REFERENCE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]{1,511}@sha256:[0-9a-f]{64}$",
    re.ASCII,
)

_SOCKET_INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "sealed_release_descriptor_sha256",
        "deployment_manifest_lock_sha256",
        "route_binding_sha256",
        "postgres_image",
        "postgres_major",
        "network_mode",
        "tcp_listener",
        "unix_socket_directory",
        "unix_socket_port",
        "socket_authentication",
        "recovery_mode",
        "direct_site_control",
        "destination_object_ingest",
        "promotion_authorized",
        "full_matrix_authorized",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "sealed_release_descriptor_sha256",
        "deployment_manifest_lock_sha256",
        "socket_only_recovery_input_sha256",
        "bundle_id",
        "stage_receipt_sha256",
        "route_binding_sha256",
        "manifest_sha256es",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "bootstrap_id",
        "bootstrap_plan_sha256",
        "bootstrap_receipt_sha256",
        "recovery_evidence_sha256",
        "observed_at",
        "promotion_authorized",
        "writer_authorized",
        "traffic_switch_authorized",
        "full_matrix_authorized",
        "receipt_integrity_sha256",
    }
)


class PhysicalWaIrPostgresRecoveryMaterializationError(ValueError):
    """One redacted refusal from the WA-IR phase-3 recovery runtime."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaIrPostgresSocketOnlyRecoveryDeployment:
    """The complete non-secret recovery-test deployment policy.

    This is not a Docker command or Compose file.  It permits only a
    network-isolated PostgreSQL 15 recovery process whose local observation
    surface is a Unix socket.  The fixed runner may interpret it only after
    the runtime has rendered its canonical bytes and bound it to the current
    release, route, materialization plan, and Witness term.
    """

    schema: str = PHYSICAL_WA_IR_POSTGRES_SOCKET_ONLY_RECOVERY_INPUT_SCHEMA
    campaign_id: str = ""
    release_sha: str = ""
    deployment_manifest_lock_sha256: str = ""
    route_binding_sha256: str = ""
    postgres_image: str = ""
    postgres_major: int = _POSTGRES_MAJOR
    network_mode: str = "none"
    tcp_listener: str = "disabled"
    unix_socket_directory: str = _SOCKET_DIRECTORY
    unix_socket_port: int = _SOCKET_PORT
    socket_authentication: str = "peer-local-only"
    recovery_mode: str = "standby-replay-only"
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs:
    """Canonical fixed recovery inputs, not a launch authority."""

    canonical_input: bytes
    input_sha256: str
    campaign_id: str
    release_sha: str
    route_binding_sha256: str
    deployment_manifest_lock_sha256: str
    sealed_release_descriptor_sha256: str
    postgres_image: str


@dataclass(frozen=True)
class PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation:
    """Exact input for the one FD-only materialization runner call."""

    schema: str
    rendered_inputs: PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs
    bootstrap_id: str
    bootstrap_plan_sha256: str
    source_site: str
    receiver_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    invocation_sha256: str


@dataclass(frozen=True)
class PhysicalWaIrPostgresSocketOnlyRecoveryInspectionInvocation:
    """Exact input for one local socket-only recovery inspection."""

    schema: str
    rendered_inputs: PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs
    bootstrap_id: str
    bootstrap_plan_sha256: str
    bootstrap_receipt_sha256: str
    source_site: str
    receiver_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    invocation_sha256: str


class PhysicalWaIrPostgresSocketOnlyRecoveryRunner(Protocol):
    """Only the two fixed local operations permitted to the injected runner."""

    def materialize_socket_only_standby(
        self,
        *,
        invocation: PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation,
        plan: PhysicalPostgresStandbyBootstrapMaterializationPlan,
        source_stage_fd: int,
        target_pgdata_fd: int,
        recovery_signal_seed_fd: int,
    ) -> PhysicalPostgresStandbyBootstrapMaterializationAck:
        """Restore exactly the fixed detached candidate through local FDs only."""

    def inspect_socket_only_standby(
        self,
        *,
        invocation: PhysicalWaIrPostgresSocketOnlyRecoveryInspectionInvocation,
        target_pgdata_fd: int,
        request: PhysicalPostgresRecoveryReadbackInspectionRequest,
    ) -> PhysicalPostgresRecoveryLocalInspection:
        """Inspect only the same detached candidate via the fixed Unix socket."""


@dataclass(frozen=True)
class RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig:
    """Root-owned, default-off configuration for phase-3 recovery proof."""

    schema: str = PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA
    socket_only_deployment: PhysicalWaIrPostgresSocketOnlyRecoveryDeployment | None = field(
        default=None, repr=False, compare=False
    )
    sealed_release_descriptor: _release_seal.SealedPhysicalReleaseDescriptor | None = field(
        default=None, repr=False, compare=False
    )
    bootstrap_root_config: PhysicalPostgresStandbyBootstrapRootConfig | None = field(
        default=None, repr=False, compare=False
    )
    readback_root_config: PhysicalPostgresRecoveryReadbackRootConfig | None = field(
        default=None, repr=False, compare=False
    )
    redacted_receipt_root: Path | None = field(default=None, repr=False, compare=False)
    maximum_recovery_evidence_age_seconds: int = (
        DEFAULT_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_MAX_EVIDENCE_AGE_SECONDS
    )
    maximum_release_seal_freshness_seconds: int = 180
    enabled: bool = PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    receiver_site: str = _RECEIVER_SITE
    runtime_mode: str = _RUNTIME_MODE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalWaIrPostgresRecoveryMaterializationDurableEvidence:
    """Frozen redacted evidence a phase-3 adapter can turn into an oracle."""

    raw_receipt: bytes
    receipt_sha256: str
    recovery_evidence_sha256: str
    bootstrap_plan_sha256: str
    bootstrap_receipt_sha256: str
    observed_at: datetime


@dataclass(frozen=True)
class PhysicalWaIrPostgresRecoveryMaterializationResult:
    """Fresh recovery proof, explicitly not promotion/full-matrix authority."""

    schema: str
    status: str
    reason_codes: tuple[str, ...]
    rendered_inputs: PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs | None = None
    materialization: PhysicalPostgresStandbyBootstrapMaterializationResult | None = None
    recovery_evidence: PhysicalPostgresRecoveryReceiverReadbackEvidence | None = None
    recovery_result: PhysicalPostgresRecoveryPreflightResult | None = None
    durable_evidence: PhysicalWaIrPostgresRecoveryMaterializationDurableEvidence | None = None
    idempotent: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    traffic_switch_authorized: bool = False
    full_matrix_authorized: bool = False

    @property
    def replay_observed(self) -> bool:
        return self.status == PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS


@dataclass(frozen=True)
class _TermFacts:
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    proof_sha256: str
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm


@dataclass(frozen=True)
class _PullFacts:
    binding: PhysicalPostgresRecoveryPreflightBinding
    stage_evidence: object
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str


@dataclass(frozen=True)
class _RuntimeFacts:
    deployment: PhysicalWaIrPostgresSocketOnlyRecoveryDeployment
    sealed_release: _release_seal.SealedPhysicalReleaseDescriptor
    bootstrap_root_config: PhysicalPostgresStandbyBootstrapRootConfig
    readback_root_config: PhysicalPostgresRecoveryReadbackRootConfig
    redacted_receipt_root: Path
    maximum_recovery_evidence_age_seconds: int
    maximum_release_seal_freshness_seconds: int


def _fail(code: str) -> None:
    raise PhysicalWaIrPostgresRecoveryMaterializationError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("WA_IR_RECOVERY_MATERIALIZATION_ROOT_REQUIRED")
    except OSError:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_ROOT_REQUIRED")


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


def _safe_private_directory(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    try:
        before = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != value
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _release_image(
    sealed: _release_seal.SealedPhysicalReleaseDescriptor,
    *,
    code: str,
) -> str:
    images = getattr(sealed, "canonical_descriptor", None)
    if not isinstance(images, bytes):
        _fail(code)
    try:
        parsed = json.loads(images.decode("ascii", "strict"))
        raw_images = parsed["images"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(raw_images, list):
        _fail(code)
    found = [item for item in raw_images if type(item) is dict and item.get("role") == _POSTGRES_IMAGE_ROLE]
    if len(found) != 1 or type(found[0].get("reference")) is not str:
        _fail(code)
    reference = found[0]["reference"]
    if _IMAGE_REFERENCE_RE.fullmatch(reference) is None:
        _fail(code)
    return reference


def _deployment_mapping(
    deployment: PhysicalWaIrPostgresSocketOnlyRecoveryDeployment,
    *,
    sealed: _release_seal.SealedPhysicalReleaseDescriptor,
) -> dict[str, Any]:
    if type(deployment) is not PhysicalWaIrPostgresSocketOnlyRecoveryDeployment:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_SOCKET_INPUT_INVALID")
    if (
        deployment.schema != PHYSICAL_WA_IR_POSTGRES_SOCKET_ONLY_RECOVERY_INPUT_SCHEMA
        or type(deployment.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(deployment.campaign_id) is None
        or type(deployment.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(deployment.release_sha) is None
        or deployment.postgres_major != _POSTGRES_MAJOR
        or deployment.network_mode != "none"
        or deployment.tcp_listener != "disabled"
        or deployment.unix_socket_directory != _SOCKET_DIRECTORY
        or deployment.unix_socket_port != _SOCKET_PORT
        or deployment.socket_authentication != "peer-local-only"
        or deployment.recovery_mode != "standby-replay-only"
        or deployment.direct_site_control != "forbidden"
        or deployment.destination_object_ingest != "pull-only"
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_SOCKET_INPUT_INVALID")
    expected_image = _release_image(sealed, code="WA_IR_RECOVERY_MATERIALIZATION_RELEASE_INVALID")
    if deployment.postgres_image != expected_image:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_SOCKET_IMAGE_MISMATCH")
    return {
        "schema": PHYSICAL_WA_IR_POSTGRES_SOCKET_ONLY_RECOVERY_INPUT_SCHEMA,
        "status": "default-off-socket-only-recovery-input",
        "campaign_id": deployment.campaign_id,
        "release_sha": deployment.release_sha,
        "sealed_release_descriptor_sha256": _sha256(
            sealed.descriptor_sha256,
            code="WA_IR_RECOVERY_MATERIALIZATION_RELEASE_INVALID",
        ),
        "deployment_manifest_lock_sha256": _sha256(
            deployment.deployment_manifest_lock_sha256,
            code="WA_IR_RECOVERY_MATERIALIZATION_SOCKET_INPUT_INVALID",
        ),
        "route_binding_sha256": _sha256(
            deployment.route_binding_sha256,
            code="WA_IR_RECOVERY_MATERIALIZATION_SOCKET_INPUT_INVALID",
        ),
        "postgres_image": expected_image,
        "postgres_major": _POSTGRES_MAJOR,
        "network_mode": "none",
        "tcp_listener": "disabled",
        "unix_socket_directory": _SOCKET_DIRECTORY,
        "unix_socket_port": _SOCKET_PORT,
        "socket_authentication": "peer-local-only",
        "recovery_mode": "standby-replay-only",
        "direct_site_control": "forbidden",
        "destination_object_ingest": "pull-only",
        "promotion_authorized": False,
        "full_matrix_authorized": False,
    }


def render_wa_ir_postgres_socket_only_recovery_inputs(
    deployment: PhysicalWaIrPostgresSocketOnlyRecoveryDeployment,
    *,
    sealed_release: _release_seal.SealedPhysicalReleaseDescriptor,
) -> PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs:
    """Render exact network-isolated, socket-only recovery inputs without I/O."""

    payload = _deployment_mapping(deployment, sealed=sealed_release)
    if set(payload) != _SOCKET_INPUT_FIELDS:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_SOCKET_INPUT_INVALID")
    try:
        raw = canonical_json_bytes(payload)
    except (TypeError, ValueError):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_SOCKET_INPUT_INVALID")
    return PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs(
        canonical_input=raw,
        input_sha256=hashlib.sha256(raw).hexdigest(),
        campaign_id=deployment.campaign_id,
        release_sha=deployment.release_sha,
        route_binding_sha256=deployment.route_binding_sha256,
        deployment_manifest_lock_sha256=deployment.deployment_manifest_lock_sha256,
        sealed_release_descriptor_sha256=sealed_release.descriptor_sha256,
        postgres_image=deployment.postgres_image,
    )


def _inert_config_shape(config: object) -> RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig:
    if type(config) is not RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA
        or type(config.enabled) is not bool
        or config.source_site != _SOURCE_SITE
        or config.receiver_site != _RECEIVER_SITE
        or config.runtime_mode != _RUNTIME_MODE
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
        or type(config.socket_only_deployment) is not PhysicalWaIrPostgresSocketOnlyRecoveryDeployment
        or type(config.sealed_release_descriptor) is not _release_seal.SealedPhysicalReleaseDescriptor
        or type(config.bootstrap_root_config) is not PhysicalPostgresStandbyBootstrapRootConfig
        or type(config.readback_root_config) is not PhysicalPostgresRecoveryReadbackRootConfig
        or not isinstance(config.redacted_receipt_root, Path)
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_CONFIG_INVALID")
    _positive(
        config.maximum_recovery_evidence_age_seconds,
        maximum=_MAX_EVIDENCE_AGE_SECONDS,
        code="WA_IR_RECOVERY_MATERIALIZATION_EVIDENCE_AGE_INVALID",
    )
    _positive(
        config.maximum_release_seal_freshness_seconds,
        maximum=_MAX_RELEASE_SEAL_AGE_SECONDS,
        code="WA_IR_RECOVERY_MATERIALIZATION_RELEASE_AGE_INVALID",
    )
    _sha256(
        config.socket_only_deployment.deployment_manifest_lock_sha256,
        code="WA_IR_RECOVERY_MATERIALIZATION_CONFIG_INVALID",
    )
    _sha256(
        config.socket_only_deployment.route_binding_sha256,
        code="WA_IR_RECOVERY_MATERIALIZATION_CONFIG_INVALID",
    )
    return config


def validate_root_owned_wa_ir_postgres_recovery_materialization_runtime_config(
    config: RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig,
) -> RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig:
    """Validate only inert config shape; no path, runner, or network is opened."""

    return _inert_config_shape(config)


def _runtime_facts(
    config: RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig,
    *,
    now: datetime,
    require_enabled: bool,
) -> _RuntimeFacts:
    checked = _inert_config_shape(config)
    if require_enabled and checked.enabled is not True:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_DISABLED")
    try:
        sealed = _release_seal.require_sealed_physical_release_descriptor(
            checked.sealed_release_descriptor,
            now=now,
            maximum_freshness_seconds=checked.maximum_release_seal_freshness_seconds,
        )
    except Exception:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RELEASE_INVALID")
    deployment = checked.socket_only_deployment
    assert deployment is not None
    if deployment.campaign_id != sealed.campaign_id or deployment.release_sha != sealed.release_sha:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RELEASE_BINDING_MISMATCH")
    _deployment_mapping(deployment, sealed=sealed)
    bootstrap = checked.bootstrap_root_config
    readback = checked.readback_root_config
    assert bootstrap is not None and readback is not None
    if (
        bootstrap.enabled is not True
        or bootstrap.owner_uid != 0
        or readback.enabled is not True
        or readback.root_owner_uid != 0
        or readback.source_site != _SOURCE_SITE
        or readback.receiver_site != _RECEIVER_SITE
        or bootstrap.maximum_recovery_evidence_age_seconds
        != checked.maximum_recovery_evidence_age_seconds
        or readback.maximum_evidence_age_seconds
        != checked.maximum_recovery_evidence_age_seconds
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_COMPONENT_CONFIG_MISMATCH")
    return _RuntimeFacts(
        deployment=deployment,
        sealed_release=sealed,
        bootstrap_root_config=bootstrap,
        readback_root_config=readback,
        redacted_receipt_root=_safe_private_directory(
            checked.redacted_receipt_root,
            code="WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_ROOT_UNSAFE",
        ),
        maximum_recovery_evidence_age_seconds=checked.maximum_recovery_evidence_age_seconds,
        maximum_release_seal_freshness_seconds=checked.maximum_release_seal_freshness_seconds,
    )


def _term(value: object, *, now: datetime) -> _TermFacts:
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_TERM_INVALID_OR_STALE")
    if term.holder_site != _SOURCE_SITE:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_TERM_ROUTE_INVALID")
    if type(term.writer_epoch) is not int or term.writer_epoch < 1:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_TERM_INVALID_OR_STALE")
    return _TermFacts(
        holder_site=term.holder_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        proof_sha256=_sha256(term.proof_sha256, code="WA_IR_RECOVERY_MATERIALIZATION_TERM_INVALID_OR_STALE"),
        term=term,
    )


def _same_term(left: _TermFacts, right: _TermFacts) -> bool:
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


def _bundle(value: object, *, facts: _RuntimeFacts, term: _TermFacts) -> VerifiedPhysicalWalObjectStorageBundle:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_BUNDLE_INVALID")
    baseline = bundle.baseline
    if (
        baseline.source_site != _SOURCE_SITE
        or baseline.destination_site != _RECEIVER_SITE
        or baseline.campaign_id != facts.deployment.campaign_id
        or baseline.release_sha != facts.deployment.release_sha
        or baseline.writer_term.epoch != term.writer_epoch
        or baseline.writer_term.lease_id != term.writer_lease_id
        or baseline.writer_term.witnessed_term_proof_sha256 != term.proof_sha256
        or not bundle.manifest_sha256es
        or len(set(bundle.manifest_sha256es)) != len(bundle.manifest_sha256es)
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_BUNDLE_BINDING_MISMATCH")
    return bundle


def _pull(
    value: object,
    *,
    facts: _RuntimeFacts,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    term: _TermFacts,
) -> _PullFacts:
    if type(value) is not PhysicalWaIrPostgresRecoveryPullResult:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_PULL_RESULT_INVALID")
    if (
        value.status != PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED
        or value.reason_codes != ()
        or value.promotion_authorized is not False
        or value.full_matrix_authorized is not False
        or type(value.redacted_receipt) is not PhysicalWaIrPostgresRecoveryPullRedactedReceipt
        or type(value.recovery_preflight_binding) is not PhysicalPostgresRecoveryPreflightBinding
        or value.standby_bootstrap_stage_evidence is None
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_PULL_RESULT_INVALID")
    receipt = value.redacted_receipt
    binding = value.recovery_preflight_binding
    stage = binding.stage_binding
    evidence = value.standby_bootstrap_stage_evidence
    if (
        binding.local_standby_site != _RECEIVER_SITE
        or stage.bundle_id != receipt.bundle_id
        or stage.stage_receipt_sha256 != receipt.stage_receipt_sha256
        or stage.route_binding_sha256 != receipt.route_binding_sha256
        or stage.route_binding_sha256 != facts.deployment.route_binding_sha256
        or getattr(evidence, "stage_receipt_sha256", None) != stage.stage_receipt_sha256
        or binding.expected_witnessed_term != term.term
        or getattr(evidence, "source_candidate", None)
        != facts.bootstrap_root_config.source_staging_candidates_root / stage.bundle_id
        or facts.readback_root_config.stage_bundle_id != stage.bundle_id
        or facts.readback_root_config.stage_receipt_sha256 != stage.stage_receipt_sha256
        or facts.readback_root_config.route_binding_sha256 != stage.route_binding_sha256
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_PULL_BINDING_MISMATCH")
    if (
        stage.bundle_id == ""
        or stage.stage_receipt_sha256 == ""
        or stage.route_binding_sha256 == ""
        or bundle.baseline.writer_term.epoch != term.writer_epoch
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_PULL_BINDING_MISMATCH")
    return _PullFacts(
        binding=binding,
        stage_evidence=evidence,
        bundle_id=_sha256(stage.bundle_id, code="WA_IR_RECOVERY_MATERIALIZATION_PULL_BINDING_MISMATCH"),
        stage_receipt_sha256=_sha256(
            stage.stage_receipt_sha256,
            code="WA_IR_RECOVERY_MATERIALIZATION_PULL_BINDING_MISMATCH",
        ),
        route_binding_sha256=_sha256(
            stage.route_binding_sha256,
            code="WA_IR_RECOVERY_MATERIALIZATION_PULL_BINDING_MISMATCH",
        ),
    )


def _require_replay_evidence(
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pull: _PullFacts,
    evidence: object,
    now: datetime,
    maximum_age: int,
) -> PhysicalPostgresRecoveryPreflightResult:
    result = assess_physical_postgres_recovery_preflight(
        bundle=bundle,
        binding=pull.binding,
        receiver_readback_evidence=evidence,
        now=now,
        maximum_evidence_age_seconds=maximum_age,
    )
    if (
        result.status != PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED
        or result.reason_codes != ()
        or result.stage_bundle_id != pull.bundle_id
        or result.stage_receipt_sha256 != pull.stage_receipt_sha256
        or result.route_binding_sha256 != pull.route_binding_sha256
        or result.evidence_sha256 is None
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_ADMISSION_REPLAY_NOT_OBSERVED")
    return result


def _invocation_hash(payload: Mapping[str, Any]) -> str:
    try:
        return hashlib.sha256(canonical_json_bytes(dict(payload))).hexdigest()
    except (TypeError, ValueError):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_INVOCATION_INVALID")


def _materialization_invocation(
    *,
    rendered: PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs,
    plan: PhysicalPostgresStandbyBootstrapMaterializationPlan,
    term: _TermFacts,
) -> PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation:
    if (
        plan.source_site != term.holder_site
        or plan.writer_epoch != term.writer_epoch
        or plan.writer_lease_id != term.writer_lease_id
        or plan.witnessed_term_proof_sha256 != term.proof_sha256
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_PLAN_TERM_MISMATCH")
    payload = {
        "schema": PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
        "kind": "socket-only-standby-materialization",
        "socket_only_recovery_input_sha256": rendered.input_sha256,
        "bootstrap_id": plan.bootstrap_id,
        "bootstrap_plan_sha256": plan.plan_sha256,
        "source_site": plan.source_site,
        "receiver_site": plan.receiver_site,
        "writer_epoch": plan.writer_epoch,
        "writer_lease_id": plan.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": plan.witnessed_term_proof_sha256,
    }
    return PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation(
        schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
        rendered_inputs=rendered,
        bootstrap_id=plan.bootstrap_id,
        bootstrap_plan_sha256=plan.plan_sha256,
        source_site=plan.source_site,
        receiver_site=plan.receiver_site,
        writer_epoch=plan.writer_epoch,
        writer_lease_id=plan.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        witnessed_term_proof_sha256=plan.witnessed_term_proof_sha256,
        invocation_sha256=_invocation_hash(payload),
    )


def _inspection_invocation(
    *,
    rendered: PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs,
    materialization: PhysicalPostgresStandbyBootstrapMaterializationResult,
    term: _TermFacts,
) -> PhysicalWaIrPostgresSocketOnlyRecoveryInspectionInvocation:
    plan = materialization.plan
    bootstrap_receipt_sha256 = hashlib.sha256(materialization.receipt.raw_receipt).hexdigest()
    payload = {
        "schema": PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
        "kind": "socket-only-standby-recovery-inspection",
        "socket_only_recovery_input_sha256": rendered.input_sha256,
        "bootstrap_id": plan.bootstrap_id,
        "bootstrap_plan_sha256": plan.plan_sha256,
        "bootstrap_receipt_sha256": bootstrap_receipt_sha256,
        "source_site": plan.source_site,
        "receiver_site": plan.receiver_site,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.proof_sha256,
    }
    return PhysicalWaIrPostgresSocketOnlyRecoveryInspectionInvocation(
        schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
        rendered_inputs=rendered,
        bootstrap_id=plan.bootstrap_id,
        bootstrap_plan_sha256=plan.plan_sha256,
        bootstrap_receipt_sha256=bootstrap_receipt_sha256,
        source_site=plan.source_site,
        receiver_site=plan.receiver_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        witnessed_term_proof_sha256=term.proof_sha256,
        invocation_sha256=_invocation_hash(payload),
    )


class _RunnerBackedMaterializer:
    """Adapt the only runner materialization method to the FD-only boundary."""

    def __init__(
        self,
        *,
        runner: object,
        rendered: PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs,
        term: _TermFacts,
    ) -> None:
        self._runner = runner
        self._rendered = rendered
        self._term = term

    def materialize_standby_bootstrap(
        self,
        *,
        plan: PhysicalPostgresStandbyBootstrapMaterializationPlan,
        source_stage_fd: int,
        target_pgdata_fd: int,
        recovery_signal_seed_fd: int,
    ) -> PhysicalPostgresStandbyBootstrapMaterializationAck:
        method = getattr(self._runner, "materialize_socket_only_standby", None)
        if not callable(method):
            _fail("WA_IR_RECOVERY_MATERIALIZATION_RUNNER_INVALID")
        invocation = _materialization_invocation(
            rendered=self._rendered,
            plan=plan,
            term=self._term,
        )
        try:
            return method(
                invocation=invocation,
                plan=plan,
                source_stage_fd=source_stage_fd,
                target_pgdata_fd=target_pgdata_fd,
                recovery_signal_seed_fd=recovery_signal_seed_fd,
            )
        except PhysicalWaIrPostgresRecoveryMaterializationError:
            raise
        except Exception:
            _fail("WA_IR_RECOVERY_MATERIALIZATION_RUNNER_FAILED")


class _RunnerBackedInspector:
    """Expose only one bound recovery inspection to the existing collector."""

    def __init__(
        self,
        *,
        runner: object,
        invocation: PhysicalWaIrPostgresSocketOnlyRecoveryInspectionInvocation,
        target_pgdata_fd: int,
    ) -> None:
        self._runner = runner
        self._invocation = invocation
        self._target_pgdata_fd = target_pgdata_fd

    def inspect_bound_recovery_receiver(
        self,
        *,
        request: PhysicalPostgresRecoveryReadbackInspectionRequest,
    ) -> PhysicalPostgresRecoveryLocalInspection:
        method = getattr(self._runner, "inspect_socket_only_standby", None)
        if not callable(method):
            _fail("WA_IR_RECOVERY_MATERIALIZATION_RUNNER_INVALID")
        try:
            return method(
                invocation=self._invocation,
                target_pgdata_fd=self._target_pgdata_fd,
                request=request,
            )
        except PhysicalWaIrPostgresRecoveryMaterializationError:
            raise
        except Exception:
            _fail("WA_IR_RECOVERY_MATERIALIZATION_RUNNER_FAILED")


def _open_target_fd(
    *,
    root_config: PhysicalPostgresStandbyBootstrapRootConfig,
    materialization: PhysicalPostgresStandbyBootstrapMaterializationResult,
) -> int:
    root = _safe_private_directory(
        root_config.pgdata_candidates_root,
        code="WA_IR_RECOVERY_MATERIALIZATION_TARGET_UNSAFE",
    )
    plan = materialization.plan
    expected = root / plan.bootstrap_id
    target = materialization.target_pgdata_candidate
    if target != expected or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_TARGET_UNSAFE")
    descriptor = -1
    try:
        before = os.lstat(target)
        resolved = target.resolve(strict=True)
        descriptor = os.open(
            target,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if (
            resolved != target
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o700
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_dev != plan.target_pgdata_device
            or opened.st_ino != plan.target_pgdata_inode
        ):
            _fail("WA_IR_RECOVERY_MATERIALIZATION_TARGET_UNSAFE")
        return descriptor
    except PhysicalWaIrPostgresRecoveryMaterializationError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("WA_IR_RECOVERY_MATERIALIZATION_TARGET_UNSAFE")


def _assert_target_stable(
    *,
    root_config: PhysicalPostgresStandbyBootstrapRootConfig,
    materialization: PhysicalPostgresStandbyBootstrapMaterializationResult,
) -> None:
    descriptor = _open_target_fd(root_config=root_config, materialization=materialization)
    try:
        return None
    finally:
        os.close(descriptor)


def _observed_at(evidence: PhysicalPostgresRecoveryReceiverReadbackEvidence) -> datetime:
    try:
        payload = json.loads(evidence.raw_evidence.decode("ascii", "strict"))
        value = payload["observed_at"]
        parsed = datetime.fromisoformat(value)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECOVERY_EVIDENCE_INVALID")
    normalized = _utc(parsed, code="WA_IR_RECOVERY_MATERIALIZATION_RECOVERY_EVIDENCE_INVALID")
    if not isinstance(value, str) or value != normalized.isoformat():
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECOVERY_EVIDENCE_INVALID")
    return normalized


def _secure_receipts_directory(root: Path) -> Path:
    path = root / _RECEIPTS_DIRECTORY
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_WRITE_FAILED")
    return _safe_private_directory(path, code="WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_ROOT_UNSAFE")


def _receipt_mapping(
    *,
    facts: _RuntimeFacts,
    rendered: PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pull: _PullFacts,
    term: _TermFacts,
    materialization: PhysicalPostgresStandbyBootstrapMaterializationResult,
    recovery_evidence: PhysicalPostgresRecoveryReceiverReadbackEvidence,
    observed_at: datetime,
) -> dict[str, Any]:
    bootstrap_receipt_sha256 = hashlib.sha256(materialization.receipt.raw_receipt).hexdigest()
    unsigned = {
        "schema": PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RECEIPT_SCHEMA,
        "status": PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS,
        "campaign_id": facts.deployment.campaign_id,
        "release_sha": facts.deployment.release_sha,
        "sealed_release_descriptor_sha256": facts.sealed_release.descriptor_sha256,
        "deployment_manifest_lock_sha256": rendered.deployment_manifest_lock_sha256,
        "socket_only_recovery_input_sha256": rendered.input_sha256,
        "bundle_id": pull.bundle_id,
        "stage_receipt_sha256": pull.stage_receipt_sha256,
        "route_binding_sha256": pull.route_binding_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.proof_sha256,
        "bootstrap_id": materialization.plan.bootstrap_id,
        "bootstrap_plan_sha256": materialization.plan.plan_sha256,
        "bootstrap_receipt_sha256": bootstrap_receipt_sha256,
        "recovery_evidence_sha256": recovery_evidence.evidence_sha256,
        "observed_at": observed_at.isoformat(),
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "full_matrix_authorized": False,
    }
    return {
        **unsigned,
        "receipt_integrity_sha256": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
    }


def _write_or_verify_receipt(
    *,
    root: Path,
    mapping: Mapping[str, Any],
    recovery_evidence_sha256: str,
) -> PhysicalWaIrPostgresRecoveryMaterializationDurableEvidence:
    evidence_sha = _sha256(
        recovery_evidence_sha256,
        code="WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID",
    )
    try:
        raw = canonical_json_bytes(dict(mapping))
    except (TypeError, ValueError):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    directory = _secure_receipts_directory(root)
    path = directory / (evidence_sha + ".json")
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        created = True
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    except FileExistsError:
        pass
    except PhysicalWaIrPostgresRecoveryMaterializationError:
        raise
    except OSError:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if created:
        try:
            directory_fd = os.open(
                directory,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_WRITE_FAILED")
    read_fd = -1
    try:
        before = os.lstat(path)
        read_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(read_fd)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_dev != before.st_dev
            or metadata.st_ino != before.st_ino
            or metadata.st_size != len(raw)
        ):
            _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
        stored = bytearray()
        while len(stored) < metadata.st_size:
            chunk = os.read(read_fd, metadata.st_size - len(stored))
            if not chunk:
                _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
            stored.extend(chunk)
        if os.read(read_fd, 1) or bytes(stored) != raw:
            _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_REPLAY_CONFLICT")
    except PhysicalWaIrPostgresRecoveryMaterializationError:
        raise
    except OSError:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    finally:
        if read_fd >= 0:
            os.close(read_fd)
    try:
        parsed = json.loads(raw.decode("ascii", "strict"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    if (
        type(parsed) is not dict
        or set(parsed) != _RECEIPT_FIELDS
        or canonical_json_bytes(parsed) != raw
        or parsed.get("schema") != PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RECEIPT_SCHEMA
        or parsed.get("status") != PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS
        or parsed.get("recovery_evidence_sha256") != evidence_sha
        or parsed.get("promotion_authorized") is not False
        or parsed.get("writer_authorized") is not False
        or parsed.get("traffic_switch_authorized") is not False
        or parsed.get("full_matrix_authorized") is not False
    ):
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    integrity = _sha256(
        parsed.get("receipt_integrity_sha256"),
        code="WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID",
    )
    unsigned = {key: value for key, value in parsed.items() if key != "receipt_integrity_sha256"}
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != integrity:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    observed = _receipt_observed_at(parsed["observed_at"])
    return PhysicalWaIrPostgresRecoveryMaterializationDurableEvidence(
        raw_receipt=raw,
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
        recovery_evidence_sha256=evidence_sha,
        bootstrap_plan_sha256=_sha256(
            parsed.get("bootstrap_plan_sha256"),
            code="WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID",
        ),
        bootstrap_receipt_sha256=_sha256(
            parsed.get("bootstrap_receipt_sha256"),
            code="WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID",
        ),
        observed_at=observed,
    )


def _receipt_observed_at(value: object) -> datetime:
    if type(value) is not str:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    normalized = _utc(parsed, code="WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    if value != normalized.isoformat():
        _fail("WA_IR_RECOVERY_MATERIALIZATION_RECEIPT_INVALID")
    return normalized


class RootOwnedWaIrPostgresRecoveryMaterializationRuntime:
    """Inert construction plus one default-off phase-3 recovery operation."""

    def __init__(
        self,
        config: RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig,
        *,
        clock: Callable[[], datetime] | None,
    ) -> None:
        self._config = validate_root_owned_wa_ir_postgres_recovery_materialization_runtime_config(config)
        self._clock = clock

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("WA_IR_RECOVERY_MATERIALIZATION_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="WA_IR_RECOVERY_MATERIALIZATION_CLOCK_INVALID")
        except PhysicalWaIrPostgresRecoveryMaterializationError:
            raise
        except Exception:
            _fail("WA_IR_RECOVERY_MATERIALIZATION_CLOCK_INVALID")

    def run(
        self,
        *,
        bundle: object,
        pull_result: object,
        current_witnessed_term: object,
        admission_recovery_readback_evidence: object,
        runner: object,
    ) -> PhysicalWaIrPostgresRecoveryMaterializationResult:
        """Materialize and freshly verify one exact WA-IR recovery candidate.

        The initial admission evidence is deliberately required because the
        existing FD-only bootstrap boundary refuses to call a materializer
        without it.  The returned evidence is fresh post-run collector output,
        not a relabelled copy of that input.
        """

        target_fd = -1
        try:
            _require_root()
            started = self._now()
            facts = _runtime_facts(self._config, now=started, require_enabled=True)
            term = _term(current_witnessed_term, now=started)
            verified_bundle = _bundle(bundle, facts=facts, term=term)
            pull = _pull(pull_result, facts=facts, bundle=verified_bundle, term=term)
            _require_replay_evidence(
                bundle=verified_bundle,
                pull=pull,
                evidence=admission_recovery_readback_evidence,
                now=started,
                maximum_age=facts.maximum_recovery_evidence_age_seconds,
            )
            rendered = render_wa_ir_postgres_socket_only_recovery_inputs(
                facts.deployment,
                sealed_release=facts.sealed_release,
            )
            materialization = materialize_physical_postgres_standby_bootstrap(
                root_config=facts.bootstrap_root_config,
                bundle=verified_bundle,
                binding=pull.binding,
                current_witnessed_term=term.term,
                recovery_readback_evidence=admission_recovery_readback_evidence,
                stage_evidence=pull.stage_evidence,
                materializer=_RunnerBackedMaterializer(
                    runner=runner,
                    rendered=rendered,
                    term=term,
                ),
                now=started,
            )
            completed = self._now()
            if completed < started:
                _fail("WA_IR_RECOVERY_MATERIALIZATION_CLOCK_INVALID")
            # Revalidate every mutable/fresh admission fact before the second
            # runner surface (the read-only local socket inspection) exists.
            completed_facts = _runtime_facts(self._config, now=completed, require_enabled=True)
            completed_term = _term(current_witnessed_term, now=completed)
            if not _same_term(term, completed_term):
                _fail("WA_IR_RECOVERY_MATERIALIZATION_TERM_CHANGED")
            completed_bundle = _bundle(bundle, facts=completed_facts, term=completed_term)
            completed_pull = _pull(
                pull_result,
                facts=completed_facts,
                bundle=completed_bundle,
                term=completed_term,
            )
            if (
                completed_bundle != verified_bundle
                or completed_pull != pull
                or completed_facts.deployment != facts.deployment
                or completed_facts.sealed_release.descriptor_sha256
                != facts.sealed_release.descriptor_sha256
            ):
                _fail("WA_IR_RECOVERY_MATERIALIZATION_BINDING_CHANGED")
            _require_replay_evidence(
                bundle=completed_bundle,
                pull=completed_pull,
                evidence=admission_recovery_readback_evidence,
                now=completed,
                maximum_age=completed_facts.maximum_recovery_evidence_age_seconds,
            )
            target_fd = _open_target_fd(
                root_config=completed_facts.bootstrap_root_config,
                materialization=materialization,
            )
            inspection_invocation = _inspection_invocation(
                rendered=rendered,
                materialization=materialization,
                term=completed_term,
            )
            recovery_evidence = collect_physical_postgres_recovery_receiver_readback(
                root_config=completed_facts.readback_root_config,
                bundle=completed_bundle,
                binding=completed_pull.binding,
                current_witnessed_term=completed_term.term,
                inspector=_RunnerBackedInspector(
                    runner=runner,
                    invocation=inspection_invocation,
                    target_pgdata_fd=target_fd,
                ),
                now=completed,
            )
            observed_at = _observed_at(recovery_evidence)
            final_now = self._now()
            if final_now < completed:
                _fail("WA_IR_RECOVERY_MATERIALIZATION_CLOCK_INVALID")
            final_facts = _runtime_facts(self._config, now=final_now, require_enabled=True)
            final_term = _term(current_witnessed_term, now=final_now)
            if not _same_term(term, final_term):
                _fail("WA_IR_RECOVERY_MATERIALIZATION_TERM_CHANGED")
            final_bundle = _bundle(bundle, facts=final_facts, term=final_term)
            final_pull = _pull(
                pull_result,
                facts=final_facts,
                bundle=final_bundle,
                term=final_term,
            )
            if final_bundle != verified_bundle or final_pull != pull:
                _fail("WA_IR_RECOVERY_MATERIALIZATION_BINDING_CHANGED")
            _assert_target_stable(
                root_config=final_facts.bootstrap_root_config,
                materialization=materialization,
            )
            recovery_result = _require_replay_evidence(
                bundle=final_bundle,
                pull=final_pull,
                evidence=recovery_evidence,
                now=final_now,
                maximum_age=final_facts.maximum_recovery_evidence_age_seconds,
            )
            mapping = _receipt_mapping(
                facts=final_facts,
                rendered=rendered,
                bundle=final_bundle,
                pull=final_pull,
                term=final_term,
                materialization=materialization,
                recovery_evidence=recovery_evidence,
                observed_at=observed_at,
            )
            durable = _write_or_verify_receipt(
                root=final_facts.redacted_receipt_root,
                mapping=mapping,
                recovery_evidence_sha256=recovery_evidence.evidence_sha256,
            )
            return PhysicalWaIrPostgresRecoveryMaterializationResult(
                schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS,
                reason_codes=(),
                rendered_inputs=rendered,
                materialization=materialization,
                recovery_evidence=recovery_evidence,
                recovery_result=recovery_result,
                durable_evidence=durable,
                idempotent=materialization.idempotent,
                promotion_authorized=False,
                writer_authorized=False,
                traffic_switch_authorized=False,
                full_matrix_authorized=False,
            )
        except PhysicalWaIrPostgresRecoveryMaterializationError as exc:
            return PhysicalWaIrPostgresRecoveryMaterializationResult(
                schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS_BLOCKED,
                reason_codes=(exc.code,),
            )
        except PhysicalPostgresStandbyBootstrapMaterializationError:
            return PhysicalWaIrPostgresRecoveryMaterializationResult(
                schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS_BLOCKED,
                reason_codes=("WA_IR_RECOVERY_MATERIALIZATION_BOOTSTRAP_REJECTED",),
            )
        except Exception:
            return PhysicalWaIrPostgresRecoveryMaterializationResult(
                schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_STATUS_BLOCKED,
                reason_codes=("WA_IR_RECOVERY_MATERIALIZATION_UNEXPECTED_FAILURE",),
            )
        finally:
            if target_fd >= 0:
                try:
                    os.close(target_fd)
                except OSError:
                    pass


def run_root_owned_wa_ir_postgres_recovery_materialization(
    *,
    config: RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig,
    bundle: object,
    pull_result: object,
    current_witnessed_term: object,
    admission_recovery_readback_evidence: object,
    runner: object,
    now: datetime,
) -> PhysicalWaIrPostgresRecoveryMaterializationResult:
    """One-shot convenience wrapper over the inert root-only runtime."""

    runtime = RootOwnedWaIrPostgresRecoveryMaterializationRuntime(config, clock=lambda: now)
    return runtime.run(
        bundle=bundle,
        pull_result=pull_result,
        current_witnessed_term=current_witnessed_term,
        admission_recovery_readback_evidence=admission_recovery_readback_evidence,
        runner=runner,
    )
