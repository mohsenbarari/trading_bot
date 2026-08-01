"""Focused durable-state tests for the V2 immutable Witness-anchor ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core import physical_full_matrix_v4_witness_anchor_ledger as ledger
from core import physical_full_matrix_v4_receipt_journal as journal
from core import physical_full_matrix_v4_witness_anchor_adapter as adapter
from core import physical_full_matrix_v4_witness_anchor_wire as wire


NOW = datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)
RUN_ID = UUID("b0d4806a-b9f4-46ba-9fd5-f4123708b65f")
PLAN_SHA256 = "c" * 64
JOURNAL_BINDING = hashlib.sha256(b"v4-ledger-binding").hexdigest()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _binding() -> dict[str, object]:
    return {
        "campaign_id": "physical-full-matrix-v4-ledger-20260731",
        "release_sha": "d" * 40,
        "readiness_binding_sha256": _hash("readiness"),
        "route_commitment_sha256": _hash("route"),
        "four_role_binding_sha256": _hash("four-role"),
        "writer_holder_site": "webapp_fi",
        "writer_epoch": 5,
        "writer_lease_id": "ledger-writer-lease-v4-000001",
        "witnessed_term_proof_sha256": _hash("term"),
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "roundtrip_attestation_sha256": _hash("roundtrip"),
        "roundtrip_configuration_sha256": _hash("configuration"),
        "witness_transition_id": "ledger-witness-transition-v4-000001",
        "witness_sequence": 23,
    }


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now


class _CountingClock:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.calls = 0

    def now_utc(self) -> datetime:
        self.calls += 1
        return self.now


class _Signer:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        self.immutable_signatures = 0
        self.observation_signatures = 0
        self.fail_immutable = False

    def witness_public_key(self) -> bytes:
        return self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def sign_immutable_anchor_head(
        self,
        *,
        canonical_signed_immutable_head: bytes,
    ) -> bytes:
        self.immutable_signatures += 1
        if self.fail_immutable:
            raise RuntimeError("intentional signer failure")
        return self._private_key.sign(canonical_signed_immutable_head)

    def sign_read_observation(
        self,
        *,
        canonical_signed_read_observation: bytes,
    ) -> bytes:
        self.observation_signatures += 1
        return self._private_key.sign(canonical_signed_read_observation)


@dataclass(frozen=True)
class _LookalikeIdentity:
    """Same fields are intentionally insufficient without the Wire type."""

    schema: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    canonical_genesis_sha256: str


class _AdapterReplayIds:
    def __init__(self, values: list[str]) -> None:
        self.values = list(values)

    def next_controller_append_replay_id(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> str:
        del policy_identity
        return self.values.pop(0)


class _AdapterReadChallenges:
    def __init__(self, values: list[str]) -> None:
        self.values = list(values)

    def next_witness_read_challenge(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> str:
        del policy_identity
        return self.values.pop(0)


class PhysicalFullMatrixV4WitnessAnchorLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller_private = Ed25519PrivateKey.generate()
        witness_private = Ed25519PrivateKey.generate()
        controller_public = self.controller_private.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        witness_public = witness_private.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        baseline = wire.derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            initial_active_binding=_binding(),
        )
        genesis = wire.build_physical_full_matrix_v4_witness_anchor_genesis(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=41,
            head_sha256=_hash("ledger-genesis"),
            witness_private_key=witness_private,
        )
        self.policy = wire.build_physical_full_matrix_v4_witness_anchor_verification_policy(
            genesis=genesis,
            controller_public_key=controller_public,
            witness_public_key=witness_public,
        )
        self.config = ledger.RootOwnedPhysicalFullMatrixV4WitnessAnchorLedgerConfig(
            enabled=True,
            policy=self.policy,
        )
        self.identity = wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity(
            schema=wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA,
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            anchor_genesis_sequence=genesis.sequence,
            anchor_genesis_head_sha256=genesis.head_sha256,
            canonical_genesis_sha256=hashlib.sha256(
                wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(genesis)
            ).hexdigest(),
        )
        self.clock = _Clock(NOW)
        self.signer = _Signer(witness_private)
        # The ledger validates every ancestor as root-owned/non-writable.  A
        # per-test directory below this root-owned checkout satisfies the same
        # invariant without ever touching the deployment fixed path.
        self._temporary = tempfile.TemporaryDirectory(
            prefix="v4-anchor-ledger-",
            dir=Path(__file__).resolve().parents[1],
        )
        self._state_root = Path(self._temporary.name) / "state"
        self._state_root.mkdir(mode=0o700)
        self._fixed_root = ledger.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT
        ledger.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT = self._state_root
        self.service = ledger.RootOwnedPhysicalFullMatrixV4WitnessAnchorLedger(
            self.config,
            root_signer=self.signer,
            trusted_clock=self.clock,
        )

    def tearDown(self) -> None:
        ledger.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT = self._fixed_root
        self._temporary.cleanup()

    def _genesis_head(self):
        return wire.verified_physical_full_matrix_v4_witness_anchor_genesis_head(
            policy=self.policy,
            now=self.clock.now,
        )

    def _request(self, *, predecessor, phase: int = 1, replay: str = "append") -> bytes:
        commitment = wire.build_physical_full_matrix_v4_witness_anchor_commitment(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.policy.genesis.baseline_plan_binding_sha256,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            anchor_genesis_sequence=self.policy.genesis.sequence,
            anchor_genesis_head_sha256=self.policy.genesis.head_sha256,
            event="effect-started",
            phase_sequence=phase,
            phase_request_sha256=_hash(f"phase-request-{phase}"),
            effect_key=_hash(f"effect-{phase}"),
            claim_id=f"ledger-v4-claim-{phase:02d}-000000000000",
            receipt_sha256=None,
            previous_anchor_sequence=predecessor.sequence,
            previous_anchor_head_sha256=predecessor.head_sha256,
            local_previous_record_sha256=_hash(f"local-prev-{phase}"),
            local_event_sha256=_hash(f"local-event-{phase}"),
            occurred_at=self.clock.now,
        )
        return wire.build_physical_full_matrix_v4_witness_anchor_controller_append_request(
            policy=self.policy,
            predecessor=predecessor,
            commitment=commitment,
            replay_id=_hash(replay),
            issued_at=self.clock.now,
            expires_at=self.clock.now + timedelta(seconds=60),
            controller_private_key=self.controller_private,
        )

    def _adapter(
        self,
        *,
        replay_ids: list[str],
        read_challenges: list[str],
    ) -> adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter:
        return adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter(
            config=adapter.PhysicalFullMatrixV4WitnessAnchorAdapterConfig(
                policy=self.policy,
                controller_private_key=self.controller_private,
                transport=self.service,
                clock=self.clock,
                replay_id_source=_AdapterReplayIds(replay_ids),
                read_challenge_source=_AdapterReadChallenges(read_challenges),
                request_lifetime_seconds=30,
            )
        )

    def test_lookalike_identity_is_rejected_before_storage_or_signing(self) -> None:
        lookalike = _LookalikeIdentity(**self.identity.__dict__)
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "POLICY_IDENTITY_MISMATCH",
        ):
            self.service.read_signed_head(
                policy_identity=lookalike,  # type: ignore[arg-type]
                read_challenge=_hash("lookalike-read"),
            )
        self.assertEqual(0, self.signer.immutable_signatures)
        self.assertEqual(0, self.signer.observation_signatures)

    def test_nonroot_rejects_before_untrusted_clock_callback(self) -> None:
        clock = _CountingClock(self.clock.now)
        service = ledger.RootOwnedPhysicalFullMatrixV4WitnessAnchorLedger(
            self.config,
            root_signer=self.signer,
            trusted_clock=clock,
        )
        with mock.patch.object(ledger.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
                "ROOT_RUNTIME_REQUIRED",
            ):
                service.read_signed_head(
                    policy_identity=self.identity,
                    read_challenge=_hash("nonroot-read"),
                )
            with self.assertRaisesRegex(
                ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
                "ROOT_RUNTIME_REQUIRED",
            ):
                service.append_signed_request(
                    policy_identity=self.identity,
                    canonical_controller_append_request=b"not-reached",
                    read_challenge=_hash("nonroot-append"),
                )
        self.assertEqual(0, clock.calls)

    def test_state_root_symlink_and_crash_temp_residue_fail_before_signing(self) -> None:
        target = self._state_root.parent / "alternate-root"
        target.mkdir(mode=0o700)
        self._state_root.rmdir()
        self._state_root.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "STATE_ROOT_UNSAFE",
        ):
            self.service.read_signed_head(
                policy_identity=self.identity,
                read_challenge=_hash("symlink-root"),
        )
        self.assertEqual(0, self.signer.observation_signatures)

        # Restore a real secure root, then leave the exact shape of a failed
        # current-pointer temporary.  Recovery must stop, not delete it.
        self._state_root.unlink()
        self._state_root.mkdir(mode=0o700)
        ancestor_target = self._state_root.parent / "ancestor-target"
        ancestor_target.mkdir(mode=0o700)
        ancestor_link = self._state_root.parent / "ancestor-link"
        ancestor_link.symlink_to(ancestor_target, target_is_directory=True)
        ledger.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT = (
            ancestor_link / "state"
        )
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "STATE_ROOT_UNSAFE",
        ):
            self.service.read_signed_head(
                policy_identity=self.identity,
                read_challenge=_hash("symlink-ancestor"),
            )
        ledger.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT = self._state_root
        residue = self._state_root / (".current-" + "a" * 64 + ".tmp")
        residue.write_bytes(b"interrupted-current-write")
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "LAYOUT_UNSAFE",
        ):
            self.service.read_signed_head(
                policy_identity=self.identity,
                read_challenge=_hash("residue-read"),
            )
        self.assertEqual(0, self.signer.observation_signatures)

    def test_create_only_pending_collision_never_overwrites_or_signs(self) -> None:
        request = self._request(predecessor=self._genesis_head(), replay="pending-collision")
        original = ledger._write_create_only_at
        injected = False

        def collide(parent_fd: int, name: str, payload: bytes, *, code: str) -> None:
            nonlocal injected
            if not injected and ledger._PENDING_NAME_RE.fullmatch(name) is not None:
                injected = True
                original(parent_fd, name, b"attacker-pending", code=code)
            original(parent_fd, name, payload, code=code)

        with mock.patch.object(ledger, "_write_create_only_at", side_effect=collide):
            with self.assertRaisesRegex(
                ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
                "PENDING_WRITE_FAILED",
            ):
                self.service.append_signed_request(
                    policy_identity=self.identity,
                    canonical_controller_append_request=request,
                    read_challenge=_hash("pending-collision-read"),
                )
        self.assertTrue(injected)
        self.assertEqual(0, self.signer.immutable_signatures)

    def test_create_only_record_collision_never_overwrites_or_resigns(self) -> None:
        request = self._request(predecessor=self._genesis_head(), replay="record-collision")
        original = ledger._write_create_only_at
        injected = False

        def collide(parent_fd: int, name: str, payload: bytes, *, code: str) -> None:
            nonlocal injected
            if not injected and ledger._RECORD_NAME_RE.fullmatch(name) is not None:
                injected = True
                original(parent_fd, name, b"attacker-record", code=code)
            original(parent_fd, name, payload, code=code)

        with mock.patch.object(ledger, "_write_create_only_at", side_effect=collide):
            with self.assertRaisesRegex(
                ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
                "RECORD_WRITE_FAILED",
            ):
                self.service.append_signed_request(
                    policy_identity=self.identity,
                    canonical_controller_append_request=request,
                    read_challenge=_hash("record-collision-read"),
                )
        self.assertTrue(injected)
        self.assertEqual(1, self.signer.immutable_signatures)
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "RECORD_INVALID",
        ):
            self.service.read_signed_head(
                policy_identity=self.identity,
                read_challenge=_hash("record-collision-reread"),
            )

    def test_genesis_append_restart_and_fresh_observation_after_old_ttl(self) -> None:
        genesis = self._genesis_head()
        initial_challenge = _hash("initial-read")
        initial = self.service.read_signed_head(
            policy_identity=self.identity,
            read_challenge=initial_challenge,
        )
        verified_initial = wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            initial,
            policy=self.policy,
            now=self.clock.now,
            expected_read_challenge=initial_challenge,
            expected_current_sequence=genesis.sequence,
            expected_current_head_sha256=genesis.head_sha256,
        )
        self.assertEqual(genesis.head_sha256, verified_initial.anchor_head.head_sha256)
        request = self._request(predecessor=genesis)
        verified_request = wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
            request,
            policy=self.policy,
            predecessor=genesis,
            now=self.clock.now,
        )
        append_challenge = _hash("append-read")
        appended = self.service.append_signed_request(
            policy_identity=self.identity,
            canonical_controller_append_request=request,
            read_challenge=append_challenge,
        )
        verified_append = wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            appended,
            policy=self.policy,
            now=self.clock.now,
            expected_read_challenge=append_challenge,
            expected_predecessor=genesis,
            append_request=verified_request,
        )
        immutable = verified_append.anchor_head
        self.assertIsInstance(
            immutable,
            wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead,
        )
        self.assertEqual(1, self.signer.immutable_signatures)
        self.assertGreaterEqual(self.signer.observation_signatures, 2)
        # A new process/read after the old one-layer TTL gets a new short-lived
        # observation over the same durable immutable record.
        self.clock.now += timedelta(seconds=3600)
        restarted = ledger.RootOwnedPhysicalFullMatrixV4WitnessAnchorLedger(
            self.config,
            root_signer=self.signer,
            trusted_clock=self.clock,
        )
        restart_challenge = _hash("restart-read")
        reread = restarted.read_signed_head(
            policy_identity=self.identity,
            read_challenge=restart_challenge,
        )
        verified_restart = wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            reread,
            policy=self.policy,
            now=self.clock.now,
            expected_read_challenge=restart_challenge,
            expected_current_sequence=immutable.sequence,
            expected_current_head_sha256=immutable.head_sha256,
        )
        self.assertEqual(immutable.head_sha256, verified_restart.anchor_head.head_sha256)
        self.assertNotEqual(
            verified_append.read_observation.observation_id,
            verified_restart.read_observation.observation_id,
        )

    def test_real_adapter_to_ledger_restart_uses_durable_expected_tail_and_fresh_read(self) -> None:
        first_adapter = self._adapter(
            replay_ids=[_hash("adapter-ledger-replay")],
            read_challenges=[_hash("adapter-ledger-read-1"), _hash("adapter-ledger-read-2")],
        )
        genesis_head = first_adapter.read_head(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.policy.genesis.baseline_plan_binding_sha256,
            expected_anchor_sequence=self.policy.genesis.sequence,
            expected_anchor_head_sha256=self.policy.genesis.head_sha256,
        )
        commitment = journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment(
            schema=journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_COMMITMENT_SCHEMA,
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.policy.genesis.baseline_plan_binding_sha256,
            anchor_genesis_sequence=self.policy.genesis.sequence,
            anchor_genesis_head_sha256=self.policy.genesis.head_sha256,
            event="effect-started",
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            phase_sequence=1,
            phase_request_sha256=_hash("adapter-ledger-phase-request"),
            effect_key=_hash("adapter-ledger-effect"),
            claim_id="adapter-ledger-claim-v4-000000000000",
            previous_anchor_sequence=genesis_head.sequence,
            previous_anchor_head_sha256=genesis_head.head_sha256,
            local_previous_record_sha256=_hash("adapter-ledger-local-prev"),
            local_event_sha256=_hash("adapter-ledger-local-event"),
            receipt_sha256=None,
            occurred_at=self.clock.now,
        )
        receipt = first_adapter.append_commitment(commitment=commitment)
        self.assertEqual(genesis_head.sequence + 1, receipt.sequence)
        self.assertEqual(1, self.signer.immutable_signatures)

        # The new adapter models a new root process.  Its only durable input is
        # the journal-pinned tail; it has no old in-process observation cache.
        self.clock.now += timedelta(seconds=3600)
        restarted_adapter = self._adapter(
            replay_ids=[_hash("adapter-ledger-replay-after-restart")],
            read_challenges=[_hash("adapter-ledger-restart-read")],
        )
        reread = restarted_adapter.read_head(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.policy.genesis.baseline_plan_binding_sha256,
            expected_anchor_sequence=receipt.sequence,
            expected_anchor_head_sha256=receipt.head_sha256,
        )
        self.assertEqual(receipt.sequence, reread.sequence)
        self.assertEqual(receipt.head_sha256, reread.head_sha256)
        self.assertEqual(1, self.signer.immutable_signatures)
        self.assertGreaterEqual(self.signer.observation_signatures, 3)

    def test_pending_crash_refuses_retry(self) -> None:
        genesis = self._genesis_head()
        request = self._request(predecessor=genesis, replay="pending-crash")
        self.signer.fail_immutable = True
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "SIGNER_FAILED",
        ):
            self.service.append_signed_request(
                policy_identity=self.identity,
                canonical_controller_append_request=request,
                read_challenge=_hash("failed-append-read"),
            )
        self.signer.fail_immutable = False
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "PENDING_INDETERMINATE",
        ):
            self.service.read_signed_head(
                policy_identity=self.identity,
                read_challenge=_hash("blocked-read"),
            )

    def test_durable_record_before_pointer_recovers_without_resign(self) -> None:
        """A crash after record fsync repairs only the derived pointer."""

        genesis = self._genesis_head()
        request = self._request(predecessor=genesis, replay="pointer-crash")
        with mock.patch.object(
            ledger,
            "_write_current_atomic",
            side_effect=ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError("simulated"),
        ):
            with self.assertRaises(ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError):
                self.service.append_signed_request(
                    policy_identity=self.identity,
                    canonical_controller_append_request=request,
                    read_challenge=_hash("pointer-crash-read"),
                )
        signed_once = self.signer.immutable_signatures
        challenge = _hash("recovered-read")
        recovered = self.service.read_signed_head(
            policy_identity=self.identity,
            read_challenge=challenge,
        )
        verified = wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            recovered,
            policy=self.policy,
            now=self.clock.now,
            expected_read_challenge=challenge,
            expected_current_sequence=genesis.sequence,
            expected_current_head_sha256=genesis.head_sha256,
        )
        self.assertEqual(genesis.sequence + 1, verified.anchor_head.sequence)
        self.assertEqual(signed_once, self.signer.immutable_signatures)

    def test_replayed_request_and_valid_shape_pointer_rollback_fail_closed(self) -> None:
        genesis = self._genesis_head()
        request = self._request(predecessor=genesis, replay="replay-request")
        self.service.append_signed_request(
            policy_identity=self.identity,
            canonical_controller_append_request=request,
            read_challenge=_hash("initial-append-read"),
        )
        immutable_signatures = self.signer.immutable_signatures
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "REQUEST_INVALID",
        ):
            self.service.append_signed_request(
                policy_identity=self.identity,
                canonical_controller_append_request=request,
                read_challenge=_hash("replay-append-read"),
            )
        self.assertEqual(immutable_signatures, self.signer.immutable_signatures)

        # The replacement is canonical and shape-valid but rolls the derived
        # pointer back behind an immutable durable record.  Restart/read must
        # fail rather than repair a pointer that already exists and disagrees.
        facts = self.service._facts(self.clock.now)
        rollback = ledger._current_payload(
            facts=facts,
            current=ledger._Current(
                sequence=genesis.sequence,
                head_sha256=genesis.head_sha256,
                record_sha256=_hash("rollback-record"),
            ),
        )
        (self._state_root / "current.json").write_bytes(rollback)
        with self.assertRaisesRegex(
            ledger.PhysicalFullMatrixV4WitnessAnchorLedgerError,
            "CURRENT_ROLLBACK",
        ):
            self.service.read_signed_head(
                policy_identity=self.identity,
                read_challenge=_hash("rollback-read"),
            )
