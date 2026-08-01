"""Fail-closed renderer contract for the physical PostgreSQL deployment unit.

This is deliberately a *renderer*, not a deployment controller.  It cannot
open a socket, execute Docker, contact Object Storage, inspect PostgreSQL, or
change a volume.  The only thing it can mint is a set of default-off files
after a caller has supplied a canonical, root-controlled manifest and a
separate local inspection has proved that every required adapter binary and
installation attestation is present.

The intended normal direction is WA-FI primary to WA-IR archive-recovery
standby.  PostgreSQL streaming replication, ``primary_conninfo``, SSH, SCP,
and FI-to-IR database control are deliberately excluded.  In particular, a
``strict_zero_loss`` profile binds an eventual reviewed Object-Storage
pull-plane durable/replay acknowledgement adapter; native PostgreSQL
``remote_apply`` is incompatible with this route and must not be inferred.

The output is therefore not a start capability.  A future root-only execution
coordinator must still validate fresh Witness/term state, live adapter
attestations, physical base/WAL/blob continuity, and the selected writer
admission policy before it can launch anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Protocol


__all__ = (
    "ADAPTER_BINARY_NAMES",
    "ADAPTER_CONTRACTS",
    "ADAPTER_KINDS",
    "AdapterInstallation",
    "AdapterInstallationInspector",
    "PHYSICAL_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT_SCHEMA",
    "PhysicalPostgresDeploymentError",
    "PhysicalPostgresDeploymentManifest",
    "RenderedPhysicalPostgresDeployment",
    "VerifiedPhysicalPostgresAdapterInstallations",
    "canonical_json_bytes",
    "parse_physical_postgres_deployment_manifest",
    "render_physical_postgres_deployment",
    "validate_physical_postgres_deployment_manifest",
    "verify_physical_postgres_adapter_installations",
)


PHYSICAL_POSTGRES_DEPLOYMENT_MANIFEST_SCHEMA = (
    "gold-trade-physical-postgres-deployment-manifest-v1"
)
PHYSICAL_POSTGRES_RENDER_LOCK_SCHEMA = "gold-trade-physical-postgres-render-lock-v1"
PHYSICAL_POSTGRES_ADAPTER_DESCRIPTOR_SCHEMA = (
    "gold-trade-physical-postgres-adapter-descriptor-v1"
)
PHYSICAL_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT_SCHEMA = (
    "gold-trade-physical-postgres-local-base-backup-auth-preflight-v1"
)
PHYSICAL_POSTGRES_DEFAULT_OFF_STATUS = "default-off-not-launch-authorized"

PROFILE_STRICT_ZERO_LOSS = "strict_zero_loss"
PROFILE_BOUNDED_RPO_ARCHIVE = "bounded_rpo_archive"
ACK_MODE_STRICT_REMOTE_DURABLE_REPLAY = "strict_remote_durable_replay"
ACK_MODE_BOUNDED_RPO_ARCHIVE = "bounded_rpo_archive"

ADAPTER_KINDS = (
    "primary_term_guard",
    "wal_spool",
    "wal_uploader",
    "writer_ack",
    "standby_bootstrap",
    "standby_pull",
    "standby_reverse_wal_spool",
    "standby_reverse_wal_uploader",
)
ADAPTER_BINARY_NAMES: Mapping[str, str] = {
    "primary_term_guard": "primary-term-guard",
    "wal_spool": "wal-spool",
    "wal_uploader": "wal-uploader",
    "writer_ack": "writer-ack",
    "standby_bootstrap": "standby-bootstrap",
    "standby_pull": "wal-pull-agent",
    "standby_reverse_wal_spool": "reverse-wal-spool",
    "standby_reverse_wal_uploader": "reverse-wal-uploader",
}
ADAPTER_CONTRACTS: Mapping[str, str] = {
    "primary_term_guard": "gold-trade-physical-postgres-primary-term-guard-v1",
    "wal_spool": "gold-trade-physical-postgres-wal-spool-v1",
    "wal_uploader": "gold-trade-physical-postgres-wal-uploader-v1",
    "writer_ack": "gold-trade-physical-postgres-writer-ack-v1",
    "standby_bootstrap": "gold-trade-physical-postgres-standby-bootstrap-v1",
    "standby_pull": "gold-trade-physical-postgres-wal-pull-agent-v1",
    "standby_reverse_wal_spool": "gold-trade-physical-postgres-reverse-wal-spool-v1",
    "standby_reverse_wal_uploader": "gold-trade-physical-postgres-reverse-wal-uploader-v1",
}
_ADAPTER_SITE: Mapping[str, str] = {
    "primary_term_guard": "webapp_fi",
    "wal_spool": "webapp_fi",
    "wal_uploader": "webapp_fi",
    "writer_ack": "webapp_fi",
    "standby_bootstrap": "webapp_ir",
    "standby_pull": "webapp_ir",
    "standby_reverse_wal_spool": "webapp_ir",
    "standby_reverse_wal_uploader": "webapp_ir",
}

_ADAPTER_ROOT = "/opt/trading-bot/physical-postgres/adapters"
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "mode",
        "campaign_id",
        "release_sha",
    "postgres_image",
    "postgres_major",
    "postgres_runtime_identity",
        "deployment_profile",
        "baseline",
        "writer_term",
        "route",
    "primary",
    "standby",
    "adapters",
}
)
_BASELINE_FIELDS = frozenset(
    {
        "base_generation_id",
        "timeline",
        "consistent_wal_lsn",
        "base_backup_object_key",
        "base_backup_object_version_id",
        "base_backup_ciphertext_sha256",
        "base_backup_plaintext_sha256",
    }
)
_WRITER_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "term_proof_sha256",
    }
)
_ROUTE_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "delivery_route",
        "route_binding_sha256",
        "direct_fi_to_ir_ssh",
        "direct_fi_to_ir_scp",
        "direct_fi_to_ir_postgres_control",
    }
)
_PRIMARY_FIELDS = frozenset(
    {
        "site",
        "postgres_data_volume",
        "postgres_socket_volume",
        "wal_spool_volume",
        "adapter_state_volume",
        "runtime_network_name",
        "local_base_backup",
    }
)
_LOCAL_BASE_BACKUP_FIELDS = frozenset(
    {
        "transport",
        "socket_directory",
        "port",
        "replication_role",
        "peer_os_users",
        "max_wal_senders",
        "tcp_hba",
        "helper_execution",
    }
)
_POSTGRES_RUNTIME_IDENTITY_FIELDS = frozenset(
    {"image_digest", "platform", "effective_uid", "effective_gid", "attestation_sha256"}
)
_STANDBY_FIELDS = frozenset(
    {
        "site",
        "postgres_data_volume",
        "restore_spool_volume",
        "receiver_state_volume",
        "runtime_network_name",
    }
)
_ADAPTER_COMMON_FIELDS = frozenset(
    {
        "adapter_id",
        "site",
        "contract",
        "binary_path",
        "binary_sha256",
        "contract_sha256",
        "installation_attestation_sha256",
        "route_binding_sha256",
    }
)
_STRICT_ACK_FIELDS = _ADAPTER_COMMON_FIELDS | frozenset(
    {
        "acknowledgement_mode",
        "strict_remote_durable_replay_identity_sha256",
        "writer_admission_integration_sha256",
    }
)
_BOUNDED_ACK_FIELDS = _ADAPTER_COMMON_FIELDS | frozenset(
    {
        "acknowledgement_mode",
        "maximum_rpo_seconds",
        "writer_admission_integration_sha256",
    }
)

_CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,119}$", re.ASCII)
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$", re.ASCII)
_VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$", re.ASCII)
_NETWORK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$", re.ASCII)
_OBJECT_KEY_RE = re.compile(
    r"^physical-postgres/[a-z0-9][a-z0-9._/-]{7,511}$", re.ASCII
)
_POSTGRES_IMAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/-]{1,255}@sha256:[0-9a-f]{64}$", re.ASCII
)
_LOCAL_BASE_BACKUP_PEER_MAP = "physical_base_backup_peer"
_POSTGRES_15_BOOKWORM_AMD64_DIGEST = "fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786"
_LOCAL_BASE_BACKUP_REPLICATION_ROLE = "physical_backup"
_LOCAL_BASE_BACKUP_SOCKET_DIRECTORY = "/var/run/postgresql"
_LOCAL_BASE_BACKUP_PEER_OS_USERS = ("postgres",)
_LOCAL_BASE_BACKUP_MAX_WAL_SENDERS = 1
_LOCAL_BASE_BACKUP_HELPER_EXECUTION = "digest-pinned-image-attested-container-v1"
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_TEMPLATE_TOKEN_RE = re.compile(r"@@([A-Z0-9_]+)@@")

_VALIDATED_MANIFEST_CAPABILITY = object()
_VERIFIED_ADAPTER_INSTALLATIONS_CAPABILITY = object()


class PhysicalPostgresDeploymentError(ValueError):
    """The physical PostgreSQL scaffold cannot safely be rendered."""


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    """Encode local control data as canonical ASCII JSON without I/O."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalPostgresDeploymentError("value is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalPostgresDeploymentError("manifest contains duplicate JSON fields")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PhysicalPostgresDeploymentError(
        f"manifest contains unsupported JSON constant: {value}"
    )


def parse_physical_postgres_deployment_manifest(payload: bytes) -> dict[str, Any]:
    """Strictly parse one newline-terminated canonical manifest payload."""

    if not isinstance(payload, bytes) or not payload:
        raise PhysicalPostgresDeploymentError("manifest payload is invalid")
    try:
        parsed = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalPostgresDeploymentError(
            "manifest must be strict ASCII JSON"
        ) from exc
    if not isinstance(parsed, dict) or payload != canonical_json_bytes(parsed) + b"\n":
        raise PhysicalPostgresDeploymentError("manifest is not canonical JSON")
    return parsed


def _mapping(value: object, *, label: str, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PhysicalPostgresDeploymentError(f"{label} fields are invalid")
    return dict(value)


def _text(value: object, *, label: str, pattern: re.Pattern[str] = _SAFE_ID_RE) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PhysicalPostgresDeploymentError(f"{label} is invalid")
    if value != value.strip() or "\x00" in value:
        raise PhysicalPostgresDeploymentError(f"{label} is invalid")
    return value


def _sha256(value: object, *, label: str) -> str:
    digest = _text(value, label=label, pattern=_SHA256_RE)
    if digest == "0" * 64:
        raise PhysicalPostgresDeploymentError(f"{label} is empty")
    return digest


def _lsn(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        raise PhysicalPostgresDeploymentError(f"{label} is invalid")
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PhysicalPostgresDeploymentError(f"{label} is invalid")
    return value


def _object_key(value: object, *, label: str) -> str:
    key = _text(value, label=label, pattern=_OBJECT_KEY_RE)
    if "//" in key or "/../" in key or key.endswith("/.."):
        raise PhysicalPostgresDeploymentError(f"{label} is not an immutable object key")
    return key


@dataclass(frozen=True)
class _Baseline:
    base_generation_id: str
    timeline: int
    consistent_wal_lsn: str
    base_backup_object_key: str
    base_backup_object_version_id: str
    base_backup_ciphertext_sha256: str
    base_backup_plaintext_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_generation_id": self.base_generation_id,
            "timeline": self.timeline,
            "consistent_wal_lsn": self.consistent_wal_lsn,
            "base_backup_object_key": self.base_backup_object_key,
            "base_backup_object_version_id": self.base_backup_object_version_id,
            "base_backup_ciphertext_sha256": self.base_backup_ciphertext_sha256,
            "base_backup_plaintext_sha256": self.base_backup_plaintext_sha256,
        }


@dataclass(frozen=True)
class _WriterTerm:
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    term_proof_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "holder_site": self.holder_site,
            "writer_epoch": self.writer_epoch,
            "writer_lease_id": self.writer_lease_id,
            "witness_transition_id": self.witness_transition_id,
            "term_proof_sha256": self.term_proof_sha256,
        }


@dataclass(frozen=True)
class _Route:
    source_site: str
    destination_site: str
    delivery_route: str
    route_binding_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_site": self.source_site,
            "destination_site": self.destination_site,
            "delivery_route": self.delivery_route,
            "route_binding_sha256": self.route_binding_sha256,
            "direct_fi_to_ir_ssh": False,
            "direct_fi_to_ir_scp": False,
            "direct_fi_to_ir_postgres_control": False,
        }


@dataclass(frozen=True)
class _LocalBaseBackup:
    """The sole local replication exception for a digest-pinned helper.

    This declares deployment input only.  A future root-only preflight must
    still inspect the actual role attributes, peer map, socket volume mode, and
    helper installation attestation before anything may execute.
    """

    transport: str
    socket_directory: str
    port: int
    replication_role: str
    peer_os_users: tuple[str, ...]
    max_wal_senders: int
    tcp_hba: str
    helper_execution: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "socket_directory": self.socket_directory,
            "port": self.port,
            "replication_role": self.replication_role,
            "peer_os_users": list(self.peer_os_users),
            "max_wal_senders": self.max_wal_senders,
            "tcp_hba": self.tcp_hba,
            "helper_execution": self.helper_execution,
        }


@dataclass(frozen=True)
class _PostgresRuntimeIdentity:
    """Image-inspected non-root UID/GID facts; never a hard-coded default."""

    image_digest: str
    platform: str
    effective_uid: int
    effective_gid: int
    attestation_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_digest": self.image_digest,
            "platform": self.platform,
            "effective_uid": self.effective_uid,
            "effective_gid": self.effective_gid,
            "attestation_sha256": self.attestation_sha256,
        }


@dataclass(frozen=True)
class _PrimaryRole:
    site: str
    postgres_data_volume: str
    postgres_socket_volume: str
    wal_spool_volume: str
    adapter_state_volume: str
    runtime_network_name: str
    local_base_backup: _LocalBaseBackup

    def as_dict(self) -> dict[str, str]:
        return {
            "site": self.site,
            "postgres_data_volume": self.postgres_data_volume,
            "postgres_socket_volume": self.postgres_socket_volume,
            "wal_spool_volume": self.wal_spool_volume,
            "adapter_state_volume": self.adapter_state_volume,
            "runtime_network_name": self.runtime_network_name,
            "local_base_backup": self.local_base_backup.as_dict(),
        }


@dataclass(frozen=True)
class _StandbyRole:
    site: str
    postgres_data_volume: str
    restore_spool_volume: str
    receiver_state_volume: str
    runtime_network_name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "site": self.site,
            "postgres_data_volume": self.postgres_data_volume,
            "restore_spool_volume": self.restore_spool_volume,
            "receiver_state_volume": self.receiver_state_volume,
            "runtime_network_name": self.runtime_network_name,
        }


@dataclass(frozen=True)
class _AdapterSpec:
    kind: str
    adapter_id: str
    site: str
    contract: str
    binary_path: str
    binary_sha256: str
    contract_sha256: str
    installation_attestation_sha256: str
    route_binding_sha256: str
    acknowledgement_mode: str | None = None
    strict_remote_durable_replay_identity_sha256: str | None = None
    writer_admission_integration_sha256: str | None = None
    maximum_rpo_seconds: int | None = None

    @property
    def directory(self) -> str:
        return self.binary_path.rsplit("/", 1)[0]

    @property
    def attestation_path(self) -> str:
        return f"/etc/trading-bot/physical-postgres/adapters/{self.adapter_id}/installation-attestation.json"

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "adapter_id": self.adapter_id,
            "site": self.site,
            "contract": self.contract,
            "binary_path": self.binary_path,
            "binary_sha256": self.binary_sha256,
            "contract_sha256": self.contract_sha256,
            "installation_attestation_sha256": self.installation_attestation_sha256,
            "route_binding_sha256": self.route_binding_sha256,
        }
        if self.acknowledgement_mode is not None:
            result["acknowledgement_mode"] = self.acknowledgement_mode
        if self.strict_remote_durable_replay_identity_sha256 is not None:
            result[
                "strict_remote_durable_replay_identity_sha256"
            ] = self.strict_remote_durable_replay_identity_sha256
        if self.writer_admission_integration_sha256 is not None:
            result[
                "writer_admission_integration_sha256"
            ] = self.writer_admission_integration_sha256
        if self.maximum_rpo_seconds is not None:
            result["maximum_rpo_seconds"] = self.maximum_rpo_seconds
        return result


@dataclass(frozen=True)
class PhysicalPostgresDeploymentManifest:
    """Opaque normalized manifest; direct construction is not authority."""

    campaign_id: str
    release_sha: str
    postgres_image: str
    postgres_major: int
    postgres_runtime_identity: _PostgresRuntimeIdentity
    deployment_profile: str
    baseline: _Baseline
    writer_term: _WriterTerm
    route: _Route
    primary: _PrimaryRole
    standby: _StandbyRole
    adapters: tuple[_AdapterSpec, ...]
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def writer_term_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.writer_term.as_dict())).hexdigest()

    def adapter(self, kind: str) -> _AdapterSpec:
        for adapter in self.adapters:
            if adapter.kind == kind:
                return adapter
        raise PhysicalPostgresDeploymentError("required adapter is missing")

    def lock_document(self) -> dict[str, Any]:
        return {
            "schema": PHYSICAL_POSTGRES_RENDER_LOCK_SCHEMA,
            "status": PHYSICAL_POSTGRES_DEFAULT_OFF_STATUS,
            "campaign_id": self.campaign_id,
            "release_sha": self.release_sha,
            "postgres_image": self.postgres_image,
            "postgres_major": self.postgres_major,
            "postgres_runtime_identity": self.postgres_runtime_identity.as_dict(),
            "deployment_profile": self.deployment_profile,
            "baseline": self.baseline.as_dict(),
            "writer_term": self.writer_term.as_dict(),
            "writer_term_sha256": self.writer_term_sha256,
            "route": self.route.as_dict(),
            "primary": self.primary.as_dict(),
            "standby": self.standby.as_dict(),
            "adapters": {
                adapter.kind: {
                    **adapter.as_dict(),
                    "installation_attestation_path": adapter.attestation_path,
                }
                for adapter in self.adapters
            },
            "not_a_live_remote_ack_proof": True,
            "not_a_launch_authorization": True,
        }


def _validate_baseline(value: object) -> _Baseline:
    item = _mapping(value, label="baseline", fields=_BASELINE_FIELDS)
    return _Baseline(
        base_generation_id=_text(
            item["base_generation_id"], label="base_generation_id"
        ),
        timeline=_positive_int(item["timeline"], label="timeline", maximum=2**31 - 1),
        consistent_wal_lsn=_lsn(item["consistent_wal_lsn"], label="consistent_wal_lsn"),
        base_backup_object_key=_object_key(
            item["base_backup_object_key"], label="base_backup_object_key"
        ),
        base_backup_object_version_id=_text(
            item["base_backup_object_version_id"], label="base_backup_object_version_id"
        ),
        base_backup_ciphertext_sha256=_sha256(
            item["base_backup_ciphertext_sha256"], label="base_backup_ciphertext_sha256"
        ),
        base_backup_plaintext_sha256=_sha256(
            item["base_backup_plaintext_sha256"], label="base_backup_plaintext_sha256"
        ),
    )


def _validate_writer_term(value: object) -> _WriterTerm:
    item = _mapping(value, label="writer_term", fields=_WRITER_TERM_FIELDS)
    if item["holder_site"] != "webapp_fi":
        raise PhysicalPostgresDeploymentError("FI must hold the rendered normal writer term")
    return _WriterTerm(
        holder_site="webapp_fi",
        writer_epoch=_positive_int(
            item["writer_epoch"], label="writer_epoch", maximum=2**63 - 1
        ),
        writer_lease_id=_text(item["writer_lease_id"], label="writer_lease_id"),
        witness_transition_id=_text(
            item["witness_transition_id"], label="witness_transition_id"
        ),
        term_proof_sha256=_sha256(item["term_proof_sha256"], label="term_proof_sha256"),
    )


def _validate_route(value: object) -> _Route:
    item = _mapping(value, label="route", fields=_ROUTE_FIELDS)
    if (
        item["source_site"] != "webapp_fi"
        or item["destination_site"] != "webapp_ir"
        or item["delivery_route"] != "private-versioned-object-storage-pull-ack-v1"
    ):
        raise PhysicalPostgresDeploymentError("route is not the fixed FI-to-IR pull-only route")
    for field_name in (
        "direct_fi_to_ir_ssh",
        "direct_fi_to_ir_scp",
        "direct_fi_to_ir_postgres_control",
    ):
        if item[field_name] is not False:
            raise PhysicalPostgresDeploymentError(
                "direct FI-to-IR host or database control is prohibited"
            )
    return _Route(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        delivery_route="private-versioned-object-storage-pull-ack-v1",
        route_binding_sha256=_sha256(
            item["route_binding_sha256"], label="route_binding_sha256"
        ),
    )


def _volume(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=_VOLUME_RE)


def _network(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=_NETWORK_RE)


def _validate_postgres_runtime_identity(value: object) -> _PostgresRuntimeIdentity:
    item = _mapping(
        value,
        label="postgres_runtime_identity",
        fields=_POSTGRES_RUNTIME_IDENTITY_FIELDS,
    )
    if (
        item["image_digest"] != "sha256:" + _POSTGRES_15_BOOKWORM_AMD64_DIGEST
        or item["platform"] != "linux/amd64"
    ):
        raise PhysicalPostgresDeploymentError(
            "postgres_runtime_identity must bind the resolved PostgreSQL 15-bookworm amd64 digest"
        )
    return _PostgresRuntimeIdentity(
        image_digest="sha256:" + _POSTGRES_15_BOOKWORM_AMD64_DIGEST,
        platform="linux/amd64",
        effective_uid=_positive_int(
            item["effective_uid"], label="postgres runtime effective_uid", maximum=2**31 - 1
        ),
        effective_gid=_positive_int(
            item["effective_gid"], label="postgres runtime effective_gid", maximum=2**31 - 1
        ),
        attestation_sha256=_sha256(
            item["attestation_sha256"], label="postgres runtime attestation_sha256"
        ),
    )


def _validate_local_base_backup(value: object) -> _LocalBaseBackup:
    """Permit exactly one Unix-socket peer-authenticated backup client path."""

    item = _mapping(value, label="primary local_base_backup", fields=_LOCAL_BASE_BACKUP_FIELDS)
    if (
        item["transport"] != "unix-socket-only"
        or item["socket_directory"] != _LOCAL_BASE_BACKUP_SOCKET_DIRECTORY
        or item["port"] != 5432
        or item["replication_role"] != _LOCAL_BASE_BACKUP_REPLICATION_ROLE
        or item["peer_os_users"] != list(_LOCAL_BASE_BACKUP_PEER_OS_USERS)
        or item["max_wal_senders"] != _LOCAL_BASE_BACKUP_MAX_WAL_SENDERS
        or item["tcp_hba"] != "reject"
        or item["helper_execution"] != _LOCAL_BASE_BACKUP_HELPER_EXECUTION
    ):
        raise PhysicalPostgresDeploymentError(
            "primary local_base_backup must remain the fixed socket-only helper policy"
        )
    return _LocalBaseBackup(
        transport="unix-socket-only",
        socket_directory=_LOCAL_BASE_BACKUP_SOCKET_DIRECTORY,
        port=5432,
        replication_role=_LOCAL_BASE_BACKUP_REPLICATION_ROLE,
        peer_os_users=_LOCAL_BASE_BACKUP_PEER_OS_USERS,
        max_wal_senders=_LOCAL_BASE_BACKUP_MAX_WAL_SENDERS,
        tcp_hba="reject",
        helper_execution=_LOCAL_BASE_BACKUP_HELPER_EXECUTION,
    )


def _validate_primary(value: object) -> _PrimaryRole:
    item = _mapping(value, label="primary", fields=_PRIMARY_FIELDS)
    if item["site"] != "webapp_fi":
        raise PhysicalPostgresDeploymentError("primary site must be webapp_fi")
    return _PrimaryRole(
        site="webapp_fi",
        postgres_data_volume=_volume(
            item["postgres_data_volume"], label="primary postgres_data_volume"
        ),
        postgres_socket_volume=_volume(
            item["postgres_socket_volume"], label="primary postgres_socket_volume"
        ),
        wal_spool_volume=_volume(
            item["wal_spool_volume"], label="primary wal_spool_volume"
        ),
        adapter_state_volume=_volume(
            item["adapter_state_volume"], label="primary adapter_state_volume"
        ),
        runtime_network_name=_network(
            item["runtime_network_name"], label="primary runtime_network_name"
        ),
        local_base_backup=_validate_local_base_backup(item["local_base_backup"]),
    )


def _validate_standby(value: object) -> _StandbyRole:
    item = _mapping(value, label="standby", fields=_STANDBY_FIELDS)
    if item["site"] != "webapp_ir":
        raise PhysicalPostgresDeploymentError("standby site must be webapp_ir")
    return _StandbyRole(
        site="webapp_ir",
        postgres_data_volume=_volume(
            item["postgres_data_volume"], label="standby postgres_data_volume"
        ),
        restore_spool_volume=_volume(
            item["restore_spool_volume"], label="standby restore_spool_volume"
        ),
        receiver_state_volume=_volume(
            item["receiver_state_volume"], label="standby receiver_state_volume"
        ),
        runtime_network_name=_network(
            item["runtime_network_name"], label="standby runtime_network_name"
        ),
    )


def _validate_adapter(
    *,
    kind: str,
    value: object,
    deployment_profile: str,
    route_binding_sha256: str,
) -> _AdapterSpec:
    expected_fields = _ADAPTER_COMMON_FIELDS
    if kind == "writer_ack":
        expected_fields = (
            _STRICT_ACK_FIELDS
            if deployment_profile == PROFILE_STRICT_ZERO_LOSS
            else _BOUNDED_ACK_FIELDS
        )
    item = _mapping(value, label=f"{kind} adapter", fields=expected_fields)
    adapter_id = _text(item["adapter_id"], label=f"{kind} adapter_id")
    expected_binary_path = (
        f"{_ADAPTER_ROOT}/{adapter_id}/{ADAPTER_BINARY_NAMES[kind]}"
    )
    if item["site"] != _ADAPTER_SITE[kind]:
        raise PhysicalPostgresDeploymentError(f"{kind} adapter site is invalid")
    if item["contract"] != ADAPTER_CONTRACTS[kind]:
        raise PhysicalPostgresDeploymentError(f"{kind} adapter contract is invalid")
    if item["binary_path"] != expected_binary_path:
        raise PhysicalPostgresDeploymentError(
            f"{kind} adapter binary path is not the fixed adapter location"
        )
    if item["route_binding_sha256"] != route_binding_sha256:
        raise PhysicalPostgresDeploymentError(
            f"{kind} adapter route binding differs from deployment route"
        )
    adapter = _AdapterSpec(
        kind=kind,
        adapter_id=adapter_id,
        site=_ADAPTER_SITE[kind],
        contract=ADAPTER_CONTRACTS[kind],
        binary_path=expected_binary_path,
        binary_sha256=_sha256(item["binary_sha256"], label=f"{kind} binary_sha256"),
        contract_sha256=_sha256(
            item["contract_sha256"], label=f"{kind} contract_sha256"
        ),
        installation_attestation_sha256=_sha256(
            item["installation_attestation_sha256"],
            label=f"{kind} installation_attestation_sha256",
        ),
        route_binding_sha256=route_binding_sha256,
    )
    if kind != "writer_ack":
        return adapter

    writer_admission_integration_sha256 = _sha256(
        item["writer_admission_integration_sha256"],
        label="writer_ack writer_admission_integration_sha256",
    )
    if deployment_profile == PROFILE_STRICT_ZERO_LOSS:
        if item["acknowledgement_mode"] != ACK_MODE_STRICT_REMOTE_DURABLE_REPLAY:
            raise PhysicalPostgresDeploymentError(
                "archive-only acknowledgement cannot render strict_zero_loss"
            )
        return _AdapterSpec(
            **{
                **adapter.__dict__,
                "acknowledgement_mode": ACK_MODE_STRICT_REMOTE_DURABLE_REPLAY,
                "strict_remote_durable_replay_identity_sha256": _sha256(
                    item["strict_remote_durable_replay_identity_sha256"],
                    label="writer_ack strict_remote_durable_replay_identity_sha256",
                ),
                "writer_admission_integration_sha256": writer_admission_integration_sha256,
            }
        )
    if item["acknowledgement_mode"] != ACK_MODE_BOUNDED_RPO_ARCHIVE:
        raise PhysicalPostgresDeploymentError("bounded archive profile has an invalid acknowledgement mode")
    return _AdapterSpec(
        **{
            **adapter.__dict__,
            "acknowledgement_mode": ACK_MODE_BOUNDED_RPO_ARCHIVE,
            "writer_admission_integration_sha256": writer_admission_integration_sha256,
            "maximum_rpo_seconds": _positive_int(
                item["maximum_rpo_seconds"],
                label="writer_ack maximum_rpo_seconds",
                maximum=24 * 60 * 60,
            ),
        }
    )


def validate_physical_postgres_deployment_manifest(
    value: object,
) -> PhysicalPostgresDeploymentManifest:
    """Validate the exact normal FI-primary / IR-standby deployment input."""

    item = _mapping(value, label="deployment manifest", fields=_MANIFEST_FIELDS)
    if item["schema"] != PHYSICAL_POSTGRES_DEPLOYMENT_MANIFEST_SCHEMA:
        raise PhysicalPostgresDeploymentError("manifest schema is invalid")
    if item["mode"] != "default-off":
        raise PhysicalPostgresDeploymentError("manifest must remain default-off")
    campaign_id = _text(item["campaign_id"], label="campaign_id", pattern=_CAMPAIGN_RE)
    release_sha = _text(item["release_sha"], label="release_sha", pattern=_RELEASE_RE)
    postgres_image = _text(
        item["postgres_image"], label="postgres_image", pattern=_POSTGRES_IMAGE_RE
    )
    if (
        "/postgres@sha256:" not in f"/{postgres_image}"
        or not postgres_image.endswith("@sha256:" + _POSTGRES_15_BOOKWORM_AMD64_DIGEST)
    ):
        raise PhysicalPostgresDeploymentError(
            "postgres_image is not the resolved PostgreSQL 15-bookworm amd64 digest"
        )
    postgres_major = _positive_int(
        item["postgres_major"], label="postgres_major", maximum=99
    )
    if postgres_major != 15:
        raise PhysicalPostgresDeploymentError("only the PostgreSQL 15 scaffold is supported")
    postgres_runtime_identity = _validate_postgres_runtime_identity(
        item["postgres_runtime_identity"]
    )
    profile = item["deployment_profile"]
    if profile not in {PROFILE_STRICT_ZERO_LOSS, PROFILE_BOUNDED_RPO_ARCHIVE}:
        raise PhysicalPostgresDeploymentError("deployment_profile is invalid")

    baseline = _validate_baseline(item["baseline"])
    writer_term = _validate_writer_term(item["writer_term"])
    route = _validate_route(item["route"])
    primary = _validate_primary(item["primary"])
    standby = _validate_standby(item["standby"])
    all_volumes = (
        primary.postgres_data_volume,
        primary.postgres_socket_volume,
        primary.wal_spool_volume,
        primary.adapter_state_volume,
        standby.postgres_data_volume,
        standby.restore_spool_volume,
        standby.receiver_state_volume,
    )
    if len(set(all_volumes)) != len(all_volumes):
        raise PhysicalPostgresDeploymentError("physical PostgreSQL volumes must be distinct")
    if primary.runtime_network_name == standby.runtime_network_name:
        raise PhysicalPostgresDeploymentError(
            "FI and IR runtime networks must be separately named"
        )

    adapters_value = item["adapters"]
    if not isinstance(adapters_value, Mapping) or set(adapters_value) != set(ADAPTER_KINDS):
        raise PhysicalPostgresDeploymentError("manifest must bind every required adapter")
    adapters = tuple(
        _validate_adapter(
            kind=kind,
            value=adapters_value[kind],
            deployment_profile=profile,
            route_binding_sha256=route.route_binding_sha256,
        )
        for kind in ADAPTER_KINDS
    )
    if len({adapter.adapter_id for adapter in adapters}) != len(adapters):
        raise PhysicalPostgresDeploymentError("adapter identities must be unique")

    manifest = PhysicalPostgresDeploymentManifest(
        campaign_id=campaign_id,
        release_sha=release_sha,
        postgres_image=postgres_image,
        postgres_major=postgres_major,
        postgres_runtime_identity=postgres_runtime_identity,
        deployment_profile=profile,
        baseline=baseline,
        writer_term=writer_term,
        route=route,
        primary=primary,
        standby=standby,
        adapters=adapters,
    )
    object.__setattr__(manifest, "_capability", _VALIDATED_MANIFEST_CAPABILITY)
    return manifest


@dataclass(frozen=True)
class AdapterInstallation:
    """Non-authorizing local inspection facts for one immutable adapter."""

    binary_path: str
    binary_sha256: str
    installation_attestation_sha256: str
    owner_uid: int
    mode: int
    regular_file: bool
    ancestors_root_controlled: bool


class AdapterInstallationInspector(Protocol):
    """Read-only local inspector; it must not install or invoke adapters."""

    def inspect(self, *, adapter: _AdapterSpec) -> AdapterInstallation:
        """Return bounded local facts for the already-validated adapter path."""


@dataclass(frozen=True)
class VerifiedPhysicalPostgresAdapterInstallations:
    """Opaque evidence that all required local adapters were inspected."""

    manifest_lock_sha256: str
    adapter_attestation_sha256s: tuple[tuple[str, str], ...]
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def verify_physical_postgres_adapter_installations(
    manifest: PhysicalPostgresDeploymentManifest,
    *,
    inspector: AdapterInstallationInspector,
) -> VerifiedPhysicalPostgresAdapterInstallations:
    """Require every binary and local attestation before any render is possible."""

    if manifest._capability is not _VALIDATED_MANIFEST_CAPABILITY:
        raise PhysicalPostgresDeploymentError("manifest was not validated by this module")
    observed: list[tuple[str, str]] = []
    for adapter in manifest.adapters:
        try:
            installed = inspector.inspect(adapter=adapter)
        except Exception as exc:  # pragma: no cover - exact OS errors are adapter-specific
            raise PhysicalPostgresDeploymentError(
                f"{adapter.kind} adapter cannot be safely inspected"
            ) from exc
        if not isinstance(installed, AdapterInstallation):
            raise PhysicalPostgresDeploymentError(
                f"{adapter.kind} adapter inspection returned invalid evidence"
            )
        if (
            installed.binary_path != adapter.binary_path
            or installed.binary_sha256 != adapter.binary_sha256
            or installed.installation_attestation_sha256
            != adapter.installation_attestation_sha256
            or installed.owner_uid != 0
            or installed.mode & 0o022
            or not installed.regular_file
            or not installed.ancestors_root_controlled
        ):
            raise PhysicalPostgresDeploymentError(
                f"{adapter.kind} adapter binary or attestation is not verified"
            )
        observed.append((adapter.kind, installed.installation_attestation_sha256))
    result = VerifiedPhysicalPostgresAdapterInstallations(
        manifest_lock_sha256=hashlib.sha256(
            canonical_json_bytes(manifest.lock_document())
        ).hexdigest(),
        adapter_attestation_sha256s=tuple(observed),
    )
    object.__setattr__(
        result, "_capability", _VERIFIED_ADAPTER_INSTALLATIONS_CAPABILITY
    )
    return result


@dataclass(frozen=True)
class RenderedPhysicalPostgresDeployment:
    """Default-off files; their existence is explicitly not launch authority."""

    files: tuple[tuple[str, bytes], ...]
    manifest_lock_sha256: str
    postgres_runtime_gid: int
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def file(self, relative_path: str) -> bytes:
        for path, payload in self.files:
            if path == relative_path:
                return payload
        raise KeyError(relative_path)


_TEMPLATE_NAMES = (
    "primary-postgresql.conf.template",
    "primary-pg_hba.conf.template",
    "primary-pg_ident.conf.template",
    "standby-postgresql.conf.template",
    "standby-pg_hba.conf.template",
    "docker-compose.primary.yml.template",
    "docker-compose.standby.yml.template",
)


def _render_template(template: object, *, values: Mapping[str, str], label: str) -> bytes:
    if not isinstance(template, str) or not template.endswith("\n"):
        raise PhysicalPostgresDeploymentError(f"{label} template is invalid")
    tokens = set(_TEMPLATE_TOKEN_RE.findall(template))
    if any(token not in values for token in tokens):
        raise PhysicalPostgresDeploymentError(f"{label} template has an unknown token")

    def replace(match: re.Match[str]) -> str:
        return values[match.group(1)]

    rendered = _TEMPLATE_TOKEN_RE.sub(replace, template)
    if "@@" in rendered:
        raise PhysicalPostgresDeploymentError(f"{label} template was not fully rendered")
    return rendered.encode("utf-8")


def _adapter_descriptor(
    manifest: PhysicalPostgresDeploymentManifest,
    adapter: _AdapterSpec,
    *,
    role: str,
) -> bytes:
    payload: dict[str, Any] = {
        "schema": PHYSICAL_POSTGRES_ADAPTER_DESCRIPTOR_SCHEMA,
        "status": PHYSICAL_POSTGRES_DEFAULT_OFF_STATUS,
        "role": role,
        "campaign_id": manifest.campaign_id,
        "release_sha": manifest.release_sha,
        "deployment_profile": manifest.deployment_profile,
        "baseline": manifest.baseline.as_dict(),
        "writer_term": manifest.writer_term.as_dict(),
        "writer_term_sha256": manifest.writer_term_sha256,
        "route": manifest.route.as_dict(),
        "adapter": {
            **adapter.as_dict(),
            "installation_attestation_path": adapter.attestation_path,
        },
        "not_a_remote_ack_proof": True,
        "not_a_launch_authorization": True,
    }
    return canonical_json_bytes(payload) + b"\n"


def _require_primary_local_socket_substrate(
    *,
    primary_postgresql_conf: bytes,
    primary_pg_hba_conf: bytes,
    primary_pg_ident_conf: bytes,
    primary_compose: bytes,
    postgres_socket_volume: str,
) -> None:
    """Reject templates that could silently reintroduce TCP or omit the socket.

    The renderer deliberately stays independent of a Compose/YAML library.
    These are fixed, exact generated fragments, so a literal post-render
    assertion is narrower and safer than accepting arbitrary YAML semantics.
    """

    try:
        config = primary_postgresql_conf.decode("utf-8", "strict")
        pg_hba = primary_pg_hba_conf.decode("utf-8", "strict")
        pg_ident = primary_pg_ident_conf.decode("utf-8", "strict")
        compose = primary_compose.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:  # pragma: no cover - templates are str
        raise PhysicalPostgresDeploymentError(
            "primary local socket template is not UTF-8"
        ) from exc
    active_config = tuple(
        line.strip()
        for line in config.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if (
        tuple(line for line in active_config if line.startswith("listen_addresses"))
        != ("listen_addresses = ''",)
        or "unix_socket_directories = '/var/run/postgresql'" not in active_config
        or tuple(line for line in active_config if line.startswith("max_wal_senders"))
        != ("max_wal_senders = 1",)
        or "hba_file = '/etc/trading-bot/physical-postgres/primary/pg_hba.conf'"
        not in active_config
        or "ident_file = '/etc/trading-bot/physical-postgres/primary/pg_ident.conf'"
        not in active_config
        or "unix_socket_group = 'postgres'" not in active_config
        or "unix_socket_permissions = 0770" not in active_config
    ):
        raise PhysicalPostgresDeploymentError(
            "primary PostgreSQL template must force the local socket-only base-backup policy"
        )
    active_hba = tuple(
        tuple(line.split())
        for line in pg_hba.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if active_hba != (
        ("local", "all", "postgres", "peer"),
        ("local", "replication", "physical_backup", "peer", "map=physical_base_backup_peer"),
        ("local", "all", "all", "reject"),
        ("host", "all", "all", "0.0.0.0/0", "reject"),
        ("host", "all", "all", "::0/0", "reject"),
    ):
        raise PhysicalPostgresDeploymentError(
            "primary local replication HBA must permit only the fixed peer map and reject TCP"
        )
    active_ident = tuple(
        tuple(line.split())
        for line in pg_ident.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if active_ident != (
        ("physical_base_backup_peer", "postgres", "physical_backup"),
    ):
        raise PhysicalPostgresDeploymentError(
            "primary local replication peer map is incomplete"
        )
    if (
        "ports:" in compose
        or "expose:" in compose
        or "network_mode: host" in compose
        or 'network_mode: "host"' in compose
        or "network_mode: 'host'" in compose
    ):
        raise PhysicalPostgresDeploymentError(
            "primary Compose template must not expose a PostgreSQL TCP or host network"
        )
    expected_mount = (
        "      - type: volume\n"
        f"        source: {postgres_socket_volume}\n"
        "        target: /var/run/postgresql\n"
    )
    expected_volume = f"  {postgres_socket_volume}:\n    external: true\n"
    if expected_mount not in compose or expected_volume not in compose:
        raise PhysicalPostgresDeploymentError(
            "primary Compose template is missing the distinct local PostgreSQL socket volume"
        )


def _require_standby_local_socket_substrate(
    *,
    standby_postgresql_conf: bytes,
    standby_pg_hba_conf: bytes,
    standby_compose: bytes,
) -> None:
    """Require an explicit, socket-only WA-IR standby access policy.

    The standby needs outbound Object-Storage reachability for its pull adapter,
    but that does not require accepting PostgreSQL TCP connections from the
    external runtime network.  Keep its generated recovery/readback substrate
    local until a separate reviewed reader-access boundary exists.
    """

    try:
        config = standby_postgresql_conf.decode("utf-8", "strict")
        pg_hba = standby_pg_hba_conf.decode("utf-8", "strict")
        compose = standby_compose.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:  # pragma: no cover - templates are str
        raise PhysicalPostgresDeploymentError(
            "standby local socket template is not UTF-8"
        ) from exc
    active_config = tuple(
        line.strip()
        for line in config.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if (
        tuple(line for line in active_config if line.startswith("listen_addresses"))
        != ("listen_addresses = ''",)
        or "unix_socket_directories = '/var/run/postgresql'" not in active_config
        or "unix_socket_group = 'postgres'" not in active_config
        or "unix_socket_permissions = 0770" not in active_config
        or "hba_file = '/etc/trading-bot/physical-postgres/standby/pg_hba.conf'"
        not in active_config
        or tuple(line for line in active_config if line.startswith("max_wal_senders"))
        != ("max_wal_senders = 0",)
        or tuple(line for line in active_config if line.startswith("max_replication_slots"))
        != ("max_replication_slots = 0",)
        or tuple(line for line in active_config if line.startswith("primary_conninfo"))
        != ("primary_conninfo = ''",)
        or tuple(line for line in active_config if line.startswith("primary_slot_name"))
        != ("primary_slot_name = ''",)
        or tuple(
            line for line in active_config if line.startswith("synchronous_standby_names")
        )
        != ("synchronous_standby_names = ''",)
    ):
        raise PhysicalPostgresDeploymentError(
            "standby PostgreSQL template must force the local socket-only pull-only policy"
        )
    active_hba = tuple(
        tuple(line.split())
        for line in pg_hba.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if active_hba != (
        ("local", "all", "postgres", "peer"),
        ("local", "replication", "all", "reject"),
        ("local", "all", "all", "reject"),
        ("host", "replication", "all", "0.0.0.0/0", "reject"),
        ("host", "all", "all", "0.0.0.0/0", "reject"),
        ("host", "replication", "all", "::0/0", "reject"),
        ("host", "all", "all", "::0/0", "reject"),
    ):
        raise PhysicalPostgresDeploymentError(
            "standby HBA must permit only local postgres peer maintenance and reject TCP"
        )
    expected_mount = (
        "      - type: bind\n"
        "        source: /etc/trading-bot/physical-postgres/rendered/standby\n"
        "        target: /etc/trading-bot/physical-postgres/standby\n"
        "        read_only: true\n"
        "        bind:\n"
        "          create_host_path: false\n"
    )
    if expected_mount not in compose:
        raise PhysicalPostgresDeploymentError(
            "standby Compose template is missing the read-only rendered HBA mount"
        )


def _require_no_compose_host_ports(*, primary_compose: bytes, standby_compose: bytes) -> None:
    forbidden = (b"ports:", b"expose:", b"network_mode: host", b'network_mode: "host"', b"network_mode: 'host'")
    if any(token in primary_compose or token in standby_compose for token in forbidden):
        raise PhysicalPostgresDeploymentError(
            "physical PostgreSQL Compose templates must not expose TCP ports or host networking"
        )


def render_physical_postgres_deployment(
    manifest: PhysicalPostgresDeploymentManifest,
    *,
    verified_adapters: VerifiedPhysicalPostgresAdapterInstallations,
    templates: Mapping[str, str],
) -> RenderedPhysicalPostgresDeployment:
    """Render primary/standby config only after all local adapter checks pass."""

    if manifest._capability is not _VALIDATED_MANIFEST_CAPABILITY:
        raise PhysicalPostgresDeploymentError("manifest was not validated by this module")
    if verified_adapters._capability is not _VERIFIED_ADAPTER_INSTALLATIONS_CAPABILITY:
        raise PhysicalPostgresDeploymentError("adapter installations were not verified")
    expected_lock_sha256 = hashlib.sha256(
        canonical_json_bytes(manifest.lock_document())
    ).hexdigest()
    if verified_adapters.manifest_lock_sha256 != expected_lock_sha256:
        raise PhysicalPostgresDeploymentError("adapter inspection does not match manifest lock")
    if set(templates) != set(_TEMPLATE_NAMES):
        raise PhysicalPostgresDeploymentError("renderer template set is invalid")
    # This pull-only scaffold deliberately has no reviewed Object-Storage
    # durable-replay runtime.  A manifest label, hash, or purported adapter
    # identity is not evidence that acknowledged FI writes reached WA-IR.
    # Refuse to render a misleading strict profile until that runtime and its
    # live coordinator are implemented and independently reviewed.
    if manifest.deployment_profile == PROFILE_STRICT_ZERO_LOSS:
        raise PhysicalPostgresDeploymentError(
            "strict_zero_loss cannot render: no reviewed Object-Storage durable-replay runtime exists"
        )

    primary_guard = manifest.adapter("primary_term_guard")
    wal_spool = manifest.adapter("wal_spool")
    standby_bootstrap = manifest.adapter("standby_bootstrap")
    standby_pull = manifest.adapter("standby_pull")
    standby_reverse_wal_spool = manifest.adapter("standby_reverse_wal_spool")
    values = {
        "CAMPAIGN_ID": manifest.campaign_id,
        "POSTGRES_IMAGE": manifest.postgres_image,
        "RELEASE_SHA": manifest.release_sha,
        "BASE_GENERATION_ID": manifest.baseline.base_generation_id,
        "WRITER_TERM_SHA256": manifest.writer_term_sha256,
        "ROUTE_BINDING_SHA256": manifest.route.route_binding_sha256,
        "DEPLOYMENT_PROFILE": manifest.deployment_profile,
        "WAL_SPOOL_BINARY": wal_spool.binary_path,
        "PRIMARY_SPOOL_CONFIG": "/etc/trading-bot/physical-postgres/primary/wal-spool.json",
        "STANDBY_PULL_BINARY": standby_pull.binary_path,
        "STANDBY_PULL_CONFIG": "/etc/trading-bot/physical-postgres/standby/pull-agent.json",
        "STANDBY_REVERSE_WAL_SPOOL_BINARY": standby_reverse_wal_spool.binary_path,
        "STANDBY_REVERSE_SPOOL_CONFIG": "/etc/trading-bot/physical-postgres/standby/reverse-wal-spool.json",
        "PRIMARY_GUARD_BINARY": primary_guard.binary_path,
        "PRIMARY_GUARD_CONFIG": "/etc/trading-bot/physical-postgres/primary/term-guard.json",
        "PRIMARY_HBA_FILE": "/etc/trading-bot/physical-postgres/primary/pg_hba.conf",
        "PRIMARY_IDENT_FILE": "/etc/trading-bot/physical-postgres/primary/pg_ident.conf",
        "STANDBY_HBA_FILE": "/etc/trading-bot/physical-postgres/standby/pg_hba.conf",
        "LOCAL_BASE_BACKUP_MAX_WAL_SENDERS": str(
            manifest.primary.local_base_backup.max_wal_senders
        ),
        "LOCAL_BASE_BACKUP_PEER_MAP": _LOCAL_BASE_BACKUP_PEER_MAP,
        "LOCAL_BASE_BACKUP_REPLICATION_ROLE": manifest.primary.local_base_backup.replication_role,
        "STANDBY_BOOTSTRAP_BINARY": standby_bootstrap.binary_path,
        "STANDBY_BOOTSTRAP_CONFIG": "/etc/trading-bot/physical-postgres/standby/bootstrap.json",
        "PRIMARY_DATA_VOLUME": manifest.primary.postgres_data_volume,
        "PRIMARY_SOCKET_VOLUME": manifest.primary.postgres_socket_volume,
        "PRIMARY_WAL_SPOOL_VOLUME": manifest.primary.wal_spool_volume,
        "PRIMARY_ADAPTER_STATE_VOLUME": manifest.primary.adapter_state_volume,
        "PRIMARY_RUNTIME_NETWORK": manifest.primary.runtime_network_name,
        "STANDBY_DATA_VOLUME": manifest.standby.postgres_data_volume,
        "STANDBY_RESTORE_SPOOL_VOLUME": manifest.standby.restore_spool_volume,
        "STANDBY_RECEIVER_STATE_VOLUME": manifest.standby.receiver_state_volume,
        "STANDBY_RUNTIME_NETWORK": manifest.standby.runtime_network_name,
        "PRIMARY_GUARD_DIRECTORY": primary_guard.directory,
        "WAL_SPOOL_DIRECTORY": wal_spool.directory,
        "STANDBY_BOOTSTRAP_DIRECTORY": standby_bootstrap.directory,
        "STANDBY_PULL_DIRECTORY": standby_pull.directory,
        "STANDBY_REVERSE_WAL_SPOOL_DIRECTORY": standby_reverse_wal_spool.directory,
    }
    primary_postgresql_conf = _render_template(
        templates["primary-postgresql.conf.template"],
        values=values,
        label="primary PostgreSQL",
    )
    primary_pg_hba_conf = _render_template(
        templates["primary-pg_hba.conf.template"],
        values=values,
        label="primary pg_hba",
    )
    primary_pg_ident_conf = _render_template(
        templates["primary-pg_ident.conf.template"],
        values=values,
        label="primary pg_ident",
    )
    standby_postgresql_conf = _render_template(
        templates["standby-postgresql.conf.template"],
        values=values,
        label="standby PostgreSQL",
    )
    standby_pg_hba_conf = _render_template(
        templates["standby-pg_hba.conf.template"],
        values=values,
        label="standby pg_hba",
    )
    primary_compose = _render_template(
        templates["docker-compose.primary.yml.template"],
        values=values,
        label="primary Compose",
    )
    standby_compose = _render_template(
        templates["docker-compose.standby.yml.template"],
        values=values,
        label="standby Compose",
    )
    _require_primary_local_socket_substrate(
        primary_postgresql_conf=primary_postgresql_conf,
        primary_pg_hba_conf=primary_pg_hba_conf,
        primary_pg_ident_conf=primary_pg_ident_conf,
        primary_compose=primary_compose,
        postgres_socket_volume=manifest.primary.postgres_socket_volume,
    )
    _require_standby_local_socket_substrate(
        standby_postgresql_conf=standby_postgresql_conf,
        standby_pg_hba_conf=standby_pg_hba_conf,
        standby_compose=standby_compose,
    )
    _require_no_compose_host_ports(
        primary_compose=primary_compose,
        standby_compose=standby_compose,
    )
    files = {
        "primary/postgresql.conf": primary_postgresql_conf,
        "primary/pg_hba.conf": primary_pg_hba_conf,
        "primary/pg_ident.conf": primary_pg_ident_conf,
        "standby/postgresql.conf": standby_postgresql_conf,
        "standby/pg_hba.conf": standby_pg_hba_conf,
        "primary/docker-compose.yml": primary_compose,
        "standby/docker-compose.yml": standby_compose,
        "primary/term-guard.json": _adapter_descriptor(
            manifest, primary_guard, role="primary"
        ),
        "primary/wal-spool.json": _adapter_descriptor(
            manifest, wal_spool, role="primary"
        ),
        "primary/wal-uploader.json": _adapter_descriptor(
            manifest, manifest.adapter("wal_uploader"), role="primary"
        ),
        "primary/writer-ack.json": _adapter_descriptor(
            manifest, manifest.adapter("writer_ack"), role="primary"
        ),
        "primary/local-base-backup-auth-preflight.json": canonical_json_bytes(
            {
                "schema": PHYSICAL_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT_SCHEMA,
                "status": PHYSICAL_POSTGRES_DEFAULT_OFF_STATUS,
                "campaign_id": manifest.campaign_id,
                "release_sha": manifest.release_sha,
                "source_site": "webapp_fi",
                "destination_site": "webapp_ir",
                "direct_fi_to_ir_postgres_control": False,
                "postgres_major": manifest.postgres_major,
                "postgres_runtime_identity": manifest.postgres_runtime_identity.as_dict(),
                "postgres_socket_volume": manifest.primary.postgres_socket_volume,
                "local_base_backup": manifest.primary.local_base_backup.as_dict(),
                "pg_hba_sha256": hashlib.sha256(primary_pg_hba_conf).hexdigest(),
                "pg_ident_sha256": hashlib.sha256(primary_pg_ident_conf).hexdigest(),
                "postgresql_conf_sha256": hashlib.sha256(primary_postgresql_conf).hexdigest(),
                "required_role_attributes": {
                    "role": _LOCAL_BASE_BACKUP_REPLICATION_ROLE,
                    "login": True,
                    "replication": True,
                    "superuser": False,
                    "createdb": False,
                    "createrole": False,
                    "bypassrls": False,
                    "inherit": False,
                    "password_authentication": "forbidden",
                },
                "not_a_role_creation_authorization": True,
                "not_a_launch_authorization": True,
            }
        )
        + b"\n",
        "standby/bootstrap.json": _adapter_descriptor(
            manifest, standby_bootstrap, role="standby"
        ),
        "standby/pull-agent.json": _adapter_descriptor(
            manifest, standby_pull, role="standby"
        ),
        "standby/reverse-wal-spool.json": _adapter_descriptor(
            manifest, standby_reverse_wal_spool, role="standby"
        ),
        "standby/reverse-wal-uploader.json": _adapter_descriptor(
            manifest, manifest.adapter("standby_reverse_wal_uploader"), role="standby"
        ),
        # The bootstrap adapter must copy this exact marker into a newly
        # verified PGDATA only after it has validated the base generation.
        "standby/recovery.signal": b"",
        "manifest-lock.json": canonical_json_bytes(manifest.lock_document()) + b"\n",
    }
    result = RenderedPhysicalPostgresDeployment(
        files=tuple(sorted(files.items())),
        manifest_lock_sha256=expected_lock_sha256,
        postgres_runtime_gid=manifest.postgres_runtime_identity.effective_gid,
    )
    object.__setattr__(result, "_capability", _VALIDATED_MANIFEST_CAPABILITY)
    return result
