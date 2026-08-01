from __future__ import annotations

import base64
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_blob_object_storage_uploader import (
    PhysicalBlobInventoryShardObjectStorageReceipt,
)
from core.physical_blob_pre_cas_acceptance import (
    PhysicalBlobPreCasAcceptanceConfig,
    PhysicalBlobPreCasAcceptanceError,
    VerifiedPhysicalBlobPreCasAcceptance,
    persist_physical_blob_pre_cas_acceptance,
    require_verified_physical_blob_pre_cas_acceptance,
    verify_physical_blob_pre_cas_acceptance,
)
from core.physical_blob_receiver_promotion_evidence import (
    PhysicalBlobReceiverPromotionEvidenceConfig,
    VerifiedPhysicalBlobReceiverPromotionEvidence,
    VerifiedPhysicalWalPromotionV2BlobRequirement,
)
from tests import test_physical_wal_promotion_gate as physical_wal_gate_tests


NOW = physical_wal_gate_tests.NOW


def public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class FakeDurableAppendOnlyAuthority:
    """Memory double for the explicit persistence/readback trust boundary."""

    def __init__(self, *, signer: Ed25519PrivateKey) -> None:
        self._signer = signer
        self.records: dict[str, bytes] = {}
        self.calls: list[tuple[bytes, str]] = []
        self.return_wrong_readback = False

    def append_and_read_back(
        self,
        *,
        canonical_acceptance: bytes,
        acceptance_sha256: str,
    ) -> bytes:
        self.calls.append((canonical_acceptance, acceptance_sha256))
        payload = json.loads(canonical_acceptance)
        operation_id = payload["pre_cas_operation_id"]
        if operation_id in self.records:
            raise RuntimeError("duplicate append")
        if hashlib.sha256(canonical_acceptance).hexdigest() != acceptance_sha256:
            raise RuntimeError("wrong digest")
        self.records[operation_id] = canonical_acceptance
        readback_sha = acceptance_sha256 if not self.return_wrong_readback else "f" * 64
        unsigned = {
            "schema": "gold-trade-physical-blob-pre-cas-acceptance-receipt-v1",
            "kind": "durable_pre_cas_v2_blob_acceptance_readback",
            "pre_cas_operation_id": operation_id,
            "acceptance_sha256": acceptance_sha256,
            "readback_acceptance_sha256": readback_sha,
            "append_sequence": len(self.records),
            "accepted_at": payload["accepted_at"],
            "issued_at": payload["accepted_at"],
        }
        return canonical_json_bytes(
            {
                **unsigned,
                "signature": base64.b64encode(
                    self._signer.sign(canonical_json_bytes(unsigned))
                ).decode("ascii"),
            }
        )


class PhysicalBlobPreCasAcceptanceTests(TestCase):
    def setUp(self) -> None:
        self.wal_fixture = physical_wal_gate_tests.PhysicalWalPromotionGateTests(
            methodName="runTest"
        )
        self.wal_fixture.setUp()
        self.wal_evidence = self.wal_fixture.evidence()
        self.source_payload = json.loads(self.wal_evidence.source_durability_receipt)
        self.authority_private_key = Ed25519PrivateKey.generate()
        self.authority = FakeDurableAppendOnlyAuthority(signer=self.authority_private_key)
        self.config = PhysicalBlobPreCasAcceptanceConfig(
            authority_public_key=public_key(self.authority_private_key),
            enabled=True,
        )
        self.blob_evidence_config = PhysicalBlobReceiverPromotionEvidenceConfig(
            mapping_signer_public_key=b"m" * 32,
            blob_receipt_signer_public_key=b"b" * 32,
            enabled=True,
        )
        self.blob_binding = object()

    def v2_requirement(self, **overrides: object) -> VerifiedPhysicalWalPromotionV2BlobRequirement:
        source = self.source_payload
        receiver_evidence = VerifiedPhysicalBlobReceiverPromotionEvidence(
            schema="gold-trade-physical-blob-receiver-promotion-evidence-v1",
            canonical_mapping_plaintext=b"{}",
            mapping_receipt=object(),
            original_v1_inventory_receipt=object(),
            blob_object_receipts=(),
            blob_objects=(),
            source_site=source["source_site"],
            destination_site=source["destination_site"],
            campaign_id=source["campaign_id"],
            release_sha=source["release_sha"],
            baseline_generation_id=source["baseline_generation_id"],
            baseline_manifest_sha256=source["baseline_manifest_sha256"],
            baseline_wal_lsn=source["baseline_wal_lsn"],
            route_binding_sha256="d" * 64,
            writer_epoch=source["prior_writer_epoch"],
            writer_lease_id=source["prior_writer_lease_id"],
            witnessed_term_proof_sha256=source["prior_term_proof_sha256"],
            destination_age_recipient=self.wal_fixture.policy.webapp_ir_age_recipient,
            timeline_id=1,
            mapping_plaintext_sha256="e" * 64,
            mapping_plaintext_bytes=2,
            mapping_receipt_sha256="f" * 64,
            original_v1_inventory_sha256="1" * 64,
            original_v1_inventory_bytes=2,
            original_v1_inventory_receipt_sha256="2" * 64,
            shard_ordinal=1,
            entry_count=1,
            blob_receipts_sha256="3" * 64,
            mapping_eligible_replay_wal_lsn=source["baseline_wal_lsn"],
            mapping_signer_public_key=b"m" * 32,
            blob_receipt_signer_public_key=b"b" * 32,
        )
        values: dict[str, object] = {
            "schema": "gold-trade-physical-wal-promotion-v2-blob-requirement-v1",
            "receiver_promotion_evidence": receiver_evidence,
            "source_site": source["source_site"],
            "destination_site": source["destination_site"],
            "campaign_id": source["campaign_id"],
            "release_sha": source["release_sha"],
            "baseline_generation_id": source["baseline_generation_id"],
            "baseline_manifest_sha256": source["baseline_manifest_sha256"],
            "baseline_wal_lsn": source["baseline_wal_lsn"],
            "route_binding_sha256": "d" * 64,
            "writer_epoch": source["prior_writer_epoch"],
            "writer_lease_id": source["prior_writer_lease_id"],
            "witnessed_term_proof_sha256": source["prior_term_proof_sha256"],
            "timeline_id": 1,
            "mapping_plaintext_sha256": "e" * 64,
            "mapping_receipt_sha256": "f" * 64,
            "mapping_object_key": "physical/blob/mapping-0001.age",
            "mapping_object_version_id": "mapping-version-0001",
            "mapping_ciphertext_sha256": "4" * 64,
            "mapping_ciphertext_bytes": 64,
            "original_v1_inventory_receipt_sha256": "2" * 64,
            "blob_receipts_sha256": "3" * 64,
            "entry_count": 1,
            "mapping_eligible_replay_wal_lsn": source["baseline_wal_lsn"],
        }
        values.update(overrides)
        return VerifiedPhysicalWalPromotionV2BlobRequirement(**values)

    def patch_v2_requirement(self, requirement: object):
        return patch(
            "core.physical_blob_pre_cas_acceptance"
            ".require_physical_wal_promotion_v2_blob_requirement",
            return_value=requirement,
        )

    def persist(self, requirement: object, **overrides: object):
        values: dict[str, object] = {
            "config": self.config,
            "prior_activation": self.wal_fixture.prior_activation,
            "former_witnessed_term": self.wal_fixture.prior_term,
            "verified_physical_wal_evidence": self.wal_evidence,
            "verified_v2_blob_requirement": requirement,
            "blob_evidence_config": self.blob_evidence_config,
            "verified_blob_binding": self.blob_binding,
            "pre_cas_operation_id": "precas-acceptance-20260731-0001",
            "authority": self.authority,
            "now": NOW,
        }
        values.update(overrides)
        return persist_physical_blob_pre_cas_acceptance(**values)

    def test_persists_exact_live_predecessor_v2_facts_and_rechecks_without_source_liveness(
        self,
    ) -> None:
        requirement = self.v2_requirement()
        with self.patch_v2_requirement(requirement) as verifier:
            acceptance = self.persist(requirement)
            self.assertIs(
                acceptance,
                require_verified_physical_blob_pre_cas_acceptance(
                    acceptance,
                    config=self.config,
                    now=NOW,
                ),
            )
            # The require path verifies only the signed durable record.  It
            # remains valid after the old source term itself has expired.
            self.assertIs(
                acceptance,
                require_verified_physical_blob_pre_cas_acceptance(
                    acceptance,
                    config=self.config,
                    now=NOW + timedelta(seconds=51),
                ),
            )

        self.assertEqual(len(self.authority.records), 1)
        self.assertEqual(
            self.authority.calls,
            [
                (
                    acceptance.canonical_acceptance,
                    hashlib.sha256(acceptance.canonical_acceptance).hexdigest(),
                )
            ],
        )
        self.assertEqual(acceptance.source_evidence_sha256, hashlib.sha256(self.wal_evidence.source_durability_receipt).hexdigest())
        self.assertEqual(
            acceptance.source_evidence_schema,
            "gold-trade-physical-wal-source-durability-receipt-v1",
        )
        self.assertEqual(acceptance.blob_mapping_object_version_id, "mapping-version-0001")
        self.assertEqual(acceptance.former_witnessed_term_proof_sha256, self.wal_fixture.prior_term.proof_sha256)
        verifier.assert_called_once_with(
            requirement,
            config=self.blob_evidence_config,
            verified_binding=self.blob_binding,
            now=NOW,
        )

    def test_raw_signed_readback_can_be_independently_verified_but_raw_is_not_a_capability(
        self,
    ) -> None:
        requirement = self.v2_requirement()
        with self.patch_v2_requirement(requirement):
            acceptance = self.persist(requirement)
        reread = verify_physical_blob_pre_cas_acceptance(
            canonical_acceptance=acceptance.canonical_acceptance,
            signed_authority_receipt=acceptance.signed_authority_receipt,
            config=self.config,
            now=NOW,
        )
        self.assertEqual(reread.pre_cas_operation_id, acceptance.pre_cas_operation_id)
        with self.assertRaisesRegex(
            PhysicalBlobPreCasAcceptanceError,
            "PRE_CAS_ACCEPTANCE_CAPABILITY_REQUIRED",
        ):
            require_verified_physical_blob_pre_cas_acceptance(
                acceptance.canonical_acceptance,
                config=self.config,
                now=NOW,
            )

    def test_v1_raw_blob_wrong_direction_baseline_term_and_bool_projections_fail_closed(self) -> None:
        legacy_v1 = PhysicalBlobInventoryShardObjectStorageReceipt(
            signed_receipt=b"legacy-v1",
            receipt_sha256="a" * 64,
            shard_ordinal=1,
            entry_count=1,
            plaintext_sha256="b" * 64,
            plaintext_bytes=1,
            blob_receipts_sha256="c" * 64,
            object_key="physical/blob/inventory-0001.age",
            version_id="inventory-version-0001",
            ciphertext_sha256="d" * 64,
            ciphertext_bytes=1,
            timeline_id=1,
            route_binding_sha256="e" * 64,
        )
        with self.assertRaisesRegex(
            PhysicalBlobPreCasAcceptanceError,
            "PRE_CAS_V2_BLOB_REQUIREMENT_UNVERIFIED",
        ):
            self.persist(legacy_v1)

        cases = (
            (replace(self.v2_requirement(), source_site="webapp_ir"), "PRE_CAS_V2_BLOB_ROUTE_MISMATCH"),
            (
                replace(self.v2_requirement(), baseline_generation_id="pg-base-fi-ir-0002"),
                "PRE_CAS_V2_BLOB_BASELINE_MISMATCH",
            ),
            (replace(self.v2_requirement(), writer_epoch=8), "PRE_CAS_V2_BLOB_FORMER_TERM_MISMATCH"),
            (replace(self.v2_requirement(), timeline_id=True), "PRE_CAS_V2_BLOB_TIMELINE_INVALID"),
            (replace(self.v2_requirement(), mapping_object_version_id=True), "PRE_CAS_V2_BLOB_MAPPING_VERSION_INVALID"),
        )
        for requirement, reason in cases:
            with self.subTest(reason=reason), self.patch_v2_requirement(requirement):
                with self.assertRaisesRegex(PhysicalBlobPreCasAcceptanceError, reason):
                    self.persist(requirement)

    def test_missing_authority_duplicate_or_wrong_readback_fails_closed(self) -> None:
        requirement = self.v2_requirement()
        with self.patch_v2_requirement(requirement):
            with self.assertRaisesRegex(
                PhysicalBlobPreCasAcceptanceError,
                "DURABLE_ACCEPTANCE_AUTHORITY_MISSING",
            ):
                self.persist(requirement, authority=object())

            self.authority.return_wrong_readback = True
            with self.assertRaisesRegex(
                PhysicalBlobPreCasAcceptanceError,
                "PRE_CAS_ACCEPTANCE_READBACK_MISMATCH",
            ):
                self.persist(requirement)
            self.authority.return_wrong_readback = False
            # The failed signed receipt still represents a durable append in
            # the fake authority.  A reused operation is therefore rejected.
            with self.assertRaisesRegex(
                PhysicalBlobPreCasAcceptanceError,
                "DURABLE_ACCEPTANCE_APPEND_OR_READBACK_FAILED",
            ):
                self.persist(requirement)

    def test_receipt_pin_staleness_and_wrapper_tamper_fail_closed(self) -> None:
        requirement = self.v2_requirement()
        with self.patch_v2_requirement(requirement):
            acceptance = self.persist(requirement)

        other_config = PhysicalBlobPreCasAcceptanceConfig(
            authority_public_key=public_key(Ed25519PrivateKey.generate()),
            enabled=True,
        )
        with self.assertRaisesRegex(
            PhysicalBlobPreCasAcceptanceError,
            "PRE_CAS_ACCEPTANCE_RECEIPT_SIGNATURE_INVALID",
        ):
            require_verified_physical_blob_pre_cas_acceptance(
                acceptance,
                config=other_config,
                now=NOW,
            )
        with self.assertRaisesRegex(
            PhysicalBlobPreCasAcceptanceError,
            "PRE_CAS_ACCEPTANCE_STALE_OR_FUTURE",
        ):
            require_verified_physical_blob_pre_cas_acceptance(
                acceptance,
                config=self.config,
                now=NOW + timedelta(seconds=601),
            )

        object.__setattr__(acceptance, "blob_timeline_id", True)
        with self.assertRaisesRegex(
            PhysicalBlobPreCasAcceptanceError,
            "PRE_CAS_ACCEPTANCE_CAPABILITY_TAMPERED",
        ):
            require_verified_physical_blob_pre_cas_acceptance(
                acceptance,
                config=self.config,
                now=NOW,
            )

    def test_dataclass_replace_and_source_evidence_substitution_cannot_mint_or_reuse_capability(
        self,
    ) -> None:
        requirement = self.v2_requirement()
        with self.patch_v2_requirement(requirement):
            acceptance = self.persist(requirement)
            with self.assertRaisesRegex(
                PhysicalBlobPreCasAcceptanceError,
                "PRE_CAS_ACCEPTANCE_CAPABILITY_REQUIRED",
            ):
                require_verified_physical_blob_pre_cas_acceptance(
                    replace(acceptance),
                    config=self.config,
                    now=NOW,
                )

        raw = json.loads(acceptance.canonical_acceptance)
        raw["source_evidence_sha256"] = "f" * 64
        forged_acceptance = canonical_json_bytes(raw)
        with self.assertRaisesRegex(
            PhysicalBlobPreCasAcceptanceError,
            "PRE_CAS_ACCEPTANCE_READBACK_MISMATCH",
        ):
            verify_physical_blob_pre_cas_acceptance(
                canonical_acceptance=forged_acceptance,
                signed_authority_receipt=acceptance.signed_authority_receipt,
                config=self.config,
                now=NOW,
            )

    def test_contract_has_no_direct_runtime_io_or_client_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_blob_pre_cas_acceptance.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "import os",
            "from os",
            "import socket",
            "from socket",
            "import subprocess",
            "from subprocess",
            "import sqlalchemy",
            "from sqlalchemy",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "import boto3",
            "from boto3",
            "import docker",
            "from docker",
        )
        self.assertFalse([item for item in forbidden if item in source])


if __name__ == "__main__":
    import unittest

    unittest.main()
