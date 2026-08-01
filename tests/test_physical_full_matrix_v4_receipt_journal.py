"""Focused V4-only tests for the Witness-anchored receipt journal.

The Anchor below is an in-memory semantic stand-in.  It models an already
authenticated external immutable Witness boundary; it is deliberately not a
transport, signing, provider, or host implementation.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v4_receipt_journal as journal
from core import physical_full_matrix_v4_witness_anchor_wire as wire


NOW = datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc)
RUN_ID = UUID("7ea994a3-a50a-4f10-bdaf-c75278a0ea74")
PLAN_SHA256 = "a" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_receipt_journal.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.calls = 0

    def now_utc(self) -> datetime:
        result = self.now
        self.now += timedelta(seconds=1)
        self.calls += 1
        return result


class _Anchor:
    """Small exact-head model for the injected external authority seam."""

    def __init__(
        self,
        *,
        binding: str,
        baseline: str,
        genesis_sequence: int = 0,
        genesis_head_sha256: str = "0" * 64,
    ) -> None:
        self.binding = binding
        self.baseline = baseline
        self.sequence = genesis_sequence
        self.head_sha256 = genesis_head_sha256
        self.previous_head_sha256 = "0" * 64
        self.commitment_sha256 = "0" * 64
        self.attestation_sha256 = "0" * 64
        self.commitment: journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment | None = None
        self.history: list[journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment] = []
        self.return_wrong_append_head = False
        self.reads = 0
        self.expected_reads: list[tuple[int, str]] = []

    def read_head(
        self,
        *,
        journal_binding_sha256: str,
        baseline_plan_binding_sha256: str,
        expected_anchor_sequence: int,
        expected_anchor_head_sha256: str,
    ):
        self.reads += 1
        self.expected_reads.append(
            (expected_anchor_sequence, expected_anchor_head_sha256)
        )
        if (
            journal_binding_sha256 != self.binding
            or baseline_plan_binding_sha256 != self.baseline
        ):
            raise RuntimeError("wrong binding")
        return journal.PhysicalFullMatrixV4WitnessJournalAnchorHead(
            schema=journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA,
            journal_binding_sha256=self.binding,
            baseline_plan_binding_sha256=self.baseline,
            sequence=self.sequence,
            head_sha256=self.head_sha256,
            previous_head_sha256=self.previous_head_sha256,
            commitment_sha256=self.commitment_sha256,
            attestation_sha256=self.attestation_sha256,
            commitment=self.commitment,
        )

    def append_commitment(self, *, commitment):
        if (
            commitment.journal_binding_sha256 != self.binding
            or commitment.baseline_plan_binding_sha256 != self.baseline
            or commitment.previous_anchor_sequence != self.sequence
            or commitment.previous_anchor_head_sha256 != self.head_sha256
        ):
            raise RuntimeError("stale external predecessor")
        digest = journal._commitment_sha256(commitment)
        next_sequence = self.sequence + 1
        next_head = hashlib.sha256(
            f"{self.head_sha256}:{next_sequence}:{digest}".encode("ascii")
        ).hexdigest()
        attestation = _hash(f"attestation:{next_sequence}:{next_head}")
        previous = self.head_sha256
        self.sequence = next_sequence
        self.previous_head_sha256 = previous
        self.head_sha256 = next_head
        self.commitment_sha256 = digest
        self.attestation_sha256 = attestation
        self.commitment = commitment
        self.history.append(commitment)
        returned_head = _hash("wrong-return-head") if self.return_wrong_append_head else next_head
        return journal.PhysicalFullMatrixV4WitnessJournalAnchorReceipt(
            schema=journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA,
            journal_binding_sha256=self.binding,
            baseline_plan_binding_sha256=self.baseline,
            sequence=next_sequence,
            previous_head_sha256=previous,
            head_sha256=returned_head,
            commitment_sha256=digest,
            attestation_sha256=attestation,
        )


def _binding() -> driver.PhysicalFullMatrixV4ExecutionBinding:
    return driver.PhysicalFullMatrixV4ExecutionBinding(
        campaign_id="physical-full-matrix-v4-20260731",
        release_sha="d" * 40,
        readiness_binding_sha256=_hash("readiness"),
        route_commitment_sha256=_hash("route"),
        four_role_binding_sha256=_hash("four-role"),
        writer_holder_site="webapp_fi",
        writer_epoch=7,
        writer_lease_id="writer-lease-v4-journal-000001",
        witnessed_term_proof_sha256=_hash("term"),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        roundtrip_attestation_sha256=_hash("roundtrip"),
        roundtrip_configuration_sha256=_hash("configuration"),
        witness_transition_id="witness-transition-v4-journal-000001",
        witness_sequence=17,
    )


def _campaign_binding() -> journal.PhysicalFullMatrixV4ReceiptJournalCampaignBinding:
    return journal.PhysicalFullMatrixV4ReceiptJournalCampaignBinding(
        schema=journal.PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_SCHEMA,
        run_id=RUN_ID,
        plan_sha256=PLAN_SHA256,
        initial_active_binding=_binding(),
        anchor_genesis_sequence=0,
        anchor_genesis_head_sha256="0" * 64,
    )


def _request_facts(
    *,
    sequence: int = 1,
) -> driver.PhysicalFullMatrixV4ExecutionRequest:
    binding = _binding()
    snapshot = driver._PlanSnapshot(
        canonical_plan=b"",
        plan_sha256=PLAN_SHA256,
        run_id=RUN_ID,
        binding=driver._snapshot_binding(binding, direction=("webapp_fi", "webapp_ir")),
        phases=driver._phase_snapshots(),
        maximum_oracle_age_seconds=1,
    )
    return driver._request(
        snapshot=snapshot,
        phase=driver._phase_snapshots()[sequence - 1],
        binding=snapshot.binding,
    )


REQUEST_SHA256 = _request_facts().phase_request_sha256
EFFECT_KEY = _request_facts().effect_key


def _receipt(
    *,
    previous_receipt_sha256: str = "0" * 64,
    sequence: int = 1,
) -> bytes:
    phase = driver._phase_snapshots()[sequence - 1]
    request = _request_facts(sequence=sequence)
    oracle = driver.PhysicalFullMatrixV4PhaseOracle(
        schema=driver.PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
        status="oracle-succeeded",
        phase=phase.name,
        oracle=phase.oracle,
        transport_profile=phase.transport_profile,
        effect_key=request.effect_key,
        evidence_sha256=_hash("oracle-evidence"),
        observed_at=NOW,
        readiness_evidence=None,
    )
    return driver._canonical(
        driver._receipt_body(
            request=request,
            phase=phase,
            oracle=oracle,
            successor=None,
            previous_receipt_sha256=previous_receipt_sha256,
            recorded_at=NOW,
        ),
        code="test",
    ) + b"\n"


@unittest.skipUnless(os.geteuid() == 0, "root-owned journal tests require root")
class PhysicalFullMatrixV4ReceiptJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        # Avoid a world-writable parent so the fixed-root ancestor checks are
        # exercised rather than patched away.
        self.temporary = tempfile.TemporaryDirectory(
            prefix="full-matrix-v4-receipt-journal-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.patch_root = mock.patch.object(
            journal,
            "FIXED_PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT",
            self.root,
        )
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.campaign_binding = _campaign_binding()
        self.journal_binding = journal._campaign_binding_sha256(self.campaign_binding)
        self.baseline_binding = journal._baseline_plan_binding_sha256(self.campaign_binding)
        self.anchor = _Anchor(
            binding=self.journal_binding,
            baseline=self.baseline_binding,
        )
        self.clock = _Clock()
        self.config = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig(
            enabled=True,
            journal_binding_sha256=self.journal_binding,
            campaign_binding=self.campaign_binding,
        )
        self.instance = self._journal()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _journal(self) -> journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal:
        return journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal(
            self.config,
            witness_anchor=self.anchor,
            trusted_clock=self.clock,
        )

    def _claim(self, active=None, *, effect_key: str = EFFECT_KEY):
        return (self.instance if active is None else active).claim_phase(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=REQUEST_SHA256,
            effect_key=effect_key,
        )

    def _start(self, active=None):
        active = self.instance if active is None else active
        claim = self._claim(active)
        return active.mark_effect_started(claim=claim, effect_key=EFFECT_KEY)

    def _complete(self, active=None) -> bytes:
        active = self.instance if active is None else active
        start = self._start(active)
        return active.append_started(effect_start=start, canonical_receipt=_receipt())

    def test_default_off_and_anchor_are_required_before_fixed_state_is_opened(self) -> None:
        disabled = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal(
            journal.RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig(
                journal_binding_sha256=self.journal_binding,
                campaign_binding=self.campaign_binding,
            ),
            witness_anchor=self.anchor,
            trusted_clock=self.clock,
        )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONFIG_INVALID$",
        ):
            disabled.read_receipts(run_id=RUN_ID)
        self.assertFalse((self.root / "receipt-journal.lock").exists())

        bad = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal(
            self.config,
            witness_anchor=None,  # type: ignore[arg-type]
            trusted_clock=self.clock,
        )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_REQUIRED",
        ):
            bad.read_receipts(run_id=RUN_ID)

    def test_public_baseline_and_campaign_digest_helpers_are_typed_and_non_authorizing(self) -> None:
        expected_wire_baseline = (
            wire.derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=dict(driver._snapshot_binding(
                    _binding(),
                    direction=("webapp_fi", "webapp_ir"),
                ).__dict__),
            )
        )
        self.assertEqual(
            self.baseline_binding,
            journal.derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=_binding(),
            ),
        )
        self.assertEqual(expected_wire_baseline, self.baseline_binding)
        self.assertEqual(
            self.journal_binding,
            journal.derive_physical_full_matrix_v4_receipt_journal_campaign_binding_sha256(
                campaign_binding=self.campaign_binding,
            ),
        )
        self.assertFalse((self.root / "binding.json").exists())
        self.assertEqual([], self.anchor.history)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "BASELINE_BINDING_INVALID",
        ):
            journal.derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=driver.PhysicalFullMatrixV4ExecutionBinding(
                    **{
                        **_binding().__dict__,
                        "writer_holder_site": "webapp_ir",
                        "source_site": "webapp_ir",
                        "destination_site": "webapp_fi",
                    }
                ),
            )

    def test_completion_is_externally_anchored_before_local_record_and_clock_floor_is_post_callback(self) -> None:
        appended = self._complete()
        self.assertEqual(_receipt(), appended)
        self.assertEqual(
            ["effect-started", "completed"],
            [item.event for item in self.anchor.history],
        )
        self.assertEqual((appended,), tuple(self.instance.read_receipts(run_id=RUN_ID)))
        files = sorted((self.root / "records").glob("*.json"))
        self.assertEqual(3, len(files))
        start_record = json.loads(files[1].read_text(encoding="ascii"))
        completion_record = json.loads(files[2].read_text(encoding="ascii"))
        # The persisted floor comes from the post-anchor read, rather than the
        # pre-callback commitment timestamp.
        self.assertGreater(start_record["clock_floor"], start_record["occurred_at"])
        self.assertGreater(completion_record["clock_floor"], completion_record["occurred_at"])
        self.assertEqual(self.anchor.head_sha256, completion_record["anchor_head_sha256"])
        self.assertFalse(completion_record.get("execution_authorized", False))

    def test_campaign_continuity_gate_pins_sequence_zero_and_anchored_completion_projection(self) -> None:
        zero = self.instance.verify_campaign_continuity(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            completed_sequence=0,
            active_binding=_binding(),
        )
        self.assertIs(
            zero,
            journal.require_verified_physical_full_matrix_v4_campaign_continuity(zero),
        )
        self.assertEqual(self.journal_binding, zero.journal_binding_sha256)
        self.assertEqual(self.baseline_binding, zero.baseline_plan_binding_sha256)
        self.assertEqual(0, zero.anchor_sequence)
        with self.assertRaises(TypeError):
            zero.__reduce_ex__(4)

        self._complete()
        restarted = self._journal()
        completed = restarted.verify_campaign_continuity(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            completed_sequence=1,
            active_binding=_binding(),
        )
        self.assertEqual(1, completed.completed_sequence)
        self.assertEqual(self.anchor.head_sha256, completed.anchor_head_sha256)
        self.assertIs(
            completed,
            journal.require_verified_physical_full_matrix_v4_campaign_continuity(completed),
        )

    def test_sequence_zero_requires_the_exact_typed_nonzero_witness_genesis(self) -> None:
        genesis_head = _hash("separate-witness-genesis")
        campaign = replace(
            self.campaign_binding,
            anchor_genesis_sequence=41,
            anchor_genesis_head_sha256=genesis_head,
        )
        campaign_digest = (
            journal.derive_physical_full_matrix_v4_receipt_journal_campaign_binding_sha256(
                campaign_binding=campaign,
            )
        )
        anchor = _Anchor(
            binding=campaign_digest,
            baseline=journal.derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256(
                run_id=campaign.run_id,
                plan_sha256=campaign.plan_sha256,
                initial_active_binding=campaign.initial_active_binding,
            ),
            genesis_sequence=41,
            genesis_head_sha256=genesis_head,
        )
        instance = journal.RootOwnedPhysicalFullMatrixV4ReceiptJournal(
            journal.RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig(
                enabled=True,
                journal_binding_sha256=campaign_digest,
                campaign_binding=campaign,
                anchor_genesis_sequence=41,
                anchor_genesis_head_sha256=genesis_head,
            ),
            witness_anchor=anchor,
            trusted_clock=self.clock,
        )
        projection = instance.verify_campaign_continuity(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            completed_sequence=0,
            active_binding=_binding(),
        )
        self.assertEqual(41, projection.anchor_sequence)
        self.assertEqual(genesis_head, projection.anchor_head_sha256)

    def test_campaign_continuity_rejects_wrong_baseline_effect_start_and_anchor_pending(self) -> None:
        wrong_binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            **{**_binding().__dict__, "writer_epoch": 8}
        )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "CONTINUITY_MISMATCH",
        ):
            self.instance.verify_campaign_continuity(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                completed_sequence=0,
                active_binding=wrong_binding,
            )

        # CLAIMED has no external effect commitment.  A restart may prove the
        # unchanged completed prefix and obtain the same live claim again.
        claim = self._claim()
        restarted_before_effect = self._journal()
        zero = restarted_before_effect.verify_campaign_continuity(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            completed_sequence=0,
            active_binding=_binding(),
        )
        self.assertEqual(0, zero.completed_sequence)
        reissued = self._claim(restarted_before_effect)
        self.assertFalse(reissued.indeterminate)
        self.assertEqual(claim.claim_id, reissued.claim_id)

        # This branch has an externally committed start but no corresponding
        # local record: neither a restart nor continuity gate may fill it in.
        with mock.patch.object(journal, "_append_record", side_effect=RuntimeError("crash")):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                restarted_before_effect.mark_effect_started(
                    claim=reissued,
                    effect_key=EFFECT_KEY,
                )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "CONTINUITY_ANCHOR_PENDING",
        ):
            self._journal().verify_campaign_continuity(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                completed_sequence=0,
                active_binding=_binding(),
            )

    def test_campaign_continuity_projection_does_not_accept_forged_raw_receipt_state(self) -> None:
        forged = journal.VerifiedPhysicalFullMatrixV4CampaignContinuity(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            completed_sequence=0,
            active_binding=_binding(),
            journal_binding_sha256=self.journal_binding,
            baseline_plan_binding_sha256=self.baseline_binding,
            anchor_sequence=0,
            anchor_head_sha256="0" * 64,
        )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "CONTINUITY_PROVENANCE_INVALID",
        ):
            journal.require_verified_physical_full_matrix_v4_campaign_continuity(forged)

    def test_current_driver_campaign_gate_protocol_accepts_the_journal_directly(self) -> None:
        snapshot = driver._PlanSnapshot(
            canonical_plan=b"",
            plan_sha256=PLAN_SHA256,
            run_id=RUN_ID,
            binding=driver._snapshot_binding(_binding(), direction=("webapp_fi", "webapp_ir")),
            phases=driver._phase_snapshots(),
            maximum_oracle_age_seconds=1,
        )
        adapters = driver.PhysicalFullMatrixV4ExecutionAdapters(
            campaign_continuity_gate=self.instance,
            trusted_clock=self.clock,
        )
        # The driver sees only the current Protocol method.  It never passes
        # a raw receipt or gains access to the journal's file/anchor state.
        driver._require_campaign_continuity(
            adapters=adapters,
            snapshot=snapshot,
            completed_sequence=0,
            active=snapshot.binding,
            floor=NOW,
        )

    def test_crash_after_external_effect_start_is_anchor_pending_and_never_retryable(self) -> None:
        claim = self._claim()
        with mock.patch.object(journal, "_append_record", side_effect=RuntimeError("simulated crash")):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                self.instance.mark_effect_started(claim=claim, effect_key=EFFECT_KEY)
        self.assertEqual(["effect-started"], [item.event for item in self.anchor.history])

        restarted = self._journal()
        pending = self._claim(restarted)
        self.assertTrue(pending.indeterminate)
        self.assertIsNone(pending.claim_id)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_PENDING",
        ):
            restarted.read_receipts(run_id=RUN_ID)
        self.assertEqual(1, len(self.anchor.history))

    def test_restart_with_local_effect_start_is_indeterminate_and_cannot_resume_adapter(self) -> None:
        self._start()
        self.assertEqual(["effect-started"], [item.event for item in self.anchor.history])
        restarted = self._journal()
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "EFFECT_INDETERMINATE",
        ):
            restarted.read_receipts(run_id=RUN_ID)
        resumed = self._claim(restarted)
        self.assertTrue(resumed.indeterminate)
        self.assertIsNone(resumed.claim_id)
        self.assertEqual(1, len(self.anchor.history))
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "EFFECT_START_NOT_LIVE",
        ):
            restarted.append_started(
                effect_start=driver.PhysicalFullMatrixV4EffectStart(
                    run_id=RUN_ID,
                    plan_sha256=PLAN_SHA256,
                    sequence=1,
                    phase_request_sha256=REQUEST_SHA256,
                    effect_key=EFFECT_KEY,
                    claim_id="pfm-v4-witness-journal-fake-000001",
                ),
                canonical_receipt=_receipt(),
            )

    def test_effect_start_anchor_projection_is_live_exact_and_cleared_on_completion(self) -> None:
        """The journal may project only its own current, read-back start.

        The projection deliberately carries public correlation pins rather
        than a journal handle or authority.  It must disappear once the
        matching receipt completes (and is never reconstructible after a
        restart), so a finished effect cannot be repurposed as an adapter
        invocation token.
        """

        request = _request_facts()
        absent = driver.PhysicalFullMatrixV4EffectStart(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=REQUEST_SHA256,
            effect_key=EFFECT_KEY,
            claim_id="pfm-v4-witness-journal-fake-000001",
        )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "EFFECT_START_ANCHOR_PROOF_NOT_LIVE",
        ):
            self.instance.project_effect_start_anchor_proof(
                effect_start=absent,
                request=request,
            )

        start = self._start()
        proof = self.instance.project_effect_start_anchor_proof(
            effect_start=start,
            request=request,
        )
        assert self.anchor.commitment is not None
        self.assertEqual(
            driver.PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA,
            proof.schema,
        )
        self.assertEqual(start.claim_id, proof.claim_id)
        self.assertEqual(
            driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(start),
            proof.journaled_effect_start_identity_sha256,
        )
        self.assertEqual(self.journal_binding, proof.journal_binding_sha256)
        self.assertEqual(self.baseline_binding, proof.baseline_plan_binding_sha256)
        self.assertEqual(0, proof.anchor_genesis_sequence)
        self.assertEqual(1, proof.anchor_sequence)
        self.assertEqual(self.anchor.head_sha256, proof.anchor_head_sha256)
        self.assertEqual(self.anchor.commitment_sha256, proof.anchor_commitment_sha256)
        self.assertEqual(self.anchor.attestation_sha256, proof.anchor_attestation_sha256)
        self.assertEqual(
            self.anchor.commitment.local_event_sha256,
            proof.anchor_local_event_sha256,
        )
        self.assertFalse(proof.writer_authorized)
        self.assertFalse(proof.promotion_authorized)
        self.assertFalse(proof.execution_authorized)
        self.assertFalse(proof.full_matrix_authorized)
        self.assertFalse(hasattr(proof, "journal_path"))
        self.assertFalse(hasattr(proof, "state_root"))

        # A request that cannot name the exact durable start is rejected by
        # the journal before a proof reaches any adapter.
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "EFFECT_START_ANCHOR_PROOF_REQUEST_INVALID",
        ):
            self.instance.project_effect_start_anchor_proof(
                effect_start=start,
                request=replace(request, effect_key=_hash("wrong-effect-key")),
            )

        self.instance.append_started(effect_start=start, canonical_receipt=_receipt())
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "EFFECT_START_ANCHOR_PROOF_NOT_LIVE",
        ):
            self.instance.project_effect_start_anchor_proof(
                effect_start=start,
                request=request,
            )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "EFFECT_START_ANCHOR_PROOF_NOT_LIVE",
        ):
            self._journal().project_effect_start_anchor_proof(
                effect_start=start,
                request=request,
            )

    def test_predecessor_completion_anchor_projection_is_durable_and_restart_safe(self) -> None:
        """A new P2 start proves the exact durable P1 completion predecessor.

        The P1 in-memory state is intentionally discarded before P2 begins.
        Projection therefore has to reconstruct P1's typed receipt/start/
        completion facts from the create-only records and cross-check them to
        the newly re-read current Witness head, rather than trusting a cache.
        """

        phase1_request = _request_facts(sequence=1)
        phase1_claim = self.instance.claim_phase(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=phase1_request.phase_request_sha256,
            effect_key=phase1_request.effect_key,
        )
        phase1_start = self.instance.mark_effect_started(
            claim=phase1_claim,
            effect_key=phase1_request.effect_key,
        )
        phase1_receipt = _receipt(sequence=1)
        self.instance.append_started(
            effect_start=phase1_start,
            canonical_receipt=phase1_receipt,
        )

        # This is deliberately a fresh Journal object with no phase-one live
        # maps.  It can only project after it has made and read back the new
        # phase-two effect-start commitment.
        restarted = self._journal()
        phase2_request = _request_facts(sequence=2)
        phase2_claim = restarted.claim_phase(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=2,
            phase_request_sha256=phase2_request.phase_request_sha256,
            effect_key=phase2_request.effect_key,
        )
        phase2_start = restarted.mark_effect_started(
            claim=phase2_claim,
            effect_key=phase2_request.effect_key,
        )
        phase2_anchor = restarted.project_effect_start_anchor_proof(
            effect_start=phase2_start,
            request=phase2_request,
        )
        phase2_authority = driver._mint_effect_start_authority(
            effect_start=phase2_start,
            claim=phase2_claim,
            request=phase2_request,
        )
        start_bound_request = driver._adapter_request_with_effect_start_authority(
            request=phase2_request,
            authority=phase2_authority,
            anchor_proof=phase2_anchor,
        )
        proof = restarted.project_predecessor_phase_completion_anchor_proof(
            effect_start=phase2_start,
            request=start_bound_request,
        )
        adapter_request = driver._adapter_request_with_effect_start_authority(
            request=phase2_request,
            authority=phase2_authority,
            anchor_proof=phase2_anchor,
            predecessor_phase_completion_anchor_proof=proof,
        )
        checked = driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
            request=adapter_request
        )
        expected_receipt = driver.parse_physical_full_matrix_v4_run_receipt(
            phase1_receipt
        )
        self.assertIs(proof, checked)
        self.assertEqual(1, proof.predecessor_phase_sequence)
        self.assertEqual(
            driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0].name,
            proof.predecessor_phase_name,
        )
        self.assertEqual(phase1_request.effect_key, proof.predecessor_effect_key)
        self.assertEqual(
            driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(
                phase1_start
            ),
            proof.predecessor_effect_start_identity_sha256,
        )
        self.assertEqual(
            expected_receipt.receipt_sha256,
            proof.predecessor_completion_receipt_sha256,
        )
        self.assertEqual(
            proof.predecessor_effect_start_anchor_sequence,
            proof.predecessor_completion_anchor_previous_sequence,
        )
        self.assertEqual(
            proof.predecessor_effect_start_anchor_head_sha256,
            proof.predecessor_completion_anchor_previous_head_sha256,
        )
        self.assertEqual(
            proof.predecessor_completion_anchor_sequence,
            proof.successor_effect_start_anchor_previous_sequence,
        )
        self.assertEqual(
            proof.predecessor_completion_anchor_head_sha256,
            proof.successor_effect_start_anchor_previous_head_sha256,
        )
        self.assertEqual(phase2_anchor.anchor_sequence, proof.successor_effect_start_anchor_sequence)
        self.assertEqual(phase2_anchor.anchor_head_sha256, proof.successor_effect_start_anchor_head_sha256)
        self.assertFalse(proof.writer_authorized)
        self.assertFalse(proof.promotion_authorized)
        self.assertFalse(proof.execution_authorized)
        self.assertFalse(proof.full_matrix_authorized)
        self.assertFalse(hasattr(proof, "canonical_receipt"))
        self.assertFalse(hasattr(proof, "journal_path"))
        object.__setattr__(
            proof,
            "predecessor_completion_anchor_head_sha256",
            _hash("tampered-completion-anchor"),
        )
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_TAMPERED",
        ):
            driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
                request=adapter_request
            )

    def test_predecessor_completion_proof_is_unavailable_without_successor_start(self) -> None:
        """A completed P1 alone cannot mint an adapter-facing bridge."""

        self._complete()
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "PREDECESSOR_COMPLETION_ANCHOR_PROOF_INVALID",
        ):
            self.instance.project_predecessor_phase_completion_anchor_proof(
                effect_start=driver.PhysicalFullMatrixV4EffectStart(
                    run_id=RUN_ID,
                    plan_sha256=PLAN_SHA256,
                    sequence=2,
                    phase_request_sha256=_request_facts(
                        sequence=2
                    ).phase_request_sha256,
                    effect_key=_request_facts(sequence=2).effect_key,
                    claim_id="pfm-v4-witness-journal-fake-000002",
                ),
                request=_request_facts(sequence=2),
            )

    def test_local_snapshot_rollback_after_completion_is_detected_by_external_head(self) -> None:
        completed = self._complete()
        completion_file = sorted((self.root / "records").glob("*.json"))[-1]
        completion_file.unlink()
        restarted = self._journal()
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_PENDING",
        ):
            restarted.read_receipts(run_id=RUN_ID)
        result = self._claim(restarted)
        self.assertTrue(result.indeterminate)
        self.assertEqual(2, len(self.anchor.history))
        self.assertNotEqual((), (completed,))

    def test_locally_anchored_tail_rejects_remote_genesis_and_sequence_gap(self) -> None:
        """A fresh adapter cannot make the journal forget its anchored tail."""

        self._complete()
        assert self.anchor.commitment is not None
        original = (
            self.anchor.sequence,
            self.anchor.head_sha256,
            self.anchor.previous_head_sha256,
            self.anchor.commitment_sha256,
            self.anchor.attestation_sha256,
            self.anchor.commitment,
        )

        # This models a newly constructed adapter whose external service has
        # (incorrectly or maliciously) returned the configured genesis.  The
        # journal's local anchored state, not the adapter process cache, is
        # the rollback fence.
        self.anchor.sequence = 0
        self.anchor.head_sha256 = "0" * 64
        self.anchor.previous_head_sha256 = "0" * 64
        self.anchor.commitment_sha256 = "0" * 64
        self.anchor.attestation_sha256 = "0" * 64
        self.anchor.commitment = None
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_ROLLBACK_OR_DIVERGENCE",
        ):
            self._journal().read_receipts(run_id=RUN_ID)

        (
            self.anchor.sequence,
            self.anchor.head_sha256,
            self.anchor.previous_head_sha256,
            self.anchor.commitment_sha256,
            self.anchor.attestation_sha256,
            self.anchor.commitment,
        ) = original
        assert self.anchor.commitment is not None
        gap_previous_head = _hash("remote-gap-previous-head")
        gap_commitment = replace(
            self.anchor.commitment,
            previous_anchor_sequence=self.anchor.sequence + 1,
            previous_anchor_head_sha256=gap_previous_head,
        )
        # The fake head is internally well-formed, but intentionally skips
        # the one predecessor the local journal has pinned.  It is therefore
        # not dismissed merely as a malformed adapter value.
        self.anchor.sequence += 2
        self.anchor.previous_head_sha256 = gap_previous_head
        self.anchor.head_sha256 = _hash("remote-gap-head")
        self.anchor.commitment = gap_commitment
        self.anchor.commitment_sha256 = journal._commitment_sha256(gap_commitment)
        self.anchor.attestation_sha256 = _hash("remote-gap-attestation")
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_ROLLBACK_OR_DIVERGENCE",
        ):
            self._journal().read_receipts(run_id=RUN_ID)

    def test_wrong_effect_key_and_mismatched_post_append_head_fail_closed(self) -> None:
        claim = self._claim()
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "CLAIM_NOT_LIVE",
        ):
            self.instance.mark_effect_started(claim=claim, effect_key="e" * 64)
        self.assertEqual([], self.anchor.history)

        self.anchor.return_wrong_append_head = True
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_APPEND_NOT_DURABLE",
        ):
            self.instance.mark_effect_started(claim=claim, effect_key=EFFECT_KEY)
        self.assertEqual(["effect-started"], [item.event for item in self.anchor.history])
        self.assertTrue(self._claim(self._journal()).indeterminate)

    def test_current_anchor_head_must_match_the_last_local_anchored_commitment(self) -> None:
        self._complete()
        assert self.anchor.commitment is not None
        # Model a broken anchor adapter that returns a typed, internally
        # self-consistent commitment under a reused head identifier.  The
        # journal must still bind that remote tail to its local record.
        self.anchor.commitment = replace(
            self.anchor.commitment,
            claim_id="pfm-v4-witness-journal-different-000001",
        )
        self.anchor.commitment_sha256 = journal._commitment_sha256(self.anchor.commitment)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_LOCAL_MISMATCH",
        ):
            self._journal().read_receipts(run_id=RUN_ID)

    def test_anchor_commitment_digest_is_the_exact_authoritative_wire_digest(self) -> None:
        self._complete()
        assert self.anchor.commitment is not None
        wire_commitment = journal._wire_commitment(
            self.anchor.commitment,
            code="test",
        )
        self.assertEqual(
            wire.physical_full_matrix_v4_witness_anchor_phase_name(
                self.anchor.commitment.phase_sequence
            ),
            wire_commitment.phase,
        )
        self.assertEqual(
            wire.derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(
                wire_commitment
            ),
            self.anchor.commitment_sha256,
        )
        self.assertEqual(
            self.anchor.commitment_sha256,
            journal._commitment_sha256(self.anchor.commitment),
        )

    def test_wire_commitment_digest_and_genesis_mismatches_fail_closed(self) -> None:
        self._complete()
        assert self.anchor.commitment is not None
        self.anchor.commitment_sha256 = _hash("not-the-signed-wire-digest")
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_INVALID",
        ):
            self._journal().read_receipts(run_id=RUN_ID)

        self.anchor.commitment = replace(
            self.anchor.commitment,
            anchor_genesis_head_sha256=_hash("foreign-wire-genesis"),
        )
        self.anchor.commitment_sha256 = journal._commitment_sha256(self.anchor.commitment)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_INVALID",
        ):
            self._journal().read_receipts(run_id=RUN_ID)

    def test_wire_phase_name_cannot_be_substituted_while_deriving_anchor_digest(self) -> None:
        self._start()
        assert self.anchor.commitment is not None
        with mock.patch.object(
            wire,
            "physical_full_matrix_v4_witness_anchor_phase_name",
            return_value="not-a-v4-phase",
        ):
            with self.assertRaisesRegex(
                journal.PhysicalFullMatrixV4ReceiptJournalError,
                "ANCHOR_INVALID",
            ):
                journal._commitment_sha256(self.anchor.commitment)

    def test_faulted_anchor_read_before_claim_leaves_no_effect_start(self) -> None:
        """An unavailable external head cannot create a local retry ambiguity."""

        with mock.patch.object(
            self.anchor,
            "read_head",
            side_effect=RuntimeError("injected anchor-read outage"),
        ), self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_READ_FAILED",
        ):
            self._claim()

        self.assertEqual([], self.anchor.history)
        self.assertEqual((), self.instance.read_receipts(run_id=RUN_ID))
        # No externally committed start exists, so a fresh root-journal
        # instance can safely reissue the incomplete claim.
        reissued = self._claim(self._journal())
        self.assertFalse(reissued.indeterminate)
        self.assertIsNotNone(reissued.claim_id)

    def test_faulted_anchor_append_before_commit_keeps_claim_safely_reclaimable(self) -> None:
        """A callback failure before Witness persistence is not an effect start."""

        claim = self._claim()
        with mock.patch.object(
            self.anchor,
            "append_commitment",
            side_effect=RuntimeError("injected pre-commit append outage"),
        ), self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_APPEND_FAILED",
        ):
            self.instance.mark_effect_started(claim=claim, effect_key=EFFECT_KEY)

        self.assertEqual([], self.anchor.history)
        self.assertEqual((), self.instance.read_receipts(run_id=RUN_ID))
        reissued = self._claim(self._journal())
        self.assertFalse(reissued.indeterminate)
        self.assertEqual(claim.claim_id, reissued.claim_id)

    def test_lost_anchor_append_response_after_commit_is_indeterminate(self) -> None:
        """An external start with no accepted callback response is never retried."""

        claim = self._claim()
        original_append = self.anchor.append_commitment

        def commit_then_lose_response(*, commitment):
            original_append(commitment=commitment)
            raise RuntimeError("injected lost append response")

        with mock.patch.object(
            self.anchor,
            "append_commitment",
            side_effect=commit_then_lose_response,
        ), self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_APPEND_FAILED",
        ):
            self.instance.mark_effect_started(claim=claim, effect_key=EFFECT_KEY)

        self.assertEqual(["effect-started"], [item.event for item in self.anchor.history])
        restarted = self._journal()
        resumed = self._claim(restarted)
        self.assertTrue(resumed.indeterminate)
        self.assertIsNone(resumed.claim_id)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_PENDING",
        ):
            restarted.read_receipts(run_id=RUN_ID)

    def test_post_append_durable_reread_fault_is_indeterminate(self) -> None:
        """The journal refuses to trust an append response without a reread."""

        claim = self._claim()
        original_read = self.anchor.read_head

        def fail_only_the_post_append_reread(**kwargs):
            if kwargs["expected_anchor_sequence"] == 1:
                raise RuntimeError("injected post-append reread outage")
            return original_read(**kwargs)

        with mock.patch.object(
            self.anchor,
            "read_head",
            side_effect=fail_only_the_post_append_reread,
        ), self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_READ_FAILED",
        ):
            self.instance.mark_effect_started(claim=claim, effect_key=EFFECT_KEY)

        self.assertEqual(["effect-started"], [item.event for item in self.anchor.history])
        restarted = self._journal()
        resumed = self._claim(restarted)
        self.assertTrue(resumed.indeterminate)
        self.assertIsNone(resumed.claim_id)

    def test_completion_local_write_fault_after_witness_commit_is_indeterminate(self) -> None:
        """A receipt never becomes a retry permit after a split external/local commit."""

        start = self._start()
        with mock.patch.object(
            journal,
            "_append_record",
            side_effect=RuntimeError("injected local completion-write outage"),
        ), self.assertRaisesRegex(RuntimeError, "completion-write outage"):
            self.instance.append_started(
                effect_start=start,
                canonical_receipt=_receipt(),
            )

        self.assertEqual(
            ["effect-started", "completed"],
            [item.event for item in self.anchor.history],
        )
        restarted = self._journal()
        resumed = self._claim(restarted)
        self.assertTrue(resumed.indeterminate)
        self.assertIsNone(resumed.claim_id)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixV4ReceiptJournalError,
            "ANCHOR_PENDING",
        ):
            restarted.read_receipts(run_id=RUN_ID)

    def test_static_boundary_does_not_reuse_old_journals_or_add_transport_clients(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse(
            any(
                name.endswith("physical_full_matrix_receipt_journal")
                or name.endswith("physical_full_matrix_execution_driver")
                or name.endswith("physical_full_matrix_execution_driver_v3")
                for name in imported
            ),
            imported,
        )
        forbidden = {"boto3", "paramiko", "requests", "socket", "subprocess", "urllib"}
        self.assertFalse(any(name.split(".")[0] in forbidden for name in imported), imported)
