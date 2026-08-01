"""Default-off root-bound reservation coordinator for one Object-delta prefix.

This is deliberately the first and only source-runtime wedge. It may durably
reserve or replay a deterministic publication attempt that was already derived
from an opaque locked snapshot. It never assembles new payloads, encrypts,
spools, signs, contacts Object Storage, or starts a worker.

The caller must retain the transaction that minted the locked snapshot. Before
the persistence seam can run, this coordinator independently reads the local
root-only source binding and a fresh root-only Writer Witness lease. It binds
both to the opaque authorization and exact immutable attempt. A caller cannot
inject a term, raw intent, or raw attempt into this public boundary.
"""

from __future__ import annotations

from core.application_writer_term import (
    ApplicationWriterTermError,
    ValidatedWriterTerm,
    policy_from_settings,
    require_active_writer_term,
)
from core.object_delta_runtime_binding import (
    ObjectDeltaRuntimeBindingError,
    ObjectDeltaSourceRuntimeBinding,
    binding_from_settings,
)
from core.object_delta_source_preupload_authorization import (
    ObjectDeltaSourcePreuploadAuthorizationError,
    project_authorized_object_delta_source_preupload_attempt,
    require_authorized_object_delta_source_preupload,
)
from core.object_delta_source_publication_attempt_persistence import (
    ObjectDeltaSourcePublicationAttemptPersistenceError,
    ObjectDeltaSourcePublicationAttemptPersistenceResult,
    _mint_authorized_object_delta_source_preupload_reservation,
    reserve_authorized_object_delta_source_preupload_attempt,
)


class ObjectDeltaSourcePreuploadReservationCoordinatorError(RuntimeError):
    """The root-bound pre-upload reservation gate cannot proceed safely."""


def _session_has_active_transaction(session: object) -> bool:
    probe = getattr(session, "in_transaction", None)
    try:
        state = probe() if callable(probe) else probe
    except Exception:
        return False
    return bool(state)


def _require_active_caller_owned_transaction(session: object) -> None:
    if not _session_has_active_transaction(session):
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation requires an active caller-owned transaction"
        )


def _settings_from_root_runtime() -> object:
    """Resolve application settings only when this explicit wedge is invoked."""

    from core.config import settings

    return settings


def _require_enabled(runtime_settings: object) -> None:
    enabled = getattr(
        runtime_settings,
        "object_delta_source_preupload_reservation_enabled",
        False,
    )
    if enabled is False:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation runtime is disabled"
        )
    if enabled is not True:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation runtime flag is invalid"
        )


def _load_root_runtime_binding(runtime_settings: object) -> ObjectDeltaSourceRuntimeBinding:
    try:
        binding = binding_from_settings(runtime_settings)
    except ObjectDeltaRuntimeBindingError as exc:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation root runtime binding is unavailable"
        ) from exc
    if binding is None:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation root runtime binding is required"
        )
    if type(binding) is not ObjectDeltaSourceRuntimeBinding:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation root runtime binding is invalid"
        )
    return binding


def _require_matching_fresh_writer_term(
    *,
    binding: ObjectDeltaSourceRuntimeBinding,
    authorization: object,
    runtime_settings: object,
) -> ValidatedWriterTerm:
    try:
        verified = require_authorized_object_delta_source_preupload(authorization)
        attempt = project_authorized_object_delta_source_preupload_attempt(verified)
    except ObjectDeltaSourcePreuploadAuthorizationError as exc:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation requires an opaque authorized locked snapshot"
        ) from exc
    if verified.pin.binding != binding:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation authorized pin does not match the root runtime binding"
        )
    try:
        writer_term = require_active_writer_term(policy_from_settings(runtime_settings))
    except ApplicationWriterTermError as exc:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation fresh Writer Witness term is unavailable"
        ) from exc
    if type(writer_term) is not ValidatedWriterTerm:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation fresh Writer Witness term is required"
        )
    if (
        writer_term.holder_site,
        writer_term.writer_epoch,
        writer_term.lease_id,
    ) != (
        binding.source_site,
        attempt.intent.writer_epoch,
        attempt.intent.writer_lease_id,
    ):
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation fresh Writer Witness term does not match locked evidence"
        )
    return writer_term


async def reserve_authorized_object_delta_source_preupload(
    session: object,
    authorization: object,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Durably reserve/replay one opaque locked prefix before encryption/PUT.

    The default-off switch is evaluated before the root binding or Writer
    Witness lease is read. The function never begins, commits, or rolls back
    the caller-owned transaction. It intentionally provides no continuation
    for seal, upload, receipt, attestation, or receiver delivery.
    """

    _require_active_caller_owned_transaction(session)
    runtime_settings = _settings_from_root_runtime()
    _require_enabled(runtime_settings)
    binding = _load_root_runtime_binding(runtime_settings)
    writer_term = _require_matching_fresh_writer_term(
        binding=binding,
        authorization=authorization,
        runtime_settings=runtime_settings,
    )
    try:
        persistence_authorization = _mint_authorized_object_delta_source_preupload_reservation(
            authorization,
            writer_term=writer_term,
        )
    except ObjectDeltaSourcePublicationAttemptPersistenceError as exc:
        raise ObjectDeltaSourcePreuploadReservationCoordinatorError(
            "source pre-upload reservation coordinator capability cannot be minted"
        ) from exc
    return await reserve_authorized_object_delta_source_preupload_attempt(
        session,
        persistence_authorization,
    )


__all__ = (
    "ObjectDeltaSourcePreuploadReservationCoordinatorError",
    "reserve_authorized_object_delta_source_preupload",
)
