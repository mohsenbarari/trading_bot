"""Pure, default-off receiver intents for the smallest Object-delta MVP slice.

This is deliberately not a database adapter and it accepts no raw ChangeLog
or legacy sync item.  It compiles only a separately validated portable
descriptor into an immutable instruction for a future dedicated transaction
adapter.  Keeping the first handler to the natural-key-only ``commodities``
table establishes the explicit per-table pattern without granting a generic
upsert path, local-id translation, or receiver runtime authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Final

from core.object_delta_mvp_canonical import (
    INSERT,
    UPDATE,
    CanonicalMvpObjectDeltaDescriptor,
    validate_canonical_mvp_object_delta,
)


OBJECT_DELTA_RECEIVER_MVP_HANDLERS_DEFAULT_ENABLED = False
OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY_SCHEMA: Final[str] = (
    "gold-trade-object-delta-receiver-execution-registry-v1"
)
COMMODITIES_TABLE = "commodities"
COMMODITIES_NATURAL_KEY = "name"
COMMODITY_ENSURE_CONFLICT_POLICY = "insert_on_conflict_do_nothing"
COMMODITY_EXECUTION_HANDLER_CONTRACT: Final[str] = "commodity_natural_key_ensure_v1"
COMMODITY_EXECUTION_HANDLER_IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    COMMODITIES_NATURAL_KEY,
)
COMMODITY_EXECUTION_HANDLER_FIELD_NAMES: Final[tuple[str, ...]] = ()
COMMODITY_EXECUTION_HANDLER_REFERENCE_NAMES: Final[tuple[str, ...]] = ()

class ObjectDeltaReceiverMvpHandlerError(ValueError):
    """A canonical descriptor has no explicit, safe receiver handler."""


@dataclass(frozen=True)
class ObjectDeltaReceiverMvpHandlerSpec:
    """Exact, release-compiled semantics for one executable handler slot."""

    table: str
    operation: str
    handler_contract: str
    identity_fields: tuple[str, ...]
    field_names: tuple[str, ...]
    reference_names: tuple[str, ...]
    conflict_policy: str

    def __post_init__(self) -> None:
        if type(self.table) is not str or not self.table:
            raise ObjectDeltaReceiverMvpHandlerError("receiver handler table is invalid")
        if type(self.operation) is not str or not self.operation:
            raise ObjectDeltaReceiverMvpHandlerError("receiver handler operation is invalid")
        if type(self.handler_contract) is not str or not self.handler_contract:
            raise ObjectDeltaReceiverMvpHandlerError("receiver handler contract is invalid")
        for label, fields in (
            ("identity", self.identity_fields),
            ("field", self.field_names),
            ("reference", self.reference_names),
        ):
            if (
                type(fields) is not tuple
                or len(set(fields)) != len(fields)
                or any(type(field) is not str or not field for field in fields)
            ):
                raise ObjectDeltaReceiverMvpHandlerError(
                    f"receiver handler {label} fields are invalid"
                )
        if type(self.conflict_policy) is not str or not self.conflict_policy:
            raise ObjectDeltaReceiverMvpHandlerError("receiver handler conflict policy is invalid")


# This is deliberately narrower than both the source Sync registry and the
# declarative MVP scope.  A table in either of those declarations is *not*
# executable on a receiver until it appears here with a concrete handler
# contract.  Keep this object immutable and compiled into the release: the
# payload-admission boundary already pins the source Sync-registry fingerprint
# to a root-only, release-bound receiver binding.
OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY: Final[MappingProxyType] = MappingProxyType(
    {
        (COMMODITIES_TABLE, INSERT): ObjectDeltaReceiverMvpHandlerSpec(
            table=COMMODITIES_TABLE,
            operation=INSERT,
            handler_contract=COMMODITY_EXECUTION_HANDLER_CONTRACT,
            identity_fields=COMMODITY_EXECUTION_HANDLER_IDENTITY_FIELDS,
            field_names=COMMODITY_EXECUTION_HANDLER_FIELD_NAMES,
            reference_names=COMMODITY_EXECUTION_HANDLER_REFERENCE_NAMES,
            conflict_policy=COMMODITY_ENSURE_CONFLICT_POLICY,
        ),
    }
)
SUPPORTED_OBJECT_DELTA_MVP_RECEIVER_TABLES = frozenset(
    table for table, _operation in OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY
)
_PLANNED_CHANGE_CAPABILITY = object()


def object_delta_receiver_mvp_execution_registry_fingerprint() -> str:
    """Return the stable hash of the executable receiver surface.

    This is intentionally distinct from the broader Sync-registry fingerprint:
    changing an input table declaration must not silently expand the receiver
    mutation surface.  A later runtime configuration must pin this value with
    its release binding before it enables a receiver; today this module remains
    pure and default-off.
    """

    payload = {
        "schema": OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY_SCHEMA,
        "handlers": [
            {
                "table": spec.table,
                "operation": spec.operation,
                "contract": spec.handler_contract,
                "identity_fields": spec.identity_fields,
                "field_names": spec.field_names,
                "reference_names": spec.reference_names,
                "conflict_policy": spec.conflict_policy,
            }
            for _key, spec in sorted(
                OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY.items()
            )
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


@dataclass(frozen=True)
class CommodityEnsureIntent:
    """One id-free natural-key ensure instruction for a future SQL adapter.

    Only canonical INSERT is accepted.  Treating an UPDATE as an ensure would
    create a record when the receiver had missed its original INSERT, which is
    unsafe without a baseline-proven, ordered, gap-free stream.  UPDATE stays
    unavailable until that evidence and an explicit recency policy exist. The
    future adapter must consume this typed value in the already-authorized
    receiver transaction; this module itself performs no SQL or I/O.
    """

    table: str
    operation: str
    name: str
    conflict_policy: str = COMMODITY_ENSURE_CONFLICT_POLICY
    default_enabled: bool = False
    enables_receiver: bool = False

    def __post_init__(self) -> None:
        if self.table != COMMODITIES_TABLE:
            raise ObjectDeltaReceiverMvpHandlerError("commodity intent table is invalid")
        if self.operation != INSERT:
            raise ObjectDeltaReceiverMvpHandlerError("commodity intent operation is invalid")
        if type(self.name) is not str or not self.name or self.name != self.name.strip():
            raise ObjectDeltaReceiverMvpHandlerError("commodity intent name is invalid")
        if self.conflict_policy != COMMODITY_ENSURE_CONFLICT_POLICY:
            raise ObjectDeltaReceiverMvpHandlerError("commodity intent conflict policy is invalid")
        if type(self.default_enabled) is not bool or type(self.enables_receiver) is not bool:
            raise ObjectDeltaReceiverMvpHandlerError("commodity intent capability flag is invalid")
        if self.default_enabled or self.enables_receiver:
            raise ObjectDeltaReceiverMvpHandlerError(
                "commodity intent cannot enable a receiver runtime"
            )


@dataclass(frozen=True)
class ObjectDeltaReceiverMvpPlannedChange:
    """Opaque, executable receiver change for one release-pinned handler.

    The only mutable-looking source evidence retained here is the logical
    sequence and ChangeLog provenance.  The database-facing content is an
    exact typed handler intent, never a legacy ``sync_item`` mapping.  Private
    capability minting prevents a caller from treating an arbitrary generic
    Sync item as an Object-delta receiver change.
    """

    logical_sequence: int
    change_log_id: int
    execution_registry_fingerprint: str
    intent: CommodityEnsureIntent
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def _require_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaReceiverMvpHandlerError(f"{label} is invalid")
    return value


def _canonical_descriptor_for_intent(intent: CommodityEnsureIntent) -> CanonicalMvpObjectDeltaDescriptor:
    return validate_canonical_mvp_object_delta(
        {
            "table": intent.table,
            "operation": intent.operation,
            "identity": {COMMODITIES_NATURAL_KEY: intent.name},
            "fields": {},
            "references": {},
        }
    )


def compile_object_delta_mvp_receiver_intent(
    descriptor: object,
) -> CommodityEnsureIntent:
    """Compile only the canonical commodities slice into an immutable intent.

    Revalidation prevents a caller from constructing a look-alike dataclass
    that bypasses the canonical identity/operation rules.  Every other MVP
    table remains explicitly unavailable until it has its own reviewed
    identity, reference, recency, and side-effect contract.
    """

    if type(descriptor) is not CanonicalMvpObjectDeltaDescriptor:
        raise ObjectDeltaReceiverMvpHandlerError("canonical receiver descriptor is invalid")
    try:
        normalized = validate_canonical_mvp_object_delta(descriptor.as_mapping())
    except ValueError as exc:
        raise ObjectDeltaReceiverMvpHandlerError("canonical receiver descriptor is invalid") from exc
    if normalized != descriptor:
        raise ObjectDeltaReceiverMvpHandlerError("canonical receiver descriptor is not normalized")
    if descriptor.table not in SUPPORTED_OBJECT_DELTA_MVP_RECEIVER_TABLES:
        raise ObjectDeltaReceiverMvpHandlerError(
            "canonical receiver table has no explicit handler"
        )
    spec = OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY.get(
        (descriptor.table, descriptor.operation)
    )
    if spec is None:
        # The executable registry is operation-specific; commodities remains
        # INSERT-only even though the broader canonical schema can describe an
        # UPDATE for a later reviewed handler.
        raise ObjectDeltaReceiverMvpHandlerError("canonical receiver operation has no explicit handler")
    if (
        type(spec) is not ObjectDeltaReceiverMvpHandlerSpec
        or spec
        != ObjectDeltaReceiverMvpHandlerSpec(
            table=COMMODITIES_TABLE,
            operation=INSERT,
            handler_contract=COMMODITY_EXECUTION_HANDLER_CONTRACT,
            identity_fields=COMMODITY_EXECUTION_HANDLER_IDENTITY_FIELDS,
            field_names=COMMODITY_EXECUTION_HANDLER_FIELD_NAMES,
            reference_names=COMMODITY_EXECUTION_HANDLER_REFERENCE_NAMES,
            conflict_policy=COMMODITY_ENSURE_CONFLICT_POLICY,
        )
    ):
        raise ObjectDeltaReceiverMvpHandlerError(
            "canonical receiver handler contract is invalid"
        )
    if descriptor.fields or descriptor.references:
        raise ObjectDeltaReceiverMvpHandlerError("commodity receiver descriptor has unsupported content")
    return CommodityEnsureIntent(
        table=COMMODITIES_TABLE,
        operation=descriptor.operation,
        name=descriptor.identity[COMMODITIES_NATURAL_KEY],
    )


def compile_object_delta_mvp_receiver_planned_change(
    *,
    logical_sequence: object,
    change_log_id: object,
    descriptor: object,
) -> ObjectDeltaReceiverMvpPlannedChange:
    """Mint one opaque change only from the exact reviewed handler intent.

    The caller may supply a canonical descriptor but cannot select a table,
    operation, SQL fields, or conflict policy outside the immutable execution
    registry.  This is a pure planning capability, not a receiver enablement
    mechanism and not validation of a raw ChangeLog record.
    """

    sequence = _require_positive_int(logical_sequence, label="receiver logical sequence")
    provenance_id = _require_positive_int(change_log_id, label="receiver ChangeLog evidence")
    intent = compile_object_delta_mvp_receiver_intent(descriptor)
    change = ObjectDeltaReceiverMvpPlannedChange(
        logical_sequence=sequence,
        change_log_id=provenance_id,
        execution_registry_fingerprint=object_delta_receiver_mvp_execution_registry_fingerprint(),
        intent=intent,
    )
    object.__setattr__(change, "_capability", _PLANNED_CHANGE_CAPABILITY)
    return require_object_delta_mvp_receiver_planned_change(change)


def require_object_delta_mvp_receiver_planned_change(
    value: object,
) -> ObjectDeltaReceiverMvpPlannedChange:
    """Reject direct, replaced, or semantically widened planned changes."""

    if type(value) is not ObjectDeltaReceiverMvpPlannedChange:
        raise ObjectDeltaReceiverMvpHandlerError("receiver planned change is invalid")
    if value._capability is not _PLANNED_CHANGE_CAPABILITY:
        raise ObjectDeltaReceiverMvpHandlerError("receiver planned change was not authorized")
    _require_positive_int(value.logical_sequence, label="receiver logical sequence")
    _require_positive_int(value.change_log_id, label="receiver ChangeLog evidence")
    if value.execution_registry_fingerprint != object_delta_receiver_mvp_execution_registry_fingerprint():
        raise ObjectDeltaReceiverMvpHandlerError(
            "receiver planned change registry fingerprint is invalid"
        )
    if type(value.intent) is not CommodityEnsureIntent:
        raise ObjectDeltaReceiverMvpHandlerError("receiver planned change intent is invalid")
    try:
        canonical = _canonical_descriptor_for_intent(value.intent)
        if compile_object_delta_mvp_receiver_intent(canonical) != value.intent:
            raise ObjectDeltaReceiverMvpHandlerError("receiver planned change intent is invalid")
    except (KeyError, ValueError) as exc:
        raise ObjectDeltaReceiverMvpHandlerError("receiver planned change intent is invalid") from exc
    return value
