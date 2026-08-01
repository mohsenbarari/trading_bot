"""Pure canonical mappings for a deliberately small Object-delta MVP.

This module is a value-object boundary only.  It accepts a separately
constructed canonical mapping and returns an immutable descriptor for a
future serializer or receiver.  It does not read, validate, or endorse raw
``ChangeLog`` records, and it does not open a database, contact Object
Storage, use the network, read files, or enable a runtime.

The allowed fields are intentionally narrower than the physical ORM rows:
site-local primary keys and unresolved foreign keys are not portable object
identities.  In particular, a commodity alias carries its parent through the
canonical ``commodity_name`` reference instead of ``commodity_id``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from types import MappingProxyType
from typing import Any, Final


OBJECT_DELTA_MVP_CANONICAL_SCHEMA: Final[str] = "object_delta_mvp_canonical_v1"
OBJECT_DELTA_MVP_CANONICAL_DEFAULT_ENABLED: Final[bool] = False

# These capability flags are deliberately explicit so callers cannot mistake
# a pure shape validator for a source-evidence validator or a live receiver.
OBJECT_DELTA_MVP_CANONICAL_VALIDATES_RAW_CHANGELOG_PAYLOADS: Final[bool] = False
OBJECT_DELTA_MVP_CANONICAL_ENABLES_RECEIVER: Final[bool] = False

INSERT: Final[str] = "INSERT"
UPDATE: Final[str] = "UPDATE"
DELETE: Final[str] = "DELETE"
SUPPORTED_OPERATIONS: Final[tuple[str, ...]] = (INSERT, UPDATE)

MARKET_RUNTIME_SINGLETON_FIELD: Final[str] = "market_runtime_singleton"
MARKET_RUNTIME_SINGLETON_VALUE: Final[str] = "market_runtime_state"

MARKET_SCHEDULE_OVERRIDE_TYPES: Final[frozenset[str]] = frozenset(
    {"closed_all_day", "open_all_day", "custom_hours"}
)

_ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset(
    {"table", "operation", "identity", "fields", "references"}
)
_LOCAL_RAW_ID_FIELDS: Final[frozenset[str]] = frozenset(
    {"id", "commodity_id", "created_by_user_id"}
)


class ObjectDeltaMvpCanonicalError(ValueError):
    """Raised when a canonical MVP object-delta mapping is unsafe or malformed."""


@dataclass(frozen=True)
class ObjectDeltaMvpCanonicalTableDescriptor:
    """Non-secret, immutable contract for one canonical MVP table.

    The declaration does not represent a selected runtime table.  It gives a
    future serializer or receiver the exact portable names that this module
    can validate without treating local raw ids as cross-site identities.
    """

    table: str
    identity_kind: str
    identity_fields: tuple[str, ...]
    field_names: tuple[str, ...]
    reference_names: tuple[str, ...]
    allowed_operations: tuple[str, ...] = SUPPORTED_OPERATIONS
    default_enabled: bool = False
    validates_raw_changelog_payloads: bool = False
    enables_receiver: bool = False

    def __post_init__(self) -> None:
        if type(self.table) is not str or not self.table:
            raise ObjectDeltaMvpCanonicalError("canonical table name is invalid")
        if type(self.identity_kind) is not str or not self.identity_kind:
            raise ObjectDeltaMvpCanonicalError("canonical identity kind is invalid")
        for label, values in (
            ("canonical identity fields", self.identity_fields),
            ("canonical field names", self.field_names),
            ("canonical reference names", self.reference_names),
        ):
            if (
                type(values) is not tuple
                or len(set(values)) != len(values)
                or any(type(value) is not str or not value for value in values)
            ):
                raise ObjectDeltaMvpCanonicalError(f"{label} are invalid")
        if (
            type(self.allowed_operations) is not tuple
            or not self.allowed_operations
            or tuple(self.allowed_operations) != SUPPORTED_OPERATIONS
        ):
            raise ObjectDeltaMvpCanonicalError("canonical operations are invalid")
        if any(
            type(value) is not bool
            for value in (
                self.default_enabled,
                self.validates_raw_changelog_payloads,
                self.enables_receiver,
            )
        ):
            raise ObjectDeltaMvpCanonicalError("canonical capability flag is invalid")
        if (
            self.default_enabled
            or self.validates_raw_changelog_payloads
            or self.enables_receiver
        ):
            raise ObjectDeltaMvpCanonicalError(
                "canonical descriptor cannot enable a runtime or claim raw ChangeLog safety"
            )

    @property
    def all_canonical_names(self) -> frozenset[str]:
        """Return every portable field name declared by this contract."""

        return frozenset(
            self.identity_fields + self.field_names + self.reference_names
        )


@dataclass(frozen=True)
class CanonicalMvpObjectDeltaDescriptor:
    """Immutable, non-secret shape for one future canonical object delta.

    ``identity``, ``fields``, and ``references`` contain only JSON scalar
    values accepted by this module.  They are copied into read-only mappings,
    so mutating the caller's input cannot change the validated descriptor.
    """

    schema: str
    table: str
    operation: str
    identity: Mapping[str, object]
    fields: Mapping[str, object]
    references: Mapping[str, object]
    validates_raw_changelog_payloads: bool = False
    enables_receiver: bool = False

    def __post_init__(self) -> None:
        if self.schema != OBJECT_DELTA_MVP_CANONICAL_SCHEMA:
            raise ObjectDeltaMvpCanonicalError("canonical descriptor schema is invalid")
        if type(self.table) is not str or not self.table:
            raise ObjectDeltaMvpCanonicalError("canonical descriptor table is invalid")
        if self.operation not in SUPPORTED_OPERATIONS:
            raise ObjectDeltaMvpCanonicalError("canonical descriptor operation is invalid")
        if (
            type(self.validates_raw_changelog_payloads) is not bool
            or type(self.enables_receiver) is not bool
            or self.validates_raw_changelog_payloads
            or self.enables_receiver
        ):
            raise ObjectDeltaMvpCanonicalError(
                "canonical descriptor cannot claim raw ChangeLog safety or receiver authority"
            )
        object.__setattr__(self, "identity", _freeze_mapping(self.identity))
        object.__setattr__(self, "fields", _freeze_mapping(self.fields))
        object.__setattr__(self, "references", _freeze_mapping(self.references))

    def as_mapping(self) -> dict[str, object]:
        """Return an isolated serializer-ready mapping without any source evidence."""

        return {
            "table": self.table,
            "operation": self.operation,
            "identity": dict(self.identity),
            "fields": dict(self.fields),
            "references": dict(self.references),
        }


# These names are based on the current ORM models and the existing
# commodity-alias wire-only reference.  Audit timestamps and local foreign
# keys are deliberately excluded from this portable, low-complexity subset.
OBJECT_DELTA_MVP_CANONICAL_TABLES: Final[
    Mapping[str, ObjectDeltaMvpCanonicalTableDescriptor]
] = MappingProxyType(
    {
        "trading_settings": ObjectDeltaMvpCanonicalTableDescriptor(
            table="trading_settings",
            identity_kind="natural_key",
            identity_fields=("key",),
            field_names=("value",),
            reference_names=(),
        ),
        "market_schedule_overrides": ObjectDeltaMvpCanonicalTableDescriptor(
            table="market_schedule_overrides",
            identity_kind="natural_key",
            identity_fields=("date",),
            field_names=(
                "override_type",
                "open_time_local",
                "close_time_local",
                "note",
            ),
            reference_names=(),
        ),
        "market_runtime_state": ObjectDeltaMvpCanonicalTableDescriptor(
            table="market_runtime_state",
            identity_kind="fixed_singleton",
            identity_fields=(MARKET_RUNTIME_SINGLETON_FIELD,),
            field_names=(
                "is_open",
                "active_web_notice_visible",
                "offers_since_last_open",
                "last_transition_at",
            ),
            reference_names=(),
        ),
        "commodities": ObjectDeltaMvpCanonicalTableDescriptor(
            table="commodities",
            identity_kind="natural_key",
            identity_fields=("name",),
            field_names=(),
            reference_names=(),
        ),
        "commodity_aliases": ObjectDeltaMvpCanonicalTableDescriptor(
            table="commodity_aliases",
            identity_kind="natural_key",
            identity_fields=("alias",),
            field_names=(),
            reference_names=("commodity_name",),
        ),
    }
)


def canonical_mvp_table_descriptor(
    table: object,
) -> ObjectDeltaMvpCanonicalTableDescriptor:
    """Return one immutable contract without enabling any runtime behavior."""

    if type(table) is not str or table != table.strip() or not table:
        raise ObjectDeltaMvpCanonicalError("canonical table name is invalid")
    try:
        return OBJECT_DELTA_MVP_CANONICAL_TABLES[table]
    except KeyError as exc:
        raise ObjectDeltaMvpCanonicalError("canonical table is not in the MVP subset") from exc


def validate_canonical_mvp_object_delta(
    value: object,
) -> CanonicalMvpObjectDeltaDescriptor:
    """Validate one independent canonical mapping without I/O or activation.

    The accepted envelope is exactly ``table``, ``operation``, ``identity``,
    ``fields``, and ``references``.  It is intentionally not the legacy
    ChangeLog or generic Object-delta envelope: source record ids, hashes,
    timestamps, and authority evidence belong to separately reviewed layers.
    """

    envelope = _require_exact_mapping(value, label="canonical object delta", fields=_ENVELOPE_FIELDS)
    table = envelope["table"]
    descriptor = canonical_mvp_table_descriptor(table)
    operation = _require_operation(envelope["operation"])
    identity = _require_exact_mapping(
        envelope["identity"],
        label="canonical object delta identity",
        fields=frozenset(descriptor.identity_fields),
    )
    fields = _require_exact_mapping(
        envelope["fields"],
        label="canonical object delta fields",
        fields=frozenset(descriptor.field_names),
    )
    references = _require_exact_mapping(
        envelope["references"],
        label="canonical object delta references",
        fields=frozenset(descriptor.reference_names),
    )

    _reject_local_raw_ids(identity, label="canonical object delta identity")
    _reject_local_raw_ids(fields, label="canonical object delta fields")
    _reject_local_raw_ids(references, label="canonical object delta references")

    normalized_identity = _validate_identity(descriptor.table, identity)
    normalized_fields = _validate_fields(descriptor.table, fields)
    normalized_references = _validate_references(descriptor.table, references)

    return CanonicalMvpObjectDeltaDescriptor(
        schema=OBJECT_DELTA_MVP_CANONICAL_SCHEMA,
        table=descriptor.table,
        operation=operation,
        identity=normalized_identity,
        fields=normalized_fields,
        references=normalized_references,
    )


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ObjectDeltaMvpCanonicalError("canonical descriptor mapping is invalid")
    return MappingProxyType(dict(value))


def _require_exact_mapping(
    value: object,
    *,
    label: str,
    fields: frozenset[str],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ObjectDeltaMvpCanonicalError(f"{label} must be a mapping")
    actual = set(value)
    if actual == fields:
        return dict(value)
    raw_id_fields = actual & _LOCAL_RAW_ID_FIELDS
    if raw_id_fields:
        raise ObjectDeltaMvpCanonicalError(
            f"{label} contains a local raw id field: {sorted(raw_id_fields)[0]}"
        )
    unknown = actual - fields
    if unknown:
        raise ObjectDeltaMvpCanonicalError(
            f"{label} contains unknown fields: {sorted(unknown, key=str)[0]}"
        )
    missing = fields - actual
    raise ObjectDeltaMvpCanonicalError(
        f"{label} is missing required fields: {sorted(missing)[0]}"
    )


def _require_operation(value: object) -> str:
    if value == DELETE:
        raise ObjectDeltaMvpCanonicalError("canonical object delta delete is unsupported")
    if type(value) is not str or value not in SUPPORTED_OPERATIONS:
        raise ObjectDeltaMvpCanonicalError("canonical object delta operation is unsupported")
    return value


def _reject_local_raw_ids(value: Mapping[str, object], *, label: str) -> None:
    raw_id_fields = set(value) & _LOCAL_RAW_ID_FIELDS
    if raw_id_fields:
        raise ObjectDeltaMvpCanonicalError(
            f"{label} contains a local raw id field: {sorted(raw_id_fields)[0]}"
        )


def _require_canonical_text(value: object, *, label: str, max_length: int | None = None) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ObjectDeltaMvpCanonicalError(f"{label} is not canonical text")
    if max_length is not None and len(value) > max_length:
        raise ObjectDeltaMvpCanonicalError(f"{label} is too long")
    return value


def _require_canonical_date(value: object, *, label: str) -> str:
    text = _require_canonical_text(value, label=label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ObjectDeltaMvpCanonicalError(f"{label} is not an ISO date") from exc
    if parsed.isoformat() != text:
        raise ObjectDeltaMvpCanonicalError(f"{label} is not an ISO date")
    return text


def _require_canonical_local_time(value: object, *, label: str) -> str:
    text = _require_canonical_text(value, label=label)
    try:
        parsed = time.fromisoformat(text)
    except ValueError as exc:
        raise ObjectDeltaMvpCanonicalError(f"{label} is not a canonical local time") from exc
    if (
        parsed.tzinfo is not None
        or parsed.microsecond != 0
        or parsed.isoformat(timespec="seconds") != text
    ):
        raise ObjectDeltaMvpCanonicalError(f"{label} is not a canonical local time")
    return text


def _require_canonical_utc_timestamp(value: object, *, label: str) -> str:
    text = _require_canonical_text(value, label=label)
    if text.endswith("Z"):
        parseable = text[:-1] + "+00:00"
    elif text.endswith("+00:00"):
        parseable = text
    else:
        raise ObjectDeltaMvpCanonicalError(f"{label} is not a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(parseable)
    except ValueError as exc:
        raise ObjectDeltaMvpCanonicalError(
            f"{label} is not a canonical UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ObjectDeltaMvpCanonicalError(f"{label} is not a canonical UTC timestamp")
    normalized = parsed.isoformat().replace("+00:00", "Z")
    if text not in {normalized, normalized.replace("Z", "+00:00")}:
        raise ObjectDeltaMvpCanonicalError(f"{label} is not a canonical UTC timestamp")
    return normalized


def _validate_identity(table: str, identity: Mapping[str, object]) -> dict[str, object]:
    if table == "trading_settings":
        return {"key": _require_canonical_text(identity["key"], label="trading setting key", max_length=100)}
    if table == "market_schedule_overrides":
        return {"date": _require_canonical_date(identity["date"], label="market schedule date")}
    if table == "market_runtime_state":
        singleton = identity[MARKET_RUNTIME_SINGLETON_FIELD]
        if (
            type(singleton) is not str
            or singleton != MARKET_RUNTIME_SINGLETON_VALUE
        ):
            raise ObjectDeltaMvpCanonicalError("market runtime singleton identity is invalid")
        return {MARKET_RUNTIME_SINGLETON_FIELD: MARKET_RUNTIME_SINGLETON_VALUE}
    if table == "commodities":
        return {"name": _require_canonical_text(identity["name"], label="commodity name")}
    if table == "commodity_aliases":
        return {"alias": _require_canonical_text(identity["alias"], label="commodity alias")}
    raise ObjectDeltaMvpCanonicalError("canonical table is not in the MVP subset")


def _validate_fields(table: str, fields: Mapping[str, object]) -> dict[str, object]:
    if table == "trading_settings":
        value = fields["value"]
        if type(value) is not str:
            raise ObjectDeltaMvpCanonicalError("trading setting value must be text")
        return {"value": value}
    if table == "market_schedule_overrides":
        return _validate_market_schedule_fields(fields)
    if table == "market_runtime_state":
        is_open = fields["is_open"]
        notice_visible = fields["active_web_notice_visible"]
        offers_since_open = fields["offers_since_last_open"]
        transitioned_at = fields["last_transition_at"]
        if type(is_open) is not bool or type(notice_visible) is not bool:
            raise ObjectDeltaMvpCanonicalError("market runtime boolean field is invalid")
        if type(offers_since_open) is not int or offers_since_open < 0:
            raise ObjectDeltaMvpCanonicalError("market runtime offer count is invalid")
        if transitioned_at is not None:
            transitioned_at = _require_canonical_utc_timestamp(
                transitioned_at,
                label="market runtime transition timestamp",
            )
        return {
            "is_open": is_open,
            "active_web_notice_visible": notice_visible,
            "offers_since_last_open": offers_since_open,
            "last_transition_at": transitioned_at,
        }
    if table in {"commodities", "commodity_aliases"}:
        return {}
    raise ObjectDeltaMvpCanonicalError("canonical table is not in the MVP subset")


def _validate_market_schedule_fields(fields: Mapping[str, object]) -> dict[str, object]:
    override_type = fields["override_type"]
    if type(override_type) is not str or override_type not in MARKET_SCHEDULE_OVERRIDE_TYPES:
        raise ObjectDeltaMvpCanonicalError("market schedule override type is invalid")
    open_time = fields["open_time_local"]
    close_time = fields["close_time_local"]
    note = fields["note"]
    if note is not None:
        note = _require_canonical_text(note, label="market schedule note", max_length=255)
    if override_type == "custom_hours":
        open_time = _require_canonical_local_time(
            open_time,
            label="market schedule open time",
        )
        close_time = _require_canonical_local_time(
            close_time,
            label="market schedule close time",
        )
        if open_time >= close_time:
            raise ObjectDeltaMvpCanonicalError(
                "market schedule custom hours must be ordered"
            )
    elif open_time is not None or close_time is not None:
        raise ObjectDeltaMvpCanonicalError(
            "all-day market schedule override cannot include local hours"
        )
    return {
        "override_type": override_type,
        "open_time_local": open_time,
        "close_time_local": close_time,
        "note": note,
    }


def _validate_references(table: str, references: Mapping[str, object]) -> dict[str, object]:
    if table != "commodity_aliases":
        return {}
    return {
        "commodity_name": _require_canonical_text(
            references["commodity_name"],
            label="commodity alias commodity_name reference",
        )
    }
