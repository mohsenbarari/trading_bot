"""One inert V4 driver/journal integration path.

This test intentionally uses a semantic in-memory Witness anchor and root-owned
temporary journal directory.  It performs no network, provider, SSH, Docker,
or phase effect.  Its purpose is to ensure the transition-safe V4 driver can
use the concrete external-anchor journal rather than only a test-double
journal, and that a fresh journal instance can prove the resulting campaign
point without treating raw receipt bytes as authority.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v4_receipt_journal as journal
from core import physical_full_matrix_v4_plan_rehydration as rehydration
from core import physical_full_matrix_v4_witness_anchor_adapter as witness_adapter
from core import physical_full_matrix_v4_witness_anchor_wire as witness_wire


NOW = datetime(2026, 7, 31, 18, 0, tzinfo=timezone.utc)
RUN_ID = UUID("6a5a0e81-8dc4-4b46-b2df-a2809ef9deaa")


def _sha256(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        result = self.now
        self.now += timedelta(seconds=1)
        return result


class _ManualClock:
    """Shared deterministic trusted time for the signed restart integration."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now


class _WireReplayIds:
    def __init__(self, *, namespace: str) -> None:
        self.namespace = namespace
        self.ordinal = 0

    def next_controller_append_replay_id(
        self,
        *,
        policy_identity: witness_adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> str:
        del policy_identity
        self.ordinal += 1
        return _sha256(f"driver-journal-wire-replay-{self.namespace}-{self.ordinal}")


class _WireReadChallenges:
    def __init__(self, *, namespace: str) -> None:
        self.namespace = namespace
        self.ordinal = 0

    def next_witness_read_challenge(
        self,
        *,
        policy_identity: witness_adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> str:
        del policy_identity
        self.ordinal += 1
        return _sha256(f"driver-journal-wire-read-{self.namespace}-{self.ordinal}")


_VerifiedWireAnchor = (
    witness_wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead
    | witness_wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
)


class _SignedEnvelopeWitnessTransport:
    """Narrow signed Witness fake used only by the real adapter integration.

    It stores a stable immutable anchor and emits a distinct short-lived,
    challenge-bound observation for every read/append response.  It has no
    network or provider behavior.
    """

    def __init__(
        self,
        *,
        policy: witness_wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
        witness_private_key: Ed25519PrivateKey,
        clock: _ManualClock,
    ) -> None:
        self.policy = policy
        self.witness_private_key = witness_private_key
        self.clock = clock
        self.current: _VerifiedWireAnchor = (
            witness_wire.verified_physical_full_matrix_v4_witness_anchor_genesis_head(
                policy=policy,
                now=clock.now,
            )
        )
        self.current_raw = (
            witness_wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
                policy.genesis
            )
        )
        self.seen_replay_ids: set[str] = set()
        self.observation_ordinal = 0
        self.appended_immutable_attestations: list[str] = []

    def _identity_matches(
        self,
        value: witness_adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> bool:
        genesis = self.policy.genesis
        return (
            value.schema
            == witness_adapter.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_IDENTITY_SCHEMA
            and value.journal_binding_sha256 == genesis.journal_binding_sha256
            and value.baseline_plan_binding_sha256
            == genesis.baseline_plan_binding_sha256
            and value.run_id == genesis.run_id
            and value.plan_sha256 == genesis.plan_sha256
            and value.anchor_genesis_sequence == genesis.sequence
            and value.anchor_genesis_head_sha256 == genesis.head_sha256
            and value.canonical_genesis_sha256
            == hashlib.sha256(
                witness_wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
                    genesis
                )
            ).hexdigest()
        )

    def _response(self, *, read_challenge: str) -> bytes:
        self.observation_ordinal += 1
        observation = (
            witness_wire.build_physical_full_matrix_v4_witness_anchor_read_observation(
                policy=self.policy,
                anchor_head=self.current,
                read_challenge=read_challenge,
                observation_id=_sha256(
                    f"driver-journal-wire-observation-{self.observation_ordinal}"
                ),
                observed_at=self.clock.now,
                expires_at=self.clock.now + timedelta(seconds=30),
                witness_private_key=self.witness_private_key,
            )
        )
        return witness_wire.build_physical_full_matrix_v4_witness_anchor_transport_envelope(
            canonical_anchor_head=self.current_raw,
            canonical_read_observation=observation,
            read_challenge=read_challenge,
        )

    def read_signed_head(
        self,
        *,
        policy_identity: witness_adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        read_challenge: str,
    ) -> bytes:
        if not self._identity_matches(policy_identity):
            raise RuntimeError("unexpected signed witness identity")
        return self._response(read_challenge=read_challenge)

    def append_signed_request(
        self,
        *,
        policy_identity: witness_adapter.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        canonical_controller_append_request: bytes,
        read_challenge: str,
    ) -> bytes:
        if not self._identity_matches(policy_identity):
            raise RuntimeError("unexpected signed witness identity")
        request = (
            witness_wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
                canonical_controller_append_request,
                policy=self.policy,
                predecessor=self.current,
                now=self.clock.now,
                seen_replay_ids=self.seen_replay_ids,
            )
        )
        self.seen_replay_ids.add(request.replay_id)
        raw = witness_wire.build_physical_full_matrix_v4_witness_anchor_immutable_head(
            policy=self.policy,
            predecessor=self.current,
            append_request=request,
            now=self.clock.now,
            witness_private_key=self.witness_private_key,
        )
        prior = self.current
        self.current = witness_wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
            raw,
            policy=self.policy,
            now=self.clock.now,
            expected_predecessor=prior,
            append_request=request,
        )
        self.current_raw = raw
        self.appended_immutable_attestations.append(
            self.current.immutable_attestation_sha256
        )
        return self._response(read_challenge=read_challenge)


class _Anchor:
    """Authenticated-anchor semantic double; no transport or signing path."""

    def __init__(
        self,
        *,
        journal_binding: str,
        baseline_binding: str,
        genesis_sequence: int = 0,
        genesis_head_sha256: str = "0" * 64,
    ) -> None:
        self.journal_binding = journal_binding
        self.baseline_binding = baseline_binding
        self.sequence = genesis_sequence
        self.head_sha256 = genesis_head_sha256
        self.previous_head_sha256 = "0" * 64
        self.commitment_sha256 = "0" * 64
        self.attestation_sha256 = "0" * 64
        self.commitment = None
        self.history: list[journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment] = []

    def read_head(
        self,
        *,
        journal_binding_sha256: str,
        baseline_plan_binding_sha256: str,
        expected_anchor_sequence: int,
        expected_anchor_head_sha256: str,
    ) -> journal.PhysicalFullMatrixV4WitnessJournalAnchorHead:
        if (
            journal_binding_sha256 != self.journal_binding
            or baseline_plan_binding_sha256 != self.baseline_binding
            or expected_anchor_sequence != self.sequence
            or expected_anchor_head_sha256 != self.head_sha256
        ):
            raise RuntimeError("unexpected anchor binding")
        return journal.PhysicalFullMatrixV4WitnessJournalAnchorHead(
            schema=journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA,
            journal_binding_sha256=self.journal_binding,
            baseline_plan_binding_sha256=self.baseline_binding,
            sequence=self.sequence,
            head_sha256=self.head_sha256,
            previous_head_sha256=self.previous_head_sha256,
            commitment_sha256=self.commitment_sha256,
            attestation_sha256=self.attestation_sha256,
            commitment=self.commitment,
        )

    def append_commitment(
        self,
        *,
        commitment: journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment,
    ) -> journal.PhysicalFullMatrixV4WitnessJournalAnchorReceipt:
        if (
            commitment.journal_binding_sha256 != self.journal_binding
            or commitment.baseline_plan_binding_sha256 != self.baseline_binding
            or commitment.previous_anchor_sequence != self.sequence
            or commitment.previous_anchor_head_sha256 != self.head_sha256
        ):
            raise RuntimeError("anchor predecessor mismatch")
        digest = journal._commitment_sha256(commitment)
        previous = self.head_sha256
        self.sequence += 1
        self.head_sha256 = hashlib.sha256(
            f"{previous}:{self.sequence}:{digest}".encode("ascii")
        ).hexdigest()
        self.previous_head_sha256 = previous
        self.commitment_sha256 = digest
        self.attestation_sha256 = _sha256(f"attestation:{self.sequence}:{self.head_sha256}")
        self.commitment = commitment
        self.history.append(commitment)
        return journal.PhysicalFullMatrixV4WitnessJournalAnchorReceipt(
            schema=journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA,
            journal_binding_sha256=self.journal_binding,
            baseline_plan_binding_sha256=self.baseline_binding,
            sequence=self.sequence,
            previous_head_sha256=previous,
            head_sha256=self.head_sha256,
            commitment_sha256=digest,
            attestation_sha256=self.attestation_sha256,
        )


def _binding() -> driver.PhysicalFullMatrixV4ExecutionBinding:
    return driver.PhysicalFullMatrixV4ExecutionBinding(
        campaign_id="physical-full-matrix-v4-integration-20260731",
        release_sha="d" * 40,
        readiness_binding_sha256=_sha256("readiness"),
        route_commitment_sha256=_sha256("route"),
        four_role_binding_sha256=_sha256("four-role"),
        writer_holder_site="webapp_fi",
        writer_epoch=7,
        writer_lease_id="writer-lease-v4-integration-000001",
        witnessed_term_proof_sha256=_sha256("term"),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        roundtrip_attestation_sha256=_sha256("roundtrip"),
        roundtrip_configuration_sha256=_sha256("configuration"),
        witness_transition_id="witness-transition-v4-integration-000001",
        witness_sequence=17,
    )


class _Resolver:
    def resolve_readiness(
        self, *, binding: driver.PhysicalFullMatrixV4ExecutionBinding
    ) -> driver.PhysicalFullMatrixV4ReadinessEvidence:
        return driver.PhysicalFullMatrixV4ReadinessEvidence(
            binding=binding,
            readiness=object(),
        )


class _PostEffectCompletion:
    """Inert, process-local fake capability for the integration harness."""


class _PostEffectVerifier:
    """Separate fake phase owner; adapter success is not self-attesting."""

    def __init__(self, phase: driver.PhysicalFullMatrixV4ExecutionPhase) -> None:
        self.phase_name = phase.name
        self.phase_sequence = phase.sequence
        self.oracle = phase.oracle
        self.transport_profile = phase.transport_profile
        self._issued: dict[
            int,
            tuple[
                _PostEffectCompletion,
                driver.PhysicalFullMatrixV4ExecutionRequest,
                driver.PhysicalFullMatrixV4EffectStartAuthority,
                driver.PhysicalFullMatrixV4EffectStartAnchorProof,
                str,
                datetime,
            ],
        ] = {}

    def issue(
        self,
        *,
        request: driver.PhysicalFullMatrixV4ExecutionRequest,
        authority: driver.PhysicalFullMatrixV4EffectStartAuthority,
        anchor: driver.PhysicalFullMatrixV4EffectStartAnchorProof,
        evidence_sha256: str,
        observed_at: datetime,
    ) -> _PostEffectCompletion:
        completion = _PostEffectCompletion()
        self._issued[id(completion)] = (
            completion,
            request,
            authority,
            anchor,
            evidence_sha256,
            observed_at,
        )
        return completion

    def require_post_effect_completion(
        self,
        *,
        request: driver.PhysicalFullMatrixV4ExecutionRequest,
        effect_start_authority: driver.PhysicalFullMatrixV4EffectStartAuthority,
        effect_start_anchor_proof: driver.PhysicalFullMatrixV4EffectStartAnchorProof,
        oracle: driver.PhysicalFullMatrixV4PhaseOracle,
        completion: object,
        observed_at: datetime,
        now: datetime,
        maximum_oracle_age_seconds: int,
    ) -> None:
        issued = self._issued.get(id(completion))
        if issued is None:
            raise AssertionError("foreign post-effect completion")
        (
            issued_completion,
            issued_request,
            issued_authority,
            issued_anchor,
            issued_evidence_sha256,
            issued_observed_at,
        ) = issued
        if (
            completion is not issued_completion
            or request is not issued_request
            or effect_start_authority is not issued_authority
            or effect_start_anchor_proof is not issued_anchor
            or oracle.evidence_sha256 != issued_evidence_sha256
            or observed_at != issued_observed_at
            or oracle.phase != self.phase_name
            or oracle.oracle != self.oracle
            or oracle.transport_profile != self.transport_profile
            or oracle.observed_at != observed_at
            or now - observed_at
            > timedelta(seconds=maximum_oracle_age_seconds)
            or observed_at > now + timedelta(seconds=5)
            or driver.require_physical_full_matrix_v4_effect_start_authority(
                request=request
            )
            is not effect_start_authority
            or driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=request
            )
            is not effect_start_anchor_proof
        ):
            raise AssertionError("post-effect completion correlation mismatch")


class _Adapter:
    def __init__(self, *, observed_at: datetime, verifier: _PostEffectVerifier) -> None:
        self.observed_at = observed_at
        self.verifier = verifier
        self.requests: list[driver.PhysicalFullMatrixV4ExecutionRequest] = []
        self.effect_start_authorities: list[
            driver.PhysicalFullMatrixV4EffectStartAuthority
        ] = []
        self.effect_start_anchor_proofs: list[
            driver.PhysicalFullMatrixV4EffectStartAnchorProof
        ] = []

    def execute_phase(
        self, *, request: driver.PhysicalFullMatrixV4ExecutionRequest
    ) -> driver.PhysicalFullMatrixV4PhaseOracle:
        self.effect_start_authorities.append(
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=request
            )
        )
        self.effect_start_anchor_proofs.append(
            driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=request
            )
        )
        self.requests.append(request)
        evidence_sha256 = _sha256("phase-one-oracle")
        return driver.PhysicalFullMatrixV4PhaseOracle(
            schema=driver.PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
            status="oracle-succeeded",
            phase=request.phase.name,
            oracle=request.phase.oracle,
            transport_profile=request.phase.transport_profile,
            effect_key=request.effect_key,
            evidence_sha256=evidence_sha256,
            observed_at=self.observed_at,
            readiness_evidence=request.pre_effect_readiness_evidence,
            post_effect_completion=self.verifier.issue(
                request=request,
                authority=self.effect_start_authorities[-1],
                anchor=self.effect_start_anchor_proofs[-1],
                evidence_sha256=evidence_sha256,
                observed_at=self.observed_at,
            ),
        )


@unittest.skipUnless(os.geteuid() == 0, "root-owned journal test requires root")
class PhysicalFullMatrixV4DriverJournalIntegrationTests(unittest.TestCase):
    def test_driver_phase_uses_external_anchor_journal_and_restart_continuity(self) -> None:
        binding = _binding()
        config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=binding,
            readiness=object(),
            run_id=RUN_ID,
            enabled=True,
        )
        with mock.patch.object(driver, "_validate_readiness", return_value=None), mock.patch.object(
            driver,
            "_validate_readiness_evidence",
            return_value=None,
        ):
            plan = driver.build_physical_full_matrix_v4_execution_plan(config=config)
            genesis_sequence = 41
            genesis_head_sha256 = _sha256("integration-witness-genesis")
            campaign_binding = journal.PhysicalFullMatrixV4ReceiptJournalCampaignBinding(
                schema=journal.PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_SCHEMA,
                run_id=plan.run_id,
                plan_sha256=plan.plan_sha256,
                initial_active_binding=plan.binding,
                anchor_genesis_sequence=genesis_sequence,
                anchor_genesis_head_sha256=genesis_head_sha256,
            )
            journal_binding = (
                journal.derive_physical_full_matrix_v4_receipt_journal_campaign_binding_sha256(
                    campaign_binding=campaign_binding,
                )
            )
            baseline_binding = (
                journal.derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256(
                    run_id=campaign_binding.run_id,
                    plan_sha256=campaign_binding.plan_sha256,
                    initial_active_binding=campaign_binding.initial_active_binding,
                )
            )
            anchor = _Anchor(
                journal_binding=journal_binding,
                baseline_binding=baseline_binding,
                genesis_sequence=genesis_sequence,
                genesis_head_sha256=genesis_head_sha256,
            )
            journal_config = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig(
                enabled=True,
                journal_binding_sha256=journal_binding,
                campaign_binding=campaign_binding,
                anchor_genesis_sequence=genesis_sequence,
                anchor_genesis_head_sha256=genesis_head_sha256,
            )
            with tempfile.TemporaryDirectory(
                prefix="full-matrix-v4-driver-journal-",
                dir=Path(__file__).resolve().parents[1],
            ) as temporary:
                root = Path(temporary).resolve()
                root.chmod(0o700)
                with mock.patch.object(
                    journal,
                    "FIXED_PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT",
                    root,
                ):
                    receipt_journal = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal(
                        journal_config,
                        witness_anchor=anchor,
                        trusted_clock=_Clock(),
                    )
                    phase_verifiers = {
                        phase.name: _PostEffectVerifier(phase)
                        for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
                    }
                    phase_adapters = {
                        phase.name: _Adapter(
                            observed_at=NOW,
                            verifier=phase_verifiers[phase.name],
                        )
                        for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
                    }
                    adapters = driver.PhysicalFullMatrixV4ExecutionAdapters(
                        phase_adapters=phase_adapters,
                        receipt_journal=receipt_journal,
                        readiness_resolver=_Resolver(),
                        trusted_clock=_Clock(),
                        campaign_continuity_gate=receipt_journal,
                        phase_post_effect_verifiers=phase_verifiers,
                    )
                    result = driver.execute_next_physical_full_matrix_v4_phase(
                        config=config,
                        plan=plan,
                        adapters=adapters,
                        now=NOW,
                    )
                    self.assertEqual("completed-redacted-phase-receipt", result.status)
                    self.assertEqual(plan.phases[0].name, result.phase)
                    self.assertIsNotNone(result.receipt)
                    self.assertEqual(["effect-started", "completed"], [item.event for item in anchor.history])
                    self.assertEqual(1, len(receipt_journal.read_receipts(run_id=plan.run_id)))
                    first_adapter = phase_adapters[plan.phases[0].name]
                    self.assertEqual(1, len(first_adapter.effect_start_authorities))
                    first_authority = first_adapter.effect_start_authorities[0]
                    self.assertEqual(plan.run_id, first_authority.run_id)
                    self.assertEqual(plan.plan_sha256, first_authority.plan_sha256)
                    self.assertFalse(first_authority.writer_authorized)
                    self.assertFalse(first_authority.promotion_authorized)
                    self.assertFalse(first_authority.execution_authorized)
                    self.assertFalse(first_authority.full_matrix_authorized)

                    restarted = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal(
                        journal_config,
                        witness_anchor=anchor,
                        # A real restart must restore/acquire a clock no older
                        # than the durable journal floor.  Starting it at NOW
                        # would correctly be refused as a rollback.
                        trusted_clock=_Clock(NOW + timedelta(hours=1)),
                    )
                    continuity = restarted.verify_campaign_continuity(
                        run_id=plan.run_id,
                        plan_sha256=plan.plan_sha256,
                        completed_sequence=1,
                        active_binding=plan.binding,
                    )
                    self.assertEqual(1, continuity.completed_sequence)
                    self.assertEqual(genesis_sequence + 2, continuity.anchor_sequence)
                    self.assertEqual(anchor.head_sha256, continuity.anchor_head_sha256)
                    self.assertIs(
                        continuity,
                        journal.require_verified_physical_full_matrix_v4_campaign_continuity(
                            continuity
                        ),
                    )

                    # The plan itself is intentionally nonserializable.  A
                    # restart obtains a fresh typed continuity projection and
                    # derives this new process-local plan only from its exact
                    # baseline pins; it is then still subject to normal V4
                    # readiness and continuity checks before phase two.
                    history_before_rehydration = tuple(anchor.history)
                    rehydrated_plan = (
                        rehydration.rehydrate_physical_full_matrix_v4_execution_plan(
                            config=config,
                            continuity=continuity,
                        )
                    )
                    self.assertEqual(history_before_rehydration, tuple(anchor.history))
                    self.assertFalse(rehydrated_plan.materialization_authorized)
                    self.assertFalse(rehydrated_plan.promotion_authorized)
                    self.assertFalse(rehydrated_plan.execution_authorized)
                    self.assertIs(
                        rehydrated_plan,
                        driver.require_physical_full_matrix_v4_execution_plan(
                            rehydrated_plan
                        ),
                    )
                    self.assertEqual(plan.canonical_plan, rehydrated_plan.canonical_plan)
                    second = driver.execute_next_physical_full_matrix_v4_phase(
                        config=config,
                        plan=rehydrated_plan,
                        adapters=adapters,
                        now=NOW,
                    )
                    self.assertEqual(plan.phases[1].name, second.phase)
                    self.assertEqual(2, len(receipt_journal.read_receipts(run_id=plan.run_id)))
                    self.assertEqual(
                        ["effect-started", "completed", "effect-started", "completed"],
                        [item.event for item in anchor.history],
                    )
                    # A now-stale projection may still rebuild only the same
                    # non-authorizing static plan; resume position is derived
                    # afresh from the concrete journal and current anchor by
                    # the driver, never from this old projection.
                    stale_plan = rehydration.rehydrate_physical_full_matrix_v4_execution_plan(
                        config=config,
                        continuity=continuity,
                    )
                    self.assertEqual(4, len(anchor.history))
                    third = driver.execute_next_physical_full_matrix_v4_phase(
                        config=config,
                        plan=stale_plan,
                        adapters=adapters,
                        now=NOW,
                    )
                    self.assertEqual(plan.phases[2].name, third.phase)
                    self.assertEqual(3, len(receipt_journal.read_receipts(run_id=plan.run_id)))
                    self.assertEqual(6, len(anchor.history))
                    with self.assertRaisesRegex(
                        rehydration.PhysicalFullMatrixV4PlanRehydrationError,
                        "PLAN_MISMATCH",
                    ):
                        rehydration.rehydrate_physical_full_matrix_v4_execution_plan(
                            config=replace(
                                config,
                                maximum_oracle_age_seconds=(
                                    config.maximum_oracle_age_seconds + 1
                                ),
                            ),
                            continuity=continuity,
                        )
                    forged = journal.VerifiedPhysicalFullMatrixV4CampaignContinuity(
                        run_id=continuity.run_id,
                        plan_sha256=continuity.plan_sha256,
                        completed_sequence=continuity.completed_sequence,
                        active_binding=continuity.active_binding,
                        journal_binding_sha256=continuity.journal_binding_sha256,
                        baseline_plan_binding_sha256=(
                            continuity.baseline_plan_binding_sha256
                        ),
                        anchor_sequence=continuity.anchor_sequence,
                        anchor_head_sha256=continuity.anchor_head_sha256,
                    )
                    with self.assertRaisesRegex(
                        rehydration.PhysicalFullMatrixV4PlanRehydrationError,
                        "CONTINUITY_INVALID",
                    ):
                        rehydration.rehydrate_physical_full_matrix_v4_execution_plan(
                            config=config,
                            continuity=forged,
                        )

    def test_signed_wire_adapter_restarts_after_observation_ttl_then_executes_phase_two(self) -> None:
        """Exercise driver -> real journal -> signed V2 adapter end to end.

        The first process completes phase one.  We then move trusted time far
        beyond the old read-observation lifetime, recreate *both* journal and
        adapter, obtain a newly signed observation of the unchanged immutable
        tail, and execute phase two.  This is the restart property required
        by the three-server failover architecture: short-lived transport read
        proofs may expire, but they cannot make an already durable immutable
        append tail disappear or mutate.
        """

        binding = _binding()
        config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=binding,
            readiness=object(),
            run_id=RUN_ID,
            enabled=True,
        )
        controller_private = Ed25519PrivateKey.generate()
        witness_private = Ed25519PrivateKey.generate()
        controller_public = controller_private.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        witness_public = witness_private.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        trusted_clock = _ManualClock(NOW)

        with mock.patch.object(driver, "_validate_readiness", return_value=None), mock.patch.object(
            driver,
            "_validate_readiness_evidence",
            return_value=None,
        ):
            plan = driver.build_physical_full_matrix_v4_execution_plan(config=config)
            genesis_sequence = 73
            campaign_binding = journal.PhysicalFullMatrixV4ReceiptJournalCampaignBinding(
                schema=journal.PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_SCHEMA,
                run_id=plan.run_id,
                plan_sha256=plan.plan_sha256,
                initial_active_binding=plan.binding,
                anchor_genesis_sequence=genesis_sequence,
                anchor_genesis_head_sha256=_sha256("signed-wire-restart-genesis"),
            )
            journal_binding = (
                journal.derive_physical_full_matrix_v4_receipt_journal_campaign_binding_sha256(
                    campaign_binding=campaign_binding,
                )
            )
            baseline_binding = (
                journal.derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256(
                    run_id=campaign_binding.run_id,
                    plan_sha256=campaign_binding.plan_sha256,
                    initial_active_binding=campaign_binding.initial_active_binding,
                )
            )
            genesis = witness_wire.build_physical_full_matrix_v4_witness_anchor_genesis(
                journal_binding_sha256=journal_binding,
                baseline_plan_binding_sha256=baseline_binding,
                run_id=plan.run_id,
                plan_sha256=plan.plan_sha256,
                sequence=genesis_sequence,
                head_sha256=campaign_binding.anchor_genesis_head_sha256,
                witness_private_key=witness_private,
            )
            policy = witness_wire.build_physical_full_matrix_v4_witness_anchor_verification_policy(
                genesis=genesis,
                controller_public_key=controller_public,
                witness_public_key=witness_public,
            )
            transport = _SignedEnvelopeWitnessTransport(
                policy=policy,
                witness_private_key=witness_private,
                clock=trusted_clock,
            )
            journal_config = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig(
                enabled=True,
                journal_binding_sha256=journal_binding,
                campaign_binding=campaign_binding,
                anchor_genesis_sequence=genesis.sequence,
                anchor_genesis_head_sha256=genesis.head_sha256,
            )
            process_generation = 0

            def new_wire_anchor() -> witness_adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter:
                nonlocal process_generation
                process_generation += 1
                namespace = f"process-{process_generation}"
                return witness_adapter.PhysicalFullMatrixV4WitnessAnchorWireAdapter(
                    config=witness_adapter.PhysicalFullMatrixV4WitnessAnchorAdapterConfig(
                        policy=policy,
                        controller_private_key=controller_private,
                        transport=transport,
                        clock=trusted_clock,
                        replay_id_source=_WireReplayIds(namespace=namespace),
                        read_challenge_source=_WireReadChallenges(namespace=namespace),
                    )
                )

            def execution_adapters(
                *,
                receipt_journal: journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal,
                observed_at: datetime,
            ) -> driver.PhysicalFullMatrixV4ExecutionAdapters:
                phase_verifiers = {
                    phase.name: _PostEffectVerifier(phase)
                    for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
                }
                return driver.PhysicalFullMatrixV4ExecutionAdapters(
                    phase_adapters={
                        phase.name: _Adapter(
                            observed_at=observed_at,
                            verifier=phase_verifiers[phase.name],
                        )
                        for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
                    },
                    receipt_journal=receipt_journal,
                    readiness_resolver=_Resolver(),
                    trusted_clock=trusted_clock,
                    campaign_continuity_gate=receipt_journal,
                    phase_post_effect_verifiers=phase_verifiers,
                )

            with tempfile.TemporaryDirectory(
                prefix="full-matrix-v4-signed-wire-restart-",
                dir=Path(__file__).resolve().parents[1],
            ) as temporary:
                root = Path(temporary).resolve()
                root.chmod(0o700)
                with mock.patch.object(
                    journal,
                    "FIXED_PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT",
                    root,
                ):
                    first_anchor = new_wire_anchor()
                    first_journal = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal(
                        journal_config,
                        witness_anchor=first_anchor,
                        trusted_clock=trusted_clock,
                    )
                    first_result = driver.execute_next_physical_full_matrix_v4_phase(
                        config=config,
                        plan=plan,
                        adapters=execution_adapters(
                            receipt_journal=first_journal,
                            observed_at=trusted_clock.now,
                        ),
                        now=trusted_clock.now,
                    )
                    self.assertEqual("completed-redacted-phase-receipt", first_result.status)
                    self.assertEqual(plan.phases[0].name, first_result.phase)
                    self.assertEqual(1, len(first_journal.read_receipts(run_id=plan.run_id)))
                    self.assertEqual(2, len(transport.appended_immutable_attestations))
                    phase_one_immutable_tail = transport.current
                    phase_one_attestation = (
                        transport.appended_immutable_attestations[-1]
                    )

                    # The first envelope observations expire after 30 seconds;
                    # this leap proves the new process cannot rely on them.
                    trusted_clock.now += timedelta(hours=2)
                    restarted_anchor = new_wire_anchor()
                    restarted_journal = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal(
                        journal_config,
                        witness_anchor=restarted_anchor,
                        trusted_clock=trusted_clock,
                    )
                    continuity = restarted_journal.verify_campaign_continuity(
                        run_id=plan.run_id,
                        plan_sha256=plan.plan_sha256,
                        completed_sequence=1,
                        active_binding=plan.binding,
                    )
                    self.assertEqual(1, continuity.completed_sequence)
                    self.assertEqual(phase_one_immutable_tail.sequence, continuity.anchor_sequence)
                    self.assertEqual(phase_one_immutable_tail.head_sha256, continuity.anchor_head_sha256)

                    # A fresh observation maps to the same permanent tail;
                    # it does not substitute a short-lived attestation hash.
                    restarted_head = restarted_anchor.read_head(
                        journal_binding_sha256=journal_binding,
                        baseline_plan_binding_sha256=baseline_binding,
                        expected_anchor_sequence=phase_one_immutable_tail.sequence,
                        expected_anchor_head_sha256=phase_one_immutable_tail.head_sha256,
                    )
                    self.assertEqual(phase_one_attestation, restarted_head.attestation_sha256)
                    self.assertEqual(
                        phase_one_immutable_tail.immutable_attestation_sha256,
                        restarted_head.attestation_sha256,
                    )

                    rehydrated = rehydration.rehydrate_physical_full_matrix_v4_execution_plan(
                        config=config,
                        continuity=continuity,
                    )
                    second_result = driver.execute_next_physical_full_matrix_v4_phase(
                        config=config,
                        plan=rehydrated,
                        adapters=execution_adapters(
                            receipt_journal=restarted_journal,
                            observed_at=trusted_clock.now,
                        ),
                        now=trusted_clock.now,
                    )
                    self.assertEqual(plan.phases[1].name, second_result.phase)
                    self.assertEqual(2, len(restarted_journal.read_receipts(run_id=plan.run_id)))
                    self.assertEqual(4, len(transport.appended_immutable_attestations))
                    self.assertEqual(
                        phase_one_attestation,
                        transport.appended_immutable_attestations[1],
                    )


if __name__ == "__main__":
    unittest.main()
