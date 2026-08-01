from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_chunked_base_backup_blob_frontier_coverage as coverage_module
from core.physical_wal_chunked_base_backup_blob_frontier_coverage import (
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_BLOB_FRONTIER_COVERAGE_SCHEMA,
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    PhysicalWalV2BlobObjectVersionSelector,
    VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
    VerifiedPhysicalWalV2BlobObjectVersionCoverage,
    build_physical_wal_v2_blob_object_version_coverage,
    canonical_physical_wal_v2_blob_object_version_coverage_bytes,
    derive_physical_wal_v2_blob_object_version_prefix,
    mint_physical_wal_chunked_base_backup_blob_frontier_coverage,
    require_verified_physical_wal_chunked_base_backup_blob_frontier_coverage,
    require_verified_physical_wal_v2_blob_object_version_coverage,
    verify_physical_wal_v2_blob_object_version_coverage,
)
from core.physical_wal_chunked_base_backup_transfer import (
    build_physical_wal_chunked_base_backup_binding,
)
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import (
    NOW,
    RECIPIENT,
    _V2Evidence,
)


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalWalChunkedBaseBackupBlobFrontierCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = _V2Evidence()
        self.owner = Ed25519PrivateKey.generate()
        self.target_wal_lsn = "0/3000000"
        self.prefix = derive_physical_wal_v2_blob_object_version_prefix(
            transfer_binding=self.evidence.binding,
            lineage_sha256=self.evidence.handoff.lineage_sha256,
        )
        self.objects = (
            PhysicalWalV2BlobObjectVersionSelector(
                ordinal=0,
                blob_id="blob-record-000000000001",
                object_key=self.prefix + "000000000001.age",
                version_id="blob-version-000000000001",
                ciphertext_sha256="1" * 64,
                ciphertext_bytes=1101,
                plaintext_sha256="2" * 64,
                plaintext_bytes=1001,
                age_recipient=RECIPIENT,
            ),
            PhysicalWalV2BlobObjectVersionSelector(
                ordinal=1,
                blob_id="blob-record-000000000002",
                object_key=self.prefix + "000000000002.age",
                version_id="blob-version-000000000002",
                ciphertext_sha256="3" * 64,
                ciphertext_bytes=1201,
                plaintext_sha256="4" * 64,
                plaintext_bytes=1101,
                age_recipient=RECIPIENT,
            ),
        )

    def _scope(self, **changes: object) -> PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope:
        values: dict[str, object] = {
            "transfer_binding": self.evidence.binding,
            "lineage_sha256": self.evidence.handoff.lineage_sha256,
            "target_wal_lsn": self.target_wal_lsn,
            "required_blob_object_versions": self.objects,
        }
        values.update(changes)
        return PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope(**values)

    def _raw(self, **changes: object) -> dict:
        manifest_sha = hashlib.sha256(self.evidence.manifest.canonical_manifest).hexdigest()
        values: dict[str, object] = {
            "transfer_binding": self.evidence.binding,
            "canonical_base_backup_manifest_sha256": manifest_sha,
            "lineage_sha256": self.evidence.handoff.lineage_sha256,
            "baseline_generation_id": self.evidence.handoff.baseline_generation_id,
            "database_system_identifier": self.evidence.handoff.database_system_identifier,
            "timeline_id": self.evidence.handoff.timeline_id,
            "wal_segment_size_bytes": self.evidence.handoff.wal_segment_size_bytes,
            "baseline_wal_lsn": self.evidence.handoff.baseline_wal_lsn,
            "wal_chain_start_lsn": self.evidence.handoff.wal_chain_start_lsn,
            "base_backup_end_lsn": self.evidence.handoff.base_backup_end_lsn,
            "target_wal_lsn": self.target_wal_lsn,
            "coverage_id": "blob-frontier-coverage-000001",
            "coverage_nonce": "B" * 22,
            "observed_at": NOW,
            "expires_at": NOW + timedelta(seconds=90),
            "objects": self.objects,
            "owner_signer": self.owner,
        }
        values.update(changes)
        return build_physical_wal_v2_blob_object_version_coverage(**values)

    def _verified(self, **changes: object) -> VerifiedPhysicalWalV2BlobObjectVersionCoverage:
        return verify_physical_wal_v2_blob_object_version_coverage(
            coverage=self._raw(**changes),
            expected_owner_public_key=_public(self.owner),
            now=NOW + timedelta(seconds=1),
        )

    def _mint(
        self,
        *,
        owner_coverage: object | None = None,
        scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope | None = None,
        now=NOW + timedelta(seconds=1),
    ) -> VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage:
        return mint_physical_wal_chunked_base_backup_blob_frontier_coverage(
            owner_coverage=self._verified() if owner_coverage is None else owner_coverage,
            expected_owner_public_key=_public(self.owner),
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=self._scope() if scope is None else scope,
            now=now,
        )

    def _wrong_route_binding(self):
        binding = self.evidence.binding
        return build_physical_wal_chunked_base_backup_binding(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            object_storage_namespace=binding.object_storage_namespace,
            route_commitment_sha256=binding.route_commitment_sha256,
            four_role_binding_sha256=binding.four_role_binding_sha256,
            destination_age_recipient=binding.destination_age_recipient,
            writer_holder_site="webapp_ir",
            writer_epoch=binding.writer_term.writer_epoch,
            writer_lease_id=binding.writer_term.writer_lease_id,
            witnessed_term_proof_sha256=binding.writer_term.witnessed_term_proof_sha256,
        )

    def test_mints_non_authorizing_exact_v2_blob_frontier_capability(self) -> None:
        owner_coverage = self._verified()
        capability = self._mint(owner_coverage=owner_coverage)
        self.assertIsInstance(
            capability,
            VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
        )
        self.assertEqual(
            PHYSICAL_WAL_CHUNKED_BASE_BACKUP_BLOB_FRONTIER_COVERAGE_SCHEMA,
            capability.schema,
        )
        self.assertEqual(
            hashlib.sha256(self.evidence.manifest.canonical_manifest).hexdigest(),
            capability.canonical_base_backup_manifest_sha256,
        )
        self.assertEqual(self.target_wal_lsn, capability.target_wal_lsn)
        self.assertEqual(self.objects, capability.objects)
        self.assertEqual(self.evidence.handoff.lineage_sha256, capability.lineage_sha256)
        self.assertEqual(self.evidence.handoff.base_backup_end_lsn, capability.base_backup_end_lsn)
        self.assertFalse(hasattr(capability, "remote_ack_binding"))
        self.assertFalse(hasattr(capability, "promotion_authorization"))
        self.assertIs(
            capability,
            require_verified_physical_wal_chunked_base_backup_blob_frontier_coverage(
                capability,
                owner_coverage=owner_coverage,
                expected_owner_public_key=_public(self.owner),
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=self._scope(),
                now=NOW + timedelta(seconds=1),
            ),
        )
        self.assertEqual(
            canonical_physical_wal_v2_blob_object_version_coverage_bytes(self._raw()),
            owner_coverage.canonical_coverage,
        )

    def test_mint_rejects_raw_or_legacy_shaped_inputs_not_an_owner_capability(self) -> None:
        raw = self._raw()
        for label, candidate in (
            ("raw signed coverage", raw),
            ("canonical bytes", canonical_physical_wal_v2_blob_object_version_coverage_bytes(raw)),
            (
                "legacy inventory shape",
                {
                    "schema": "gold-trade-physical-blob-inventory-shard-v1",
                    "objects": [],
                },
            ),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
                "OWNER_COVERAGE_CAPABILITY_REQUIRED",
            ):
                self._mint(owner_coverage=candidate)

    def test_incomplete_duplicate_or_wrong_object_versions_fail_closed(self) -> None:
        incomplete = self._verified(objects=self.objects[:1])
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "OWNER_OBJECT_VERSION_MISMATCH",
        ):
            self._mint(owner_coverage=incomplete)

        duplicate = (self.objects[0], replace(self.objects[0], ordinal=1))
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "OWNER_COVERAGE_OBJECTS_INVALID",
        ):
            self._raw(objects=duplicate)

        wrong_versions = (
            replace(self.objects[0], version_id="blob-version-000000000099"),
            self.objects[1],
        )
        wrong_version_coverage = self._verified(objects=wrong_versions)
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "OWNER_OBJECT_VERSION_MISMATCH",
        ):
            self._mint(owner_coverage=wrong_version_coverage)

        outside_prefix = (
            replace(self.objects[0], object_key="physical-wal/blob/foreign.age"),
            self.objects[1],
        )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "OBJECT_PREFIX_INVALID",
        ):
            self._raw(objects=outside_prefix)

    def test_frontier_route_term_recipient_and_canonical_manifest_pins_are_exact(self) -> None:
        wrong_term = replace(
            self.evidence.binding,
            writer_term=replace(self.evidence.binding.writer_term, writer_epoch=74),
        )
        wrong_recipient = replace(
            self.evidence.binding,
            destination_age_recipient=("age1" + "p" * 52),
        )
        wrong_recipient_objects = tuple(
            replace(item, age_recipient=wrong_recipient.destination_age_recipient)
            for item in self.objects
        )
        cases = (
            (
                "frontier",
                {"target_wal_lsn": "0/3100000"},
                "OWNER_FRONTIER_MISMATCH",
            ),
            (
                "route",
                {"transfer_binding": self._wrong_route_binding()},
                "OWNER_ROUTE_MISMATCH",
            ),
            (
                "term",
                {"transfer_binding": wrong_term},
                "OWNER_TERM_MISMATCH",
            ),
            (
                "recipient",
                {
                    "transfer_binding": wrong_recipient,
                    "objects": wrong_recipient_objects,
                },
                "OWNER_RECIPIENT_MISMATCH",
            ),
            (
                "manifest hash",
                {"canonical_base_backup_manifest_sha256": "f" * 64},
                "OWNER_MANIFEST_HASH_MISMATCH",
            ),
        )
        for label, changes, code in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
                code,
            ):
                self._mint(owner_coverage=self._verified(**changes))

    def test_forged_or_stale_owner_capability_is_rejected_before_join(self) -> None:
        owner_coverage = self._verified()
        forged = replace(owner_coverage, target_wal_lsn="0/3100000")
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "OWNER_COVERAGE_CAPABILITY_REQUIRED",
        ):
            self._mint(owner_coverage=forged)

        stale_raw = self._raw(
            observed_at=NOW - timedelta(seconds=90),
            expires_at=NOW - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "OWNER_COVERAGE_STALE",
        ):
            verify_physical_wal_v2_blob_object_version_coverage(
                coverage=stale_raw,
                expected_owner_public_key=_public(self.owner),
                now=NOW,
            )

        self.assertIs(
            owner_coverage,
            require_verified_physical_wal_v2_blob_object_version_coverage(
                owner_coverage,
                expected_owner_public_key=_public(self.owner),
                now=NOW + timedelta(seconds=1),
            ),
        )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "OWNER_COVERAGE_STALE",
        ):
            require_verified_physical_wal_v2_blob_object_version_coverage(
                owner_coverage,
                expected_owner_public_key=_public(self.owner),
                now=NOW + timedelta(seconds=91),
            )

    def test_scope_is_exact_and_no_completion_flag_can_seed_coverage(self) -> None:
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
            "SCOPE_TARGET_PRECEDES_BASE_BACKUP",
        ):
            self._mint(scope=self._scope(target_wal_lsn="0/2700000"))
        with self.assertRaisesRegex(TypeError, "objects_complete"):
            PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope(
                transfer_binding=self.evidence.binding,
                lineage_sha256=self.evidence.handoff.lineage_sha256,
                target_wal_lsn=self.target_wal_lsn,
                required_blob_object_versions=self.objects,
                objects_complete=True,
            )

    def test_module_has_no_v1_provider_driver_or_local_runtime_import_surface(self) -> None:
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
        joined_imports = "\n".join(imported_modules)
        self.assertNotIn("physical_blob_", joined_imports)
        self.assertNotIn("physical_wal_object_manifest", joined_imports)
        self.assertNotIn("physical_wal_base_backup_spool", joined_imports)
        self.assertNotIn("physical_wal_remote_ack", joined_imports)
        self.assertNotIn("physical_full_matrix", joined_imports)
        self.assertNotIn("physical_arvan", joined_imports)
        self.assertNotIn("os", imported_modules)
        self.assertNotIn("pathlib", imported_modules)
        self.assertNotIn("socket", imported_modules)
        self.assertNotIn("subprocess", imported_modules)
        self.assertNotIn("requests", imported_modules)
        self.assertNotIn(
            "objects_complete",
            inspect.signature(
                mint_physical_wal_chunked_base_backup_blob_frontier_coverage
            ).parameters,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
