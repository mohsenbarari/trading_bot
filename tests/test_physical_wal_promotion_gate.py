from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_baseline_manifest import build_object_delta_baseline_manifest
from core.object_delta_delivery_control_packet import (
    ObjectDeltaReceiverDeliveryPermit,
    controller_key_id_from_public_key,
)
from core.object_delta_receiver_delivery_binding import ObjectDeltaReceiverDeliveryBinding
from core.object_delta_role_matrix import (
    OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
    ObjectDeltaRoleMatrixRoute,
    ObjectDeltaRoleMatrixWriterTerm,
    authorize_object_delta_role_matrix,
)
from core.object_delta_role_matrix_rollover import (
    bootstrap_object_delta_role_matrix_activation,
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_route_generations,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_batch_attestation import source_key_id_from_public_key
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverRecord,
    build_object_delta_source_cutover_attestation,
)
from core.object_delta_source_cutover_publication_gate import ObjectDeltaSourceCutoverPublicationPin
from core.object_delta_transport_binding import ObjectDeltaTransportPolicy, destination_age_recipient
from core.physical_wal_remote_ack import (
    PhysicalWalRemoteAckError,
    build_physical_wal_remote_ack_binding,
    build_physical_wal_remote_ack_receipt,
    build_physical_wal_remote_ack_request,
    verify_physical_wal_remote_ack_evidence,
)
from core.physical_wal_promotion_gate import (
    PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_ARCHIVE_ONLY,
    PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_STRICT_REMOTE_DURABLE_REPLAY,
    PHYSICAL_WAL_BLOB_OBJECT_RECEIPT_SCHEMA,
    PHYSICAL_WAL_CONTINUITY_ARTIFACT_SCHEMA,
    PHYSICAL_WAL_RECEIVER_REPLAY_RECEIPT_SCHEMA,
    PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
    PhysicalWalPromotionAssessment,
    PhysicalWalPromotionGateError,
    assess_physical_wal_promotion,
    require_physical_wal_promotion_eligible,
    verify_physical_wal_promotion_evidence,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "wa-physical-wal-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
FINGERPRINT = "0123456789abcdef"


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def sign(payload: dict, signer: Ed25519PrivateKey) -> dict:
    unsigned = dict(payload)
    signature = signer.sign(canonical_json_bytes(unsigned))
    return {
        **unsigned,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def digest(payload: dict) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class PhysicalWalPromotionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi_signer = Ed25519PrivateKey.generate()
        self.ir_signer = Ed25519PrivateKey.generate()
        self.controller_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.fi_key = public_key(self.fi_signer)
        self.ir_key = public_key(self.ir_signer)
        self.controller_key = public_key(self.controller_signer)
        self.witness_key = public_key(self.witness_signer)
        self.policy = ObjectDeltaTransportPolicy(
            bucket="private-delta-bucket",
            prefix="campaigns/three-site",
            webapp_fi_age_recipient="age1" + "a" * 30,
            webapp_ir_age_recipient="age1" + "c" * 30,
        )
        self.normal7, self.normal7_cutover = self.route_artifact(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            stream_generation_id="fi-ir-normal-term-7",
            source_signer=self.fi_signer,
            source_public_key=self.fi_key,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
        )
        self.promoted6, self.promoted6_cutover = self.route_artifact(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-promoted-term-6",
            source_signer=self.ir_signer,
            source_public_key=self.ir_key,
            writer_epoch=6,
            writer_lease_id="writer-lease-6",
        )
        routes = verify_object_delta_role_matrix_route_generations(
            normal_route=self.normal7,
            normal_source_cutover_attestation=self.normal7_cutover,
            promoted_route=self.promoted6,
            promoted_source_cutover_attestation=self.promoted6_cutover,
        )
        self.prior_term = self.witnessed_term(
            holder_site="webapp_fi",
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
        )
        matrix = authorize_object_delta_role_matrix(
            normal_route=self.normal7,
            promoted_route=self.promoted6,
            active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
            active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                holder_site="webapp_fi",
                writer_epoch=7,
                writer_lease_id="writer-lease-7",
            ),
        )
        self.prior_activation = bootstrap_object_delta_role_matrix_activation(
            prior_verified_matrix=matrix,
            witnessed_term=self.prior_term,
            route_generations=routes,
            now=NOW,
        )
        self.candidate_term = self.witnessed_term(
            holder_site="webapp_ir",
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
        )
        self._remote_ack_for_source_receipt_sha256: dict[str, object | None] = {}

    def witnessed_term(
        self,
        *,
        holder_site: str,
        writer_epoch: int,
        writer_lease_id: str,
        witness_transition_id: str | None = None,
    ):
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site=holder_site,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            witness_transition_id=(
                witness_transition_id
                or f"transition-{writer_epoch}-{writer_lease_id}"
            ),
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=50),
            witness_signer=self.witness_signer,
        )
        return verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=self.witness_key,
            maximum_lease_duration_seconds=90,
            safety_margin_seconds=5,
            now=NOW,
        )

    def route_artifact(
        self,
        *,
        source_site: str,
        destination_site: str,
        stream_generation_id: str,
        source_signer: Ed25519PrivateKey,
        source_public_key: bytes,
        writer_epoch: int,
        writer_lease_id: str,
    ) -> tuple[ObjectDeltaRoleMatrixRoute, dict]:
        source_pin = ObjectDeltaSourceCutoverPublicationPin(
            binding=ObjectDeltaSourceRuntimeBinding(
                source_site=source_site,
                destination_site=destination_site,
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id=stream_generation_id,
                expected_registry_fingerprint=FINGERPRINT,
            ),
            expected_source_public_key=source_public_key,
            transport_policy=self.policy,
        )
        receiver_binding = ObjectDeltaReceiverDeliveryBinding(
            policy=self.policy,
            permit=ObjectDeltaReceiverDeliveryPermit(
                source_site=source_site,
                destination_site=destination_site,
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id=stream_generation_id,
                bucket=self.policy.bucket,
                destination_age_recipient=destination_age_recipient(
                    self.policy,
                    destination_site=destination_site,
                ),
                controller_key_id=controller_key_id_from_public_key(self.controller_key),
                writer_epoch=writer_epoch,
                writer_lease_id=writer_lease_id,
            ),
            source_public_key=source_public_key,
            source_key_id=source_key_id_from_public_key(source_public_key),
            controller_public_key=self.controller_key,
            expected_registry_fingerprint=FINGERPRINT,
        )
        route = ObjectDeltaRoleMatrixRoute(
            source_pin=source_pin,
            receiver_binding=receiver_binding,
        )
        snapshot = {
            "source_generation": f"{source_site}-{stream_generation_id}-baseline",
            "snapshot_id": "20260731T120000Z-0123456789abcdef",
            "release_sha": RELEASE,
            "alembic_revision": "f2c7d8e9a0b1",
            "manifest_object_key": f"campaigns/three-site/{stream_generation_id}/snapshot.age",
            "manifest_object_version_id": f"snapshot-version-{writer_epoch}",
            "manifest_ciphertext_sha256": "a" * 64,
            "manifest_ciphertext_bytes": 1024,
            "database_sha256": "b" * 64,
            "uploads_sha256": "c" * 64,
        }
        gate_id = str(uuid4())
        baseline = build_object_delta_baseline_manifest(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id=stream_generation_id,
            registry_fingerprint=FINGERPRINT,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            snapshot=snapshot,
            write_gate_id=gate_id,
            source_signer=source_signer,
        )
        record = ObjectDeltaSourceCutoverRecord(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id=stream_generation_id,
            state="baseline_published",
            registry_fingerprint=FINGERPRINT,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            write_gate_id=gate_id,
            source_generation=snapshot["source_generation"],
            snapshot_id=snapshot["snapshot_id"],
            alembic_revision=snapshot["alembic_revision"],
            snapshot_manifest_object_key=snapshot["manifest_object_key"],
            snapshot_manifest_object_version_id=snapshot["manifest_object_version_id"],
            snapshot_manifest_ciphertext_sha256=snapshot["manifest_ciphertext_sha256"],
            snapshot_manifest_ciphertext_bytes=snapshot["manifest_ciphertext_bytes"],
            database_sha256=snapshot["database_sha256"],
            uploads_sha256=snapshot["uploads_sha256"],
            baseline_manifest_object_key=f"campaigns/three-site/{stream_generation_id}/baseline.age",
            baseline_manifest_object_version_id=f"baseline-version-{writer_epoch}",
            baseline_manifest_ciphertext_sha256="d" * 64,
            baseline_manifest_ciphertext_bytes=2048,
        )
        return route, build_object_delta_source_cutover_attestation(
            cutover=record,
            baseline_manifest=baseline,
            source_signer=source_signer,
        )

    def common(self, *, baseline_generation_id: str = "pg-base-fi-ir-0001") -> dict:
        policy_payload = {
            "bucket": self.policy.bucket,
            "prefix": self.policy.prefix,
            "webapp_fi_age_recipient": self.policy.webapp_fi_age_recipient,
            "webapp_ir_age_recipient": self.policy.webapp_ir_age_recipient,
        }
        source_sha = hashlib.sha256(self.fi_key).hexdigest()
        controller_sha = hashlib.sha256(self.controller_key).hexdigest()
        policy_sha = hashlib.sha256(canonical_json_bytes(policy_payload)).hexdigest()
        route_payload = {
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "campaign_id": CAMPAIGN,
            "release_sha": RELEASE,
            "registry_fingerprint": FINGERPRINT,
            "stream_generation_id": "fi-ir-normal-term-7",
            "source_key_sha256": source_sha,
            "controller_key_sha256": controller_sha,
            "transport_policy_sha256": policy_sha,
        }
        return {
            "continuity_id": "cont-20260731-0001",
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "campaign_id": CAMPAIGN,
            "release_sha": RELEASE,
            "registry_fingerprint": FINGERPRINT,
            "stream_generation_id": "fi-ir-normal-term-7",
            "baseline_generation_id": baseline_generation_id,
            "baseline_manifest_sha256": "a" * 64,
            "source_key_sha256": source_sha,
            "controller_key_sha256": controller_sha,
            "transport_policy_sha256": policy_sha,
            "route_binding_sha256": hashlib.sha256(canonical_json_bytes(route_payload)).hexdigest(),
            "prior_term_proof_sha256": self.prior_term.proof_sha256,
            "prior_holder_site": "webapp_fi",
            "prior_writer_epoch": 7,
            "prior_writer_lease_id": "writer-lease-7",
        }

    def evidence(
        self,
        *,
        source_ack: str = "0/120",
        receiver_replay: str = "0/130",
        blob_frontier: str = "0/130",
        baseline_lsn: str = "0/100",
        objects_complete: bool = True,
        common_overrides: dict | None = None,
        receiver_overrides: dict | None = None,
        blob_overrides: dict | None = None,
        continuity_overrides: dict | None = None,
        source_public_key: bytes | None = None,
        source_signer: Ed25519PrivateKey | None = None,
        acknowledgement_mode: str = PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_STRICT_REMOTE_DURABLE_REPLAY,
        remote_ack_evidence: object = Ellipsis,
    ):
        if acknowledgement_mode == PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_STRICT_REMOTE_DURABLE_REPLAY:
            if remote_ack_evidence is Ellipsis:
                try:
                    remote_ack_evidence = self.remote_ack(
                        target_acknowledged_wal_lsn=source_ack,
                        blob_object_frontier_wal_lsn=blob_frontier,
                    )
                except PhysicalWalRemoteAckError:
                    # The surrounding evidence constructor remains the
                    # subject under test for malformed local frontiers.
                    remote_ack_evidence = None
            if remote_ack_evidence is None:
                remote_ack_request_sha256 = None
                remote_ack_receipt_sha256 = None
            else:
                remote_ack_request_sha256 = hashlib.sha256(
                    remote_ack_evidence.source_request
                ).hexdigest()
                remote_ack_receipt_sha256 = hashlib.sha256(
                    remote_ack_evidence.destination_receipt
                ).hexdigest()
        else:
            remote_ack_evidence = None
            remote_ack_request_sha256 = None
            remote_ack_receipt_sha256 = None
        common = self.common()
        if common_overrides:
            common.update(common_overrides)
        source = sign(
            {
                "schema": PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
                "kind": "source_durable_wal_frontier",
                **common,
                "acknowledgement_mode": acknowledgement_mode,
                "baseline_wal_lsn": baseline_lsn,
                "acknowledged_durable_wal_lsn": source_ack,
                "observed_at": (NOW - timedelta(seconds=12)).isoformat(),
            },
            source_signer or self.fi_signer,
        )
        receiver = {
            "schema": PHYSICAL_WAL_RECEIVER_REPLAY_RECEIPT_SCHEMA,
            "kind": "receiver_replay_wal_frontier",
            **common,
            "source_durability_receipt_sha256": digest(source),
            "receiver_replay_wal_lsn": receiver_replay,
            "observed_at": (NOW - timedelta(seconds=9)).isoformat(),
        }
        if receiver_overrides:
            receiver.update(receiver_overrides)
        receiver = sign(receiver, self.controller_signer)
        blob = {
            "schema": PHYSICAL_WAL_BLOB_OBJECT_RECEIPT_SCHEMA,
            "kind": "blob_object_frontier",
            **common,
            "source_durability_receipt_sha256": digest(source),
            "receiver_replay_receipt_sha256": digest(receiver),
            "blob_object_frontier_wal_lsn": blob_frontier,
            "objects_complete": objects_complete,
            "object_manifest_sha256": "b" * 64,
            "object_manifest_version_id": "blob-manifest-version-1",
            "observed_at": (NOW - timedelta(seconds=6)).isoformat(),
        }
        if blob_overrides:
            blob.update(blob_overrides)
        blob = sign(blob, self.controller_signer)
        continuity = {
            "schema": PHYSICAL_WAL_CONTINUITY_ARTIFACT_SCHEMA,
            "kind": "physical_wal_continuity",
            **common,
            "candidate_term_proof_sha256": self.candidate_term.proof_sha256,
            "candidate_holder_site": self.candidate_term.holder_site,
            "candidate_writer_epoch": self.candidate_term.writer_epoch,
            "candidate_writer_lease_id": self.candidate_term.writer_lease_id,
            "source_durability_receipt_sha256": digest(source),
            "receiver_replay_receipt_sha256": digest(receiver),
            "blob_object_receipt_sha256": digest(blob),
            "source_acknowledged_durable_wal_lsn": source_ack,
            "receiver_replay_wal_lsn": receiver_replay,
            "blob_object_frontier_wal_lsn": blob_frontier,
            "objects_complete": objects_complete,
            "remote_ack_request_sha256": remote_ack_request_sha256,
            "remote_ack_receipt_sha256": remote_ack_receipt_sha256,
            "issued_at": (NOW - timedelta(seconds=3)).isoformat(),
        }
        if continuity_overrides:
            continuity.update(continuity_overrides)
        continuity = sign(continuity, self.controller_signer)
        result = verify_physical_wal_promotion_evidence(
            source_durability_receipt=source,
            receiver_replay_receipt=receiver,
            blob_object_receipt=blob,
            continuity_artifact=continuity,
            source_public_key=source_public_key or self.fi_key,
            controller_public_key=self.controller_key,
        )
        self._remote_ack_for_source_receipt_sha256[
            hashlib.sha256(result.source_durability_receipt).hexdigest()
        ] = remote_ack_evidence
        return result

    def remote_ack(
        self,
        *,
        target_acknowledged_wal_lsn: str = "0/120",
        blob_object_frontier_wal_lsn: str = "0/130",
        destination_age_recipient_value: str | None = None,
        request_suffix: str = "0001",
        receipt_suffix: str = "0001",
    ):
        binding = build_physical_wal_remote_ack_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            destination_age_recipient=(
                destination_age_recipient_value
                or destination_age_recipient(self.policy, destination_site="webapp_ir")
            ),
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id="fi-ir-normal-term-7",
            baseline_generation_id="pg-base-fi-ir-0001",
            baseline_manifest_sha256="a" * 64,
            writer_epoch=7,
            writer_holder_site="webapp_fi",
            writer_lease_id="writer-lease-7",
            witnessed_term_proof_sha256=self.prior_term.proof_sha256,
            target_acknowledged_wal_lsn=target_acknowledged_wal_lsn,
            blob_object_frontier_wal_lsn=blob_object_frontier_wal_lsn,
            manifest_sha256es=("a" * 64, "b" * 64, "c" * 64),
            object_versions=(
                ("physical/fi-ir/base/backup-001.age", "base-version-001"),
                ("physical/fi-ir/wal/000000010000000000000001.age", "wal-version-001"),
                ("physical/fi-ir/blob/inventory-001.age", "blob-version-001"),
            ),
        )
        request = build_physical_wal_remote_ack_request(
            binding=binding,
            request_id=f"promotion-ack-request-{request_suffix}",
            request_nonce="Q" * 22,
            issued_at=NOW - timedelta(seconds=15),
            source_signer=self.fi_signer,
        )
        receipt = build_physical_wal_remote_ack_receipt(
            source_request=request,
            receipt_id=f"promotion-ack-receipt-{receipt_suffix}",
            receipt_nonce="R" * 22,
            acknowledged_at=NOW - timedelta(seconds=13),
            destination_signer=self.controller_signer,
        )
        return verify_physical_wal_remote_ack_evidence(
            source_request=request,
            destination_receipt=receipt,
            expected_binding=binding,
            expected_source_public_key=self.fi_key,
            expected_destination_public_key=self.controller_key,
            now=NOW,
        )

    def assess(self, evidence, *, candidate=None, remote_ack=Ellipsis):
        if remote_ack is Ellipsis:
            source_receipt = getattr(evidence, "source_durability_receipt", None)
            remote_ack = (
                self._remote_ack_for_source_receipt_sha256.get(
                    hashlib.sha256(source_receipt).hexdigest()
                )
                if isinstance(source_receipt, bytes)
                else None
            )
        return assess_physical_wal_promotion(
            prior_activation=self.prior_activation,
            candidate_witnessed_term=candidate or self.candidate_term,
            verified_evidence=evidence,
            now=NOW,
            verified_remote_ack=remote_ack,
        )

    def test_fully_bound_baseline_wal_and_blob_frontiers_are_eligible(self) -> None:
        assessment = self.assess(self.evidence())

        self.assertEqual(assessment.status, "eligible")
        self.assertTrue(assessment.eligible)
        self.assertEqual(assessment.reason_codes, ())
        self.assertEqual(assessment.source_site, "webapp_fi")
        self.assertEqual(assessment.target_site, "webapp_ir")
        self.assertEqual(assessment.acknowledged_durable_wal_lsn, "0/120")
        self.assertIs(require_physical_wal_promotion_eligible(assessment), assessment)

    def test_contract_has_no_runtime_database_network_or_filesystem_adapter_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "core/physical_wal_promotion_gate.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "import sqlalchemy",
            "from sqlalchemy",
            "models.",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "import aiohttp",
            "from aiohttp",
            "import socket",
            "from socket",
            "import subprocess",
            "from subprocess",
            "import os",
            "from os",
            "from scripts",
            "import scripts",
        )
        self.assertFalse([item for item in forbidden if item in source])

    def test_receiver_replay_behind_acknowledged_frontier_is_blocked(self) -> None:
        assessment = self.assess(self.evidence(receiver_replay="0/110"))

        self.assertEqual(assessment.status, "blocked")
        self.assertIn("RECEIVER_REPLAY_BEHIND_ACKNOWLEDGED_FRONTIER", assessment.reason_codes)

    def test_blob_frontier_must_be_complete_and_at_least_the_acknowledged_frontier(self) -> None:
        assessment = self.assess(
            self.evidence(blob_frontier="0/110", objects_complete=False)
        )

        self.assertIn("BLOB_OBJECT_FRONTIER_BEHIND_ACKNOWLEDGED_FRONTIER", assessment.reason_codes)
        self.assertIn("BLOB_OBJECT_FRONTIER_INCOMPLETE", assessment.reason_codes)

    def test_source_acknowledged_frontier_cannot_precede_the_baseline(self) -> None:
        assessment = self.assess(self.evidence(baseline_lsn="0/120", source_ack="0/110"))

        self.assertIn("SOURCE_ACKNOWLEDGED_FRONTIER_PRECEDES_BASELINE", assessment.reason_codes)

    def test_archive_only_claim_can_never_be_an_acknowledged_promotion_frontier(self) -> None:
        assessment = self.assess(
            self.evidence(
                acknowledgement_mode=PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_ARCHIVE_ONLY
            )
        )

        self.assertIn(
            "SOURCE_ACKNOWLEDGEMENT_NOT_STRICT_REMOTE_DURABLE_REPLAY",
            assessment.reason_codes,
        )

    def test_strict_claim_requires_an_exact_verified_remote_ack(self) -> None:
        missing = self.assess(self.evidence(), remote_ack=None)
        self.assertIn("REMOTE_ACK_UNVERIFIED", missing.reason_codes)

        wrong_frontier = self.assess(
            self.evidence(),
            remote_ack=self.remote_ack(target_acknowledged_wal_lsn="0/110"),
        )
        self.assertIn("REMOTE_ACK_FRONTIER_OR_BLOB_MISMATCH", wrong_frontier.reason_codes)

        wrong_recipient = self.assess(
            self.evidence(),
            remote_ack=self.remote_ack(destination_age_recipient_value="age1" + "p" * 30),
        )
        self.assertIn("REMOTE_ACK_ACTIVE_ROUTE_OR_BASELINE_MISMATCH", wrong_recipient.reason_codes)

    def test_strict_claim_binds_the_exact_remote_ack_pair_into_continuity(self) -> None:
        evidence = self.evidence()
        equally_bound_but_different_pair = self.remote_ack(
            request_suffix="0002",
            receipt_suffix="0002",
        )

        assessment = self.assess(
            evidence,
            remote_ack=equally_bound_but_different_pair,
        )

        self.assertIn("REMOTE_ACK_CONTINUITY_BINDING_MISMATCH", assessment.reason_codes)

    def test_candidate_must_be_fresh_new_witness_term_for_the_prior_inactive_site(self) -> None:
        same_holder = self.witnessed_term(
            holder_site="webapp_fi",
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
        )
        assessment = self.assess(self.evidence(), candidate=same_holder)
        self.assertIn("CANDIDATE_TERM_DOES_NOT_HOLD_INACTIVE_STANDBY", assessment.reason_codes)
        self.assertIn("CONTINUITY_NOT_BOUND_TO_CANDIDATE_TERM", assessment.reason_codes)

        regressed = self.witnessed_term(
            holder_site="webapp_ir",
            writer_epoch=7,
            writer_lease_id="writer-lease-9",
        )
        assessment = self.assess(self.evidence(), candidate=regressed)
        self.assertIn("CANDIDATE_TERM_NOT_STRICTLY_NEWER", assessment.reason_codes)

        reused_lease = self.witnessed_term(
            holder_site="webapp_ir",
            writer_epoch=8,
            writer_lease_id="writer-lease-7",
        )
        assessment = self.assess(self.evidence(), candidate=reused_lease)
        self.assertIn("CANDIDATE_TERM_REUSES_PRIOR_LEASE", assessment.reason_codes)

        reused_transition = self.witnessed_term(
            holder_site="webapp_ir",
            writer_epoch=8,
            writer_lease_id="writer-lease-8-other",
            witness_transition_id=self.prior_term.witness_transition_id,
        )
        assessment = self.assess(self.evidence(), candidate=reused_transition)
        self.assertIn(
            "CANDIDATE_TERM_REUSES_WITNESS_TRANSITION",
            assessment.reason_codes,
        )

    def test_all_artifacts_must_bind_the_active_route_campaign_release_key_and_policy(self) -> None:
        evidence = self.evidence(common_overrides={"campaign_id": "wa-other-campaign-20260731"})
        assessment = self.assess(evidence)

        self.assertIn("CONTINUITY_NOT_BOUND_TO_ACTIVE_ROUTE", assessment.reason_codes)

    def test_baseline_generation_and_receipt_chain_must_be_consistent(self) -> None:
        evidence = self.evidence(blob_overrides={"baseline_generation_id": "pg-base-fi-ir-0002"})
        assessment = self.assess(evidence)
        self.assertIn("BASELINE_OR_IDENTITY_BINDING_MISMATCH", assessment.reason_codes)

        evidence = self.evidence(receiver_overrides={"source_durability_receipt_sha256": "0" * 64})
        assessment = self.assess(evidence)
        self.assertIn("CONTINUITY_ARTIFACT_RECEIPT_BINDING_MISMATCH", assessment.reason_codes)

    def test_continuity_artifact_must_bind_the_exact_frontiers_and_candidate_term(self) -> None:
        evidence = self.evidence(
            continuity_overrides={
                "candidate_term_proof_sha256": "f" * 64,
                "receiver_replay_wal_lsn": "0/140",
            }
        )
        assessment = self.assess(evidence)

        self.assertIn("CONTINUITY_NOT_BOUND_TO_CANDIDATE_TERM", assessment.reason_codes)
        self.assertIn("CONTINUITY_ARTIFACT_FRONTIER_BINDING_MISMATCH", assessment.reason_codes)

    def test_raw_or_forged_evidence_is_not_authority(self) -> None:
        assessment = self.assess({})
        self.assertEqual(assessment.reason_codes, ("CONTINUITY_EVIDENCE_UNVERIFIED",))

        forged = replace(self.evidence())
        assessment = self.assess(forged)
        self.assertEqual(assessment.reason_codes, ("CONTINUITY_EVIDENCE_UNVERIFIED",))

        assessment = assess_physical_wal_promotion(
            prior_activation=self.prior_activation,
            candidate_witnessed_term={},
            verified_evidence=self.evidence(),
            now=NOW,
        )
        self.assertEqual(assessment.reason_codes, ("CANDIDATE_WITNESS_TERM_UNVERIFIED",))

    def test_signature_and_active_route_key_are_rechecked(self) -> None:
        other_source_signer = Ed25519PrivateKey.generate()
        other_source_key = public_key(other_source_signer)
        evidence = self.evidence(
            source_public_key=other_source_key,
            source_signer=other_source_signer,
        )
        assessment = self.assess(evidence)
        self.assertEqual(assessment.reason_codes, ("CONTINUITY_EVIDENCE_UNVERIFIED",))

    def test_lsn_parser_rejects_overflow_replay_aliases_and_lowercase(self) -> None:
        for invalid_lsn in ("100000000/0", "0/100000000", "00/1", "0/0001", "0/ff"):
            with self.subTest(invalid_lsn=invalid_lsn):
                with self.assertRaises(PhysicalWalPromotionGateError):
                    self.evidence(source_ack=invalid_lsn)

    def test_evidence_must_be_fresh_and_time_ordered(self) -> None:
        evidence = self.evidence(
            continuity_overrides={"issued_at": (NOW - timedelta(seconds=61)).isoformat()}
        )
        assessment = self.assess(evidence)
        self.assertIn("CONTINUITY_EVIDENCE_STALE_OR_TIME_ORDER_INVALID", assessment.reason_codes)

    def test_non_eligible_result_cannot_be_mistaken_for_writer_authority(self) -> None:
        assessment = self.assess(self.evidence(receiver_replay="0/110"))
        with self.assertRaisesRegex(PhysicalWalPromotionGateError, "RECEIVER_REPLAY"):
            require_physical_wal_promotion_eligible(assessment)

        forged = PhysicalWalPromotionAssessment(status="eligible", reason_codes=())
        self.assertFalse(forged.eligible)
        with self.assertRaisesRegex(PhysicalWalPromotionGateError, "not authorized"):
            require_physical_wal_promotion_eligible(forged)


if __name__ == "__main__":
    unittest.main()
