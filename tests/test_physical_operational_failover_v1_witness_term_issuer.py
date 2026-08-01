"""Adversarial tests for the local Witness current-term attestation issuer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1 as evidence
from core import physical_operational_failover_v1_witness_ledger as ledger
from core import physical_operational_failover_v1_witness_term_issuer as subject
from core import physical_operational_failover_v1_witness_term_revalidator as revalidator
from core import physical_operational_failover_v1_writer_admission as admission


NOW = datetime(2032, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_operational_failover_v1_witness_term_issuer.py"
)


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


class _Reader:
    def __init__(self, snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def read_current_witness_ledger_snapshot(
        self,
    ) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None:
        self.calls += 1
        return self.snapshot


class PhysicalOperationalFailoverV1WitnessCurrentTermIssuerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi_key = Ed25519PrivateKey.generate()
        self.ir_request_key = Ed25519PrivateKey.generate()
        self.promotion_key = Ed25519PrivateKey.generate()
        self.ir_completion_key = Ed25519PrivateKey.generate()
        self.current_term_key = Ed25519PrivateKey.generate()
        self.wrong_key = Ed25519PrivateKey.generate()
        self.pins = evidence.PhysicalOperationalFailoverV1Pins(
            cluster_id="gold-trade-three-site-prod",
            release_sha=RELEASE_SHA,
            stream_generation_id=_id("physical-generation"),
            route_binding_sha256=_sha("b"),
            baseline_generation_id=_id("baseline-generation"),
            baseline_manifest_sha256=_sha("c"),
            recovery_frontier_wal_lsn="0/20",
            blob_frontier_wal_lsn="0/30",
        )
        self.verification = evidence.PhysicalOperationalFailoverV1VerificationConfig(
            pins=self.pins,
            fi_self_fence_signer_public_key=_public(self.fi_key),
            ir_promotion_request_signer_public_key=_public(self.ir_request_key),
            witness_term_signer_public_key=_public(self.promotion_key),
            ir_promotion_completion_signer_public_key=_public(self.ir_completion_key),
            enabled=True,
            maximum_evidence_age_seconds=60,
        )
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id=self.pins.cluster_id,
            local_site="webapp_fi",
            release_sha=RELEASE_SHA,
            generation_id=self.pins.stream_generation_id,
        )
        self.revalidator_config = (
            revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig(
                enabled=True,
                binding=self.binding,
                runtime_instance_id=_id("writer-runtime"),
                witness_current_term_signer_public_key=_public(self.current_term_key),
                witness_promotion_signer_public_key=_public(self.promotion_key),
                witness_current_term_signer_key_id=_id("witness-current-term-key"),
                durable_guard_id=_id("witness-term-replay-guard"),
                safety_margin_seconds=5,
                maximum_attestation_age_seconds=30,
                maximum_attestation_duration_seconds=90,
                maximum_reservation_duration_seconds=90,
            )
        )
        self.term = evidence.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id=_id("fi-writer-lease"),
            witness_transition_id=_id("witness-transition"),
            witnessed_term_proof_sha256=_sha("d"),
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=60),
        )
        self.ledger_config = ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig(
            enabled=True,
            verification_config=self.verification,
            initial_fi_term=self.term,
        )
        self.snapshot = self._active_snapshot(self.term)

    def _active_snapshot(
        self,
        term: evidence.PhysicalOperationalFailoverV1Term,
        *,
        version: int = 1,
        previous_head: str = "0" * 64,
    ) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        state = ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=version,
            phase="fi-active",
            clock_floor=NOW - timedelta(seconds=10),
            active_term=term,
            active_term_sha256=ledger._term_sha256(term, code="TEST"),
        )
        entry = ledger._make_entry(
            sequence=version,
            previous_head_sha256=previous_head,
            observed_at=NOW - timedelta(seconds=10),
            event="bootstrap-fi-active",
            state=state,
        )
        return ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
            version=version,
            head_sha256=entry.entry_sha256,
            entry=entry,
            state=state,
        )

    def _non_active_snapshot(self) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        state = ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=1,
            phase="fi-fenced",
            clock_floor=NOW - timedelta(seconds=10),
            predecessor_term=self.term,
            predecessor_term_sha256=ledger._term_sha256(self.term, code="TEST"),
            predecessor_termination_reason="fi-self-fence-receipt",
            fi_self_fence_receipt_sha256=_sha("e"),
            request_sha256=_sha("f"),
            request_id=_id("ir-promotion-request"),
            request_nonce=_nonce("a"),
            canonical_request=b'{"canonical":"request"}',
        )
        entry = ledger._make_entry(
            sequence=1,
            previous_head_sha256="0" * 64,
            observed_at=NOW - timedelta(seconds=10),
            event="fi-fenced",
            state=state,
        )
        return ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
            version=1,
            head_sha256=entry.entry_sha256,
            entry=entry,
            state=state,
        )

    def _request(
        self,
        *,
        binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding | None = None,
        revalidation_id: str | None = None,
    ) -> admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest:
        selected_binding = self.binding if binding is None else binding
        return admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest(
            binding=selected_binding,
            runtime_instance_id=self.revalidator_config.runtime_instance_id or "",
            revalidation_id=_id("revalidation") if revalidation_id is None else revalidation_id,
            minimum_writer_epoch=0,
            previous_writer_lease_id=None,
            previous_evidence_id=None,
            previous_revalidation_id=None,
            clock_floor=None,
        )

    def _reservation(
        self,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
        *,
        minimum_version: int = 0,
        previous_head: str | None = None,
    ) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
        facts = revalidator._config(self.revalidator_config)
        request_facts = revalidator._request_facts(request, facts=facts)
        reservation_request = revalidator._reservation_request(
            facts=facts,
            request=request_facts,
            now=NOW,
        )
        return revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation(
            schema=reservation_request.schema,
            configuration_sha256=reservation_request.configuration_sha256,
            durable_guard_id=reservation_request.durable_guard_id,
            reservation_id=_id("guard-reservation"),
            binding_sha256=reservation_request.binding_sha256,
            runtime_instance_id=reservation_request.runtime_instance_id,
            revalidation_id=reservation_request.revalidation_id,
            request_sha256=reservation_request.request_sha256,
            requested_at=reservation_request.requested_at,
            reserved_at=NOW,
            expires_at=NOW + timedelta(seconds=60),
            minimum_ledger_version=minimum_version,
            previous_ledger_head_sha256=previous_head,
        )

    def _issuer(
        self,
        *,
        snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None = None,
        config: subject.RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig | None = None,
        private_key: Ed25519PrivateKey | None = None,
    ) -> tuple[subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuer, _Reader]:
        reader = _Reader(self.snapshot if snapshot is None else snapshot)
        issuer = subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuer(
            config=(
                subject.RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig(
                    enabled=True,
                    revalidator_config=self.revalidator_config,
                    ledger_config=self.ledger_config,
                )
                if config is None
                else config
            ),
            snapshot_reader=reader,
            clock=_Clock(),
            current_term_private_key=(self.current_term_key if private_key is None else private_key),
        )
        return issuer, reader

    def test_issues_existing_grammar_from_exact_single_local_snapshot(self) -> None:
        issuer, reader = self._issuer()
        request = self._request()
        reservation = self._reservation(request)

        response = issuer.issue_current_term_attestation(
            request=request,
            reservation=reservation,
        )

        self.assertEqual(1, reader.calls)
        self.assertIs(self.snapshot, response.ledger_snapshot)
        verified = revalidator.verify_physical_operational_failover_v1_witness_current_term_attestation(
            value=response.canonical_attestation,
            config=self.revalidator_config,
            request=request,
            reservation=reservation,
            ledger_snapshot=self.snapshot,
            now=NOW,
        )
        self.assertEqual(self.term, verified.active_term)
        self.assertEqual(self.snapshot.head_sha256, verified.ledger_head_sha256)
        self.assertEqual(reservation.reservation_id, verified.reservation_id)
        self.assertFalse(hasattr(response, "writer_authorized"))
        self.assertFalse(hasattr(response, "promotion_authorized"))
        self.assertFalse(hasattr(response, "traffic_authorized"))

    def test_rejects_wrong_private_key_and_promotion_role_collision(self) -> None:
        request = self._request()
        reservation = self._reservation(request)
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError, "PRIVATE_KEY_ROLE_MISMATCH"):
            self._issuer(private_key=self.wrong_key)

        colliding = replace(
            self.revalidator_config,
            witness_current_term_signer_public_key=_public(self.promotion_key),
        )
        config = subject.RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig(
            enabled=True,
            revalidator_config=colliding,
            ledger_config=self.ledger_config,
        )
        with self.assertRaises(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError):
            self._issuer(config=config, private_key=self.promotion_key)
        self.assertEqual(_id("revalidation"), request.revalidation_id)
        self.assertEqual(_id("guard-reservation"), reservation.reservation_id)

    def test_rejects_stale_and_rollback_snapshot_against_durable_reservation(self) -> None:
        request = self._request()
        stale = self._reservation(request, minimum_version=2, previous_head=_sha("f"))
        issuer, reader = self._issuer()
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError, "LEDGER_SNAPSHOT_STALE"):
            issuer.issue_current_term_attestation(request=request, reservation=stale)
        self.assertEqual(1, reader.calls)

        rollback = self._reservation(request, minimum_version=1, previous_head=_sha("e"))
        issuer, reader = self._issuer()
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError, "LEDGER_SNAPSHOT_ROLLBACK"):
            issuer.issue_current_term_attestation(request=request, reservation=rollback)
        self.assertEqual(1, reader.calls)

    def test_rejects_reservation_for_a_different_exact_request(self) -> None:
        issuer, reader = self._issuer()
        request = self._request(revalidation_id=_id("revalidation-one"))
        other = self._request(revalidation_id=_id("revalidation-two"))
        reservation = self._reservation(other)
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError, "RESERVATION_MISMATCH"):
            issuer.issue_current_term_attestation(request=request, reservation=reservation)
        self.assertEqual(0, reader.calls)

    def test_rejects_non_active_snapshot_and_active_holder_mismatch(self) -> None:
        request = self._request()
        reservation = self._reservation(request)
        issuer, reader = self._issuer(snapshot=self._non_active_snapshot())
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError, "LEDGER_NOT_CURRENT"):
            issuer.issue_current_term_attestation(request=request, reservation=reservation)
        self.assertEqual(1, reader.calls)

        ir_binding = replace(self.binding, local_site="webapp_ir")
        ir_config = replace(self.revalidator_config, binding=ir_binding)
        ir_request = self._request(binding=ir_binding)
        facts = revalidator._config(ir_config)
        request_facts = revalidator._request_facts(ir_request, facts=facts)
        reservation_request = revalidator._reservation_request(
            facts=facts,
            request=request_facts,
            now=NOW,
        )
        ir_reservation = revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation(
            schema=reservation_request.schema,
            configuration_sha256=reservation_request.configuration_sha256,
            durable_guard_id=reservation_request.durable_guard_id,
            reservation_id=_id("guard-reservation-ir"),
            binding_sha256=reservation_request.binding_sha256,
            runtime_instance_id=reservation_request.runtime_instance_id,
            revalidation_id=reservation_request.revalidation_id,
            request_sha256=reservation_request.request_sha256,
            requested_at=NOW,
            reserved_at=NOW,
            expires_at=NOW + timedelta(seconds=60),
            minimum_ledger_version=0,
            previous_ledger_head_sha256=None,
        )
        ir_issuer, reader = self._issuer(
            config=subject.RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig(
                enabled=True,
                revalidator_config=ir_config,
                ledger_config=self.ledger_config,
            )
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError, "ACTIVE_HOLDER_MISMATCH"):
            ir_issuer.issue_current_term_attestation(
                request=ir_request,
                reservation=ir_reservation,
            )
        self.assertEqual(1, reader.calls)

    def test_rejects_expiring_term_and_non_root_runtime(self) -> None:
        expiring_term = replace(self.term, expires_at=NOW + timedelta(seconds=5))
        request = self._request()
        reservation = self._reservation(request)
        issuer, reader = self._issuer(snapshot=self._active_snapshot(expiring_term))
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError, "TERM_NOT_CURRENT"):
            issuer.issue_current_term_attestation(request=request, reservation=reservation)
        self.assertEqual(1, reader.calls)

        config = subject.RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig(
            enabled=True,
            revalidator_config=self.revalidator_config,
            ledger_config=self.ledger_config,
        )
        with mock.patch.object(subject.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError, "ROOT_RUNTIME_REQUIRED"):
                subject.PhysicalOperationalFailoverV1WitnessCurrentTermIssuer(
                    config=config,
                    snapshot_reader=_Reader(self.snapshot),
                    clock=_Clock(),
                    current_term_private_key=self.current_term_key,
                )

    def test_static_isolation_has_no_mutation_or_external_runtime_import(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertTrue(imported_roots <= {"__future__", "dataclasses", "datetime", "os", "secrets", "typing", "cryptography", "core"})
        self.assertFalse(imported_roots & {"boto3", "botocore", "requests", "socket", "subprocess", "urllib", "paramiko", "psycopg"})
        self.assertNotIn("append_compare_and_swap(", source)
        self.assertNotIn("fence_or_expire_fi(", source)
        self.assertNotIn("reserve_ir_promotion(", source)
        self.assertNotIn("issue_reserved_ir_promotion_grant(", source)
        self.assertNotIn("complete_ir_promotion(", source)
        self.assertNotIn("physical_wal_v2", source)
        self.assertNotIn("physical_full_matrix", source)


if __name__ == "__main__":
    unittest.main()
