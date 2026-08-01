"""Focused fail-closed tests for the isolated operational Witness ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1 as wire
from core import physical_operational_failover_v1_witness_ledger as subject


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _id(prefix: str) -> str:
    return prefix + "-" + "x" * 24


def _nonce(letter: str) -> str:
    return letter * 24


def _sha(letter: str) -> str:
    return letter * 64


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now


class _Store:
    def __init__(self) -> None:
        self.current: subject.PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None = None
        self.fail_next_cas = False
        self.calls: list[tuple[int, str]] = []

    def read_current(self) -> subject.PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None:
        return self.current

    def append_compare_and_swap(
        self,
        *,
        expected_version: int,
        expected_head_sha256: str,
        entry: subject.PhysicalOperationalFailoverV1WitnessLedgerEntry,
        next_state: subject.PhysicalOperationalFailoverV1WitnessLedgerState,
    ) -> bool:
        self.calls.append((expected_version, expected_head_sha256))
        if self.fail_next_cas:
            self.fail_next_cas = False
            return False
        actual_version = 0 if self.current is None else self.current.version
        actual_head = "0" * 64 if self.current is None else self.current.head_sha256
        if expected_version != actual_version or expected_head_sha256 != actual_head:
            return False
        self.current = subject.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
            version=entry.sequence,
            head_sha256=entry.entry_sha256,
            entry=entry,
            state=next_state,
        )
        return True


class _Issuer:
    def __init__(self, key: Ed25519PrivateKey) -> None:
        self.key = key

    def issue_witness_promotion_grant(self, **kwargs: object) -> bytes:
        return wire.sign_physical_operational_failover_v1_witness_promotion_grant(
            value=kwargs["value"],  # type: ignore[arg-type]
            config=kwargs["verification_config"],  # type: ignore[arg-type]
            private_key=self.key,
            now=kwargs["now"],  # type: ignore[arg-type]
            expected_request=kwargs["expected_request"],  # type: ignore[arg-type]
        )


class PhysicalOperationalFailoverV1WitnessLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi_key = Ed25519PrivateKey.generate()
        self.ir_request_key = Ed25519PrivateKey.generate()
        self.witness_key = Ed25519PrivateKey.generate()
        self.ir_completion_key = Ed25519PrivateKey.generate()
        self.pins = wire.PhysicalOperationalFailoverV1Pins(
            cluster_id="gold-trade-three-site-prod",
            release_sha="a" * 40,
            stream_generation_id=_id("stream-generation"),
            route_binding_sha256=_sha("b"),
            baseline_generation_id=_id("baseline-generation"),
            baseline_manifest_sha256=_sha("c"),
            recovery_frontier_wal_lsn="0/20",
            blob_frontier_wal_lsn="0/30",
        )
        self.verification = wire.PhysicalOperationalFailoverV1VerificationConfig(
            pins=self.pins,
            fi_self_fence_signer_public_key=_public(self.fi_key),
            ir_promotion_request_signer_public_key=_public(self.ir_request_key),
            witness_term_signer_public_key=_public(self.witness_key),
            ir_promotion_completion_signer_public_key=_public(self.ir_completion_key),
            enabled=True,
            maximum_evidence_age_seconds=60,
        )
        self.fi_term = wire.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id="writer-lease-73",
            witness_transition_id=_id("fi-transition"),
            witnessed_term_proof_sha256=_sha("d"),
            issued_at=NOW - timedelta(seconds=20),
            expires_at=NOW + timedelta(seconds=40),
        )
        self.ir_term = wire.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_ir",
            writer_epoch=42,
            writer_lease_id=_id("ir-lease"),
            witness_transition_id=_id("ir-transition"),
            witnessed_term_proof_sha256=_sha("e"),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=50),
        )
        self.clock = _Clock()
        self.store = _Store()
        self.config = subject.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig(
            enabled=True,
            verification_config=self.verification,
            initial_fi_term=self.fi_term,
        )
        self.ledger = subject.RootOwnedPhysicalOperationalFailoverV1WitnessLedger(
            config=self.config,
            durable_store=self.store,
            clock=self.clock,
        )

    def _fence(self, **changes: object) -> wire.VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt:
        values: dict[str, object] = {
            "receipt_id": _id("fi-fence"),
            "receipt_nonce": _nonce("a"),
            "issued_at": NOW,
            "expires_at": NOW + timedelta(seconds=50),
            "replay_key_sha256": _sha("f"),
            "pins": self.pins,
            "predecessor_term": self.fi_term,
            "fence_reason": "ack-unavailable",
            "last_final_ack_sha256": _sha("1"),
            "last_committed_frontier_wal_lsn": "0/20",
        }
        values.update(changes)
        raw = wire.sign_physical_operational_failover_v1_fi_self_fence_receipt(
            value=wire.PhysicalOperationalFailoverV1FiSelfFenceReceiptInput(**values),
            config=self.verification,
            private_key=self.fi_key,
            now=NOW,
        )
        return wire.verify_physical_operational_failover_v1_fi_self_fence_receipt(
            raw, config=self.verification, now=NOW
        )

    def _request(
        self,
        fence: wire.VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt,
        **changes: object,
    ) -> wire.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
        values: dict[str, object] = {
            "request_id": _id("ir-promotion-request"),
            "request_nonce": _nonce("b"),
            "issued_at": NOW,
            "expires_at": NOW + timedelta(seconds=50),
            "replay_key_sha256": _sha("2"),
            "pins": self.pins,
            "predecessor_term": self.fi_term,
            "predecessor_termination_reason": "fi-self-fence-receipt",
            "fi_self_fence_receipt_sha256": fence.receipt_sha256,
            "recovery_evidence_sha256": _sha("3"),
            "p0_policy_bundle_sha256": _sha("4"),
        }
        values.update(changes)
        raw = wire.sign_physical_operational_failover_v1_ir_promotion_request(
            value=wire.PhysicalOperationalFailoverV1IrPromotionRequestInput(**values),
            config=self.verification,
            private_key=self.ir_request_key,
            now=NOW,
        )
        return wire.verify_physical_operational_failover_v1_ir_promotion_request(
            raw, config=self.verification, now=NOW
        )

    def _reservation(self, **changes: object) -> subject.PhysicalOperationalFailoverV1WitnessGrantReservation:
        values: dict[str, object] = {
            "grant_id": _id("witness-promotion-grant"),
            "grant_nonce": _nonce("c"),
            "grant_replay_key_sha256": _sha("5"),
            "issued_at": NOW,
            "expires_at": NOW + timedelta(seconds=50),
            "successor_term": self.ir_term,
            "activation_route_artifact_sha256": _sha("6"),
            "activation_receiver_permit_sha256": _sha("7"),
        }
        values.update(changes)
        return subject.PhysicalOperationalFailoverV1WitnessGrantReservation(**values)

    def _completion(
        self,
        grant: wire.VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant,
    ) -> wire.VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion:
        raw = wire.sign_physical_operational_failover_v1_ir_promotion_completion(
            value=wire.PhysicalOperationalFailoverV1IrPromotionCompletionInput(
                completion_id=_id("ir-promotion-completion"),
                completion_nonce=_nonce("d"),
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=50),
                replay_key_sha256=_sha("a"),
                pins=self.pins,
                predecessor_term=self.fi_term,
                predecessor_termination_reason="fi-self-fence-receipt",
                fi_self_fence_receipt_sha256=grant.fi_self_fence_receipt_sha256,
                grant_sha256=grant.grant_sha256,
                grant_id=grant.grant_id,
                grant_nonce=grant.grant_nonce,
                successor_term=self.ir_term,
                activation_route_artifact_sha256=grant.activation_route_artifact_sha256,
                activation_receiver_permit_sha256=grant.activation_receiver_permit_sha256,
                promotion_record_sha256=_sha("b"),
                recovery_evidence_sha256=_sha("c"),
                p0_execution_sha256=_sha("d"),
                traffic_fence_receipt_sha256=_sha("e"),
            ),
            config=self.verification,
            private_key=self.ir_completion_key,
            now=NOW,
            expected_grant=grant,
        )
        return wire.verify_physical_operational_failover_v1_ir_promotion_completion(
            raw, config=self.verification, now=NOW, expected_grant=grant
        )

    def _to_issued(self) -> tuple[
        subject.PhysicalOperationalFailoverV1WitnessLedgerSnapshot,
        wire.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
        wire.VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant,
    ]:
        active = self.ledger.bootstrap_normal_fi_term()
        fence = self._fence()
        request = self._request(fence)
        fenced = self.ledger.fence_or_expire_fi(
            expected_version=active.version,
            expected_head_sha256=active.head_sha256,
            request=request,
            fi_self_fence_receipt=fence,
        )
        pending = self.ledger.reserve_ir_promotion(
            expected_version=fenced.version,
            expected_head_sha256=fenced.head_sha256,
            request=request,
            reservation=self._reservation(),
        )
        issued, grant = self.ledger.issue_reserved_ir_promotion_grant(
            expected_version=pending.version,
            expected_head_sha256=pending.head_sha256,
            issuer=_Issuer(self.witness_key),
        )
        return issued, request, grant

    def test_default_off_root_guard_and_full_path_has_at_most_one_active_term(self) -> None:
        disabled = subject.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig(
            verification_config=self.verification,
            initial_fi_term=self.fi_term,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "CONFIG_INVALID"):
            subject.RootOwnedPhysicalOperationalFailoverV1WitnessLedger(
                config=disabled, durable_store=_Store(), clock=_Clock()
            )
        with mock.patch.object(subject.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "ROOT_RUNTIME_REQUIRED"):
                subject.RootOwnedPhysicalOperationalFailoverV1WitnessLedger(
                    config=self.config, durable_store=_Store(), clock=_Clock()
                )
        issued, request, grant = self._to_issued()
        self.assertEqual(issued.state.phase, "ir-grant-issued")
        self.assertIsNone(issued.state.active_term)
        completed = self.ledger.complete_ir_promotion(
            expected_version=issued.version,
            expected_head_sha256=issued.head_sha256,
            request=request,
            grant=grant,
            completion=self._completion(grant),
        )
        self.assertEqual(completed.state.phase, "ir-active")
        self.assertEqual(completed.state.active_term, self.ir_term)
        self.assertEqual(completed.state.active_term.holder_site, "webapp_ir")

    def test_conflicting_cas_and_replay_are_fail_closed(self) -> None:
        active = self.ledger.bootstrap_normal_fi_term()
        fence = self._fence()
        request = self._request(fence)
        self.store.fail_next_cas = True
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "CAS_CONFLICT"):
            self.ledger.fence_or_expire_fi(
                expected_version=active.version,
                expected_head_sha256=active.head_sha256,
                request=request,
                fi_self_fence_receipt=fence,
            )
        self.assertEqual(self.store.current, active)
        fenced = self.ledger.fence_or_expire_fi(
            expected_version=active.version,
            expected_head_sha256=active.head_sha256,
            request=request,
            fi_self_fence_receipt=fence,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "FI_NOT_ACTIVE"):
            self.ledger.fence_or_expire_fi(
                expected_version=fenced.version,
                expected_head_sha256=fenced.head_sha256,
                request=request,
                fi_self_fence_receipt=fence,
            )

    def test_wrong_receipt_or_new_request_cannot_cross_the_fenced_head(self) -> None:
        active = self.ledger.bootstrap_normal_fi_term()
        fence = self._fence()
        request = self._request(fence)
        wrong_fence = self._fence(receipt_id=_id("other-fi-fence"), receipt_nonce=_nonce("z"), replay_key_sha256=_sha("9"))
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "FI_RECEIPT_MISMATCH"):
            self.ledger.fence_or_expire_fi(
                expected_version=active.version,
                expected_head_sha256=active.head_sha256,
                request=request,
                fi_self_fence_receipt=wrong_fence,
            )
        fenced = self.ledger.fence_or_expire_fi(
            expected_version=active.version,
            expected_head_sha256=active.head_sha256,
            request=request,
            fi_self_fence_receipt=fence,
        )
        other_request = self._request(
            fence,
            request_id=_id("other-ir-request"),
            request_nonce=_nonce("y"),
            replay_key_sha256=_sha("8"),
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "REQUEST_STATE_MISMATCH"):
            self.ledger.reserve_ir_promotion(
                expected_version=fenced.version,
                expected_head_sha256=fenced.head_sha256,
                request=other_request,
                reservation=self._reservation(),
            )

    def test_stale_and_rollback_clock_fail_closed(self) -> None:
        active = self.ledger.bootstrap_normal_fi_term()
        self.clock.now = NOW - timedelta(seconds=1)
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "CLOCK_REGRESSION"):
            self.ledger.fence_or_expire_fi(
                expected_version=active.version,
                expected_head_sha256=active.head_sha256,
                request=self._request(self._fence()),
                fi_self_fence_receipt=self._fence(),
            )
        self.clock.now = NOW + timedelta(seconds=61)
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "REQUEST_INVALID"):
            self.ledger.fence_or_expire_fi(
                expected_version=active.version,
                expected_head_sha256=active.head_sha256,
                request=self._request(self._fence()),
                fi_self_fence_receipt=self._fence(),
            )
        self.clock.now = NOW + timedelta(seconds=301)
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessLedgerError, "CLOCK_STEP_UNSAFE"):
            self.ledger.fence_or_expire_fi(
                expected_version=active.version,
                expected_head_sha256=active.head_sha256,
                request=self._request(self._fence()),
                fi_self_fence_receipt=self._fence(),
            )

    def test_expiry_path_uses_trusted_persisted_clock_and_never_keeps_fi_active(self) -> None:
        active = self.ledger.bootstrap_normal_fi_term()
        later = NOW + timedelta(seconds=41)
        self.clock.now = later
        raw = wire.sign_physical_operational_failover_v1_ir_promotion_request(
            value=wire.PhysicalOperationalFailoverV1IrPromotionRequestInput(
                request_id=_id("expiry-ir-request"),
                request_nonce=_nonce("q"),
                issued_at=later,
                expires_at=later + timedelta(seconds=50),
                replay_key_sha256=_sha("9"),
                pins=self.pins,
                predecessor_term=self.fi_term,
                predecessor_termination_reason="predecessor-term-expired",
                fi_self_fence_receipt_sha256=None,
                recovery_evidence_sha256=_sha("3"),
                p0_policy_bundle_sha256=_sha("4"),
            ),
            config=self.verification,
            private_key=self.ir_request_key,
            now=later,
        )
        request = wire.verify_physical_operational_failover_v1_ir_promotion_request(
            raw, config=self.verification, now=later
        )
        expired = self.ledger.fence_or_expire_fi(
            expected_version=active.version,
            expected_head_sha256=active.head_sha256,
            request=request,
        )
        self.assertEqual(expired.state.phase, "fi-expired")
        self.assertIsNone(expired.state.active_term)
        self.assertEqual(expired.state.clock_floor, later)

    def test_source_boundary_excludes_old_matrix_and_peer_transport_surfaces(self) -> None:
        source = Path(subject.__file__).read_text(encoding="utf-8").lower()
        for forbidden in (
            "physical_wal_v2",
            "physical_full_matrix_v4",
            "boto3",
            "botocore",
            "object storage",
            "object-storage",
            "ssh",
            "scp",
            "sftp",
            "rsync",
            "subprocess",
            "socket",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("webapp_fi -> webapp_ir", source)
        self.assertNotIn("webapp_ir -> webapp_fi", source)
