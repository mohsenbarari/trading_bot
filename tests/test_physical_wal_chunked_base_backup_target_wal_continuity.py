from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
import unittest

from core.physical_wal_chunked_base_backup_target_wal_continuity import (
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCHEMA,
    PhysicalWalChunkedBaseBackupTargetWalContinuityError,
    PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector,
    PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    build_physical_wal_chunked_base_backup_target_wal_continuity_receipt,
    mint_physical_wal_chunked_base_backup_target_wal_continuity,
    require_verified_physical_wal_chunked_base_backup_target_wal_continuity,
    require_verified_physical_wal_chunked_base_backup_target_wal_continuity_receipt,
    verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt,
)
from core import physical_wal_chunked_base_backup_target_wal_continuity as continuity_module
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import (
    NOW,
    _V2Evidence,
    _nonce,
)


class PhysicalWalChunkedBaseBackupTargetWalContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = _V2Evidence()

    def scope(self, **changes: object) -> PhysicalWalChunkedBaseBackupTargetWalContinuityScope:
        values: dict[str, object] = {
            "transfer_binding": self.evidence.binding,
            "lineage_sha256": self.evidence.handoff.lineage_sha256,
            "baseline_generation_id": self.evidence.handoff.baseline_generation_id,
            "database_system_identifier": self.evidence.handoff.database_system_identifier,
            "timeline_id": self.evidence.handoff.timeline_id,
            "wal_segment_size_bytes": self.evidence.handoff.wal_segment_size_bytes,
            "baseline_wal_lsn": self.evidence.handoff.baseline_wal_lsn,
            "wal_chain_start_lsn": self.evidence.handoff.wal_chain_start_lsn,
            "base_backup_end_lsn": self.evidence.handoff.base_backup_end_lsn,
            "target_lsn": "0/2A00000",
        }
        values.update(changes)
        return PhysicalWalChunkedBaseBackupTargetWalContinuityScope(**values)

    def selectors(self, **changes: object):
        prefix = (
            f"{self.evidence.binding.object_storage_namespace}/"
            f"{self.evidence.binding.campaign_id}/"
            f"{self.evidence.binding.release_sha}/wal-v2/"
            f"{self.evidence.handoff.lineage_sha256}/"
        )
        values = (
            PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector(
                index=0,
                object_key=prefix + "000000010000000000000002-a.age",
                version_id="wal-version-0000000001",
                ciphertext_sha256="d" * 64,
                ciphertext_bytes=1024 * 1024 + 128,
                plaintext_sha256="e" * 64,
                plaintext_bytes=1024 * 1024,
                timeline_id=self.evidence.handoff.timeline_id,
                start_lsn="0/2800000",
                end_lsn="0/2900000",
                age_recipient=self.evidence.binding.destination_age_recipient,
            ),
            PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector(
                index=1,
                object_key=prefix + "000000010000000000000002-b.age",
                version_id="wal-version-0000000002",
                ciphertext_sha256="f" * 64,
                ciphertext_bytes=1024 * 1024 + 128,
                plaintext_sha256="a" * 64,
                plaintext_bytes=1024 * 1024,
                timeline_id=self.evidence.handoff.timeline_id,
                start_lsn="0/2900000",
                end_lsn="0/2A00000",
                age_recipient=self.evidence.binding.destination_age_recipient,
            ),
        )
        return changes.get("selectors", values)

    def raw_receipt(self, **changes: object):
        scope = changes.pop("scope", self.scope())
        return build_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=scope,
            wal_object_selectors=changes.pop("selectors", self.selectors()),
            receipt_id=changes.pop("receipt_id", "target-wal-continuity-receipt-0001"),
            receipt_nonce=changes.pop("receipt_nonce", _nonce(90_001)),
            issued_at=changes.pop("issued_at", NOW),
            expires_at=changes.pop("expires_at", NOW + timedelta(seconds=50)),
            witness_signer=changes.pop("witness_signer", self.evidence.witness),
            **changes,
        )

    def verified_receipt(self, **changes: object):
        scope = changes.pop("scope", self.scope())
        raw = changes.pop("raw", self.raw_receipt(scope=scope, **changes))
        return verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
            continuity_receipt=raw,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=scope,
            now=NOW,
        ), scope, raw

    def mint(self, **changes: object):
        receipt, scope, _raw = self.verified_receipt(**changes)
        return mint_physical_wal_chunked_base_backup_target_wal_continuity(
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            continuity_receipt=receipt,
            scope=scope,
            now=NOW,
        ), receipt, scope

    def test_exact_signed_selector_chain_mints_only_opaque_target_evidence(self) -> None:
        capability, receipt, scope = self.mint()

        self.assertEqual(PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCHEMA, capability.schema)
        self.assertEqual("0/2800000", capability.base_backup_end_lsn)
        self.assertEqual("0/2A00000", capability.target_lsn)
        self.assertEqual(self.selectors(), capability.wal_object_selectors)
        self.assertEqual(64, len(capability.selector_set_sha256))
        self.assertIs(
            capability,
            require_verified_physical_wal_chunked_base_backup_target_wal_continuity(
                capability,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                continuity_receipt=receipt,
                scope=scope,
                now=NOW,
            ),
        )
        self.assertIs(
            receipt,
            require_verified_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
                receipt,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=scope,
                now=NOW,
            ),
        )
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            capability.__reduce_ex__(4)

    def test_zero_length_target_is_explicitly_allowed_only_with_an_empty_selector_set(self) -> None:
        scope = self.scope(target_lsn=self.evidence.handoff.base_backup_end_lsn)
        raw = self.raw_receipt(scope=scope, selectors=())
        receipt = verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
            continuity_receipt=raw,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=scope,
            now=NOW,
        )
        capability = mint_physical_wal_chunked_base_backup_target_wal_continuity(
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            continuity_receipt=receipt,
            scope=scope,
            now=NOW,
        )
        self.assertEqual((), capability.wal_object_selectors)

    def test_gap_overlap_reordered_or_wrong_final_target_fails_closed(self) -> None:
        first, second = self.selectors()
        bad_sets = (
            (first, replace(second, start_lsn="0/2980000")),
            (first, replace(second, start_lsn="0/2880000")),
            (second, first),
            (first, replace(second, end_lsn="0/2B00000")),
        )
        for selectors in bad_sets:
            with self.subTest(selectors=selectors), self.assertRaisesRegex(
                PhysicalWalChunkedBaseBackupTargetWalContinuityError,
                "SELECTOR_SET_INVALID",
            ):
                self.raw_receipt(selectors=selectors)

    def test_aliases_duplicate_selectors_and_target_before_base_end_fail_closed(self) -> None:
        first, second = self.selectors()
        cases = (
            (replace(first, object_key="physical-wal/v2/latest/segment.age"), second),
            (replace(first, version_id="latest"), second),
            (first, replace(second, object_key=first.object_key)),
        )
        for selectors in cases:
            with self.subTest(selectors=selectors), self.assertRaisesRegex(
                PhysicalWalChunkedBaseBackupTargetWalContinuityError,
                "SELECTOR_SET_INVALID",
            ):
                self.raw_receipt(selectors=selectors)
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "TARGET_BEFORE_BASE_END",
        ):
            self.raw_receipt(scope=self.scope(target_lsn="0/2700000"))

    def test_selector_keys_are_pinned_to_namespace_campaign_release_and_lineage(self) -> None:
        first, second = self.selectors()
        binding = self.evidence.binding
        lineage = self.evidence.handoff.lineage_sha256
        suffix = "000000010000000000000002-a.age"
        wrong_prefixes = (
            f"physical-failback/{binding.campaign_id}/{binding.release_sha}/wal-v2/{lineage}/",
            f"{binding.object_storage_namespace}/other-campaign-v2/{binding.release_sha}/wal-v2/{lineage}/",
            f"{binding.object_storage_namespace}/{binding.campaign_id}/{'f' * len(binding.release_sha)}/wal-v2/{lineage}/",
            f"{binding.object_storage_namespace}/{binding.campaign_id}/{binding.release_sha}/wal-v2/{'f' * 64}/",
        )
        for prefix in wrong_prefixes:
            with self.subTest(prefix=prefix), self.assertRaisesRegex(
                PhysicalWalChunkedBaseBackupTargetWalContinuityError,
                "SELECTOR_SET_INVALID",
            ):
                self.raw_receipt(selectors=(replace(first, object_key=prefix + suffix), second))

    def test_route_recipient_term_lineage_manifest_and_target_are_all_exact_pins(self) -> None:
        receipt, scope, raw = self.verified_receipt()
        wrong_scope = self.scope(target_lsn="0/2B00000")
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "RECEIPT_LINEAGE_OR_TARGET_MISMATCH",
        ):
            verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
                continuity_receipt=raw,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=wrong_scope,
                now=NOW,
            )
        wrong_term = replace(
            self.evidence.binding,
            writer_term=replace(self.evidence.binding.writer_term, writer_epoch=74),
        )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "SCOPE_TERM_MISMATCH",
        ):
            verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
                continuity_receipt=raw,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=replace(scope, transfer_binding=wrong_term),
                now=NOW,
            )
        self.assertEqual(receipt.manifest_sha256, hashlib.sha256(self.evidence.manifest.canonical_manifest).hexdigest())

    def test_raw_or_v1ish_receipts_cannot_be_minted_and_expiry_fails_closed(self) -> None:
        receipt, scope, raw = self.verified_receipt()
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "RECEIPT_REQUIRED",
        ):
            mint_physical_wal_chunked_base_backup_target_wal_continuity(
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                continuity_receipt=raw,  # type: ignore[arg-type]
                scope=scope,
                now=NOW,
            )
        legacy = dict(raw)
        legacy["schema"] = "physical-wal-continuity-v1"
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "RECEIPT_SCHEMA_INVALID",
        ):
            verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
                continuity_receipt=legacy,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=scope,
                now=NOW,
            )
        expiring_raw = self.raw_receipt(expires_at=NOW + timedelta(seconds=10))
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "RECEIPT_EXPIRED",
        ):
            verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
                continuity_receipt=expiring_raw,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=self.scope(),
                now=NOW + timedelta(seconds=10),
            )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "RECEIPT_EXPIRED",
        ):
            verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
                continuity_receipt=expiring_raw,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=self.scope(),
                now=NOW + timedelta(seconds=20),
            )
        self.assertIsNotNone(receipt)

    def test_tampered_capability_is_not_a_verified_capability(self) -> None:
        capability, receipt, scope = self.mint()
        forged = replace(capability, target_lsn="0/2B00000")
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupTargetWalContinuityError,
            "CAPABILITY_REQUIRED",
        ):
            require_verified_physical_wal_chunked_base_backup_target_wal_continuity(
                forged,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                continuity_receipt=receipt,
                scope=scope,
                now=NOW,
            )

    def test_ast_fence_keeps_foundation_v2_only_and_side_effect_free(self) -> None:
        source = inspect.getsource(continuity_module)
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
            "physical_wal_incremental_receiver_chain",
            "physical_wal_remote_ack",
            "physical_wal_promotion_gate",
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto",
        ):
            self.assertNotIn(forbidden, joined)
        self.assertNotIn("PhysicalWalObjectStorageBundle", source)
        self.assertNotIn("base_backup_object", source)
        self.assertNotIn("open(", source)
        self.assertNotIn("connect(", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
