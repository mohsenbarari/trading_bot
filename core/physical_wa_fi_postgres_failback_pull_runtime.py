"""Root-only FI receiver pull runtime for the separate IR-to-FI failback route.

This module is deliberately independent from the normal WA-IR recovery pull
runtime.  A rebuilding WA-FI standby is allowed to obtain encrypted physical
recovery material only through a fresh four-identity IR-to-FI preflight and a
dedicated FI-receiver factory.  The normal FI-publisher/IR-receiver
credentials, factories, and private helpers are neither imported nor
accepted.

The factory owns the provider endpoint, bucket, region, and FI-receiver
exact-GET capability.  Those facts are exposed solely inside one synchronous
callback after the runtime has pinned the release, live IR Witness term,
immutable object versions, and the ``physical-failback`` namespace.  This
runtime never lists objects, follows aliases, sends direct site control,
starts PostgreSQL, promotes a writer, or authorizes Full Matrix.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Protocol

from core.append_only_sync_delta_batch import (
    OBJECT_KEY_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_age_v1_adapter import (
    DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES,
    DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES,
    MAX_PHYSICAL_AGE_IDENTITY_BYTES,
    PhysicalAgeV1DecryptorConfig,
    PhysicalAgeV1FdDecryptor,
)
from core.physical_arvan_exact_version_pull import (
    ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES,
    ArvanExactVersionPullError,
    ArvanExactVersionPullExpectation,
    ArvanExactVersionPullReader,
    RootOwnedArvanExactVersionPullConfig,
    validate_arvan_exact_version_pull_config,
)
from core.physical_ir_to_fi_object_storage_failback_preflight import (
    PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
    require_verified_physical_ir_to_fi_object_storage_failback_preflight,
)
from core.physical_postgres_recovery_preflight import (
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryStageBinding,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
)
from core.physical_wal_receiver_staging import (
    MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES,
    PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS,
    PHYSICAL_WAL_RECEIVER_STAGING_SCHEMA,
    PHYSICAL_WAL_RECEIVER_STAGING_STATUS,
    PhysicalWalDecryptor,
    PhysicalWalReceiverStagingConfig,
    PhysicalWalReceiverStagingPin,
    PhysicalWalReceiverStagingResult,
    derive_physical_wal_receiver_staging_route_binding_sha256,
    stage_physical_wal_object_storage_bundle,
)


__all__ = (
    "DEFAULT_WA_FI_POSTGRES_FAILBACK_PULL_LOCATOR_AGE_SECONDS",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_DEFAULT_ENABLED",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_LOCATOR_SCHEMA",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RECEIPT_SCHEMA",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_BLOCKED",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED",
    "PhysicalWaFiFailbackExactVersionGetClient",
    "PhysicalWaFiFailbackExactVersionReceiverAdmission",
    "PhysicalWaFiFailbackExactVersionReceiverFactory",
    "PhysicalWaFiFailbackExactVersionReceiverRoute",
    "PhysicalWaFiPostgresFailbackExactObjectLocator",
    "PhysicalWaFiPostgresFailbackPullRedactedReceipt",
    "PhysicalWaFiPostgresFailbackPullResult",
    "PhysicalWaFiPostgresFailbackPullRuntimeError",
    "PhysicalWaFiPostgresFailbackStageEvidence",
    "RootOwnedWaFiPostgresFailbackPullRuntime",
    "RootOwnedWaFiPostgresFailbackPullRuntimeConfig",
    "build_physical_wa_fi_failback_exact_version_receiver_admission",
    "canonical_wa_fi_postgres_failback_exact_object_locator_bytes",
    "derive_wa_fi_postgres_failback_exact_object_locator_sha256",
    "require_physical_wa_fi_failback_exact_version_receiver_admission",
    "stage_root_owned_wa_fi_postgres_failback_bundle",
    "validate_root_owned_wa_fi_postgres_failback_pull_runtime_config",
)


PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-failback-pull-runtime-v1"
)
PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_LOCATOR_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-failback-exact-object-locator-v1"
)
PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RECEIPT_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-failback-pull-redacted-receipt-v1"
)
PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_DEFAULT_ENABLED = False
PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED = "staged-not-replay-verified"
PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_BLOCKED = "blocked"

DEFAULT_WA_FI_POSTGRES_FAILBACK_PULL_LOCATOR_AGE_SECONDS = 180
_MAX_LOCATOR_AGE_SECONDS = 300
_MAX_ADMISSION_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_SOURCE_SITE = "webapp_ir"
_RECEIVER_SITE = "webapp_fi"
_RUNTIME_MODE = "root-owned-wa-fi-failback-exact-version-age-v1-recovery-pull-v1"
_RECEIPTS_DIRECTORY = "receipts"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MUTABLE_SELECTOR_ALIASES = frozenset(
    {"alias", "current", "head", "latest", "pointer", "null", "none", "undefined"}
)
_FAILBACK_RECEIVER_ADMISSION_CAPABILITY = object()
_TOMBSTONED_PAIRED_FACTORY_MODULE = "core.physical_arvan_s3_failback_separated_client_factory"
_TOMBSTONED_PAIRED_FACTORY_CLASS = "RootOwnedArvanS3FailbackSeparatedClientFactory"
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bundle_id",
        "stage_receipt_sha256",
        "route_binding_sha256",
        "manifest_sha256es",
        "writer_epoch",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "preflight_evidence_sha256",
        "recovery_preflight_ready",
        "failback_materialization_authorized",
        "promotion_authorized",
        "full_matrix_authorized",
        "receipt_sha256",
    }
)
_STAGE_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bundle_id",
        "route_binding_sha256",
        "candidate_path",
        "manifest_sha256es",
        "object_versions",
        "artifacts",
        "receipt_sha256",
    }
)


class PhysicalWaFiPostgresFailbackPullRuntimeError(ValueError):
    """A stable, redacted refusal from the FI reverse receiver."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaFiFailbackExactVersionReceiverRoute:
    """Factory-owned provider route released only inside one callback.

    It is not accepted in runtime configuration and contains no credential,
    object selector, or normal-direction capability.  A concrete four-role
    factory must construct it only after admitting its FI-receiver scope.
    """

    endpoint: str
    region: str
    bucket: str
    object_storage_namespace: str = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE


class PhysicalWaFiFailbackExactVersionGetClient(Protocol):
    """The only exact-version provider reads exposed to the reverse receiver.

    A receiver authenticates metadata with ``HeadObject`` before it pulls
    ciphertext.  The concrete factory still exposes neither listing nor any
    mutation, and both calls require a fixed ``Key + VersionId``.
    """

    def head_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]: ...

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class PhysicalWaFiFailbackExactVersionReceiverAdmission:
    """Opaque FI-receiver-only capability bound to one IR writer term.

    It contains no endpoint, bucket, version selector, credential, or client;
    serializing it is forbidden.  The two identity digests are public pins,
    not secret material, and prove that the reverse roles remain separate.
    """

    campaign_id: str
    release_sha: str
    route_binding_sha256: str
    object_storage_namespace: str
    ir_publisher_identity_sha256: str
    fi_receiver_identity_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    admitted_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("WA_FI_FAILBACK_RECEIVER_ADMISSION_SERIALIZATION_FORBIDDEN")


class PhysicalWaFiFailbackExactVersionReceiverFactory(Protocol):
    """Distinct FI receiver seam; normal FI/IR factories cannot satisfy it."""

    def admit_fi_receiver_failback_exact_pull(
        self,
        *,
        preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> PhysicalWaFiFailbackExactVersionReceiverAdmission:
        """Mint one FI-receiver-only opaque admission."""

    def require_fi_receiver_failback_exact_pull_admission(
        self,
        admission: PhysicalWaFiFailbackExactVersionReceiverAdmission,
        *,
        preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> PhysicalWaFiFailbackExactVersionReceiverAdmission:
        """Recheck the capability without releasing a provider client."""

    def execute_fi_receiver_failback_exact_pull(
        self,
        *,
        admission: PhysicalWaFiFailbackExactVersionReceiverAdmission,
        now: datetime,
        operation: Callable[
            [PhysicalWaFiFailbackExactVersionGetClient, PhysicalWaFiFailbackExactVersionReceiverRoute],
            object,
        ],
    ) -> object:
        """Run one callback using only the FI-receiver exact-GET capability."""


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackExactObjectLocator:
    """Fresh root-pinned public metadata for one IR-to-FI object bundle.

    Every object must be an immutable ``Key + VersionId`` under the dedicated
    failback namespace.  It deliberately has no provider route, URL,
    credential, or mutable selector.
    """

    schema: str = PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_LOCATOR_SCHEMA
    issued_at: datetime | None = None
    source_site: str = _SOURCE_SITE
    destination_site: str = _RECEIVER_SITE
    object_storage_namespace: str = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    campaign_id: str = ""
    release_sha: str = ""
    route_binding_sha256: str = ""
    manifest_sha256es: tuple[str, ...] = ()
    object_expectations: tuple[ArvanExactVersionPullExpectation, ...] = ()


@dataclass(frozen=True)
class RootOwnedWaFiPostgresFailbackPullRuntimeConfig:
    """Default-off FI receiver policy with no endpoint/bucket/credential field."""

    schema: str = PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA
    receiver_factory: PhysicalWaFiFailbackExactVersionReceiverFactory | None = field(
        default=None, repr=False, compare=False
    )
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig | None = field(
        default=None, repr=False, compare=False
    )
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight | None = field(
        default=None, repr=False, compare=False
    )
    age_decryptor_config: PhysicalAgeV1DecryptorConfig | None = field(
        default=None, repr=False, compare=False
    )
    receiver_staging_config: PhysicalWalReceiverStagingConfig | None = field(
        default=None, repr=False, compare=False
    )
    redacted_receipt_root: Path | None = field(default=None, repr=False, compare=False)
    expected_locator_sha256: str = ""
    maximum_locator_age_seconds: int = DEFAULT_WA_FI_POSTGRES_FAILBACK_PULL_LOCATOR_AGE_SECONDS
    maximum_ciphertext_bytes: int = ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES
    enabled: bool = PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    receiver_site: str = _RECEIVER_SITE
    object_storage_namespace: str = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    runtime_mode: str = _RUNTIME_MODE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackPullRedactedReceipt:
    """Portable non-secret local staging proof; it never conveys authority."""

    raw_receipt: bytes
    receipt_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackStageEvidence:
    """FI-local input for a later dedicated failback materializer only."""

    source_candidate: Path
    raw_stage_receipt: bytes
    stage_receipt_sha256: str


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackPullResult:
    """Typed FI staging outcome; no replay/promotion/full-matrix authority."""

    schema: str
    status: str
    reason_codes: tuple[str, ...]
    redacted_receipt: PhysicalWaFiPostgresFailbackPullRedactedReceipt | None = None
    recovery_preflight_binding: PhysicalPostgresRecoveryPreflightBinding | None = None
    failback_stage_evidence: PhysicalWaFiPostgresFailbackStageEvidence | None = None
    idempotent: bool = False
    promotion_authorized: bool = False
    full_matrix_authorized: bool = False

    @property
    def staged(self) -> bool:
        return self.status == PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED


@dataclass(frozen=True)
class _RuntimeFacts:
    receiver_factory: PhysicalWaFiFailbackExactVersionReceiverFactory
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight
    age_config: PhysicalAgeV1DecryptorConfig
    staging_config: PhysicalWalReceiverStagingConfig
    redacted_receipt_root: Path
    expected_locator_sha256: str
    maximum_locator_age_seconds: int
    maximum_ciphertext_bytes: int


@dataclass(frozen=True)
class _LocatorFacts:
    canonical_locator: bytes
    locator_sha256: str
    expectations: tuple[ArvanExactVersionPullExpectation, ...]


AgeDecryptorFactory = Callable[[PhysicalAgeV1DecryptorConfig], PhysicalWalDecryptor]


def _fail(code: str) -> None:
    raise PhysicalWaFiPostgresFailbackPullRuntimeError(code)


def _is_tombstoned_paired_reverse_factory(value: object) -> bool:
    """Reject the old dual-role factory without importing it in this runtime."""

    try:
        return any(
            base.__module__ == _TOMBSTONED_PAIRED_FACTORY_MODULE
            and base.__name__ == _TOMBSTONED_PAIRED_FACTORY_CLASS
            for base in type(value).__mro__
        )
    except Exception:
        return True


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("WA_FI_FAILBACK_PULL_ROOT_REQUIRED")
    except OSError:
        _fail("WA_FI_FAILBACK_PULL_ROOT_REQUIRED")


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


def _safe_private_file(value: object, *, code: str, maximum_bytes: int) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    descriptor = -1
    try:
        before = os.lstat(value)
        resolved = value.resolve(strict=True)
        descriptor = os.open(value, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(descriptor)
        if (
            resolved != value
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
            or metadata.st_dev != before.st_dev
            or metadata.st_ino != before.st_ino
            or not 1 <= metadata.st_size <= maximum_bytes
        ):
            _fail(code)
        _safe_private_directory(value.parent, code=code)
        return resolved
    except PhysicalWaFiPostgresFailbackPullRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _receiver_factory(value: object) -> PhysicalWaFiFailbackExactVersionReceiverFactory:
    if _is_tombstoned_paired_reverse_factory(value):
        _fail("WA_FI_FAILBACK_PULL_LEGACY_PAIRED_FACTORY_FORBIDDEN")
    if value is None or not all(
        callable(getattr(value, name, None))
        for name in (
            "admit_fi_receiver_failback_exact_pull",
            "require_fi_receiver_failback_exact_pull_admission",
            "execute_fi_receiver_failback_exact_pull",
        )
    ):
        _fail("WA_FI_FAILBACK_PULL_RECEIVER_FACTORY_INVALID")
    return value


def _config_facts(
    value: object,
    *,
    now: datetime,
    require_enabled: bool,
) -> _RuntimeFacts:
    if type(value) is not RootOwnedWaFiPostgresFailbackPullRuntimeConfig:
        _fail("WA_FI_FAILBACK_PULL_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA
        or type(value.enabled) is not bool
        or value.source_site != _SOURCE_SITE
        or value.receiver_site != _RECEIVER_SITE
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.runtime_mode != _RUNTIME_MODE
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
    ):
        _fail("WA_FI_FAILBACK_PULL_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("WA_FI_FAILBACK_PULL_DISABLED")
    if type(value.preflight_config) is not PhysicalIrToFiObjectStorageFailbackPreflightConfig:
        _fail("WA_FI_FAILBACK_PULL_PREFLIGHT_CONFIG_INVALID")
    if type(value.preflight) is not VerifiedPhysicalIrToFiObjectStorageFailbackPreflight:
        _fail("WA_FI_FAILBACK_PULL_PREFLIGHT_INVALID")
    try:
        preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            value.preflight,
            config=value.preflight_config,
            now=now,
        )
    except Exception:
        _fail("WA_FI_FAILBACK_PULL_PREFLIGHT_INVALID_OR_STALE")
    if (
        preflight.binding.source_site != _SOURCE_SITE
        or preflight.binding.destination_site != _RECEIVER_SITE
        or preflight.binding.object_storage_namespace
        != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    ):
        _fail("WA_FI_FAILBACK_PULL_PREFLIGHT_ROUTE_MISMATCH")
    if type(value.age_decryptor_config) is not PhysicalAgeV1DecryptorConfig:
        _fail("WA_FI_FAILBACK_PULL_AGE_CONFIG_INVALID")
    age_config = value.age_decryptor_config
    if (
        age_config.enabled is not True
        or age_config.direct_site_control != "forbidden"
        or age_config.destination_object_ingest != "pull-only"
        or not isinstance(age_config.recipient, str)
        or AGE_RECIPIENT_RE.fullmatch(age_config.recipient) is None
        or type(age_config.maximum_plaintext_bytes) is not int
        or type(age_config.maximum_ciphertext_bytes) is not int
        or not 1 <= age_config.maximum_plaintext_bytes <= DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES
        or not age_config.maximum_plaintext_bytes <= age_config.maximum_ciphertext_bytes
        or age_config.maximum_ciphertext_bytes > DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES
    ):
        _fail("WA_FI_FAILBACK_PULL_AGE_CONFIG_INVALID")
    _safe_private_directory(age_config.workspace_root, code="WA_FI_FAILBACK_PULL_AGE_CONFIG_INVALID")
    _safe_private_file(
        age_config.identity_path,
        code="WA_FI_FAILBACK_PULL_AGE_CONFIG_INVALID",
        maximum_bytes=MAX_PHYSICAL_AGE_IDENTITY_BYTES,
    )
    maximum_ciphertext = _positive(
        value.maximum_ciphertext_bytes,
        maximum=ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES,
        code="WA_FI_FAILBACK_PULL_MAXIMUM_CIPHERTEXT_INVALID",
    )
    if maximum_ciphertext > age_config.maximum_ciphertext_bytes:
        _fail("WA_FI_FAILBACK_PULL_AGE_CONFIG_INVALID")
    if type(value.receiver_staging_config) is not PhysicalWalReceiverStagingConfig:
        _fail("WA_FI_FAILBACK_PULL_CONFIG_INVALID")
    try:
        staging_receiver = _safe_private_directory(
            value.receiver_staging_config.receiver_root,
            code="WA_FI_FAILBACK_PULL_STAGING_ROOT_UNSAFE",
        )
        staging_state = _safe_private_directory(
            value.receiver_staging_config.state_root,
            code="WA_FI_FAILBACK_PULL_STAGING_ROOT_UNSAFE",
        )
    except AttributeError:
        _fail("WA_FI_FAILBACK_PULL_CONFIG_INVALID")
    receipt_root = _safe_private_directory(
        value.redacted_receipt_root,
        code="WA_FI_FAILBACK_PULL_RECEIPT_ROOT_UNSAFE",
    )
    if (
        receipt_root == staging_receiver
        or receipt_root == staging_state
        or receipt_root.is_relative_to(staging_receiver)
        or receipt_root.is_relative_to(staging_state)
        or staging_receiver.is_relative_to(receipt_root)
        or staging_state.is_relative_to(receipt_root)
    ):
        _fail("WA_FI_FAILBACK_PULL_RECEIPT_ROOT_OVERLAPS_STAGING")
    return _RuntimeFacts(
        receiver_factory=_receiver_factory(value.receiver_factory),
        preflight_config=value.preflight_config,
        preflight=preflight,
        age_config=age_config,
        staging_config=PhysicalWalReceiverStagingConfig(
            receiver_root=staging_receiver,
            state_root=staging_state,
        ),
        redacted_receipt_root=receipt_root,
        expected_locator_sha256=_sha256(
            value.expected_locator_sha256,
            code="WA_FI_FAILBACK_PULL_LOCATOR_PIN_INVALID",
        ),
        maximum_locator_age_seconds=_positive(
            value.maximum_locator_age_seconds,
            maximum=_MAX_LOCATOR_AGE_SECONDS,
            code="WA_FI_FAILBACK_PULL_LOCATOR_AGE_INVALID",
        ),
        maximum_ciphertext_bytes=maximum_ciphertext,
    )


def validate_root_owned_wa_fi_postgres_failback_pull_runtime_config(
    config: RootOwnedWaFiPostgresFailbackPullRuntimeConfig,
) -> RootOwnedWaFiPostgresFailbackPullRuntimeConfig:
    """Perform only inert shape validation; construction opens no secret or client."""

    if type(config) is not RootOwnedWaFiPostgresFailbackPullRuntimeConfig:
        _fail("WA_FI_FAILBACK_PULL_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA
        or type(config.enabled) is not bool
        or config.source_site != _SOURCE_SITE
        or config.receiver_site != _RECEIVER_SITE
        or config.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or config.runtime_mode != _RUNTIME_MODE
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
        or type(config.preflight_config) is not PhysicalIrToFiObjectStorageFailbackPreflightConfig
        or type(config.preflight) is not VerifiedPhysicalIrToFiObjectStorageFailbackPreflight
        or type(config.age_decryptor_config) is not PhysicalAgeV1DecryptorConfig
        or type(config.receiver_staging_config) is not PhysicalWalReceiverStagingConfig
        or not isinstance(config.redacted_receipt_root, Path)
    ):
        _fail("WA_FI_FAILBACK_PULL_CONFIG_INVALID")
    _receiver_factory(config.receiver_factory)
    _sha256(config.expected_locator_sha256, code="WA_FI_FAILBACK_PULL_LOCATOR_PIN_INVALID")
    _positive(
        config.maximum_locator_age_seconds,
        maximum=_MAX_LOCATOR_AGE_SECONDS,
        code="WA_FI_FAILBACK_PULL_LOCATOR_AGE_INVALID",
    )
    _positive(
        config.maximum_ciphertext_bytes,
        maximum=ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES,
        code="WA_FI_FAILBACK_PULL_MAXIMUM_CIPHERTEXT_INVALID",
    )
    return config


def build_physical_wa_fi_failback_exact_version_receiver_admission(
    *,
    preflight: object,
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    current_witnessed_term: object,
    now: datetime,
) -> PhysicalWaFiFailbackExactVersionReceiverAdmission:
    """Mint a local opaque FI-receiver admission after fresh reverse checks."""

    observed = _utc(now, code="WA_FI_FAILBACK_PULL_CLOCK_INVALID")
    try:
        checked_preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            preflight,
            config=preflight_config,
            now=observed,
        )
    except Exception:
        _fail("WA_FI_FAILBACK_PULL_PREFLIGHT_INVALID_OR_STALE")
    checked_term = _term(current_witnessed_term, now=observed)
    result = PhysicalWaFiFailbackExactVersionReceiverAdmission(
        campaign_id=checked_preflight.binding.campaign_id,
        release_sha=checked_preflight.binding.release_sha,
        route_binding_sha256=checked_preflight.binding.route_binding_sha256,
        object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        ir_publisher_identity_sha256=checked_preflight.binding.ir_publisher_identity_sha256,
        fi_receiver_identity_sha256=checked_preflight.binding.fi_receiver_identity_sha256,
        writer_epoch=checked_term.writer_epoch,
        writer_lease_id=checked_term.writer_lease_id,
        witness_transition_id=checked_term.witness_transition_id,
        witnessed_term_proof_sha256=checked_term.proof_sha256,
        admitted_at=observed,
    )
    object.__setattr__(result, "_capability", _FAILBACK_RECEIVER_ADMISSION_CAPABILITY)
    return result


def require_physical_wa_fi_failback_exact_version_receiver_admission(
    value: object,
    *,
    preflight: object,
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    current_witnessed_term: object,
    now: datetime,
) -> PhysicalWaFiFailbackExactVersionReceiverAdmission:
    """Require one current FI-only opaque admission without a provider call."""

    observed = _utc(now, code="WA_FI_FAILBACK_PULL_CLOCK_INVALID")
    if type(value) is not PhysicalWaFiFailbackExactVersionReceiverAdmission:
        _fail("WA_FI_FAILBACK_PULL_RECEIVER_ADMISSION_FAILED")
    admitted = _utc(value.admitted_at, code="WA_FI_FAILBACK_PULL_RECEIVER_ADMISSION_FAILED")
    if (
        value._capability is not _FAILBACK_RECEIVER_ADMISSION_CAPABILITY
        or admitted > observed + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or admitted < observed - timedelta(seconds=_MAX_ADMISSION_AGE_SECONDS)
    ):
        _fail("WA_FI_FAILBACK_PULL_RECEIVER_ADMISSION_FAILED")
    try:
        checked_preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            preflight,
            config=preflight_config,
            now=observed,
        )
    except Exception:
        _fail("WA_FI_FAILBACK_PULL_PREFLIGHT_INVALID_OR_STALE")
    checked_term = _term(current_witnessed_term, now=observed)
    if (
        value.campaign_id != checked_preflight.binding.campaign_id
        or value.release_sha != checked_preflight.binding.release_sha
        or value.route_binding_sha256 != checked_preflight.binding.route_binding_sha256
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.ir_publisher_identity_sha256
        != checked_preflight.binding.ir_publisher_identity_sha256
        or value.fi_receiver_identity_sha256 != checked_preflight.binding.fi_receiver_identity_sha256
        or value.writer_epoch != checked_term.writer_epoch
        or value.writer_lease_id != checked_term.writer_lease_id
        or value.witness_transition_id != checked_term.witness_transition_id
        or value.witnessed_term_proof_sha256 != checked_term.proof_sha256
    ):
        _fail("WA_FI_FAILBACK_PULL_RECEIVER_ADMISSION_FAILED")
    return value


def _term(value: object, *, now: datetime) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    try:
        result = require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("WA_FI_FAILBACK_PULL_WITNESS_TERM_INVALID_OR_STALE")
    if result.holder_site != _SOURCE_SITE:
        _fail("WA_FI_FAILBACK_PULL_WITNESS_TERM_ROUTE_INVALID")
    return result


def _canonical_locator_mapping(
    locator: PhysicalWaFiPostgresFailbackExactObjectLocator,
) -> dict[str, Any]:
    issued_at = _utc(locator.issued_at, code="WA_FI_FAILBACK_PULL_LOCATOR_INVALID")
    if type(locator.object_expectations) is not tuple or type(locator.manifest_sha256es) is not tuple:
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_INVALID")
    objects: list[dict[str, Any]] = []
    for expectation in locator.object_expectations:
        if type(expectation) is not ArvanExactVersionPullExpectation or type(expectation.metadata) is not dict:
            _fail("WA_FI_FAILBACK_PULL_LOCATOR_INVALID")
        objects.append(
            {
                "object_key": expectation.object_key,
                "version_id": expectation.version_id,
                "ciphertext_sha256": expectation.ciphertext_sha256,
                "ciphertext_bytes": expectation.ciphertext_bytes,
                "metadata": dict(expectation.metadata),
            }
        )
    return {
        "schema": locator.schema,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "source_site": locator.source_site,
        "destination_site": locator.destination_site,
        "object_storage_namespace": locator.object_storage_namespace,
        "campaign_id": locator.campaign_id,
        "release_sha": locator.release_sha,
        "route_binding_sha256": locator.route_binding_sha256,
        "manifest_sha256es": list(locator.manifest_sha256es),
        "object_expectations": objects,
    }


def canonical_wa_fi_postgres_failback_exact_object_locator_bytes(
    locator: PhysicalWaFiPostgresFailbackExactObjectLocator,
) -> bytes:
    """Return canonical public locator bytes for a root policy pin."""

    if type(locator) is not PhysicalWaFiPostgresFailbackExactObjectLocator:
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_INVALID")
    if locator.schema != PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_LOCATOR_SCHEMA:
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_INVALID")
    try:
        return canonical_json_bytes(_canonical_locator_mapping(locator))
    except (TypeError, ValueError):
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_INVALID")


def derive_wa_fi_postgres_failback_exact_object_locator_sha256(
    locator: PhysicalWaFiPostgresFailbackExactObjectLocator,
) -> str:
    """Return the exact root-pinnable digest for one reverse locator."""

    return hashlib.sha256(
        canonical_wa_fi_postgres_failback_exact_object_locator_bytes(locator)
    ).hexdigest()


def _bundle_objects(bundle: VerifiedPhysicalWalObjectStorageBundle) -> tuple[object, ...]:
    values: list[object] = [bundle.baseline.base_backup_object]
    for manifest in bundle.wal_manifests:
        values.extend(segment.object for segment in manifest.segments)
    values.extend(shard.object for shard in bundle.blob_frontier.inventory_shards)
    if not values:
        _fail("WA_FI_FAILBACK_PULL_BUNDLE_INVALID")
    for item in values:
        key = getattr(item, "object_key", None)
        if type(key) is not str or not key.startswith(PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE + "/"):
            _fail("WA_FI_FAILBACK_PULL_OBJECT_NAMESPACE_INVALID")
    return tuple(values)


def _bundle_and_pin(
    bundle_value: object,
    pin_value: object,
    current_term_value: object,
    *,
    now: datetime,
) -> tuple[
    VerifiedPhysicalWalObjectStorageBundle,
    PhysicalWalReceiverStagingPin,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
]:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(bundle_value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError):
        _fail("WA_FI_FAILBACK_PULL_BUNDLE_INVALID")
    _bundle_objects(bundle)
    if type(pin_value) is not PhysicalWalReceiverStagingPin:
        _fail("WA_FI_FAILBACK_PULL_RECEIVER_PIN_INVALID")
    pin = pin_value
    if pin.source_site != _SOURCE_SITE or pin.destination_site != _RECEIVER_SITE:
        _fail("WA_FI_FAILBACK_PULL_ROUTE_INVALID")
    try:
        route_hash = derive_physical_wal_receiver_staging_route_binding_sha256(pin)
    except Exception:
        _fail("WA_FI_FAILBACK_PULL_RECEIVER_PIN_INVALID")
    if route_hash != pin.route_binding_sha256:
        _fail("WA_FI_FAILBACK_PULL_RECEIVER_PIN_INVALID")
    term = _term(current_term_value, now=now)
    baseline = bundle.baseline
    if (
        baseline.source_site != _SOURCE_SITE
        or baseline.destination_site != _RECEIVER_SITE
        or baseline.source_public_key != pin.source_public_key
        or baseline.campaign_id != pin.campaign_id
        or baseline.release_sha != pin.release_sha
        or baseline.writer_term.epoch != pin.writer_epoch
        or baseline.writer_term.lease_id != pin.writer_lease_id
        or baseline.writer_term.witnessed_term_proof_sha256 != pin.witnessed_term_proof_sha256
        or baseline.writer_term.epoch != term.writer_epoch
        or baseline.writer_term.lease_id != term.writer_lease_id
        or baseline.writer_term.witnessed_term_proof_sha256 != term.proof_sha256
        or baseline.baseline_generation_id != pin.baseline_generation_id
        or baseline.manifest_sha256 != pin.baseline_manifest_sha256
        or baseline.database_system_identifier != pin.database_system_identifier
        or baseline.timeline_id != pin.timeline_id
        or baseline.wal_segment_size_bytes != pin.wal_segment_size_bytes
        or baseline.baseline_wal_lsn != pin.baseline_wal_lsn
        or baseline.wal_chain_start_lsn != pin.wal_chain_start_lsn
        or baseline.base_backup_end_lsn != pin.base_backup_end_lsn
        or baseline.base_backup_object.age_recipient != pin.destination_age_recipient
        or not bundle.blob_frontier.objects_complete
    ):
        _fail("WA_FI_FAILBACK_PULL_BUNDLE_PIN_OR_TERM_MISMATCH")
    return bundle, pin, term


def _prevalidate_exact_expectation(
    value: object,
    *,
    pin: PhysicalWalReceiverStagingPin,
    expected: object,
    maximum_ciphertext_bytes: int,
) -> ArvanExactVersionPullExpectation:
    if type(value) is not ArvanExactVersionPullExpectation or type(value.metadata) is not dict:
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_OBJECTS_MISMATCH")
    if (
        value.object_key != getattr(expected, "object_key", None)
        or value.version_id != getattr(expected, "version_id", None)
        or value.ciphertext_sha256 != getattr(expected, "ciphertext_sha256", None)
        or value.ciphertext_bytes != getattr(expected, "ciphertext_bytes", None)
        or type(value.object_key) is not str
        or not value.object_key.startswith(PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE + "/")
        or OBJECT_KEY_RE.fullmatch(value.object_key) is None
        or any(part.lower() in _MUTABLE_SELECTOR_ALIASES for part in value.object_key.split("/"))
        or type(value.version_id) is not str
        or VERSION_ID_RE.fullmatch(value.version_id) is None
        or value.version_id.lower() in _MUTABLE_SELECTOR_ALIASES
        or _sha256(value.ciphertext_sha256, code="WA_FI_FAILBACK_PULL_LOCATOR_OBJECTS_MISMATCH")
        != value.ciphertext_sha256
        or type(value.ciphertext_bytes) is not int
        or not 1 <= value.ciphertext_bytes <= maximum_ciphertext_bytes
        or value.metadata.get("encryption") != "age-v1"
        or value.metadata.get("destination-age-recipient") != pin.destination_age_recipient
        or value.metadata.get("ciphertext-sha256") != value.ciphertext_sha256
        or value.metadata.get("ciphertext-bytes") != str(value.ciphertext_bytes)
    ):
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_OBJECTS_MISMATCH")
    return value


def _locator_facts(
    locator_value: object,
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
    facts: _RuntimeFacts,
    now: datetime,
) -> _LocatorFacts:
    if type(locator_value) is not PhysicalWaFiPostgresFailbackExactObjectLocator:
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_INVALID")
    locator = locator_value
    if locator.schema != PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_LOCATOR_SCHEMA:
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_INVALID")
    issued_at = _utc(locator.issued_at, code="WA_FI_FAILBACK_PULL_LOCATOR_INVALID")
    if (
        issued_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or issued_at < now - timedelta(seconds=facts.maximum_locator_age_seconds)
    ):
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_STALE")
    if (
        locator.source_site != _SOURCE_SITE
        or locator.destination_site != _RECEIVER_SITE
        or locator.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or locator.campaign_id != bundle.baseline.campaign_id
        or locator.release_sha != bundle.baseline.release_sha
        or locator.route_binding_sha256 != pin.route_binding_sha256
        or tuple(locator.manifest_sha256es) != bundle.manifest_sha256es
    ):
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_BINDING_MISMATCH")
    canonical = canonical_wa_fi_postgres_failback_exact_object_locator_bytes(locator)
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != facts.expected_locator_sha256:
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_PIN_MISMATCH")
    expected_objects = _bundle_objects(bundle)
    actual = locator.object_expectations
    if type(actual) is not tuple or len(actual) != len(expected_objects):
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_OBJECTS_MISMATCH")
    accepted = tuple(
        _prevalidate_exact_expectation(
            supplied,
            pin=pin,
            expected=expected,
            maximum_ciphertext_bytes=facts.maximum_ciphertext_bytes,
        )
        for expected, supplied in zip(expected_objects, actual, strict=True)
    )
    if len({(item.object_key, item.version_id) for item in accepted}) != len(accepted):
        _fail("WA_FI_FAILBACK_PULL_LOCATOR_OBJECTS_MISMATCH")
    return _LocatorFacts(
        canonical_locator=canonical,
        locator_sha256=digest,
        expectations=accepted,
    )


class _ExactReceiverGetClient:
    """Restrict a factory callback client to one fixed bucket/key/version set."""

    def __init__(
        self,
        *,
        raw_client: PhysicalWaFiFailbackExactVersionGetClient,
        bucket: str,
        selectors: frozenset[tuple[str, str]],
    ) -> None:
        self._raw_client = raw_client
        self._bucket = bucket
        self._selectors = selectors

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]:
        if (
            type(Bucket) is not str
            or type(Key) is not str
            or type(VersionId) is not str
            or Bucket != self._bucket
            or (Key, VersionId) not in self._selectors
        ):
            _fail("WA_FI_FAILBACK_PULL_EXACT_GET_SELECTOR_INVALID")
        try:
            method = getattr(self._raw_client, "get_object", None)
        except Exception:
            _fail("WA_FI_FAILBACK_PULL_EXACT_GET_CLIENT_INVALID")
        if not callable(method):
            _fail("WA_FI_FAILBACK_PULL_EXACT_GET_CLIENT_INVALID")
        try:
            response = method(Bucket=Bucket, Key=Key, VersionId=VersionId)
        except Exception:
            _fail("WA_FI_FAILBACK_PULL_EXACT_GET_FAILED")
        if not isinstance(response, Mapping):
            _fail("WA_FI_FAILBACK_PULL_EXACT_GET_FAILED")
        return dict(response)


def _new_decryptor(
    facts: _RuntimeFacts,
    factory: AgeDecryptorFactory | None,
) -> PhysicalWalDecryptor:
    try:
        decryptor = PhysicalAgeV1FdDecryptor(facts.age_config) if factory is None else factory(facts.age_config)
    except Exception:
        _fail("WA_FI_FAILBACK_PULL_AGE_DECRYPTOR_INVALID")
    if not callable(getattr(decryptor, "decrypt_to_fd", None)):
        _fail("WA_FI_FAILBACK_PULL_AGE_DECRYPTOR_INVALID")
    return decryptor


def _route_pull_config(
    route: object,
    *,
    maximum_ciphertext_bytes: int,
) -> RootOwnedArvanExactVersionPullConfig:
    if (
        type(route) is not PhysicalWaFiFailbackExactVersionReceiverRoute
        or type(route.endpoint) is not str
        or type(route.region) is not str
        or type(route.bucket) is not str
        or not route.endpoint
        or not route.region
        or not route.bucket
        or route.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    ):
        _fail("WA_FI_FAILBACK_PULL_FACTORY_ROUTE_INVALID")
    config = RootOwnedArvanExactVersionPullConfig(
        endpoint=route.endpoint,
        region=route.region,
        bucket=route.bucket,
        maximum_ciphertext_bytes=maximum_ciphertext_bytes,
        enabled=True,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )
    try:
        # Validation remains inside the factory callback, so provider route
        # facts never become runtime configuration.
        return validate_arvan_exact_version_pull_config(config)
    except ArvanExactVersionPullError:
        _fail("WA_FI_FAILBACK_PULL_FACTORY_ROUTE_INVALID")


def _open_frozen_stage_receipt(
    result: PhysicalWalReceiverStagingResult,
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
    staging_config: PhysicalWalReceiverStagingConfig,
) -> tuple[bytes, str, Path]:
    if (
        result.status != PHYSICAL_WAL_RECEIVER_STAGING_STATUS
        or result.candidate_path is None
        or result.stage_receipt_path is None
        or result.bundle_id is None
        or result.bundle_id
        != hashlib.sha256(
            canonical_json_bytes(
                {
                    "schema": PHYSICAL_WAL_RECEIVER_STAGING_SCHEMA,
                    "route_binding_sha256": pin.route_binding_sha256,
                    "manifest_sha256es": list(bundle.manifest_sha256es),
                }
            )
        ).hexdigest()
        or result.stage_receipt_path != result.candidate_path / "stage-receipt.json"
    ):
        _fail("WA_FI_FAILBACK_PULL_STAGE_RESULT_INVALID")
    expected_candidate = staging_config.receiver_root / "candidates" / result.bundle_id
    try:
        candidate_metadata = os.lstat(result.candidate_path)
        candidate_resolved = result.candidate_path.resolve(strict=True)
    except OSError:
        _fail("WA_FI_FAILBACK_PULL_STAGE_RESULT_INVALID")
    if (
        result.candidate_path != expected_candidate
        or candidate_resolved != result.candidate_path
        or stat.S_ISLNK(candidate_metadata.st_mode)
        or not stat.S_ISDIR(candidate_metadata.st_mode)
        or candidate_metadata.st_uid != 0
        or stat.S_IMODE(candidate_metadata.st_mode) != 0o700
    ):
        _fail("WA_FI_FAILBACK_PULL_STAGE_RESULT_INVALID")
    path = result.stage_receipt_path
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_UNSAFE")
    descriptor = -1
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o400
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not 1 <= opened.st_size <= MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES
        ):
            _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_UNSAFE")
        raw = bytearray()
        while len(raw) < opened.st_size:
            chunk = os.read(descriptor, opened.st_size - len(raw))
            if not chunk:
                _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_UNSAFE")
            raw.extend(chunk)
        if os.read(descriptor, 1):
            _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_UNSAFE")
        after = os.fstat(descriptor)
        if after.st_dev != opened.st_dev or after.st_ino != opened.st_ino or after.st_size != opened.st_size:
            _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_UNSAFE")
    except PhysicalWaFiPostgresFailbackPullRuntimeError:
        raise
    except OSError:
        _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        parsed = json.loads(bytes(raw).decode("ascii", "strict"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_INVALID")
    if (
        type(parsed) is not dict
        or set(parsed) != _STAGE_RECEIPT_FIELDS
        or canonical_json_bytes(parsed) != bytes(raw)
        or parsed.get("status") != PHYSICAL_WAL_RECEIVER_STAGING_STATUS
        or parsed.get("bundle_id") != result.bundle_id
        or parsed.get("route_binding_sha256") != pin.route_binding_sha256
        or tuple(parsed.get("manifest_sha256es", ())) != bundle.manifest_sha256es
        or parsed.get("candidate_path") != str(result.candidate_path)
    ):
        _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_INVALID")
    stage_hash = _sha256(
        parsed.get("receipt_sha256"),
        code="WA_FI_FAILBACK_PULL_STAGE_RECEIPT_INVALID",
    )
    unsigned = {key: value for key, value in parsed.items() if key != "receipt_sha256"}
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != stage_hash:
        _fail("WA_FI_FAILBACK_PULL_STAGE_RECEIPT_INVALID")
    return bytes(raw), stage_hash, result.candidate_path


def _receipt_mapping(
    *,
    bundle_id: str,
    stage_receipt_sha256: str,
    pin: PhysicalWalReceiverStagingPin,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
) -> dict[str, Any]:
    unsigned = {
        "schema": PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RECEIPT_SCHEMA,
        "status": PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED,
        "bundle_id": bundle_id,
        "stage_receipt_sha256": stage_receipt_sha256,
        "route_binding_sha256": pin.route_binding_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "writer_epoch": term.writer_epoch,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.proof_sha256,
        "preflight_evidence_sha256": preflight.observation.evidence_sha256,
        "recovery_preflight_ready": True,
        "failback_materialization_authorized": False,
        "promotion_authorized": False,
        "full_matrix_authorized": False,
    }
    return {
        **unsigned,
        "receipt_sha256": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
    }


def _secure_receipts_directory(root: Path) -> Path:
    path = root / _RECEIPTS_DIRECTORY
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("WA_FI_FAILBACK_PULL_RECEIPT_WRITE_FAILED")
    return _safe_private_directory(path, code="WA_FI_FAILBACK_PULL_RECEIPT_ROOT_UNSAFE")


def _write_or_verify_redacted_receipt(
    *,
    root: Path,
    mapping: Mapping[str, Any],
    bundle_id: str,
) -> PhysicalWaFiPostgresFailbackPullRedactedReceipt:
    if SHA256_RE.fullmatch(bundle_id) is None or not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_FI_FAILBACK_PULL_RECEIPT_INVALID")
    payload = canonical_json_bytes(dict(mapping))
    directory = _secure_receipts_directory(root)
    path = directory / (bundle_id + ".json")
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        created = True
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("WA_FI_FAILBACK_PULL_RECEIPT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    except FileExistsError:
        pass
    except PhysicalWaFiPostgresFailbackPullRuntimeError:
        raise
    except OSError:
        _fail("WA_FI_FAILBACK_PULL_RECEIPT_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if created:
        directory_fd = -1
        try:
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
            os.fsync(directory_fd)
        except OSError:
            _fail("WA_FI_FAILBACK_PULL_RECEIPT_WRITE_FAILED")
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)
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
            or metadata.st_size != len(payload)
        ):
            _fail("WA_FI_FAILBACK_PULL_RECEIPT_INVALID")
        stored = bytearray()
        while len(stored) < metadata.st_size:
            chunk = os.read(read_fd, metadata.st_size - len(stored))
            if not chunk:
                _fail("WA_FI_FAILBACK_PULL_RECEIPT_INVALID")
            stored.extend(chunk)
        if os.read(read_fd, 1) or bytes(stored) != payload:
            _fail("WA_FI_FAILBACK_PULL_RECEIPT_REPLAY_CONFLICT")
    except PhysicalWaFiPostgresFailbackPullRuntimeError:
        raise
    except OSError:
        _fail("WA_FI_FAILBACK_PULL_RECEIPT_INVALID")
    finally:
        if read_fd >= 0:
            os.close(read_fd)
    try:
        parsed = json.loads(payload.decode("ascii", "strict"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("WA_FI_FAILBACK_PULL_RECEIPT_INVALID")
    if (
        type(parsed) is not dict
        or set(parsed) != _RECEIPT_FIELDS
        or canonical_json_bytes(parsed) != payload
        or parsed.get("schema") != PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RECEIPT_SCHEMA
        or parsed.get("status") != PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED
        or parsed.get("bundle_id") != bundle_id
        or parsed.get("recovery_preflight_ready") is not True
        or parsed.get("failback_materialization_authorized") is not False
        or parsed.get("promotion_authorized") is not False
        or parsed.get("full_matrix_authorized") is not False
    ):
        _fail("WA_FI_FAILBACK_PULL_RECEIPT_INVALID")
    receipt_hash = _sha256(parsed.get("receipt_sha256"), code="WA_FI_FAILBACK_PULL_RECEIPT_INVALID")
    unsigned = {key: value for key, value in parsed.items() if key != "receipt_sha256"}
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != receipt_hash:
        _fail("WA_FI_FAILBACK_PULL_RECEIPT_INVALID")
    return PhysicalWaFiPostgresFailbackPullRedactedReceipt(
        raw_receipt=payload,
        receipt_sha256=receipt_hash,
        bundle_id=bundle_id,
        stage_receipt_sha256=_sha256(
            parsed.get("stage_receipt_sha256"),
            code="WA_FI_FAILBACK_PULL_RECEIPT_INVALID",
        ),
        route_binding_sha256=_sha256(
            parsed.get("route_binding_sha256"),
            code="WA_FI_FAILBACK_PULL_RECEIPT_INVALID",
        ),
    )


class RootOwnedWaFiPostgresFailbackPullRuntime:
    """Inert construction plus one FI-local exact reverse-pull entry point."""

    def __init__(
        self,
        config: RootOwnedWaFiPostgresFailbackPullRuntimeConfig,
        *,
        clock: Callable[[], datetime] | None,
        age_decryptor_factory: AgeDecryptorFactory | None = None,
    ) -> None:
        self._config = validate_root_owned_wa_fi_postgres_failback_pull_runtime_config(config)
        self._clock = clock
        self._age_decryptor_factory = age_decryptor_factory

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("WA_FI_FAILBACK_PULL_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="WA_FI_FAILBACK_PULL_CLOCK_INVALID")
        except PhysicalWaFiPostgresFailbackPullRuntimeError:
            raise
        except Exception:
            _fail("WA_FI_FAILBACK_PULL_CLOCK_INVALID")

    def stage(
        self,
        *,
        bundle: object,
        receiver_pin: object,
        locator: object,
        current_witnessed_term: object,
    ) -> PhysicalWaFiPostgresFailbackPullResult:
        """Pull/stage one exact IR→FI bundle; never invoke PostgreSQL."""

        try:
            _require_root()
            started = self._now()
            facts = _config_facts(self._config, now=started, require_enabled=True)
            verified_bundle, pin, term = _bundle_and_pin(
                bundle,
                receiver_pin,
                current_witnessed_term,
                now=started,
            )
            if facts.age_config.recipient != pin.destination_age_recipient:
                _fail("WA_FI_FAILBACK_PULL_AGE_RECIPIENT_MISMATCH")
            if (
                facts.preflight.binding.campaign_id != verified_bundle.baseline.campaign_id
                or facts.preflight.binding.release_sha != verified_bundle.baseline.release_sha
            ):
                _fail("WA_FI_FAILBACK_PULL_PREFLIGHT_CAMPAIGN_MISMATCH")
            locator_facts = _locator_facts(
                locator,
                bundle=verified_bundle,
                pin=pin,
                facts=facts,
                now=started,
            )
            try:
                admission = facts.receiver_factory.admit_fi_receiver_failback_exact_pull(
                    preflight=facts.preflight,
                    current_witnessed_term=term,
                    now=started,
                )
                admission = require_physical_wa_fi_failback_exact_version_receiver_admission(
                    admission,
                    preflight=facts.preflight,
                    preflight_config=facts.preflight_config,
                    current_witnessed_term=term,
                    now=started,
                )
                admission = facts.receiver_factory.require_fi_receiver_failback_exact_pull_admission(
                    admission,
                    preflight=facts.preflight,
                    current_witnessed_term=term,
                    now=started,
                )
                admission = require_physical_wa_fi_failback_exact_version_receiver_admission(
                    admission,
                    preflight=facts.preflight,
                    preflight_config=facts.preflight_config,
                    current_witnessed_term=term,
                    now=started,
                )
            except Exception:
                _fail("WA_FI_FAILBACK_PULL_RECEIVER_ADMISSION_FAILED")

            callback_active = True
            callback_called = False
            callback_invalidated = False
            callback_result: PhysicalWalReceiverStagingResult | None = None
            callback_thread_id = threading.get_ident()

            def operation(
                client: PhysicalWaFiFailbackExactVersionGetClient,
                route: PhysicalWaFiFailbackExactVersionReceiverRoute,
            ) -> PhysicalWalReceiverStagingResult:
                nonlocal callback_called, callback_invalidated, callback_result
                if (
                    not callback_active
                    or callback_called
                    or threading.get_ident() != callback_thread_id
                ):
                    callback_invalidated = True
                    _fail("WA_FI_FAILBACK_PULL_FACTORY_CALLBACK_INVALID")
                callback_called = True
                pull_config = _route_pull_config(
                    route,
                    maximum_ciphertext_bytes=facts.maximum_ciphertext_bytes,
                )
                selectors = frozenset(
                    (item.object_key, item.version_id) for item in locator_facts.expectations
                )
                reader = ArvanExactVersionPullReader(
                    config=pull_config,
                    client_factory=lambda **_kwargs: _ExactReceiverGetClient(
                        raw_client=client,
                        bucket=route.bucket,
                        selectors=selectors,
                    ),
                    expectations=locator_facts.expectations,
                )
                result = stage_physical_wal_object_storage_bundle(
                    bundle=verified_bundle,
                    pin=pin,
                    config=facts.staging_config,
                    exact_version_reader=reader,
                    decryptor=_new_decryptor(facts, self._age_decryptor_factory),
                )
                if type(result) is not PhysicalWalReceiverStagingResult:
                    callback_invalidated = True
                    _fail("WA_FI_FAILBACK_PULL_FACTORY_CALLBACK_INVALID")
                callback_result = result
                return result

            try:
                staged = facts.receiver_factory.execute_fi_receiver_failback_exact_pull(
                    admission=admission,
                    now=started,
                    operation=operation,
                )
            except PhysicalWaFiPostgresFailbackPullRuntimeError:
                raise
            except Exception:
                _fail("WA_FI_FAILBACK_PULL_EXACT_PULL_FAILED")
            finally:
                callback_active = False
            if (
                callback_invalidated
                or not callback_called
                or callback_result is None
                or staged is not callback_result
                or type(staged) is not PhysicalWalReceiverStagingResult
            ):
                _fail("WA_FI_FAILBACK_PULL_FACTORY_CALLBACK_INVALID")
            if staged.status == PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS:
                return PhysicalWaFiPostgresFailbackPullResult(
                    schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA,
                    status=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_BLOCKED,
                    reason_codes=(
                        "WA_FI_FAILBACK_PULL_STAGING_"
                        + (staged.reason_codes[0] if staged.reason_codes else "FAILED"),
                    ),
                )
            raw_stage_receipt, stage_receipt_sha256, candidate = _open_frozen_stage_receipt(
                staged,
                bundle=verified_bundle,
                pin=pin,
                staging_config=facts.staging_config,
            )
            completed = self._now()
            if completed < started:
                _fail("WA_FI_FAILBACK_PULL_CLOCK_INVALID")
            completed_facts = _config_facts(self._config, now=completed, require_enabled=True)
            if (
                completed_facts.expected_locator_sha256 != facts.expected_locator_sha256
                or completed_facts.maximum_ciphertext_bytes != facts.maximum_ciphertext_bytes
            ):
                _fail("WA_FI_FAILBACK_PULL_POLICY_CHANGED")
            _bundle_after, pin_after, term_after = _bundle_and_pin(
                verified_bundle,
                pin,
                current_witnessed_term,
                now=completed,
            )
            if pin_after != pin or term_after != term:
                _fail("WA_FI_FAILBACK_PULL_WITNESS_TERM_CHANGED")
            _locator_facts(
                locator,
                bundle=verified_bundle,
                pin=pin,
                facts=completed_facts,
                now=completed,
            )
            try:
                admission = require_physical_wa_fi_failback_exact_version_receiver_admission(
                    admission,
                    preflight=completed_facts.preflight,
                    preflight_config=completed_facts.preflight_config,
                    current_witnessed_term=term,
                    now=completed,
                )
                admission = completed_facts.receiver_factory.require_fi_receiver_failback_exact_pull_admission(
                    admission,
                    preflight=completed_facts.preflight,
                    current_witnessed_term=term,
                    now=completed,
                )
                require_physical_wa_fi_failback_exact_version_receiver_admission(
                    admission,
                    preflight=completed_facts.preflight,
                    preflight_config=completed_facts.preflight_config,
                    current_witnessed_term=term,
                    now=completed,
                )
            except PhysicalWaFiPostgresFailbackPullRuntimeError:
                raise
            except Exception:
                _fail("WA_FI_FAILBACK_PULL_POST_PULL_RECHECK_FAILED")
            receipt = _write_or_verify_redacted_receipt(
                root=completed_facts.redacted_receipt_root,
                mapping=_receipt_mapping(
                    bundle_id=staged.bundle_id or "",
                    stage_receipt_sha256=stage_receipt_sha256,
                    pin=pin,
                    bundle=verified_bundle,
                    term=term,
                    preflight=completed_facts.preflight,
                ),
                bundle_id=staged.bundle_id or "",
            )
            stage_binding = PhysicalPostgresRecoveryStageBinding(
                bundle_id=receipt.bundle_id,
                stage_receipt_sha256=receipt.stage_receipt_sha256,
                route_binding_sha256=receipt.route_binding_sha256,
            )
            return PhysicalWaFiPostgresFailbackPullResult(
                schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED,
                reason_codes=(),
                redacted_receipt=receipt,
                recovery_preflight_binding=PhysicalPostgresRecoveryPreflightBinding(
                    local_standby_site=_RECEIVER_SITE,
                    stage_binding=stage_binding,
                    expected_witnessed_term=term,
                ),
                failback_stage_evidence=PhysicalWaFiPostgresFailbackStageEvidence(
                    source_candidate=candidate,
                    raw_stage_receipt=raw_stage_receipt,
                    stage_receipt_sha256=stage_receipt_sha256,
                ),
                idempotent=staged.idempotent,
                promotion_authorized=False,
                full_matrix_authorized=False,
            )
        except PhysicalWaFiPostgresFailbackPullRuntimeError as exc:
            return PhysicalWaFiPostgresFailbackPullResult(
                schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_BLOCKED,
                reason_codes=(exc.code,),
            )
        except Exception:
            return PhysicalWaFiPostgresFailbackPullResult(
                schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_BLOCKED,
                reason_codes=("WA_FI_FAILBACK_PULL_UNEXPECTED_FAILURE",),
            )


def stage_root_owned_wa_fi_postgres_failback_bundle(
    *,
    config: RootOwnedWaFiPostgresFailbackPullRuntimeConfig,
    bundle: object,
    receiver_pin: object,
    locator: object,
    current_witnessed_term: object,
    now: datetime,
    age_decryptor_factory: AgeDecryptorFactory | None = None,
) -> PhysicalWaFiPostgresFailbackPullResult:
    """One-shot non-authorizing convenience wrapper over the FI runtime."""

    runtime = RootOwnedWaFiPostgresFailbackPullRuntime(
        config,
        clock=lambda: now,
        age_decryptor_factory=age_decryptor_factory,
    )
    return runtime.stage(
        bundle=bundle,
        receiver_pin=receiver_pin,
        locator=locator,
        current_witnessed_term=current_witnessed_term,
    )
