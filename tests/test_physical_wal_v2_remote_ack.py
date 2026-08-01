from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2_remote_ack as remote_ack
from core import physical_wal_v2_remote_ack_receiver_ledger as receiver_ledger
from core import physical_wal_v2_strict_remote_ack_writer_response as strict_writer
from core.physical_wal_v2_remote_ack import (
    PhysicalWalV2RemoteAckConfig,
    PhysicalWalV2RemoteAckCoverageInputs,
    PhysicalWalV2RemoteAckError,
    PhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    build_physical_wal_v2_remote_ack_receipt,
    build_physical_wal_v2_remote_ack_request,
    mint_physical_wal_v2_remote_ack_context,
    require_verified_physical_wal_v2_remote_ack_context,
    require_verified_physical_wal_v2_remote_ack_evidence,
    require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence,
    verify_physical_wal_v2_remote_ack_evidence,
    verify_physical_wal_v2_remote_ack_receiver_recovery_evidence,
    verify_physical_wal_v2_remote_ack_request,
)
from core.physical_wal_v2_remote_ack_receiver_ledger import (
    PhysicalWalV2RemoteAckReceiverLedgerConfig,
    PhysicalWalV2RemoteAckReceiverLedgerError,
    VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
    issue_physical_wal_v2_remote_ack_receiver_receipt,
    require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt,
)
from core.physical_wal_v2_strict_remote_ack_writer_response import (
    PhysicalWalV2StrictRemoteAckWriterResponseConfig,
    PhysicalWalV2StrictRemoteAckWriterResponseError,
    VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation,
    commit_physical_wal_v2_strict_remote_ack_writer_response,
    require_verified_physical_wal_v2_strict_remote_ack_writer_response_observation,
)
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW
from tests.test_physical_wal_v2_remote_ack_coverage import (
    PhysicalWalV2RemoteAckCoverageTests,
)
from tests.test_physical_full_matrix_v2_recovery_evidence import (
    PhysicalFullMatrixV2RecoveryEvidenceTests,
)


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalWalV2RemoteAckTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reuse the independent existing V2 coverage fixture rather than
        # synthesising any remote-ack-shaped input in this test.
        fixture = PhysicalWalV2RemoteAckCoverageTests(methodName="runTest")
        fixture.setUp()
        self.fixture = fixture
        self.coverage = fixture._mint()
        self.inputs = PhysicalWalV2RemoteAckCoverageInputs(
            base_backup_evidence=fixture.base,
            blob_frontier_coverage=fixture.blob,
            blob_owner_coverage=fixture.owner_coverage,
            blob_expected_owner_public_key=_public(fixture.owner),
            target_wal_continuity=fixture.continuity,
            target_wal_continuity_receipt=fixture.continuity_receipt,
            manifest=fixture.evidence.manifest,
            handoff_receipt=fixture.evidence.handoff,
            blob_scope=fixture.blob_scope,
            continuity_scope=fixture.continuity_scope,
            scope=fixture.scope,
        )
        self.context = mint_physical_wal_v2_remote_ack_context(
            coverage=self.coverage,
            inputs=self.inputs,
            now=NOW,
        )
        self.source = Ed25519PrivateKey.generate()
        self.destination = Ed25519PrivateKey.generate()
        self.config = PhysicalWalV2RemoteAckConfig(
            expected_context_sha256=self.context.context_sha256,
            expected_source_site=self.context.source_site,
            expected_destination_site=self.context.destination_site,
            expected_source_public_key=_public(self.source),
            expected_destination_public_key=_public(self.destination),
            enabled=True,
            maximum_evidence_age_seconds=45,
        )
        self.request_raw = build_physical_wal_v2_remote_ack_request(
            config=self.config,
            context=self.context,
            request_id="v2-remote-ack-request-000001",
            request_nonce="R" * 22,
            expires_at=NOW + timedelta(seconds=30),
            source_signer=self.source,
            now=NOW,
        )
        self.request = verify_physical_wal_v2_remote_ack_request(
            source_request=self.request_raw,
            config=self.config,
            now=NOW,
        )
        self.recovery_raw = PhysicalWalV2RemoteAckReceiverRecoveryEvidence(
            source_request_sha256=hashlib.sha256(self.request.canonical_request).hexdigest(),
            context_sha256=self.request.context_sha256,
            receiver_recovery_evidence_sha256="9" * 64,
            receiver_site=self.context.destination_site,
            source_site=self.context.source_site,
            destination_site=self.context.destination_site,
            object_version_set_sha256=self.context.object_version_set_sha256,
            target_lsn=self.context.target_lsn,
            replay_lsn=self.context.target_lsn,
            observed_at=NOW,
            in_recovery=True,
            role="standby",
        )
        self.recovery = verify_physical_wal_v2_remote_ack_receiver_recovery_evidence(
            evidence=self.recovery_raw,
            source_request=self.request,
            config=self.config,
            now=NOW,
        )
        self.receipt_raw = build_physical_wal_v2_remote_ack_receipt(
            config=self.config,
            source_request=self.request,
            receiver_recovery_evidence=self.recovery,
            receipt_id="v2-remote-ack-receipt-000001",
            receipt_nonce="S" * 22,
            destination_signer=self.destination,
            now=NOW,
        )

    def test_context_preserves_full_v2_route_term_and_object_set_pins(self) -> None:
        context = json.loads(self.context.canonical_context.decode("ascii"))
        self.assertEqual(self.coverage.object_version_set_sha256, context["object_version_set_sha256"])
        self.assertEqual(self.coverage.coverage_scope_sha256, context["coverage_scope_sha256"])
        self.assertEqual(self.coverage.wal_continuity_selector_set_sha256, context["wal_continuity_selector_set_sha256"])
        self.assertEqual(self.coverage.transfer_binding.route_commitment_sha256, context["route_commitment_sha256"])
        self.assertEqual(self.coverage.transfer_binding.four_role_binding_sha256, context["four_role_binding_sha256"])
        self.assertEqual(
            self.coverage.transfer_binding.writer_term.witnessed_term_proof_sha256,
            context["writer_term"]["witnessed_term_proof_sha256"],
        )
        self.assertEqual("forbidden", context["direct_webapp_transport"])
        self.assertNotIn("objects_complete", context)
        self.assertIs(
            self.context,
            require_verified_physical_wal_v2_remote_ack_context(self.context, now=NOW),
        )

    def test_forged_context_or_raw_recovery_fails_closed(self) -> None:
        forged_context = replace(self.context, context_sha256="f" * 64)
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckError, "CONTEXT_CAPABILITY_REQUIRED"):
            require_verified_physical_wal_v2_remote_ack_context(forged_context, now=NOW)
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckError, "RECOVERY_CAPABILITY_REQUIRED"):
            require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence(
                self.recovery_raw,
                source_request=self.request,
                config=self.config,
                now=NOW,
            )
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckError, "RECOVERY_MISMATCH"):
            verify_physical_wal_v2_remote_ack_receiver_recovery_evidence(
                evidence=replace(self.recovery_raw, object_version_set_sha256="f" * 64),
                source_request=self.request,
                config=self.config,
                now=NOW,
            )

    def test_pair_revalidates_exact_request_recovery_digest_and_expiry_but_has_no_authority(self) -> None:
        pair = verify_physical_wal_v2_remote_ack_evidence(
            source_request=self.request_raw,
            destination_receipt=self.receipt_raw,
            config=self.config,
            now=NOW,
        )
        self.assertIs(
            pair,
            require_verified_physical_wal_v2_remote_ack_evidence(pair, config=self.config, now=NOW),
        )
        self.assertFalse(hasattr(pair, "durable_ledger_entry_sha256"))
        self.assertFalse(hasattr(pair, "writer_authorization"))
        self.assertFalse(hasattr(pair, "promotion_authorization"))
        self.assertEqual("physical-wal-v2-replay-ack-request", self.request_raw["kind"])
        self.assertEqual("physical-wal-v2-replay-ack-receipt", self.receipt_raw["kind"])
        self.assertNotIn("durable", self.receipt_raw["kind"])
        tampered = dict(self.receipt_raw)
        tampered["receiver_replay_lsn"] = "0/2B00000"
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckError, "SIGNATURE_INVALID"):
            verify_physical_wal_v2_remote_ack_evidence(
                source_request=self.request_raw,
                destination_receipt=tampered,
                config=self.config,
                now=NOW,
            )
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckError, "REQUEST_STALE_OR_EXPIRED"):
            verify_physical_wal_v2_remote_ack_request(
                source_request=self.request_raw,
                config=self.config,
                now=NOW + timedelta(seconds=31),
            )

    def test_ledger_and_strict_seams_reject_wire_receipts_and_forged_public_objects(self) -> None:
        """No signed pair or public data-class constructor may claim authority."""

        pair = verify_physical_wal_v2_remote_ack_evidence(
            source_request=self.request_raw,
            destination_receipt=self.receipt_raw,
            config=self.config,
            now=NOW,
        )
        ledger_config = PhysicalWalV2RemoteAckReceiverLedgerConfig(
            state_root=Path("/var/lib/trading-bot/test-v2-remote-ack-ledger"),
            remote_ack_config=self.config,
            enabled=True,
        )
        # A signed non-durable pair has the wrong type and cannot be used as a
        # ledger capability through the public verifier.
        with self.assertRaisesRegex(
            PhysicalWalV2RemoteAckReceiverLedgerError,
            "RECEIPT_CAPABILITY_REQUIRED",
        ):
            require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt(
                pair,
                config=ledger_config,
                source_request=self.request,
                remote_ack_evidence=pair,
                receiver_recovery_evidence=self.recovery,
                target_recovery_evidence=object(),
                now=NOW,
            )

        strict_config = PhysicalWalV2StrictRemoteAckWriterResponseConfig(
            remote_ack_config=self.config,
            receiver_ledger_config=ledger_config,
            enabled=True,
        )
        with self.assertRaisesRegex(
            PhysicalWalV2StrictRemoteAckWriterResponseError,
            "DURABLE_LEDGER_REQUIRED",
        ):
            commit_physical_wal_v2_strict_remote_ack_writer_response(
                config=strict_config,
                remote_ack_evidence=pair,
                receiver_ledger_receipt=object(),
                writer_term_observation=object(),
                now=NOW,
            )
        forged_strict = VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation(
            schema=strict_writer.PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
            context_sha256=self.request.context_sha256,
            source_request_sha256="1" * 64,
            destination_receipt_sha256="2" * 64,
            durable_ledger_entry_sha256="3" * 64,
            local_commit_record_id="v2-strict-forged-commit-000001",
            local_response_id="v2-strict-forged-response-000001",
            committed_at=NOW,
        )
        with self.assertRaisesRegex(
            PhysicalWalV2StrictRemoteAckWriterResponseError,
            "OBSERVATION_CAPABILITY_REQUIRED",
        ):
            require_verified_physical_wal_v2_strict_remote_ack_writer_response_observation(
                forged_strict,
                config=strict_config,
                now=NOW,
            )

    def test_v2_boundaries_have_no_legacy_or_network_imports(self) -> None:
        for module in (remote_ack, receiver_ledger, strict_writer):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                tree = ast.parse(source)
                imported_modules = {
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                } | {
                    node.module
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module is not None
                }
                joined = "\n".join(imported_modules)
                for forbidden in (
                    "physical_wal_remote_ack",
                    "physical_wal_remote_ack_receiver_ledger",
                    "physical_strict_remote_ack_writer_response",
                    "socket",
                    "subprocess",
                    "requests",
                    "boto",
                ):
                    self.assertNotIn(forbidden, joined)
                self.assertNotIn("connect(", source)
                if module is remote_ack:
                    self.assertNotIn("physical_full_matrix", joined)
                    self.assertNotIn("os", joined)
                    self.assertNotIn("pathlib", joined)
                    self.assertNotIn("open(", source)
                if module is receiver_ledger:
                    self.assertIn("physical_full_matrix_v2_recovery_evidence", joined)
                    self.assertNotIn(".resolve(", source)
                    self.assertNotIn("os.lstat(", source)
                    self.assertIn("src_dir_fd=directory_fd", source)
                    self.assertIn("dst_dir_fd=directory_fd", source)

    def test_ast_fence_keeps_protocol_separate_from_legacy_and_io_surfaces(self) -> None:
        source = inspect.getsource(remote_ack)
        tree = ast.parse(source)
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        joined = "\n".join(imported_modules)
        for forbidden in (
            "physical_wal_remote_ack",
            "physical_strict_remote_ack_writer_response",
            "physical_full_matrix",
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto",
        ):
            self.assertNotIn(forbidden, joined)
        self.assertNotIn("open(", source)
        self.assertNotIn("connect(", source)


class PhysicalWalV2RemoteAckReceiverLedgerTests(unittest.TestCase):
    """Exercise the root-owned V2 ledger with an independent signed bridge."""

    def setUp(self) -> None:
        self.bridge_fixture = PhysicalFullMatrixV2RecoveryEvidenceTests(
            methodName="runTest"
        )
        self.bridge_fixture.setUp()
        fixture = self.bridge_fixture
        target_fixture = fixture.target_fixture
        self.target_recovery = fixture.mint()
        self.context = mint_physical_wal_v2_remote_ack_context(
            coverage=fixture.coverage,
            inputs=PhysicalWalV2RemoteAckCoverageInputs(
                base_backup_evidence=fixture.base,
                blob_frontier_coverage=fixture.blob,
                blob_owner_coverage=fixture.owner_coverage,
                blob_expected_owner_public_key=_public(fixture.owner),
                target_wal_continuity=target_fixture.continuity,
                target_wal_continuity_receipt=target_fixture.continuity_receipt,
                manifest=target_fixture.evidence.manifest,
                handoff_receipt=target_fixture.evidence.handoff,
                blob_scope=fixture.blob_scope,
                continuity_scope=target_fixture.continuity_scope,
                scope=fixture.coverage_scope,
            ),
            now=NOW,
        )
        self.source = Ed25519PrivateKey.generate()
        self.destination = Ed25519PrivateKey.generate()
        self.remote_config = PhysicalWalV2RemoteAckConfig(
            expected_context_sha256=self.context.context_sha256,
            expected_source_site=self.context.source_site,
            expected_destination_site=self.context.destination_site,
            expected_source_public_key=_public(self.source),
            expected_destination_public_key=_public(self.destination),
            enabled=True,
            maximum_evidence_age_seconds=45,
        )
        request_raw = build_physical_wal_v2_remote_ack_request(
            config=self.remote_config,
            context=self.context,
            request_id="v2-remote-ack-ledger-request-000001",
            request_nonce="L" * 22,
            expires_at=NOW + timedelta(seconds=30),
            source_signer=self.source,
            now=NOW,
        )
        self.request = verify_physical_wal_v2_remote_ack_request(
            source_request=request_raw,
            config=self.remote_config,
            now=NOW,
        )
        self.recovery_raw = PhysicalWalV2RemoteAckReceiverRecoveryEvidence(
            source_request_sha256=hashlib.sha256(self.request.canonical_request).hexdigest(),
            context_sha256=self.request.context_sha256,
            receiver_recovery_evidence_sha256=(
                self.target_recovery.readback_evidence_sha256
            ),
            receiver_site=self.context.destination_site,
            source_site=self.context.source_site,
            destination_site=self.context.destination_site,
            object_version_set_sha256=self.context.object_version_set_sha256,
            target_lsn=self.context.target_lsn,
            replay_lsn=self.context.target_lsn,
            observed_at=self.target_recovery.observed_at,
            in_recovery=True,
            role="standby",
        )
        self.recovery = verify_physical_wal_v2_remote_ack_receiver_recovery_evidence(
            evidence=self.recovery_raw,
            source_request=self.request,
            config=self.remote_config,
            now=NOW,
        )
        raw_pair = build_physical_wal_v2_remote_ack_receipt(
            config=self.remote_config,
            source_request=self.request,
            receiver_recovery_evidence=self.recovery,
            receipt_id="v2-remote-ack-ledger-prior-receipt-000001",
            receipt_nonce="P" * 22,
            destination_signer=self.destination,
            now=NOW,
        )
        self.prior_pair = verify_physical_wal_v2_remote_ack_evidence(
            source_request=self.request.canonical_request,
            destination_receipt=raw_pair,
            config=self.remote_config,
            now=NOW,
        )
        self.temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temporary.name)
        os.chmod(self.state_root, 0o700)
        self.ledger_config = PhysicalWalV2RemoteAckReceiverLedgerConfig(
            state_root=self.state_root,
            remote_ack_config=self.remote_config,
            enabled=True,
            maximum_entries=8,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()
        self.bridge_fixture.tearDown()

    def _issue(self, **changes: object):
        values: dict[str, object] = {
            "config": self.ledger_config,
            "source_request": self.request,
            "receiver_recovery_evidence": self.recovery,
            "target_recovery_evidence": self.target_recovery,
            "destination_signer": self.destination,
            "remote_ack_evidence": self.prior_pair,
            # The public value is ignored; patching the private receiver clock
            # below is the only deterministic test-time clock injection.
            "now": NOW - timedelta(days=1),
        }
        values.update(changes)
        return issue_physical_wal_v2_remote_ack_receiver_receipt(**values)

    def test_root_owned_ledger_binds_signed_target_recovery_and_is_idempotent(self) -> None:
        with patch.object(receiver_ledger, "_trusted_now", return_value=NOW):
            issued = self._issue(destination_signer=None)
            self.assertFalse(issued.idempotent)
            self.assertEqual(
                self.target_recovery.evidence_sha256,
                issued.receipt.target_recovery_evidence_sha256,
            )
            self.assertEqual(
                self.target_recovery.readback_attestation_sha256,
                issued.receipt.readback_attestation_sha256,
            )
            self.assertEqual(
                self.target_recovery.stage_receipt_sha256,
                issued.receipt.stage_receipt_sha256,
            )
            self.assertEqual(
                self.target_recovery.witness_transition_id,
                issued.receipt.witness_transition_id,
            )
            self.assertFalse(hasattr(issued.receipt, "writer_authorization"))
            self.assertFalse(hasattr(issued.receipt, "promotion_authorization"))

            retried = self._issue(destination_signer=None)
            self.assertTrue(retried.idempotent)
            self.assertEqual(
                issued.receipt.durable_ledger_entry_sha256,
                retried.receipt.durable_ledger_entry_sha256,
            )
            alternate_raw = build_physical_wal_v2_remote_ack_receipt(
                config=self.remote_config,
                source_request=self.request,
                receiver_recovery_evidence=self.recovery,
                receipt_id="v2-remote-ack-ledger-alternate-receipt-000001",
                receipt_nonce="A" * 22,
                destination_signer=self.destination,
                now=NOW,
            )
            alternate_pair = verify_physical_wal_v2_remote_ack_evidence(
                source_request=self.request.canonical_request,
                destination_receipt=alternate_raw,
                config=self.remote_config,
                now=NOW,
            )
            with self.assertRaisesRegex(
                PhysicalWalV2RemoteAckReceiverLedgerError,
                "REPLAY_CONFLICT",
            ):
                self._issue(
                    destination_signer=None,
                    remote_ack_evidence=alternate_pair,
                )
            self.assertIs(
                issued.receipt,
                require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt(
                    issued.receipt,
                    config=self.ledger_config,
                    source_request=self.request,
                    receiver_recovery_evidence=self.recovery,
                    target_recovery_evidence=self.target_recovery,
                    remote_ack_evidence=issued.remote_ack_evidence,
                    now=NOW - timedelta(days=1),
                ),
            )

        ledger_path = self.state_root / "physical-wal-v2-remote-ack-ledger" / "ledger.json"
        payload = json.loads(ledger_path.read_text(encoding="ascii"))
        self.assertEqual(
            receiver_ledger.PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA,
            payload["schema"],
        )
        self.assertEqual(1, len(payload["entries"]))
        self.assertEqual(
            self.target_recovery.evidence_sha256,
            payload["entries"][0]["target_recovery_evidence_sha256"],
        )

    def test_local_recovery_claim_or_forged_bridge_cannot_issue(self) -> None:
        mismatched = verify_physical_wal_v2_remote_ack_receiver_recovery_evidence(
            evidence=replace(
                self.recovery_raw,
                receiver_recovery_evidence_sha256="1" * 64,
            ),
            source_request=self.request,
            config=self.remote_config,
            now=NOW,
        )
        with patch.object(receiver_ledger, "_trusted_now", return_value=NOW):
            with self.assertRaisesRegex(
                PhysicalWalV2RemoteAckReceiverLedgerError,
                "TARGET_RECOVERY_CROSS_PIN_MISMATCH",
            ):
                self._issue(receiver_recovery_evidence=mismatched)
            with self.assertRaisesRegex(
                PhysicalWalV2RemoteAckReceiverLedgerError,
                "INPUT_INVALID",
            ):
                self._issue(
                    target_recovery_evidence=replace(
                        self.target_recovery,
                        evidence_sha256="f" * 64,
                    )
                )

    def test_persisted_clock_floor_rejects_rollback(self) -> None:
        with patch.object(receiver_ledger, "_trusted_now", return_value=NOW):
            issued = self._issue(remote_ack_evidence=None)
        with patch.object(
            receiver_ledger,
            "_trusted_now",
            return_value=NOW - timedelta(seconds=1),
        ):
            with self.assertRaisesRegex(
                PhysicalWalV2RemoteAckReceiverLedgerError,
                "CLOCK_ROLLBACK_DETECTED",
            ):
                require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt(
                    issued.receipt,
                    config=self.ledger_config,
                    source_request=self.request,
                    receiver_recovery_evidence=self.recovery,
                    target_recovery_evidence=self.target_recovery,
                    now=NOW,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
