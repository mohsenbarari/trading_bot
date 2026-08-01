"""Focused adversarial tests for the V1 current-term revalidator contract."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1 as wire
from core import physical_operational_failover_v1_witness_ledger as ledger
from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_witness_term_revalidator as subject


NOW = datetime(2030, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40


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


class _Guard:
    """In-memory fake only for exercising the contract's injected seam."""

    def __init__(
        self,
        *,
        previous_version: int = 0,
        previous_head: str | None = None,
    ) -> None:
        self.previous_version = previous_version
        self.previous_head = previous_head
        self.reserved: set[str] = set()
        self.consumed: set[tuple[str, str, str]] = set()
        self.reservation_calls: list[
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest
        ] = []
        self.consumption_calls: list[
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption
        ] = []

    def reserve_revalidation(
        self,
        *,
        request: subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest,
    ) -> subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
        self.reservation_calls.append(request)
        if request.revalidation_id in self.reserved:
            raise RuntimeError("durable replay")
        self.reserved.add(request.revalidation_id)
        return subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation(
            schema=request.schema,
            configuration_sha256=request.configuration_sha256,
            durable_guard_id=request.durable_guard_id,
            reservation_id=_id("guard-reservation-" + str(len(self.reserved))),
            binding_sha256=request.binding_sha256,
            runtime_instance_id=request.runtime_instance_id,
            revalidation_id=request.revalidation_id,
            request_sha256=request.request_sha256,
            requested_at=request.requested_at,
            reserved_at=request.requested_at,
            expires_at=request.requested_at + timedelta(seconds=60),
            minimum_ledger_version=self.previous_version,
            previous_ledger_head_sha256=self.previous_head,
        )

    def consume_attestation(
        self,
        *,
        reservation: subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
        consumption: subject.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption,
    ) -> subject.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt:
        self.consumption_calls.append(consumption)
        replay_key = (
            consumption.attestation_id,
            consumption.attestation_nonce,
            consumption.attestation_sha256,
        )
        if replay_key in self.consumed:
            raise RuntimeError("durable attestation replay")
        self.consumed.add(replay_key)
        self.previous_version = consumption.ledger_version
        self.previous_head = consumption.ledger_head_sha256
        return subject.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt(
            schema=consumption.schema,
            configuration_sha256=consumption.configuration_sha256,
            durable_guard_id=consumption.durable_guard_id,
            reservation_id=consumption.reservation_id,
            revalidation_id=consumption.revalidation_id,
            request_sha256=consumption.request_sha256,
            attestation_id=consumption.attestation_id,
            attestation_nonce=consumption.attestation_nonce,
            attestation_sha256=consumption.attestation_sha256,
            ledger_version=consumption.ledger_version,
            ledger_head_sha256=consumption.ledger_head_sha256,
            consumed_at=consumption.consumed_at,
            receipt_id=_id("guard-consumed-" + str(len(self.consumed))),
        )


class _Fetcher:
    def __init__(
        self,
        *,
        signing_config: subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
        private_key: Ed25519PrivateKey,
        snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot,
        mutator: callable | None = None,
        returned_snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None = None,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        self.signing_config = signing_config
        self.private_key = private_key
        self.snapshot = snapshot
        self.mutator = mutator
        self.returned_snapshot = returned_snapshot
        self.issued_at = NOW - timedelta(seconds=1) if issued_at is None else issued_at
        self.expires_at = NOW + timedelta(seconds=50) if expires_at is None else expires_at
        self.requests: list[admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest] = []

    def fetch_current_term_attestation(
        self,
        *,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
        reservation: subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    ) -> subject.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse:
        self.requests.append(request)
        state = self.snapshot.state
        assert state.active_term is not None
        raw = subject.sign_physical_operational_failover_v1_witness_current_term_attestation(
            value=subject.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationInput(
                attestation_id="witness-current-attestation-" + request.revalidation_id,
                attestation_nonce=_nonce("a"),
                issued_at=self.issued_at,
                expires_at=self.expires_at,
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
            config=self.signing_config,
            private_key=self.private_key,
        )
        if self.mutator is not None:
            raw = self.mutator(raw)
        return subject.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse(
            canonical_attestation=raw,
            ledger_snapshot=self.snapshot if self.returned_snapshot is None else self.returned_snapshot,
        )


class PhysicalOperationalFailoverV1WitnessTermRevalidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.attestation_key = Ed25519PrivateKey.generate()
        self.promotion_key = Ed25519PrivateKey.generate()
        self.wrong_key = Ed25519PrivateKey.generate()
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id="gold-trade-three-site-prod",
            local_site="webapp_fi",
            release_sha=RELEASE_SHA,
            generation_id=_id("physical-generation"),
        )
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=_id("writer-runtime"),
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        self.config = subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=self.writer_config.runtime_instance_id,
            witness_current_term_signer_public_key=_public(self.attestation_key),
            witness_promotion_signer_public_key=_public(self.promotion_key),
            witness_current_term_signer_key_id=_id("witness-current-term-key"),
            durable_guard_id=_id("witness-term-replay-guard"),
            safety_margin_seconds=5,
            maximum_attestation_age_seconds=30,
            maximum_attestation_duration_seconds=90,
            maximum_reservation_duration_seconds=90,
        )
        self.term = wire.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id="writer-lease-73",
            witness_transition_id=_id("witness-transition"),
            witnessed_term_proof_sha256=_sha("b"),
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=60),
        )
        term_sha = ledger._term_sha256(self.term, code="test")
        state = ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=1,
            phase="fi-active",
            clock_floor=NOW - timedelta(seconds=10),
            active_term=self.term,
            active_term_sha256=term_sha,
        )
        entry = ledger._make_entry(
            sequence=1,
            previous_head_sha256="0" * 64,
            observed_at=NOW - timedelta(seconds=10),
            event="bootstrap-fi-active",
            state=state,
        )
        self.snapshot = ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
            version=1,
            head_sha256=entry.entry_sha256,
            entry=entry,
            state=state,
        )

    def request(self, revalidation_id: str = _id("revalidation")) -> admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest:
        return admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest(
            binding=self.binding,
            runtime_instance_id=self.writer_config.runtime_instance_id or "",
            revalidation_id=revalidation_id,
            minimum_writer_epoch=0,
            previous_writer_lease_id=None,
            previous_evidence_id=None,
            previous_revalidation_id=None,
            clock_floor=None,
        )

    def bridge(
        self,
        *,
        guard: _Guard | None = None,
        fetcher: _Fetcher | None = None,
    ) -> tuple[subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator, _Guard, _Fetcher]:
        selected_guard = _Guard() if guard is None else guard
        selected_fetcher = (
            _Fetcher(
                signing_config=self.config,
                private_key=self.attestation_key,
                snapshot=self.snapshot,
            )
            if fetcher is None
            else fetcher
        )
        return (
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator(
                config=self.config,
                fetcher=selected_fetcher,
                durable_guard=selected_guard,
                clock=_Clock(),
            ),
            selected_guard,
            selected_fetcher,
        )

    def re_sign(self, raw: bytes, *, key: Ed25519PrivateKey, **changes: object) -> bytes:
        decoded = json.loads(raw.decode("ascii"))
        decoded.update(changes)
        del decoded["signature_base64"]
        signature = key.sign(subject._DOMAIN + subject._canonical(decoded, code="test"))
        decoded["signature_base64"] = base64.b64encode(signature).decode("ascii")
        return subject._canonical(decoded, code="test")

    def test_healthy_attestation_projects_only_narrow_evidence_and_integrates_writer_admission(self) -> None:
        bridge, guard, fetcher = self.bridge()
        startup = admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )
        transition = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=startup,
            evidence_revalidator=bridge,
            revalidation_id=_id("revalidation"),
            now=NOW,
        )
        self.assertIsNotNone(transition)
        assert transition is not None
        self.assertEqual(transition.next_state.active_term.writer_epoch, 41)
        self.assertEqual(len(guard.reservation_calls), 1)
        self.assertEqual(len(guard.consumption_calls), 1)
        self.assertEqual(len(fetcher.requests), 1)

        evidence = bridge.revalidate_writer_term(request=self.request(_id("revalidation-next")))
        self.assertEqual(evidence.writer_epoch, 41)
        self.assertEqual(
            set(evidence.__dataclass_fields__),
            {
                "cluster_id",
                "holder_site",
                "writer_epoch",
                "writer_lease_id",
                "release_sha",
                "generation_id",
                "evidence_id",
                "revalidation_id",
                "issued_at",
                "expires_at",
            },
        )
        self.assertFalse(any("author" in field for field in evidence.__dataclass_fields__))

    def test_short_writer_lease_crosses_wire_ledger_and_revalidator_only(self) -> None:
        """All V1 term serializers share the canonical lease grammar."""

        short = replace(self.term, writer_lease_id="writer-lease-73")
        self.assertEqual(
            "writer-lease-73",
            wire._term_mapping(short, code="test")[0].writer_lease_id,
        )
        self.assertEqual(
            "writer-lease-73",
            ledger._term_mapping(short, code="test")[0].writer_lease_id,
        )
        self.assertEqual(
            "writer-lease-73",
            subject._term_mapping(short, code="test")[0].writer_lease_id,
        )

        invalid = replace(self.term, writer_lease_id="writer:lease-000073")
        with self.assertRaisesRegex(wire.PhysicalOperationalFailoverV1Error, "test"):
            wire._term_mapping(invalid, code="test")
        with self.assertRaisesRegex(
            ledger.PhysicalOperationalFailoverV1WitnessLedgerError,
            "test",
        ):
            ledger._term_mapping(invalid, code="test")
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "test",
        ):
            subject._term_mapping(invalid, code="test")

    def test_role_key_config_ledger_head_and_replay_attacks_fail_closed(self) -> None:
        attacks: list[tuple[str, callable, str]] = []

        attacks.append(
            (
                "wrong-key",
                lambda raw: self.re_sign(raw, key=self.wrong_key),
                "ATTESTATION_SIGNATURE_INVALID",
            )
        )
        attacks.append(
            (
                "wrong-role",
                lambda raw: self.re_sign(raw, key=self.attestation_key, issuer_site="webapp_fi"),
                "ATTESTATION_CONFIG_MISMATCH",
            )
        )
        attacks.append(
            (
                "wrong-holder",
                lambda raw: self.re_sign(raw, key=self.attestation_key, holder_site="webapp_ir"),
                "ATTESTATION_BINDING_MISMATCH",
            )
        )
        for name, mutator, expected in attacks:
            with self.subTest(name=name):
                fetcher = _Fetcher(
                    signing_config=self.config,
                    private_key=self.attestation_key,
                    snapshot=self.snapshot,
                    mutator=mutator,
                )
                bridge, _guard, _fetcher = self.bridge(fetcher=fetcher)
                with self.assertRaisesRegex(
                    subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
                    expected,
                ):
                    bridge.revalidate_writer_term(request=self.request(_id("revalidation-" + name)))

        with self.subTest("ledger-head"):
            state = ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
                sequence=2,
                phase="fi-active",
                clock_floor=NOW - timedelta(seconds=9),
                active_term=self.term,
                active_term_sha256=ledger._term_sha256(self.term, code="test"),
            )
            entry = ledger._make_entry(
                sequence=2,
                previous_head_sha256=self.snapshot.head_sha256,
                observed_at=NOW - timedelta(seconds=9),
                event="heartbeat-fi-active",
                state=state,
            )
            different_snapshot = ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
                version=2,
                head_sha256=entry.entry_sha256,
                entry=entry,
                state=state,
            )
            fetcher = _Fetcher(
                signing_config=self.config,
                private_key=self.attestation_key,
                snapshot=self.snapshot,
                returned_snapshot=different_snapshot,
            )
            bridge, _guard, _fetcher = self.bridge(fetcher=fetcher)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
                "LEDGER_HEAD_MISMATCH",
            ):
                bridge.revalidate_writer_term(request=self.request(_id("revalidation-head")))

        with self.subTest("durable-replay"):
            bridge, guard, _fetcher = self.bridge()
            request = self.request(_id("revalidation-replay"))
            bridge.revalidate_writer_term(request=request)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
                "GUARD_RESERVATION_FAILED",
            ):
                bridge.revalidate_writer_term(request=request)
            self.assertEqual(len(guard.consumption_calls), 1)

    def test_stale_config_and_rollbacked_ledger_head_fail_closed(self) -> None:
        with self.subTest("stale"):
            fetcher = _Fetcher(
                signing_config=self.config,
                private_key=self.attestation_key,
                snapshot=self.snapshot,
                issued_at=NOW - timedelta(seconds=31),
                expires_at=NOW + timedelta(seconds=40),
            )
            bridge, _guard, _fetcher = self.bridge(fetcher=fetcher)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
                "ATTESTATION_STALE",
            ):
                bridge.revalidate_writer_term(request=self.request(_id("revalidation-stale")))

        with self.subTest("config"):
            alternate = subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig(
                enabled=True,
                binding=self.binding,
                runtime_instance_id=self.writer_config.runtime_instance_id,
                witness_current_term_signer_public_key=_public(self.attestation_key),
                witness_promotion_signer_public_key=_public(self.promotion_key),
                witness_current_term_signer_key_id=_id("witness-current-term-key"),
                durable_guard_id=_id("different-durable-guard"),
            )
            fetcher = _Fetcher(
                signing_config=alternate,
                private_key=self.attestation_key,
                snapshot=self.snapshot,
            )
            bridge, _guard, _fetcher = self.bridge(fetcher=fetcher)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
                "ATTESTATION_CONFIG_MISMATCH",
            ):
                bridge.revalidate_writer_term(request=self.request(_id("revalidation-config")))

        with self.subTest("rollback"):
            guard = _Guard(previous_version=2, previous_head=_sha("f"))
            bridge, _guard, _fetcher = self.bridge(guard=guard)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
                "LEDGER_HEAD_STALE",
            ):
                bridge.revalidate_writer_term(request=self.request(_id("revalidation-rollback")))

    def test_same_key_role_is_rejected_by_configuration(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "KEY_ROLE_COLLISION",
        ):
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator(
                config=subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig(
                    enabled=True,
                    binding=self.binding,
                    runtime_instance_id=self.writer_config.runtime_instance_id,
                    witness_current_term_signer_public_key=_public(self.attestation_key),
                    witness_promotion_signer_public_key=_public(self.attestation_key),
                    witness_current_term_signer_key_id=_id("witness-current-term-key"),
                    durable_guard_id=_id("witness-term-replay-guard"),
                ),
                fetcher=object(),  # type: ignore[arg-type]
                durable_guard=object(),  # type: ignore[arg-type]
                clock=object(),  # type: ignore[arg-type]
            )
