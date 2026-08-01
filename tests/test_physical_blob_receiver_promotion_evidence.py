from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import importlib
import json
import os
import unittest

import core.physical_blob_receiver_inventory_mapping as receiver_mapping
from core.physical_blob_object_storage_uploader import (
    authorize_physical_blob_object_storage_binding,
)
from core.physical_blob_receiver_promotion_evidence import (
    PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_DEFAULT_ENABLED,
    PHYSICAL_WAL_PROMOTION_V2_BLOB_REQUIREMENT_SCHEMA,
    PhysicalBlobReceiverPromotionEvidenceConfig,
    PhysicalBlobReceiverPromotionEvidenceError,
    build_physical_wal_promotion_v2_blob_requirement,
    require_physical_wal_promotion_v2_blob_requirement,
    require_verified_physical_blob_receiver_promotion_evidence,
    verify_physical_blob_receiver_promotion_evidence,
)
from tests.test_physical_blob_receiver_inventory_mapping import (
    NOW,
)


@unittest.skipUnless(os.geteuid() == 0, "receiver promotion-evidence contract requires root fixture")
class PhysicalBlobReceiverPromotionEvidenceTests(unittest.TestCase):
    """Reuse the no-network two-Blob fixture that mints a verified mapping."""

    def setUp(self) -> None:
        fixture_module = importlib.import_module(
            "tests.test_physical_blob_receiver_inventory_mapping"
        )
        fixture_type = fixture_module.PhysicalBlobReceiverInventoryMappingTests
        self.fixture = fixture_type(methodName="runTest")
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def config(self, **overrides: object) -> PhysicalBlobReceiverPromotionEvidenceConfig:
        values: dict[str, object] = {
            "mapping_signer_public_key": self.fixture.mapping_public_key,
            "blob_receipt_signer_public_key": self.fixture.blob_receipt_public_key,
            "enabled": True,
        }
        values.update(overrides)
        return PhysicalBlobReceiverPromotionEvidenceConfig(**values)

    def mapping_inputs(self):
        (
            client,
            result,
            blob_receipts,
            inventory_receipt,
            publisher,
            artifact,
            receipt,
            verified_mapping,
        ) = self.fixture.verified_mapping()
        return (
            client,
            result,
            blob_receipts,
            inventory_receipt,
            publisher,
            artifact,
            receipt,
            verified_mapping,
        )

    def evidence(self):
        (
            client,
            _result,
            _blob_receipts,
            _inventory_receipt,
            _publisher,
            _artifact,
            receipt,
            verified_mapping,
        ) = self.mapping_inputs()
        before_calls = len(client.calls)
        evidence = verify_physical_blob_receiver_promotion_evidence(
            config=self.config(),
            verified_mapping=verified_mapping,
            pinned_mapping_receipt=receipt,
            requested_replay_wal_lsn=verified_mapping.baseline_wal_lsn,
            verified_binding=self.fixture.storage_binding,
            now=NOW,
        )
        self.assertEqual(before_calls, len(client.calls))
        return receipt, verified_mapping, evidence

    def test_v2_mapping_capability_binds_all_receipts_and_is_revalidated(self) -> None:
        receipt, verified_mapping, evidence = self.evidence()

        self.assertEqual(verified_mapping.baseline_wal_lsn, evidence.mapping_eligible_replay_wal_lsn)
        self.assertEqual(receipt.receipt_sha256, evidence.mapping_receipt_sha256)
        self.assertEqual(verified_mapping.entry_count, len(evidence.blob_objects))
        self.assertEqual(
            evidence.original_v1_inventory_receipt.receipt_sha256,
            evidence.original_v1_inventory_receipt_sha256,
        )
        self.assertEqual(
            tuple(range(1, evidence.entry_count + 1)),
            tuple(item.ordinal for item in evidence.blob_objects),
        )
        self.assertEqual(
            receipt.route_binding_sha256,
            evidence.route_binding_sha256,
        )
        self.assertIs(
            evidence,
            require_verified_physical_blob_receiver_promotion_evidence(
                evidence,
                config=self.config(),
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            ),
        )

    def test_promotion_v2_requirement_only_accepts_the_v2_mapping_capability(self) -> None:
        (
            _client,
            _result,
            _blob_receipts,
            inventory_receipt,
            _publisher,
            _artifact,
            _receipt,
            _verified_mapping,
        ) = self.mapping_inputs()
        _receipt, _verified_mapping, evidence = self.evidence()
        requirement = build_physical_wal_promotion_v2_blob_requirement(
            receiver_promotion_evidence=evidence,
            config=self.config(),
            verified_binding=self.fixture.storage_binding,
            now=NOW,
        )
        self.assertEqual(PHYSICAL_WAL_PROMOTION_V2_BLOB_REQUIREMENT_SCHEMA, requirement.schema)
        self.assertIs(
            requirement,
            require_physical_wal_promotion_v2_blob_requirement(
                requirement,
                config=self.config(),
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            ),
        )
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "promotion-v2 Blob requirement"):
            build_physical_wal_promotion_v2_blob_requirement(
                receiver_promotion_evidence=inventory_receipt,
                config=self.config(),
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )

    def test_raw_v1_receipt_and_mapping_scope_replay_substitutes_are_rejected(self) -> None:
        (
            _client,
            _result,
            _blob_receipts,
            inventory_receipt,
            _publisher,
            _artifact,
            receipt,
            verified_mapping,
        ) = self.mapping_inputs()
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "v1 Blob receipts are insufficient"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(),
                verified_mapping=verified_mapping,
                pinned_mapping_receipt=inventory_receipt,
                requested_replay_wal_lsn=verified_mapping.baseline_wal_lsn,
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "outside"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(),
                verified_mapping=verified_mapping,
                pinned_mapping_receipt=receipt,
                requested_replay_wal_lsn="0/1800010",
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )

    def test_bool_inbound_mapping_projection_and_recheck_pin_swap_are_rejected(self) -> None:
        receipt, verified_mapping, evidence = self.evidence()
        object.__setattr__(verified_mapping, "shard_ordinal", True)
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "mapping shard ordinal"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(),
                verified_mapping=verified_mapping,
                pinned_mapping_receipt=receipt,
                requested_replay_wal_lsn=verified_mapping.baseline_wal_lsn,
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "signer pins"):
            require_verified_physical_blob_receiver_promotion_evidence(
                evidence,
                config=self.config(
                    mapping_signer_public_key=self.fixture.blob_receipt_public_key
                ),
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )
    def test_stale_or_mismatched_live_binding_and_tampered_mapping_receipt_fail_closed(self) -> None:
        receipt, verified_mapping, evidence = self.evidence()
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "not live|authorized"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(),
                verified_mapping=verified_mapping,
                pinned_mapping_receipt=receipt,
                requested_replay_wal_lsn=verified_mapping.baseline_wal_lsn,
                verified_binding=self.fixture.storage_binding,
                now=NOW + timedelta(seconds=46),
            )
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "not live|authorized"):
            require_verified_physical_blob_receiver_promotion_evidence(
                evidence,
                config=self.config(),
                verified_binding=self.fixture.storage_binding,
                now=NOW + timedelta(seconds=46),
            )
        foreign_timeline_binding = authorize_physical_blob_object_storage_binding(
            artifact_binding=self.fixture.artifact_binding,
            timeline_id=8,
            now=NOW,
        )
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "mapping is invalid"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(),
                verified_mapping=verified_mapping,
                pinned_mapping_receipt=receipt,
                requested_replay_wal_lsn=verified_mapping.baseline_wal_lsn,
                verified_binding=foreign_timeline_binding,
                now=NOW,
            )
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "pinned"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(),
                verified_mapping=verified_mapping,
                pinned_mapping_receipt=replace(receipt, mapping_plaintext_sha256="f" * 64),
                requested_replay_wal_lsn=verified_mapping.baseline_wal_lsn,
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )

    def test_resigned_reordered_mapping_or_descriptor_substitution_never_mints_evidence(self) -> None:
        receipt, verified_mapping, _evidence = self.evidence()
        raw = json.loads(verified_mapping.canonical_plaintext)
        raw["entries"] = list(reversed(raw["entries"]))
        for ordinal, entry in enumerate(raw["entries"], start=1):
            entry["ordinal"] = ordinal
        forged = receiver_mapping._sign_canonical(
            value={key: value for key, value in raw.items() if key != "source_mapping_signature"},
            signature_field="source_mapping_signature",
            signature_domain=receiver_mapping._MAPPING_SIGNATURE_DOMAIN,
            signer_factory=lambda: self.fixture.mapping_private_key,
            expected_public_key=self.fixture.mapping_public_key,
            label="test forged mapping",
        )
        object.__setattr__(verified_mapping, "canonical_plaintext", forged)
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "mapping is invalid"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(),
                verified_mapping=verified_mapping,
                pinned_mapping_receipt=receipt,
                requested_replay_wal_lsn=verified_mapping.baseline_wal_lsn,
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )

        (
            _client,
            _result,
            _blob_receipts,
            _inventory_receipt,
            _publisher,
            _artifact,
            descriptor_receipt,
            descriptor_mapping,
        ) = self.mapping_inputs()
        altered = json.loads(descriptor_mapping.canonical_plaintext)
        altered["entries"][0]["final_object"]["version_id"] = "forged-version"
        forged_descriptor = receiver_mapping._sign_canonical(
            value={key: value for key, value in altered.items() if key != "source_mapping_signature"},
            signature_field="source_mapping_signature",
            signature_domain=receiver_mapping._MAPPING_SIGNATURE_DOMAIN,
            signer_factory=lambda: self.fixture.mapping_private_key,
            expected_public_key=self.fixture.mapping_public_key,
            label="test forged mapping",
        )
        object.__setattr__(descriptor_mapping, "canonical_plaintext", forged_descriptor)
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "mapping is invalid"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(),
                verified_mapping=descriptor_mapping,
                pinned_mapping_receipt=descriptor_receipt,
                requested_replay_wal_lsn=descriptor_mapping.baseline_wal_lsn,
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )

    def test_default_disabled_rejects_before_mapping_or_receipt_use(self) -> None:
        self.assertFalse(PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_DEFAULT_ENABLED)
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "disabled"):
            verify_physical_blob_receiver_promotion_evidence(
                config=self.config(enabled=False),
                verified_mapping=object(),
                pinned_mapping_receipt=object(),
                requested_replay_wal_lsn="0/1800000",
                verified_binding=object(),
                now=NOW,
            )

    def test_bool_projection_ordinal_cannot_survive_capability_recheck(self) -> None:
        _receipt, _verified_mapping, evidence = self.evidence()
        object.__setattr__(evidence, "shard_ordinal", True)
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "shard ordinal"):
            require_verified_physical_blob_receiver_promotion_evidence(
                evidence,
                config=self.config(),
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )

    def test_bool_in_nested_mapping_receipt_cannot_survive_capability_recheck(self) -> None:
        _receipt, _verified_mapping, evidence = self.evidence()
        object.__setattr__(evidence.mapping_receipt, "shard_ordinal", True)
        with self.assertRaisesRegex(PhysicalBlobReceiverPromotionEvidenceError, "mapping receipt"):
            require_verified_physical_blob_receiver_promotion_evidence(
                evidence,
                config=self.config(),
                verified_binding=self.fixture.storage_binding,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
