"""Focused pure-contract tests for the V4 signed Witness-anchor wire."""

from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core import physical_full_matrix_v4_witness_anchor_wire as wire


NOW = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
RUN_ID = UUID("7ea994a3-a50a-4f10-bdaf-c75278a0ea74")
PLAN_SHA256 = "a" * 64
JOURNAL_BINDING = hashlib.sha256(b"v4-wire-campaign-binding").hexdigest()
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_witness_anchor_wire.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii") + b"\n"


def _binding() -> dict[str, object]:
    return {
        "campaign_id": "physical-full-matrix-v4-20260731",
        "release_sha": "d" * 40,
        "readiness_binding_sha256": _hash("readiness"),
        "route_commitment_sha256": _hash("route"),
        "four_role_binding_sha256": _hash("four-role"),
        "writer_holder_site": "webapp_fi",
        "writer_epoch": 7,
        "writer_lease_id": "writer-lease-v4-wire-000001",
        "witnessed_term_proof_sha256": _hash("term"),
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "roundtrip_attestation_sha256": _hash("roundtrip"),
        "roundtrip_configuration_sha256": _hash("configuration"),
        "witness_transition_id": "witness-transition-v4-wire-000001",
        "witness_sequence": 17,
    }


class PhysicalFullMatrixV4WitnessAnchorWireTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller_private = Ed25519PrivateKey.generate()
        self.witness_private = Ed25519PrivateKey.generate()
        self.controller_public = self.controller_private.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        self.witness_public = self.witness_private.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        self.binding = _binding()
        self.baseline = (
            wire.derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=self.binding,
            )
        )
        # A nonzero root models a globally witnessed checkpoint, not an
        # implicitly assumed local zero head.
        self.genesis = wire.build_physical_full_matrix_v4_witness_anchor_genesis(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=19,
            head_sha256=_hash("externally-pinned-genesis-head"),
            witness_private_key=self.witness_private,
        )
        self.policy = wire.build_physical_full_matrix_v4_witness_anchor_verification_policy(
            genesis=self.genesis,
            controller_public_key=self.controller_public,
            witness_public_key=self.witness_public,
        )
        self.root_head = wire.verified_physical_full_matrix_v4_witness_anchor_genesis_head(
            policy=self.policy,
            now=NOW,
        )

    def _commitment(
        self,
        *,
        predecessor=None,
        event: str = "effect-started",
        receipt_sha256: str | None = None,
        phase_sequence: int = 1,
    ):
        prior = self.root_head if predecessor is None else predecessor
        return wire.build_physical_full_matrix_v4_witness_anchor_commitment(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            anchor_genesis_sequence=self.genesis.sequence,
            anchor_genesis_head_sha256=self.genesis.head_sha256,
            event=event,
            phase_sequence=phase_sequence,
            phase_request_sha256=_hash(f"request-{phase_sequence}"),
            effect_key=_hash(f"effect-{phase_sequence}"),
            claim_id=f"claim-v4-wire-{phase_sequence:02d}-000000000000",
            receipt_sha256=receipt_sha256,
            previous_anchor_sequence=prior.sequence,
            previous_anchor_head_sha256=prior.head_sha256,
            local_previous_record_sha256=_hash(f"local-previous-{phase_sequence}"),
            local_event_sha256=_hash(f"local-event-{phase_sequence}"),
            occurred_at=NOW,
        )

    def _request(
        self,
        *,
        predecessor=None,
        replay_id: str | None = None,
        commitment=None,
        expires_at: datetime | None = None,
    ) -> bytes:
        prior = self.root_head if predecessor is None else predecessor
        return wire.build_physical_full_matrix_v4_witness_anchor_controller_append_request(
            policy=self.policy,
            predecessor=prior,
            commitment=self._commitment(predecessor=prior)
            if commitment is None
            else commitment,
            replay_id=_hash("controller-replay") if replay_id is None else replay_id,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=30)
            if expires_at is None
            else expires_at,
            controller_private_key=self.controller_private,
        )

    def _verified_request(self, *, predecessor=None, raw: bytes | None = None):
        prior = self.root_head if predecessor is None else predecessor
        request = self._request(predecessor=prior) if raw is None else raw
        return wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
            request,
            policy=self.policy,
            predecessor=prior,
            now=NOW,
        )

    def test_round_trip_binds_every_campaign_plan_request_and_head_pin(self) -> None:
        raw_head, request, head = self._immutable_head(
            predecessor=self.root_head,
            phase_sequence=1,
            replay_label="round-trip-immutable",
        )
        self.assertTrue(raw_head.endswith(b"\n"))
        self.assertTrue(request.canonical_request.endswith(b"\n"))
        self.assertEqual(JOURNAL_BINDING, head.journal_binding_sha256)
        self.assertEqual(self.baseline, head.baseline_plan_binding_sha256)
        self.assertEqual(RUN_ID, head.run_id)
        self.assertEqual(PLAN_SHA256, head.plan_sha256)
        self.assertEqual(self.genesis.sequence + 1, head.sequence)
        self.assertEqual(self.root_head.head_sha256, head.previous_head_sha256)
        self.assertEqual(request.request_sha256, head.controller_request_sha256)
        self.assertEqual(request.commitment_sha256, head.commitment_sha256)
        self.assertEqual(request.commitment, head.commitment)
        self.assertEqual(raw_head, head.canonical_immutable_head)
        self.assertFalse(head.execution_authorized)
        self.assertFalse(head.promotion_authorized)
        self.assertFalse(head.full_matrix_executed)
        with self.assertRaisesRegex(TypeError, "MINTED_ONLY"):
            wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead()  # type: ignore[call-arg]

    def test_forged_replayed_or_expired_controller_request_fails_closed(self) -> None:
        raw = self._request()
        decoded = json.loads(raw)
        decoded["replay_id"] = _hash("forged-replay")
        forged = _canonical(decoded)
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "CONTROLLER_SIGNATURE_INVALID",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
                forged,
                policy=self.policy,
                predecessor=self.root_head,
                now=NOW,
            )

        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "REQUEST_REPLAYED",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
                raw,
                policy=self.policy,
                predecessor=self.root_head,
                now=NOW,
                seen_replay_ids={_hash("controller-replay")},
            )

        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "REQUEST_TIME_INVALID",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
                raw,
                policy=self.policy,
                predecessor=self.root_head,
                now=NOW + timedelta(seconds=31),
            )

    def test_wrong_predecessor_and_both_independent_binding_mismatches_fail(self) -> None:
        _raw_first, _first_request, first_head = self._immutable_head(
            predecessor=self.root_head,
            phase_sequence=1,
            replay_label="first-immutable",
        )
        stale = self._request(predecessor=self.root_head, replay_id=_hash("stale-request"))
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "PREDECESSOR_MISMATCH",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
                stale,
                policy=self.policy,
                predecessor=first_head,
                now=NOW + timedelta(seconds=2),
            )

        wrong_journal = replace(
            self._commitment(),
            journal_binding_sha256=_hash("other-journal"),
        )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "REQUEST_INVALID",
        ):
            self._request(commitment=wrong_journal)

        wrong_baseline = replace(
            self._commitment(),
            baseline_plan_binding_sha256=_hash("other-baseline"),
        )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "REQUEST_INVALID",
        ):
            self._request(commitment=wrong_baseline)

    def test_phase_five_compatibility_aliases_cannot_enter_the_current_anchor(self) -> None:
        """A V2R Phase-5 label needs a fresh anchor generation, never an alias."""

        commitment = self._commitment(phase_sequence=5)
        self.assertEqual(
            "ir-writer-v2-witness-roundtrip-strict-ack-matrix",
            commitment.phase,
        )
        for alias in (
            "ir-writer-v2-strict-ack-matrix",
            "ir-writer-v2r-witness-roundtrip-strict-ack-matrix",
        ):
            with self.subTest(alias=alias), self.assertRaisesRegex(
                wire.PhysicalFullMatrixV4WitnessAnchorWireError,
                "COMMITMENT_INVALID",
            ):
                wire.canonical_physical_full_matrix_v4_witness_anchor_commitment_bytes(
                    replace(commitment, phase=alias)
                )

    def test_signed_nonzero_genesis_and_initial_normal_baseline_are_strict(self) -> None:
        self.assertEqual(19, self.root_head.sequence)
        self.assertEqual(self.genesis.head_sha256, self.root_head.head_sha256)
        self.assertEqual(
            self.baseline,
            wire.derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=dict(self.binding),
            ),
        )
        reverse = dict(self.binding)
        reverse.update(
            writer_holder_site="webapp_ir",
            source_site="webapp_ir",
            destination_site="webapp_fi",
        )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "BASELINE_INVALID",
        ):
            wire.canonical_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_facts(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=reverse,
            )
        unequal_holder = dict(self.binding)
        unequal_holder["writer_holder_site"] = "webapp_ir"
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "BASELINE_INVALID",
        ):
            wire.canonical_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_facts(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                initial_active_binding=unequal_holder,
            )
        mutated_genesis = replace(self.genesis, head_sha256=_hash("mutated-genesis"))
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "GENESIS_(SIGNATURE|INVALID)",
        ):
            wire.build_physical_full_matrix_v4_witness_anchor_verification_policy(
                genesis=mutated_genesis,
                controller_public_key=self.controller_public,
                witness_public_key=self.witness_public,
            )

    def test_wrong_signers_forged_immutable_head_and_replayed_observation_fail_closed(self) -> None:
        commitment = self._commitment()
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "CONTROLLER_SIGNER_INVALID",
        ):
            wire.build_physical_full_matrix_v4_witness_anchor_controller_append_request(
                policy=self.policy,
                predecessor=self.root_head,
                commitment=commitment,
                replay_id=_hash("wrong-controller"),
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=30),
                controller_private_key=self.witness_private,
            )
        request = self._immutable_request(
            predecessor=self.root_head,
            phase_sequence=1,
            replay_label="wrong-signer-immutable",
        )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "WITNESS_SIGNER_INVALID",
        ):
            wire.build_physical_full_matrix_v4_witness_anchor_immutable_head(
                policy=self.policy,
                predecessor=self.root_head,
                append_request=request,
                now=NOW + timedelta(seconds=1),
                witness_private_key=self.controller_private,
            )
        raw_head, _request, immutable = self._immutable_head(
            predecessor=self.root_head,
            phase_sequence=1,
            replay_label="forged-immutable",
        )
        forged = json.loads(raw_head)
        forged["witness_signature"]["signature_base64"] = base64.b64encode(
            b"x" * 64
        ).decode("ascii")
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "IMMUTABLE_SIGNATURE_INVALID",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
                _canonical(forged),
                policy=self.policy,
                now=NOW + timedelta(seconds=2),
                expected_predecessor=self.root_head,
            )
        challenge = _hash("replayed-observation-challenge")
        observation = wire.build_physical_full_matrix_v4_witness_anchor_read_observation(
            policy=self.policy,
            anchor_head=immutable,
            read_challenge=challenge,
            observation_id=_hash("replayed-observation"),
            observed_at=NOW + timedelta(seconds=2),
            expires_at=NOW + timedelta(seconds=32),
            witness_private_key=self.witness_private,
        )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "OBSERVATION_REPLAYED",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_read_observation(
                observation,
                policy=self.policy,
                now=NOW + timedelta(seconds=2),
                anchor_head=immutable,
                expected_read_challenge=challenge,
                seen_observation_ids={_hash("replayed-observation")},
            )

    def test_parser_is_canonical_ascii_exact_and_wire_has_no_io_or_legacy_imports(self) -> None:
        raw = self._request()
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "REQUEST_ENCODING_INVALID",
        ):
            wire.parse_physical_full_matrix_v4_witness_anchor_controller_append_request(
                raw.rstrip(b"\n")
            )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "REQUEST_ENCODING_INVALID",
        ):
            wire.parse_physical_full_matrix_v4_witness_anchor_controller_append_request(
                b" " + raw
            )
        duplicate = raw.replace(
            b'"schema":',
            b'"schema":"duplicate","schema":',
            1,
        )
        with self.assertRaises(wire.PhysicalFullMatrixV4WitnessAnchorWireError):
            wire.parse_physical_full_matrix_v4_witness_anchor_controller_append_request(
                duplicate
            )

        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        forbidden_prefixes = (
            "core.",
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "urllib",
            "http",
            "boto",
        )
        self.assertFalse(
            [
                item
                for item in imports
                if item == "core" or item.startswith(forbidden_prefixes)
            ],
            imports,
        )

    def _immutable_request(self, *, predecessor, phase_sequence: int, replay_label: str):
        commitment = wire.build_physical_full_matrix_v4_witness_anchor_commitment(
            journal_binding_sha256=JOURNAL_BINDING,
            baseline_plan_binding_sha256=self.baseline,
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            anchor_genesis_sequence=self.genesis.sequence,
            anchor_genesis_head_sha256=self.genesis.head_sha256,
            event="effect-started",
            phase_sequence=phase_sequence,
            phase_request_sha256=_hash(f"immutable-phase-request-{phase_sequence}"),
            effect_key=_hash(f"immutable-effect-{phase_sequence}"),
            claim_id=f"immutable-v4-wire-{phase_sequence:02d}-000000000000",
            receipt_sha256=None,
            previous_anchor_sequence=predecessor.sequence,
            previous_anchor_head_sha256=predecessor.head_sha256,
            local_previous_record_sha256=_hash(f"immutable-local-prev-{phase_sequence}"),
            local_event_sha256=_hash(f"immutable-local-event-{phase_sequence}"),
            occurred_at=NOW,
        )
        raw = wire.build_physical_full_matrix_v4_witness_anchor_controller_append_request(
            policy=self.policy,
            predecessor=predecessor,
            commitment=commitment,
            replay_id=_hash(replay_label),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
            controller_private_key=self.controller_private,
        )
        return wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
            raw,
            policy=self.policy,
            predecessor=predecessor,
            now=NOW,
        )

    def _immutable_head(self, *, predecessor, phase_sequence: int, replay_label: str):
        request = self._immutable_request(
            predecessor=predecessor,
            phase_sequence=phase_sequence,
            replay_label=replay_label,
        )
        raw = wire.build_physical_full_matrix_v4_witness_anchor_immutable_head(
            policy=self.policy,
            predecessor=predecessor,
            append_request=request,
            now=NOW + timedelta(seconds=1),
            witness_private_key=self.witness_private,
        )
        return raw, request, wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
            raw,
            policy=self.policy,
            now=NOW + timedelta(seconds=1),
            expected_predecessor=predecessor,
            append_request=request,
        )

    def test_v2_immutable_anchor_survives_old_ttl_and_fresh_observation_is_challenge_bound(self) -> None:
        raw_head, _request, immutable = self._immutable_head(
            predecessor=self.root_head,
            phase_sequence=1,
            replay_label="immutable-request-one",
        )
        much_later = NOW + timedelta(
            seconds=self.policy.maximum_attestation_lifetime_seconds + 3600
        )
        # Immutable proof has no expiry.  The exact expected-current facts are
        # the narrow restart input, not an arbitrary gap bypass.
        reverified = wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
            raw_head,
            policy=self.policy,
            now=much_later,
            expected_current_sequence=immutable.sequence,
            expected_current_head_sha256=immutable.head_sha256,
        )
        challenge = _hash("fresh-read-challenge")
        raw_observation = wire.build_physical_full_matrix_v4_witness_anchor_read_observation(
            policy=self.policy,
            anchor_head=reverified,
            read_challenge=challenge,
            observation_id=_hash("fresh-observation"),
            observed_at=much_later,
            expires_at=much_later + timedelta(seconds=30),
            witness_private_key=self.witness_private,
        )
        envelope = wire.build_physical_full_matrix_v4_witness_anchor_transport_envelope(
            canonical_anchor_head=raw_head,
            canonical_read_observation=raw_observation,
            read_challenge=challenge,
        )
        verified = wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            envelope,
            policy=self.policy,
            now=much_later + timedelta(seconds=1),
            expected_read_challenge=challenge,
            expected_current_sequence=immutable.sequence,
            expected_current_head_sha256=immutable.head_sha256,
        )
        self.assertEqual(immutable.head_sha256, verified.anchor_head.head_sha256)
        self.assertEqual(challenge, verified.read_observation.read_challenge)
        self.assertFalse(verified.execution_authorized)
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "TRANSPORT_CHALLENGE_MISMATCH",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
                envelope,
                policy=self.policy,
                now=much_later + timedelta(seconds=1),
                expected_read_challenge=_hash("another-controller-read"),
                expected_current_sequence=immutable.sequence,
                expected_current_head_sha256=immutable.head_sha256,
            )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "OBSERVATION_REPLAYED",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_read_observation(
                raw_observation,
                policy=self.policy,
                anchor_head=immutable,
                now=much_later + timedelta(seconds=1),
                expected_read_challenge=challenge,
                seen_observation_ids={_hash("fresh-observation")},
            )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "READ_OBSERVATION_TIME_INVALID",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_read_observation(
                raw_observation,
                policy=self.policy,
                anchor_head=immutable,
                now=much_later + timedelta(seconds=31),
                expected_read_challenge=challenge,
            )

    def test_v2_transport_allows_only_exact_local_current_or_one_successor(self) -> None:
        raw_first, _first_request, first = self._immutable_head(
            predecessor=self.root_head,
            phase_sequence=1,
            replay_label="immutable-first",
        )
        raw_second, _second_request, second = self._immutable_head(
            predecessor=first,
            phase_sequence=2,
            replay_label="immutable-second",
        )
        challenge = _hash("successor-read-challenge")
        observation = wire.build_physical_full_matrix_v4_witness_anchor_read_observation(
            policy=self.policy,
            anchor_head=second,
            read_challenge=challenge,
            observation_id=_hash("successor-observation"),
            observed_at=NOW + timedelta(seconds=2),
            expires_at=NOW + timedelta(seconds=32),
            witness_private_key=self.witness_private,
        )
        envelope = wire.build_physical_full_matrix_v4_witness_anchor_transport_envelope(
            canonical_anchor_head=raw_second,
            canonical_read_observation=observation,
            read_challenge=challenge,
        )
        verified = wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            envelope,
            policy=self.policy,
            now=NOW + timedelta(seconds=3),
            expected_read_challenge=challenge,
            expected_current_sequence=first.sequence,
            expected_current_head_sha256=first.head_sha256,
        )
        self.assertEqual(second.sequence, verified.anchor_head.sequence)
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "EXPECTED_CURRENT_MISMATCH",
        ):
            wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
                envelope,
                policy=self.policy,
                now=NOW + timedelta(seconds=3),
                expected_read_challenge=challenge,
                expected_current_sequence=self.root_head.sequence,
                expected_current_head_sha256=self.root_head.head_sha256,
            )
        # An old one-layer schema can never be wrapped in the V2 transport.
        legacy = _canonical(
            {
                "schema": wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_SCHEMA,
                "purpose": "physical-full-matrix-v4-witness-anchor-head-attestation-v1",
            }
        )
        with self.assertRaises(wire.PhysicalFullMatrixV4WitnessAnchorWireError):
            wire.build_physical_full_matrix_v4_witness_anchor_transport_envelope(
                canonical_anchor_head=legacy,
                canonical_read_observation=observation,
                read_challenge=challenge,
            )

    def test_v2_genesis_observation_is_explicit_and_signature_domains_do_not_cross(self) -> None:
        challenge = _hash("genesis-read-challenge")
        raw = wire.build_physical_full_matrix_v4_witness_anchor_read_observation(
            policy=self.policy,
            anchor_head=self.root_head,
            read_challenge=challenge,
            observation_id=_hash("genesis-observation"),
            observed_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
            witness_private_key=self.witness_private,
        )
        decoded = json.loads(raw)
        self.assertEqual("0" * 64, decoded["previous_head_sha256"])
        self.assertEqual("0" * 64, decoded["commitment_sha256"])
        self.assertEqual("0" * 64, decoded["controller_request_sha256"])
        self.assertEqual(challenge, decoded["read_challenge"])
        payload = wire.prepare_physical_full_matrix_v4_witness_anchor_read_observation(
            policy=self.policy,
            anchor_head=self.root_head,
            read_challenge=challenge,
            observation_id=_hash("domain-observation"),
            observed_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        )
        wrong_signature = self.witness_private.sign(
            b"physical-full-matrix-v4-witness-anchor-immutable-append-v1\n"
        )
        with self.assertRaisesRegex(
            wire.PhysicalFullMatrixV4WitnessAnchorWireError,
            "OBSERVATION_SIGNATURE_INVALID",
        ):
            wire.finalize_physical_full_matrix_v4_witness_anchor_read_observation(
                policy=self.policy,
                anchor_head=self.root_head,
                signing_payload=payload,
                witness_signature=wrong_signature,
                now=NOW,
            )

    def test_unreleased_one_layer_entrypoints_are_not_exported_and_are_hard_fenced(self) -> None:
        legacy_names = (
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_SCHEMA",
            "PhysicalFullMatrixV4WitnessAnchorWitnessHead",
            "PhysicalFullMatrixV4WitnessAnchorWitnessHeadSigningPayload",
            "parse_physical_full_matrix_v4_witness_anchor_witness_head",
            "prepare_physical_full_matrix_v4_witness_anchor_witness_head",
            "finalize_physical_full_matrix_v4_witness_anchor_witness_head",
            "build_physical_full_matrix_v4_witness_anchor_witness_head",
            "verify_physical_full_matrix_v4_witness_anchor_witness_head",
            "verify_physical_full_matrix_v4_witness_anchor_append_head",
        )
        self.assertFalse(set(legacy_names) & set(wire.__all__))
        request = self._immutable_request(
            predecessor=self.root_head,
            phase_sequence=1,
            replay_label="legacy-fence-request",
        )
        callbacks = (
            lambda: wire.parse_physical_full_matrix_v4_witness_anchor_witness_head(b"{}\n"),
            lambda: wire.prepare_physical_full_matrix_v4_witness_anchor_witness_head(
                policy=self.policy,
                predecessor=self.root_head,
                append_request=request,
                attestation_id=_hash("legacy-fence"),
                attested_at=NOW,
                expires_at=NOW + timedelta(seconds=1),
            ),
            lambda: wire.finalize_physical_full_matrix_v4_witness_anchor_witness_head(
                policy=self.policy,
                signing_payload=None,  # type: ignore[arg-type]
                witness_signature=b"",
                now=NOW,
            ),
            lambda: wire.build_physical_full_matrix_v4_witness_anchor_witness_head(
                policy=self.policy,
                predecessor=self.root_head,
                append_request=request,
                attestation_id=_hash("legacy-fence-build"),
                attested_at=NOW,
                expires_at=NOW + timedelta(seconds=1),
                witness_private_key=self.witness_private,
            ),
            lambda: wire.verify_physical_full_matrix_v4_witness_anchor_witness_head(
                b"{}\n",
                policy=self.policy,
                now=NOW,
            ),
            lambda: wire.verify_physical_full_matrix_v4_witness_anchor_append_head(
                b"{}\n",
                policy=self.policy,
                predecessor=self.root_head,
                append_request=request,
                now=NOW,
            ),
        )
        for callback in callbacks:
            with self.assertRaisesRegex(
                wire.PhysicalFullMatrixV4WitnessAnchorWireError,
                "LEGACY_ONE_LAYER_MIGRATION_REQUIRED",
            ):
                callback()
