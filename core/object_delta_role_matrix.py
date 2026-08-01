"""Pure, default-off role matrix for bidirectional Object-delta failover.

The separate source, receiver, and Writer Witness contracts validate useful
local facts, but none of them previously asserted that the two legal
directions form one mutually exclusive role assignment:

* normal operation: ``webapp_fi`` is the sole writer/source and
  ``webapp_ir`` is the standby receiver;
* emergency promotion: ``webapp_ir`` is the sole writer/source and
  ``webapp_fi`` is the standby receiver.

This module binds both release-controlled directions in one pure value.  It
does not load a root-only file, contact the Witness, read a database, start a
worker, or activate either role.  A future adapter must obtain the source pin,
receiver permit, and active Writer Witness term from their existing trusted
boundaries, then use the opaque result here before enabling a route.

It is deliberately a consistency gate, not proof of global liveness: only a
shared Witness/rollout protocol can prove that every process in a distributed
deployment observes the same activation.  The gate nevertheless rejects an
ambiguous local configuration, reversed route, mismatched release/campaign/
registry/key/policy, dual use of one writer term, or source/receiver role
overlap inside the declared matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.append_only_sync_delta_batch import LEASE_ID_RE, WEBAPP_SITES
from core.append_only_sync_delta_payload import REGISTRY_FINGERPRINT_RE
from core.object_delta_delivery_control_packet import (
    ObjectDeltaDeliveryControlPacketError,
    ObjectDeltaReceiverDeliveryPermit,
    controller_key_id_from_public_key,
    validate_object_delta_receiver_delivery_permit,
)
from core.object_delta_receiver_delivery_binding import (
    ObjectDeltaReceiverDeliveryBinding,
)
from core.object_delta_source_batch_attestation import source_key_id_from_public_key
from core.object_delta_source_cutover_publication_gate import (
    ObjectDeltaSourceCutoverPublicationPin,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    validate_object_delta_transport_policy,
)


OBJECT_DELTA_ROLE_MATRIX_SCHEMA = "gold-trade-object-delta-role-matrix-v1"
OBJECT_DELTA_ROLE_MATRIX_DEFAULT_ENABLED = False

OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER = "normal_fi_writer"
OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER = "promoted_ir_writer"
OBJECT_DELTA_ROLE_MATRIX_MODES = frozenset(
    {
        OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
        OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
    }
)

OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE = "writer_source"
OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER = "standby_receiver"

_VERIFIED_ROLE_MATRIX_CAPABILITY = object()


class ObjectDeltaRoleMatrixError(ValueError):
    """The two-site role assignment is incomplete, ambiguous, or unsafe."""


@dataclass(frozen=True)
class ObjectDeltaRoleMatrixWriterTerm:
    """The exact active Witness term consumed by Object-delta contracts."""

    holder_site: str
    writer_epoch: int
    writer_lease_id: str

    def __post_init__(self) -> None:
        if self.holder_site not in WEBAPP_SITES:
            raise ObjectDeltaRoleMatrixError("role-matrix Writer Witness holder site is invalid")
        if type(self.writer_epoch) is not int or self.writer_epoch < 1:
            raise ObjectDeltaRoleMatrixError("role-matrix Writer Witness epoch is invalid")
        if (
            not isinstance(self.writer_lease_id, str)
            or LEASE_ID_RE.fullmatch(self.writer_lease_id) is None
        ):
            raise ObjectDeltaRoleMatrixError("role-matrix Writer Witness lease is invalid")


@dataclass(frozen=True)
class ObjectDeltaRoleMatrixRoute:
    """One source/standby route with both independent local bindings."""

    source_pin: ObjectDeltaSourceCutoverPublicationPin
    receiver_binding: ObjectDeltaReceiverDeliveryBinding


@dataclass(frozen=True)
class ObjectDeltaRoleMatrixSiteRole:
    """Derived one-role-only projection for one WebApp site."""

    site: str
    role: str

    def __post_init__(self) -> None:
        if self.site not in WEBAPP_SITES:
            raise ObjectDeltaRoleMatrixError("role-matrix site is invalid")
        if self.role not in {
            OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
            OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
        }:
            raise ObjectDeltaRoleMatrixError("role-matrix site role is invalid")


@dataclass(frozen=True)
class VerifiedObjectDeltaRoleMatrix:
    """Opaque complete two-site role assignment for one active direction.

    The roles are derived rather than caller selected.  Only
    :func:`authorize_object_delta_role_matrix` can mint the private
    capability; direct construction and ``dataclasses.replace`` cannot be
    used as source or receiver activation authority.
    """

    normal_route: ObjectDeltaRoleMatrixRoute
    promoted_route: ObjectDeltaRoleMatrixRoute
    active_mode: str
    active_writer_term: ObjectDeltaRoleMatrixWriterTerm
    site_roles: tuple[ObjectDeltaRoleMatrixSiteRole, ObjectDeltaRoleMatrixSiteRole]
    _capability: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


def _normalise_source_pin(value: object) -> ObjectDeltaSourceCutoverPublicationPin:
    if type(value) is not ObjectDeltaSourceCutoverPublicationPin:
        raise ObjectDeltaRoleMatrixError("role-matrix source pin is invalid")
    try:
        return ObjectDeltaSourceCutoverPublicationPin(
            binding=value.binding,
            expected_source_public_key=value.expected_source_public_key,
            transport_policy=value.transport_policy,
        )
    except Exception as exc:
        raise ObjectDeltaRoleMatrixError("role-matrix source pin is invalid") from exc


def _normalise_receiver_binding(value: object) -> ObjectDeltaReceiverDeliveryBinding:
    if type(value) is not ObjectDeltaReceiverDeliveryBinding:
        raise ObjectDeltaRoleMatrixError("role-matrix receiver binding is invalid")
    try:
        policy = validate_object_delta_transport_policy(value.policy)
        permit = validate_object_delta_receiver_delivery_permit(value.permit, policy=policy)
        source_key_id = source_key_id_from_public_key(value.source_public_key)
        controller_key_id = controller_key_id_from_public_key(value.controller_public_key)
        registry_fingerprint = value.expected_registry_fingerprint
    except (
        AttributeError,
        ObjectDeltaDeliveryControlPacketError,
        ObjectDeltaTransportBindingError,
        ValueError,
    ) as exc:
        raise ObjectDeltaRoleMatrixError("role-matrix receiver binding is invalid") from exc
    if source_key_id != value.source_key_id:
        raise ObjectDeltaRoleMatrixError("role-matrix receiver source key ID is invalid")
    if permit.controller_key_id != controller_key_id:
        raise ObjectDeltaRoleMatrixError("role-matrix receiver controller key is invalid")
    if (
        not isinstance(registry_fingerprint, str)
        or REGISTRY_FINGERPRINT_RE.fullmatch(registry_fingerprint) is None
    ):
        raise ObjectDeltaRoleMatrixError("role-matrix receiver registry fingerprint is invalid")
    return ObjectDeltaReceiverDeliveryBinding(
        policy=policy,
        permit=permit,
        source_public_key=value.source_public_key,
        source_key_id=source_key_id,
        controller_public_key=value.controller_public_key,
        expected_registry_fingerprint=registry_fingerprint,
    )


def _normalise_writer_term(value: object) -> ObjectDeltaRoleMatrixWriterTerm:
    if type(value) is not ObjectDeltaRoleMatrixWriterTerm:
        raise ObjectDeltaRoleMatrixError("role-matrix Writer Witness term is invalid")
    try:
        return ObjectDeltaRoleMatrixWriterTerm(
            holder_site=value.holder_site,
            writer_epoch=value.writer_epoch,
            writer_lease_id=value.writer_lease_id,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaRoleMatrixError("role-matrix Writer Witness term is invalid") from exc


def _normalise_route(
    value: object,
    *,
    expected_source_site: str,
    expected_destination_site: str,
    label: str,
) -> ObjectDeltaRoleMatrixRoute:
    if type(value) is not ObjectDeltaRoleMatrixRoute:
        raise ObjectDeltaRoleMatrixError(f"role-matrix {label} route is invalid")
    source_pin = _normalise_source_pin(value.source_pin)
    receiver = _normalise_receiver_binding(value.receiver_binding)
    source = source_pin.binding
    permit = receiver.permit
    if (
        source.source_site != expected_source_site
        or source.destination_site != expected_destination_site
    ):
        raise ObjectDeltaRoleMatrixError(f"role-matrix {label} source route is invalid")
    expected_identity = (
        source.source_site,
        source.destination_site,
        source.campaign_id,
        source.release_sha,
        source.stream_generation_id,
    )
    actual_identity = (
        permit.source_site,
        permit.destination_site,
        permit.campaign_id,
        permit.release_sha,
        permit.stream_generation_id,
    )
    if actual_identity != expected_identity:
        raise ObjectDeltaRoleMatrixError(
            f"role-matrix {label} source and receiver routes do not match"
        )
    if source.expected_registry_fingerprint != receiver.expected_registry_fingerprint:
        raise ObjectDeltaRoleMatrixError(
            f"role-matrix {label} source and receiver registry fingerprints do not match"
        )
    if source_pin.transport_policy != receiver.policy:
        raise ObjectDeltaRoleMatrixError(
            f"role-matrix {label} source and receiver transport policies do not match"
        )
    if (
        source_pin.expected_source_public_key != receiver.source_public_key
        or source_key_id_from_public_key(source_pin.expected_source_public_key)
        != receiver.source_key_id
    ):
        raise ObjectDeltaRoleMatrixError(
            f"role-matrix {label} source and receiver source keys do not match"
        )
    return ObjectDeltaRoleMatrixRoute(
        source_pin=source_pin,
        receiver_binding=receiver,
    )


def _normalise_mode(value: object) -> str:
    if value not in OBJECT_DELTA_ROLE_MATRIX_MODES:
        raise ObjectDeltaRoleMatrixError("role-matrix active mode is invalid")
    return value


def _active_route(
    *,
    normal_route: ObjectDeltaRoleMatrixRoute,
    promoted_route: ObjectDeltaRoleMatrixRoute,
    active_mode: str,
) -> ObjectDeltaRoleMatrixRoute:
    return (
        normal_route
        if active_mode == OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER
        else promoted_route
    )


def _validate_matrix(
    *,
    normal_route: object,
    promoted_route: object,
    active_mode: object,
    active_writer_term: object,
) -> tuple[
    ObjectDeltaRoleMatrixRoute,
    ObjectDeltaRoleMatrixRoute,
    str,
    ObjectDeltaRoleMatrixWriterTerm,
    tuple[ObjectDeltaRoleMatrixSiteRole, ObjectDeltaRoleMatrixSiteRole],
]:
    normal = _normalise_route(
        normal_route,
        expected_source_site="webapp_fi",
        expected_destination_site="webapp_ir",
        label="normal FI-writer",
    )
    promoted = _normalise_route(
        promoted_route,
        expected_source_site="webapp_ir",
        expected_destination_site="webapp_fi",
        label="promoted IR-writer",
    )
    mode = _normalise_mode(active_mode)
    writer_term = _normalise_writer_term(active_writer_term)

    normal_source = normal.source_pin.binding
    promoted_source = promoted.source_pin.binding
    if (
        normal_source.campaign_id,
        normal_source.release_sha,
        normal_source.expected_registry_fingerprint,
    ) != (
        promoted_source.campaign_id,
        promoted_source.release_sha,
        promoted_source.expected_registry_fingerprint,
    ):
        raise ObjectDeltaRoleMatrixError(
            "role-matrix normal and promoted routes do not share release campaign and registry"
        )
    if normal.receiver_binding.policy != promoted.receiver_binding.policy:
        raise ObjectDeltaRoleMatrixError(
            "role-matrix normal and promoted transport policies do not match"
        )
    if normal.receiver_binding.controller_public_key != promoted.receiver_binding.controller_public_key:
        raise ObjectDeltaRoleMatrixError(
            "role-matrix normal and promoted controller keys do not match"
        )

    active = _active_route(
        normal_route=normal,
        promoted_route=promoted,
        active_mode=mode,
    )
    inactive = promoted if active is normal else normal
    active_source = active.source_pin.binding
    active_permit = active.receiver_binding.permit
    if writer_term.holder_site != active_source.source_site:
        raise ObjectDeltaRoleMatrixError(
            "role-matrix active Writer Witness holder does not match the active source"
        )
    if (active_permit.writer_epoch, active_permit.writer_lease_id) != (
        writer_term.writer_epoch,
        writer_term.writer_lease_id,
    ):
        raise ObjectDeltaRoleMatrixError(
            "role-matrix active receiver permit does not match the active Writer Witness term"
        )
    inactive_permit = inactive.receiver_binding.permit
    if (inactive_permit.writer_epoch, inactive_permit.writer_lease_id) == (
        writer_term.writer_epoch,
        writer_term.writer_lease_id,
    ):
        raise ObjectDeltaRoleMatrixError(
            "role-matrix one Writer Witness term cannot authorize both directions"
        )

    roles = (
        ObjectDeltaRoleMatrixSiteRole(
            site=active_source.source_site,
            role=OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
        ),
        ObjectDeltaRoleMatrixSiteRole(
            site=active_source.destination_site,
            role=OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
        ),
    )
    if (
        roles[0].site == roles[1].site
        or roles[0].role != OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE
        or roles[1].role != OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER
    ):
        raise ObjectDeltaRoleMatrixError("role-matrix source and receiver roles overlap")
    return normal, promoted, mode, writer_term, roles


def authorize_object_delta_role_matrix(
    *,
    normal_route: ObjectDeltaRoleMatrixRoute,
    promoted_route: ObjectDeltaRoleMatrixRoute,
    active_mode: str,
    active_writer_term: ObjectDeltaRoleMatrixWriterTerm,
) -> VerifiedObjectDeltaRoleMatrix:
    """Validate exactly one active bidirectional role assignment.

    This function remains default-off because it has no caller in the runtime.
    The returned capability does not activate a service; it makes a later
    adapter fail closed unless source/receiver bindings and the selected term
    agree on one direction only.
    """

    normal, promoted, mode, writer_term, roles = _validate_matrix(
        normal_route=normal_route,
        promoted_route=promoted_route,
        active_mode=active_mode,
        active_writer_term=active_writer_term,
    )
    verified = VerifiedObjectDeltaRoleMatrix(
        normal_route=normal,
        promoted_route=promoted,
        active_mode=mode,
        active_writer_term=writer_term,
        site_roles=roles,
    )
    object.__setattr__(verified, "_capability", _VERIFIED_ROLE_MATRIX_CAPABILITY)
    _validated_role_matrix(verified)
    return verified


def _validated_role_matrix(
    value: object,
) -> VerifiedObjectDeltaRoleMatrix:
    if type(value) is not VerifiedObjectDeltaRoleMatrix:
        raise ObjectDeltaRoleMatrixError("verified role-matrix capability is required")
    if value._capability is not _VERIFIED_ROLE_MATRIX_CAPABILITY:
        raise ObjectDeltaRoleMatrixError("verified role-matrix was not authorized")
    normal, promoted, mode, term, roles = _validate_matrix(
        normal_route=value.normal_route,
        promoted_route=value.promoted_route,
        active_mode=value.active_mode,
        active_writer_term=value.active_writer_term,
    )
    if value.site_roles != roles:
        raise ObjectDeltaRoleMatrixError("verified role-matrix site roles do not match its activation")
    if (
        value.normal_route != normal
        or value.promoted_route != promoted
        or value.active_mode != mode
        or value.active_writer_term != term
    ):
        raise ObjectDeltaRoleMatrixError("verified role-matrix is not normalized")
    return value


def require_verified_object_delta_role_matrix(value: object) -> VerifiedObjectDeltaRoleMatrix:
    """Revalidate the opaque role-matrix capability before a later hand-off."""

    return _validated_role_matrix(value)


def active_object_delta_role_matrix_route(
    value: VerifiedObjectDeltaRoleMatrix,
) -> ObjectDeltaRoleMatrixRoute:
    """Return only the selected route after capability revalidation."""

    verified = _validated_role_matrix(value)
    return _active_route(
        normal_route=verified.normal_route,
        promoted_route=verified.promoted_route,
        active_mode=verified.active_mode,
    )


def object_delta_role_matrix_site_role(
    value: VerifiedObjectDeltaRoleMatrix,
    *,
    site: str,
) -> ObjectDeltaRoleMatrixSiteRole:
    """Return the one mutually exclusive active role for a requested site."""

    verified = _validated_role_matrix(value)
    if site not in WEBAPP_SITES:
        raise ObjectDeltaRoleMatrixError("role-matrix requested site is invalid")
    for role in verified.site_roles:
        if role.site == site:
            return role
    raise ObjectDeltaRoleMatrixError("role-matrix requested site has no active role")
