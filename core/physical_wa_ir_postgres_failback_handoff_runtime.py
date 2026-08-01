"""Root-only IR publisher handoff for the separate IR-to-FI failback route.

This is deliberately *not* a parameterized form of the normal WA-FI handoff.
It can be enabled only for a promoted IR writer and only through a distinct
IR-publisher object-store factory seam.  The normal FI publisher/IR receiver
factory and its two credential files are neither imported nor accepted.

The runtime has no Object-Storage SDK, credential loader, Docker, PostgreSQL,
SSH, socket, shell, direct FI control, or promotion implementation.  It
wraps the existing generic encrypted create-only WAL/base-backup uploaders
inside one injected, role-specific callback after exact route/release/term
checks.  A later four-role concrete factory must open only the IR-publisher
credential and expose only its bounded create-only/readback client there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_age_v1_adapter import (
    DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES,
    DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES,
    PhysicalAgeV1Encryptor,
    PhysicalAgeV1EncryptorConfig,
)
from core.physical_ir_to_fi_object_storage_failback_preflight import (
    PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
    require_verified_physical_ir_to_fi_object_storage_failback_preflight,
)
from core.physical_wal_archive_spool import PhysicalWalArchiveUploadReceipt
from core.physical_wal_base_backup_spool import (
    MAX_PHYSICAL_BASE_BACKUP_BYTES,
    PhysicalWalBaseBackupUploadReceipt,
)
from core.physical_wal_object_manifest import PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES
from core.physical_wal_object_manifest import PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
from core.physical_wal_object_storage_uploader import (
    PhysicalWalAgeEncryptor,
    PhysicalWalBaseBackupObjectStorageUploader,
    PhysicalWalObjectStorageClient,
    PhysicalWalObjectStorageUploader,
    PhysicalWalObjectStorageUploaderConfig,
)


__all__ = (
    "PHYSICAL_WA_IR_POSTGRES_FAILBACK_HANDOFF_DEFAULT_ENABLED",
    "PHYSICAL_WA_IR_POSTGRES_FAILBACK_HANDOFF_SCHEMA",
    "PhysicalWaIrFailbackObjectStoragePublisherAdmission",
    "PhysicalWaIrFailbackObjectStoragePublisherFactory",
    "PhysicalWaIrFailbackObjectStoragePublisherRoute",
    "PhysicalWaIrPostgresFailbackHandoffError",
    "RootOwnedWaIrPostgresFailbackHandoff",
    "RootOwnedWaIrPostgresFailbackHandoffConfig",
    "RootOwnedWaIrPostgresFailbackUploaderPolicy",
    "build_physical_wa_ir_failback_object_storage_publisher_admission",
    "require_physical_wa_ir_failback_object_storage_publisher_admission",
    "validate_root_owned_wa_ir_postgres_failback_handoff_config",
)


PHYSICAL_WA_IR_POSTGRES_FAILBACK_HANDOFF_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-failback-handoff-runtime-v1"
)
PHYSICAL_WA_IR_POSTGRES_FAILBACK_HANDOFF_DEFAULT_ENABLED = False

_SOURCE_SITE = "webapp_ir"
_DESTINATION_SITE = "webapp_fi"
_RUNTIME_MODE = "root-owned-ir-publisher-age-v1-versioned-create-only-v1"
_MAX_PATH_LENGTH = 4096
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_FUTURE_SKEW_SECONDS = 5
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_PUBLISHER_ADMISSION_CAPABILITY = object()
_TOMBSTONED_PAIRED_FACTORY_MODULE = "core.physical_arvan_s3_failback_separated_client_factory"
_TOMBSTONED_PAIRED_FACTORY_CLASS = "RootOwnedArvanS3FailbackSeparatedClientFactory"


class PhysicalWaIrPostgresFailbackHandoffError(RuntimeError):
    """A stable refusal from the non-authorizing IR failback publisher."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaIrFailbackObjectStoragePublisherRoute:
    """Factory-owned route facts, released only inside one callback.

    This is not a caller configuration or a credential.  A concrete four-role
    factory derives it from the separate IR-publisher admission and must never
    return it outside the synchronous publisher callback.
    """

    bucket: str
    region: str
    object_storage_namespace: str = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE


@dataclass(frozen=True)
class PhysicalWaIrFailbackObjectStoragePublisherAdmission:
    """Opaque IR-publisher-only capability, bound to one live reverse term.

    The concrete factory may mint this only after its own four-role secret
    preflight.  It contains no credential, endpoint, bucket, object key, or
    transferable normal-direction capability and rejects serialization.
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
        raise TypeError("WA_IR_FAILBACK_PUBLISHER_ADMISSION_SERIALIZATION_FORBIDDEN")


class PhysicalWaIrFailbackObjectStoragePublisherFactory(Protocol):
    """Required distinct IR-publisher factory; no normal-direction fallback."""

    def admit_ir_publisher_failback_handoff(
        self,
        *,
        preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> PhysicalWaIrFailbackObjectStoragePublisherAdmission:
        """Mint an opaque fresh IR-publisher-only admission without I/O leak."""

    def require_ir_publisher_failback_handoff_admission(
        self,
        admission: PhysicalWaIrFailbackObjectStoragePublisherAdmission,
        *,
        preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> PhysicalWaIrFailbackObjectStoragePublisherAdmission:
        """Recheck admission freshness without exposing a credential/client."""

    def execute_ir_publisher_failback_handoff(
        self,
        *,
        admission: PhysicalWaIrFailbackObjectStoragePublisherAdmission,
        now: datetime,
        operation: Callable[
            [PhysicalWalObjectStorageClient, PhysicalWaIrFailbackObjectStoragePublisherRoute],
            object,
        ],
    ) -> object:
        """Open only the IR-publisher scope for one synchronous local callback.

        The runtime enforces that ``operation`` is invoked exactly once while
        this method is active and that its exact return object is propagated.
        A factory must neither synthesize a receipt nor retain the callback.
        """


@dataclass(frozen=True)
class RootOwnedWaIrPostgresFailbackUploaderPolicy:
    """One local WAL or base-backup publication policy without a destination."""

    workspace: Path | None = None
    spool_root: Path | None = None
    destination_age_recipient: str = ""
    maximum_plaintext_bytes: int = 0


@dataclass(frozen=True)
class RootOwnedWaIrPostgresFailbackHandoffConfig:
    """Default-off IR→Object Storage→FI publisher configuration.

    No endpoint, bucket, object key, direct host, credential, token, or normal
    FI/IR factory is accepted.  Route details stay inside the distinct
    publisher-factory callback and must match the preflight owned by that
    factory.
    """

    schema: str = PHYSICAL_WA_IR_POSTGRES_FAILBACK_HANDOFF_SCHEMA
    publisher_factory: PhysicalWaIrFailbackObjectStoragePublisherFactory | None = field(
        default=None, repr=False, compare=False
    )
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig | None = field(
        default=None, repr=False, compare=False
    )
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight | None = field(
        default=None, repr=False, compare=False
    )
    age_encryptor_config: PhysicalAgeV1EncryptorConfig | None = field(
        default=None, repr=False, compare=False
    )
    wal_policy: RootOwnedWaIrPostgresFailbackUploaderPolicy | None = field(
        default=None, repr=False, compare=False
    )
    base_backup_policy: RootOwnedWaIrPostgresFailbackUploaderPolicy | None = field(
        default=None, repr=False, compare=False
    )
    enabled: bool = PHYSICAL_WA_IR_POSTGRES_FAILBACK_HANDOFF_DEFAULT_ENABLED
    mode: str = _RUNTIME_MODE
    source_site: str = _SOURCE_SITE
    destination_site: str = _DESTINATION_SITE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class _PolicyFacts:
    workspace: Path
    spool_root: Path
    destination_age_recipient: str
    maximum_plaintext_bytes: int


@dataclass(frozen=True)
class _ConfigFacts:
    publisher_factory: PhysicalWaIrFailbackObjectStoragePublisherFactory
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight
    age_encryptor_config: PhysicalAgeV1EncryptorConfig
    wal_policy: _PolicyFacts | None
    base_backup_policy: _PolicyFacts | None


@dataclass(frozen=True)
class _DescriptorFacts:
    campaign_id: str
    release_sha: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str


def _fail(code: str) -> None:
    raise PhysicalWaIrPostgresFailbackHandoffError(code)


def _is_tombstoned_paired_reverse_factory(value: object) -> bool:
    """Reject the old dual-role implementation without importing it here."""

    try:
        return any(
            base.__module__ == _TOMBSTONED_PAIRED_FACTORY_MODULE
            and base.__name__ == _TOMBSTONED_PAIRED_FACTORY_CLASS
            for base in type(value).__mro__
        )
    except Exception:
        return True


def _safe_path(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    text = str(value)
    if not text or len(text) > _MAX_PATH_LENGTH or _URL_OR_SECRET_RE.search(text) is not None:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _safe_recipient(value: object, *, code: str) -> str:
    if type(value) is not str or AGE_RECIPIENT_RE.fullmatch(value) is None:
        _fail(code)
    if _URL_OR_SECRET_RE.search(value) is not None:
        _fail(code)
    return value


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _policy(value: object, *, kind: str) -> _PolicyFacts:
    if type(value) is not RootOwnedWaIrPostgresFailbackUploaderPolicy:
        _fail("WA_IR_FAILBACK_HANDOFF_POLICY_INVALID")
    workspace = _safe_path(value.workspace, code="WA_IR_FAILBACK_HANDOFF_POLICY_INVALID")
    spool_root = _safe_path(value.spool_root, code="WA_IR_FAILBACK_HANDOFF_POLICY_INVALID")
    if workspace == spool_root or workspace in spool_root.parents or spool_root in workspace.parents:
        _fail("WA_IR_FAILBACK_HANDOFF_POLICY_ROOTS_OVERLAP")
    maximum = _positive(
        value.maximum_plaintext_bytes,
        maximum=min(MAX_PHYSICAL_BASE_BACKUP_BYTES, DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES),
        code="WA_IR_FAILBACK_HANDOFF_POLICY_INVALID",
    )
    if kind == "wal" and maximum not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        _fail("WA_IR_FAILBACK_HANDOFF_WAL_GEOMETRY_INVALID")
    if kind not in {"wal", "base-backup"}:
        _fail("WA_IR_FAILBACK_HANDOFF_POLICY_INVALID")
    return _PolicyFacts(
        workspace=workspace,
        spool_root=spool_root,
        destination_age_recipient=_safe_recipient(
            value.destination_age_recipient,
            code="WA_IR_FAILBACK_HANDOFF_POLICY_INVALID",
        ),
        maximum_plaintext_bytes=maximum,
    )


def _age_config(
    value: object,
    *,
    expected_recipients: tuple[str, ...],
) -> PhysicalAgeV1EncryptorConfig:
    if type(value) is not PhysicalAgeV1EncryptorConfig:
        _fail("WA_IR_FAILBACK_HANDOFF_AGE_CONFIG_INVALID")
    if (
        value.enabled is not True
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
        or _safe_recipient(value.recipient, code="WA_IR_FAILBACK_HANDOFF_AGE_CONFIG_INVALID")
        not in expected_recipients
    ):
        _fail("WA_IR_FAILBACK_HANDOFF_AGE_CONFIG_INVALID")
    _safe_path(value.workspace_root, code="WA_IR_FAILBACK_HANDOFF_AGE_CONFIG_INVALID")
    plaintext = _positive(
        value.maximum_plaintext_bytes,
        maximum=DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES,
        code="WA_IR_FAILBACK_HANDOFF_AGE_CONFIG_INVALID",
    )
    ciphertext = _positive(
        value.maximum_ciphertext_bytes,
        maximum=DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES,
        code="WA_IR_FAILBACK_HANDOFF_AGE_CONFIG_INVALID",
    )
    if ciphertext < plaintext:
        _fail("WA_IR_FAILBACK_HANDOFF_AGE_CONFIG_INVALID")
    return value


def _config(value: object, *, require_enabled: bool) -> _ConfigFacts:
    if type(value) is not RootOwnedWaIrPostgresFailbackHandoffConfig:
        _fail("WA_IR_FAILBACK_HANDOFF_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_WA_IR_POSTGRES_FAILBACK_HANDOFF_SCHEMA
        or type(value.enabled) is not bool
        or value.mode != _RUNTIME_MODE
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
    ):
        _fail("WA_IR_FAILBACK_HANDOFF_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("WA_IR_FAILBACK_HANDOFF_DISABLED")
    factory = value.publisher_factory
    if type(value.preflight_config) is not PhysicalIrToFiObjectStorageFailbackPreflightConfig:
        _fail("WA_IR_FAILBACK_HANDOFF_PREFLIGHT_CONFIG_INVALID")
    if type(value.preflight) is not VerifiedPhysicalIrToFiObjectStorageFailbackPreflight:
        _fail("WA_IR_FAILBACK_HANDOFF_PREFLIGHT_INVALID")
    if _is_tombstoned_paired_reverse_factory(factory):
        _fail("WA_IR_FAILBACK_HANDOFF_LEGACY_PAIRED_FACTORY_FORBIDDEN")
    if factory is None or not all(
        callable(getattr(factory, name, None))
        for name in (
            "admit_ir_publisher_failback_handoff",
            "require_ir_publisher_failback_handoff_admission",
            "execute_ir_publisher_failback_handoff",
        )
    ):
        _fail("WA_IR_FAILBACK_HANDOFF_PUBLISHER_FACTORY_INVALID")
    wal = None if value.wal_policy is None else _policy(value.wal_policy, kind="wal")
    base = None if value.base_backup_policy is None else _policy(value.base_backup_policy, kind="base-backup")
    if wal is None and base is None:
        _fail("WA_IR_FAILBACK_HANDOFF_POLICY_INVALID")
    recipients = tuple(item.destination_age_recipient for item in (wal, base) if item is not None)
    age = _age_config(value.age_encryptor_config, expected_recipients=recipients)
    if any(item != age.recipient for item in recipients):
        _fail("WA_IR_FAILBACK_HANDOFF_AGE_RECIPIENT_MISMATCH")
    return _ConfigFacts(
        publisher_factory=factory,
        preflight_config=value.preflight_config,
        preflight=value.preflight,
        age_encryptor_config=age,
        wal_policy=wal,
        base_backup_policy=base,
    )


def validate_root_owned_wa_ir_postgres_failback_handoff_config(
    config: RootOwnedWaIrPostgresFailbackHandoffConfig,
) -> RootOwnedWaIrPostgresFailbackHandoffConfig:
    """Pure config validation; it opens no factory, credential, client, or path."""

    _config(config, require_enabled=False)
    return config


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("WA_IR_FAILBACK_HANDOFF_ROOT_REQUIRED")
    except OSError:
        _fail("WA_IR_FAILBACK_HANDOFF_ROOT_REQUIRED")


def _term(value: object, *, now: datetime) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    try:
        result = require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("WA_IR_FAILBACK_HANDOFF_TERM_INVALID_OR_STALE")
    if result.holder_site != _SOURCE_SITE:
        _fail("WA_IR_FAILBACK_HANDOFF_TERM_ROUTE_INVALID")
    return result


def build_physical_wa_ir_failback_object_storage_publisher_admission(
    *,
    preflight: object,
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    current_witnessed_term: object,
    now: datetime,
) -> PhysicalWaIrFailbackObjectStoragePublisherAdmission:
    """Mint a local opaque publisher admission after fresh reverse preflight.

    This is deliberately a capability constructor, not a credential factory.
    A concrete IR-publisher factory owns the enabled preflight configuration;
    callers cannot choose a normal FI-to-IR profile or namespace here.
    """

    observed = _utc(now, code="WA_IR_FAILBACK_HANDOFF_CLOCK_INVALID")
    try:
        checked_preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            preflight,
            config=preflight_config,
            now=observed,
        )
    except Exception:
        _fail("WA_IR_FAILBACK_HANDOFF_PREFLIGHT_INVALID_OR_STALE")
    checked_term = _term(current_witnessed_term, now=observed)
    result = PhysicalWaIrFailbackObjectStoragePublisherAdmission(
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
    object.__setattr__(result, "_capability", _PUBLISHER_ADMISSION_CAPABILITY)
    return result


def require_physical_wa_ir_failback_object_storage_publisher_admission(
    value: object,
    *,
    preflight: object,
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    current_witnessed_term: object,
    now: datetime,
) -> PhysicalWaIrFailbackObjectStoragePublisherAdmission:
    """Recheck the opaque admission against the current reverse term/policy."""

    observed = _utc(now, code="WA_IR_FAILBACK_HANDOFF_CLOCK_INVALID")
    if (
        type(value) is not PhysicalWaIrFailbackObjectStoragePublisherAdmission
        or value._capability is not _PUBLISHER_ADMISSION_CAPABILITY
        or _utc(value.admitted_at, code="WA_IR_FAILBACK_HANDOFF_PUBLISHER_ADMISSION_FAILED")
        > observed + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
    ):
        _fail("WA_IR_FAILBACK_HANDOFF_PUBLISHER_ADMISSION_FAILED")
    try:
        checked_preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            preflight,
            config=preflight_config,
            now=observed,
        )
    except Exception:
        _fail("WA_IR_FAILBACK_HANDOFF_PREFLIGHT_INVALID_OR_STALE")
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
        _fail("WA_IR_FAILBACK_HANDOFF_PUBLISHER_ADMISSION_FAILED")
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID")
        result[key] = value
    return result


def _descriptor(
    *,
    descriptor_bytes: object,
    descriptor_sha256: object,
    facts: _ConfigFacts,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    kind: str,
) -> _DescriptorFacts:
    if not isinstance(descriptor_bytes, bytes) or not descriptor_bytes:
        _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID")
    if hashlib.sha256(descriptor_bytes).hexdigest() != _sha256(
        descriptor_sha256,
        code="WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID",
    ):
        _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID")
    try:
        mapping = json.loads(descriptor_bytes.decode("utf-8", "strict"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID")
    if type(mapping) is not dict or canonical_json_bytes(mapping) != descriptor_bytes:
        _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID")
    expected_kind = (
        "physical_wal_segment_handoff"
        if kind == "wal"
        else "physical_postgresql_base_backup_handoff"
    )
    if (
        mapping.get("kind") != expected_kind
        or mapping.get("source_site") != _SOURCE_SITE
        or mapping.get("destination_site") != _DESTINATION_SITE
        or mapping.get("object_storage_namespace")
        != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or mapping.get("campaign_id") != facts.preflight.binding.campaign_id
        or mapping.get("release_sha") != facts.preflight.binding.release_sha
    ):
        _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_BINDING_MISMATCH")
    # This is the source spool's *lineage* binding: it includes the concrete
    # WAL/base-backup facts and Writer term, and therefore cannot equal the
    # provider/identity route binding carried by reverse preflight (nor can a
    # single provider route hash equal both a WAL and a base-backup hash).
    # The two domains are deliberately checked independently: direction,
    # campaign, release, namespace and live term below bind this descriptor to
    # this handoff, while preflight/admission bind the factory-owned provider
    # route.  Do not conflate the two hashes.
    descriptor_route_binding_sha256 = _sha256(
        mapping.get("route_binding_sha256"),
        code="WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID",
    )
    writer = mapping.get("writer_term")
    if type(writer) is not dict:
        _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID")
    if kind == "wal":
        expected_writer_fields = {
            "holder_site",
            "writer_epoch",
            "writer_lease_id",
            "witnessed_term_proof_sha256",
        }
        epoch = writer.get("writer_epoch")
        lease = writer.get("writer_lease_id")
        transition = term.witness_transition_id
    else:
        expected_writer_fields = {
            "holder_site",
            "epoch",
            "lease_id",
            "witness_transition_id",
            "witnessed_term_proof_sha256",
        }
        epoch = writer.get("epoch")
        lease = writer.get("lease_id")
        transition = writer.get("witness_transition_id")
    if set(writer) != expected_writer_fields:
        _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_INVALID")
    if (
        writer.get("holder_site") != _SOURCE_SITE
        or epoch != term.writer_epoch
        or lease != term.writer_lease_id
        or writer.get("witnessed_term_proof_sha256") != term.proof_sha256
        or transition != term.witness_transition_id
    ):
        _fail("WA_IR_FAILBACK_HANDOFF_DESCRIPTOR_TERM_MISMATCH")
    return _DescriptorFacts(
        campaign_id=facts.preflight.binding.campaign_id,
        release_sha=facts.preflight.binding.release_sha,
        route_binding_sha256=descriptor_route_binding_sha256,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witnessed_term_proof_sha256=term.proof_sha256,
    )


class _BoundUploader:
    """One type- and term-bound uploader surfaced only by this runtime."""

    def __init__(
        self,
        owner: "RootOwnedWaIrPostgresFailbackHandoff",
        *,
        kind: str,
        term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    ) -> None:
        self._owner = owner
        self._kind = kind
        self._term = term

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt | PhysicalWalBaseBackupUploadReceipt:
        return self._owner._publish(
            kind=self._kind,
            term=self._term,
            snapshot_path=snapshot_path,
            descriptor_bytes=descriptor_bytes,
            descriptor_sha256=descriptor_sha256,
        )


class RootOwnedWaIrPostgresFailbackHandoff:
    """IR publisher-only handoff with no normal-route object-store dependency."""

    def __init__(
        self,
        config: RootOwnedWaIrPostgresFailbackHandoffConfig,
        *,
        clock: Callable[[], datetime] | None,
        age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor] | None = None,
    ) -> None:
        self._config = validate_root_owned_wa_ir_postgres_failback_handoff_config(config)
        self._clock = clock
        self._age_encryptor_factory = age_encryptor_factory

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("WA_IR_FAILBACK_HANDOFF_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="WA_IR_FAILBACK_HANDOFF_CLOCK_INVALID")
        except PhysicalWaIrPostgresFailbackHandoffError:
            raise
        except Exception:
            _fail("WA_IR_FAILBACK_HANDOFF_CLOCK_INVALID")

    def wal_uploader(self, *, current_witnessed_term: object) -> _BoundUploader:
        """Return a single IR-term-bound WAL uploader; construction is inert."""

        observed = self._now()
        _require_root()
        _config(self._config, require_enabled=True)
        return _BoundUploader(self, kind="wal", term=_term(current_witnessed_term, now=observed))

    def base_backup_uploader(self, *, current_witnessed_term: object) -> _BoundUploader:
        """Return a single IR-term-bound base-backup uploader; no capture occurs."""

        observed = self._now()
        _require_root()
        _config(self._config, require_enabled=True)
        return _BoundUploader(self, kind="base-backup", term=_term(current_witnessed_term, now=observed))

    def _new_age_encryptor(self, facts: _ConfigFacts) -> PhysicalWalAgeEncryptor:
        if self._age_encryptor_factory is None:
            return PhysicalAgeV1Encryptor(facts.age_encryptor_config)
        if not callable(self._age_encryptor_factory):
            _fail("WA_IR_FAILBACK_HANDOFF_AGE_FACTORY_INVALID")
        try:
            encryptor = self._age_encryptor_factory()
        except Exception:
            _fail("WA_IR_FAILBACK_HANDOFF_AGE_FACTORY_INVALID")
        if not callable(getattr(encryptor, "encrypt", None)):
            _fail("WA_IR_FAILBACK_HANDOFF_AGE_FACTORY_INVALID")
        return encryptor

    def _publish(
        self,
        *,
        kind: str,
        term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt | PhysicalWalBaseBackupUploadReceipt:
        _require_root()
        started = self._now()
        facts = _config(self._config, require_enabled=True)
        current_term = _term(term, now=started)
        policy = facts.wal_policy if kind == "wal" else facts.base_backup_policy
        if policy is None or kind not in {"wal", "base-backup"}:
            _fail("WA_IR_FAILBACK_HANDOFF_POLICY_INVALID")
        try:
            preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
                facts.preflight,
                config=facts.preflight_config,
                now=started,
            )
        except Exception:
            _fail("WA_IR_FAILBACK_HANDOFF_PREFLIGHT_INVALID_OR_STALE")
        _descriptor(
            descriptor_bytes=descriptor_bytes,
            descriptor_sha256=descriptor_sha256,
            facts=facts,
            term=current_term,
            kind=kind,
        )
        try:
            admission = facts.publisher_factory.admit_ir_publisher_failback_handoff(
                preflight=preflight,
                current_witnessed_term=current_term,
                now=started,
            )
            admission = require_physical_wa_ir_failback_object_storage_publisher_admission(
                admission,
                preflight=preflight,
                preflight_config=facts.preflight_config,
                current_witnessed_term=current_term,
                now=started,
            )
            admission = facts.publisher_factory.require_ir_publisher_failback_handoff_admission(
                admission,
                preflight=preflight,
                current_witnessed_term=current_term,
                now=started,
            )
            admission = require_physical_wa_ir_failback_object_storage_publisher_admission(
                admission,
                preflight=preflight,
                preflight_config=facts.preflight_config,
                current_witnessed_term=current_term,
                now=started,
            )
        except Exception:
            _fail("WA_IR_FAILBACK_HANDOFF_PUBLISHER_ADMISSION_FAILED")

        expected_receipt_type = (
            PhysicalWalArchiveUploadReceipt
            if kind == "wal"
            else PhysicalWalBaseBackupUploadReceipt
        )
        callback_active = True
        callback_called = False
        callback_invalidated = False
        callback_result: PhysicalWalArchiveUploadReceipt | PhysicalWalBaseBackupUploadReceipt | None = None

        def operation(
            client: PhysicalWalObjectStorageClient,
            route: PhysicalWaIrFailbackObjectStoragePublisherRoute,
        ) -> PhysicalWalArchiveUploadReceipt | PhysicalWalBaseBackupUploadReceipt:
            nonlocal callback_called, callback_invalidated, callback_result
            if not callback_active or callback_called:
                callback_invalidated = True
                _fail("WA_IR_FAILBACK_HANDOFF_FACTORY_CALLBACK_INVALID")
            callback_called = True
            if (
                type(route) is not PhysicalWaIrFailbackObjectStoragePublisherRoute
                or type(route.bucket) is not str
                or type(route.region) is not str
                or not route.bucket
                or not route.region
                or route.object_storage_namespace
                != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
            ):
                _fail("WA_IR_FAILBACK_HANDOFF_FACTORY_ROUTE_INVALID")
            uploader_config = PhysicalWalObjectStorageUploaderConfig(
                source_site=_SOURCE_SITE,
                destination_site=_DESTINATION_SITE,
                workspace=policy.workspace,
                spool_root=policy.spool_root,
                spool_owner_uid=0,
                bucket=route.bucket,
                region=route.region,
                destination_age_recipient=policy.destination_age_recipient,
                object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
                enabled=True,
                maximum_plaintext_bytes=policy.maximum_plaintext_bytes,
                direct_site_control="forbidden",
                destination_object_ingest="pull-only",
            )
            encryptor = self._new_age_encryptor(facts)
            uploader = (
                PhysicalWalObjectStorageUploader(
                    config=uploader_config,
                    age_encryptor_factory=lambda: encryptor,
                    client_factory=lambda: client,
                )
                if kind == "wal"
                else PhysicalWalBaseBackupObjectStorageUploader(
                    config=uploader_config,
                    age_encryptor_factory=lambda: encryptor,
                    client_factory=lambda: client,
                )
            )
            receipt = uploader.upload(
                snapshot_path=snapshot_path,
                descriptor_bytes=descriptor_bytes,
                descriptor_sha256=descriptor_sha256,
            )
            if type(receipt) is not expected_receipt_type:
                callback_invalidated = True
                _fail("WA_IR_FAILBACK_HANDOFF_FACTORY_CALLBACK_INVALID")
            callback_result = receipt
            return receipt

        try:
            result = facts.publisher_factory.execute_ir_publisher_failback_handoff(
                admission=admission,
                now=started,
                operation=operation,
            )
        except PhysicalWaIrPostgresFailbackHandoffError:
            raise
        except Exception:
            _fail("WA_IR_FAILBACK_HANDOFF_PUBLISH_FAILED")
        finally:
            callback_active = False
        if (
            callback_invalidated
            or not callback_called
            or callback_result is None
            or result is not callback_result
            or type(result) is not expected_receipt_type
        ):
            _fail("WA_IR_FAILBACK_HANDOFF_FACTORY_CALLBACK_INVALID")
        completed = self._now()
        if completed < started:
            _fail("WA_IR_FAILBACK_HANDOFF_CLOCK_INVALID")
        final_term = _term(term, now=completed)
        if final_term != current_term:
            _fail("WA_IR_FAILBACK_HANDOFF_TERM_CHANGED")
        try:
            final_preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
                facts.preflight,
                config=facts.preflight_config,
                now=completed,
            )
            admission = require_physical_wa_ir_failback_object_storage_publisher_admission(
                admission,
                preflight=final_preflight,
                preflight_config=facts.preflight_config,
                current_witnessed_term=final_term,
                now=completed,
            )
            admission = facts.publisher_factory.require_ir_publisher_failback_handoff_admission(
                admission,
                preflight=final_preflight,
                current_witnessed_term=final_term,
                now=completed,
            )
            require_physical_wa_ir_failback_object_storage_publisher_admission(
                admission,
                preflight=final_preflight,
                preflight_config=facts.preflight_config,
                current_witnessed_term=final_term,
                now=completed,
            )
        except PhysicalWaIrPostgresFailbackHandoffError:
            raise
        except Exception:
            _fail("WA_IR_FAILBACK_HANDOFF_POST_PUBLISH_RECHECK_FAILED")
        return result
