"""Pure, default-off logical scope for a future Object-delta MVP.

This module is deliberately only an allowlist and prerequisite declaration.
It neither accepts nor validates a raw ``ChangeLog`` payload, so a selected
table/operation does not make that payload safe.  It also does not configure,
authorize, or enable an Object-delta receiver, database mutation, transport,
or any runtime component.

The caller must opt in explicitly for each selection with ``enabled=True``.
That opt-in remains an in-memory decision only; production wiring belongs to a
separate, later design.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


OBJECT_DELTA_MVP_SCOPE_VERSION = "object_delta_mvp_scope_v1"
OBJECT_DELTA_MVP_DEFAULT_ENABLED = False

# These remain false even when a caller explicitly asks this pure selector for
# an in-scope decision.  The selector receives no payload bytes or receiver
# authority and therefore cannot establish either property.
OBJECT_DELTA_MVP_VALIDATES_RAW_CHANGELOG_PAYLOADS = False
OBJECT_DELTA_MVP_ENABLES_RECEIVERS = False

INSERT = "INSERT"
UPDATE = "UPDATE"


class ObjectDeltaMvpScopeError(ValueError):
    """The requested logical Object-delta MVP selection is malformed."""


@dataclass(frozen=True)
class ObjectDeltaMvpReferencePrerequisite:
    """A relationship that must resolve by a canonical target identity.

    ``required`` describes whether every selected row must carry the
    relationship.  A false value means the relationship is nullable, but when
    it is present it must still be represented by the target's canonical
    identity rather than a site-local database id.
    """

    name: str
    target_table: str
    target_identity_kind: str
    target_identity_fields: tuple[str, ...]
    required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ObjectDeltaMvpScopeError("Object-delta MVP reference name is invalid")
        if not isinstance(self.target_table, str) or not self.target_table:
            raise ObjectDeltaMvpScopeError("Object-delta MVP reference target table is invalid")
        if not isinstance(self.target_identity_kind, str) or not self.target_identity_kind:
            raise ObjectDeltaMvpScopeError("Object-delta MVP reference identity kind is invalid")
        if (
            not isinstance(self.target_identity_fields, tuple)
            or not self.target_identity_fields
            or any(not isinstance(field, str) or not field for field in self.target_identity_fields)
        ):
            raise ObjectDeltaMvpScopeError("Object-delta MVP reference identity fields are invalid")
        if type(self.required) is not bool:
            raise ObjectDeltaMvpScopeError("Object-delta MVP reference required flag is invalid")


@dataclass(frozen=True)
class ObjectDeltaMvpTableScope:
    """The complete logical v1 contract for one explicitly named table."""

    table: str
    allowed_operations: tuple[str, ...]
    canonical_identity_kind: str
    canonical_identity_fields: tuple[str, ...]
    canonical_reference_prerequisites: tuple[ObjectDeltaMvpReferencePrerequisite, ...]
    requires_canonical_paired_user_identities: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.table, str) or not self.table:
            raise ObjectDeltaMvpScopeError("Object-delta MVP table name is invalid")
        if (
            not isinstance(self.allowed_operations, tuple)
            or not self.allowed_operations
            or any(operation not in {INSERT, UPDATE} for operation in self.allowed_operations)
            or len(set(self.allowed_operations)) != len(self.allowed_operations)
        ):
            raise ObjectDeltaMvpScopeError("Object-delta MVP allowed operations are invalid")
        if not isinstance(self.canonical_identity_kind, str) or not self.canonical_identity_kind:
            raise ObjectDeltaMvpScopeError("Object-delta MVP canonical identity kind is invalid")
        if (
            not isinstance(self.canonical_identity_fields, tuple)
            or not self.canonical_identity_fields
            or any(
                not isinstance(field, str) or not field
                for field in self.canonical_identity_fields
            )
        ):
            raise ObjectDeltaMvpScopeError("Object-delta MVP canonical identity fields are invalid")
        if (
            not isinstance(self.canonical_reference_prerequisites, tuple)
            or any(
                not isinstance(reference, ObjectDeltaMvpReferencePrerequisite)
                for reference in self.canonical_reference_prerequisites
            )
        ):
            raise ObjectDeltaMvpScopeError("Object-delta MVP reference prerequisites are invalid")
        reference_names = tuple(
            reference.name for reference in self.canonical_reference_prerequisites
        )
        if len(set(reference_names)) != len(reference_names):
            raise ObjectDeltaMvpScopeError("Object-delta MVP reference names must be unique")
        if type(self.requires_canonical_paired_user_identities) is not bool:
            raise ObjectDeltaMvpScopeError("Object-delta MVP user identity prerequisite is invalid")

    @property
    def required_canonical_reference_prerequisites(
        self,
    ) -> tuple[ObjectDeltaMvpReferencePrerequisite, ...]:
        """Return references that every selected row must carry."""

        return tuple(
            reference
            for reference in self.canonical_reference_prerequisites
            if reference.required
        )


@dataclass(frozen=True)
class ObjectDeltaMvpSelection:
    """One strict, in-memory logical scope decision.

    ``selected`` means only that the exact table/operation belongs to the
    declarative MVP allowlist and the caller supplied explicit opt-in.  It is
    not payload validation and is not receiver authority.
    """

    scope: ObjectDeltaMvpTableScope
    operation: str
    enabled: bool
    table_operation_in_scope: bool
    selected: bool
    reason: str
    validates_raw_changelog_payloads: bool = False
    enables_receiver: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ObjectDeltaMvpTableScope):
            raise ObjectDeltaMvpScopeError("Object-delta MVP selection scope is invalid")
        if self.operation not in _KNOWN_LOGICAL_OPERATIONS:
            raise ObjectDeltaMvpScopeError("Object-delta MVP selection operation is invalid")
        if type(self.enabled) is not bool or type(self.table_operation_in_scope) is not bool:
            raise ObjectDeltaMvpScopeError("Object-delta MVP selection flags are invalid")
        if type(self.selected) is not bool:
            raise ObjectDeltaMvpScopeError("Object-delta MVP selected flag is invalid")
        if self.table_operation_in_scope != (self.operation in self.scope.allowed_operations):
            raise ObjectDeltaMvpScopeError("Object-delta MVP selection scope does not match operation")
        if self.selected != (self.enabled and self.table_operation_in_scope):
            raise ObjectDeltaMvpScopeError("Object-delta MVP selected flag does not match scope")
        expected_reason = (
            SELECTION_REASON_OPERATION_OUTSIDE_V1_SCOPE
            if not self.table_operation_in_scope
            else SELECTION_REASON_SELECTED
            if self.enabled
            else SELECTION_REASON_DEFAULT_OFF
        )
        if self.reason != expected_reason:
            raise ObjectDeltaMvpScopeError("Object-delta MVP selection reason does not match scope")
        if self.validates_raw_changelog_payloads or self.enables_receiver:
            raise ObjectDeltaMvpScopeError(
                "Object-delta MVP selection cannot claim payload validation or receiver authority"
            )


SELECTION_REASON_SELECTED = "selected"
SELECTION_REASON_DEFAULT_OFF = "default_off"
SELECTION_REASON_OPERATION_OUTSIDE_V1_SCOPE = "operation_outside_v1_scope"

USER_CANONICAL_IDENTITY_KIND = "registration_identity_v1"
USER_CANONICAL_IDENTITY_FIELDS = (
    "normalized_account_name",
    "normalized_mobile_number",
)


def _reference(
    name: str,
    target_table: str,
    target_identity_kind: str,
    target_identity_fields: tuple[str, ...],
    *,
    required: bool,
) -> ObjectDeltaMvpReferencePrerequisite:
    return ObjectDeltaMvpReferencePrerequisite(
        name=name,
        target_table=target_table,
        target_identity_kind=target_identity_kind,
        target_identity_fields=target_identity_fields,
        required=required,
    )


def _user_reference(name: str, *, required: bool) -> ObjectDeltaMvpReferencePrerequisite:
    return _reference(
        name,
        "users",
        USER_CANONICAL_IDENTITY_KIND,
        USER_CANONICAL_IDENTITY_FIELDS,
        required=required,
    )


# Keep this declaration isolated from the existing legacy sync registry.  This
# is a deliberately smaller logical v1 scope, not a statement that legacy
# ChangeLog records are compatible with it.
OBJECT_DELTA_MVP_TABLE_SCOPES: Mapping[str, ObjectDeltaMvpTableScope] = MappingProxyType(
    {
        "trading_settings": ObjectDeltaMvpTableScope(
            table="trading_settings",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="trading_setting_key",
            canonical_identity_fields=("key",),
            canonical_reference_prerequisites=(),
        ),
        "market_schedule_overrides": ObjectDeltaMvpTableScope(
            table="market_schedule_overrides",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="market_schedule_date",
            canonical_identity_fields=("date",),
            canonical_reference_prerequisites=(
                _user_reference("created_by_user", required=False),
            ),
        ),
        "market_runtime_state": ObjectDeltaMvpTableScope(
            table="market_runtime_state",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="fixed_market_runtime_singleton",
            canonical_identity_fields=("market_runtime_singleton",),
            canonical_reference_prerequisites=(),
        ),
        "users": ObjectDeltaMvpTableScope(
            table="users",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind=USER_CANONICAL_IDENTITY_KIND,
            canonical_identity_fields=USER_CANONICAL_IDENTITY_FIELDS,
            canonical_reference_prerequisites=(),
        ),
        "invitations": ObjectDeltaMvpTableScope(
            table="invitations",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="invitation_token",
            canonical_identity_fields=("token",),
            canonical_reference_prerequisites=(
                _user_reference("created_by_user", required=False),
                _user_reference("registered_user", required=False),
            ),
        ),
        "accountant_relations": ObjectDeltaMvpTableScope(
            table="accountant_relations",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="accountant_relation_invitation_token",
            canonical_identity_fields=("invitation_token",),
            canonical_reference_prerequisites=(
                _reference(
                    "invitation",
                    "invitations",
                    "invitation_token",
                    ("token",),
                    required=True,
                ),
                _user_reference("owner_user", required=True),
                _user_reference("accountant_user", required=False),
                _user_reference("created_by_user", required=False),
            ),
        ),
        "customer_relations": ObjectDeltaMvpTableScope(
            table="customer_relations",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="customer_relation_invitation_token",
            canonical_identity_fields=("invitation_token",),
            canonical_reference_prerequisites=(
                _reference(
                    "invitation",
                    "invitations",
                    "invitation_token",
                    ("token",),
                    required=True,
                ),
                _user_reference("owner_user", required=True),
                _user_reference("customer_user", required=False),
                _user_reference("created_by_user", required=False),
            ),
        ),
        "user_blocks": ObjectDeltaMvpTableScope(
            table="user_blocks",
            allowed_operations=(INSERT,),
            canonical_identity_kind="canonical_paired_user_identities",
            canonical_identity_fields=("blocker_user_identity", "blocked_user_identity"),
            canonical_reference_prerequisites=(
                _user_reference("blocker_user", required=True),
                _user_reference("blocked_user", required=True),
            ),
            requires_canonical_paired_user_identities=True,
        ),
        "commodities": ObjectDeltaMvpTableScope(
            table="commodities",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="commodity_name",
            canonical_identity_fields=("name",),
            canonical_reference_prerequisites=(),
        ),
        "commodity_aliases": ObjectDeltaMvpTableScope(
            table="commodity_aliases",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="commodity_alias",
            canonical_identity_fields=("alias",),
            canonical_reference_prerequisites=(
                _reference(
                    "commodity",
                    "commodities",
                    "commodity_name",
                    ("name",),
                    required=True,
                ),
            ),
        ),
        "offers": ObjectDeltaMvpTableScope(
            table="offers",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="offer_public_id",
            canonical_identity_fields=("offer_public_id",),
            canonical_reference_prerequisites=(
                _reference(
                    "commodity",
                    "commodities",
                    "commodity_name",
                    ("name",),
                    required=True,
                ),
                _user_reference("owner_user", required=False),
                _user_reference("actor_user", required=False),
                _user_reference("expired_by_user", required=False),
                _user_reference("expired_by_actor_user", required=False),
                _reference(
                    "republished_from_offer",
                    "offers",
                    "offer_public_id",
                    ("offer_public_id",),
                    required=False,
                ),
            ),
        ),
        "offer_requests": ObjectDeltaMvpTableScope(
            table="offer_requests",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="offer_request_home_and_idempotency_key",
            canonical_identity_fields=("request_home_server", "idempotency_key"),
            canonical_reference_prerequisites=(
                _reference(
                    "offer",
                    "offers",
                    "offer_public_id",
                    ("offer_public_id",),
                    required=True,
                ),
                _user_reference("requester_user", required=False),
                _user_reference("actor_user", required=False),
                _reference(
                    "customer_relation",
                    "customer_relations",
                    "customer_relation_invitation_token",
                    ("invitation_token",),
                    required=False,
                ),
                _user_reference("customer_owner_user", required=False),
                _reference(
                    "resulting_trade",
                    "trades",
                    "trade_number",
                    ("trade_number",),
                    required=False,
                ),
            ),
        ),
        "trades": ObjectDeltaMvpTableScope(
            table="trades",
            allowed_operations=(INSERT, UPDATE),
            canonical_identity_kind="trade_number",
            canonical_identity_fields=("trade_number",),
            canonical_reference_prerequisites=(
                _reference(
                    "offer",
                    "offers",
                    "offer_public_id",
                    ("offer_public_id",),
                    required=False,
                ),
                _reference(
                    "commodity",
                    "commodities",
                    "commodity_name",
                    ("name",),
                    required=True,
                ),
                _user_reference("offer_user", required=False),
                _user_reference("responder_user", required=False),
                _user_reference("actor_user", required=False),
            ),
        ),
    }
)

# ``DELETE`` is recognized solely to return a clear negative decision.  It is
# intentionally absent from every scope declaration above.
_KNOWN_LOGICAL_OPERATIONS = frozenset({INSERT, UPDATE, "DELETE"})


def object_delta_mvp_scope_for_table(table: object) -> ObjectDeltaMvpTableScope:
    """Return the explicitly declared v1 scope for one exact table name.

    Unknown, whitespace-padded, and non-string table values are rejected
    instead of being normalized into an accidental allowlist match.
    """

    if not isinstance(table, str) or table not in OBJECT_DELTA_MVP_TABLE_SCOPES:
        raise ObjectDeltaMvpScopeError("Object-delta MVP table is unknown or malformed")
    return OBJECT_DELTA_MVP_TABLE_SCOPES[table]


def _require_known_operation(operation: object) -> str:
    if not isinstance(operation, str) or operation not in _KNOWN_LOGICAL_OPERATIONS:
        raise ObjectDeltaMvpScopeError("Object-delta MVP operation is unknown or malformed")
    return operation


def select_object_delta_mvp_scope(
    table: object,
    operation: object,
    *,
    enabled: bool = OBJECT_DELTA_MVP_DEFAULT_ENABLED,
) -> ObjectDeltaMvpSelection:
    """Return the default-off, exact logical v1 selection decision.

    A caller must opt in with the literal boolean ``enabled=True`` before an
    allowed table/operation is selected.  A known but excluded operation, such
    as ``DELETE`` or ``UPDATE`` for ``user_blocks``, returns an explicit
    negative decision.  Unknown or malformed table/operation values raise
    :class:`ObjectDeltaMvpScopeError`.
    """

    scope = object_delta_mvp_scope_for_table(table)
    normalized_operation = _require_known_operation(operation)
    if type(enabled) is not bool:
        raise ObjectDeltaMvpScopeError("Object-delta MVP enabled flag is invalid")

    table_operation_in_scope = normalized_operation in scope.allowed_operations
    if not table_operation_in_scope:
        reason = SELECTION_REASON_OPERATION_OUTSIDE_V1_SCOPE
    elif not enabled:
        reason = SELECTION_REASON_DEFAULT_OFF
    else:
        reason = SELECTION_REASON_SELECTED

    return ObjectDeltaMvpSelection(
        scope=scope,
        operation=normalized_operation,
        enabled=enabled,
        table_operation_in_scope=table_operation_in_scope,
        selected=enabled and table_operation_in_scope,
        reason=reason,
    )
