"""Data-only controller validation for convergence-observer records.

This module deliberately contains only deterministic parsing and validation of
the redacted records that a role observer publishes.  It does not import the
role worker, execute a command, open a network connection, read Object
Storage, mutate a filesystem path, or expose a release descriptor.  The role
worker remains responsible for its own local filesystem and launcher checks.

The controller-facing request validator checks canonical serialized paths as
data.  It intentionally does not inspect those remote-host paths: such an
inspection would neither prove the remote state nor belong in a future
FD-pinned controller source map.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping
from uuid import UUID


__all__ = (
    "ATTESTATION_SCHEMA",
    "CONTRACT_SCHEMA",
    "ConvergenceObservationContractError",
    "EXPECTED_CONSTRAINTS",
    "HOST_IDENTITY_PROOF_SCHEMA",
    "MAX_JSON_BYTES",
    "OPERATION",
    "PHASE",
    "REQUEST_SCHEMA",
    "ROLES",
    "RUNTIME_SNAPSHOT_ROLES",
    "UNAVAILABLE_REASONS",
    "canonical_paths",
    "validate_attestation",
    "validate_compose_execution_proof",
    "validate_host_identity_proof",
    "validate_request",
)


CONTRACT_SCHEMA = "production-shadow-convergence-controller-observation-contract-v1"
REQUEST_SCHEMA = "production-shadow-convergence-role-observer-request-v2"
ATTESTATION_SCHEMA = "production-shadow-convergence-role-observation-v2"
HOST_IDENTITY_PROOF_SCHEMA = "production-shadow-convergence-local-host-ip-proof-v1"

PHASE = "convergence_gate"
OPERATION = "verify-shadow-three-site-convergence"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
RUNTIME_SNAPSHOT_ROLES = frozenset({"bot_fi", "webapp_fi", "webapp_ir"})

# These are serialized contract values, not paths opened by this module.
PROJECT_ROOT_PREFIX = PurePosixPath("/srv/trading-bot-three-site-production-shadow")
SECRET_ROOT_PREFIX = PurePosixPath(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
WORKER_RELATIVE = PurePosixPath(
    "scripts/production_shadow_convergence_observer_worker.py"
)

MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_ROWS_PER_TABLE = 100_000
MAX_REQUEST_FUTURE_SKEW = timedelta(seconds=5)
MAX_OBSERVATION_FUTURE_SKEW = timedelta(seconds=5)
MAX_OBSERVATION_AGE = timedelta(minutes=15)
MAX_CAPTURE_TO_ATTESTATION_SKEW = timedelta(minutes=2)
MAX_HOST_PROOF_TO_ATTESTATION_SKEW = timedelta(minutes=2)

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64

EXPECTED_CONSTRAINTS = {
    "plan_only_default": True,
    "read_only_runtime_snapshot_required": True,
    "caller_observation_values_forbidden": True,
    "raw_business_values_forbidden": True,
    "credentials_and_paths_forbidden": True,
    "worker_transport_io_forbidden": True,
    "direct_fi_to_ir_transfer_forbidden": True,
    "object_storage_operation_forbidden": True,
    "unsupported_observations_fail_closed": True,
    "create_only_root_only_artifact_required": True,
    "fixed_isolated_release_collector_required": True,
    "local_expected_host_ip_proof_required": True,
    "runtime_target_binding_required_for_runtime_roles": True,
}

REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "role",
        "expected_host",
        "phase_started_at",
        "release_root",
        "worker_path",
        "worker_sha256",
        "output_root",
        "max_rows_per_table",
        "constraints",
        "request_sha256",
    }
)
HOST_IDENTITY_PROOF_FIELDS = frozenset(
    {
        "schema",
        "expected_host",
        "observed_host",
        "address_family",
        "interface",
        "collector",
        "observed_at",
        "host_identity_proof_sha256",
    }
)
ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "role",
        "expected_host",
        "phase_started_at",
        "request_sha256",
        "worker_sha256",
        "host_identity_proof",
        "observed_at",
        "release_identity",
        "runtime_snapshot",
        "compose_execution",
        "available_observations",
        "unavailable_observations",
        "redaction",
        "production_mutated",
        "worker_transport_contacted",
        "object_storage_contacted",
        "attestation_sha256",
    }
)
UNAVAILABLE_REASONS = {
    "blob_roundtrip": (
        "no exact-version object-storage readback collector is bound to the "
        "role runtime"
    ),
    "queue_state": (
        "no read-only runtime collector joins mutator processes, Redis due "
        "work, effects, leases, and provider-attempt deltas"
    ),
    "dr_tls": (
        "no release-bound bidirectional DR TLS handshake collector and peer "
        "endpoint contract is available"
    ),
    "destination_firewall": (
        "no canonical operation-labelled local/provider firewall allowlist "
        "readback collector is available"
    ),
    "witness_live": (
        "no minimal read-only Witness live-status exporter binds a signed "
        "proof to this convergence journal"
    ),
}


class ConvergenceObservationContractError(RuntimeError):
    """A controller-side observer record is malformed or unbound."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConvergenceObservationContractError(
                "JSON document has duplicate fields"
            )
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConvergenceObservationContractError(
            "value is not canonical JSON"
        ) from exc


def _sha256(value: bytes | Mapping[str, Any] | list[Any]) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value)
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise ConvergenceObservationContractError(
            f"{label} must be a nonzero SHA-256"
        )
    return value


def _release_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise ConvergenceObservationContractError(
            f"{label} must be a 40-character lowercase SHA"
        )
    return value


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ConvergenceObservationContractError(
            f"{label} must be a canonical UUID"
        )
    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as exc:
        raise ConvergenceObservationContractError(
            f"{label} must be a canonical UUID"
        ) from exc
    if str(parsed) != value or parsed.int == 0:
        raise ConvergenceObservationContractError(
            f"{label} must be a nonzero canonical UUID"
        )
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ConvergenceObservationContractError(
            f"{label} must be an RFC3339 timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConvergenceObservationContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConvergenceObservationContractError(
            f"{label} must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _ipv4_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ConvergenceObservationContractError(
            f"{label} must be an IPv4 address"
        )
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ConvergenceObservationContractError(
            f"{label} must be an IPv4 address"
        ) from exc
    if str(parsed) != value or parsed.is_unspecified or parsed.is_multicast:
        raise ConvergenceObservationContractError(
            f"{label} must be a canonical IPv4 address"
        )
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_paths(
    *,
    operation_id: str,
    release_sha: str,
    role: str,
) -> dict[str, PurePosixPath]:
    """Return serialized canonical paths without opening or mutating them."""

    _canonical_uuid(operation_id, label="operation_id")
    _release_sha(release_sha, label="release_sha")
    if role not in ROLES:
        raise ConvergenceObservationContractError("observer role is invalid")
    release_root = PROJECT_ROOT_PREFIX / operation_id / "releases" / release_sha
    output_root = SECRET_ROOT_PREFIX / operation_id / "convergence-observations" / role
    return {
        "release_root": release_root,
        "worker_path": release_root / WORKER_RELATIVE,
        "output_root": output_root,
    }


def _canonical_absolute_path(
    value: Any,
    *,
    expected: PurePosixPath,
    label: str,
) -> PurePosixPath:
    if not isinstance(value, str):
        raise ConvergenceObservationContractError(f"{label} path is invalid")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
        or str(path) != value
        or path != expected
    ):
        raise ConvergenceObservationContractError(
            f"observer {label} is not canonical"
        )
    return path


def _request_digest(document: Mapping[str, Any]) -> str:
    unsigned = {
        key: value for key, value in document.items() if key != "request_sha256"
    }
    return _sha256(unsigned)


def validate_request(
    value: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one serialized role-observer request without touching a host."""

    if (
        not isinstance(value, Mapping)
        or set(value) != REQUEST_FIELDS
        or value.get("schema") != REQUEST_SCHEMA
        or value.get("status") != "authorized-read-only-observation"
        or value.get("phase") != PHASE
        or value.get("operation") != OPERATION
    ):
        raise ConvergenceObservationContractError("observer request fields differ")
    try:
        document = json.loads(
            _canonical_json(dict(value)).decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ConvergenceObservationContractError(
            "observer request is not canonical JSON"
        ) from exc
    if document != dict(value):
        raise ConvergenceObservationContractError(
            "observer request canonical form differs"
        )
    campaign_id = _canonical_uuid(document["campaign_id"], label="campaign_id")
    operation_id = _canonical_uuid(document["operation_id"], label="operation_id")
    if campaign_id == operation_id:
        raise ConvergenceObservationContractError(
            "campaign and operation IDs must differ"
        )
    release_sha = _release_sha(document["release_sha"], label="release_sha")
    _release_sha(document["release_tree_sha"], label="release_tree_sha")
    role = document.get("role")
    if role not in ROLES:
        raise ConvergenceObservationContractError("observer role is invalid")
    _ipv4_text(document.get("expected_host"), label="observer expected host")
    for field in ("manifest_sha256", "plan_sha256", "approval_sha256", "worker_sha256"):
        _nonzero_sha256(document[field], label=field)
    runtime_target_binding = document["runtime_target_binding_sha256"]
    if role in RUNTIME_SNAPSHOT_ROLES:
        _nonzero_sha256(
            runtime_target_binding,
            label="runtime_target_binding_sha256",
        )
    elif runtime_target_binding is not None:
        raise ConvergenceObservationContractError(
            "Witness observer must carry a null runtime target binding"
        )
    if (
        type(document.get("max_rows_per_table")) is not int
        or not 1 <= document["max_rows_per_table"] <= MAX_ROWS_PER_TABLE
        or document.get("constraints") != EXPECTED_CONSTRAINTS
    ):
        raise ConvergenceObservationContractError(
            "observer limits or constraints differ"
        )
    phase_started_at = _timestamp(
        document["phase_started_at"],
        label="phase_started_at",
    )
    current = (now or _utcnow()).astimezone(timezone.utc)
    if phase_started_at > current + MAX_REQUEST_FUTURE_SKEW:
        raise ConvergenceObservationContractError(
            "observer request predates its durable phase start"
        )
    expected = canonical_paths(
        operation_id=operation_id,
        release_sha=release_sha,
        role=role,
    )
    for field in ("release_root", "worker_path", "output_root"):
        _canonical_absolute_path(
            document[field],
            expected=expected[field],
            label=field,
        )
    if document["request_sha256"] != _request_digest(document):
        raise ConvergenceObservationContractError("observer request digest differs")
    return document


def validate_compose_execution_proof(
    value: Any,
    *,
    expected_execution_plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate only the redacted, already-collected Compose receipt."""

    if not isinstance(value, Mapping) or set(value) != {
        "execution_plan_sha256",
        "receipt_sha256",
        "container_id_sha256",
        "network_id_sha256",
        "cleanup_verified",
    }:
        raise ConvergenceObservationContractError(
            "Compose execution proof fields differ"
        )
    document = dict(value)
    for field in (
        "execution_plan_sha256",
        "receipt_sha256",
        "container_id_sha256",
        "network_id_sha256",
    ):
        _nonzero_sha256(document.get(field), label=f"Compose execution proof {field}")
    if (
        expected_execution_plan_sha256 is not None
        and document["execution_plan_sha256"] != expected_execution_plan_sha256
    ):
        raise ConvergenceObservationContractError(
            "Compose execution proof plan differs from installed plan"
        )
    if document.get("cleanup_verified") is not True:
        raise ConvergenceObservationContractError(
            "Compose execution cleanup proof differs"
        )
    return document


def _attestation_digest(document: Mapping[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in document.items()
        if key != "attestation_sha256"
    }
    return _sha256(unsigned)


def _host_identity_proof_digest(document: Mapping[str, Any]) -> str:
    return _sha256(
        {
            key: value
            for key, value in document.items()
            if key != "host_identity_proof_sha256"
        }
    )


def validate_host_identity_proof(
    value: Any,
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a previously collected local-address proof as data."""

    bound = validate_request(request, now=now)
    if (
        not isinstance(value, Mapping)
        or set(value) != HOST_IDENTITY_PROOF_FIELDS
        or value.get("schema") != HOST_IDENTITY_PROOF_SCHEMA
        or value.get("address_family") != "inet"
        or value.get("collector") != "kernel-ip-json"
    ):
        raise ConvergenceObservationContractError(
            "local host identity proof fields differ"
        )
    document = dict(value)
    expected_host = _ipv4_text(
        document.get("expected_host"),
        label="proof expected host",
    )
    observed_host = _ipv4_text(
        document.get("observed_host"),
        label="proof observed host",
    )
    if expected_host != bound["expected_host"] or observed_host != expected_host:
        raise ConvergenceObservationContractError(
            "local host identity proof does not bind the expected host"
        )
    interface = document.get("interface")
    if (
        not isinstance(interface, str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", interface) is None
    ):
        raise ConvergenceObservationContractError(
            "local host identity proof interface is invalid"
        )
    observed_at = _timestamp(
        document.get("observed_at"),
        label="host identity proof time",
    )
    phase_started_at = _timestamp(
        bound["phase_started_at"],
        label="phase_started_at",
    )
    current = (now or _utcnow()).astimezone(timezone.utc)
    if observed_at < phase_started_at:
        raise ConvergenceObservationContractError(
            "local host identity proof predates phase start"
        )
    if observed_at > current + MAX_OBSERVATION_FUTURE_SKEW:
        raise ConvergenceObservationContractError(
            "local host identity proof is future dated"
        )
    if current - observed_at > MAX_OBSERVATION_AGE:
        raise ConvergenceObservationContractError(
            "local host identity proof is stale"
        )
    if document.get("host_identity_proof_sha256") != _host_identity_proof_digest(
        document
    ):
        raise ConvergenceObservationContractError(
            "local host identity proof digest differs"
        )
    return document


def validate_attestation(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one redacted observer attestation without executing a worker."""

    bound = validate_request(request, now=now)
    if (
        not isinstance(value, Mapping)
        or set(value) != ATTESTATION_FIELDS
        or value.get("schema") != ATTESTATION_SCHEMA
        or value.get("status") != "observed"
    ):
        raise ConvergenceObservationContractError(
            "role attestation fields differ"
        )
    document = dict(value)
    for field in (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "role",
        "expected_host",
        "phase_started_at",
        "request_sha256",
        "worker_sha256",
    ):
        if document.get(field) != bound.get(field):
            raise ConvergenceObservationContractError(
                f"role attestation {field} differs from request"
            )
    observed_at = _timestamp(document["observed_at"], label="role observation time")
    if observed_at < _timestamp(
        bound["phase_started_at"],
        label="phase_started_at",
    ):
        raise ConvergenceObservationContractError(
            "role attestation predates phase start"
        )
    current = (now or _utcnow()).astimezone(timezone.utc)
    if observed_at > current + MAX_OBSERVATION_FUTURE_SKEW:
        raise ConvergenceObservationContractError(
            "role attestation is future dated"
        )
    if current - observed_at > MAX_OBSERVATION_AGE:
        raise ConvergenceObservationContractError("role attestation is stale")
    host_identity_proof = validate_host_identity_proof(
        document.get("host_identity_proof"),
        request=bound,
        now=current,
    )
    host_proof_at = _timestamp(
        host_identity_proof["observed_at"],
        label="host identity proof time",
    )
    if (
        host_proof_at > observed_at
        or observed_at - host_proof_at > MAX_HOST_PROOF_TO_ATTESTATION_SKEW
    ):
        raise ConvergenceObservationContractError(
            "host identity proof-to-attestation skew is invalid"
        )
    if (
        document.get("production_mutated") is not False
        or document.get("worker_transport_contacted") is not False
        or document.get("object_storage_contacted") is not False
    ):
        raise ConvergenceObservationContractError(
            "role attestation reports an out-of-scope action"
        )
    if document.get("redaction") != {
        "contains_credentials": False,
        "contains_raw_database_values": False,
        "contains_file_paths": False,
        "contains_object_keys": False,
        "contains_presigned_urls": False,
    }:
        raise ConvergenceObservationContractError(
            "role attestation redaction declaration differs"
        )
    release_identity = document.get("release_identity")
    if not isinstance(release_identity, Mapping) or set(release_identity) != {
        "release_root_sha256",
        "head",
        "tree",
        "source_tree_bound",
        "worker_sha256",
    }:
        raise ConvergenceObservationContractError(
            "role release identity fields differ"
        )
    if (
        release_identity.get("head") != bound["release_sha"]
        or release_identity.get("tree") != bound["release_tree_sha"]
        or release_identity.get("source_tree_bound") is not True
        or release_identity.get("worker_sha256") != bound["worker_sha256"]
    ):
        raise ConvergenceObservationContractError("role release identity differs")
    _nonzero_sha256(release_identity.get("release_root_sha256"), label="release root")
    available = document.get("available_observations")
    if not isinstance(available, list) or any(
        not isinstance(item, str) for item in available
    ):
        raise ConvergenceObservationContractError(
            "role availability list is invalid"
        )
    expected_available = (
        ["database_parity", "dr_convergence"]
        if bound["role"] in RUNTIME_SNAPSHOT_ROLES
        else []
    )
    if available != expected_available:
        raise ConvergenceObservationContractError(
            "role availability differs from implemented collectors"
        )
    unavailable = document.get("unavailable_observations")
    if not isinstance(unavailable, Mapping) or set(unavailable) != set(
        UNAVAILABLE_REASONS
    ):
        raise ConvergenceObservationContractError(
            "role unavailable observation set differs"
        )
    if any(
        unavailable.get(label) != reason
        for label, reason in UNAVAILABLE_REASONS.items()
        if label not in available
    ):
        raise ConvergenceObservationContractError(
            "role unavailable observation reason differs"
        )
    snapshot = document.get("runtime_snapshot")
    if bound["role"] in RUNTIME_SNAPSHOT_ROLES:
        validate_compose_execution_proof(document.get("compose_execution"))
        if not isinstance(snapshot, Mapping) or set(snapshot) != {
            "captured_at",
            "database",
            "redacted_parity_snapshot",
            "dr",
        }:
            raise ConvergenceObservationContractError(
                "runtime observation summary fields differ"
            )
        captured_at = _timestamp(
            snapshot["captured_at"],
            label="runtime capture time",
        )
        if captured_at < _timestamp(
            bound["phase_started_at"],
            label="phase_started_at",
        ):
            raise ConvergenceObservationContractError(
                "runtime observation predates phase start"
            )
        if captured_at > current + MAX_OBSERVATION_FUTURE_SKEW:
            raise ConvergenceObservationContractError(
                "runtime observation is future dated"
            )
        if current - captured_at > MAX_OBSERVATION_AGE:
            raise ConvergenceObservationContractError(
                "runtime observation is stale"
            )
        if (
            observed_at < captured_at
            or observed_at - captured_at > MAX_CAPTURE_TO_ATTESTATION_SKEW
        ):
            raise ConvergenceObservationContractError(
                "runtime capture-to-attestation skew is invalid"
            )
        database = snapshot["database"]
        if not isinstance(database, Mapping) or set(database) != {
            "table_set_sha256",
            "business_fingerprint_sha256",
            "row_count",
            "table_count",
            "redacted_snapshot_sha256",
            "database_state_sha256",
        }:
            raise ConvergenceObservationContractError(
                "database runtime summary fields differ"
            )
        for field in (
            "table_set_sha256",
            "business_fingerprint_sha256",
            "redacted_snapshot_sha256",
            "database_state_sha256",
        ):
            _nonzero_sha256(database.get(field), label=f"database {field}")
        if (
            type(database.get("row_count")) is not int
            or database["row_count"] < 0
            or type(database.get("table_count")) is not int
            or database["table_count"] < 1
        ):
            raise ConvergenceObservationContractError(
                "database runtime summary values differ"
            )
        expected_database = {
            key: item
            for key, item in database.items()
            if key != "database_state_sha256"
        }
        if database["database_state_sha256"] != _sha256(expected_database):
            raise ConvergenceObservationContractError(
                "database runtime summary digest differs"
            )
        redacted_parity_snapshot = snapshot["redacted_parity_snapshot"]
        if (
            not isinstance(redacted_parity_snapshot, Mapping)
            or database["redacted_snapshot_sha256"]
            != _sha256(redacted_parity_snapshot)
        ):
            raise ConvergenceObservationContractError(
                "redacted parity snapshot binding differs"
            )
        dr = snapshot["dr"]
        if not isinstance(dr, Mapping) or set(dr) != {
            "producer_epoch",
            "source_streams",
            "destination_streams",
            "unresolved_conflict_count",
            "dr_state_sha256",
        }:
            raise ConvergenceObservationContractError(
                "DR runtime summary fields differ"
            )
        if type(dr.get("producer_epoch")) is not int or dr["producer_epoch"] < 1:
            raise ConvergenceObservationContractError("DR producer epoch is invalid")
        if not isinstance(dr.get("source_streams"), list) or not isinstance(
            dr.get("destination_streams"),
            list,
        ):
            raise ConvergenceObservationContractError("DR runtime streams are invalid")
        if (
            type(dr.get("unresolved_conflict_count")) is not int
            or dr["unresolved_conflict_count"] < 0
        ):
            raise ConvergenceObservationContractError("DR conflict count is invalid")
        expected_dr = {
            key: item for key, item in dr.items() if key != "dr_state_sha256"
        }
        if dr.get("dr_state_sha256") != _sha256(expected_dr):
            raise ConvergenceObservationContractError(
                "DR runtime summary digest differs"
            )
        _nonzero_sha256(dr.get("dr_state_sha256"), label="DR state")
    elif snapshot is not None or document.get("compose_execution") is not None:
        raise ConvergenceObservationContractError(
            "Witness observer must not claim a runtime database snapshot"
        )
    if document.get("attestation_sha256") != _attestation_digest(document):
        raise ConvergenceObservationContractError(
            "role attestation digest differs"
        )
    return document
