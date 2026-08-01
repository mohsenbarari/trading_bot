from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2_remote_ack_coverage as coverage_module
from core.physical_wal_chunked_base_backup_blob_frontier_coverage import (
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    PhysicalWalV2BlobObjectVersionSelector,
    build_physical_wal_v2_blob_object_version_coverage,
    derive_physical_wal_v2_blob_object_version_prefix,
    mint_physical_wal_chunked_base_backup_blob_frontier_coverage,
    verify_physical_wal_v2_blob_object_version_coverage,
)
from core.physical_wal_chunked_base_backup_remote_ack_bridge import (
    mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence,
)
from core.physical_wal_chunked_base_backup_target_wal_continuity import (
    PhysicalWalChunkedBaseBackupTargetWalContinuityError,
    PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector,
    PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    build_physical_wal_chunked_base_backup_target_wal_continuity_receipt,
    mint_physical_wal_chunked_base_backup_target_wal_continuity,
    verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt,
)
from core.physical_wal_v2_remote_ack_coverage import (
    PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA,
    PhysicalWalV2RemoteAckCoverageError,
    PhysicalWalV2RemoteAckCoverageScope,
    VerifiedPhysicalWalV2RemoteAckCoverage,
    mint_physical_wal_v2_remote_ack_coverage,
    require_verified_physical_wal_v2_remote_ack_coverage,
)
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import (
    NOW,
    RECIPIENT,
    _V2Evidence,
    _nonce,
)


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalWalV2RemoteAckCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = _V2Evidence()
        self.target_lsn = "0/2A00000"
        self.base_scope = self.evidence.scope()
        self.base = mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=self.base_scope,
            now=NOW,
        )
        self.owner = Ed25519PrivateKey.generate()
        self.blob_prefix = derive_physical_wal_v2_blob_object_version_prefix(
            transfer_binding=self.evidence.binding,
            lineage_sha256=self.evidence.handoff.lineage_sha256,
        )
        self.blob_objects = (
            PhysicalWalV2BlobObjectVersionSelector(
                ordinal=0,
                blob_id="remote-ack-blob-record-000001",
                object_key=self.blob_prefix + "000000000001.age",
                version_id="remote-ack-blob-version-000001",
                ciphertext_sha256="1" * 64,
                ciphertext_bytes=111,
                plaintext_sha256="2" * 64,
                plaintext_bytes=101,
                age_recipient=RECIPIENT,
            ),
            PhysicalWalV2BlobObjectVersionSelector(
                ordinal=1,
                blob_id="remote-ack-blob-record-000002",
                object_key=self.blob_prefix + "000000000002.age",
                version_id="remote-ack-blob-version-000002",
                ciphertext_sha256="3" * 64,
                ciphertext_bytes=222,
                plaintext_sha256="4" * 64,
                plaintext_bytes=202,
                age_recipient=RECIPIENT,
            ),
        )
        self.blob_scope = PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope(
            transfer_binding=self.evidence.binding,
            lineage_sha256=self.evidence.handoff.lineage_sha256,
            target_wal_lsn=self.target_lsn,
            required_blob_object_versions=self.blob_objects,
        )
        self.owner_coverage, self.blob = self._blob_coverage(
            objects=self.blob_objects,
            scope=self.blob_scope,
        )
        self.continuity_scope = PhysicalWalChunkedBaseBackupTargetWalContinuityScope(
            transfer_binding=self.evidence.binding,
            lineage_sha256=self.evidence.handoff.lineage_sha256,
            baseline_generation_id=self.evidence.handoff.baseline_generation_id,
            database_system_identifier=self.evidence.handoff.database_system_identifier,
            timeline_id=self.evidence.handoff.timeline_id,
            wal_segment_size_bytes=self.evidence.handoff.wal_segment_size_bytes,
            baseline_wal_lsn=self.evidence.handoff.baseline_wal_lsn,
            wal_chain_start_lsn=self.evidence.handoff.wal_chain_start_lsn,
            base_backup_end_lsn=self.evidence.handoff.base_backup_end_lsn,
            target_lsn=self.target_lsn,
        )
        self.wal_selectors = (
            PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector(
                index=0,
                object_key=(
                    f"{self.evidence.binding.object_storage_namespace}/"
                    f"{self.evidence.binding.campaign_id}/"
                    f"{self.evidence.binding.release_sha}/wal-v2/"
                    f"{self.evidence.handoff.lineage_sha256}/000000010000000000000002-a.age"
                ),
                version_id="remote-ack-wal-version-000001",
                ciphertext_sha256="5" * 64,
                ciphertext_bytes=333,
                plaintext_sha256="6" * 64,
                plaintext_bytes=303,
                timeline_id=self.evidence.handoff.timeline_id,
                start_lsn="0/2800000",
                end_lsn="0/2900000",
                age_recipient=RECIPIENT,
            ),
            PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector(
                index=1,
                object_key=(
                    f"{self.evidence.binding.object_storage_namespace}/"
                    f"{self.evidence.binding.campaign_id}/"
                    f"{self.evidence.binding.release_sha}/wal-v2/"
                    f"{self.evidence.handoff.lineage_sha256}/000000010000000000000002-b.age"
                ),
                version_id="remote-ack-wal-version-000002",
                ciphertext_sha256="7" * 64,
                ciphertext_bytes=444,
                plaintext_sha256="8" * 64,
                plaintext_bytes=404,
                timeline_id=self.evidence.handoff.timeline_id,
                start_lsn="0/2900000",
                end_lsn=self.target_lsn,
                age_recipient=RECIPIENT,
            ),
        )
        raw_continuity = build_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=self.continuity_scope,
            wal_object_selectors=self.wal_selectors,
            receipt_id="remote-ack-coverage-wal-receipt-01",
            receipt_nonce=_nonce(80_001),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=50),
            witness_signer=self.evidence.witness,
        )
        self.continuity_receipt = verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
            continuity_receipt=raw_continuity,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=self.continuity_scope,
            now=NOW,
        )
        self.continuity = mint_physical_wal_chunked_base_backup_target_wal_continuity(
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            continuity_receipt=self.continuity_receipt,
            scope=self.continuity_scope,
            now=NOW,
        )
        self.scope = PhysicalWalV2RemoteAckCoverageScope(
            base_backup_scope=self.base_scope,
            target_lsn=self.target_lsn,
        )

    def _blob_coverage(self, *, objects, scope):
        raw = build_physical_wal_v2_blob_object_version_coverage(
            transfer_binding=self.evidence.binding,
            canonical_base_backup_manifest_sha256=hashlib.sha256(
                self.evidence.manifest.canonical_manifest
            ).hexdigest(),
            lineage_sha256=self.evidence.handoff.lineage_sha256,
            baseline_generation_id=self.evidence.handoff.baseline_generation_id,
            database_system_identifier=self.evidence.handoff.database_system_identifier,
            timeline_id=self.evidence.handoff.timeline_id,
            wal_segment_size_bytes=self.evidence.handoff.wal_segment_size_bytes,
            baseline_wal_lsn=self.evidence.handoff.baseline_wal_lsn,
            wal_chain_start_lsn=self.evidence.handoff.wal_chain_start_lsn,
            base_backup_end_lsn=self.evidence.handoff.base_backup_end_lsn,
            target_wal_lsn=self.target_lsn,
            coverage_id="remote-ack-blob-coverage-0001",
            coverage_nonce="Z" * 22,
            observed_at=NOW,
            expires_at=NOW + timedelta(seconds=50),
            objects=objects,
            owner_signer=self.owner,
        )
        owner = verify_physical_wal_v2_blob_object_version_coverage(
            coverage=raw,
            expected_owner_public_key=_public(self.owner),
            now=NOW,
        )
        return owner, mint_physical_wal_chunked_base_backup_blob_frontier_coverage(
            owner_coverage=owner,
            expected_owner_public_key=_public(self.owner),
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=scope,
            now=NOW,
        )

    def _mint(self, **changes):
        values = {
            "base_backup_evidence": self.base,
            "blob_frontier_coverage": self.blob,
            "blob_owner_coverage": self.owner_coverage,
            "blob_expected_owner_public_key": _public(self.owner),
            "target_wal_continuity": self.continuity,
            "target_wal_continuity_receipt": self.continuity_receipt,
            "manifest": self.evidence.manifest,
            "handoff_receipt": self.evidence.handoff,
            "blob_scope": self.blob_scope,
            "continuity_scope": self.continuity_scope,
            "scope": self.scope,
            "now": NOW,
        }
        values.update(changes)
        return mint_physical_wal_v2_remote_ack_coverage(**values)

    def test_revalidates_all_v2_evidence_and_mints_non_authorizing_exact_set(self) -> None:
        capability = self._mint()
        self.assertIsInstance(capability, VerifiedPhysicalWalV2RemoteAckCoverage)
        self.assertEqual(PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA, capability.schema)
        self.assertEqual(self.target_lsn, capability.target_lsn)
        self.assertEqual(
            ("base_backup", "wal", "wal", "blob", "blob"),
            tuple(item.source for item in capability.objects),
        )
        self.assertEqual(5, len(capability.objects))
        self.assertFalse(hasattr(capability, "remote_ack_binding"))
        self.assertFalse(hasattr(capability, "acknowledgement"))
        self.assertFalse(hasattr(capability, "promotion_authorization"))
        self.assertIs(
            capability,
            require_verified_physical_wal_v2_remote_ack_coverage(
                capability,
                base_backup_evidence=self.base,
                blob_frontier_coverage=self.blob,
                blob_owner_coverage=self.owner_coverage,
                blob_expected_owner_public_key=_public(self.owner),
                target_wal_continuity=self.continuity,
                target_wal_continuity_receipt=self.continuity_receipt,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                blob_scope=self.blob_scope,
                continuity_scope=self.continuity_scope,
                scope=self.scope,
                now=NOW,
            ),
        )

    def test_raw_legacy_or_generic_ack_shaped_inputs_cannot_seed_join(self) -> None:
        cases = (
            ("base raw", {"base_backup_evidence": self.evidence.manifest}, "BASE_EVIDENCE_INVALID"),
            ("blob raw", {"blob_frontier_coverage": {"schema": "blob-v1"}}, "BLOB_FRONTIER_INVALID"),
            ("wal raw", {"target_wal_continuity": {"schema": "wal-v1"}}, "TARGET_WAL_INVALID"),
            ("generic shaped", {"base_backup_evidence": {"schema": "remote-ack-v1"}}, "BASE_EVIDENCE_INVALID"),
        )
        for label, changes, code in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                PhysicalWalV2RemoteAckCoverageError, code
            ):
                self._mint(**changes)

    def test_target_and_cross_component_context_are_exact(self) -> None:
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckCoverageError, "TARGET_MISMATCH"):
            self._mint(
                scope=PhysicalWalV2RemoteAckCoverageScope(
                    base_backup_scope=self.base_scope,
                    target_lsn="0/2B00000",
                )
            )
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckCoverageError, "TARGET_WAL_INVALID"):
            self._mint(target_wal_continuity=replace(self.continuity, target_lsn="0/2B00000"))
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckCoverageError, "BLOB_FRONTIER_INVALID"):
            self._mint(blob_frontier_coverage=replace(self.blob, lineage_sha256="f" * 64))

    def test_blob_key_outside_its_route_bound_prefix_is_rejected(self) -> None:
        overlapping = (
            replace(
                self.blob_objects[0],
                object_key=self.evidence.manifest.chunks[0].object_key,
                version_id="overlap-version-000001",
            ),
            self.blob_objects[1],
        )
        overlap_scope = replace(self.blob_scope, required_blob_object_versions=overlapping)
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "OBJECT_PREFIX_INVALID",
        ):
            self._blob_coverage(objects=overlapping, scope=overlap_scope)

    def test_wal_key_outside_its_route_bound_prefix_is_rejected_by_foundation(self) -> None:
        invalid_wal = (
            replace(
                self.wal_selectors[0],
                object_key="physical-wal/v2/wal/foreign-selector.age",
            ),
            self.wal_selectors[1],
        )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "SELECTOR_SET_INVALID",
        ):
            build_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=self.continuity_scope,
                wal_object_selectors=invalid_wal,
                receipt_id="remote-ack-coverage-wal-receipt-02",
                receipt_nonce=_nonce(80_002),
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=50),
                witness_signer=self.evidence.witness,
            )

    def test_forged_join_capability_fails_closed(self) -> None:
        capability = self._mint()
        forged = replace(capability, target_lsn="0/2B00000")
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckCoverageError, "CAPABILITY_REQUIRED"):
            require_verified_physical_wal_v2_remote_ack_coverage(
                forged,
                base_backup_evidence=self.base,
                blob_frontier_coverage=self.blob,
                blob_owner_coverage=self.owner_coverage,
                blob_expected_owner_public_key=_public(self.owner),
                target_wal_continuity=self.continuity,
                target_wal_continuity_receipt=self.continuity_receipt,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                blob_scope=self.blob_scope,
                continuity_scope=self.continuity_scope,
                scope=self.scope,
                now=NOW,
            )

    def test_ast_fence_keeps_join_v2_only_and_side_effect_free(self) -> None:
        source = inspect.getsource(coverage_module)
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
            "physical_wal_base_backup_spool",
            "physical_wal_object_manifest",
            "physical_wal_remote_ack",
            "physical_wal_promotion_gate",
            "physical_full_matrix",
            "physical_arvan",
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto",
        ):
            self.assertNotIn(forbidden, joined)
        self.assertNotIn("objects_complete", source)
        self.assertNotIn("open(", source)
        self.assertNotIn("connect(", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
