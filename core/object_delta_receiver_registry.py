"""Pure, fail-closed declaration for a future Object-delta DB receiver.

This module is deliberately only a registry.  It neither verifies a batch nor
opens a database, resolves references, constructs SQL, contacts Object
Storage, or enables a receiver.  Every declared table is currently unavailable
for mutation: the required baseline cut-point and receiver implementation do
not exist in the current wire contract.

The registry records the future dispatch surface explicitly so a later adapter
cannot silently fall back to legacy router behaviour or assume that source
primary keys are portable across sites.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Final


class ObjectDeltaReceiverRegistryError(ValueError):
    """Raised when a caller asks for an undeclared receiver-table contract."""


class ReceiverIdentityKind(str, Enum):
    """The stable row identity a future receiver must prove before mutation."""

    SOURCE_PRIMARY_KEY = "source_primary_key"
    SINGLETON_SOURCE_PRIMARY_KEY = "singleton_source_primary_key"
    NATURAL_KEY = "natural_key"
    COMPOSITE_NATURAL_KEY = "composite_natural_key"
    PUBLIC_KEY = "public_key"
    USER_SYNC_IDENTITY = "user_sync_identity"
    USER_REFERENCE = "user_reference"
    USER_REFERENCE_PAIR = "user_reference_pair"
    TEXT_PRIMARY_KEY = "text_primary_key"


class ReceiverReferenceCategory(str, Enum):
    """A target-side resolver category; this registry does not resolve it."""

    USER = "user"
    COMMODITY = "commodity"
    OFFER = "offer"
    TRADE = "trade"
    CUSTOMER_RELATION = "customer_relation"
    NOTIFICATION = "notification"
    ADMIN_MARKET_MESSAGE = "admin_market_message"
    TELEGRAM_ADMIN_BROADCAST = "telegram_admin_broadcast"


class ReceiverGuardClassification(str, Enum):
    """A database-policy family required by a future table handler."""

    SOURCE_PRIMARY_KEY_ALIGNMENT = "source_primary_key_alignment"
    NATURAL_KEY_CONFLICT = "natural_key_conflict"
    NATURAL_IDENTITY_DELETE = "natural_identity_delete"
    REFERENCE_LOCALIZATION = "reference_localization"
    UPDATED_AT_RECENCY = "updated_at_recency"
    STRICT_UPDATED_AT_RECENCY = "strict_updated_at_recency"
    MARKET_TRANSITION_RECENCY = "market_transition_recency"
    REGISTRATION_VERSION = "registration_version"
    RELATION_LINK_MONOTONICITY = "relation_link_monotonicity"
    USER_COUNTER_EVENT = "user_counter_event"
    MONOTONIC_BOOLEAN = "monotonic_boolean"
    TERMINAL_STATE = "terminal_state"
    OFFER_VERSION_TERMINAL = "offer_version_terminal"
    OFFER_PUBLICATION_PRECEDENCE = "offer_publication_precedence"
    OFFER_REQUEST_VERSION_TERMINAL = "offer_request_version_terminal"
    COMPLETED_TRADE_IMMUTABILITY = "completed_trade_immutability"
    IMMUTABLE_DELIVERY_FIELDS = "immutable_delivery_fields"
    LOCAL_ONLY_FIELD_PROJECTION = "local_only_field_projection"
    DELETE_FORBIDDEN = "delete_forbidden"


class ReceiverHandlerClassification(str, Enum):
    """The future implementation effort, never an enablement state."""

    IDENTITY_PENDING = "identity_pending"
    DECLARATIVE_HANDLER_PENDING = "declarative_handler_pending"
    SEMANTIC_HANDLER_PENDING = "semantic_handler_pending"


class ReceiverApplyStatus(str, Enum):
    """Current receiver status.  No value in this registry permits mutation."""

    UNAVAILABLE = "unavailable"


class ReceiverPrerequisite(str, Enum):
    """Evidence or code that must exist before a table can become executable."""

    ATTESTED_BASELINE_CUTPOINT = "attested_baseline_cutpoint"
    NO_STANDBY_WRITES_AFTER_STREAM_START = "no_standby_writes_after_stream_start"
    DEDICATED_ATOMIC_RECEIVER_ADAPTER = "dedicated_atomic_receiver_adapter"
    PORTABLE_IDENTITY_PROOF = "portable_identity_proof"
    PRIMARY_KEY_ALIGNMENT_ATTESTATION = "primary_key_alignment_attestation"
    CANONICAL_REFERENCE_PROOF = "canonical_reference_proof"
    DECLARATIVE_HANDLER = "declarative_handler"
    SEMANTIC_HANDLER = "semantic_handler"
    DELETE_POLICY = "delete_policy"


ALLOWED_OBJECT_DELTA_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"INSERT", "UPDATE", "DELETE"}
)

# These preconditions are intentionally implicit in every table spec.  The
# current snapshot receipt does not bind a delta cursor/high-water mark and
# therefore cannot satisfy the first requirement yet.
GLOBAL_RECEIVER_PRECONDITIONS: Final[frozenset[ReceiverPrerequisite]] = frozenset(
    {
        ReceiverPrerequisite.ATTESTED_BASELINE_CUTPOINT,
        ReceiverPrerequisite.NO_STANDBY_WRITES_AFTER_STREAM_START,
        ReceiverPrerequisite.DEDICATED_ATOMIC_RECEIVER_ADAPTER,
    }
)


@dataclass(frozen=True)
class ReceiverReferenceSpec:
    """One target field that needs an explicitly defined reference resolver.

    ``canonical_wire_field`` names the stable evidence a future handler should
    consume when it exists.  ``None`` means the present wire format contains no
    sufficient canonical evidence; the table remains blocked rather than
    treating the numeric source id as portable.
    """

    local_column: str
    category: ReceiverReferenceCategory
    nullable: bool
    canonical_wire_field: str | None = None


@dataclass(frozen=True)
class ObjectDeltaReceiverTableSpec:
    """Immutable declaration for one enabled Object-delta source table.

    ``allowed_columns`` contains only physical target columns.  ``wire_only``
    fields are authenticated payload evidence to be consumed by a future
    resolver and must never be passed directly to SQL.
    """

    table: str
    identity_kind: ReceiverIdentityKind
    identity_fields: tuple[str, ...]
    allowed_operations: frozenset[str]
    allowed_columns: frozenset[str]
    wire_only_fields: frozenset[str]
    references: tuple[ReceiverReferenceSpec, ...]
    guard_classifications: frozenset[ReceiverGuardClassification]
    handler_classification: ReceiverHandlerClassification
    table_preconditions: frozenset[ReceiverPrerequisite]
    apply_status: ReceiverApplyStatus = ReceiverApplyStatus.UNAVAILABLE

    def __post_init__(self) -> None:
        if not isinstance(self.table, str) or not self.table:
            raise ObjectDeltaReceiverRegistryError("receiver table name is invalid")
        if not isinstance(self.identity_kind, ReceiverIdentityKind):
            raise ObjectDeltaReceiverRegistryError("receiver identity kind is invalid")
        if not isinstance(self.handler_classification, ReceiverHandlerClassification):
            raise ObjectDeltaReceiverRegistryError("receiver handler classification is invalid")
        if not self.identity_fields or any(not isinstance(field, str) or not field for field in self.identity_fields):
            raise ObjectDeltaReceiverRegistryError("receiver table identity is invalid")
        if not self.allowed_operations or not self.allowed_operations <= ALLOWED_OBJECT_DELTA_OPERATIONS:
            raise ObjectDeltaReceiverRegistryError("receiver table operations are invalid")
        if not self.allowed_columns or any(not isinstance(column, str) or not column for column in self.allowed_columns):
            raise ObjectDeltaReceiverRegistryError("receiver table columns are invalid")
        if self.wire_only_fields & self.allowed_columns:
            raise ObjectDeltaReceiverRegistryError("receiver wire-only fields overlap target columns")
        if any(not isinstance(field, str) or not field for field in self.wire_only_fields):
            raise ObjectDeltaReceiverRegistryError("receiver wire-only fields are invalid")
        if any(
            field not in self.allowed_columns and field not in self.wire_only_fields
            for field in self.identity_fields
        ):
            raise ObjectDeltaReceiverRegistryError("receiver identity field is not declared")
        if not self.guard_classifications:
            raise ObjectDeltaReceiverRegistryError("receiver guard classification is missing")
        if any(
            not isinstance(guard, ReceiverGuardClassification)
            for guard in self.guard_classifications
        ):
            raise ObjectDeltaReceiverRegistryError("receiver guard classification is invalid")
        if not self.table_preconditions:
            raise ObjectDeltaReceiverRegistryError("receiver table preconditions are missing")
        if any(
            not isinstance(precondition, ReceiverPrerequisite)
            for precondition in self.table_preconditions
        ):
            raise ObjectDeltaReceiverRegistryError("receiver table preconditions are invalid")
        if self.apply_status is not ReceiverApplyStatus.UNAVAILABLE:
            raise ObjectDeltaReceiverRegistryError("receiver apply status must remain unavailable")
        if self.identity_kind in {
            ReceiverIdentityKind.SOURCE_PRIMARY_KEY,
            ReceiverIdentityKind.SINGLETON_SOURCE_PRIMARY_KEY,
        } and ReceiverPrerequisite.PRIMARY_KEY_ALIGNMENT_ATTESTATION not in self.table_preconditions:
            raise ObjectDeltaReceiverRegistryError(
                "source primary-key identity requires an explicit alignment attestation"
            )
        seen_reference_columns: set[str] = set()
        for reference in self.references:
            if not isinstance(reference, ReceiverReferenceSpec):
                raise ObjectDeltaReceiverRegistryError("receiver reference declaration is invalid")
            if reference.local_column not in self.allowed_columns:
                raise ObjectDeltaReceiverRegistryError("receiver reference column is not declared")
            if reference.local_column in seen_reference_columns:
                raise ObjectDeltaReceiverRegistryError("receiver reference column is duplicated")
            seen_reference_columns.add(reference.local_column)
            if reference.canonical_wire_field is not None and (
                reference.canonical_wire_field not in self.allowed_columns
                and reference.canonical_wire_field not in self.wire_only_fields
            ):
                raise ObjectDeltaReceiverRegistryError("receiver reference evidence is not declared")

    @property
    def reference_categories(self) -> frozenset[ReceiverReferenceCategory]:
        """The resolver families required by this table, without doing I/O."""

        return frozenset(reference.category for reference in self.references)

    @property
    def required_preconditions(self) -> frozenset[ReceiverPrerequisite]:
        """Global cut-point requirements plus this table's own blockers."""

        return GLOBAL_RECEIVER_PRECONDITIONS | self.table_preconditions

    @property
    def currently_applicable(self) -> bool:
        """Always false until a separately reviewed implementation changes it."""

        return False


def _columns(*values: str) -> frozenset[str]:
    return frozenset(values)


def _wire(*values: str) -> frozenset[str]:
    return frozenset(values)


def _guards(*values: ReceiverGuardClassification) -> frozenset[ReceiverGuardClassification]:
    return frozenset(values)


def _preconditions(*values: ReceiverPrerequisite) -> frozenset[ReceiverPrerequisite]:
    return frozenset(values)


def _reference(
    local_column: str,
    category: ReceiverReferenceCategory,
    *,
    nullable: bool,
    canonical_wire_field: str | None = None,
) -> ReceiverReferenceSpec:
    return ReceiverReferenceSpec(
        local_column=local_column,
        category=category,
        nullable=nullable,
        canonical_wire_field=canonical_wire_field,
    )


_SPECS: Final[tuple[ObjectDeltaReceiverTableSpec, ...]] = (
    ObjectDeltaReceiverTableSpec(
        table="accountant_relations",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("invitation_token",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "owner_user_id", "accountant_user_id", "created_by_user_id", "invitation_token",
            "global_account_name", "relation_display_name", "duty_description", "mobile_number",
            "status", "expires_at", "activated_at", "deleted_at", "created_at", "updated_at",
            "sync_version",
        ),
        wire_only_fields=_wire("_registration_user_references"),
        references=(
            _reference("owner_user_id", ReceiverReferenceCategory.USER, nullable=False, canonical_wire_field="_registration_user_references"),
            _reference("accountant_user_id", ReceiverReferenceCategory.USER, nullable=True, canonical_wire_field="_registration_user_references"),
            _reference("created_by_user_id", ReceiverReferenceCategory.USER, nullable=True, canonical_wire_field="_registration_user_references"),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.REGISTRATION_VERSION,
            ReceiverGuardClassification.RELATION_LINK_MONOTONICITY,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="admin_broadcast_messages",
        identity_kind=ReceiverIdentityKind.SOURCE_PRIMARY_KEY,
        identity_fields=("id",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns("id", "content", "created_by_id", "target_groups", "recipient_count", "published_at", "created_at"),
        wire_only_fields=_wire(),
        references=(
            _reference("created_by_id", ReceiverReferenceCategory.USER, nullable=False),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.SOURCE_PRIMARY_KEY_ALIGNMENT,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
        ),
        handler_classification=ReceiverHandlerClassification.IDENTITY_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PRIMARY_KEY_ALIGNMENT_ATTESTATION,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.DECLARATIVE_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="admin_market_messages",
        identity_kind=ReceiverIdentityKind.SOURCE_PRIMARY_KEY,
        identity_fields=("id",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "content", "created_by_id", "reused_from_id", "is_active", "notified_recipients_count",
            "published_at", "created_at", "updated_at",
        ),
        wire_only_fields=_wire(),
        references=(
            _reference("created_by_id", ReceiverReferenceCategory.USER, nullable=False),
            _reference("reused_from_id", ReceiverReferenceCategory.ADMIN_MARKET_MESSAGE, nullable=True),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.SOURCE_PRIMARY_KEY_ALIGNMENT,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.UPDATED_AT_RECENCY,
        ),
        handler_classification=ReceiverHandlerClassification.IDENTITY_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PRIMARY_KEY_ALIGNMENT_ATTESTATION,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.DECLARATIVE_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="commodities",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("name",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns("id", "name"),
        wire_only_fields=_wire(),
        references=(),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
        ),
        handler_classification=ReceiverHandlerClassification.DECLARATIVE_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.DECLARATIVE_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="commodity_aliases",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("alias",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns("id", "alias", "commodity_id"),
        wire_only_fields=_wire("commodity_name"),
        references=(
            _reference("commodity_id", ReceiverReferenceCategory.COMMODITY, nullable=False, canonical_wire_field="commodity_name"),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
        ),
        handler_classification=ReceiverHandlerClassification.DECLARATIVE_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.DECLARATIVE_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="customer_relations",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("invitation_token",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "owner_user_id", "customer_user_id", "created_by_user_id", "invitation_token",
            "management_name", "customer_tier", "commission_rate", "min_trade_quantity", "max_trade_quantity",
            "max_daily_trades", "max_daily_commodity_volume", "trading_restricted_until", "status",
            "expires_at", "activated_at", "deleted_at", "created_at", "updated_at", "sync_version",
        ),
        wire_only_fields=_wire("_registration_user_references"),
        references=(
            _reference("owner_user_id", ReceiverReferenceCategory.USER, nullable=False, canonical_wire_field="_registration_user_references"),
            _reference("customer_user_id", ReceiverReferenceCategory.USER, nullable=True, canonical_wire_field="_registration_user_references"),
            _reference("created_by_user_id", ReceiverReferenceCategory.USER, nullable=True, canonical_wire_field="_registration_user_references"),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.REGISTRATION_VERSION,
            ReceiverGuardClassification.RELATION_LINK_MONOTONICITY,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="invitations",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("token",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "account_name", "mobile_number", "token", "short_code", "role", "kind", "created_by_id",
            "is_used", "expires_at", "registered_user_id", "completed_at", "completed_via", "revoked_at",
            "sync_version", "created_at", "updated_at",
        ),
        wire_only_fields=_wire("_registration_user_references"),
        references=(
            _reference("created_by_id", ReceiverReferenceCategory.USER, nullable=True, canonical_wire_field="_registration_user_references"),
            _reference("registered_user_id", ReceiverReferenceCategory.USER, nullable=True, canonical_wire_field="_registration_user_references"),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.REGISTRATION_VERSION,
            ReceiverGuardClassification.MONOTONIC_BOOLEAN,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="market_runtime_state",
        identity_kind=ReceiverIdentityKind.SINGLETON_SOURCE_PRIMARY_KEY,
        identity_fields=("id",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "is_open", "active_web_notice_visible", "offers_since_last_open", "last_transition_at",
            "created_at", "updated_at",
        ),
        wire_only_fields=_wire(),
        references=(),
        guard_classifications=_guards(
            ReceiverGuardClassification.SOURCE_PRIMARY_KEY_ALIGNMENT,
            ReceiverGuardClassification.MARKET_TRANSITION_RECENCY,
        ),
        handler_classification=ReceiverHandlerClassification.IDENTITY_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PRIMARY_KEY_ALIGNMENT_ATTESTATION,
            ReceiverPrerequisite.DECLARATIVE_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="market_schedule_overrides",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("date",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "date", "override_type", "open_time_local", "close_time_local", "note",
            "created_by_user_id", "created_at", "updated_at",
        ),
        wire_only_fields=_wire(),
        references=(
            _reference("created_by_user_id", ReceiverReferenceCategory.USER, nullable=True),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.UPDATED_AT_RECENCY,
        ),
        handler_classification=ReceiverHandlerClassification.DECLARATIVE_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.DECLARATIVE_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="notifications",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("dedupe_key",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "user_id", "message", "is_read", "created_at", "level", "category", "dedupe_key",
            "extra_payload",
        ),
        wire_only_fields=_wire(),
        references=(
            _reference("user_id", ReceiverReferenceCategory.USER, nullable=False),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.MONOTONIC_BOOLEAN,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="offer_publication_states",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("dedupe_key",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "version_id", "offer_id", "offer_public_id", "offer_home_server", "surface",
            "publication_owner_server", "status", "dedupe_key", "surface_resource_id", "telegram_chat_id",
            "telegram_message_id", "offer_version_id", "last_known_offer_status", "last_attempt_at",
            "last_success_at", "next_retry_at", "disabled_at", "lagged_at", "error_code", "error_message",
            "state_metadata", "archived", "created_at", "updated_at",
        ),
        wire_only_fields=_wire(),
        references=(
            _reference("offer_id", ReceiverReferenceCategory.OFFER, nullable=True, canonical_wire_field="offer_public_id"),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.OFFER_PUBLICATION_PRECEDENCE,
            ReceiverGuardClassification.LOCAL_ONLY_FIELD_PROJECTION,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="offer_requests",
        identity_kind=ReceiverIdentityKind.COMPOSITE_NATURAL_KEY,
        identity_fields=("request_home_server", "idempotency_key"),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "version_id", "request_home_server", "local_offer_id", "offer_public_id", "requester_user_id",
            "actor_user_id", "request_source_surface", "request_source_server", "requested_quantity",
            "idempotency_key", "received_at", "decided_at", "result_status", "public_failure_code",
            "public_failure_message", "internal_failure_code", "internal_failure_context", "resulting_trade_id",
            "customer_relation_id", "customer_owner_user_id", "customer_tier_snapshot",
            "customer_management_name_snapshot", "customer_commission_rate_snapshot", "customer_commission_context",
            "archived", "created_at", "updated_at",
        ),
        wire_only_fields=_wire("resulting_trade_number", "customer_relation_invitation_token"),
        references=(
            _reference("local_offer_id", ReceiverReferenceCategory.OFFER, nullable=True, canonical_wire_field="offer_public_id"),
            _reference("requester_user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("actor_user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("resulting_trade_id", ReceiverReferenceCategory.TRADE, nullable=True, canonical_wire_field="resulting_trade_number"),
            _reference("customer_relation_id", ReceiverReferenceCategory.CUSTOMER_RELATION, nullable=True, canonical_wire_field="customer_relation_invitation_token"),
            _reference("customer_owner_user_id", ReceiverReferenceCategory.USER, nullable=True),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.OFFER_REQUEST_VERSION_TERMINAL,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="offers",
        identity_kind=ReceiverIdentityKind.PUBLIC_KEY,
        identity_fields=("offer_public_id",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "offer_public_id", "version_id", "user_id", "actor_user_id", "home_server", "offer_type",
            "settlement_type", "commodity_id", "quantity", "price", "exclude_from_competitive_price",
            "price_warning_type", "expire_reason", "expired_at", "expired_by_user_id", "expired_by_actor_user_id",
            "expire_source_surface", "expire_source_server", "remaining_quantity", "is_wholesale", "lot_sizes",
            "original_lot_sizes", "status", "notes", "channel_message_id", "republished_offer_id",
            "republished_from_offer_public_id", "created_at", "updated_at", "idempotency_key",
            "idempotency_fingerprint_version", "idempotency_fingerprint", "archived",
        ),
        wire_only_fields=_wire("commodity_name", "republished_offer_public_id"),
        references=(
            _reference("user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("actor_user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("commodity_id", ReceiverReferenceCategory.COMMODITY, nullable=False, canonical_wire_field="commodity_name"),
            _reference("expired_by_user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("expired_by_actor_user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("republished_offer_id", ReceiverReferenceCategory.OFFER, nullable=True, canonical_wire_field="republished_offer_public_id"),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.OFFER_VERSION_TERMINAL,
            ReceiverGuardClassification.LOCAL_ONLY_FIELD_PROJECTION,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="trades",
        identity_kind=ReceiverIdentityKind.PUBLIC_KEY,
        identity_fields=("trade_number",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "version_id", "trade_number", "offer_id", "offer_user_id", "offer_user_mobile",
            "responder_user_id", "responder_user_mobile", "actor_user_id", "commodity_id", "trade_type",
            "settlement_type", "quantity", "price", "status", "note", "created_at", "updated_at",
            "confirmed_at", "completed_at", "idempotency_key", "archived",
        ),
        wire_only_fields=_wire("commodity_name", "offer_public_id"),
        references=(
            _reference("offer_id", ReceiverReferenceCategory.OFFER, nullable=True, canonical_wire_field="offer_public_id"),
            _reference("offer_user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("responder_user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("actor_user_id", ReceiverReferenceCategory.USER, nullable=True),
            _reference("commodity_id", ReceiverReferenceCategory.COMMODITY, nullable=False, canonical_wire_field="commodity_name"),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.COMPLETED_TRADE_IMMUTABILITY,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="trade_delivery_receipts",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("dedupe_key",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "event_type", "dedupe_key", "trade_id", "trade_number", "offer_id", "recipient_user_id",
            "recipient_role", "channel", "destination_server", "status", "reason", "notification_id",
            "telegram_message_id", "worker_id", "lease_until", "attempt_count", "next_retry_at", "last_error",
            "last_error_class", "audit_payload", "event_created_at", "sent_at", "terminal_at", "created_at",
            "updated_at",
        ),
        wire_only_fields=_wire(),
        references=(
            _reference("trade_id", ReceiverReferenceCategory.TRADE, nullable=True, canonical_wire_field="trade_number"),
            _reference("offer_id", ReceiverReferenceCategory.OFFER, nullable=True, canonical_wire_field="trade_number"),
            _reference("recipient_user_id", ReceiverReferenceCategory.USER, nullable=False),
            _reference("notification_id", ReceiverReferenceCategory.NOTIFICATION, nullable=True),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.TERMINAL_STATE,
            ReceiverGuardClassification.IMMUTABLE_DELIVERY_FIELDS,
            ReceiverGuardClassification.LOCAL_ONLY_FIELD_PROJECTION,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="telegram_link_tokens",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("token_hash",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "user_id", "token_hash", "status", "issued_by_server", "expires_at", "used_at",
            "used_telegram_id", "revoked_at", "created_at", "updated_at",
        ),
        wire_only_fields=_wire("_registration_user_references"),
        references=(
            _reference("user_id", ReceiverReferenceCategory.USER, nullable=False, canonical_wire_field="_registration_user_references"),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.TERMINAL_STATE,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="telegram_admin_broadcasts",
        identity_kind=ReceiverIdentityKind.SOURCE_PRIMARY_KEY,
        identity_fields=("id",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "content", "created_by_id", "audience_type", "target_groups", "recipient_count", "status",
            "queued_at", "completed_at", "created_at", "updated_at",
        ),
        wire_only_fields=_wire(),
        references=(
            _reference("created_by_id", ReceiverReferenceCategory.USER, nullable=False),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.SOURCE_PRIMARY_KEY_ALIGNMENT,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
        ),
        handler_classification=ReceiverHandlerClassification.IDENTITY_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PRIMARY_KEY_ALIGNMENT_ATTESTATION,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.DECLARATIVE_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="telegram_admin_broadcast_receipts",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("dedupe_key",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "broadcast_id", "recipient_user_id", "telegram_id_at_enqueue", "telegram_id_at_send", "dedupe_key",
            "status", "reason", "telegram_message_id", "attempt_count", "next_retry_at", "last_error_class",
            "last_error_message", "worker_id", "lease_until", "sent_at", "terminal_at", "created_at", "updated_at",
        ),
        wire_only_fields=_wire(),
        references=(
            _reference("broadcast_id", ReceiverReferenceCategory.TELEGRAM_ADMIN_BROADCAST, nullable=False),
            _reference("recipient_user_id", ReceiverReferenceCategory.USER, nullable=False),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.TERMINAL_STATE,
            ReceiverGuardClassification.IMMUTABLE_DELIVERY_FIELDS,
            ReceiverGuardClassification.LOCAL_ONLY_FIELD_PROJECTION,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="telegram_notification_outbox",
        identity_kind=ReceiverIdentityKind.NATURAL_KEY,
        identity_fields=("dedupe_key",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "dedupe_key", "source_type", "source_id", "recipient_user_id", "telegram_id_at_enqueue",
            "telegram_id_at_send", "text", "parse_mode", "status", "reason", "telegram_message_id",
            "attempt_count", "next_retry_at", "last_error_class", "last_error_message", "worker_id",
            "lease_until", "sent_at", "terminal_at", "extra_payload", "created_at", "updated_at",
        ),
        wire_only_fields=_wire(),
        references=(
            _reference("recipient_user_id", ReceiverReferenceCategory.USER, nullable=True),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_KEY_CONFLICT,
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.TERMINAL_STATE,
            ReceiverGuardClassification.IMMUTABLE_DELIVERY_FIELDS,
            ReceiverGuardClassification.LOCAL_ONLY_FIELD_PROJECTION,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="trading_settings",
        identity_kind=ReceiverIdentityKind.TEXT_PRIMARY_KEY,
        identity_fields=("key",),
        allowed_operations=frozenset({"INSERT", "UPDATE"}),
        allowed_columns=_columns("key", "value", "updated_at"),
        wire_only_fields=_wire(),
        references=(),
        guard_classifications=_guards(
            ReceiverGuardClassification.UPDATED_AT_RECENCY,
            ReceiverGuardClassification.DELETE_FORBIDDEN,
        ),
        handler_classification=ReceiverHandlerClassification.DECLARATIVE_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.DECLARATIVE_HANDLER,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="user_blocks",
        identity_kind=ReceiverIdentityKind.USER_REFERENCE_PAIR,
        identity_fields=("blocker_id", "blocked_id"),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns("id", "blocker_id", "blocked_id", "created_at"),
        wire_only_fields=_wire(),
        references=(
            _reference("blocker_id", ReceiverReferenceCategory.USER, nullable=False),
            _reference("blocked_id", ReceiverReferenceCategory.USER, nullable=False),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="user_notification_preferences",
        identity_kind=ReceiverIdentityKind.USER_REFERENCE,
        identity_fields=("user_id",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns("id", "user_id", "market_offer_push_enabled", "created_at", "updated_at"),
        wire_only_fields=_wire(),
        references=(
            _reference("user_id", ReceiverReferenceCategory.USER, nullable=False),
        ),
        guard_classifications=_guards(
            ReceiverGuardClassification.NATURAL_IDENTITY_DELETE,
            ReceiverGuardClassification.REFERENCE_LOCALIZATION,
            ReceiverGuardClassification.STRICT_UPDATED_AT_RECENCY,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.CANONICAL_REFERENCE_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
    ObjectDeltaReceiverTableSpec(
        table="users",
        identity_kind=ReceiverIdentityKind.USER_SYNC_IDENTITY,
        identity_fields=("_sync_identity",),
        allowed_operations=ALLOWED_OBJECT_DELTA_OPERATIONS,
        allowed_columns=_columns(
            "id", "account_name", "mobile_number", "normalized_account_name", "normalized_mobile_number",
            "telegram_id", "username", "full_name", "address", "avatar_file_id", "role", "account_status",
            "deactivated_at", "messenger_grace_expires_at", "messenger_blocked_at", "has_bot_access",
            "bot_onboarding_required_step", "bot_onboarding_completed_step", "bot_onboarding_completed_at",
            "is_deleted", "deleted_at", "admin_password_hash", "must_change_password", "trading_restricted_until",
            "max_daily_trades", "max_active_commodities", "max_daily_requests", "limitations_expire_at",
            "trades_count", "commodities_traded_count", "channel_messages_count", "counter_epoch",
            "max_sessions", "max_accountants", "max_customers", "home_server", "sync_version",
            "can_block_users", "max_blocked_users", "last_seen_at", "created_at", "updated_at",
        ),
        wire_only_fields=_wire(
            "_sync_identity", "_sync_contract", "_counter_event_id", "_counter_event_kind", "_counter_epoch",
            "_counter_deltas", "_counter_occurred_at",
        ),
        references=(),
        guard_classifications=_guards(
            ReceiverGuardClassification.REGISTRATION_VERSION,
            ReceiverGuardClassification.USER_COUNTER_EVENT,
            ReceiverGuardClassification.MONOTONIC_BOOLEAN,
            ReceiverGuardClassification.LOCAL_ONLY_FIELD_PROJECTION,
        ),
        handler_classification=ReceiverHandlerClassification.SEMANTIC_HANDLER_PENDING,
        table_preconditions=_preconditions(
            ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
            ReceiverPrerequisite.SEMANTIC_HANDLER,
            ReceiverPrerequisite.DELETE_POLICY,
        ),
    ),
)


def _build_registry(
    specs: tuple[ObjectDeltaReceiverTableSpec, ...],
) -> Mapping[str, ObjectDeltaReceiverTableSpec]:
    registry: dict[str, ObjectDeltaReceiverTableSpec] = {}
    for spec in specs:
        if spec.table in registry:
            raise ObjectDeltaReceiverRegistryError("receiver table is declared more than once")
        registry[spec.table] = spec
    if not registry:
        raise ObjectDeltaReceiverRegistryError("receiver table registry is empty")
    return MappingProxyType(registry)


_REGISTRY: Final[Mapping[str, ObjectDeltaReceiverTableSpec]] = _build_registry(_SPECS)


def receiver_table_spec(table: object) -> ObjectDeltaReceiverTableSpec:
    """Return one exact declaration and fail closed for missing/unknown tables."""

    if not isinstance(table, str) or not table.strip():
        raise ObjectDeltaReceiverRegistryError("receiver table is missing")
    try:
        return _REGISTRY[table]
    except KeyError as exc:
        raise ObjectDeltaReceiverRegistryError("receiver table is unknown") from exc


def receiver_table_specs() -> Mapping[str, ObjectDeltaReceiverTableSpec]:
    """Return the immutable declaration map; callers cannot enable a table."""

    return _REGISTRY


def validate_receiver_table_operation(table: object, operation: object) -> ObjectDeltaReceiverTableSpec:
    """Validate a declared operation without authorizing or applying it."""

    spec = receiver_table_spec(table)
    if not isinstance(operation, str) or not operation:
        raise ObjectDeltaReceiverRegistryError("receiver operation is missing")
    if operation not in spec.allowed_operations:
        raise ObjectDeltaReceiverRegistryError("receiver operation is forbidden for this table")
    return spec


def validate_receiver_target_columns(table: object, columns: object) -> frozenset[str]:
    """Reject a target projection containing an undeclared physical column.

    This is a declaration check only.  It does not validate source payload
    fields, coerce values, resolve references, construct SQL, or grant apply
    permission.
    """

    spec = receiver_table_spec(table)
    if isinstance(columns, (str, bytes)) or not isinstance(columns, Iterable):
        raise ObjectDeltaReceiverRegistryError("receiver target columns are invalid")
    normalized: set[str] = set()
    for column in columns:
        if not isinstance(column, str) or not column:
            raise ObjectDeltaReceiverRegistryError("receiver target column is invalid")
        if column in normalized:
            raise ObjectDeltaReceiverRegistryError("receiver target column is duplicated")
        normalized.add(column)
    unknown = normalized - spec.allowed_columns
    if unknown:
        raise ObjectDeltaReceiverRegistryError("receiver target column is unknown")
    return frozenset(normalized)


def receiver_registry_fingerprint() -> str:
    """Return a deterministic declaration fingerprint for future release binding."""

    descriptors = []
    for table in sorted(_REGISTRY):
        spec = _REGISTRY[table]
        descriptors.append(
            {
                "table": spec.table,
                "identity_kind": spec.identity_kind.value,
                "identity_fields": list(spec.identity_fields),
                "allowed_operations": sorted(spec.allowed_operations),
                "allowed_columns": sorted(spec.allowed_columns),
                "wire_only_fields": sorted(spec.wire_only_fields),
                "references": [
                    {
                        "local_column": reference.local_column,
                        "category": reference.category.value,
                        "nullable": reference.nullable,
                        "canonical_wire_field": reference.canonical_wire_field,
                    }
                    for reference in spec.references
                ],
                "guard_classifications": sorted(guard.value for guard in spec.guard_classifications),
                "handler_classification": spec.handler_classification.value,
                "table_preconditions": sorted(precondition.value for precondition in spec.table_preconditions),
                "apply_status": spec.apply_status.value,
            }
        )
    payload = json.dumps(
        descriptors,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()
