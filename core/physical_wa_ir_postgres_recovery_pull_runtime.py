"""Root-only WA-IR physical PostgreSQL recovery-material pull runtime.

This is the concrete, deliberately narrow assembly point for the receiver
half of the physical PostgreSQL Object-Storage route.  It accepts one
already-verified signed base/WAL/blob bundle plus a fresh, root-pinned exact
metadata locator, fetches only those immutable versions with the WA-IR
credential, decrypts only into the existing receiver stager, and returns the
typed inputs needed by the later recovery-readback/bootstrap boundaries.

It never lists a bucket, writes to Object Storage, contacts WA-FI, opens the
FI credential, starts/restores PostgreSQL, runs Docker/SSH, promotes a node,
or authorizes a release or Full Matrix campaign.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_arvan_immutability_preflight as _preflight
from core import physical_arvan_s3_ir_receiver_role_loader as _ir_receiver_loader
from core import physical_arvan_s3_role_local_credential_reader as _credential_reader
from core import physical_age_v1_adapter as _age
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.physical_arvan_exact_version_pull import (
    ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES,
    ArvanExactVersionPullError,
    ArvanExactVersionPullExpectation,
    ArvanExactVersionPullReader,
    RootOwnedArvanExactVersionPullConfig,
    validate_arvan_exact_version_pull_config,
)
from core.physical_age_v1_adapter import (
    PhysicalAgeV1DecryptorConfig,
    PhysicalAgeV1FdDecryptor,
)
from core.physical_postgres_recovery_preflight import (
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryStageBinding,
)
from core.physical_postgres_standby_bootstrap_materialization import (
    PhysicalPostgresStandbyBootstrapStageEvidence,
)
from core.physical_arvan_s3_role_local_route_policy import ArvanS3RoleLocalRoutePolicy
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
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
    "DEFAULT_WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_AGE_SECONDS",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_DEFAULT_ENABLED",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_SCHEMA",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_SCHEMA",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_BLOCKED",
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED",
    "PhysicalWaIrPostgresRecoveryExactObjectLocator",
    "PhysicalWaIrPostgresRecoveryPullRedactedReceipt",
    "PhysicalWaIrPostgresRecoveryPullResult",
    "PhysicalWaIrPostgresRecoveryPullRuntimeError",
    "RootOwnedWaIrPostgresRecoveryPullRuntime",
    "RootOwnedWaIrPostgresRecoveryPullRuntimeConfig",
    "canonical_wa_ir_postgres_recovery_exact_object_locator_bytes",
    "derive_wa_ir_postgres_recovery_exact_object_locator_sha256",
    "stage_root_owned_wa_ir_postgres_recovery_bundle",
    "validate_root_owned_wa_ir_postgres_recovery_pull_runtime_config",
)


PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-recovery-pull-runtime-v1"
)
PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-recovery-exact-object-locator-v1"
)
PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-recovery-pull-redacted-receipt-v1"
)
PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_DEFAULT_ENABLED = False
PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED = (
    "staged-not-replay-verified"
)
PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_BLOCKED = "blocked"

DEFAULT_WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_AGE_SECONDS = 180
_MAX_LOCATOR_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_SOURCE_SITE = "webapp_fi"
_RECEIVER_SITE = "webapp_ir"
_RUNTIME_MODE = "root-owned-wa-ir-exact-version-age-v1-recovery-pull-v1"
_RECEIPTS_DIRECTORY = "receipts"
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bundle_id",
        "stage_receipt_sha256",
        "route_binding_sha256",
        "manifest_sha256es",
        "writer_epoch",
        "witnessed_term_proof_sha256",
        "preflight_evidence_sha256",
        "recovery_preflight_ready",
        "standby_bootstrap_materialization_authorized",
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
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class PhysicalWaIrPostgresRecoveryPullRuntimeError(ValueError):
    """Fixed-code refusal; its message never contains a selector or secret."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaIrPostgresRecoveryExactObjectLocator:
    """Fresh root-pinned full metadata for a signed physical object bundle.

    The signed bundle already fixes every key, version, ciphertext hash/size,
    and age recipient.  The locator adds the *complete* S3 metadata map that
    ``ArvanExactVersionPullReader`` intentionally compares exactly.  It has
    no credential, URL, path, latest selector, or client capability.
    """

    schema: str = PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_SCHEMA
    issued_at: datetime | None = None
    source_site: str = _SOURCE_SITE
    destination_site: str = _RECEIVER_SITE
    campaign_id: str = ""
    release_sha: str = ""
    route_binding_sha256: str = ""
    manifest_sha256es: tuple[str, ...] = ()
    object_expectations: tuple[ArvanExactVersionPullExpectation, ...] = ()


@dataclass(frozen=True)
class RootOwnedWaIrPostgresRecoveryPullRuntimeConfig:
    """Default-off WA-IR-only policy with no caller-selected transport path.

    ``preflight`` is the fresh opaque paired provider evidence.  It is used
    only to pin the private bucket route and the distinct public FI/IR machine
    identities.  The execution path opens *only* the fixed IR credential.
    """

    schema: str = PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA
    exact_pull_config: RootOwnedArvanExactVersionPullConfig | None = field(
        default=None, repr=False, compare=False
    )
    age_decryptor_config: PhysicalAgeV1DecryptorConfig | None = field(
        default=None, repr=False, compare=False
    )
    receiver_staging_config: PhysicalWalReceiverStagingConfig | None = field(
        default=None, repr=False, compare=False
    )
    redacted_receipt_root: Path | None = field(default=None, repr=False, compare=False)
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight | None = field(
        default=None, repr=False, compare=False
    )
    expected_locator_sha256: str = ""
    maximum_locator_age_seconds: int = DEFAULT_WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_AGE_SECONDS
    enabled: bool = PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    receiver_site: str = _RECEIVER_SITE
    runtime_mode: str = _RUNTIME_MODE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalWaIrPostgresRecoveryPullRedactedReceipt:
    """Portable non-secret evidence of local staging, never an authority."""

    raw_receipt: bytes
    receipt_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str


@dataclass(frozen=True)
class PhysicalWaIrPostgresRecoveryPullResult:
    """Staging result plus *typed input* for the next non-authorizing gates."""

    schema: str
    status: str
    reason_codes: tuple[str, ...]
    redacted_receipt: PhysicalWaIrPostgresRecoveryPullRedactedReceipt | None = None
    recovery_preflight_binding: PhysicalPostgresRecoveryPreflightBinding | None = None
    standby_bootstrap_stage_evidence: PhysicalPostgresStandbyBootstrapStageEvidence | None = None
    idempotent: bool = False
    promotion_authorized: bool = False
    full_matrix_authorized: bool = False

    @property
    def staged(self) -> bool:
        return self.status == PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED


@dataclass(frozen=True)
class _RuntimeFacts:
    pull_config: RootOwnedArvanExactVersionPullConfig
    age_config: PhysicalAgeV1DecryptorConfig
    staging_config: PhysicalWalReceiverStagingConfig
    redacted_receipt_root: Path
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    expected_locator_sha256: str
    maximum_locator_age_seconds: int
    fi_identity_sha256: str
    ir_identity_sha256: str


@dataclass(frozen=True)
class _LocatorFacts:
    canonical_locator: bytes
    locator_sha256: str
    expectations: tuple[ArvanExactVersionPullExpectation, ...]


class _RawS3ClientBuilder(Protocol):
    def __call__(
        self,
        *,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
    ) -> object: ...


CredentialAdmitter = Callable[
    [ArvanS3RoleLocalRoutePolicy],
    tuple[
        _credential_reader.ArvanS3RoleLocalRouteFacts,
        _credential_reader.ArvanS3RoleLocalCredentialFacts,
    ],
]
AgeDecryptorFactory = Callable[[PhysicalAgeV1DecryptorConfig], PhysicalWalDecryptor]


def _fail(code: str) -> None:
    raise PhysicalWaIrPostgresRecoveryPullRuntimeError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_ROOT_REQUIRED")
    except OSError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_ROOT_REQUIRED")


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


def _safe_private_file(
    value: object,
    *,
    code: str,
    maximum_bytes: int,
) -> Path:
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
    except PhysicalWaIrPostgresRecoveryPullRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _config_facts(
    value: object,
    *,
    now: datetime,
    require_enabled: bool,
) -> _RuntimeFacts:
    if type(value) is not RootOwnedWaIrPostgresRecoveryPullRuntimeConfig:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA
        or type(value.enabled) is not bool
        or value.source_site != _SOURCE_SITE
        or value.receiver_site != _RECEIVER_SITE
        or value.runtime_mode != _RUNTIME_MODE
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_DISABLED")
    if type(value.exact_pull_config) is not RootOwnedArvanExactVersionPullConfig:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    try:
        pull_config = validate_arvan_exact_version_pull_config(value.exact_pull_config)
    except ArvanExactVersionPullError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    if require_enabled and pull_config.enabled is not True:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_DISABLED")
    if type(value.age_decryptor_config) is not PhysicalAgeV1DecryptorConfig:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_AGE_CONFIG_INVALID")
    age_config = value.age_decryptor_config
    if (
        age_config.enabled is not True
        or age_config.direct_site_control != "forbidden"
        or age_config.destination_object_ingest != "pull-only"
        or not isinstance(age_config.recipient, str)
        or not age_config.recipient
        or type(age_config.maximum_plaintext_bytes) is not int
        or type(age_config.maximum_ciphertext_bytes) is not int
        or not 1 <= age_config.maximum_plaintext_bytes <= _age.DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES
        or not age_config.maximum_plaintext_bytes <= age_config.maximum_ciphertext_bytes
        or age_config.maximum_ciphertext_bytes > _age.DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES
        or AGE_RECIPIENT_RE.fullmatch(age_config.recipient) is None
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_AGE_CONFIG_INVALID")
    _safe_private_directory(
        age_config.workspace_root,
        code="WA_IR_POSTGRES_RECOVERY_PULL_AGE_CONFIG_INVALID",
    )
    _safe_private_file(
        age_config.identity_path,
        code="WA_IR_POSTGRES_RECOVERY_PULL_AGE_CONFIG_INVALID",
        maximum_bytes=_age.MAX_PHYSICAL_AGE_IDENTITY_BYTES,
    )
    if pull_config.maximum_ciphertext_bytes > age_config.maximum_ciphertext_bytes:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_AGE_CONFIG_INVALID")
    if type(value.receiver_staging_config) is not PhysicalWalReceiverStagingConfig:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    receipt_root = _safe_private_directory(
        value.redacted_receipt_root,
        code="WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_ROOT_UNSAFE",
    )
    try:
        staging_receiver = _safe_private_directory(
            value.receiver_staging_config.receiver_root,
            code="WA_IR_POSTGRES_RECOVERY_PULL_STAGING_ROOT_UNSAFE",
        )
        staging_state = _safe_private_directory(
            value.receiver_staging_config.state_root,
            code="WA_IR_POSTGRES_RECOVERY_PULL_STAGING_ROOT_UNSAFE",
        )
    except AttributeError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    if (
        receipt_root == staging_receiver
        or receipt_root == staging_state
        or receipt_root.is_relative_to(staging_receiver)
        or receipt_root.is_relative_to(staging_state)
        or staging_receiver.is_relative_to(receipt_root)
        or staging_state.is_relative_to(receipt_root)
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_ROOT_OVERLAPS_STAGING")
    maximum_locator_age = _positive(
        value.maximum_locator_age_seconds,
        maximum=_MAX_LOCATOR_AGE_SECONDS,
        code="WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_AGE_INVALID",
    )
    if type(value.preflight) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_INVALID")
    try:
        verified_preflight = _preflight.require_verified_physical_arvan_immutability_preflight(
            value.preflight,
            binding=value.preflight.binding,
            now=now,
        )
    except Exception:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_INVALID")
    binding = verified_preflight.binding
    if (
        binding.source_site != _SOURCE_SITE
        or binding.destination_site != _RECEIVER_SITE
        or binding.endpoint != pull_config.endpoint
        or binding.region != pull_config.region
        or binding.bucket != pull_config.bucket
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_ROUTE_MISMATCH")
    try:
        restrictions = {item.role: item for item in verified_preflight.observation.credential_restrictions}
        fi_identity = _sha256(
            restrictions["fi-publisher"].credential_identity_sha256,
            code="WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_IDENTITIES_INVALID",
        )
        ir_identity = _sha256(
            restrictions["ir-receiver"].credential_identity_sha256,
            code="WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_IDENTITIES_INVALID",
        )
    except Exception:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_IDENTITIES_INVALID")
    if fi_identity == ir_identity:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_IDENTITIES_INVALID")
    return _RuntimeFacts(
        pull_config=pull_config,
        age_config=age_config,
        staging_config=PhysicalWalReceiverStagingConfig(
            receiver_root=staging_receiver,
            state_root=staging_state,
        ),
        redacted_receipt_root=receipt_root,
        preflight=verified_preflight,
        expected_locator_sha256=_sha256(
            value.expected_locator_sha256,
            code="WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_PIN_INVALID",
        ),
        maximum_locator_age_seconds=maximum_locator_age,
        fi_identity_sha256=fi_identity,
        ir_identity_sha256=ir_identity,
    )


def validate_root_owned_wa_ir_postgres_recovery_pull_runtime_config(
    config: RootOwnedWaIrPostgresRecoveryPullRuntimeConfig,
) -> RootOwnedWaIrPostgresRecoveryPullRuntimeConfig:
    """Perform only inert shape validation for a root-owned policy.

    In particular, construction does not open a directory, private identity,
    credential, SDK client, socket, age binary, PostgreSQL connection, or
    Object Storage object.  The execution entry point rechecks every live
    filesystem/preflight/term fact immediately before and after staging.
    """

    if type(config) is not RootOwnedWaIrPostgresRecoveryPullRuntimeConfig:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA
        or type(config.enabled) is not bool
        or config.source_site != _SOURCE_SITE
        or config.receiver_site != _RECEIVER_SITE
        or config.runtime_mode != _RUNTIME_MODE
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
        or type(config.exact_pull_config) is not RootOwnedArvanExactVersionPullConfig
        or type(config.age_decryptor_config) is not PhysicalAgeV1DecryptorConfig
        or type(config.receiver_staging_config) is not PhysicalWalReceiverStagingConfig
        or not isinstance(config.redacted_receipt_root, Path)
        or type(config.preflight) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    try:
        validate_arvan_exact_version_pull_config(config.exact_pull_config)
    except ArvanExactVersionPullError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_CONFIG_INVALID")
    _sha256(
        config.expected_locator_sha256,
        code="WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_PIN_INVALID",
    )
    _positive(
        config.maximum_locator_age_seconds,
        maximum=_MAX_LOCATOR_AGE_SECONDS,
        code="WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_AGE_INVALID",
    )
    return config


def _canonical_locator_mapping(locator: PhysicalWaIrPostgresRecoveryExactObjectLocator) -> dict[str, Any]:
    issued_at = _utc(locator.issued_at, code="WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    if type(locator.object_expectations) is not tuple:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    objects: list[dict[str, Any]] = []
    for expectation in locator.object_expectations:
        if type(expectation) is not ArvanExactVersionPullExpectation or type(expectation.metadata) is not dict:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
        objects.append(
            {
                "object_key": expectation.object_key,
                "version_id": expectation.version_id,
                "ciphertext_sha256": expectation.ciphertext_sha256,
                "ciphertext_bytes": expectation.ciphertext_bytes,
                "metadata": dict(expectation.metadata),
            }
        )
    if type(locator.manifest_sha256es) is not tuple:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    return {
        "schema": locator.schema,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "source_site": locator.source_site,
        "destination_site": locator.destination_site,
        "campaign_id": locator.campaign_id,
        "release_sha": locator.release_sha,
        "route_binding_sha256": locator.route_binding_sha256,
        "manifest_sha256es": list(locator.manifest_sha256es),
        "object_expectations": objects,
    }


def canonical_wa_ir_postgres_recovery_exact_object_locator_bytes(
    locator: PhysicalWaIrPostgresRecoveryExactObjectLocator,
) -> bytes:
    """Return canonical non-secret locator bytes for a root policy hash pin.

    This helper verifies only the public locator shape.  The runtime later
    binds it to the opaque signed bundle, live Witness term, and fresh paired
    Object-Storage preflight before any receiver credential is opened.
    """

    if type(locator) is not PhysicalWaIrPostgresRecoveryExactObjectLocator:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    if locator.schema != PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_SCHEMA:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    try:
        return canonical_json_bytes(_canonical_locator_mapping(locator))
    except (TypeError, ValueError):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")


def derive_wa_ir_postgres_recovery_exact_object_locator_sha256(
    locator: PhysicalWaIrPostgresRecoveryExactObjectLocator,
) -> str:
    """Return the exact root-pinnable SHA-256 for one public locator."""

    return hashlib.sha256(
        canonical_wa_ir_postgres_recovery_exact_object_locator_bytes(locator)
    ).hexdigest()


def _bundle_objects(bundle: VerifiedPhysicalWalObjectStorageBundle) -> tuple[object, ...]:
    values: list[object] = [bundle.baseline.base_backup_object]
    for manifest in bundle.wal_manifests:
        values.extend(segment.object for segment in manifest.segments)
    values.extend(shard.object for shard in bundle.blob_frontier.inventory_shards)
    if not values:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_BUNDLE_INVALID")
    return tuple(values)


def _bundle_and_pin(
    bundle_value: object,
    pin_value: object,
    current_term_value: object,
    *,
    now: datetime,
) -> tuple[VerifiedPhysicalWalObjectStorageBundle, PhysicalWalReceiverStagingPin, VerifiedObjectDeltaRoleMatrixWitnessedTerm]:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(bundle_value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_BUNDLE_INVALID")
    if type(pin_value) is not PhysicalWalReceiverStagingPin:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIVER_PIN_INVALID")
    pin = pin_value
    if pin.source_site != _SOURCE_SITE or pin.destination_site != _RECEIVER_SITE:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_ROUTE_INVALID")
    try:
        route_hash = derive_physical_wal_receiver_staging_route_binding_sha256(pin)
    except Exception:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIVER_PIN_INVALID")
    if route_hash != pin.route_binding_sha256:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIVER_PIN_INVALID")
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(current_term_value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_WITNESS_TERM_INVALID_OR_STALE")
    baseline = bundle.baseline
    if (
        baseline.source_site != _SOURCE_SITE
        or baseline.destination_site != _RECEIVER_SITE
        or term.holder_site != _SOURCE_SITE
        or baseline.source_public_key != pin.source_public_key
        or baseline.source_site != pin.source_site
        or baseline.destination_site != pin.destination_site
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
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_BUNDLE_PIN_OR_TERM_MISMATCH")
    return bundle, pin, term


def _locator_facts(
    locator_value: object,
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
    facts: _RuntimeFacts,
    now: datetime,
) -> _LocatorFacts:
    if type(locator_value) is not PhysicalWaIrPostgresRecoveryExactObjectLocator:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    locator = locator_value
    if locator.schema != PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_SCHEMA:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    issued_at = _utc(locator.issued_at, code="WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    if (
        issued_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or issued_at < now - timedelta(seconds=facts.maximum_locator_age_seconds)
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_STALE")
    if (
        locator.source_site != _SOURCE_SITE
        or locator.destination_site != _RECEIVER_SITE
        or locator.campaign_id != bundle.baseline.campaign_id
        or locator.release_sha != bundle.baseline.release_sha
        or locator.route_binding_sha256 != pin.route_binding_sha256
        or tuple(locator.manifest_sha256es) != bundle.manifest_sha256es
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_BINDING_MISMATCH")
    try:
        canonical = canonical_json_bytes(_canonical_locator_mapping(locator))
    except (TypeError, ValueError):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_INVALID")
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != facts.expected_locator_sha256:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_PIN_MISMATCH")
    expected_objects = _bundle_objects(bundle)
    actual = locator.object_expectations
    if type(actual) is not tuple or len(actual) != len(expected_objects):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_OBJECTS_MISMATCH")
    accepted: list[ArvanExactVersionPullExpectation] = []
    for expected, supplied in zip(expected_objects, actual, strict=True):
        if type(supplied) is not ArvanExactVersionPullExpectation or type(supplied.metadata) is not dict:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_OBJECTS_MISMATCH")
        if (
            supplied.object_key != expected.object_key
            or supplied.version_id != expected.version_id
            or supplied.ciphertext_sha256 != expected.ciphertext_sha256
            or supplied.ciphertext_bytes != expected.ciphertext_bytes
            or supplied.metadata.get("encryption") != "age-v1"
            or supplied.metadata.get("destination-age-recipient") != pin.destination_age_recipient
            or supplied.metadata.get("ciphertext-sha256") != expected.ciphertext_sha256
            or supplied.metadata.get("ciphertext-bytes") != str(expected.ciphertext_bytes)
        ):
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_OBJECTS_MISMATCH")
        accepted.append(supplied)
    if len({(item.object_key, item.version_id) for item in accepted}) != len(accepted):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_OBJECTS_MISMATCH")
    # The strict existing reader is also the single parser for its exact
    # metadata grammar and maximum ciphertext bound.  This construction has
    # no client call because the callable is not invoked until a staged read.
    try:
        ArvanExactVersionPullReader(
            config=facts.pull_config,
            client_factory=lambda **_kwargs: None,
            expectations=tuple(accepted),
        )
    except ArvanExactVersionPullError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_OBJECTS_MISMATCH")
    return _LocatorFacts(
        canonical_locator=canonical,
        locator_sha256=digest,
        expectations=tuple(accepted),
    )


def _role_local_route_policy(facts: _RuntimeFacts) -> ArvanS3RoleLocalRoutePolicy:
    """Construct the sole non-secret normal route accepted by IR artifact."""

    return ArvanS3RoleLocalRoutePolicy(
        endpoint=facts.pull_config.endpoint,
        region=facts.pull_config.region,
        bucket=facts.pull_config.bucket,
        enabled=True,
        source_site=_SOURCE_SITE,
        destination_site=_RECEIVER_SITE,
        object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _default_credential_admitter(
    route_policy: ArvanS3RoleLocalRoutePolicy,
) -> tuple[
    _credential_reader.ArvanS3RoleLocalRouteFacts,
    _credential_reader.ArvanS3RoleLocalCredentialFacts,
]:
    # The production route reaches the single-role artifact, never the
    # historical paired normal credential API.
    return _ir_receiver_loader.load_root_owned_arvan_s3_ir_receiver_role_credential_facts(route_policy)


def _default_raw_s3_client_builder(
    *, endpoint: str, region: str, access_key: str, secret_key: str
) -> object:
    try:
        boto3_module = importlib.import_module("boto3")
        botocore_config_module = importlib.import_module("botocore.config")
        session_type = getattr(getattr(boto3_module, "session"), "Session")
        config_type = getattr(botocore_config_module, "Config")
        if not callable(session_type) or not callable(config_type):
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_SDK_UNAVAILABLE")
        client_config = config_type(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path"},
            proxies={},
        )
        session = session_type(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        client = session.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            use_ssl=True,
            verify=True,
            config=client_config,
        )
    except PhysicalWaIrPostgresRecoveryPullRuntimeError:
        raise
    except Exception:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_SDK_CLIENT_FAILED")
    if client is None:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_SDK_CLIENT_FAILED")
    return client


class _ExactReceiverGetClient:
    """Private wrapper with exactly the expected GET selector surface."""

    def __init__(self, *, raw_client: object, bucket: str, selectors: frozenset[tuple[str, str]]) -> None:
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
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_EXACT_GET_SELECTOR_INVALID")
        try:
            method = getattr(self._raw_client, "get_object", None)
        except Exception:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_SDK_CLIENT_FAILED")
        if not callable(method):
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_SDK_CLIENT_FAILED")
        try:
            response = method(Bucket=Bucket, Key=Key, VersionId=VersionId)
        except Exception:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_EXACT_GET_FAILED")
        if not isinstance(response, Mapping):
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_EXACT_GET_FAILED")
        return dict(response)


class _RootOwnedWaIrExactPullClientFactory:
    """Open only the fixed IR credential for one exact Object GET."""

    def __init__(
        self,
        *,
        facts: _RuntimeFacts,
        locator: _LocatorFacts,
        credential_admitter: CredentialAdmitter,
        raw_s3_client_builder: _RawS3ClientBuilder,
    ) -> None:
        self._facts = facts
        self._selectors = frozenset(
            (item.object_key, item.version_id) for item in locator.expectations
        )
        self._credential_admitter = credential_admitter
        self._raw_s3_client_builder = raw_s3_client_builder

    def __call__(self, *, endpoint: str, region: str) -> _ExactReceiverGetClient:
        _require_root()
        if endpoint != self._facts.pull_config.endpoint or region != self._facts.pull_config.region:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_CLIENT_ORIGIN_MISMATCH")
        if not callable(self._credential_admitter) or not callable(self._raw_s3_client_builder):
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_CLIENT_FACTORY_INVALID")
        try:
            route_facts, credential = self._credential_admitter(
                _role_local_route_policy(self._facts)
            )
        except PhysicalWaIrPostgresRecoveryPullRuntimeError:
            raise
        except Exception:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIVER_CREDENTIAL_ADMISSION_FAILED")
        if (
            type(route_facts) is not _credential_reader.ArvanS3RoleLocalRouteFacts
            or type(credential) is not _credential_reader.ArvanS3RoleLocalCredentialFacts
            or route_facts.endpoint != self._facts.pull_config.endpoint
            or route_facts.region != self._facts.pull_config.region
            or route_facts.bucket != self._facts.pull_config.bucket
            or credential.identity_sha256 != self._facts.ir_identity_sha256
            or credential.identity_sha256 == self._facts.fi_identity_sha256
        ):
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIVER_CREDENTIAL_MISMATCH")
        try:
            raw = self._raw_s3_client_builder(
                endpoint=self._facts.pull_config.endpoint,
                region=self._facts.pull_config.region,
                access_key=credential.access_key,
                secret_key=credential.secret_key,
            )
        except PhysicalWaIrPostgresRecoveryPullRuntimeError:
            raise
        except Exception:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_SDK_CLIENT_FAILED")
        if raw is None:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_SDK_CLIENT_FAILED")
        return _ExactReceiverGetClient(
            raw_client=raw,
            bucket=self._facts.pull_config.bucket,
            selectors=self._selectors,
        )


def _new_decryptor(
    facts: _RuntimeFacts,
    factory: AgeDecryptorFactory | None,
) -> PhysicalWalDecryptor:
    if facts.age_config.recipient == "":
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_AGE_CONFIG_INVALID")
    try:
        decryptor = PhysicalAgeV1FdDecryptor(facts.age_config) if factory is None else factory(facts.age_config)
    except Exception:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_AGE_DECRYPTOR_INVALID")
    if not callable(getattr(decryptor, "decrypt_to_fd", None)):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_AGE_DECRYPTOR_INVALID")
    return decryptor


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
        or result.bundle_id != hashlib.sha256(
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
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RESULT_INVALID")
    expected_candidate = staging_config.receiver_root / "candidates" / result.bundle_id
    try:
        candidate_metadata = os.lstat(result.candidate_path)
        candidate_resolved = result.candidate_path.resolve(strict=True)
    except OSError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RESULT_INVALID")
    if (
        result.candidate_path != expected_candidate
        or candidate_resolved != result.candidate_path
        or stat.S_ISLNK(candidate_metadata.st_mode)
        or not stat.S_ISDIR(candidate_metadata.st_mode)
        or candidate_metadata.st_uid != 0
        or stat.S_IMODE(candidate_metadata.st_mode) != 0o700
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RESULT_INVALID")
    path = result.stage_receipt_path
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_UNSAFE")
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
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_UNSAFE")
        raw = bytearray()
        while len(raw) < opened.st_size:
            chunk = os.read(descriptor, opened.st_size - len(raw))
            if not chunk:
                _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_UNSAFE")
            raw.extend(chunk)
        if os.read(descriptor, 1):
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_UNSAFE")
        after = os.fstat(descriptor)
        if after.st_dev != opened.st_dev or after.st_ino != opened.st_ino or after.st_size != opened.st_size:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_UNSAFE")
    except PhysicalWaIrPostgresRecoveryPullRuntimeError:
        raise
    except OSError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        parsed = json.loads(bytes(raw).decode("ascii", "strict"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_INVALID")
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
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_INVALID")
    stage_hash = _sha256(
        parsed.get("receipt_sha256"),
        code="WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_INVALID",
    )
    unsigned = {key: value for key, value in parsed.items() if key != "receipt_sha256"}
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != stage_hash:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_STAGE_RECEIPT_INVALID")
    return bytes(raw), stage_hash, result.candidate_path


def _receipt_mapping(
    *,
    bundle_id: str,
    stage_receipt_sha256: str,
    pin: PhysicalWalReceiverStagingPin,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight,
) -> dict[str, Any]:
    unsigned = {
        "schema": PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_SCHEMA,
        "status": PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED,
        "bundle_id": bundle_id,
        "stage_receipt_sha256": stage_receipt_sha256,
        "route_binding_sha256": pin.route_binding_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "writer_epoch": term.writer_epoch,
        "witnessed_term_proof_sha256": term.proof_sha256,
        "preflight_evidence_sha256": preflight.observation.evidence_sha256,
        "recovery_preflight_ready": True,
        "standby_bootstrap_materialization_authorized": False,
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
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_WRITE_FAILED")
    return _safe_private_directory(path, code="WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_ROOT_UNSAFE")


def _write_or_verify_redacted_receipt(
    *,
    root: Path,
    mapping: Mapping[str, Any],
    bundle_id: str,
) -> PhysicalWaIrPostgresRecoveryPullRedactedReceipt:
    if SHA256_RE.fullmatch(bundle_id) is None or not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID")
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
                _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    except FileExistsError:
        pass
    except PhysicalWaIrPostgresRecoveryPullRuntimeError:
        raise
    except OSError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if created:
        try:
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_WRITE_FAILED")
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
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID")
        stored = bytearray()
        while len(stored) < metadata.st_size:
            chunk = os.read(read_fd, metadata.st_size - len(stored))
            if not chunk:
                _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID")
            stored.extend(chunk)
        if os.read(read_fd, 1) or bytes(stored) != payload:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_REPLAY_CONFLICT")
    except PhysicalWaIrPostgresRecoveryPullRuntimeError:
        raise
    except OSError:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID")
    finally:
        if read_fd >= 0:
            os.close(read_fd)
    try:
        parsed = json.loads(payload.decode("ascii", "strict"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID")
    if (
        type(parsed) is not dict
        or set(parsed) != _RECEIPT_FIELDS
        or canonical_json_bytes(parsed) != payload
        or parsed.get("schema") != PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_SCHEMA
        or parsed.get("status") != PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED
        or parsed.get("bundle_id") != bundle_id
        or parsed.get("promotion_authorized") is not False
        or parsed.get("full_matrix_authorized") is not False
        or parsed.get("standby_bootstrap_materialization_authorized") is not False
        or parsed.get("recovery_preflight_ready") is not True
    ):
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID")
    receipt_hash = _sha256(parsed.get("receipt_sha256"), code="WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID")
    unsigned = {key: value for key, value in parsed.items() if key != "receipt_sha256"}
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != receipt_hash:
        _fail("WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID")
    return PhysicalWaIrPostgresRecoveryPullRedactedReceipt(
        raw_receipt=payload,
        receipt_sha256=receipt_hash,
        bundle_id=bundle_id,
        stage_receipt_sha256=_sha256(
            parsed.get("stage_receipt_sha256"),
            code="WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID",
        ),
        route_binding_sha256=_sha256(
            parsed.get("route_binding_sha256"),
            code="WA_IR_POSTGRES_RECOVERY_PULL_RECEIPT_INVALID",
        ),
    )


class RootOwnedWaIrPostgresRecoveryPullRuntime:
    """Inert construction plus one root-only exact pull/stage entry point."""

    def __init__(
        self,
        config: RootOwnedWaIrPostgresRecoveryPullRuntimeConfig,
        *,
        clock: Callable[[], datetime] | None,
        credential_admitter: CredentialAdmitter = _default_credential_admitter,
        raw_s3_client_builder: _RawS3ClientBuilder = _default_raw_s3_client_builder,
        age_decryptor_factory: AgeDecryptorFactory | None = None,
    ) -> None:
        self._config = validate_root_owned_wa_ir_postgres_recovery_pull_runtime_config(config)
        self._clock = clock
        self._credential_admitter = credential_admitter
        self._raw_s3_client_builder = raw_s3_client_builder
        self._age_decryptor_factory = age_decryptor_factory

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="WA_IR_POSTGRES_RECOVERY_PULL_CLOCK_INVALID")
        except PhysicalWaIrPostgresRecoveryPullRuntimeError:
            raise
        except Exception:
            _fail("WA_IR_POSTGRES_RECOVERY_PULL_CLOCK_INVALID")

    def stage(
        self,
        *,
        bundle: object,
        receiver_pin: object,
        locator: object,
        current_witnessed_term: object,
    ) -> PhysicalWaIrPostgresRecoveryPullResult:
        """Pull/stage one exact FI→IR bundle; never invoke PostgreSQL.

        A result with ``staged`` only provides a typed stage binding for the
        later recovery-readback/bootstrap contracts.  It never provides their
        recovery evidence, materializer invocation, promotion authority, or
        Full-Matrix admission.
        """

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
                _fail("WA_IR_POSTGRES_RECOVERY_PULL_AGE_RECIPIENT_MISMATCH")
            if (
                facts.preflight.binding.campaign_id != verified_bundle.baseline.campaign_id
                or facts.preflight.binding.release_sha != verified_bundle.baseline.release_sha
            ):
                _fail("WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_CAMPAIGN_MISMATCH")
            locator_facts = _locator_facts(
                locator,
                bundle=verified_bundle,
                pin=pin,
                facts=facts,
                now=started,
            )
            client_factory = _RootOwnedWaIrExactPullClientFactory(
                facts=facts,
                locator=locator_facts,
                credential_admitter=self._credential_admitter,
                raw_s3_client_builder=self._raw_s3_client_builder,
            )
            reader = ArvanExactVersionPullReader(
                config=facts.pull_config,
                client_factory=client_factory,
                expectations=locator_facts.expectations,
            )
            decryptor = _new_decryptor(facts, self._age_decryptor_factory)
            staged = stage_physical_wal_object_storage_bundle(
                bundle=verified_bundle,
                pin=pin,
                config=facts.staging_config,
                exact_version_reader=reader,
                decryptor=decryptor,
            )
            if staged.status == PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS:
                return PhysicalWaIrPostgresRecoveryPullResult(
                    schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA,
                    status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_BLOCKED,
                    reason_codes=(
                        "WA_IR_POSTGRES_RECOVERY_PULL_STAGING_" + (staged.reason_codes[0] if staged.reason_codes else "FAILED"),
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
                _fail("WA_IR_POSTGRES_RECOVERY_PULL_CLOCK_INVALID")
            # Recheck the fresh provider and Witness facts after the potentially
            # long base/WAL transfer and before exposing stage evidence.
            completed_facts = _config_facts(self._config, now=completed, require_enabled=True)
            if (
                completed_facts.pull_config != facts.pull_config
                or completed_facts.expected_locator_sha256 != facts.expected_locator_sha256
            ):
                _fail("WA_IR_POSTGRES_RECOVERY_PULL_POLICY_CHANGED")
            _bundle_after, pin_after, term_after = _bundle_and_pin(
                verified_bundle,
                pin,
                current_witnessed_term,
                now=completed,
            )
            if pin_after != pin or term_after != term:
                _fail("WA_IR_POSTGRES_RECOVERY_PULL_WITNESS_TERM_CHANGED")
            _locator_facts(
                locator,
                bundle=verified_bundle,
                pin=pin,
                facts=completed_facts,
                now=completed,
            )
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
            return PhysicalWaIrPostgresRecoveryPullResult(
                schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED,
                reason_codes=(),
                redacted_receipt=receipt,
                recovery_preflight_binding=PhysicalPostgresRecoveryPreflightBinding(
                    local_standby_site=_RECEIVER_SITE,
                    stage_binding=stage_binding,
                    expected_witnessed_term=term,
                ),
                standby_bootstrap_stage_evidence=PhysicalPostgresStandbyBootstrapStageEvidence(
                    source_candidate=candidate,
                    raw_stage_receipt=raw_stage_receipt,
                    stage_receipt_sha256=stage_receipt_sha256,
                ),
                idempotent=staged.idempotent,
                promotion_authorized=False,
                full_matrix_authorized=False,
            )
        except PhysicalWaIrPostgresRecoveryPullRuntimeError as exc:
            return PhysicalWaIrPostgresRecoveryPullResult(
                schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_BLOCKED,
                reason_codes=(exc.code,),
            )
        except Exception:
            return PhysicalWaIrPostgresRecoveryPullResult(
                schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_BLOCKED,
                reason_codes=("WA_IR_POSTGRES_RECOVERY_PULL_UNEXPECTED_FAILURE",),
            )


def stage_root_owned_wa_ir_postgres_recovery_bundle(
    *,
    config: RootOwnedWaIrPostgresRecoveryPullRuntimeConfig,
    bundle: object,
    receiver_pin: object,
    locator: object,
    current_witnessed_term: object,
    now: datetime,
    credential_admitter: CredentialAdmitter = _default_credential_admitter,
    raw_s3_client_builder: _RawS3ClientBuilder = _default_raw_s3_client_builder,
    age_decryptor_factory: AgeDecryptorFactory | None = None,
) -> PhysicalWaIrPostgresRecoveryPullResult:
    """One-shot convenience wrapper over the inert root-owned runtime.

    It is useful to a fixed runtime harness that has a single trusted clock;
    it remains a non-authorizing staging call and has no network fallback.
    """

    runtime = RootOwnedWaIrPostgresRecoveryPullRuntime(
        config,
        clock=lambda: now,
        credential_admitter=credential_admitter,
        raw_s3_client_builder=raw_s3_client_builder,
        age_decryptor_factory=age_decryptor_factory,
    )
    return runtime.stage(
        bundle=bundle,
        receiver_pin=receiver_pin,
        locator=locator,
        current_witnessed_term=current_witnessed_term,
    )
