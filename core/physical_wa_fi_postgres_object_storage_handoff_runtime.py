"""Root-only FI PostgreSQL recovery-material Object-Storage handoff.

This module is the missing concrete runtime boundary between the existing
local PostgreSQL recovery spools and their injected encrypted Object-Storage
uploader protocols.  It deliberately owns no PostgreSQL command, Docker,
SSH, network route, release, promotion, writer, or Full-Matrix action.

When explicitly enabled by a root-owned caller, it can do exactly two things:

* hand the typed output of ``physical_wa_fi_postgres_helper_capture_bridge``
  to the existing base-backup spool; or
* provide the exact uploader protocol consumed by the existing WAL spool.

For either object kind, the existing uploader remains the sole implementation
of age-v1 encryption, private/versioned bucket checks, create-only PUT, exact
VersionId history, HEAD, and streamed GET read-back.  This runtime only
constructs that uploader inside the dedicated FI-publisher-only factory's
narrow callback.  The callback has no endpoint/bucket/key input: all route
facts are factory-owned, the object key comes only from a canonical spool
descriptor, and only the FI credential is reopened after a fresh paired
preflight admission.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import re

from core import physical_arvan_immutability_preflight as _preflight
from core import physical_arvan_s3_fi_publisher_role_factory as _fi_publisher_factory
from core import physical_wa_fi_postgres_helper_capture_bridge as _helper_bridge
from core.physical_age_v1_adapter import (
    DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES,
    DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES,
    PhysicalAgeV1Encryptor,
    PhysicalAgeV1EncryptorConfig,
)
from core.physical_wal_archive_spool import PhysicalWalArchiveUploadReceipt
from core.physical_wal_base_backup_spool import (
    DEFAULT_SPOOL_RESERVE_BYTES,
    MAX_PHYSICAL_BASE_BACKUP_BYTES,
    PhysicalWalBaseBackupUploadReceipt,
    PhysicalWalBaseBackupSpoolConfig,
    PhysicalWalBaseBackupSpoolResult,
    capture_physical_wal_base_backup,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
)
from core.physical_wal_object_storage_uploader import (
    PhysicalWalAgeEncryptor,
    PhysicalWalBaseBackupObjectStorageUploader,
    PhysicalWalObjectStorageClient,
    PhysicalWalObjectStorageUploader,
    PhysicalWalObjectStorageUploaderConfig,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE


__all__ = (
    "PHYSICAL_WA_FI_POSTGRES_OBJECT_STORAGE_HANDOFF_DEFAULT_ENABLED",
    "PHYSICAL_WA_FI_POSTGRES_OBJECT_STORAGE_HANDOFF_SCHEMA",
    "PhysicalWaFiPostgresObjectStorageHandoffError",
    "RootOwnedWaFiPostgresObjectStorageHandoff",
    "RootOwnedWaFiPostgresObjectStorageHandoffConfig",
    "RootOwnedWaFiPostgresObjectStorageUploaderPolicy",
    "validate_root_owned_wa_fi_postgres_object_storage_handoff_config",
)


PHYSICAL_WA_FI_POSTGRES_OBJECT_STORAGE_HANDOFF_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-object-storage-handoff-runtime-v1"
)
PHYSICAL_WA_FI_POSTGRES_OBJECT_STORAGE_HANDOFF_DEFAULT_ENABLED = False

_RUNTIME_MODE = "root-owned-fi-publisher-age-v1-versioned-create-only-v1"
_SOURCE_SITE = "webapp_fi"
_DESTINATION_SITE = "webapp_ir"
_MAX_PATH_LENGTH = 4096
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)


class PhysicalWaFiPostgresObjectStorageHandoffError(RuntimeError):
    """Fixed-code failure for this non-authorizing local handoff boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedWaFiPostgresObjectStorageUploaderPolicy:
    """Local-only policy for one typed WAL or base-backup uploader.

    There is deliberately no endpoint, bucket, object key, credential, host,
    URL, shell command, or destination control selector here.  The factory
    injects the bucket/region only inside one synchronous FI-only callback;
    the existing spool derives the object key from its canonical descriptor.
    """

    workspace: Path | None = None
    spool_root: Path | None = None
    destination_age_recipient: str = ""
    maximum_plaintext_bytes: int = 0


@dataclass(frozen=True)
class RootOwnedWaFiPostgresObjectStorageHandoffConfig:
    """Default-off policy for FI → private Object Storage → IR pull-only.

    ``preflight`` is opaque verified evidence from the paired FI/IR immutable
    bucket probe.  It is rechecked immediately before and after every upload;
    it does not grant release, promotion, writer, or Full-Matrix authority.
    """

    schema: str = PHYSICAL_WA_FI_POSTGRES_OBJECT_STORAGE_HANDOFF_SCHEMA
    fi_publisher_factory: _fi_publisher_factory.RootOwnedArvanS3FiPublisherRoleFactory | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    age_encryptor_config: PhysicalAgeV1EncryptorConfig | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    wal_policy: RootOwnedWaFiPostgresObjectStorageUploaderPolicy | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    base_backup_policy: RootOwnedWaFiPostgresObjectStorageUploaderPolicy | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    base_backup_spool_reserve_bytes: int = DEFAULT_SPOOL_RESERVE_BYTES
    enabled: bool = PHYSICAL_WA_FI_POSTGRES_OBJECT_STORAGE_HANDOFF_DEFAULT_ENABLED
    mode: str = _RUNTIME_MODE
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
    fi_publisher_factory: _fi_publisher_factory.RootOwnedArvanS3FiPublisherRoleFactory
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    age_encryptor_config: PhysicalAgeV1EncryptorConfig
    wal_policy: _PolicyFacts | None
    base_backup_policy: _PolicyFacts | None
    base_backup_spool_reserve_bytes: int


def _fail(code: str) -> None:
    raise PhysicalWaFiPostgresObjectStorageHandoffError(code)


def _safe_path(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    text = str(value)
    if not text or len(text) > _MAX_PATH_LENGTH or _URL_OR_SECRET_RE.search(text) is not None:
        _fail(code)
    return value


def _safe_recipient(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or AGE_RECIPIENT_RE.fullmatch(value) is None
        or _URL_OR_SECRET_RE.search(value) is not None
    ):
        _fail(code)
    return value


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _policy_facts(
    value: object,
    *,
    kind: str,
) -> _PolicyFacts:
    if type(value) is not RootOwnedWaFiPostgresObjectStorageUploaderPolicy:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_INVALID")
    workspace = _safe_path(value.workspace, code="WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_INVALID")
    spool_root = _safe_path(value.spool_root, code="WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_INVALID")
    if workspace == spool_root or workspace in spool_root.parents or spool_root in workspace.parents:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_ROOTS_OVERLAP")
    recipient = _safe_recipient(
        value.destination_age_recipient,
        code="WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_INVALID",
    )
    maximum = _positive(
        value.maximum_plaintext_bytes,
        maximum=min(MAX_PHYSICAL_BASE_BACKUP_BYTES, DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES),
        code="WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_INVALID",
    )
    if kind == "wal":
        if maximum not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_WAL_GEOMETRY_INVALID")
    elif kind != "base-backup":
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_INVALID")
    return _PolicyFacts(
        workspace=workspace,
        spool_root=spool_root,
        destination_age_recipient=recipient,
        maximum_plaintext_bytes=maximum,
    )


def _age_config_facts(
    value: object,
    *,
    expected_recipients: tuple[str, ...],
) -> PhysicalAgeV1EncryptorConfig:
    if type(value) is not PhysicalAgeV1EncryptorConfig:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_AGE_CONFIG_INVALID")
    if (
        value.enabled is not True
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
        or _safe_recipient(value.recipient, code="WA_FI_OBJECT_STORAGE_HANDOFF_AGE_CONFIG_INVALID")
        not in expected_recipients
    ):
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_AGE_CONFIG_INVALID")
    _safe_path(value.workspace_root, code="WA_FI_OBJECT_STORAGE_HANDOFF_AGE_CONFIG_INVALID")
    maximum_plaintext = _positive(
        value.maximum_plaintext_bytes,
        maximum=DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES,
        code="WA_FI_OBJECT_STORAGE_HANDOFF_AGE_CONFIG_INVALID",
    )
    maximum_ciphertext = _positive(
        value.maximum_ciphertext_bytes,
        maximum=DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES,
        code="WA_FI_OBJECT_STORAGE_HANDOFF_AGE_CONFIG_INVALID",
    )
    if maximum_ciphertext < maximum_plaintext:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_AGE_CONFIG_INVALID")
    return value


def _config_facts(
    value: object,
    *,
    require_enabled: bool,
) -> _ConfigFacts:
    if type(value) is not RootOwnedWaFiPostgresObjectStorageHandoffConfig:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_WA_FI_POSTGRES_OBJECT_STORAGE_HANDOFF_SCHEMA
        or type(value.enabled) is not bool
        or value.mode != _RUNTIME_MODE
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
    ):
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_DISABLED")
    if (
        type(value.fi_publisher_factory)
        is not _fi_publisher_factory.RootOwnedArvanS3FiPublisherRoleFactory
    ):
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_FACTORY_INVALID")
    if type(value.preflight) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_PREFLIGHT_INVALID")
    wal_policy = (
        None if value.wal_policy is None else _policy_facts(value.wal_policy, kind="wal")
    )
    base_policy = (
        None
        if value.base_backup_policy is None
        else _policy_facts(value.base_backup_policy, kind="base-backup")
    )
    if wal_policy is None and base_policy is None:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_INVALID")
    recipients = tuple(
        policy.destination_age_recipient for policy in (wal_policy, base_policy) if policy is not None
    )
    age_config = _age_config_facts(value.age_encryptor_config, expected_recipients=recipients)
    if any(recipient != age_config.recipient for recipient in recipients):
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_AGE_RECIPIENT_MISMATCH")
    reserve = _positive(
        value.base_backup_spool_reserve_bytes,
        maximum=MAX_PHYSICAL_BASE_BACKUP_BYTES,
        code="WA_FI_OBJECT_STORAGE_HANDOFF_CONFIG_INVALID",
    )
    if base_policy is not None and reserve > base_policy.maximum_plaintext_bytes:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_CONFIG_INVALID")
    return _ConfigFacts(
        fi_publisher_factory=value.fi_publisher_factory,
        preflight=value.preflight,
        age_encryptor_config=age_config,
        wal_policy=wal_policy,
        base_backup_policy=base_policy,
        base_backup_spool_reserve_bytes=reserve,
    )


def validate_root_owned_wa_fi_postgres_object_storage_handoff_config(
    config: RootOwnedWaFiPostgresObjectStorageHandoffConfig,
) -> RootOwnedWaFiPostgresObjectStorageHandoffConfig:
    """Pure configuration validation; it opens no file, client, or socket."""

    facts = _config_facts(config, require_enabled=False)
    return RootOwnedWaFiPostgresObjectStorageHandoffConfig(
        schema=PHYSICAL_WA_FI_POSTGRES_OBJECT_STORAGE_HANDOFF_SCHEMA,
        fi_publisher_factory=facts.fi_publisher_factory,
        preflight=facts.preflight,
        age_encryptor_config=facts.age_encryptor_config,
        wal_policy=config.wal_policy,
        base_backup_policy=config.base_backup_policy,
        base_backup_spool_reserve_bytes=facts.base_backup_spool_reserve_bytes,
        enabled=config.enabled,
        mode=_RUNTIME_MODE,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_ROOT_REQUIRED")
    except OSError:
        _fail("WA_FI_OBJECT_STORAGE_HANDOFF_ROOT_REQUIRED")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


class _RootOwnedWaFiPhysicalRecoveryUploader:
    """One kind-specific uploader protocol backed by the FI-only factory call."""

    def __init__(self, owner: "RootOwnedWaFiPostgresObjectStorageHandoff", *, kind: str) -> None:
        self._owner = owner
        self._kind = kind

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt | PhysicalWalBaseBackupUploadReceipt:
        return self._owner._publish(
            kind=self._kind,
            snapshot_path=snapshot_path,
            descriptor_bytes=descriptor_bytes,
            descriptor_sha256=descriptor_sha256,
        )


class RootOwnedWaFiPostgresObjectStorageHandoff:
    """Explicit root adapter for typed WA-FI WAL/base-backup spools.

    Construction is inert.  The supplied clock and optional age factory are
    dependency-injection seams for the root-owned runtime/test harness; they
    are never read from environment variables or a caller-selected module.
    The default age path is the existing pinned ``/usr/bin/age`` adapter.
    """

    def __init__(
        self,
        config: RootOwnedWaFiPostgresObjectStorageHandoffConfig,
        *,
        clock: Callable[[], datetime] | None,
        age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor] | None = None,
    ) -> None:
        self._config = validate_root_owned_wa_fi_postgres_object_storage_handoff_config(config)
        self._clock = clock
        self._age_encryptor_factory = age_encryptor_factory

    def wal_uploader(self) -> _RootOwnedWaFiPhysicalRecoveryUploader:
        """Return the fixed WAL-only uploader protocol; no I/O occurs yet."""

        return _RootOwnedWaFiPhysicalRecoveryUploader(self, kind="wal")

    def base_backup_uploader(self) -> _RootOwnedWaFiPhysicalRecoveryUploader:
        """Return the fixed base-backup-only uploader; no I/O occurs yet."""

        return _RootOwnedWaFiPhysicalRecoveryUploader(self, kind="base-backup")

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="WA_FI_OBJECT_STORAGE_HANDOFF_CLOCK_INVALID")
        except PhysicalWaFiPostgresObjectStorageHandoffError:
            raise
        except Exception:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_CLOCK_INVALID")

    def _new_age_encryptor(self, facts: _ConfigFacts) -> PhysicalWalAgeEncryptor:
        if self._age_encryptor_factory is None:
            return PhysicalAgeV1Encryptor(facts.age_encryptor_config)
        if not callable(self._age_encryptor_factory):
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_AGE_FACTORY_INVALID")
        try:
            encryptor = self._age_encryptor_factory()
        except Exception:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_AGE_FACTORY_INVALID")
        if not callable(getattr(encryptor, "encrypt", None)):
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_AGE_FACTORY_INVALID")
        return encryptor

    def _publish(
        self,
        *,
        kind: str,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt | PhysicalWalBaseBackupUploadReceipt:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        policy = facts.wal_policy if kind == "wal" else facts.base_backup_policy
        if policy is None or kind not in {"wal", "base-backup"}:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_POLICY_INVALID")
        started_at = self._now()
        try:
            admission = facts.fi_publisher_factory.admit_fi_publisher_recovery_handoff(
                preflight=facts.preflight,
                now=started_at,
            )
        except Exception:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_PREFLIGHT_ADMISSION_FAILED")

        def _operation(
            client: PhysicalWalObjectStorageClient,
            route: object,
        ) -> PhysicalWalArchiveUploadReceipt | PhysicalWalBaseBackupUploadReceipt:
            bucket = getattr(route, "bucket", None)
            region = getattr(route, "region", None)
            if type(bucket) is not str or type(region) is not str:
                _fail("WA_FI_OBJECT_STORAGE_HANDOFF_FACTORY_ROUTE_INVALID")
            uploader_config = PhysicalWalObjectStorageUploaderConfig(
                source_site=_SOURCE_SITE,
                destination_site=_DESTINATION_SITE,
                workspace=policy.workspace,
                spool_root=policy.spool_root,
                spool_owner_uid=0,
                bucket=bucket,
                region=region,
                destination_age_recipient=policy.destination_age_recipient,
                object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
                enabled=True,
                maximum_plaintext_bytes=policy.maximum_plaintext_bytes,
                direct_site_control="forbidden",
                destination_object_ingest="pull-only",
            )
            encryptor = self._new_age_encryptor(facts)
            if kind == "wal":
                uploader = PhysicalWalObjectStorageUploader(
                    config=uploader_config,
                    age_encryptor_factory=lambda: encryptor,
                    client_factory=lambda: client,
                )
            else:
                uploader = PhysicalWalBaseBackupObjectStorageUploader(
                    config=uploader_config,
                    age_encryptor_factory=lambda: encryptor,
                    client_factory=lambda: client,
                )
            return uploader.upload(
                snapshot_path=snapshot_path,
                descriptor_bytes=descriptor_bytes,
                descriptor_sha256=descriptor_sha256,
            )

        try:
            receipt = facts.fi_publisher_factory.execute_fi_publisher_recovery_handoff(
                admission=admission,
                now=started_at,
                operation=_operation,
            )
        except Exception:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_PUBLISH_FAILED")
        expected_type = (
            PhysicalWalArchiveUploadReceipt
            if kind == "wal"
            else PhysicalWalBaseBackupUploadReceipt
        )
        if type(receipt) is not expected_type:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_RECEIPT_INVALID")
        completed_at = self._now()
        if completed_at < started_at:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_CLOCK_INVALID")
        try:
            facts.fi_publisher_factory.require_fi_publisher_recovery_handoff_admission(
                admission,
                now=completed_at,
            )
        except Exception:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_POST_PUBLISH_PREFLIGHT_FAILED")
        return receipt

    def publish_helper_base_backup(
        self,
        *,
        handoff: _helper_bridge.PhysicalWaFiPostgresHelperCaptureBridgeHandoff,
        now: datetime,
        term_recheck_clock: Callable[[], datetime] | None,
    ) -> PhysicalWalBaseBackupSpoolResult:
        """Spool and publish only a verified typed helper base-backup handoff.

        The helper capability supplies the source root and base-backup binding;
        callers cannot substitute an arbitrary source file.  The existing
        base-backup spool creates the immutable local completion record only
        after the encrypted create-only exact-version readback receipt.
        """

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        if facts.base_backup_policy is None:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_BASE_BACKUP_POLICY_REQUIRED")
        observed = _utc(now, code="WA_FI_OBJECT_STORAGE_HANDOFF_CLOCK_INVALID")
        if term_recheck_clock is None or not callable(term_recheck_clock):
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_TERM_CLOCK_REQUIRED")
        try:
            verified_handoff = _helper_bridge.require_physical_wa_fi_postgres_helper_capture_bridge_handoff(
                handoff,
                now=observed,
            )
        except Exception:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_HELPER_HANDOFF_INVALID")
        try:
            result = capture_physical_wal_base_backup(
                config=PhysicalWalBaseBackupSpoolConfig(
                    source_root=verified_handoff.capture_source_root,
                    spool_root=facts.base_backup_policy.spool_root,
                    maximum_base_backup_bytes=facts.base_backup_policy.maximum_plaintext_bytes,
                    spool_reserve_bytes=facts.base_backup_spool_reserve_bytes,
                ),
                verified_binding=verified_handoff.verified_base_backup_binding,
                uploader=self.base_backup_uploader(),
                now=observed,
                term_recheck_clock=term_recheck_clock,
            )
        except Exception:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_BASE_BACKUP_PUBLISH_FAILED")
        completion = self._now()
        if completion < observed:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_CLOCK_INVALID")
        try:
            _helper_bridge.require_physical_wa_fi_postgres_helper_capture_bridge_handoff(
                verified_handoff,
                now=completion,
            )
        except Exception:
            _fail("WA_FI_OBJECT_STORAGE_HANDOFF_HELPER_HANDOFF_POST_PUBLISH_INVALID")
        return result
