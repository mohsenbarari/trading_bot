"""Canonical digest contract for the V1 PostgreSQL admission schema.

This is a pure serialization helper shared by a future local SQLAlchemy
transaction adapter and the ``operational_writer_admission_*`` tables.  It
does not create a database session, execute SQL, check host identity, contact
a Witness, start a writer, or communicate with another site.

The database's ``control_role_label`` is deliberately serialized only as
policy metadata.  A caller must not treat it as proof of Unix root, a service
identity, a current Witness term, or permission to write.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Mapping
from uuid import UUID

from core.append_only_sync_delta_batch import LEASE_ID_RE
from models.operational_writer_admission import (
    OPERATIONAL_WRITER_ADMISSION_COMMIT_KINDS,
    OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
)


__all__ = (
    "OPERATIONAL_WRITER_ADMISSION_POSTGRES_COMMIT_DIGEST_SCHEMA",
    "OPERATIONAL_WRITER_ADMISSION_POSTGRES_RECEIPT_SCHEMA",
    "OPERATIONAL_WRITER_ADMISSION_POSTGRES_STATE_SCHEMA",
    "OperationalWriterAdmissionPostgresContractError",
    "canonical_operational_writer_admission_postgres_commit_v1",
    "canonical_operational_writer_admission_postgres_receipt_v1",
    "canonical_operational_writer_admission_postgres_state_v1",
    "operational_writer_admission_postgres_commit_sha256_v1",
    "operational_writer_admission_postgres_receipt_sha256_v1",
    "operational_writer_admission_postgres_state_sha256_v1",
)


OPERATIONAL_WRITER_ADMISSION_POSTGRES_STATE_SCHEMA = (
    "gold-trade-operational-writer-admission-postgres-state-v1"
)
OPERATIONAL_WRITER_ADMISSION_POSTGRES_RECEIPT_SCHEMA = (
    "gold-trade-operational-writer-admission-postgres-receipt-v1"
)
OPERATIONAL_WRITER_ADMISSION_POSTGRES_COMMIT_DIGEST_SCHEMA = (
    "gold-trade-operational-writer-admission-postgres-commit-v1"
)

_ZERO_SHA256 = "0" * 64
_BINDING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$", re.ASCII)
_CLUSTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$", re.ASCII)
_RELEASE_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CONTROL_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{2,127}$", re.ASCII)
_SITES = frozenset({"webapp_fi", "webapp_ir"})
_STATE_FIELDS = frozenset(
    {
        "revision",
        "highest_writer_epoch",
        "active_term",
        "revalidated_runtime_instance_id",
        "clock_floor",
        "fence_generation",
        "fenced",
        "fence_reason",
        "requires_fresh_witness_revalidation",
    }
)
_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "evidence_id",
        "revalidation_id",
        "issued_at",
        "expires_at",
    }
)
_OPERATION_FIELDS = frozenset(
    {
        "operation_kind",
        "opened_state_revision",
        "fence_generation",
        "evidence_id",
        "writer_epoch",
        "writer_lease_id",
        "opened_at",
        "admitted_at",
    }
)
_CONTROL_FIELDS = frozenset(
    {"control_boundary", "control_role_label", "control_policy_sha256"}
)


class OperationalWriterAdmissionPostgresContractError(ValueError):
    """A state/receipt/commit digest input is not exact and safe to persist."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise OperationalWriterAdmissionPostgresContractError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OperationalWriterAdmissionPostgresContractError(code) from exc


def _mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _BINDING_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _writer_lease_id(value: object, *, code: str) -> str:
    """Apply the shared lease grammar to persisted state and receipts."""

    if type(value) is not str or LEASE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or _SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _nonnegative(value: object, *, code: str, permit_negative_one: bool = False) -> int:
    minimum = -1 if permit_negative_one else 0
    if type(value) is not int or value < minimum:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(code)
    try:
        if value.utcoffset() is None:
            _fail(code)
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)


def _timestamp(value: object, *, code: str) -> str | None:
    if value is None:
        return None
    utc = _utc(value, code=code)
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _binding(value: object, *, code: str) -> dict[str, str]:
    fields = _mapping(
        value,
        fields=frozenset({"cluster_id", "local_site", "release_sha", "generation_id"}),
        code=code,
    )
    cluster_id = fields["cluster_id"]
    local_site = fields["local_site"]
    release_sha = fields["release_sha"]
    generation_id = fields["generation_id"]
    if type(cluster_id) is not str or _CLUSTER_RE.fullmatch(cluster_id) is None:
        _fail(code)
    if local_site not in _SITES:
        _fail(code)
    if type(release_sha) is not str or _RELEASE_RE.fullmatch(release_sha) is None:
        _fail(code)
    _identifier(generation_id, code=code)
    return {
        "cluster_id": cluster_id,
        "local_site": local_site,
        "release_sha": release_sha,
        "generation_id": generation_id,
    }


def _term(value: object, *, binding: Mapping[str, str], code: str) -> dict[str, object] | None:
    if value is None:
        return None
    fields = _mapping(value, fields=_TERM_FIELDS, code=code)
    holder_site = fields["holder_site"]
    writer_epoch = fields["writer_epoch"]
    if holder_site != binding["local_site"] or type(writer_epoch) is not int or writer_epoch < 1:
        _fail(code)
    writer_lease_id = _writer_lease_id(fields["writer_lease_id"], code=code)
    evidence_id = _identifier(fields["evidence_id"], code=code)
    revalidation_id = _identifier(fields["revalidation_id"], code=code)
    issued_at = _utc(fields["issued_at"], code=code)
    expires_at = _utc(fields["expires_at"], code=code)
    if expires_at <= issued_at:
        _fail(code)
    return {
        "holder_site": holder_site,
        "writer_epoch": writer_epoch,
        "writer_lease_id": writer_lease_id,
        "evidence_id": evidence_id,
        "revalidation_id": revalidation_id,
        "issued_at": _timestamp(issued_at, code=code),
        "expires_at": _timestamp(expires_at, code=code),
    }


def _state(value: object, *, binding: Mapping[str, str], code: str) -> dict[str, object]:
    fields = _mapping(value, fields=_STATE_FIELDS, code=code)
    revision = _nonnegative(fields["revision"], code=code)
    highest_writer_epoch = _nonnegative(fields["highest_writer_epoch"], code=code)
    fence_generation = _nonnegative(fields["fence_generation"], code=code)
    fenced = fields["fenced"]
    requires_fresh = fields["requires_fresh_witness_revalidation"]
    if type(fenced) is not bool or type(requires_fresh) is not bool:
        _fail(code)
    term = _term(fields["active_term"], binding=binding, code=code)
    runtime = fields["revalidated_runtime_instance_id"]
    if runtime is not None:
        runtime = _identifier(runtime, code=code)
    fence_reason = fields["fence_reason"]
    if fenced:
        fence_reason = _identifier(fence_reason, code=code)
    elif fence_reason is not None:
        _fail(code)
    if term is None:
        if (
            highest_writer_epoch != 0
            or runtime is not None
            or fenced is not True
            or requires_fresh is not True
        ):
            _fail(code)
    else:
        if term["writer_epoch"] != highest_writer_epoch:
            _fail(code)
        if fenced is True and requires_fresh is not True:
            _fail(code)
        if requires_fresh is False and (runtime is None or fenced is True):
            _fail(code)
    return {
        "revision": revision,
        "highest_writer_epoch": highest_writer_epoch,
        "active_term": term,
        "revalidated_runtime_instance_id": runtime,
        "clock_floor": _timestamp(fields["clock_floor"], code=code),
        "fence_generation": fence_generation,
        "fenced": fenced,
        "fence_reason": fence_reason,
        "requires_fresh_witness_revalidation": requires_fresh,
    }


def _control(value: object, *, code: str) -> dict[str, str]:
    fields = _mapping(value, fields=_CONTROL_FIELDS, code=code)
    boundary = fields["control_boundary"]
    role = fields["control_role_label"]
    policy_sha = fields["control_policy_sha256"]
    if boundary != OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA:
        _fail(code)
    if type(role) is not str or _CONTROL_ROLE_RE.fullmatch(role) is None:
        _fail(code)
    _sha256(policy_sha, code=code)
    return {
        "control_boundary": boundary,
        "control_role_label": role,
        "control_policy_sha256": policy_sha,
    }


def canonical_operational_writer_admission_postgres_state_v1(
    *,
    binding: Mapping[str, object],
    state: Mapping[str, object],
) -> bytes:
    """Return canonical bytes for the exact persisted V1 state projection."""

    normalized_binding = _binding(binding, code="OWA_POSTGRES_STATE_BINDING_INVALID")
    normalized_state = _state(
        state,
        binding=normalized_binding,
        code="OWA_POSTGRES_STATE_INVALID",
    )
    return _canonical(
        {
            "schema": OPERATIONAL_WRITER_ADMISSION_POSTGRES_STATE_SCHEMA,
            "binding": normalized_binding,
            "state": normalized_state,
        },
        code="OWA_POSTGRES_STATE_INVALID",
    )


def operational_writer_admission_postgres_state_sha256_v1(
    *,
    binding: Mapping[str, object],
    state: Mapping[str, object],
) -> str:
    return hashlib.sha256(
        canonical_operational_writer_admission_postgres_state_v1(
            binding=binding,
            state=state,
        )
    ).hexdigest()


def _operation(value: object, *, transition_kind: str, code: str) -> dict[str, object] | None:
    if transition_kind != "writer_admission":
        if value is not None:
            _fail(code)
        return None
    fields = _mapping(value, fields=_OPERATION_FIELDS, code=code)
    operation_kind = fields["operation_kind"]
    if operation_kind not in {"transaction_commit", "external_effect"}:
        _fail(code)
    writer_epoch = fields["writer_epoch"]
    if type(writer_epoch) is not int or writer_epoch < 1:
        _fail(code)
    opened_at = _timestamp(fields["opened_at"], code=code)
    admitted_at = _timestamp(fields["admitted_at"], code=code)
    if opened_at is None or admitted_at is None:
        _fail(code)
    if _utc(fields["admitted_at"], code=code) < _utc(fields["opened_at"], code=code):
        _fail(code)
    return {
        "operation_kind": operation_kind,
        "opened_state_revision": _nonnegative(fields["opened_state_revision"], code=code),
        "fence_generation": _nonnegative(fields["fence_generation"], code=code),
        "evidence_id": _identifier(fields["evidence_id"], code=code),
        "writer_epoch": writer_epoch,
        "writer_lease_id": _writer_lease_id(fields["writer_lease_id"], code=code),
        "opened_at": opened_at,
        "admitted_at": admitted_at,
    }


def canonical_operational_writer_admission_postgres_receipt_v1(
    *,
    binding: Mapping[str, object],
    transition_kind: str,
    prior_revision: int,
    prior_fence_generation: int,
    prior_state_sha256: str,
    previous_commit_sha256: str,
    next_state_sha256: str,
    next_fence_generation: int,
    operation: Mapping[str, object] | None,
    control: Mapping[str, object],
    committed_at: datetime,
) -> bytes:
    """Return canonical immutable receipt bytes for one head transition."""

    code = "OWA_POSTGRES_RECEIPT_INVALID"
    normalized_binding = _binding(binding, code=code)
    if transition_kind not in OPERATIONAL_WRITER_ADMISSION_COMMIT_KINDS:
        _fail(code)
    prior_revision = _nonnegative(
        prior_revision,
        code=code,
        permit_negative_one=True,
    )
    if transition_kind == "bootstrap":
        if prior_revision != -1:
            _fail(code)
    elif prior_revision < 0:
        _fail(code)
    prior_fence_generation = _nonnegative(prior_fence_generation, code=code)
    next_fence_generation = _nonnegative(next_fence_generation, code=code)
    prior_state = _sha256(prior_state_sha256, code=code, permit_zero=True)
    previous_commit = _sha256(previous_commit_sha256, code=code, permit_zero=True)
    if transition_kind == "bootstrap" and (
        prior_state != _ZERO_SHA256 or previous_commit != _ZERO_SHA256
    ):
        _fail(code)
    next_state = _sha256(next_state_sha256, code=code)
    normalized_operation = _operation(operation, transition_kind=transition_kind, code=code)
    normalized_control = _control(control, code=code)
    rendered_committed_at = _timestamp(committed_at, code=code)
    if rendered_committed_at is None:
        _fail(code)
    return _canonical(
        {
            "schema": OPERATIONAL_WRITER_ADMISSION_POSTGRES_RECEIPT_SCHEMA,
            "binding": normalized_binding,
            "transition_kind": transition_kind,
            "prior_revision": prior_revision,
            "next_revision": prior_revision + 1,
            "prior_fence_generation": prior_fence_generation,
            "next_fence_generation": next_fence_generation,
            "prior_state_sha256": prior_state,
            "previous_commit_sha256": previous_commit,
            "next_state_sha256": next_state,
            "operation": normalized_operation,
            "control": normalized_control,
            "committed_at": rendered_committed_at,
        },
        code=code,
    )


def operational_writer_admission_postgres_receipt_sha256_v1(
    **kwargs: object,
) -> str:
    return hashlib.sha256(
        canonical_operational_writer_admission_postgres_receipt_v1(**kwargs)  # type: ignore[arg-type]
    ).hexdigest()


def _uuid(value: object, *, code: str) -> str:
    if isinstance(value, UUID):
        return str(value)
    if type(value) is not str:
        _fail(code)
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        _fail(code)


def canonical_operational_writer_admission_postgres_commit_v1(
    *,
    commit_id: UUID | str,
    head_id: UUID | str,
    receipt_sha256: str,
    previous_commit_sha256: str,
    state_sha256: str,
    committed_at: datetime,
) -> bytes:
    """Return canonical immutable chain digest bytes for one SQL receipt row."""

    code = "OWA_POSTGRES_COMMIT_INVALID"
    rendered_committed_at = _timestamp(committed_at, code=code)
    if rendered_committed_at is None:
        _fail(code)
    return _canonical(
        {
            "schema": OPERATIONAL_WRITER_ADMISSION_POSTGRES_COMMIT_DIGEST_SCHEMA,
            "commit_id": _uuid(commit_id, code=code),
            "head_id": _uuid(head_id, code=code),
            "receipt_sha256": _sha256(receipt_sha256, code=code),
            "previous_commit_sha256": _sha256(
                previous_commit_sha256,
                code=code,
                permit_zero=True,
            ),
            "state_sha256": _sha256(state_sha256, code=code),
            "committed_at": rendered_committed_at,
        },
        code=code,
    )


def operational_writer_admission_postgres_commit_sha256_v1(
    **kwargs: object,
) -> str:
    return hashlib.sha256(
        canonical_operational_writer_admission_postgres_commit_v1(**kwargs)  # type: ignore[arg-type]
    ).hexdigest()
