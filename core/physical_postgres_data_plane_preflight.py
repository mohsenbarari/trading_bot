"""Pure, default-off posture contract for the physical PostgreSQL data plane.

The Object-delta MVP is intentionally not a complete mirror.  The replacement
path needs a physical PostgreSQL base backup plus one ordered WAL stream, and
must distinguish two materially different acknowledgement promises:

* ``strict-zero-loss`` is reserved for a reviewed Object-Storage pull-plane
  durable/replay acknowledgement that is enforced at the writer admission
  boundary.  Native PostgreSQL ``remote_apply`` requires a live standby
  connection and is therefore forbidden by this FI-to-IR architecture.
* ``archive-bounded-rpo`` uses encrypted/versioned Object Storage WAL
  archival and WA-IR pull recovery.  It can be useful for recovery, but it is
  explicitly not a zero-loss claim.

This module is only an admission contract for future **read-only** adapters.
It does not run ``SHOW``, open a database, inspect a command line, contact
Object Storage, start replication, install a slot, enable a writer, or make a
promotion decision.  A future root-controlled adapter must collect the raw
settings/evidence, bind them to a trusted release/term manifest, and perform
its own live re-check immediately before any writer transition.

Raw readback is accepted only as bounded, byte-for-byte canonical JSON with a
typed provenance/hash envelope.  The schema deliberately stores command
*identity* and SHA-256 only; command text, URLs, credentials, and secrets are
not accepted or returned.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import LEASE_ID_RE


__all__ = (
    "ARCHIVE_BOUNDED_RPO",
    "DEFAULT_MAX_EVIDENCE_AGE_SECONDS",
    "MAX_PHYSICAL_POSTGRES_READBACK_BYTES",
    "PHYSICAL_POSTGRES_DATA_PLANE_DEFAULT_ENABLED",
    "PHYSICAL_POSTGRES_DATA_PLANE_EVIDENCE_SCHEMA",
    "PHYSICAL_POSTGRES_DATA_PLANE_PREFLIGHT_SCHEMA",
    "PHYSICAL_POSTGRES_DATA_PLANE_RESULT_SCHEMA",
    "PhysicalPostgresCommandDescriptor",
    "PhysicalPostgresDataPlanePreflight",
    "PhysicalPostgresDataPlanePreflightBinding",
    "PhysicalPostgresDataPlanePreflightError",
    "PhysicalPostgresEvidenceProvenance",
    "PhysicalPostgresReadbackEvidence",
    "PhysicalPostgresWriterTermBinding",
    "PREFLIGHT_STATUS_BLOCKED",
    "PREFLIGHT_STATUS_OBSERVED",
    "STRICT_OBJECT_STORAGE_REMOTE_DURABLE_REPLAY_DELIVERY",
    "STRICT_ZERO_LOSS",
    "assess_physical_postgres_data_plane_preflight",
    "canonical_physical_postgres_readback_bytes",
    "require_observed_physical_postgres_data_plane_preflight",
    "validate_physical_postgres_data_plane_preflight_binding",
    "verify_physical_postgres_readback_evidence",
)


PHYSICAL_POSTGRES_DATA_PLANE_PREFLIGHT_SCHEMA = (
    "gold-trade-physical-postgres-data-plane-preflight-v1"
)
PHYSICAL_POSTGRES_DATA_PLANE_EVIDENCE_SCHEMA = (
    "gold-trade-physical-postgres-readback-evidence-v1"
)
PHYSICAL_POSTGRES_DATA_PLANE_RESULT_SCHEMA = (
    "gold-trade-physical-postgres-data-plane-preflight-result-v1"
)
PHYSICAL_POSTGRES_DATA_PLANE_DEFAULT_ENABLED = False

STRICT_ZERO_LOSS = "strict-zero-loss"
ARCHIVE_BOUNDED_RPO = "archive-bounded-rpo"
_DURABILITY_PROFILES = frozenset({STRICT_ZERO_LOSS, ARCHIVE_BOUNDED_RPO})

PREFLIGHT_STATUS_OBSERVED = "observed"
PREFLIGHT_STATUS_BLOCKED = "blocked"

WEBAPP_FI = "webapp_fi"
WEBAPP_IR = "webapp_ir"
PRIMARY_ROLE = "primary"
STANDBY_ROLE = "standby"

ARCHIVE_TRANSPORT = "private-versioned-object-storage"
WA_IR_OBJECT_INGEST = "pull-only"
DIRECT_FI_TO_IR_CONTROL = "forbidden"
ARCHIVE_ONLY_DELIVERY = "archive-only"
STRICT_OBJECT_STORAGE_REMOTE_DURABLE_REPLAY_DELIVERY = (
    "strict-object-storage-remote-durable-replay"
)

MAX_PHYSICAL_POSTGRES_READBACK_BYTES = 32 * 1024
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_EVIDENCE_AGE_SECONDS = 900
_MAX_FUTURE_SKEW_SECONDS = 5

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_LSN = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_PROVENANCE_PATH = re.compile(
    r"^physical-postgres-preflight/(webapp_fi|webapp_ir)/readback-v1\.json$",
    re.ASCII,
)
_URL_VALUE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
_SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|"
    r"private[_ -]?key|secret|token)"
)
_VERIFIED_READBACK_CAPABILITY = object()
_PREFLIGHT_CAPABILITY = object()


class PhysicalPostgresDataPlanePreflightError(ValueError):
    """Readback evidence, its provenance, or its binding is unsafe."""


@dataclass(frozen=True)
class PhysicalPostgresCommandDescriptor:
    """Non-secret identity/hash projection of an archive or restore command."""

    identity: str
    sha256: str


@dataclass(frozen=True)
class PhysicalPostgresWriterTermBinding:
    """Non-secret projection of the active Witness-fenced writer term."""

    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    proof_sha256: str


@dataclass(frozen=True)
class PhysicalPostgresEvidenceProvenance:
    """Typed binding for one read-only raw evidence capture.

    ``logical_path`` is an evidence namespace, not a local filesystem path or
    Object Storage URL.  The adapter is responsible for obtaining and
    protecting the raw evidence; this pure contract only binds its bytes.
    """

    site: str
    collector_identity: str
    logical_path: str
    evidence_sha256: str


@dataclass(frozen=True)
class PhysicalPostgresReadbackEvidence:
    """Bounded canonical raw PostgreSQL setting/evidence input."""

    raw_evidence: bytes
    evidence_sha256: str
    provenance: PhysicalPostgresEvidenceProvenance


@dataclass(frozen=True)
class PhysicalPostgresDataPlanePreflightBinding:
    """Trusted expected release/schema/term/baseline/configuration binding.

    This is a typed input, not a signature verifier.  A future coordinator
    must derive it from its independently verified release and Witness
    evidence rather than accepting it from an operator or host readback.
    """

    release_id: str
    schema_revision: str
    active_term: PhysicalPostgresWriterTermBinding
    base_generation_id: str
    base_backup_sha256: str
    fi_archive_command: PhysicalPostgresCommandDescriptor
    fi_restore_command: PhysicalPostgresCommandDescriptor
    ir_archive_command: PhysicalPostgresCommandDescriptor
    ir_restore_command: PhysicalPostgresCommandDescriptor


@dataclass(frozen=True)
class _PostgresSettings:
    wal_level: str
    archive_mode: str
    archive_command: PhysicalPostgresCommandDescriptor
    restore_command: PhysicalPostgresCommandDescriptor
    max_wal_senders: int
    max_replication_slots: int
    hot_standby: str
    synchronous_commit: str
    synchronous_mode: str
    receiver_site: str | None
    receiver_application_name: str | None
    receiver_slot_name: str | None
    receiver_evidence_sha256: str | None
    synchronous_acknowledgement: str
    synchronous_transport: str


@dataclass(frozen=True)
class _PhysicalState:
    timeline: int
    base_generation_id: str
    base_backup_sha256: str
    source_wal_frontier: str | None
    archived_wal_frontier: str
    replay_frontier: str | None
    acknowledged_wal_frontier: str | None


@dataclass(frozen=True)
class _TransportAssumptions:
    archive_transport: str
    wa_ir_object_ingest: str
    direct_fi_to_ir_control: str
    wal_delivery_mode: str


@dataclass(frozen=True)
class _ReadbackFacts:
    site: str
    observed_role: str
    durability_claim: str
    release_id: str
    schema_revision: str
    writer_term: PhysicalPostgresWriterTermBinding
    postgres: _PostgresSettings
    physical: _PhysicalState
    transport: _TransportAssumptions
    observed_at: datetime


@dataclass(frozen=True)
class _VerifiedPhysicalPostgresReadback:
    """Opaque, revalidatable verified readback capability."""

    evidence: PhysicalPostgresReadbackEvidence
    facts: _ReadbackFacts
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalPostgresDataPlanePreflight:
    """Safe posture projection; never a deployment or promotion authority.

    ``observed`` means only that the supplied bounded snapshots meet this
    contract at the supplied clock instant.  It does not prove that settings
    stay live, that PostgreSQL has an active receiver, or that a promotion is
    safe.  ``blocked`` is the only result for an archive-only zero-loss claim.
    """

    schema: str
    status: str
    requested_durability_profile: str
    observed_durability_profile: str | None
    reasons: tuple[str, ...]
    fi_evidence_sha256: str
    ir_evidence_sha256: str
    release_id: str
    schema_revision: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    base_generation_id: str
    base_backup_sha256: str
    timeline: int
    fi_source_wal_frontier: str
    fi_archived_wal_frontier: str
    fi_acknowledged_wal_frontier: str
    ir_archived_wal_frontier: str
    ir_replay_frontier: str
    _fi_evidence: PhysicalPostgresReadbackEvidence = field(
        repr=False, compare=False
    )
    _ir_evidence: PhysicalPostgresReadbackEvidence = field(
        repr=False, compare=False
    )
    _binding: PhysicalPostgresDataPlanePreflightBinding = field(
        repr=False, compare=False
    )
    _maximum_evidence_age_seconds: int = field(repr=False, compare=False)
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalPostgresDataPlanePreflightError(
                "physical PostgreSQL evidence contains duplicate JSON fields"
            )
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise PhysicalPostgresDataPlanePreflightError(
        "physical PostgreSQL evidence contains a non-finite JSON number"
    )


def _canonical_json_bytes(value: Mapping[str, Any], *, label: str) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PhysicalPostgresDataPlanePreflightError(
            f"{label} is not canonical JSON"
        ) from exc


def canonical_physical_postgres_readback_bytes(value: Mapping[str, Any]) -> bytes:
    """Return canonical bytes for an adapter/test that already has safe data.

    This helper has no I/O and does not create trust.  The receiving verifier
    still requires the envelope hash, provenance, exact schema, and all
    field-level constraints.
    """

    if not isinstance(value, Mapping):
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL raw evidence is invalid"
        )
    return _canonical_json_bytes(value, label="physical PostgreSQL raw evidence")


def _exact_mapping(value: object, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} fields are invalid")
    return dict(value)


def _safe_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid")
    if _URL_VALUE.search(value) or _SENSITIVE_VALUE.search(value):
        raise PhysicalPostgresDataPlanePreflightError(
            f"{label} contains a URL or secret-shaped value"
        )
    return value


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None or value == "0" * 64:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid")
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid")
    return value


def _lsn(value: object, *, label: str, allow_none: bool) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or _LSN.fullmatch(value) is None:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid")
    high, low = value.split("/", 1)
    if int(high, 16) == 0 and int(low, 16) == 0:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is zero")
    return value


def _lsn_value(value: str) -> int:
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) + int(low, 16)


def _canonical_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid")
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat().replace("+00:00", "Z") != value:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is not canonical UTC")
    return normalized


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _reject_unsafe_string_leaves(value: object, *, label: str) -> None:
    """Reject URLs/secrets even if a future schema edit accidentally exposes one."""

    if isinstance(value, str):
        if _URL_VALUE.search(value) or _SENSITIVE_VALUE.search(value):
            raise PhysicalPostgresDataPlanePreflightError(
                f"{label} contains a URL or secret-shaped value"
            )
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_unsafe_string_leaves(key, label=label)
            _reject_unsafe_string_leaves(nested, label=label)
        return
    if isinstance(value, list):
        for nested in value:
            _reject_unsafe_string_leaves(nested, label=label)


def _parse_canonical_raw_evidence(raw_evidence: object) -> dict[str, Any]:
    if not isinstance(raw_evidence, bytes) or not raw_evidence:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL raw evidence is invalid")
    if len(raw_evidence) > MAX_PHYSICAL_POSTGRES_READBACK_BYTES:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL raw evidence exceeds the bounded size"
        )
    try:
        text = raw_evidence.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL raw evidence is not UTF-8"
        ) from exc
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, PhysicalPostgresDataPlanePreflightError):
            raise
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL raw evidence is invalid JSON"
        ) from exc
    if not isinstance(parsed, dict) or _canonical_json_bytes(
        parsed, label="physical PostgreSQL raw evidence"
    ) != raw_evidence:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL raw evidence is not canonical"
        )
    _reject_unsafe_string_leaves(parsed, label="physical PostgreSQL raw evidence")
    return parsed


def _command_descriptor(value: object, *, label: str) -> PhysicalPostgresCommandDescriptor:
    item = _exact_mapping(value, label=label, fields={"identity", "sha256"})
    return PhysicalPostgresCommandDescriptor(
        identity=_safe_identifier(item["identity"], label=f"{label} identity"),
        sha256=_sha256(item["sha256"], label=f"{label} SHA-256"),
    )


def _writer_term(value: object, *, label: str) -> PhysicalPostgresWriterTermBinding:
    item = _exact_mapping(
        value,
        label=label,
        fields={
            "holder_site",
            "writer_epoch",
            "writer_lease_id",
            "witness_transition_id",
            "proof_sha256",
        },
    )
    holder_site = item["holder_site"]
    if holder_site not in {WEBAPP_FI, WEBAPP_IR}:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} holder site is invalid")
    epoch = _positive_int(item["writer_epoch"], label=f"{label} writer epoch", maximum=2**63 - 1)
    lease_id = item["writer_lease_id"]
    if not isinstance(lease_id, str) or LEASE_ID_RE.fullmatch(lease_id) is None:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} writer lease is invalid")
    return PhysicalPostgresWriterTermBinding(
        holder_site=holder_site,
        writer_epoch=epoch,
        writer_lease_id=lease_id,
        witness_transition_id=_safe_identifier(
            item["witness_transition_id"], label=f"{label} Witness transition"
        ),
        proof_sha256=_sha256(item["proof_sha256"], label=f"{label} proof SHA-256"),
    )


def _synchronous_settings(value: object, *, label: str) -> tuple[
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
    str,
]:
    item = _exact_mapping(
        value,
        label=label,
        fields={
            "mode",
            "receiver_site",
            "receiver_application_name",
            "receiver_slot_name",
            "receiver_evidence_sha256",
            "acknowledgement",
            "transport",
        },
    )
    mode = item["mode"]
    # This topology intentionally has no FI-to-IR PostgreSQL connection.
    # A native synchronous receiver / ``remote_apply`` claim therefore cannot
    # be used as a substitute for the separate Object-Storage replay ledger.
    # Keep the full shape in the evidence so a future adapter cannot hide it,
    # but accept only the explicit disabled state.
    if (
        mode != "disabled"
        or item["receiver_site"] is not None
        or item["receiver_application_name"] is not None
        or item["receiver_slot_name"] is not None
        or item["receiver_evidence_sha256"] is not None
        or item["acknowledgement"] != "none"
        or item["transport"] != "none"
    ):
        raise PhysicalPostgresDataPlanePreflightError(
            f"{label} native synchronous receiver is forbidden for the Object-Storage pull route"
        )
    return ("disabled", None, None, None, None, "none", "none")


def _postgres_settings(value: object, *, label: str) -> _PostgresSettings:
    item = _exact_mapping(
        value,
        label=label,
        fields={
            "wal_level",
            "archive_mode",
            "archive_command",
            "restore_command",
            "max_wal_senders",
            "max_replication_slots",
            "hot_standby",
            "synchronous_commit",
            "synchronous_standby",
        },
    )
    if item["wal_level"] != "replica":
        raise PhysicalPostgresDataPlanePreflightError(f"{label} wal_level must be replica")
    if item["archive_mode"] not in {"on", "always"}:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} archive_mode is invalid")
    if item["hot_standby"] not in {"on", "off"}:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} hot_standby is invalid")
    if item["synchronous_commit"] not in {"on", "local"}:
        raise PhysicalPostgresDataPlanePreflightError(
            f"{label} synchronous_commit is invalid"
        )
    (
        synchronous_mode,
        receiver_site,
        receiver_application_name,
        receiver_slot_name,
        receiver_evidence_sha256,
        acknowledgement,
        transport,
    ) = _synchronous_settings(item["synchronous_standby"], label=f"{label} synchronous standby")
    return _PostgresSettings(
        wal_level="replica",
        archive_mode=item["archive_mode"],
        archive_command=_command_descriptor(item["archive_command"], label=f"{label} archive command"),
        restore_command=_command_descriptor(item["restore_command"], label=f"{label} restore command"),
        max_wal_senders=_positive_int(
            item["max_wal_senders"], label=f"{label} max_wal_senders", maximum=10000
        ),
        max_replication_slots=_positive_int(
            item["max_replication_slots"], label=f"{label} max_replication_slots", maximum=10000
        ),
        hot_standby=item["hot_standby"],
        synchronous_commit=item["synchronous_commit"],
        synchronous_mode=synchronous_mode,
        receiver_site=receiver_site,
        receiver_application_name=receiver_application_name,
        receiver_slot_name=receiver_slot_name,
        receiver_evidence_sha256=receiver_evidence_sha256,
        synchronous_acknowledgement=acknowledgement,
        synchronous_transport=transport,
    )


def _physical_state(value: object, *, label: str) -> _PhysicalState:
    item = _exact_mapping(
        value,
        label=label,
        fields={
            "timeline",
            "base_generation_id",
            "base_backup_sha256",
            "source_wal_frontier",
            "archived_wal_frontier",
            "replay_frontier",
            "acknowledged_wal_frontier",
        },
    )
    return _PhysicalState(
        timeline=_positive_int(item["timeline"], label=f"{label} timeline", maximum=2**31 - 1),
        base_generation_id=_safe_identifier(
            item["base_generation_id"], label=f"{label} base generation"
        ),
        base_backup_sha256=_sha256(
            item["base_backup_sha256"], label=f"{label} base backup SHA-256"
        ),
        source_wal_frontier=_lsn(
            item["source_wal_frontier"], label=f"{label} source WAL frontier", allow_none=True
        ),
        archived_wal_frontier=_lsn(
            item["archived_wal_frontier"],
            label=f"{label} archived WAL frontier",
            allow_none=False,
        ),
        replay_frontier=_lsn(
            item["replay_frontier"], label=f"{label} replay frontier", allow_none=True
        ),
        acknowledged_wal_frontier=_lsn(
            item["acknowledged_wal_frontier"],
            label=f"{label} acknowledged WAL frontier",
            allow_none=True,
        ),
    )


def _transport_assumptions(value: object, *, label: str) -> _TransportAssumptions:
    item = _exact_mapping(
        value,
        label=label,
        fields={
            "archive_transport",
            "wa_ir_object_ingest",
            "direct_fi_to_ir_control",
            "wal_delivery_mode",
        },
    )
    if item["archive_transport"] != ARCHIVE_TRANSPORT:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} archive transport is invalid")
    if item["wa_ir_object_ingest"] != WA_IR_OBJECT_INGEST:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} WA-IR ingest mode is invalid")
    if item["direct_fi_to_ir_control"] != DIRECT_FI_TO_IR_CONTROL:
        raise PhysicalPostgresDataPlanePreflightError(
            f"{label} direct FI-to-IR control posture is invalid"
        )
    if item["wal_delivery_mode"] not in {
        ARCHIVE_ONLY_DELIVERY,
        STRICT_OBJECT_STORAGE_REMOTE_DURABLE_REPLAY_DELIVERY,
    }:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} WAL delivery mode is invalid")
    return _TransportAssumptions(
        archive_transport=ARCHIVE_TRANSPORT,
        wa_ir_object_ingest=WA_IR_OBJECT_INGEST,
        direct_fi_to_ir_control=DIRECT_FI_TO_IR_CONTROL,
        wal_delivery_mode=item["wal_delivery_mode"],
    )


def _readback_facts(value: Mapping[str, Any]) -> _ReadbackFacts:
    item = _exact_mapping(
        value,
        label="physical PostgreSQL readback",
        fields={
            "schema",
            "site",
            "observed_role",
            "durability_claim",
            "release_id",
            "schema_revision",
            "writer_term",
            "postgres",
            "physical",
            "transport",
            "observed_at",
        },
    )
    if item["schema"] != PHYSICAL_POSTGRES_DATA_PLANE_EVIDENCE_SCHEMA:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL evidence schema is invalid")
    if item["site"] not in {WEBAPP_FI, WEBAPP_IR}:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL evidence site is invalid")
    if item["observed_role"] not in {PRIMARY_ROLE, STANDBY_ROLE}:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL observed role is invalid")
    if item["durability_claim"] not in _DURABILITY_PROFILES:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL durability claim is invalid")
    return _ReadbackFacts(
        site=item["site"],
        observed_role=item["observed_role"],
        durability_claim=item["durability_claim"],
        release_id=_safe_identifier(item["release_id"], label="physical PostgreSQL release"),
        schema_revision=_safe_identifier(
            item["schema_revision"], label="physical PostgreSQL schema revision"
        ),
        writer_term=_writer_term(item["writer_term"], label="physical PostgreSQL writer term"),
        postgres=_postgres_settings(item["postgres"], label="physical PostgreSQL settings"),
        physical=_physical_state(item["physical"], label="physical PostgreSQL physical state"),
        transport=_transport_assumptions(item["transport"], label="physical PostgreSQL transport"),
        observed_at=_canonical_timestamp(item["observed_at"], label="physical PostgreSQL observed_at"),
    )


def _validate_provenance(value: object) -> PhysicalPostgresEvidenceProvenance:
    if type(value) is not PhysicalPostgresEvidenceProvenance:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL evidence provenance is invalid")
    if value.site not in {WEBAPP_FI, WEBAPP_IR}:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL provenance site is invalid")
    collector = _safe_identifier(
        value.collector_identity, label="physical PostgreSQL provenance collector"
    )
    if collector != "read-only-postgres-settings-agent-v1":
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL provenance collector is not approved"
        )
    path = value.logical_path
    if not isinstance(path, str) or _PROVENANCE_PATH.fullmatch(path) is None:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL provenance path is invalid")
    if path != f"physical-postgres-preflight/{value.site}/readback-v1.json":
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL provenance path is mismatched")
    return PhysicalPostgresEvidenceProvenance(
        site=value.site,
        collector_identity=collector,
        logical_path=path,
        evidence_sha256=_sha256(
            value.evidence_sha256, label="physical PostgreSQL provenance SHA-256"
        ),
    )


def verify_physical_postgres_readback_evidence(
    value: object,
) -> _VerifiedPhysicalPostgresReadback:
    """Verify one canonical bounded raw readback + typed provenance/hash envelope."""

    if type(value) is not PhysicalPostgresReadbackEvidence:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL readback envelope is invalid")
    provenance = _validate_provenance(value.provenance)
    raw = value.raw_evidence
    parsed = _parse_canonical_raw_evidence(raw)
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    envelope_sha256 = _sha256(value.evidence_sha256, label="physical PostgreSQL evidence SHA-256")
    if actual_sha256 != envelope_sha256 or actual_sha256 != provenance.evidence_sha256:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL evidence SHA-256 does not bind the raw bytes"
        )
    facts = _readback_facts(parsed)
    if facts.site != provenance.site:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL evidence site does not match provenance"
        )
    verified = _VerifiedPhysicalPostgresReadback(
        evidence=PhysicalPostgresReadbackEvidence(
            raw_evidence=raw,
            evidence_sha256=actual_sha256,
            provenance=provenance,
        ),
        facts=facts,
    )
    object.__setattr__(verified, "_capability", _VERIFIED_READBACK_CAPABILITY)
    return verified


def _normalise_command_descriptor(
    value: object, *, label: str
) -> PhysicalPostgresCommandDescriptor:
    if type(value) is not PhysicalPostgresCommandDescriptor:
        raise PhysicalPostgresDataPlanePreflightError(f"{label} is invalid")
    return PhysicalPostgresCommandDescriptor(
        identity=_safe_identifier(value.identity, label=f"{label} identity"),
        sha256=_sha256(value.sha256, label=f"{label} SHA-256"),
    )


def _normalise_term_binding(value: object) -> PhysicalPostgresWriterTermBinding:
    if type(value) is not PhysicalPostgresWriterTermBinding:
        raise PhysicalPostgresDataPlanePreflightError("physical PostgreSQL expected term is invalid")
    return _writer_term(
        {
            "holder_site": value.holder_site,
            "writer_epoch": value.writer_epoch,
            "writer_lease_id": value.writer_lease_id,
            "witness_transition_id": value.witness_transition_id,
            "proof_sha256": value.proof_sha256,
        },
        label="physical PostgreSQL expected term",
    )


def validate_physical_postgres_data_plane_preflight_binding(
    value: object,
) -> PhysicalPostgresDataPlanePreflightBinding:
    """Validate the typed, independently trusted expected configuration binding."""

    if type(value) is not PhysicalPostgresDataPlanePreflightBinding:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL preflight binding is invalid"
        )
    term = _normalise_term_binding(value.active_term)
    if term.holder_site != WEBAPP_FI:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL normal preflight requires a FI-held writer term"
        )
    return PhysicalPostgresDataPlanePreflightBinding(
        release_id=_safe_identifier(value.release_id, label="physical PostgreSQL expected release"),
        schema_revision=_safe_identifier(
            value.schema_revision, label="physical PostgreSQL expected schema revision"
        ),
        active_term=term,
        base_generation_id=_safe_identifier(
            value.base_generation_id, label="physical PostgreSQL expected base generation"
        ),
        base_backup_sha256=_sha256(
            value.base_backup_sha256, label="physical PostgreSQL expected base backup SHA-256"
        ),
        fi_archive_command=_normalise_command_descriptor(
            value.fi_archive_command, label="physical PostgreSQL FI archive command"
        ),
        fi_restore_command=_normalise_command_descriptor(
            value.fi_restore_command, label="physical PostgreSQL FI restore command"
        ),
        ir_archive_command=_normalise_command_descriptor(
            value.ir_archive_command, label="physical PostgreSQL IR archive command"
        ),
        ir_restore_command=_normalise_command_descriptor(
            value.ir_restore_command, label="physical PostgreSQL IR restore command"
        ),
    )


def _require_verified_readback(value: object) -> _VerifiedPhysicalPostgresReadback:
    if (
        type(value) is not _VerifiedPhysicalPostgresReadback
        or value._capability is not _VERIFIED_READBACK_CAPABILITY
    ):
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL verified readback capability is invalid"
        )
    reverified = verify_physical_postgres_readback_evidence(value.evidence)
    if reverified.facts != value.facts:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL verified readback facts no longer match evidence"
        )
    return reverified


def _validate_role_specific_posture(
    facts: _ReadbackFacts,
    *,
    expected_site: str,
    expected_role: str,
) -> list[str]:
    reasons: list[str] = []
    if facts.site != expected_site:
        reasons.append(f"{expected_site}-site-mismatch")
    if facts.observed_role != expected_role:
        reasons.append(f"{expected_site}-role-mismatch")
    physical = facts.physical
    postgres = facts.postgres
    if expected_role == PRIMARY_ROLE:
        if physical.source_wal_frontier is None:
            reasons.append("fi-primary-source-wal-frontier-missing")
        if physical.replay_frontier is not None:
            reasons.append("fi-primary-replay-frontier-must-be-null")
        if physical.acknowledged_wal_frontier is None:
            reasons.append("fi-primary-acknowledged-wal-frontier-missing")
        if (
            physical.source_wal_frontier is not None
            and _lsn_value(physical.archived_wal_frontier)
            > _lsn_value(physical.source_wal_frontier)
        ):
            reasons.append("fi-archive-frontier-exceeds-source-frontier")
        if (
            physical.source_wal_frontier is not None
            and physical.acknowledged_wal_frontier is not None
            and _lsn_value(physical.acknowledged_wal_frontier)
            > _lsn_value(physical.source_wal_frontier)
        ):
            reasons.append("fi-acknowledged-frontier-exceeds-source-frontier")
    else:
        if postgres.hot_standby != "on":
            reasons.append("ir-standby-hot-standby-is-not-on")
        if postgres.archive_mode != "always":
            reasons.append("ir-standby-archive-mode-is-not-always")
        if physical.source_wal_frontier is not None:
            reasons.append("ir-standby-source-wal-frontier-must-be-null")
        if physical.replay_frontier is None:
            reasons.append("ir-standby-replay-frontier-missing")
        if physical.acknowledged_wal_frontier is not None:
            reasons.append("ir-standby-acknowledged-frontier-must-be-null")
    return reasons


def _validate_binding_match(
    facts: _ReadbackFacts,
    *,
    binding: PhysicalPostgresDataPlanePreflightBinding,
    expected_site: str,
) -> list[str]:
    reasons: list[str] = []
    if facts.release_id != binding.release_id:
        reasons.append(f"{expected_site}-release-binding-mismatch")
    if facts.schema_revision != binding.schema_revision:
        reasons.append(f"{expected_site}-schema-binding-mismatch")
    if facts.writer_term != binding.active_term:
        reasons.append(f"{expected_site}-writer-term-binding-mismatch")
    if facts.physical.base_generation_id != binding.base_generation_id:
        reasons.append(f"{expected_site}-base-generation-binding-mismatch")
    if facts.physical.base_backup_sha256 != binding.base_backup_sha256:
        reasons.append(f"{expected_site}-base-backup-binding-mismatch")
    expected_archive = (
        binding.fi_archive_command if expected_site == WEBAPP_FI else binding.ir_archive_command
    )
    expected_restore = (
        binding.fi_restore_command if expected_site == WEBAPP_FI else binding.ir_restore_command
    )
    if facts.postgres.archive_command != expected_archive:
        reasons.append(f"{expected_site}-archive-command-binding-mismatch")
    if facts.postgres.restore_command != expected_restore:
        reasons.append(f"{expected_site}-restore-command-binding-mismatch")
    return reasons


def _within_evidence_window(
    observed_at: datetime,
    *,
    now: datetime,
    maximum_evidence_age_seconds: int,
    site: str,
) -> list[str]:
    if observed_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        return [f"{site}-evidence-is-from-the-future"]
    if observed_at < now - timedelta(seconds=maximum_evidence_age_seconds):
        return [f"{site}-evidence-is-stale"]
    return []


def _normalise_requested_profile(value: object) -> str:
    if value not in _DURABILITY_PROFILES:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL requested durability profile is invalid"
        )
    return str(value)


def _normalise_maximum_age(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_EVIDENCE_AGE_SECONDS:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL maximum evidence age is invalid"
        )
    return value


def _assessment_reasons(
    *,
    fi: _ReadbackFacts,
    ir: _ReadbackFacts,
    fi_evidence_sha256: str,
    ir_evidence_sha256: str,
    binding: PhysicalPostgresDataPlanePreflightBinding,
    requested_profile: str,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    reasons.extend(
        _validate_role_specific_posture(
            fi, expected_site=WEBAPP_FI, expected_role=PRIMARY_ROLE
        )
    )
    reasons.extend(
        _validate_role_specific_posture(
            ir, expected_site=WEBAPP_IR, expected_role=STANDBY_ROLE
        )
    )
    reasons.extend(_validate_binding_match(fi, binding=binding, expected_site=WEBAPP_FI))
    reasons.extend(_validate_binding_match(ir, binding=binding, expected_site=WEBAPP_IR))
    reasons.extend(
        _within_evidence_window(
            fi.observed_at,
            now=now,
            maximum_evidence_age_seconds=maximum_evidence_age_seconds,
            site=WEBAPP_FI,
        )
    )
    reasons.extend(
        _within_evidence_window(
            ir.observed_at,
            now=now,
            maximum_evidence_age_seconds=maximum_evidence_age_seconds,
            site=WEBAPP_IR,
        )
    )

    if fi.release_id != ir.release_id:
        reasons.append("fi-ir-release-mismatch")
    if fi.schema_revision != ir.schema_revision:
        reasons.append("fi-ir-schema-revision-mismatch")
    if fi.writer_term != ir.writer_term:
        reasons.append("fi-ir-writer-term-mismatch")
    if fi.physical.timeline != ir.physical.timeline:
        reasons.append("fi-ir-timeline-mismatch")
    if fi.physical.base_generation_id != ir.physical.base_generation_id:
        reasons.append("fi-ir-base-generation-mismatch")
    if fi.physical.base_backup_sha256 != ir.physical.base_backup_sha256:
        reasons.append("fi-ir-base-backup-mismatch")
    if fi.transport.wal_delivery_mode != ir.transport.wal_delivery_mode:
        reasons.append("fi-ir-wal-delivery-mode-mismatch")
    if fi.durability_claim != ir.durability_claim:
        reasons.append("fi-ir-durability-claim-mismatch")
    if fi.durability_claim != requested_profile or ir.durability_claim != requested_profile:
        reasons.append("readback-durability-claim-does-not-match-request")

    if fi.physical.source_wal_frontier is not None and ir.physical.replay_frontier is not None:
        if _lsn_value(ir.physical.replay_frontier) > _lsn_value(fi.physical.source_wal_frontier):
            reasons.append("ir-replay-frontier-exceeds-fi-source-frontier")
    if _lsn_value(ir.physical.archived_wal_frontier) > _lsn_value(fi.physical.archived_wal_frontier):
        reasons.append("ir-archive-frontier-exceeds-fi-archive-frontier")

    delivery_mode = fi.transport.wal_delivery_mode
    if requested_profile == STRICT_ZERO_LOSS:
        if delivery_mode == ARCHIVE_ONLY_DELIVERY:
            reasons.append("strict-zero-loss-is-blocked-for-archive-only-wal-delivery")
        if delivery_mode != STRICT_OBJECT_STORAGE_REMOTE_DURABLE_REPLAY_DELIVERY:
            reasons.append(
                "strict-zero-loss-requires-object-storage-remote-durable-replay-delivery"
            )
        if fi.postgres.synchronous_commit != "on":
            reasons.append("strict-zero-loss-requires-local-wal-durability")
        if fi.postgres.synchronous_mode != "disabled":
            reasons.append("strict-zero-loss-forbids-native-postgresql-synchronous-receiver")
        if (
            fi.physical.acknowledged_wal_frontier is not None
            and ir.physical.replay_frontier is not None
            and _lsn_value(ir.physical.replay_frontier)
            < _lsn_value(fi.physical.acknowledged_wal_frontier)
        ):
            reasons.append("ir-replay-frontier-is-behind-fi-acknowledged-frontier")
        # The local preflight can inspect neither the durable receiver ledger
        # nor the source write-admission hook.  Until those runtime adapters
        # are installed and independently verified, it must never elevate the
        # Object-Storage label to a strict/no-loss observation.
        reasons.append("strict-remote-durable-replay-runtime-not-implemented")
    else:
        if delivery_mode != ARCHIVE_ONLY_DELIVERY:
            reasons.append("archive-bounded-rpo-requires-archive-only-wal-delivery")
        if fi.postgres.synchronous_mode != "disabled":
            reasons.append("archive-bounded-rpo-forbids-native-postgresql-synchronous-receiver")
        if fi.physical.acknowledged_wal_frontier is None:
            reasons.append("archive-bounded-rpo-fi-acknowledged-frontier-missing")

    # De-duplicate while retaining deterministic explanatory order.
    return tuple(dict.fromkeys(reasons))


def assess_physical_postgres_data_plane_preflight(
    *,
    fi_readback: PhysicalPostgresReadbackEvidence,
    ir_readback: PhysicalPostgresReadbackEvidence,
    expected_binding: PhysicalPostgresDataPlanePreflightBinding,
    requested_durability_profile: str,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
) -> PhysicalPostgresDataPlanePreflight:
    """Assess a normal FI-primary / IR-standby posture without enabling it.

    Individual malformed evidence is rejected with an exception.  Coherent but
    unsafe combinations return a signed-by-nobody, default-off ``blocked``
    posture so an outer read-only adapter can report all observed causes
    without treating them as a promotion permission.
    """

    verified_fi = _require_verified_readback(verify_physical_postgres_readback_evidence(fi_readback))
    verified_ir = _require_verified_readback(verify_physical_postgres_readback_evidence(ir_readback))
    binding = validate_physical_postgres_data_plane_preflight_binding(expected_binding)
    requested = _normalise_requested_profile(requested_durability_profile)
    observed_now = _utc(now, label="physical PostgreSQL preflight clock")
    maximum_age = _normalise_maximum_age(maximum_evidence_age_seconds)
    fi = verified_fi.facts
    ir = verified_ir.facts
    reasons = _assessment_reasons(
        fi=fi,
        ir=ir,
        fi_evidence_sha256=verified_fi.evidence.evidence_sha256,
        ir_evidence_sha256=verified_ir.evidence.evidence_sha256,
        binding=binding,
        requested_profile=requested,
        now=observed_now,
        maximum_evidence_age_seconds=maximum_age,
    )
    if fi.physical.source_wal_frontier is None or fi.physical.acknowledged_wal_frontier is None:
        # Role-specific reasons already explain this, but no unsafe optional
        # value may escape in the public result.
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL FI primary frontiers are invalid"
        )
    if ir.physical.replay_frontier is None:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL IR standby replay frontier is invalid"
        )
    result = PhysicalPostgresDataPlanePreflight(
        schema=PHYSICAL_POSTGRES_DATA_PLANE_RESULT_SCHEMA,
        status=PREFLIGHT_STATUS_BLOCKED if reasons else PREFLIGHT_STATUS_OBSERVED,
        requested_durability_profile=requested,
        observed_durability_profile=requested if not reasons else None,
        reasons=reasons,
        fi_evidence_sha256=verified_fi.evidence.evidence_sha256,
        ir_evidence_sha256=verified_ir.evidence.evidence_sha256,
        release_id=binding.release_id,
        schema_revision=binding.schema_revision,
        writer_epoch=binding.active_term.writer_epoch,
        writer_lease_id=binding.active_term.writer_lease_id,
        witness_transition_id=binding.active_term.witness_transition_id,
        base_generation_id=binding.base_generation_id,
        base_backup_sha256=binding.base_backup_sha256,
        timeline=fi.physical.timeline,
        fi_source_wal_frontier=fi.physical.source_wal_frontier,
        fi_archived_wal_frontier=fi.physical.archived_wal_frontier,
        fi_acknowledged_wal_frontier=fi.physical.acknowledged_wal_frontier,
        ir_archived_wal_frontier=ir.physical.archived_wal_frontier,
        ir_replay_frontier=ir.physical.replay_frontier,
        _fi_evidence=verified_fi.evidence,
        _ir_evidence=verified_ir.evidence,
        _binding=binding,
        _maximum_evidence_age_seconds=maximum_age,
    )
    object.__setattr__(result, "_capability", _PREFLIGHT_CAPABILITY)
    return result


def _public_projection(
    value: PhysicalPostgresDataPlanePreflight,
) -> tuple[object, ...]:
    return (
        value.schema,
        value.status,
        value.requested_durability_profile,
        value.observed_durability_profile,
        value.reasons,
        value.fi_evidence_sha256,
        value.ir_evidence_sha256,
        value.release_id,
        value.schema_revision,
        value.writer_epoch,
        value.writer_lease_id,
        value.witness_transition_id,
        value.base_generation_id,
        value.base_backup_sha256,
        value.timeline,
        value.fi_source_wal_frontier,
        value.fi_archived_wal_frontier,
        value.fi_acknowledged_wal_frontier,
        value.ir_archived_wal_frontier,
        value.ir_replay_frontier,
    )


def require_observed_physical_postgres_data_plane_preflight(
    value: object,
    *,
    now: datetime,
) -> PhysicalPostgresDataPlanePreflight:
    """Re-validate an opaque observed posture at a caller-supplied fresh clock.

    The return value remains an observation only.  A caller still needs a
    live Witness term, a real replication-health probe, writer fencing, and a
    promotion-specific gate before it can change traffic or write authority.
    """

    if (
        type(value) is not PhysicalPostgresDataPlanePreflight
        or value._capability is not _PREFLIGHT_CAPABILITY
    ):
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL preflight capability is invalid"
        )
    refreshed = assess_physical_postgres_data_plane_preflight(
        fi_readback=value._fi_evidence,
        ir_readback=value._ir_evidence,
        expected_binding=value._binding,
        requested_durability_profile=value.requested_durability_profile,
        now=now,
        maximum_evidence_age_seconds=value._maximum_evidence_age_seconds,
    )
    if _public_projection(refreshed) != _public_projection(value):
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL preflight is blocked or no longer matches its evidence"
        )
    if refreshed.status != PREFLIGHT_STATUS_OBSERVED:
        raise PhysicalPostgresDataPlanePreflightError(
            "physical PostgreSQL data-plane posture is blocked"
        )
    return refreshed
