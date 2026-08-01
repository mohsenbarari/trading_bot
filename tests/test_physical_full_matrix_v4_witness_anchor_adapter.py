"""Boundary tests for the signed-Wire V2 journal-anchor adapter.

The fake below deliberately behaves like the narrow root-local Witness
service: it never returns an unattested head.  Each response is an immutable
anchor record paired with a newly signed, controller-challenge-bound read
observation.  This exercises the real Wire grammar at the adapter boundary
without turning the test into a transport implementation.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import pickle
import unittest
from unittest import mock
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core import physical_full_matrix_v4_receipt_journal as journal
from core import physical_full_matrix_v4_witness_anchor_adapter as adapter
from core import physical_full_matrix_v4_witness_anchor_wire as wire


NOW = datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)
RUN_ID = UUID("2b64f286-e6b0-45bd-9f3d-0bd90f4d72c9")
PLAN_SHA256 = "e" * 64
JOURNAL_BINDING = hashlib.sha256(b"v4-adapter-journal-binding").hexdigest()
ADAPTER_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_witness_anchor_adapter.py"
)
JOURNAL_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_receipt_journal.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _normal_binding() -> dict[str, object]:
    return {
        "campaign_id": "physical-full-matrix-v4-adapter-20260731",
        "release_sha": "f" * 40,
        "readiness_binding_sha256": _hash("readiness"),
        "route_commitment_sha256": _hash("route"),
        "four_role_binding_sha256": _hash("four-role"),
        "writer_holder_site": "webapp_fi",
        "writer_epoch": 11,
        "writer_lease_id": "writer-lease-v4-adapter-000001",
        "witnessed_term_proof_sha256": _hash("term"),
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "roundtrip_attestation_sha256": _hash("roundtrip"),
        "roundtrip_configuration_sha256": _hash("configuration"),
        "witness_transition_id": "witness-transition-v4-adapter-000001",
        "witness_sequence": 37,
    }


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now


class _ReplayIds:
    def __init__(self, values: list[str]) -> None:
        self.values = list(values)
        self.identities: list[adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity] = []

    def next_controller_append_replay_id(
        self,
        *,
        policy_identity: adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> str:
        self.identities.append(policy_identity)
        if not self.values:
            raise RuntimeError("no replay id")
        return self.values.pop(0)


class _ReadChallenges:
    def __init__(self, values: list[str]) -> None:
        self.values = list(values)
        self.identities: list[adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity] = []

    def next_witness_read_challenge(
        self,
        *,
        policy_identity: adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> str:
        self.identities.append(policy_identity)
        if not self.values:
            raise RuntimeError("no read challenge")
        return self.values.pop(0)


_VerifiedAnchor = (
    wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead
    | wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
)


class _SignedWireTransport:
    """Memory-only fake of the future narrow Witness-local signed service."""

    def __init__(
        self,
        *,
        policy: wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
        witness_private: Ed25519PrivateKey,
        clock: _Clock,
    ) -> None:
        self.policy = policy
        self.witness_private = witness_private
        self.clock = clock
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
        self.identities: list[adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity] = []
        self.read_challenges: list[str] = []
        self.requests: list[bytes] = []
        self.observation_ordinal = 0
        self.fixed_observation_id: str | None = None
        self.response_override: bytes | None = None
        self.response_anchor_raw_override: bytes | None = None
        self.last_response: bytes | None = None
        self.advance_before_request_verify: datetime | None = None

    def _check_identity(
        self,
        value: adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> None:
        genesis = self.policy.genesis
        if (
            value.schema
            != adapter.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_IDENTITY_SCHEMA
            or value.journal_binding_sha256 != genesis.journal_binding_sha256
            or value.baseline_plan_binding_sha256 != genesis.baseline_plan_binding_sha256
            or value.run_id != genesis.run_id
            or value.plan_sha256 != genesis.plan_sha256
            or value.anchor_genesis_sequence != genesis.sequence
            or value.anchor_genesis_head_sha256 != genesis.head_sha256
            or value.canonical_genesis_sha256
            != hashlib.sha256(
                wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
                    genesis
                )
            ).hexdigest()
        ):
            raise RuntimeError("wrong identity")

    def _response(self, *, read_challenge: str) -> bytes:
        self.read_challenges.append(read_challenge)
        if self.response_override is not None:
            return self.response_override
        self.observation_ordinal += 1
        observation_id = (
            self.fixed_observation_id
            if self.fixed_observation_id is not None
            else _hash(f"adapter-observation-{self.observation_ordinal}")
        )
        observation = wire.build_physical_full_matrix_v4_witness_anchor_read_observation(
            policy=self.policy,
            anchor_head=self.current,
            read_challenge=read_challenge,
            observation_id=observation_id,
            observed_at=self.clock.now,
            expires_at=self.clock.now + timedelta(seconds=30),
            witness_private_key=self.witness_private,
        )
        response = wire.build_physical_full_matrix_v4_witness_anchor_transport_envelope(
            canonical_anchor_head=(
                self.current_raw
                if self.response_anchor_raw_override is None
                else self.response_anchor_raw_override
            ),
            canonical_read_observation=observation,
            read_challenge=read_challenge,
        )
        self.last_response = response
        return response

    def read_signed_head(
        self,
        *,
        policy_identity: adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        read_challenge: str,
    ) -> bytes:
        self._check_identity(policy_identity)
        self.identities.append(policy_identity)
        return self._response(read_challenge=read_challenge)

    def append_signed_request(
        self,
        *,
        policy_identity: adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        canonical_controller_append_request: bytes,
        read_challenge: str,
    ) -> bytes:
        self._check_identity(policy_identity)
        self.identities.append(policy_identity)
        self.requests.append(canonical_controller_append_request)
        if self.advance_before_request_verify is not None:
            self.clock.now = self.advance_before_request_verify
        request = wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
            canonical_controller_append_request,
            policy=self.policy,
            predecessor=self.current,
            now=self.clock.now,
            seen_replay_ids=self.seen_replay_ids,
        )
        self.seen_replay_ids.add(request.replay_id)
        immutable = wire.build_physical_full_matrix_v4_witness_anchor_immutable_head(
            policy=self.policy,
            predecessor=self.current,
            append_request=request,
            now=self.clock.now,
            witness_private_key=self.witness_private,
        )
        prior = self.current
        self.current = wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
            immutable,
            policy=self.policy,
            now=self.clock.now,
            expected_predecessor=prior,
            append_request=request,
        )
        self.current_raw = immutable
        return self._response(read_challenge=read_challenge)


class PhysicalFullMatrixV4WitnessAnchorAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller_private = Ed25519PrivateKey.generate()
        self.witness_private = Ed25519PrivateKey.generate()
        controller_public = self.controller_private.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        witness_public = self.witness_private.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        self.clock = _Clock()
        self.baseline = (
            wire.derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=_normal_binding(),
            )
        )
        self.genesis = wire.build_physical_full_matrix_v4_witness_anchor_genesis(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=29,
            head_sha256=_hash("adapter-pinned-genesis"),
            witness_private_key=self.witness_private,
        )
        self.policy = wire.build_physical_full_matrix_v4_witness_anchor_verification_policy(
            genesis=self.genesis,
            controller_public_key=controller_public,
            witness_public_key=witness_public,
        )
        self.transport = _SignedWireTransport(
            policy=self.policy,
            witness_private=self.witness_private,
            clock=self.clock,
        )

    def _adapter(
        self,
        *,
        replay_ids: list[str] | None = None,
        read_challenges: list[str] | None = None,
        request_lifetime_seconds: int = 30,
        transport: _SignedWireTransport | None = None,
        clock: _Clock | None = None,
    ) -> tuple[
        adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter,
        _ReplayIds,
        _ReadChallenges,
    ]:
        active_clock = self.clock if clock is None else clock
        active_transport = self.transport if transport is None else transport
        ids = _ReplayIds(
            [_hash("adapter-replay-1"), _hash("adapter-replay-2")]
            if replay_ids is None
            else replay_ids
        )
        challenges = _ReadChallenges(
            [_hash(f"adapter-read-challenge-{index}") for index in range(1, 12)]
            if read_challenges is None
            else read_challenges
        )
        return (
            adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter(
                config=adapter.PhysicalFullMatrixV4WitnessAnchorAdapterConfig(
                    policy=self.policy,
                    controller_private_key=self.controller_private,
                    transport=active_transport,
                    clock=active_clock,
                    replay_id_source=ids,
                    read_challenge_source=challenges,
                    request_lifetime_seconds=request_lifetime_seconds,
                )
            ),
            ids,
            challenges,
        )

    def _commitment(
        self,
        *,
        predecessor: _VerifiedAnchor | None = None,
        event: str = "effect-started",
        phase_sequence: int = 1,
    ) -> journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment:
        prior = self.transport.current if predecessor is None else predecessor
        return journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment(
            schema=journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_COMMITMENT_SCHEMA,
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            anchor_genesis_sequence=self.genesis.sequence,
            anchor_genesis_head_sha256=self.genesis.head_sha256,
            event=event,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            phase_sequence=phase_sequence,
            phase_request_sha256=_hash(f"phase-request-{phase_sequence}"),
            effect_key=_hash(f"effect-{phase_sequence}"),
            claim_id=f"adapter-claim-{phase_sequence:02d}-00000000000000000000",
            previous_anchor_sequence=prior.sequence,
            previous_anchor_head_sha256=prior.head_sha256,
            local_previous_record_sha256=_hash(f"local-previous-{phase_sequence}"),
            local_event_sha256=_hash(f"local-event-{event}-{phase_sequence}"),
            receipt_sha256=None
            if event == "effect-started"
            else _hash(f"receipt-{phase_sequence}"),
            occurred_at=self.clock.now,
        )

    def _read(
        self,
        value: adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter,
        *,
        expected: _VerifiedAnchor | None = None,
    ) -> journal.PhysicalFullMatrixV4WitnessJournalAnchorHead:
        durable = self.transport.current if expected is None else expected
        return value.read_head(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            expected_anchor_sequence=durable.sequence,
            expected_anchor_head_sha256=durable.head_sha256,
        )

    def test_exact_signed_genesis_alone_maps_to_the_journal_zero_sentinel(self) -> None:
        value, ids, challenges = self._adapter()
        head = self._read(value)
        self.assertEqual(self.genesis.sequence, head.sequence)
        self.assertEqual(self.genesis.head_sha256, head.head_sha256)
        self.assertEqual("0" * 64, head.previous_head_sha256)
        self.assertEqual("0" * 64, head.commitment_sha256)
        self.assertEqual("0" * 64, head.attestation_sha256)
        self.assertIsNone(head.commitment)
        self.assertEqual(value.policy_identity, self.transport.identities[-1])
        self.assertEqual(value.policy_identity, challenges.identities[-1])
        self.assertFalse(ids.identities)

        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "FRESH_HEAD_REQUIRED",
        ):
            self._adapter()[0].append_commitment(commitment=self._commitment())

    def test_controller_key_is_policy_matched_and_secret_bearers_are_root_only_nonserializable(self) -> None:
        ids = _ReplayIds([_hash("unused")])
        challenges = _ReadChallenges([_hash("unused-read-challenge")])
        config = adapter.PhysicalFullMatrixV4WitnessAnchorAdapterConfig(
            policy=self.policy,
            controller_private_key=self.controller_private,
            transport=self.transport,
            clock=self.clock,
            replay_id_source=ids,
            read_challenge_source=challenges,
        )
        with self.assertRaisesRegex(TypeError, "CONFIG_NON_SERIALIZABLE"):
            pickle.dumps(config)

        with mock.patch.object(adapter.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
                "ROOT_RUNTIME_REQUIRED",
            ):
                adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter(config=config)
        self.assertFalse(self.transport.identities)

        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "CONTROLLER_SIGNER_MISMATCH",
        ):
            adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter(
                config=replace(
                    config,
                    controller_private_key=Ed25519PrivateKey.generate(),
                )
            )
        self.assertFalse(self.transport.identities)

        value = adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter(config=config)
        with self.assertRaisesRegex(TypeError, "ADAPTER_NON_SERIALIZABLE"):
            pickle.dumps(value)

    def test_nonconfigured_genesis_never_reaches_zero_projection(self) -> None:
        other = wire.build_physical_full_matrix_v4_witness_anchor_genesis(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=self.genesis.sequence,
            head_sha256=_hash("other-signed-genesis"),
            witness_private_key=self.witness_private,
        )
        self.transport.response_anchor_raw_override = (
            wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(other)
        )
        value, _, _ = self._adapter()
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "READ_ENVELOPE_INVALID",
        ):
            self._read(value)

    def test_append_maps_exact_wire_commitment_and_stable_immutable_attestation(self) -> None:
        value, ids, challenges = self._adapter()
        root = self._read(value)
        commitment = self._commitment()
        receipt = value.append_commitment(commitment=commitment)
        expected_wire = wire.build_physical_full_matrix_v4_witness_anchor_commitment(
            journal_binding_sha256=commitment.journal_binding_sha256,
            baseline_plan_binding_sha256=commitment.baseline_plan_binding_sha256,
            run_id=commitment.run_id,
            plan_sha256=commitment.plan_sha256,
            anchor_genesis_sequence=commitment.anchor_genesis_sequence,
            anchor_genesis_head_sha256=commitment.anchor_genesis_head_sha256,
            event=commitment.event,
            phase_sequence=commitment.phase_sequence,
            phase_request_sha256=commitment.phase_request_sha256,
            effect_key=commitment.effect_key,
            claim_id=commitment.claim_id,
            receipt_sha256=commitment.receipt_sha256,
            previous_anchor_sequence=commitment.previous_anchor_sequence,
            previous_anchor_head_sha256=commitment.previous_anchor_head_sha256,
            local_previous_record_sha256=commitment.local_previous_record_sha256,
            local_event_sha256=commitment.local_event_sha256,
            occurred_at=commitment.occurred_at,
        )
        self.assertEqual(
            wire.derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(
                expected_wire
            ),
            receipt.commitment_sha256,
        )
        self.assertEqual(root.sequence + 1, receipt.sequence)
        self.assertEqual(root.head_sha256, receipt.previous_head_sha256)
        self.assertEqual(value.policy_identity, ids.identities[-1])
        self.assertEqual(value.policy_identity, challenges.identities[-1])

        head = self._read(value)
        self.assertEqual(receipt.sequence, head.sequence)
        self.assertEqual(receipt.commitment_sha256, head.commitment_sha256)
        self.assertEqual(receipt.attestation_sha256, head.attestation_sha256)
        self.assertNotEqual("0" * 64, head.previous_head_sha256)
        self.assertNotEqual("0" * 64, head.commitment_sha256)
        self.assertNotEqual("0" * 64, head.attestation_sha256)
        self.assertEqual(commitment, head.commitment)

    def test_each_observation_is_challenge_bound_one_use_and_replay_checked(self) -> None:
        first = _hash("read-challenge-first")
        value, _, _ = self._adapter(read_challenges=[first, first])
        self._read(value)
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "READ_CHALLENGE_INVALID",
        ):
            self._read(value)
        self.assertEqual([first], self.transport.read_challenges)

        value, _, _ = self._adapter(
            read_challenges=[_hash("wrong-envelope-1"), _hash("wrong-envelope-2")]
        )
        self._read(value)
        old_envelope = self.transport.last_response
        assert old_envelope is not None
        self.transport.response_override = old_envelope
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "READ_ENVELOPE_INVALID",
        ):
            self._read(value)
        self.transport.response_override = None

        fixed_observation = _hash("reused-observation-id")
        self.transport.fixed_observation_id = fixed_observation
        value, _, _ = self._adapter(
            read_challenges=[_hash("observation-a"), _hash("observation-b")]
        )
        self._read(value)
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "READ_ENVELOPE_INVALID",
        ):
            self._read(value)

    def test_expected_durable_tail_rejects_stale_genesis_and_gap(self) -> None:
        value, _, _ = self._adapter()
        self._read(value)
        receipt = value.append_commitment(commitment=self._commitment())
        self.assertEqual(self.transport.current.head_sha256, receipt.head_sha256)
        durable = self.transport.current
        self.transport.response_anchor_raw_override = (
            wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(self.genesis)
        )
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "READ_ENVELOPE_INVALID",
        ):
            self._read(value, expected=durable)

        self.transport.response_anchor_raw_override = None
        # Build a legitimate two-step remote extension, then ask the adapter
        # to read it while its durable journal tail is still one step behind.
        remote_request = wire.build_physical_full_matrix_v4_witness_anchor_controller_append_request(
            policy=self.policy,
            predecessor=self.transport.current,
            commitment=wire.build_physical_full_matrix_v4_witness_anchor_commitment(
                journal_binding_sha256=JOURNAL_BINDING,
                baseline_plan_binding_sha256=self.baseline,
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                anchor_genesis_sequence=self.genesis.sequence,
                anchor_genesis_head_sha256=self.genesis.head_sha256,
                event="effect-started",
                phase_sequence=2,
                phase_request_sha256=_hash("remote-gap-request"),
                effect_key=_hash("remote-gap-effect"),
                claim_id="adapter-remote-gap-00000000000000000000",
                receipt_sha256=None,
                previous_anchor_sequence=self.transport.current.sequence,
                previous_anchor_head_sha256=self.transport.current.head_sha256,
                local_previous_record_sha256=_hash("remote-gap-local-prev"),
                local_event_sha256=_hash("remote-gap-local-event"),
                occurred_at=self.clock.now,
            ),
            replay_id=_hash("remote-gap-replay"),
            issued_at=self.clock.now,
            expires_at=self.clock.now + timedelta(seconds=30),
            controller_private_key=self.controller_private,
        )
        verified_remote_request = wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
            remote_request,
            policy=self.policy,
            predecessor=self.transport.current,
            now=self.clock.now,
        )
        remote_raw = wire.build_physical_full_matrix_v4_witness_anchor_immutable_head(
            policy=self.policy,
            predecessor=self.transport.current,
            append_request=verified_remote_request,
            now=self.clock.now,
            witness_private_key=self.witness_private,
        )
        remote = wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
            remote_raw,
            policy=self.policy,
            now=self.clock.now,
            expected_predecessor=self.transport.current,
            append_request=verified_remote_request,
        )
        # One successor is legal for a journal crash window; force a second
        # successor so the exact-or-immediate Wire rule must refuse it.
        self.transport.current = remote
        self.transport.current_raw = remote_raw
        second_request = wire.build_physical_full_matrix_v4_witness_anchor_controller_append_request(
            policy=self.policy,
            predecessor=remote,
            commitment=wire.build_physical_full_matrix_v4_witness_anchor_commitment(
                journal_binding_sha256=JOURNAL_BINDING,
                baseline_plan_binding_sha256=self.baseline,
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                anchor_genesis_sequence=self.genesis.sequence,
                anchor_genesis_head_sha256=self.genesis.head_sha256,
                event="effect-started",
                phase_sequence=3,
                phase_request_sha256=_hash("remote-gap-request-2"),
                effect_key=_hash("remote-gap-effect-2"),
                claim_id="adapter-remote-gap-00000000000000000001",
                receipt_sha256=None,
                previous_anchor_sequence=remote.sequence,
                previous_anchor_head_sha256=remote.head_sha256,
                local_previous_record_sha256=_hash("remote-gap-local-prev-2"),
                local_event_sha256=_hash("remote-gap-local-event-2"),
                occurred_at=self.clock.now,
            ),
            replay_id=_hash("remote-gap-replay-2"),
            issued_at=self.clock.now,
            expires_at=self.clock.now + timedelta(seconds=30),
            controller_private_key=self.controller_private,
        )
        verified_second_request = wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
            second_request,
            policy=self.policy,
            predecessor=remote,
            now=self.clock.now,
        )
        second_raw = wire.build_physical_full_matrix_v4_witness_anchor_immutable_head(
            policy=self.policy,
            predecessor=remote,
            append_request=verified_second_request,
            now=self.clock.now,
            witness_private_key=self.witness_private,
        )
        self.transport.current = wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
            second_raw,
            policy=self.policy,
            now=self.clock.now,
            expected_predecessor=remote,
            append_request=verified_second_request,
        )
        self.transport.current_raw = second_raw
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "READ_ENVELOPE_INVALID",
        ):
            self._read(value, expected=durable)

    def test_restart_after_old_observation_ttl_reuses_immutable_tail_with_fresh_proof(self) -> None:
        first, _, _ = self._adapter()
        self._read(first)
        receipt = first.append_commitment(commitment=self._commitment())
        durable = self.transport.current
        self.assertEqual(receipt.head_sha256, durable.head_sha256)
        self.clock.now += timedelta(hours=2)

        restarted, _, _ = self._adapter(
            replay_ids=[_hash("restart-replay")],
            read_challenges=[_hash("restart-fresh-read")],
        )
        renewed = self._read(restarted, expected=durable)
        self.assertEqual(durable.sequence, renewed.sequence)
        self.assertEqual(durable.head_sha256, renewed.head_sha256)
        self.assertEqual(receipt.attestation_sha256, renewed.attestation_sha256)
        self.assertNotEqual("0" * 64, renewed.attestation_sha256)

    def test_commitment_predecessor_replay_and_request_expiry_fail_closed(self) -> None:
        value, _, _ = self._adapter()
        self._read(value)
        wrong_binding = replace(self._commitment(), plan_sha256=_hash("wrong-plan"))
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "COMMITMENT_INVALID",
        ):
            value.append_commitment(commitment=wrong_binding)

        wrong_predecessor = replace(
            self._commitment(),
            previous_anchor_head_sha256=_hash("wrong-predecessor"),
        )
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "PREDECESSOR_MISMATCH",
        ):
            value.append_commitment(commitment=wrong_predecessor)

        replay = _hash("same-replay")
        value, _, _ = self._adapter(replay_ids=[replay, replay])
        self._read(value)
        value.append_commitment(commitment=self._commitment())
        second = self._commitment(predecessor=self.transport.current, phase_sequence=2)
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "REPLAY_ID_INVALID",
        ):
            value.append_commitment(commitment=second)

        clock = _Clock()
        transport = _SignedWireTransport(
            policy=self.policy,
            witness_private=self.witness_private,
            clock=clock,
        )
        transport.advance_before_request_verify = NOW + timedelta(seconds=3)
        value, _, _ = self._adapter(
            request_lifetime_seconds=1,
            transport=transport,
            clock=clock,
        )
        self._read(value, expected=transport.current)
        with self.assertRaisesRegex(
            adapter.PhysicalFullMatrixV4WitnessAnchorAdapterError,
            "TRANSPORT_APPEND_FAILED",
        ):
            value.append_commitment(commitment=self._commitment(predecessor=transport.current))

    def test_static_boundary_has_no_legacy_head_or_live_provider_imports(self) -> None:
        source = ADAPTER_PATH.read_text(encoding="utf-8")
        for legacy in (
            "parse_physical_full_matrix_v4_witness_anchor_witness_head",
            "prepare_physical_full_matrix_v4_witness_anchor_witness_head",
            "finalize_physical_full_matrix_v4_witness_anchor_witness_head",
            "build_physical_full_matrix_v4_witness_anchor_witness_head",
            "verify_physical_full_matrix_v4_witness_anchor_witness_head",
            "verify_physical_full_matrix_v4_witness_anchor_append_head",
        ):
            self.assertNotIn(legacy, source)
        adapter_tree = ast.parse(source)
        adapter_imports: list[tuple[str, tuple[str, ...]]] = []
        for node in ast.walk(adapter_tree):
            if isinstance(node, ast.Import):
                adapter_imports.extend((item.name, ()) for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                adapter_imports.append((node.module, tuple(item.name for item in node.names)))
        forbidden_prefixes = (
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "urllib",
            "http",
            "boto",
            "paramiko",
        )
        self.assertFalse(
            [
                module
                for module, _names in adapter_imports
                if module == "core.physical_full_matrix_execution_driver_v4"
                or module.startswith(forbidden_prefixes)
            ],
            adapter_imports,
        )
        core_imports = {
            name
            for module, names in adapter_imports
            if module == "core"
            for name in names
        }
        self.assertEqual(
            {
                "physical_full_matrix_v4_receipt_journal",
                "physical_full_matrix_v4_witness_anchor_wire",
            },
            core_imports,
        )
        os_attributes = {
            node.attr
            for node in ast.walk(adapter_tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        }
        self.assertEqual({"geteuid"}, os_attributes)

        journal_tree = ast.parse(JOURNAL_PATH.read_text(encoding="utf-8"))
        journal_core_imports = {
            name
            for node in ast.walk(journal_tree)
            if isinstance(node, ast.ImportFrom) and node.module == "core"
            for name in (item.name for item in node.names)
        }
        self.assertNotIn("physical_full_matrix_v4_witness_anchor_adapter", journal_core_imports)
