from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_full_matrix_campaign_readiness import (
    PhysicalFullMatrixCampaignBinding,
)
import core.physical_full_matrix_campaign_readiness as readiness
import core.physical_strict_remote_ack_writer_response as strict
from core.physical_wal_remote_ack import (
    build_physical_wal_remote_ack_binding,
    build_physical_wal_remote_ack_request,
    verify_physical_wal_remote_ack_evidence,
    verify_physical_wal_remote_ack_request,
)
from core.physical_wal_remote_ack_receiver_ledger import (
    PhysicalWalRemoteAckReceiverLedgerConfig,
    PhysicalWalRemoteAckReceiverRecoveryEvidence,
    derive_physical_wal_remote_ack_receiver_request_binding_sha256,
    issue_physical_wal_remote_ack_receiver_receipt,
    verify_physical_wal_remote_ack_receiver_recovery_evidence,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "physical-strict-ack-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
SCHEMA_REVISION = "alembic-20260731"
RECIPIENT_IR = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
BASE_HASH = "b" * 64
MANIFEST_HASHES = (BASE_HASH, "c" * 64, "d" * 64)
OBJECT_VERSIONS = (
    ("physical/fi-ir/base/backup-001.age", "base-version-001"),
    ("physical/fi-ir/blob/inventory-001.age", "inventory-version-001"),
    ("physical/fi-ir/wal/0001.age", "wal-version-0001"),
)
WRITER_LEASE = "writer-lease-seven"
TRANSITION = "transition-strict-20260731"


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _DurableCommitBoundary:
    """Test-only adapter that returns the required signed local receipt."""

    def __init__(self, signer: Ed25519PrivateKey, *, tamper: bool = False) -> None:
        self.signer = signer
        self.tamper = tamper
        self.calls = 0
        self.instructions: list[strict.PhysicalStrictRemoteAckWriterCommitInstruction] = []

    def commit_after_verified_remote_ack(
        self,
        *,
        instruction: strict.PhysicalStrictRemoteAckWriterCommitInstruction,
    ) -> bytes:
        self.calls += 1
        self.instructions.append(instruction)
        facts = strict._normalise_binding(instruction.binding, code="TEST_BINDING_INVALID")
        unsigned: dict[str, object] = {
            "schema": strict.PHYSICAL_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_SCHEMA,
            "version": 1,
            "kind": "durable-local-writer-response",
            "configuration_sha256": instruction.configuration_sha256,
            "permit_binding_sha256": instruction.permit_binding_sha256,
            "commit_id": instruction.commit_id,
            "source_request_sha256": instruction.source_request_sha256,
            "destination_receipt_sha256": instruction.destination_receipt_sha256,
            "request_id": instruction.request_id,
            "request_nonce": instruction.request_nonce,
            "receipt_id": instruction.receipt_id,
            "receipt_nonce": instruction.receipt_nonce,
            "receiver_recovery_evidence_sha256": instruction.receiver_recovery_evidence_sha256,
            "receiver_replay_lsn": instruction.receiver_replay_lsn,
            "binding": strict._binding_payload(facts),
            "atomic_commit_boundary": strict.STRICT_REMOTE_ACK_WRITER_ATOMIC_COMMIT_BOUNDARY,
            "local_commit_record_id": "local-commit-record-20260731",
            "local_response_id": "local-response-record-20260731",
            "committed_at": NOW.isoformat().replace("+00:00", "Z"),
        }
        if self.tamper:
            unsigned["receipt_nonce"] = "Z" * 22
        signature = self.signer.sign(strict._COMMIT_DOMAIN + canonical_json_bytes(unsigned))
        return canonical_json_bytes(
            {
                **unsigned,
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            }
        )


@unittest.skipUnless(os.geteuid() == 0, "root-only strict writer boundary tests require root")
class PhysicalStrictRemoteAckWriterResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi = Ed25519PrivateKey.generate()
        self.ir = Ed25519PrivateKey.generate()
        self.witness = Ed25519PrivateKey.generate()
        self.fence_signer = Ed25519PrivateKey.generate()
        self.commit_signer = Ed25519PrivateKey.generate()
        self.temporary = tempfile.TemporaryDirectory()
        self.receiver_root = self._secure_directory("receiver")
        self.writer_root = self._secure_directory("writer")

        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=7,
            writer_lease_id=WRITER_LEASE,
            witness_transition_id=TRANSITION,
            issued_at=NOW - timedelta(seconds=4),
            expires_at=NOW + timedelta(seconds=60),
            witness_signer=self.witness,
        )
        self.term = verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=public_key(self.witness),
            maximum_lease_duration_seconds=120,
            safety_margin_seconds=2,
            now=NOW,
        )
        self.remote_binding = build_physical_wal_remote_ack_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            destination_age_recipient=RECIPIENT_IR,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id="strict-ack-stream-20260731",
            baseline_generation_id="strict-ack-base-20260731",
            baseline_manifest_sha256=BASE_HASH,
            writer_epoch=7,
            writer_holder_site="webapp_fi",
            writer_lease_id=WRITER_LEASE,
            witnessed_term_proof_sha256=self.term.proof_sha256,
            target_acknowledged_wal_lsn="0/2000000",
            blob_object_frontier_wal_lsn="0/2000000",
            manifest_sha256es=MANIFEST_HASHES,
            object_versions=OBJECT_VERSIONS,
        )
        request_mapping = build_physical_wal_remote_ack_request(
            binding=self.remote_binding,
            request_id="strict-request-identity-0001",
            request_nonce="R" * 22,
            issued_at=NOW - timedelta(seconds=3),
            source_signer=self.fi,
        )
        self.request = verify_physical_wal_remote_ack_request(
            source_request=request_mapping,
            expected_binding=self.remote_binding,
            expected_source_public_key=public_key(self.fi),
            now=NOW,
        )
        recovery = PhysicalWalRemoteAckReceiverRecoveryEvidence(
            source_request_sha256=self._sha(self.request.source_request),
            receiver_recovery_evidence_sha256="e" * 64,
            receiver_site="webapp_ir",
            source_site="webapp_fi",
            destination_site="webapp_ir",
            request_binding_sha256=(
                derive_physical_wal_remote_ack_receiver_request_binding_sha256(
                    source_request=self.request,
                    now=NOW,
                )
            ),
            manifest_sha256es=MANIFEST_HASHES,
            object_versions=self.remote_binding.object_versions,
            replay_lsn="0/2000000",
            observed_at=NOW,
            in_recovery=True,
            role="standby",
        )
        self.recovery = verify_physical_wal_remote_ack_receiver_recovery_evidence(
            source_request=self.request,
            recovery_evidence=recovery,
            now=NOW,
        )
        self.remote_ledger = issue_physical_wal_remote_ack_receiver_receipt(
            config=PhysicalWalRemoteAckReceiverLedgerConfig(
                state_root=self.receiver_root,
                expected_binding=self.remote_binding,
                expected_source_public_key=public_key(self.fi),
                expected_destination_public_key=public_key(self.ir),
                enabled=True,
                maximum_entries=8,
            ),
            source_request=self.request,
            recovery_evidence=self.recovery,
            destination_signer=self.ir,
            now=NOW,
        )
        self.remote_ack = verify_physical_wal_remote_ack_evidence(
            source_request=self.request.source_request,
            destination_receipt=self.remote_ledger.destination_receipt,
            expected_binding=self.remote_binding,
            expected_source_public_key=public_key(self.fi),
            expected_destination_public_key=public_key(self.ir),
            now=NOW,
        )
        self.strict_binding = strict.PhysicalStrictRemoteAckWriterResponseBinding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            schema_revision=SCHEMA_REVISION,
            stream_generation_id=self.remote_binding.stream_generation_id,
            baseline_generation_id=self.remote_binding.baseline_generation_id,
            baseline_manifest_sha256=BASE_HASH,
            baseline_wal_lsn="0/1000000",
            timeline_id=1,
            destination_age_recipient=RECIPIENT_IR,
            route_binding_sha256="a" * 64,
            writer_epoch=7,
            writer_lease_id=WRITER_LEASE,
            witness_transition_id=TRANSITION,
            witnessed_term_proof_sha256=self.term.proof_sha256,
            target_acknowledged_wal_lsn="0/2000000",
            blob_object_frontier_wal_lsn="0/2000000",
            manifest_sha256es=MANIFEST_HASHES,
            object_versions=OBJECT_VERSIONS,
        )
        self.config = strict.PhysicalStrictRemoteAckWriterResponseConfig(
            state_root=self.writer_root,
            expected_binding=self.strict_binding,
            expected_source_remote_ack_public_key=public_key(self.fi),
            expected_destination_remote_ack_public_key=public_key(self.ir),
            fence_signer_public_key=public_key(self.fence_signer),
            local_commit_signer_public_key=public_key(self.commit_signer),
            enabled=True,
            maximum_evidence_age_seconds=60,
            maximum_entries=8,
        )
        self.fence = strict.verify_physical_strict_remote_ack_writer_fence(
            self._fence_receipt(),
            config=self.config,
            now=NOW,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _secure_directory(self, name: str) -> Path:
        value = Path(self.temporary.name, name)
        value.mkdir(mode=0o700)
        os.chmod(value, 0o700)
        return value.resolve()

    @staticmethod
    def _sha(value: bytes) -> str:
        import hashlib

        return hashlib.sha256(value).hexdigest()

    def _fence_receipt(
        self,
        *,
        issued_at: datetime = NOW - timedelta(seconds=1),
        expires_at: datetime = NOW + timedelta(seconds=50),
    ) -> dict[str, object]:
        facts = strict._normalise_binding(self.strict_binding, code="TEST_BINDING_INVALID")
        unsigned: dict[str, object] = {
            "schema": strict.PHYSICAL_STRICT_REMOTE_ACK_WRITER_FENCE_RECEIPT_SCHEMA,
            "version": 1,
            "kind": "active-source-fence",
            "binding": strict._binding_payload(facts),
            "fence_id": "writer-fence-identity-0001",
            "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }
        signature = self.fence_signer.sign(strict._FENCE_DOMAIN + canonical_json_bytes(unsigned))
        return {
            **unsigned,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }

    def _permit(self):
        return strict.issue_physical_strict_remote_ack_writer_commit_permit(
            config=self.config,
            witnessed_term=self.term,
            remote_ack_evidence=self.remote_ack,
            receiver_recovery_evidence=self.recovery,
            durable_ledger_result=self.remote_ledger,
            fence=self.fence,
            now=NOW,
        )

    def _readiness_binding(self) -> PhysicalFullMatrixCampaignBinding:
        return PhysicalFullMatrixCampaignBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            schema_revision=SCHEMA_REVISION,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            baseline_generation_id=self.strict_binding.baseline_generation_id,
            baseline_manifest_sha256=BASE_HASH,
            baseline_wal_lsn="0/1000000",
            timeline_id=1,
            stream_generation_id=self.strict_binding.stream_generation_id,
            destination_age_recipient=RECIPIENT_IR,
            route_binding_sha256="a" * 64,
            writer_epoch=7,
            writer_lease_id=WRITER_LEASE,
            witness_transition_id=TRANSITION,
            witnessed_term_proof_sha256=self.term.proof_sha256,
            target_acknowledged_wal_lsn="0/2000000",
            blob_object_frontier_wal_lsn="0/2000000",
            recovery_stage_bundle_id="f" * 64,
            recovery_stage_receipt_sha256="1" * 64,
            deployment_operation_id="c65a2bb2-3d57-4cab-963c-70f037c3b60d",
            deployment_manifest_sha256="2" * 64,
            p0_operation_id=UUID("4a9ed217-a69a-4b1a-b1f0-9db8c35fb802"),
        )

    def test_full_sequence_binds_verified_remote_ack_to_one_local_durable_response(self) -> None:
        permit = self._permit()
        boundary = _DurableCommitBoundary(self.commit_signer)
        evidence = strict.commit_physical_strict_remote_ack_writer_response(
            config=self.config,
            permit=permit,
            boundary=boundary,
            now=NOW,
        )
        self.assertEqual(boundary.calls, 1)
        self.assertEqual(permit.source_request_sha256, evidence.instruction.source_request_sha256)
        self.assertEqual(permit.destination_receipt_sha256, evidence.instruction.destination_receipt_sha256)
        self.assertIs(
            strict.require_verified_physical_strict_remote_ack_writer_commit_evidence(
                evidence,
                config=self.config,
                now=NOW,
            ),
            evidence,
        )
        observation = strict.mint_physical_strict_remote_ack_writer_response_observation(
            evidence,
            config=self.config,
            now=NOW,
        )
        self.assertIs(
            strict.require_verified_physical_strict_remote_ack_writer_response_observation(
                observation,
                config=self.config,
                now=NOW,
            ),
            observation,
        )
        projection = strict.project_verified_physical_strict_remote_ack_writer_response_observation(
            observation,
            now=NOW,
        )
        self.assertEqual("webapp_fi", projection.source_site)
        self.assertEqual("webapp_ir", projection.destination_site)
        self.assertEqual("0/2000000", projection.target_acknowledged_wal_lsn)
        readiness._expect_verified_strict_writer_response(
            observation,
            binding=self._readiness_binding(),
            now=NOW,
            maximum_evidence_age_seconds=90,
        )
        self.assertFalse(hasattr(observation, "commit"))
        self.assertFalse(hasattr(observation, "promote"))

    def test_no_ack_or_changed_term_never_reaches_writer_callback(self) -> None:
        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "REMOTE_ACK_EVIDENCE_INVALID",
        ):
            strict.issue_physical_strict_remote_ack_writer_commit_permit(
                config=self.config,
                witnessed_term=self.term,
                remote_ack_evidence=None,
                receiver_recovery_evidence=self.recovery,
                durable_ledger_result=self.remote_ledger,
                fence=self.fence,
                now=NOW,
            )

        permit = self._permit()
        boundary = _DurableCommitBoundary(self.commit_signer)
        changed_proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=8,
            writer_lease_id="writer-lease-eight",
            witness_transition_id="transition-strict-20260732",
            issued_at=NOW - timedelta(seconds=3),
            expires_at=NOW + timedelta(seconds=60),
            witness_signer=self.witness,
        )
        changed_term = verify_object_delta_role_matrix_witnessed_term(
            changed_proof,
            witness_public_key=public_key(self.witness),
            maximum_lease_duration_seconds=120,
            safety_margin_seconds=2,
            now=NOW,
        )
        object.__setattr__(permit, "witnessed_term", changed_term)
        with self.assertRaises(strict.PhysicalStrictRemoteAckWriterResponseError):
            strict.commit_physical_strict_remote_ack_writer_response(
                config=self.config,
                permit=permit,
                boundary=boundary,
                now=NOW,
            )
        self.assertEqual(0, boundary.calls)

    def test_tamper_and_replay_cannot_call_a_second_writer_commit(self) -> None:
        permit = self._permit()
        boundary = _DurableCommitBoundary(self.commit_signer)
        original_source_request_sha256 = permit.source_request_sha256
        object.__setattr__(permit, "source_request_sha256", "f" * 64)
        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "VERIFIED_COMMIT_PERMIT_TAMPERED_OR_DIVERGED",
        ):
            strict.commit_physical_strict_remote_ack_writer_response(
                config=self.config,
                permit=permit,
                boundary=boundary,
                now=NOW,
            )
        self.assertEqual(0, boundary.calls)
        object.__setattr__(permit, "source_request_sha256", original_source_request_sha256)
        evidence = strict.commit_physical_strict_remote_ack_writer_response(
            config=self.config,
            permit=permit,
            boundary=boundary,
            now=NOW,
        )
        self.assertEqual(1, boundary.calls)
        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "REMOTE_ACK_ALREADY_CONSUMED",
        ):
            strict.commit_physical_strict_remote_ack_writer_response(
                config=self.config,
                permit=permit,
                boundary=boundary,
                now=NOW,
        )
        self.assertEqual(1, boundary.calls)
        self.assertEqual(permit, evidence.permit)

    def test_callback_receipt_mismatch_cannot_mint_durable_evidence(self) -> None:
        permit = self._permit()
        boundary = _DurableCommitBoundary(self.commit_signer, tamper=True)
        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "DURABLE_COMMIT_RECEIPT_BINDING_MISMATCH",
        ):
            strict.commit_physical_strict_remote_ack_writer_response(
                config=self.config,
                permit=permit,
                boundary=boundary,
                now=NOW,
            )
        self.assertEqual(1, boundary.calls)
        retry = _DurableCommitBoundary(self.commit_signer)
        strict.commit_physical_strict_remote_ack_writer_response(
            config=self.config,
            permit=permit,
            boundary=retry,
            now=NOW,
        )
        self.assertEqual(1, retry.calls)

    def test_stale_fence_wrong_direction_and_bool_integer_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "FENCE_RECEIPT_EXPIRED",
        ):
            strict.verify_physical_strict_remote_ack_writer_fence(
                self._fence_receipt(
                    issued_at=NOW - timedelta(seconds=10),
                    expires_at=NOW - timedelta(seconds=1),
                ),
                config=self.config,
                now=NOW,
            )

        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "RECEIVER_RECOVERY_INVALID",
        ):
            strict.issue_physical_strict_remote_ack_writer_commit_permit(
                config=self.config,
                witnessed_term=self.term,
                remote_ack_evidence=self.remote_ack,
                receiver_recovery_evidence=self.recovery,
                durable_ledger_result=self.remote_ledger,
                fence=self.fence,
                now=NOW + timedelta(seconds=31),
            )

        bool_epoch_config = replace(
            self.config,
            expected_binding=replace(self.strict_binding, writer_epoch=True),
        )
        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "CONFIG_BINDING_INVALID",
        ):
            strict.issue_physical_strict_remote_ack_writer_commit_permit(
                config=bool_epoch_config,
                witnessed_term=self.term,
                remote_ack_evidence=self.remote_ack,
                receiver_recovery_evidence=self.recovery,
                durable_ledger_result=self.remote_ledger,
                fence=self.fence,
                now=NOW,
            )

        wrong_direction_config = replace(
            self.config,
            expected_binding=replace(
                self.strict_binding,
                source_site="webapp_ir",
                destination_site="webapp_fi",
            ),
        )
        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "CONFIG_BINDING_INVALID",
        ):
            strict.issue_physical_strict_remote_ack_writer_commit_permit(
                config=wrong_direction_config,
                witnessed_term=self.term,
                remote_ack_evidence=self.remote_ack,
                receiver_recovery_evidence=self.recovery,
                durable_ledger_result=self.remote_ledger,
                fence=self.fence,
                now=NOW,
            )

    def test_observation_cannot_be_minted_from_raw_booleans_or_unverified_data(self) -> None:
        with self.assertRaisesRegex(
            strict.PhysicalStrictRemoteAckWriterResponseError,
            "VERIFIED_COMMIT_EVIDENCE_REQUIRED",
        ):
            strict.mint_physical_strict_remote_ack_writer_response_observation(
                object(),
                config=self.config,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
