"""Focused adversarial tests for the root-local V1 Witness ledger CAS store."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import ast
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1 as wire
from core import physical_operational_failover_v1_witness_ledger as ledger
from core import physical_operational_failover_v1_witness_ledger_durable_cas as subject


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_operational_failover_v1_witness_ledger_durable_cas.py"
)


def _id(prefix: str) -> str:
    return prefix + "-" + "x" * 24


def _sha(letter: str) -> str:
    return letter * 64


def _nonce(letter: str) -> str:
    return letter * 24


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


class _Checkpoint:
    """Test double for separate root-owned monotonic persistence."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str, str], tuple[int, str, str, str]] = {}
        self.calls: list[tuple[str, str, str, int, str, str, str]] = []

    def attest_v1_witness_ledger_state(self, **kwargs: object) -> None:
        key = (
            kwargs["binding_sha256"],
            kwargs["ledger_schema"],
            kwargs["initial_fi_term_sha256"],
        )
        value = (
            kwargs["sequence"],
            kwargs["previous_head_sha256"],
            kwargs["head_sha256"],
            kwargs["record_sha256"],
        )
        if not all(type(item) is str for item in key) or type(value[0]) is not int:
            raise RuntimeError("invalid checkpoint input")
        self.calls.append((*key, *value))
        previous = self.states.get(key)
        if previous is None:
            if value != (0, "0" * 64, "0" * 64, "0" * 64):
                raise RuntimeError("checkpoint must begin empty")
            self.states[key] = value
            return
        if previous == value:
            return
        if (
            value[0] == previous[0] + 1
            and value[1] == previous[2]
            and value[2] != previous[2]
            and value[3] != previous[3]
        ):
            self.states[key] = value
            return
        raise RuntimeError("checkpoint rejected rollback or branch")


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


class PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreTests(unittest.TestCase):
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
            writer_lease_id=_id("fi-lease"),
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
        self.ledger_config = ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig(
            enabled=True,
            verification_config=self.verification,
            initial_fi_term=self.fi_term,
        )
        self.store_config = subject.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreConfig(
            enabled=True,
            ledger_config=self.ledger_config,
        )
        self.checkpoint = _Checkpoint()
        self._temporary = tempfile.TemporaryDirectory(
            prefix="v1-witness-ledger-cas-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.state_root = Path(self._temporary.name) / "state"
        self.state_root.mkdir(mode=0o700)
        self.state_root.chmod(0o700)
        self._fixed_root = subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_ROOT
        subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_ROOT = self.state_root
        self.store = subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore(
            self.store_config,
            rollback_checkpoint=self.checkpoint,
        )

    def tearDown(self) -> None:
        subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_ROOT = self._fixed_root
        self._temporary.cleanup()

    def _state_and_entry(
        self,
        *,
        sequence: int,
        previous_head_sha256: str,
    ) -> tuple[
        ledger.PhysicalOperationalFailoverV1WitnessLedgerEntry,
        ledger.PhysicalOperationalFailoverV1WitnessLedgerState,
    ]:
        state = ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=sequence,
            phase="fi-active",
            clock_floor=NOW,
            active_term=self.fi_term,
            active_term_sha256=ledger._term_sha256(self.fi_term, code="TEST"),
        )
        entry = ledger._make_entry(
            sequence=sequence,
            previous_head_sha256=previous_head_sha256,
            observed_at=NOW,
            event="bootstrap-fi-active",
            state=state,
        )
        return entry, state

    def _append_initial(self) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        entry, state = self._state_and_entry(sequence=1, previous_head_sha256="0" * 64)
        self.assertTrue(
            self.store.append_compare_and_swap(
                expected_version=0,
                expected_head_sha256="0" * 64,
                entry=entry,
                next_state=state,
            )
        )
        current = self.store.read_current()
        self.assertIsNotNone(current)
        assert current is not None
        return current

    def _fence(self) -> wire.VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt:
        raw = wire.sign_physical_operational_failover_v1_fi_self_fence_receipt(
            value=wire.PhysicalOperationalFailoverV1FiSelfFenceReceiptInput(
                receipt_id=_id("fi-fence"),
                receipt_nonce=_nonce("a"),
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=50),
                replay_key_sha256=_sha("f"),
                pins=self.pins,
                predecessor_term=self.fi_term,
                fence_reason="ack-unavailable",
                last_final_ack_sha256=_sha("1"),
                last_committed_frontier_wal_lsn="0/20",
            ),
            config=self.verification,
            private_key=self.fi_key,
            now=NOW,
        )
        return wire.verify_physical_operational_failover_v1_fi_self_fence_receipt(
            raw,
            config=self.verification,
            now=NOW,
        )

    def _request(
        self,
        fence: wire.VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt,
    ) -> wire.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
        raw = wire.sign_physical_operational_failover_v1_ir_promotion_request(
            value=wire.PhysicalOperationalFailoverV1IrPromotionRequestInput(
                request_id=_id("ir-promotion-request"),
                request_nonce=_nonce("b"),
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=50),
                replay_key_sha256=_sha("2"),
                pins=self.pins,
                predecessor_term=self.fi_term,
                predecessor_termination_reason="fi-self-fence-receipt",
                fi_self_fence_receipt_sha256=fence.receipt_sha256,
                recovery_evidence_sha256=_sha("3"),
                p0_policy_bundle_sha256=_sha("4"),
            ),
            config=self.verification,
            private_key=self.ir_request_key,
            now=NOW,
        )
        return wire.verify_physical_operational_failover_v1_ir_promotion_request(
            raw,
            config=self.verification,
            now=NOW,
        )

    def _reservation(self) -> ledger.PhysicalOperationalFailoverV1WitnessGrantReservation:
        return ledger.PhysicalOperationalFailoverV1WitnessGrantReservation(
            grant_id=_id("witness-promotion-grant"),
            grant_nonce=_nonce("c"),
            grant_replay_key_sha256=_sha("5"),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=50),
            successor_term=self.ir_term,
            activation_route_artifact_sha256=_sha("6"),
            activation_receiver_permit_sha256=_sha("7"),
        )

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
            raw,
            config=self.verification,
            now=NOW,
            expected_grant=grant,
        )

    def test_ledger_bootstrap_is_durable_readback_and_survives_restart(self) -> None:
        service = ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedger(
            config=self.ledger_config,
            durable_store=self.store,
            clock=_Clock(),
        )
        snapshot = service.bootstrap_normal_fi_term()

        restarted = subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore(
            self.store_config,
            rollback_checkpoint=self.checkpoint,
        )
        self.assertEqual(snapshot, restarted.read_current())
        self.assertTrue((self.state_root / "entries").is_dir())
        self.assertEqual(1, len(list((self.state_root / "entries").glob("*.json"))))
        self.assertEqual(0o700, self.state_root.stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.state_root / "binding.json").stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.state_root / "current.json").stat().st_mode & 0o777)

    def test_full_ledger_state_round_trips_canonical_request_and_reservation(self) -> None:
        service = ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedger(
            config=self.ledger_config,
            durable_store=self.store,
            clock=_Clock(),
        )
        active = service.bootstrap_normal_fi_term()
        fence = self._fence()
        request = self._request(fence)
        fenced = service.fence_or_expire_fi(
            expected_version=active.version,
            expected_head_sha256=active.head_sha256,
            request=request,
            fi_self_fence_receipt=fence,
        )
        pending = service.reserve_ir_promotion(
            expected_version=fenced.version,
            expected_head_sha256=fenced.head_sha256,
            request=request,
            reservation=self._reservation(),
        )
        issued, grant = service.issue_reserved_ir_promotion_grant(
            expected_version=pending.version,
            expected_head_sha256=pending.head_sha256,
            issuer=_Issuer(self.witness_key),
        )
        completed = service.complete_ir_promotion(
            expected_version=issued.version,
            expected_head_sha256=issued.head_sha256,
            request=request,
            grant=grant,
            completion=self._completion(grant),
        )

        restarted = subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore(
            self.store_config,
            rollback_checkpoint=self.checkpoint,
        )
        self.assertEqual(completed, restarted.read_current())
        self.assertEqual("ir-active", completed.state.phase)
        self.assertEqual(self.ir_term, completed.state.active_term)
        self.assertEqual(5, len(list((self.state_root / "entries").glob("*.json"))))

    def test_exact_head_cas_collision_returns_false_without_overwrite(self) -> None:
        entry, state = self._state_and_entry(sequence=1, previous_head_sha256="0" * 64)
        self.assertTrue(
            self.store.append_compare_and_swap(
                expected_version=0,
                expected_head_sha256="0" * 64,
                entry=entry,
                next_state=state,
            )
        )
        self.assertFalse(
            self.store.append_compare_and_swap(
                expected_version=0,
                expected_head_sha256="0" * 64,
                entry=entry,
                next_state=state,
            )
        )
        current = self.store.read_current()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(1, current.version)
        self.assertEqual(entry.entry_sha256, current.head_sha256)

    def test_checkpoint_rejects_whole_tree_rollback_after_restart(self) -> None:
        first = self._append_initial()
        old_current = (self.state_root / "current.json").read_bytes()
        second_entry, second_state = self._state_and_entry(
            sequence=2,
            previous_head_sha256=first.head_sha256,
        )
        self.assertTrue(
            self.store.append_compare_and_swap(
                expected_version=first.version,
                expected_head_sha256=first.head_sha256,
                entry=second_entry,
                next_state=second_state,
            )
        )
        (self.state_root / "entries" / f"{second_entry.sequence:020d}-{second_entry.entry_sha256}.json").unlink()
        current_path = self.state_root / "current.json"
        descriptor = os.open(current_path, os.O_WRONLY | os.O_TRUNC)
        try:
            os.write(descriptor, old_current)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        restarted = subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore(
            self.store_config,
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError,
            "CHECKPOINT_REJECTED",
        ):
            restarted.read_current()

    def test_symlink_and_temporary_residue_fail_closed(self) -> None:
        self._append_initial()
        current_path = self.state_root / "current.json"
        current_path.unlink()
        current_path.symlink_to("binding.json")
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError,
            "ROOT_CHILD_UNSAFE|CURRENT_INVALID",
        ):
            self.store.read_current()

        current_path.unlink()
        current_path.write_bytes(b"{}\n")
        current_path.chmod(0o600)
        (self.state_root / ".interrupted-write.tmp").write_bytes(b"unsafe")
        (self.state_root / ".interrupted-write.tmp").chmod(0o600)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError,
            "TEMP_RESIDUE",
        ):
            self.store.read_current()

    def test_wrong_pinned_ledger_config_cannot_read_existing_state(self) -> None:
        self._append_initial()
        wrong_term = replace(
            self.fi_term,
            writer_epoch=42,
            writer_lease_id=_id("different-fi-lease"),
            witness_transition_id=_id("different-fi-transition"),
            witnessed_term_proof_sha256=_sha("e"),
        )
        wrong_ledger_config = replace(self.ledger_config, initial_fi_term=wrong_term)
        wrong_store = subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore(
            replace(self.store_config, ledger_config=wrong_ledger_config),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError,
            "BINDING_MISMATCH",
        ):
            wrong_store.read_current()

    def test_default_off_nonroot_and_missing_checkpoint_touch_no_state(self) -> None:
        disabled = subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore(
            replace(self.store_config, enabled=False),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError,
            "DURABLE_CAS_DISABLED",
        ):
            disabled.read_current()
        self.assertEqual([], list(self.state_root.iterdir()))

        missing = subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore(
            self.store_config,
            rollback_checkpoint=None,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError,
            "CHECKPOINT_MISSING",
        ):
            missing.read_current()
        self.assertEqual([], list(self.state_root.iterdir()))

        with mock.patch.object(subject.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError,
                "ROOT_RUNTIME_REQUIRED",
            ):
                self.store.read_current()
        self.assertEqual([], list(self.state_root.iterdir()))

    def test_source_stays_local_and_documents_integration_gap(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            (node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertFalse(
            any(
                forbidden in name
                for forbidden in (
                    "physical_wal_v2",
                    "physical_full_matrix_v4",
                    "boto",
                    "requests",
                    "socket",
                    "subprocess",
                    "ssh",
                )
                for name in imported
            )
        )
        self.assertIn("neither authorizes a writer", source)
        self.assertIn("checkpoint", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
