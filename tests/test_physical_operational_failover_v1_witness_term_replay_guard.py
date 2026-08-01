"""Adversarial focused tests for the root-local V1 Witness replay guard."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_witness_term_revalidator as revalidator
from core import physical_operational_failover_v1_witness_term_replay_guard as subject


NOW = datetime(2031, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_operational_failover_v1_witness_term_replay_guard.py"
)


def _id(prefix: str) -> str:
    return prefix + "-" + "x" * 24


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class _Checkpoint:
    """A test double for a separate root-owned monotonic state facility."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str, str, str, str], tuple[int, str, str]] = {}
        self.calls: list[tuple[int, str, str]] = []

    def attest_operational_failover_v1_witness_term_replay_state(
        self,
        *,
        configuration_sha256: str,
        binding_sha256: str,
        durable_guard_id: str,
        role: str,
        state_namespace: str,
        sequence: int,
        previous_record_sha256: str,
        record_sha256: str,
    ) -> None:
        key = (configuration_sha256, binding_sha256, durable_guard_id, role, state_namespace)
        previous = self.states.get(key)
        self.calls.append((sequence, previous_record_sha256, record_sha256))
        if previous is None:
            if (sequence, previous_record_sha256, record_sha256) != (0, "0" * 64, "0" * 64):
                raise RuntimeError("checkpoint expected empty state")
            self.states[key] = (sequence, previous_record_sha256, record_sha256)
            return
        if previous == (sequence, previous_record_sha256, record_sha256):
            return
        if (
            sequence == previous[0] + 1
            and previous_record_sha256 == previous[2]
            and record_sha256 != previous[2]
        ):
            self.states[key] = (sequence, previous_record_sha256, record_sha256)
            return
        raise RuntimeError("rollback or branch")


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now


class _Fetcher:
    def __init__(
        self,
        *,
        config: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
        private_key: Ed25519PrivateKey,
        snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot,
    ) -> None:
        self.config = config
        self.private_key = private_key
        self.snapshot = snapshot

    def fetch_current_term_attestation(
        self,
        *,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
        reservation: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    ) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse:
        state = self.snapshot.state
        assert state.active_term is not None
        encoded = revalidator.sign_physical_operational_failover_v1_witness_current_term_attestation(
            value=revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationInput(
                attestation_id="witness-attestation-" + request.revalidation_id,
                attestation_nonce="a" * 24,
                issued_at=NOW - timedelta(seconds=1),
                expires_at=NOW + timedelta(seconds=50),
                cluster_id=request.binding.cluster_id,
                holder_site=state.active_term.holder_site,
                release_sha=request.binding.release_sha,
                generation_id=request.binding.generation_id,
                runtime_instance_id=request.runtime_instance_id,
                revalidation_id=request.revalidation_id,
                reservation_id=reservation.reservation_id,
                request_sha256=reservation.request_sha256,
                ledger_version=self.snapshot.version,
                ledger_head_sha256=self.snapshot.head_sha256,
                ledger_entry_sha256=self.snapshot.entry.entry_sha256,
                ledger_previous_head_sha256=self.snapshot.entry.previous_head_sha256,
                ledger_state_sha256=self.snapshot.entry.state_sha256,
                ledger_phase=state.phase,
                active_term=state.active_term,
                active_term_sha256=state.active_term_sha256 or "",
            ),
            config=self.config,
            private_key=self.private_key,
        )
        return revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse(
            canonical_attestation=encoded,
            ledger_snapshot=self.snapshot,
        )


class PhysicalOperationalFailoverV1WitnessTermReplayGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.attestation_key = Ed25519PrivateKey.generate()
        self.promotion_key = Ed25519PrivateKey.generate()
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id="gold-trade-three-site-prod",
            local_site="webapp_fi",
            release_sha=RELEASE_SHA,
            generation_id=_id("physical-generation"),
        )
        self.revalidator_config = revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=_id("writer-runtime"),
            witness_current_term_signer_public_key=_public(self.attestation_key),
            witness_promotion_signer_public_key=_public(self.promotion_key),
            witness_current_term_signer_key_id=_id("witness-current-term-key"),
            durable_guard_id=_id("witness-term-replay-guard"),
            safety_margin_seconds=5,
            maximum_attestation_age_seconds=30,
            maximum_attestation_duration_seconds=90,
            maximum_reservation_duration_seconds=90,
        )
        self.config = subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardConfig(
            enabled=True,
            role=subject.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_WEBAPP_FI,
            revalidator_config=self.revalidator_config,
        )
        self.checkpoint = _Checkpoint()
        self._temporary = tempfile.TemporaryDirectory(
            prefix="v1-witness-term-replay-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.state_root = Path(self._temporary.name) / "state"
        self.state_root.mkdir(mode=0o700)
        self._fixed_root = subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT
        subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT = self.state_root
        self.guard = subject.PhysicalOperationalFailoverV1WitnessTermReplayGuard(
            self.config,
            rollback_checkpoint=self.checkpoint,
        )

    def tearDown(self) -> None:
        subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT = self._fixed_root
        self._temporary.cleanup()

    @property
    def namespace(self) -> Path:
        return self.state_root / "webapp-fi"

    def request(
        self,
        label: str = "one",
        *,
        at: datetime = NOW,
    ) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest:
        facts = revalidator._config(self.revalidator_config)
        return revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest(
            schema=revalidator.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA,
            configuration_sha256=facts.configuration_sha256,
            durable_guard_id=facts.durable_guard_id,
            binding_sha256=facts.binding_sha256,
            runtime_instance_id=facts.runtime_instance_id,
            revalidation_id=_id("revalidation-" + label),
            request_sha256=_sha("request-" + label),
            requested_at=at,
        )

    def consume(
        self,
        reservation: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
        *,
        label: str = "one",
        at: datetime = NOW,
        ledger_version: int = 1,
        ledger_head_sha256: str | None = None,
    ) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption:
        facts = revalidator._config(self.revalidator_config)
        return revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption(
            schema=revalidator.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA,
            configuration_sha256=facts.configuration_sha256,
            durable_guard_id=facts.durable_guard_id,
            reservation_id=reservation.reservation_id,
            revalidation_id=reservation.revalidation_id,
            request_sha256=reservation.request_sha256,
            attestation_id=_id("attestation-" + label),
            attestation_nonce=(label[0] * 24),
            attestation_sha256=_sha("attestation-digest-" + label),
            ledger_version=ledger_version,
            ledger_head_sha256=_sha("ledger-head-" + label)
            if ledger_head_sha256 is None
            else ledger_head_sha256,
            consumed_at=at,
        )

    def test_reservation_and_consumption_survive_restart_without_authority(self) -> None:
        reservation = self.guard.reserve_revalidation(request=self.request())
        receipt = self.guard.consume_attestation(
            reservation=reservation,
            consumption=self.consume(reservation),
        )

        self.assertEqual("webapp-fi", self.namespace.name)
        self.assertEqual(0o700, self.namespace.stat().st_mode & 0o777)
        self.assertEqual(0o700, (self.namespace / "records").stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.namespace / "current.json").stat().st_mode & 0o777)
        self.assertFalse(hasattr(receipt, "writer_authorized"))
        self.assertFalse(hasattr(receipt, "promotion_authorized"))
        self.assertFalse(hasattr(receipt, "traffic_authorized"))
        self.assertEqual(2, len(list((self.namespace / "records").glob("*.json"))))

        restarted = subject.PhysicalOperationalFailoverV1WitnessTermReplayGuard(
            self.config,
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "REVALIDATION_REPLAY"):
            restarted.reserve_revalidation(request=self.request())
        next_reservation = restarted.reserve_revalidation(request=self.request("two"))
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "ATTESTATION_REPLAY"):
            restarted.consume_attestation(
                reservation=next_reservation,
                consumption=self.consume(
                    next_reservation,
                    label="one",
                    ledger_version=1,
                    ledger_head_sha256=_sha("ledger-head-one"),
                ),
            )

    def test_expired_or_forged_reservation_and_ledger_rollback_fail_closed(self) -> None:
        reservation = self.guard.reserve_revalidation(request=self.request())
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "EXPIRED_OR_CONSUMED"):
            self.guard.consume_attestation(
                reservation=reservation,
                consumption=self.consume(
                    reservation,
                    at=reservation.expires_at + timedelta(seconds=1),
                ),
            )
        forged = replace(reservation, request_sha256=_sha("forged"))
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "RESERVATION_MISMATCH"):
            self.guard.consume_attestation(reservation=forged, consumption=self.consume(reservation))

        valid = self.guard.consume_attestation(reservation=reservation, consumption=self.consume(reservation))
        self.assertTrue(valid.receipt_id.startswith("witness-term-consumption-v1-"))
        second = self.guard.reserve_revalidation(request=self.request("two"))
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "LEDGER_ROLLBACK"):
            self.guard.consume_attestation(
                reservation=second,
                consumption=self.consume(
                    second,
                    label="two",
                    ledger_version=1,
                    ledger_head_sha256=_sha("different-ledger-head"),
                ),
            )

    def test_checkpoint_rollback_temp_symlink_and_role_switch_are_rejected(self) -> None:
        self.guard.reserve_revalidation(request=self.request())
        records = sorted((self.namespace / "records").glob("*.json"))
        self.assertEqual(1, len(records))
        records[0].unlink()
        (self.namespace / "current.json").unlink()
        restarted = subject.PhysicalOperationalFailoverV1WitnessTermReplayGuard(
            self.config,
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "CHECKPOINT_REJECTED"):
            restarted.reserve_revalidation(request=self.request("after-rollback"))

        # A separate clean root lets residue and role-switch checks be isolated.
        self._temporary.cleanup()
        self._temporary = tempfile.TemporaryDirectory(
            prefix="v1-witness-term-replay-clean-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.state_root = Path(self._temporary.name) / "state"
        self.state_root.mkdir(mode=0o700)
        subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT = self.state_root
        self.checkpoint = _Checkpoint()
        self.guard = subject.PhysicalOperationalFailoverV1WitnessTermReplayGuard(self.config, rollback_checkpoint=self.checkpoint)
        self.guard.reserve_revalidation(request=self.request())
        (self.namespace / ".interrupted.tmp").write_text("partial", encoding="ascii")
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "TEMP_RESIDUE"):
            self.guard.reserve_revalidation(request=self.request("temp"))
        (self.namespace / ".interrupted.tmp").unlink()
        switched_binding = replace(self.binding, local_site="webapp_ir")
        switched_revalidator = replace(self.revalidator_config, binding=switched_binding)
        switched = subject.PhysicalOperationalFailoverV1WitnessTermReplayGuard(
            replace(
                self.config,
                role=subject.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_WEBAPP_IR,
                revalidator_config=switched_revalidator,
            ),
            rollback_checkpoint=self.checkpoint,
        )
        switched_request = self.request("switch")
        switched_facts = revalidator._config(switched_revalidator)
        switched_request = replace(
            switched_request,
            configuration_sha256=switched_facts.configuration_sha256,
            durable_guard_id=switched_facts.durable_guard_id,
            binding_sha256=switched_facts.binding_sha256,
            runtime_instance_id=switched_facts.runtime_instance_id,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "ROLE_CONFIG_SWITCH"):
            switched.reserve_revalidation(request=switched_request)

    def test_default_off_nonroot_missing_checkpoint_and_symlink_fail_before_use(self) -> None:
        disabled = subject.PhysicalOperationalFailoverV1WitnessTermReplayGuard(
            replace(self.config, enabled=False),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "DISABLED"):
            disabled.reserve_revalidation(request=self.request())
        self.assertEqual([], list(self.state_root.iterdir()))
        missing = subject.PhysicalOperationalFailoverV1WitnessTermReplayGuard(
            self.config,
            rollback_checkpoint=None,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "CHECKPOINT_MISSING"):
            missing.reserve_revalidation(request=self.request())
        self.assertEqual([], list(self.state_root.iterdir()))
        with mock.patch.object(os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "ROOT_RUNTIME_REQUIRED"):
                self.guard.reserve_revalidation(request=self.request())
        self.assertEqual([], list(self.state_root.iterdir()))

        self.state_root.rmdir()
        self.state_root.symlink_to("/tmp")
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError, "STATE_ROOT_UNSAFE"):
            self.guard.reserve_revalidation(request=self.request())

    def test_current_pointer_symlink_is_never_followed(self) -> None:
        self.guard.reserve_revalidation(request=self.request())
        current = self.namespace / "current.json"
        current.unlink()
        current.symlink_to("/dev/null")
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessTermReplayGuardError,
            "(?:CURRENT_INVALID|NAMESPACE_UNSAFE)",
        ):
            self.guard.reserve_revalidation(request=self.request("symlink"))

    def test_real_revalidator_uses_the_durable_guard_end_to_end(self) -> None:
        term = wire.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id=_id("fi-writer-lease"),
            witness_transition_id=_id("witness-transition"),
            witnessed_term_proof_sha256=_sha("term-proof"),
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=60),
        )
        term_sha = ledger._term_sha256(term, code="test")
        state = ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=1,
            phase="fi-active",
            clock_floor=NOW - timedelta(seconds=10),
            active_term=term,
            active_term_sha256=term_sha,
        )
        entry = ledger._make_entry(
            sequence=1,
            previous_head_sha256="0" * 64,
            observed_at=NOW - timedelta(seconds=10),
            event="bootstrap-fi-active",
            state=state,
        )
        snapshot = ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
            version=1,
            head_sha256=entry.entry_sha256,
            entry=entry,
            state=state,
        )
        bridge = revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator(
            config=self.revalidator_config,
            fetcher=_Fetcher(
                config=self.revalidator_config,
                private_key=self.attestation_key,
                snapshot=snapshot,
            ),
            durable_guard=self.guard,
            clock=_Clock(),
        )
        request = admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest(
            binding=self.binding,
            runtime_instance_id=self.revalidator_config.runtime_instance_id or "",
            revalidation_id=_id("end-to-end-revalidation"),
            minimum_writer_epoch=0,
            previous_writer_lease_id=None,
            previous_evidence_id=None,
            previous_revalidation_id=None,
            clock_floor=None,
        )
        evidence = bridge.revalidate_writer_term(request=request)
        self.assertEqual(41, evidence.writer_epoch)
        self.assertEqual("webapp_fi", evidence.holder_site)
        with self.assertRaisesRegex(
            revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "GUARD_RESERVATION_FAILED",
        ):
            bridge.revalidate_writer_term(request=request)

    def test_static_boundary_excludes_network_provider_database_and_authority(self) -> None:
        tree = __import__("ast").parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.split(".")[0]
            for node in __import__("ast").walk(tree)
            if isinstance(node, (__import__("ast").Import, __import__("ast").ImportFrom))
            for alias in node.names
        }
        forbidden = {"boto3", "botocore", "requests", "socket", "subprocess", "sqlite3", "psycopg", "redis"}
        self.assertFalse(imported_roots & forbidden)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("physical_wal_v2", source)
        self.assertNotIn("physical_full_matrix_v4", source)
        self.assertNotIn("ssh", source.lower())
        self.assertNotIn("scp", source.lower())


if __name__ == "__main__":
    unittest.main()
