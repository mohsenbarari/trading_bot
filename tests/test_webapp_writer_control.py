import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.runtime_identity import RuntimeIdentity
from core.webapp_writer_control import (
    ACTION_ACTIVATE,
    ACTION_APPROVE,
    ACTION_FENCE,
    ACTION_LEASE_REFRESH,
    CONTROL_ACTIVE,
    CONTROL_FENCED,
    REQUIRED_READY_EVIDENCE_FLAGS,
    WriterControlError,
    WriterStateSnapshot,
    snapshot_is_local_active,
    transition_writer_state,
    validate_readiness_evidence,
)
from core.writer_witness_contract import ValidatedWitnessLeaseProof
from core.writer_lease_clock import LeaseClockEvidence


NOW = datetime(2026, 7, 14, 21, 0, tzinfo=timezone.utc)


def webapp_identity(site: str = "webapp_fi") -> RuntimeIdentity:
    return RuntimeIdentity(
        logical_authority="webapp",
        physical_site=site,
        legacy_server_mode="iran",
        compatibility_inferred=False,
    )


def evidence_payload(*, site: str = "webapp_fi", epoch: int = 2) -> dict:
    payload = {
        "evidence_id": "ready-20260714-001",
        "target_site": site,
        "writer_epoch": epoch,
        "generated_at": (NOW - timedelta(seconds=30)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
    }
    payload.update({name: True for name in REQUIRED_READY_EVIDENCE_FLAGS})
    return payload


def writer_state(*, active_site="webapp_fi", epoch=1, control_state=CONTROL_ACTIVE):
    return SimpleNamespace(
        authority="webapp",
        active_site=active_site,
        writer_epoch=epoch,
        control_state=control_state,
        transition_id="transition-before",
        readiness_evidence_hash=None,
        readiness_evidence_id=None,
        readiness_approved_by=None,
        readiness_approved_at=None,
        readiness_expires_at=None,
        updated_by="migration",
        reason="bootstrap",
        witness_lease_id=None,
        witness_lease_issued_at=None,
        witness_lease_expires_at=None,
        witness_proof_hash=None,
        witness_transition_id=None,
        witness_local_boot_id=None,
        witness_local_boottime_deadline=None,
        witness_observed_wall_at=None,
        witness_observed_boottime=None,
        witness_clock_offset_ms=None,
    )


class FakeSession:
    def __init__(self):
        self.added = []
        self.flush = AsyncMock()

    def add(self, value):
        self.added.append(value)


class ReadinessEvidenceTests(unittest.TestCase):
    def test_valid_evidence_is_canonicalized_and_hashed(self):
        evidence = validate_readiness_evidence(
            evidence_payload(),
            target_site="webapp_fi",
            writer_epoch=2,
            now=NOW,
            max_age_seconds=900,
        )

        self.assertEqual(evidence.evidence_id, "ready-20260714-001")
        self.assertEqual(len(evidence.content_hash), 64)
        self.assertEqual(evidence.writer_epoch, 2)

    def test_missing_gate_stale_or_wrong_term_fails_closed(self):
        missing_gate = evidence_payload()
        missing_gate["schema_compatible"] = False
        stale = evidence_payload()
        stale["generated_at"] = (NOW - timedelta(hours=1)).isoformat()

        cases = (
            (missing_gate, "webapp_fi", 2),
            (stale, "webapp_fi", 2),
            (evidence_payload(), "webapp_ir", 2),
            (evidence_payload(), "webapp_fi", 3),
        )
        for payload, site, epoch in cases:
            with self.subTest(site=site, epoch=epoch):
                with self.assertRaises(WriterControlError):
                    validate_readiness_evidence(
                        payload,
                        target_site=site,
                        writer_epoch=epoch,
                        now=NOW,
                        max_age_seconds=900,
                    )


class WriterTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fence_clears_local_writer_and_readiness(self):
        state = writer_state()
        state.readiness_evidence_hash = "a" * 64
        state.readiness_evidence_id = "old"
        state.readiness_approved_at = NOW
        state.readiness_expires_at = NOW + timedelta(minutes=5)
        session = FakeSession()

        with patch(
            "core.webapp_writer_control.load_writer_state",
            new=AsyncMock(return_value=state),
        ):
            snapshot = await transition_writer_state(
                session,
                action=ACTION_FENCE,
                identity=webapp_identity(),
                expected_epoch=1,
                expected_active_site="webapp_fi",
                operator="operator@example",
                reason="planned handoff",
                now=NOW,
            )

        self.assertEqual(snapshot.control_state, CONTROL_FENCED)
        self.assertIsNone(snapshot.active_site)
        self.assertEqual(snapshot.writer_epoch, 1)
        self.assertIsNone(snapshot.readiness_evidence_hash)
        self.assertEqual(session.added[0].action, ACTION_FENCE)
        session.flush.assert_awaited_once()

    async def test_activate_requires_fenced_state_and_increments_epoch(self):
        state = writer_state(active_site=None, epoch=4, control_state=CONTROL_FENCED)
        session = FakeSession()
        evidence = validate_readiness_evidence(
            evidence_payload(site="webapp_ir", epoch=5),
            target_site="webapp_ir",
            writer_epoch=5,
            now=NOW,
        )

        with patch(
            "core.webapp_writer_control.load_writer_state",
            new=AsyncMock(return_value=state),
        ):
            snapshot = await transition_writer_state(
                session,
                action=ACTION_ACTIVATE,
                identity=webapp_identity("webapp_ir"),
                expected_epoch=4,
                expected_active_site=None,
                operator="operator@example",
                reason="approved outage promotion",
                evidence=evidence,
                now=NOW,
            )

        self.assertEqual(snapshot.active_site, "webapp_ir")
        self.assertEqual(snapshot.writer_epoch, 5)
        self.assertEqual(snapshot.control_state, CONTROL_ACTIVE)
        self.assertEqual(snapshot.readiness_evidence_id, evidence.evidence_id)
        self.assertEqual(session.added[0].new_epoch, 5)

    async def test_stale_expectation_and_approval_on_nonlocal_site_are_rejected(self):
        state = writer_state(active_site="webapp_fi", epoch=7)
        session = FakeSession()
        evidence = validate_readiness_evidence(
            evidence_payload(site="webapp_ir", epoch=7),
            target_site="webapp_ir",
            writer_epoch=7,
            now=NOW,
        )

        with patch(
            "core.webapp_writer_control.load_writer_state",
            new=AsyncMock(return_value=state),
        ):
            with self.assertRaises(WriterControlError):
                await transition_writer_state(
                    session,
                    action=ACTION_APPROVE,
                    identity=webapp_identity("webapp_ir"),
                    expected_epoch=6,
                    expected_active_site="webapp_fi",
                    operator="operator@example",
                    reason="stale request",
                    evidence=evidence,
                    now=NOW,
                )

            with self.assertRaises(WriterControlError):
                await transition_writer_state(
                    session,
                    action=ACTION_APPROVE,
                    identity=webapp_identity("webapp_ir"),
                    expected_epoch=7,
                    expected_active_site="webapp_fi",
                    operator="operator@example",
                    reason="wrong local site",
                    evidence=evidence,
                    now=NOW,
                )

    def test_snapshot_requires_matching_active_site_and_fresh_approval(self):
        snapshot = WriterStateSnapshot(
            active_site="webapp_fi",
            writer_epoch=3,
            control_state=CONTROL_ACTIVE,
            transition_id="transition-current",
            readiness_evidence_hash="a" * 64,
            readiness_evidence_id="evidence-current",
            readiness_approved_by="operator@example",
            readiness_approved_at=NOW - timedelta(seconds=10),
            readiness_expires_at=NOW + timedelta(minutes=5),
        )

        active, reasons = snapshot_is_local_active(
            webapp_identity(),
            snapshot,
            now=NOW,
            require_readiness_evidence=True,
        )
        standby, standby_reasons = snapshot_is_local_active(
            webapp_identity("webapp_ir"),
            snapshot,
            now=NOW,
            require_readiness_evidence=True,
        )

        self.assertTrue(active)
        self.assertEqual(reasons, ())
        self.assertFalse(standby)
        self.assertIn("writer_active_site_mismatch", standby_reasons)

    async def test_lease_refresh_preserves_writer_transition_for_inflight_transactions(self):
        state = writer_state(active_site="webapp_fi", epoch=7)
        state.witness_lease_id = "lease-7"
        state.witness_lease_expires_at = NOW + timedelta(seconds=60)
        state.witness_proof_hash = "a" * 64
        state.witness_transition_id = "witness-before"
        session = FakeSession()
        proof = ValidatedWitnessLeaseProof(
            holder_site="webapp_fi",
            writer_epoch=7,
            lease_id="lease-7",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=180),
            witness_transition_id="witness-renewed",
            proof_hash="b" * 64,
            canonical_payload={},
            clock_evidence=LeaseClockEvidence(
                boot_id="12345678-1234-4234-8234-123456789abc",
                observed_wall_at=NOW,
                observed_boottime=100.0,
                boottime_deadline=265.0,
                witness_issue_offset_ms=0,
            ),
        )

        with patch(
            "core.webapp_writer_control.load_writer_state",
            new=AsyncMock(return_value=state),
        ):
            refreshed = await transition_writer_state(
                session,
                action=ACTION_LEASE_REFRESH,
                identity=webapp_identity(),
                expected_epoch=7,
                expected_active_site="webapp_fi",
                operator="operator@example",
                reason="scheduled lease refresh",
                witness_proof=proof,
                now=NOW,
            )

        self.assertEqual(refreshed.transition_id, "transition-before")
        self.assertEqual(refreshed.witness_proof_hash, "b" * 64)
        self.assertEqual(session.added[0].action, ACTION_LEASE_REFRESH)
        self.assertNotEqual(session.added[0].transition_id, refreshed.transition_id)
        self.assertEqual(session.added[0].witness_proof_hash, "b" * 64)

    def test_snapshot_witness_gate_uses_local_safety_margin(self):
        base = WriterStateSnapshot(
            active_site="webapp_fi",
            writer_epoch=3,
            control_state=CONTROL_ACTIVE,
            transition_id="transition-current",
            readiness_evidence_hash=None,
            readiness_evidence_id=None,
            readiness_approved_by=None,
            readiness_approved_at=None,
            readiness_expires_at=None,
            witness_lease_id="lease-3",
            witness_lease_issued_at=NOW,
            witness_lease_expires_at=NOW + timedelta(seconds=16),
            witness_proof_hash="a" * 64,
            witness_transition_id="witness-3",
            witness_local_boot_id="12345678-1234-4234-8234-123456789abc",
            witness_local_boottime_deadline=116.0,
            witness_observed_wall_at=NOW,
            witness_observed_boottime=100.0,
            witness_clock_offset_ms=0,
        )

        active, _ = snapshot_is_local_active(
            webapp_identity(),
            base,
            now=NOW,
            require_witness_lease=True,
            witness_safety_margin_seconds=15,
            current_boot_id="12345678-1234-4234-8234-123456789abc",
            current_boottime=100.0,
        )
        expired, reasons = snapshot_is_local_active(
            webapp_identity(),
            base,
            now=NOW - timedelta(hours=1),
            require_witness_lease=True,
            witness_safety_margin_seconds=15,
            current_boot_id="12345678-1234-4234-8234-123456789abc",
            current_boottime=116.0,
        )

        self.assertTrue(active)
        self.assertFalse(expired)
        self.assertIn("writer_witness_monotonic_deadline_expired", reasons)

    def test_clock_rollback_and_host_reboot_fail_closed(self):
        base = WriterStateSnapshot(
            active_site="webapp_fi",
            writer_epoch=3,
            control_state=CONTROL_ACTIVE,
            transition_id="transition-current",
            readiness_evidence_hash=None,
            readiness_evidence_id=None,
            readiness_approved_by=None,
            readiness_approved_at=None,
            readiness_expires_at=None,
            witness_lease_id="lease-3",
            witness_lease_issued_at=NOW,
            witness_lease_expires_at=NOW + timedelta(seconds=180),
            witness_proof_hash="a" * 64,
            witness_transition_id="witness-3",
            witness_local_boot_id="12345678-1234-4234-8234-123456789abc",
            witness_local_boottime_deadline=200.0,
            witness_observed_wall_at=NOW,
            witness_observed_boottime=100.0,
            witness_clock_offset_ms=0,
        )
        active, reasons = snapshot_is_local_active(
            webapp_identity(), base,
            now=NOW - timedelta(days=1),
            require_witness_lease=True,
            current_boot_id="12345678-1234-4234-8234-123456789abc",
            current_boottime=201.0,
        )
        self.assertFalse(active)
        self.assertIn("writer_witness_monotonic_deadline_expired", reasons)
        active, reasons = snapshot_is_local_active(
            webapp_identity(), base,
            now=NOW,
            require_witness_lease=True,
            current_boot_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            current_boottime=101.0,
        )
        self.assertFalse(active)
        self.assertIn("writer_witness_host_rebooted", reasons)


if __name__ == "__main__":
    unittest.main()
