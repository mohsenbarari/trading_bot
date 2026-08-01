"""Focused non-live tests for the V4-only WA-FI↔Witness mailbox boundary."""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core import physical_full_matrix_v4_witness_anchor_fi_witness_anti_replay_registry as anti_replay
from core import physical_full_matrix_v4_witness_anchor_fi_witness_mailbox as mailbox
from core import physical_full_matrix_v4_witness_anchor_wire as wire


NOW = datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc)
RUN_ID = UUID("1e8dd581-2c2e-437f-b0f6-2f9de64af42f")
PLAN_SHA256 = "d" * 64
JOURNAL_BINDING = hashlib.sha256(b"v4-mailbox-journal-binding").hexdigest()
MAILBOX_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_witness_anchor_fi_witness_mailbox.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _normal_binding() -> dict[str, object]:
    return {
        "campaign_id": "physical-full-matrix-v4-mailbox-20260731",
        "release_sha": "a" * 40,
        "readiness_binding_sha256": _hash("readiness"),
        "route_commitment_sha256": _hash("route"),
        "four_role_binding_sha256": _hash("four-role"),
        "writer_holder_site": "webapp_fi",
        "writer_epoch": 12,
        "writer_lease_id": "writer-lease-v4-mailbox-000001",
        "witnessed_term_proof_sha256": _hash("term"),
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "roundtrip_attestation_sha256": _hash("roundtrip"),
        "roundtrip_configuration_sha256": _hash("configuration"),
        "witness_transition_id": "witness-transition-v4-mailbox-000001",
        "witness_sequence": 41,
    }


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now


class _RoleLocalAntiReplayRegistry:
    """Strict fake of one role-local durable reservation dependency.

    It deliberately keeps reservations across reconstructed mailbox objects so
    focused tests can prove that the mailbox has no process-local fallback.
    """

    def __init__(self, *, role: str, events: list[str]) -> None:
        self.role = role
        self.events = events
        self.reserved: set[str] = set()
        self.calls: list[tuple[str, str, str]] = []
        self.fail_after_reservation = False
        self.fail_after_reservation_kinds: set[str] = set()
        self.return_wrong_role = False
        self._sequence = 0

    def reserve_before_external_boundary(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        identifier_kind: str,
        identifier: str,
    ) -> anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt:
        self.events.append(f"{self.role}:reserve:{identifier_kind}")
        self.calls.append((identifier_kind, identifier, policy_identity.plan_sha256))
        if identifier in self.reserved:
            raise RuntimeError("identifier has already been durably reserved")
        self.reserved.add(identifier)
        self._sequence += 1
        if (
            self.fail_after_reservation
            or identifier_kind in self.fail_after_reservation_kinds
        ):
            raise RuntimeError("reservation callback outcome is ambiguous")
        if self.role == anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WA_FI_CONTROLLER:
            namespace = "wa-fi-controller"
            prefix = "wa-fi-controller-v4-anchor-reservation"
        else:
            namespace = "witness"
            prefix = "witness-v4-anchor-reservation"
        receipt_role = (
            anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WITNESS
            if self.return_wrong_role
            else self.role
        )
        return anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt(
            schema=anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA,
            role=receipt_role,
            state_namespace=namespace,
            reservation_prefix=prefix,
            policy_identity_sha256=mailbox._anti_replay_identity_sha256(policy_identity),
            identifier_kind=identifier_kind,
            identifier=identifier,
            reservation_sequence=self._sequence,
            reservation_record_sha256=_hash(
                f"{self.role}-reservation-{self._sequence}"
            ),
        )


_VerifiedAnchor = (
    wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead
    | wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
)


class _WitnessService:
    """Memory-only implementation of the pre-existing narrow V4 service."""

    def __init__(
        self,
        *,
        policy: wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
        identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        witness_private: Ed25519PrivateKey,
        clock: _Clock,
        events: list[str],
    ) -> None:
        self.policy = policy
        self.identity = identity
        self.witness_private = witness_private
        self.clock = clock
        self.events = events
        self.current: _VerifiedAnchor = (
            wire.verified_physical_full_matrix_v4_witness_anchor_genesis_head(
                policy=policy,
                now=clock.now,
            )
        )
        self.current_raw = wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
            policy.genesis
        )
        self.seen_replay_ids: set[str] = set()
        self.read_challenges: list[str] = []
        self.append_requests: list[bytes] = []
        self.observation_ordinal = 0

    def _response(
        self,
        *,
        read_challenge: str,
        anchor: _VerifiedAnchor | None = None,
        canonical_anchor: bytes | None = None,
    ) -> bytes:
        selected = self.current if anchor is None else anchor
        selected_raw = self.current_raw if canonical_anchor is None else canonical_anchor
        self.observation_ordinal += 1
        observation = wire.build_physical_full_matrix_v4_witness_anchor_read_observation(
            policy=self.policy,
            anchor_head=selected,
            read_challenge=read_challenge,
            observation_id=_hash(f"mailbox-observation-{self.observation_ordinal}"),
            observed_at=self.clock.now,
            expires_at=self.clock.now + timedelta(seconds=30),
            witness_private_key=self.witness_private,
        )
        return wire.build_physical_full_matrix_v4_witness_anchor_transport_envelope(
            canonical_anchor_head=selected_raw,
            canonical_read_observation=observation,
            read_challenge=read_challenge,
        )

    def read_signed_head(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        read_challenge: str,
    ) -> bytes:
        if policy_identity != self.identity:
            raise RuntimeError("wrong policy identity")
        self.events.append("witness-service-read")
        self.read_challenges.append(read_challenge)
        return self._response(read_challenge=read_challenge)

    def append_signed_request(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        canonical_controller_append_request: bytes,
        read_challenge: str,
    ) -> bytes:
        if policy_identity != self.identity:
            raise RuntimeError("wrong policy identity")
        self.events.append("witness-service-append")
        self.append_requests.append(canonical_controller_append_request)
        request = wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
            canonical_controller_append_request,
            policy=self.policy,
            predecessor=self.current,
            now=self.clock.now,
            seen_replay_ids=self.seen_replay_ids,
        )
        self.seen_replay_ids.add(request.replay_id)
        old = self.current
        immutable = wire.build_physical_full_matrix_v4_witness_anchor_immutable_head(
            policy=self.policy,
            predecessor=old,
            append_request=request,
            now=self.clock.now,
            witness_private_key=self.witness_private,
        )
        self.current = wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
            immutable,
            policy=self.policy,
            now=self.clock.now,
            expected_predecessor=old,
            append_request=request,
        )
        self.current_raw = immutable
        self.read_challenges.append(read_challenge)
        return self._response(read_challenge=read_challenge)


class _RoleLocalMailbox:
    """Four role-local callbacks, deliberately without any provider client."""

    def __init__(self, *, events: list[str]) -> None:
        self.events = events
        self.requests: list[mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest] = []
        self.responses: dict[
            tuple[str, str],
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse,
        ] = {}
        self.request_policies: list[
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy
        ] = []
        self.response_policies: list[
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy
        ] = []
        self.dispatcher: mailbox.PhysicalFullMatrixV4WitnessAnchorWitnessFiMailboxDispatcher | None = None
        self.fail_request_publication = False
        self.fail_response_publication = False
        self.fail_response_consume = False
        self.response_mutator = None
        self._ordinal = 0

    def _receipt(
        self,
        *,
        request_sha256: str,
        read_challenge: str,
        response: bool,
    ) -> (
        mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxPublicationReceipt
        | mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponsePublicationReceipt
    ):
        self._ordinal += 1
        version_id = f"mailbox-version-{self._ordinal:020d}"
        kwargs = {
            "schema": "gold-trade-physical-full-matrix-v4-witness-anchor-fi-witness-mailbox-receipt-v1",
            "request_sha256": request_sha256,
            "read_challenge": read_challenge,
            "object_version_id": version_id,
            "receipt_sha256": mailbox._receipt_digest(
                request_sha256=request_sha256,
                read_challenge=read_challenge,
                object_version_id=version_id,
            ),
        }
        if response:
            return mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponsePublicationReceipt(
                **kwargs
            )
        return mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxPublicationReceipt(
            **kwargs
        )


class _WaFiRequestOutbox:
    def __init__(self, bus: _RoleLocalMailbox) -> None:
        self.bus = bus

    def publish_wa_fi_v4_witness_anchor_request(
        self,
        *,
        root_policy: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
        request: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest,
    ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxPublicationReceipt:
        self.bus.events.append("wa-fi-request-publish")
        self.bus.request_policies.append(root_policy)
        if self.bus.fail_request_publication:
            raise RuntimeError("request outcome is ambiguous")
        self.bus.requests.append(request)
        return self.bus._receipt(
            request_sha256=request.request_sha256,
            read_challenge=request.read_challenge,
            response=False,
        )


class _WitnessFiRequestIngress:
    def __init__(self, bus: _RoleLocalMailbox) -> None:
        self.bus = bus

    def consume_witness_fi_v4_witness_anchor_request(
        self,
        *,
        root_policy: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
    ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest | None:
        self.bus.events.append("witness-request-consume")
        self.bus.request_policies.append(root_policy)
        if not self.bus.requests:
            return None
        return self.bus.requests.pop(0)


class _WitnessFiResponseOutbox:
    def __init__(self, bus: _RoleLocalMailbox) -> None:
        self.bus = bus

    def publish_witness_fi_v4_witness_anchor_response(
        self,
        *,
        root_policy: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
        response: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse,
    ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponsePublicationReceipt:
        self.bus.events.append("witness-response-publish")
        self.bus.response_policies.append(root_policy)
        if self.bus.fail_response_publication:
            raise RuntimeError("response outcome is ambiguous")
        key = (response.request_sha256, response.read_challenge)
        if key in self.bus.responses:
            raise RuntimeError("response object is already present")
        self.bus.responses[key] = response
        return self.bus._receipt(
            request_sha256=response.request_sha256,
            read_challenge=response.read_challenge,
            response=True,
        )


class _WaFiResponseInbox:
    def __init__(self, bus: _RoleLocalMailbox) -> None:
        self.bus = bus

    def consume_wa_fi_v4_witness_anchor_response(
        self,
        *,
        root_policy: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
        request_sha256: str,
        read_challenge: str,
    ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse:
        self.bus.events.append("wa-fi-response-consume")
        self.bus.response_policies.append(root_policy)
        if self.bus.fail_response_consume:
            raise RuntimeError("response outcome is ambiguous")
        key = (request_sha256, read_challenge)
        if key not in self.bus.responses:
            assert self.bus.dispatcher is not None
            self.bus.dispatcher.dispatch_one()
        response = self.bus.responses.get(key)
        if response is None:
            raise RuntimeError("exact response was not found")
        if self.bus.response_mutator is not None:
            return self.bus.response_mutator(response)
        return response


class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller_private = Ed25519PrivateKey.generate()
        self.witness_private = Ed25519PrivateKey.generate()
        self.clock = _Clock()
        self.baseline = (
            wire.derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=_normal_binding(),
            )
        )
        genesis = wire.build_physical_full_matrix_v4_witness_anchor_genesis(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=43,
            head_sha256=_hash("mailbox-pinned-genesis"),
            witness_private_key=self.witness_private,
        )
        self.policy = wire.build_physical_full_matrix_v4_witness_anchor_verification_policy(
            genesis=genesis,
            controller_public_key=self.controller_private.public_key().public_bytes(
                Encoding.Raw,
                PublicFormat.Raw,
            ),
            witness_public_key=self.witness_private.public_key().public_bytes(
                Encoding.Raw,
                PublicFormat.Raw,
            ),
        )
        self.identity = wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity(
            schema=wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA,
            journal_binding_sha256=genesis.journal_binding_sha256,
            baseline_plan_binding_sha256=genesis.baseline_plan_binding_sha256,
            run_id=genesis.run_id,
            plan_sha256=genesis.plan_sha256,
            anchor_genesis_sequence=genesis.sequence,
            anchor_genesis_head_sha256=genesis.head_sha256,
            canonical_genesis_sha256=hashlib.sha256(
                wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(genesis)
            ).hexdigest(),
        )
        self.root_policy = mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy(
            verification_policy=self.policy,
            policy_identity=self.identity,
            wa_fi_request_bucket_sha256=_hash("wa-fi-request-bucket"),
            wa_fi_response_bucket_sha256=_hash("wa-fi-response-bucket"),
            witness_immutable_record_bucket_sha256=_hash("witness-immutable-bucket"),
            wa_fi_request_outbox_iam_sha256=_hash("wa-fi-outbox-iam"),
            witness_fi_request_ingress_iam_sha256=_hash("witness-ingress-iam"),
            witness_fi_response_outbox_iam_sha256=_hash("witness-outbox-iam"),
            wa_fi_response_inbox_iam_sha256=_hash("wa-fi-inbox-iam"),
            request_object_lock_sha256=_hash("request-object-lock"),
            response_object_lock_sha256=_hash("response-object-lock"),
            immutable_record_object_lock_sha256=_hash("immutable-object-lock"),
        )
        self.events: list[str] = []
        self.wa_fi_anti_replay_registry = _RoleLocalAntiReplayRegistry(
            role=anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WA_FI_CONTROLLER,
            events=self.events,
        )
        self.witness_anti_replay_registry = _RoleLocalAntiReplayRegistry(
            role=anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WITNESS,
            events=self.events,
        )
        self.bus = _RoleLocalMailbox(events=self.events)
        self.service = _WitnessService(
            policy=self.policy,
            identity=self.identity,
            witness_private=self.witness_private,
            clock=self.clock,
            events=self.events,
        )
        self.outbox = _WaFiRequestOutbox(self.bus)
        self.ingress = _WitnessFiRequestIngress(self.bus)
        self.response_outbox = _WitnessFiResponseOutbox(self.bus)
        self.inbox = _WaFiResponseInbox(self.bus)
        self.dispatcher = mailbox.PhysicalFullMatrixV4WitnessAnchorWitnessFiMailboxDispatcher(
            config=mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxDispatcherConfig(
                root_policy=self.root_policy,
                clock=self.clock,
                witness_fi_request_ingress=self.ingress,
                witness_fi_response_outbox=self.response_outbox,
                witness_anchor_service=self.service,
                witness_anti_replay_registry=self.witness_anti_replay_registry,
                enabled=True,
            )
        )
        self.bus.dispatcher = self.dispatcher
        self.transport = mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransport(
            config=mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransportConfig(
                root_policy=self.root_policy,
                clock=self.clock,
                wa_fi_request_outbox=self.outbox,
                wa_fi_response_inbox=self.inbox,
                wa_fi_anti_replay_registry=self.wa_fi_anti_replay_registry,
                enabled=True,
            )
        )

    def _commitment(
        self,
        *,
        phase_sequence: int = 1,
        predecessor: _VerifiedAnchor | None = None,
    ) -> wire.PhysicalFullMatrixV4WitnessAnchorCommitment:
        prior = self.service.current if predecessor is None else predecessor
        return wire.build_physical_full_matrix_v4_witness_anchor_commitment(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            anchor_genesis_sequence=self.policy.genesis.sequence,
            anchor_genesis_head_sha256=self.policy.genesis.head_sha256,
            event="effect-started",
            phase_sequence=phase_sequence,
            phase_request_sha256=_hash(f"phase-request-{phase_sequence}"),
            effect_key=_hash(f"effect-{phase_sequence}"),
            claim_id=f"mailbox-claim-{phase_sequence:02d}-00000000000000000000",
            receipt_sha256=None,
            previous_anchor_sequence=prior.sequence,
            previous_anchor_head_sha256=prior.head_sha256,
            local_previous_record_sha256=_hash(f"local-previous-{phase_sequence}"),
            local_event_sha256=_hash(f"local-event-{phase_sequence}"),
            occurred_at=self.clock.now,
        )

    def _append_request(
        self,
        *,
        replay_label: str = "append-replay-1",
        lifetime_seconds: int = 30,
    ) -> bytes:
        return wire.build_physical_full_matrix_v4_witness_anchor_controller_append_request(
            policy=self.policy,
            predecessor=self.service.current,
            commitment=self._commitment(),
            replay_id=_hash(replay_label),
            issued_at=self.clock.now,
            expires_at=self.clock.now + timedelta(seconds=lifetime_seconds),
            controller_private_key=self.controller_private,
        )

    def _manual_append_wrapper(
        self,
        *,
        canonical_request: bytes,
        read_challenge: str,
    ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest:
        return mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest(
            schema="gold-trade-physical-full-matrix-v4-witness-anchor-fi-witness-mailbox-request-v1",
            operation="append-signed-request",
            policy_identity=self.identity,
            read_challenge=read_challenge,
            request_sha256=hashlib.sha256(canonical_request).hexdigest(),
            canonical_controller_append_request=canonical_request,
        )

    def _dispatcher_config(
        self,
    ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxDispatcherConfig:
        return mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxDispatcherConfig(
            root_policy=self.root_policy,
            clock=self.clock,
            witness_fi_request_ingress=self.ingress,
            witness_fi_response_outbox=self.response_outbox,
            witness_anchor_service=self.service,
            witness_anti_replay_registry=self.witness_anti_replay_registry,
            enabled=True,
        )

    def _transport_config(
        self,
        *,
        root_policy: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy
        | None = None,
    ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransportConfig:
        return mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransportConfig(
            root_policy=self.root_policy if root_policy is None else root_policy,
            clock=self.clock,
            wa_fi_request_outbox=self.outbox,
            wa_fi_response_inbox=self.inbox,
            wa_fi_anti_replay_registry=self.wa_fi_anti_replay_registry,
            enabled=True,
        )

    def test_default_off_root_pins_and_secret_bearers_are_nonserializable(self) -> None:
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "TRANSPORT_CONFIG_INVALID",
        ):
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransport(
                config=mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransportConfig()
            )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "DISPATCHER_CONFIG_INVALID",
        ):
            mailbox.PhysicalFullMatrixV4WitnessAnchorWitnessFiMailboxDispatcher(
                config=mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxDispatcherConfig()
            )
        with self.assertRaisesRegex(TypeError, "POLICY_NON_SERIALIZABLE"):
            pickle.dumps(self.root_policy)

        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "TRANSPORT_CONFIG_INVALID",
        ):
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransport(
                config=replace(self._transport_config(), wa_fi_anti_replay_registry=None)
            )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "DISPATCHER_CONFIG_INVALID",
        ):
            mailbox.PhysicalFullMatrixV4WitnessAnchorWitnessFiMailboxDispatcher(
                config=replace(self._dispatcher_config(), witness_anti_replay_registry=None)
            )

        for bad_policy in (
            replace(self.root_policy, controller_site="wa-other"),
            replace(self.root_policy, lane="other-lane"),
            replace(self.root_policy, wa_fi_request_prefix="other-prefix/"),
            replace(self.root_policy, wa_fi_request_outbox_iam_sha256="not-a-sha"),
            replace(self.root_policy, immutable_record_object_lock_sha256="not-a-sha"),
            replace(self.root_policy, policy_identity=replace(self.identity, plan_sha256=_hash("other"))),
        ):
            with self.assertRaisesRegex(
                mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
                "POLICY_(INVALID|IDENTITY_MISMATCH)",
            ):
                mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransport(
                    config=self._transport_config(root_policy=bad_policy)
                )

        root_fields = {item.name for item in fields(self.root_policy)}
        self.assertTrue(
            {
                "controller_site",
                "witness_site",
                "lane",
                "wa_fi_request_prefix",
                "witness_response_prefix",
                "witness_immutable_record_prefix",
                "wa_fi_request_outbox_iam_sha256",
                "witness_fi_request_ingress_iam_sha256",
                "witness_fi_response_outbox_iam_sha256",
                "wa_fi_response_inbox_iam_sha256",
                "request_object_lock_sha256",
                "response_object_lock_sha256",
                "immutable_record_object_lock_sha256",
            }.issubset(root_fields)
        )

    def test_read_round_trip_preserves_immutable_and_fresh_observation_layers(self) -> None:
        challenge = _hash("read-round-trip")
        envelope = self.transport.read_signed_head(
            policy_identity=self.identity,
            read_challenge=challenge,
        )
        verified = wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            envelope,
            policy=self.policy,
            now=self.clock.now,
            expected_read_challenge=challenge,
        )
        self.assertIsInstance(
            verified.anchor_head,
            wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead,
        )
        self.assertEqual(self.policy.genesis.sequence, verified.anchor_head.sequence)
        self.assertEqual(challenge, verified.read_observation.read_challenge)
        self.assertEqual([challenge], self.service.read_challenges)
        self.assertGreaterEqual(len(self.bus.request_policies), 2)
        self.assertGreaterEqual(len(self.bus.response_policies), 2)
        self.assertTrue(
            all(item == self.root_policy for item in self.bus.request_policies)
        )
        self.assertTrue(
            all(item == self.root_policy for item in self.bus.response_policies)
        )

    def test_durable_reservations_are_ordered_around_each_callback_boundary(self) -> None:
        """The two roles reserve independently; no in-memory set is trusted."""

        original_response = mailbox._response

        def traced_response(*args: object, **kwargs: object) -> tuple[
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse,
            str,
        ]:
            result = original_response(*args, **kwargs)  # type: ignore[arg-type]
            self.events.append("response-verified")
            return result

        read_challenge = _hash("durable-reservation-read")
        with mock.patch.object(mailbox, "_response", side_effect=traced_response):
            self.transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=read_challenge,
            )

        def first(label: str) -> int:
            return self.events.index(label)

        def later(label: str, after: int) -> int:
            return self.events.index(label, after + 1)

        wa_reserve_challenge = first("wa-fi-controller:reserve:read-challenge")
        wa_publish = first("wa-fi-request-publish")
        witness_reserve_challenge = first("witness:reserve:read-challenge")
        witness_service = first("witness-service-read")
        first_verified = first("response-verified")
        witness_reserve_observation = first(
            "witness:reserve:witness-observation-id"
        )
        witness_publish = first("witness-response-publish")
        wa_consume = first("wa-fi-response-consume")
        second_verified = later("response-verified", first_verified)
        wa_reserve_observation = first("wa-fi-controller:reserve:witness-observation-id")

        self.assertLess(wa_reserve_challenge, wa_publish)
        self.assertLess(witness_reserve_challenge, witness_service)
        self.assertLess(first_verified, witness_reserve_observation)
        self.assertLess(witness_reserve_observation, witness_publish)
        self.assertLess(wa_consume, second_verified)
        self.assertLess(second_verified, wa_reserve_observation)

        self.events.clear()
        canonical_request = self._append_request(replay_label="durable-reservation-append")
        self.transport.append_signed_request(
            policy_identity=self.identity,
            canonical_controller_append_request=canonical_request,
            read_challenge=_hash("durable-reservation-append-challenge"),
        )
        self.assertLess(
            first("wa-fi-controller:reserve:read-challenge"),
            first("wa-fi-controller:reserve:controller-replay-id"),
        )
        self.assertLess(
            first("wa-fi-controller:reserve:controller-replay-id"),
            first("wa-fi-request-publish"),
        )
        self.assertLess(
            first("witness:reserve:read-challenge"),
            first("witness:reserve:controller-replay-id"),
        )
        self.assertLess(
            first("witness:reserve:controller-replay-id"),
            first("witness-service-append"),
        )

    def test_registry_failure_before_boundary_denies_service_publish_and_retry(self) -> None:
        self.wa_fi_anti_replay_registry.fail_after_reservation = True
        challenge = _hash("wa-fi-registry-failure")
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=challenge,
            )
        self.assertNotIn("wa-fi-request-publish", self.events)
        self.assertFalse(self.service.read_challenges)
        self.assertNotIn("witness-response-publish", self.events)

        self.wa_fi_anti_replay_registry.fail_after_reservation = False
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=challenge,
            )
        self.assertNotIn("wa-fi-request-publish", self.events)

        self.events.clear()
        witness_challenge = _hash("witness-registry-failure")
        self.witness_anti_replay_registry.fail_after_reservation = True
        self.bus.requests.append(
            self._manual_append_wrapper(
                canonical_request=self._append_request(
                    replay_label="witness-registry-failure-replay"
                ),
                read_challenge=witness_challenge,
            )
        )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.dispatcher.dispatch_one()
        self.assertNotIn("witness-service-append", self.events)
        self.assertNotIn("witness-response-publish", self.events)

        self.witness_anti_replay_registry.fail_after_reservation = False
        self.bus.requests.append(
            self._manual_append_wrapper(
                canonical_request=self._append_request(
                    replay_label="witness-registry-failure-replay"
                ),
                read_challenge=witness_challenge,
            )
        )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.dispatcher.dispatch_one()
        self.assertNotIn("witness-service-append", self.events)

    def test_observation_reservation_follows_verification_and_blocks_ambiguous_publish_or_return(self) -> None:
        original_response = mailbox._response

        def traced_response(*args: object, **kwargs: object) -> tuple[
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse,
            str,
        ]:
            result = original_response(*args, **kwargs)  # type: ignore[arg-type]
            self.events.append("response-verified")
            return result

        self.witness_anti_replay_registry.fail_after_reservation_kinds.add(
            anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID
        )
        with mock.patch.object(mailbox, "_response", side_effect=traced_response):
            with self.assertRaisesRegex(
                mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
                "ANTI_REPLAY_RESERVATION_FAILED",
            ):
                self.transport.read_signed_head(
                    policy_identity=self.identity,
                    read_challenge=_hash("witness-observation-reservation-failure"),
                )
        self.assertLess(
            self.events.index("response-verified"),
            self.events.index("witness:reserve:witness-observation-id"),
        )
        self.assertNotIn("witness-response-publish", self.events)

        # A separate fresh chain shows the WA-FI reservation is also after
        # verified response evidence and prevents returning an envelope.
        self.witness_anti_replay_registry.fail_after_reservation_kinds.clear()
        self.events.clear()
        self.wa_fi_anti_replay_registry.fail_after_reservation_kinds.add(
            anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID
        )
        with mock.patch.object(mailbox, "_response", side_effect=traced_response):
            with self.assertRaisesRegex(
                mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
                "ANTI_REPLAY_RESERVATION_FAILED",
            ):
                self.transport.read_signed_head(
                    policy_identity=self.identity,
                    read_challenge=_hash("wa-fi-observation-reservation-failure"),
                )
        verified_positions = [
            index
            for index, event in enumerate(self.events)
            if event == "response-verified"
        ]
        self.assertEqual(2, len(verified_positions))
        self.assertLess(
            verified_positions[-1],
            self.events.index("wa-fi-controller:reserve:witness-observation-id"),
        )
        self.assertIn("witness-response-publish", self.events)

    def test_rejects_cross_role_registry_receipt_before_request_callback(self) -> None:
        transport = mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransport(
            config=replace(
                self._transport_config(),
                wa_fi_anti_replay_registry=self.witness_anti_replay_registry,
            )
        )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RECEIPT_INVALID",
        ):
            transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=_hash("cross-role-registry-receipt"),
            )
        self.assertNotIn("wa-fi-request-publish", self.events)

    def test_reviewed_root_local_registry_receipts_are_accepted_end_to_end(self) -> None:
        """Exercise the real reviewed registry, not only a role-local fake."""

        class _Checkpoint:
            def attest_v4_fi_witness_anti_replay_state(self, **_kwargs: object) -> None:
                return None

        with tempfile.TemporaryDirectory(
            prefix="v4-mailbox-real-anti-replay-",
            dir=Path(__file__).resolve().parents[1],
        ) as temporary:
            state_root = Path(temporary) / "state"
            state_root.mkdir(mode=0o700)
            old_root = (
                anti_replay.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT
            )
            anti_replay.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT = state_root
            try:
                checkpoint = _Checkpoint()
                wa_fi_registry = (
                    anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry(
                        anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryConfig(
                            enabled=True,
                            role=(
                                anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WA_FI_CONTROLLER
                            ),
                            policy_identity=self.identity,
                        ),
                        rollback_checkpoint=checkpoint,
                    )
                )
                witness_registry = (
                    anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry(
                        anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryConfig(
                            enabled=True,
                            role=(
                                anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WITNESS
                            ),
                            policy_identity=self.identity,
                        ),
                        rollback_checkpoint=checkpoint,
                    )
                )
                dispatcher = (
                    mailbox.PhysicalFullMatrixV4WitnessAnchorWitnessFiMailboxDispatcher(
                        config=replace(
                            self._dispatcher_config(),
                            witness_anti_replay_registry=witness_registry,
                        )
                    )
                )
                self.bus.dispatcher = dispatcher
                transport = mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransport(
                    config=replace(
                        self._transport_config(),
                        wa_fi_anti_replay_registry=wa_fi_registry,
                    )
                )
                envelope = transport.read_signed_head(
                    policy_identity=self.identity,
                    read_challenge=_hash("real-registry-read"),
                )
                self.assertTrue(envelope)
                self.assertEqual(
                    2,
                    len(list((state_root / "wa-fi-controller" / "reservations").glob("*.json"))),
                )
                self.assertEqual(
                    2,
                    len(list((state_root / "witness" / "reservations").glob("*.json"))),
                )
            finally:
                anti_replay.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT = old_root

    def test_append_is_exactly_bound_and_signed_replay_survives_fresh_outer_wrapper(self) -> None:
        canonical_request = self._append_request()
        challenge = _hash("append-round-trip")
        envelope = self.transport.append_signed_request(
            policy_identity=self.identity,
            canonical_controller_append_request=canonical_request,
            read_challenge=challenge,
        )
        verified = wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            envelope,
            policy=self.policy,
            now=self.clock.now,
            expected_read_challenge=challenge,
        )
        self.assertIsInstance(
            verified.anchor_head,
            wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead,
        )
        self.assertEqual(
            hashlib.sha256(canonical_request).hexdigest(),
            verified.anchor_head.controller_request_sha256,
        )
        self.assertEqual(1, len(self.service.seen_replay_ids))

        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.transport.append_signed_request(
                policy_identity=self.identity,
                canonical_controller_append_request=canonical_request,
                read_challenge=_hash("transport-fresh-wrapper-challenge"),
            )

        # A restarted dispatcher has no process-local replay state.  The same
        # signed append with a fresh outer challenge still reaches the actual
        # V4 service ledger and is rejected there, rather than being accepted
        # as a new mailbox operation.
        self.bus.requests.append(
            self._manual_append_wrapper(
                canonical_request=canonical_request,
                read_challenge=_hash("restarted-dispatcher-fresh-challenge"),
            )
        )
        restarted = mailbox.PhysicalFullMatrixV4WitnessAnchorWitnessFiMailboxDispatcher(
            config=self._dispatcher_config()
        )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            restarted.dispatch_one()

    def test_dispatcher_rejects_callback_identity_substitution_before_service(self) -> None:
        forged = mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest(
            schema="gold-trade-physical-full-matrix-v4-witness-anchor-fi-witness-mailbox-request-v1",
            operation="read-signed-head",
            policy_identity=replace(self.identity, plan_sha256=_hash("wrong-plan")),
            read_challenge=_hash("forged-identity-challenge"),
            request_sha256=_hash("irrelevant"),
        )
        self.bus.requests.append(forged)
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "REQUEST_IDENTITY_MISMATCH",
        ):
            self.dispatcher.dispatch_one()
        self.assertFalse(self.service.read_challenges)
        self.assertFalse(self.service.append_requests)

    def test_typed_callback_cannot_substitute_challenge_or_signed_envelope(self) -> None:
        challenge = _hash("callback-challenge-substitution")

        def wrong_challenge(
            response: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse,
        ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse:
            replacement = _hash("callback-replacement-challenge")
            return replace(
                response,
                read_challenge=replacement,
                response_sha256=mailbox._response_digest(
                    operation=response.operation,
                    identity=response.policy_identity,
                    read_challenge=replacement,
                    request_sha256=response.request_sha256,
                    canonical_transport_envelope=response.canonical_transport_envelope,
                ),
            )

        self.bus.response_mutator = wrong_challenge
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "RESPONSE_CORRELATION_MISMATCH",
        ):
            self.transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=challenge,
            )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=challenge,
            )

    def test_typed_callback_cannot_substitute_response_identity(self) -> None:
        wrong_identity = replace(self.identity, plan_sha256=_hash("response-wrong-plan"))

        def wrong_identity_response(
            response: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse,
        ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse:
            return replace(
                response,
                policy_identity=wrong_identity,
                response_sha256=mailbox._response_digest(
                    operation=response.operation,
                    identity=wrong_identity,
                    read_challenge=response.read_challenge,
                    request_sha256=response.request_sha256,
                    canonical_transport_envelope=response.canonical_transport_envelope,
                ),
            )

        self.bus.response_mutator = wrong_identity_response
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "RESPONSE_CORRELATION_MISMATCH",
        ):
            self.transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=_hash("response-identity-substitution"),
            )

    def test_valid_but_unrelated_append_envelope_is_rejected(self) -> None:
        canonical_request = self._append_request()
        previous_anchor = self.service.current
        previous_raw = self.service.current_raw

        def unrelated_envelope(
            response: mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse,
        ) -> mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse:
            envelope = self.service._response(
                read_challenge=response.read_challenge,
                anchor=previous_anchor,
                canonical_anchor=previous_raw,
            )
            return replace(
                response,
                canonical_transport_envelope=envelope,
                response_sha256=mailbox._response_digest(
                    operation=response.operation,
                    identity=response.policy_identity,
                    read_challenge=response.read_challenge,
                    request_sha256=response.request_sha256,
                    canonical_transport_envelope=envelope,
                ),
            )

        self.bus.response_mutator = unrelated_envelope
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "RESPONSE_CORRELATION_MISMATCH",
        ):
            self.transport.append_signed_request(
                policy_identity=self.identity,
                canonical_controller_append_request=canonical_request,
                read_challenge=_hash("unrelated-valid-envelope"),
            )

    def test_ambiguous_publication_or_consume_burns_values_before_retry(self) -> None:
        canonical_request = self._append_request()
        challenge = _hash("ambiguous-append-publication")
        self.bus.fail_request_publication = True
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "REQUEST_PUBLICATION_FAILED",
        ):
            self.transport.append_signed_request(
                policy_identity=self.identity,
                canonical_controller_append_request=canonical_request,
                read_challenge=challenge,
            )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.transport.append_signed_request(
                policy_identity=self.identity,
                canonical_controller_append_request=canonical_request,
                read_challenge=challenge,
            )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.transport.append_signed_request(
                policy_identity=self.identity,
                canonical_controller_append_request=canonical_request,
                read_challenge=_hash("ambiguous-append-new-challenge"),
            )

        self.bus.fail_request_publication = False
        self.bus.fail_response_consume = True
        read_challenge = _hash("ambiguous-read-consume")
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "AMBIGUOUS_RESPONSE",
        ):
            self.transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=read_challenge,
            )
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.transport.read_signed_head(
                policy_identity=self.identity,
                read_challenge=read_challenge,
            )

    def test_dispatcher_response_publication_ambiguity_burns_request(self) -> None:
        challenge = _hash("dispatcher-response-publication")
        request = self._manual_append_wrapper(
            canonical_request=self._append_request(),
            read_challenge=challenge,
        )
        self.bus.fail_response_publication = True
        self.bus.requests.append(request)
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "RESPONSE_PUBLICATION_FAILED",
        ):
            self.dispatcher.dispatch_one()
        self.bus.fail_response_publication = False
        self.bus.requests.append(request)
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "ANTI_REPLAY_RESERVATION_FAILED",
        ):
            self.dispatcher.dispatch_one()

    def test_expired_signed_append_never_reaches_a_role_callback(self) -> None:
        request = self._append_request(lifetime_seconds=1)
        self.clock.now += timedelta(seconds=2)
        with self.assertRaisesRegex(
            mailbox.PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError,
            "REQUEST_EXPIRED",
        ):
            self.transport.append_signed_request(
                policy_identity=self.identity,
                canonical_controller_append_request=request,
                read_challenge=_hash("expired-append"),
            )
        self.assertFalse(self.bus.requests)
        self.assertFalse(self.service.append_requests)

    def test_static_boundary_is_v4_only_non_live_and_has_no_ir_lane(self) -> None:
        source = MAILBOX_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports: list[tuple[str, tuple[str, ...]]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend((item.name, ()) for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append((node.module, tuple(item.name for item in node.names)))
        forbidden_prefixes = (
            "boto",
            "botocore",
            "http",
            "paramiko",
            "pathlib",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        )
        self.assertFalse(
            [
                module
                for module, _names in imports
                if module.startswith(forbidden_prefixes)
            ],
            imports,
        )
        core_imports = {
            name
            for module, names in imports
            if module == "core"
            for name in names
        }
        self.assertEqual(
            {
                "physical_full_matrix_v4_witness_anchor_fi_witness_anti_replay_registry",
                "physical_full_matrix_v4_witness_anchor_wire",
            },
            core_imports,
        )
        self.assertNotIn("physical_wal_v2", source)
        self.assertNotIn("witness_roundtrip", source)
        self.assertNotIn("wa-ir", source.lower())
        self.assertNotIn("production_deploy", source)
        self.assertNotIn("_seen_read_challenges", source)
        self.assertNotIn("_seen_replay_ids", source)
        self.assertNotIn("_seen_observation_ids", source)
        self.assertFalse(
            [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"open", "exec", "eval"}
            ]
        )
