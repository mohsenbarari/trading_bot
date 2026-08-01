from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.physical_blob_object_storage_uploader import (
    PhysicalBlobInventoryShardObjectStorageReceipt,
)
from core.physical_blob_pre_cas_acceptance import (
    PhysicalBlobPreCasAcceptanceConfig,
    VerifiedPhysicalBlobPreCasAcceptance,
)
from core.physical_postgres_promotion_coordinator import (
    PhysicalPostgresPromotionCoordinatorConfig,
    PhysicalPostgresPromotionCoordinatorError,
    PhysicalPostgresPromotionRuntimeAdapters,
    prepare_physical_postgres_promotion,
    prepare_physical_postgres_promotion_execution_boundary,
    require_prepared_physical_postgres_promotion,
    require_prepared_physical_postgres_promotion_execution_boundary,
)
from core.physical_wal_promotion_gate import (
    PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
)
from tests import test_physical_wal_promotion_gate as physical_wal_gate_tests


NOW = physical_wal_gate_tests.NOW


class _WitnessCas:
    def __init__(self) -> None:
        self.calls = 0

    def consume_promotion_term(self, *, prepared: object) -> object:
        del prepared
        self.calls += 1
        return object()


class _FormerWriterFence:
    def __init__(self) -> None:
        self.calls = 0

    def fence_former_writer(self, *, prepared: object) -> object:
        del prepared
        self.calls += 1
        return object()


class _TargetRecovery:
    def __init__(self) -> None:
        self.calls = 0

    def recover_and_promote_target(self, *, prepared: object) -> object:
        del prepared
        self.calls += 1
        return object()


class _TrafficFence:
    def __init__(self) -> None:
        self.calls = 0

    def switch_fenced_traffic(self, *, prepared: object) -> object:
        del prepared
        self.calls += 1
        return object()


class _DatabaseTransaction:
    def __init__(self) -> None:
        self.calls = 0

    def run_promotion_transaction(self, *, prepared: object) -> object:
        del prepared
        self.calls += 1
        return object()


class PhysicalPostgresPromotionCoordinatorTests(TestCase):
    """Bind WAL re-assessment to an authority-signed pre-CAS acceptance.

    The durable acceptance has a dedicated verification suite.  These tests
    patch only its public capability recheck, so this file tests coordinator
    cross-binding and, crucially, the ordering rule: acceptance must predate
    the successor Witness term.  The coordinator never receives a Blob v2
    verifier, Blob binding, or former-source liveness input.
    """

    def setUp(self) -> None:
        self.wal_fixture = physical_wal_gate_tests.PhysicalWalPromotionGateTests(
            methodName="runTest"
        )
        self.wal_fixture.setUp()
        self.wal_evidence = self.wal_fixture.evidence()
        self.wal_eligibility = self.wal_fixture.assess(self.wal_evidence)
        source_digest = hashlib.sha256(self.wal_evidence.source_durability_receipt).hexdigest()
        self.remote_ack = self.wal_fixture._remote_ack_for_source_receipt_sha256[source_digest]
        self.assertIsNotNone(self.remote_ack)
        self.source_payload = json.loads(self.wal_evidence.source_durability_receipt)
        self.config = PhysicalPostgresPromotionCoordinatorConfig(enabled=True)
        self.pre_cas_acceptance_config = PhysicalBlobPreCasAcceptanceConfig(
            authority_public_key=b"a" * 32,
            enabled=True,
        )

    def pre_cas_acceptance(
        self,
        **overrides: object,
    ) -> VerifiedPhysicalBlobPreCasAcceptance:
        source = self.source_payload
        accepted_at = NOW - timedelta(seconds=15)
        values: dict[str, object] = {
            "canonical_acceptance": b'{"opaque":"durable-pre-cas"}',
            "signed_authority_receipt": b'{"opaque":"authority-readback"}',
            "authority_public_key": b"a" * 32,
            "pre_cas_operation_id": "precas-acceptance-20260731-0001",
            "source_site": source["source_site"],
            "destination_site": source["destination_site"],
            "campaign_id": source["campaign_id"],
            "release_sha": source["release_sha"],
            "stream_generation_id": source["stream_generation_id"],
            "baseline_generation_id": source["baseline_generation_id"],
            "baseline_manifest_sha256": source["baseline_manifest_sha256"],
            "baseline_wal_lsn": source["baseline_wal_lsn"],
            "destination_age_recipient": self.wal_fixture.policy.webapp_ir_age_recipient,
            "former_writer_epoch": source["prior_writer_epoch"],
            "former_writer_lease_id": source["prior_writer_lease_id"],
            "former_witness_transition_id": self.wal_fixture.prior_term.witness_transition_id,
            "former_witnessed_term_proof_sha256": source["prior_term_proof_sha256"],
            "source_evidence_schema": PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
            "source_evidence_sha256": hashlib.sha256(
                self.wal_evidence.source_durability_receipt
            ).hexdigest(),
            "blob_timeline_id": 1,
            "blob_route_binding_sha256": "d" * 64,
            "blob_mapping_plaintext_sha256": "e" * 64,
            "blob_mapping_receipt_sha256": "f" * 64,
            "blob_mapping_object_key": "physical/blob/mapping-0001.age",
            "blob_mapping_object_version_id": "mapping-version-0001",
            "blob_mapping_ciphertext_sha256": "4" * 64,
            "blob_mapping_ciphertext_bytes": 64,
            "original_v1_inventory_receipt_sha256": "2" * 64,
            "blob_receipts_sha256": "3" * 64,
            "blob_entry_count": 1,
            "blob_mapping_eligible_replay_wal_lsn": source["baseline_wal_lsn"],
            "accepted_at": accepted_at,
            "authority_receipt_sha256": "5" * 64,
            "authority_append_sequence": 1,
            "authority_issued_at": accepted_at,
        }
        values.update(overrides)
        return VerifiedPhysicalBlobPreCasAcceptance(**values)

    def prepare(self, acceptance: object, **overrides: object):
        values: dict[str, object] = {
            "config": self.config,
            "prior_activation": self.wal_fixture.prior_activation,
            "current_witnessed_term": self.wal_fixture.candidate_term,
            "supplied_physical_wal_eligibility": self.wal_eligibility,
            "verified_physical_wal_evidence": self.wal_evidence,
            "verified_remote_ack": self.remote_ack,
            "verified_pre_cas_blob_acceptance": acceptance,
            "pre_cas_acceptance_config": self.pre_cas_acceptance_config,
            "now": NOW,
        }
        values.update(overrides)
        return prepare_physical_postgres_promotion(**values)

    def patch_pre_cas_acceptance(self, acceptance: object):
        return patch(
            "core.physical_postgres_promotion_coordinator"
            ".require_verified_physical_blob_pre_cas_acceptance",
            return_value=acceptance,
        )

    def test_prepare_reassesses_wal_and_binds_durable_pre_cas_blob_acceptance(self) -> None:
        acceptance = self.pre_cas_acceptance()
        with self.patch_pre_cas_acceptance(acceptance) as verifier:
            prepared = self.prepare(acceptance)
            self.assertIs(
                prepared,
                require_prepared_physical_postgres_promotion(prepared, now=NOW),
            )

        self.assertEqual(prepared.source_site, "webapp_fi")
        self.assertEqual(prepared.target_site, "webapp_ir")
        self.assertEqual(prepared.baseline_generation_id, self.source_payload["baseline_generation_id"])
        self.assertEqual(prepared.baseline_wal_lsn, self.source_payload["baseline_wal_lsn"])
        self.assertEqual(prepared.source_writer_epoch, 7)
        self.assertEqual(prepared.candidate_writer_epoch, 8)
        self.assertEqual(prepared.blob_timeline_id, 1)
        self.assertEqual(verifier.call_count, 2)
        for call in verifier.call_args_list:
            self.assertIs(call.kwargs["config"], self.pre_cas_acceptance_config)
            self.assertEqual(call.kwargs["now"], NOW)

    def test_default_off_raw_wal_inputs_and_legacy_v1_blob_bridge_fail_closed(self) -> None:
        acceptance = self.pre_cas_acceptance()
        with self.assertRaisesRegex(PhysicalPostgresPromotionCoordinatorError, "COORDINATOR_DISABLED"):
            self.prepare(
                acceptance,
                config=PhysicalPostgresPromotionCoordinatorConfig(enabled=False),
            )

        with self.assertRaisesRegex(
            PhysicalPostgresPromotionCoordinatorError,
            "PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ):
            self.prepare(acceptance, verified_physical_wal_evidence={})

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
            PhysicalPostgresPromotionCoordinatorError,
            "PRE_CAS_BLOB_ACCEPTANCE_UNVERIFIED",
        ):
            self.prepare(legacy_v1)

    def test_wrong_route_baseline_term_bool_and_source_evidence_projections_are_rejected(self) -> None:
        cases = (
            (
                replace(self.pre_cas_acceptance(), source_site="webapp_ir"),
                "PRE_CAS_BLOB_ACCEPTANCE_ROUTE_MISMATCH",
            ),
            (
                replace(
                    self.pre_cas_acceptance(),
                    baseline_generation_id="pg-base-fi-ir-0002",
                ),
                "PRE_CAS_BLOB_ACCEPTANCE_BASELINE_MISMATCH",
            ),
            (
                replace(self.pre_cas_acceptance(), former_writer_epoch=8),
                "PRE_CAS_BLOB_ACCEPTANCE_SOURCE_TERM_MISMATCH",
            ),
            (
                replace(self.pre_cas_acceptance(), former_writer_epoch=True),
                "PRE_CAS_BLOB_ACCEPTANCE_FORMER_TERM_INVALID",
            ),
            (
                replace(self.pre_cas_acceptance(), blob_timeline_id=True),
                "PRE_CAS_BLOB_ACCEPTANCE_INVENTORY_INVALID",
            ),
            (
                replace(self.pre_cas_acceptance(), source_evidence_sha256="f" * 64),
                "PRE_CAS_BLOB_ACCEPTANCE_SOURCE_EVIDENCE_MISMATCH",
            ),
        )
        for acceptance, reason in cases:
            with self.subTest(reason=reason), self.patch_pre_cas_acceptance(acceptance):
                with self.assertRaisesRegex(PhysicalPostgresPromotionCoordinatorError, reason):
                    self.prepare(acceptance)

    def test_acceptance_must_be_durably_recorded_before_successor_term_is_issued(self) -> None:
        cases = (
            self.pre_cas_acceptance(accepted_at=NOW),
            self.pre_cas_acceptance(authority_issued_at=NOW),
        )
        for acceptance in cases:
            with self.subTest(
                accepted_at=acceptance.accepted_at,
                authority_issued_at=acceptance.authority_issued_at,
            ), self.patch_pre_cas_acceptance(acceptance):
                with self.assertRaisesRegex(
                    PhysicalPostgresPromotionCoordinatorError,
                    "PRE_CAS_BLOB_ACCEPTANCE_AFTER_SUCCESSOR_TERM",
                ):
                    self.prepare(acceptance)

    def test_stale_wrong_or_boolean_current_term_fails_before_reassessment(self) -> None:
        acceptance = self.pre_cas_acceptance()
        wrong_target = self.wal_fixture.witnessed_term(
            holder_site="webapp_fi",
            writer_epoch=8,
            writer_lease_id="writer-lease-8-new",
        )
        with self.patch_pre_cas_acceptance(acceptance):
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "CURRENT_WITNESS_TERM_WRONG_TARGET",
            ):
                self.prepare(acceptance, current_witnessed_term=wrong_target)
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "CURRENT_WITNESS_TERM_UNVERIFIED",
            ):
                self.prepare(acceptance, now=NOW + timedelta(seconds=46))

        object.__setattr__(self.wal_fixture.candidate_term, "writer_epoch", True)
        with self.patch_pre_cas_acceptance(acceptance):
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "CURRENT_WITNESS_TERM_UNVERIFIED",
            ):
                self.prepare(acceptance)

    def test_successor_term_is_reassessed_against_the_signed_wal_continuity_artifact(self) -> None:
        acceptance = self.pre_cas_acceptance()
        other_successor = self.wal_fixture.witnessed_term(
            holder_site="webapp_ir",
            writer_epoch=9,
            writer_lease_id="writer-lease-9",
        )
        with self.patch_pre_cas_acceptance(acceptance):
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "PHYSICAL_WAL_ELIGIBILITY_REASSESSMENT_BLOCKED",
            ):
                self.prepare(acceptance, current_witnessed_term=other_successor)

    def test_destination_recipient_must_match_active_route(self) -> None:
        acceptance = self.pre_cas_acceptance(
            destination_age_recipient="age1" + "z" * 30,
        )
        with self.patch_pre_cas_acceptance(acceptance):
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "PRE_CAS_BLOB_ACCEPTANCE_DESTINATION_RECIPIENT_MISMATCH",
            ):
                self.prepare(acceptance)

    def test_tampered_or_stale_wal_assessment_cannot_be_reused(self) -> None:
        acceptance = self.pre_cas_acceptance()
        object.__setattr__(
            self.wal_eligibility,
            "baseline_generation_id",
            "pg-base-fi-ir-0002",
        )
        with self.patch_pre_cas_acceptance(acceptance):
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "PHYSICAL_WAL_ELIGIBILITY_REASSESSMENT_MISMATCH",
            ):
                self.prepare(acceptance)

    def test_prepared_recheck_detects_projection_tamper(self) -> None:
        acceptance = self.pre_cas_acceptance()
        with self.patch_pre_cas_acceptance(acceptance):
            prepared = self.prepare(acceptance)
            object.__setattr__(prepared, "candidate_writer_epoch", 999)
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "PREPARED_PROMOTION_TAMPERED_OR_STALE",
            ):
                require_prepared_physical_postgres_promotion(prepared, now=NOW)

    def test_execution_boundary_requires_every_explicit_runtime_interface_and_never_calls_them(
        self,
    ) -> None:
        acceptance = self.pre_cas_acceptance()
        with self.patch_pre_cas_acceptance(acceptance):
            prepared = self.prepare(acceptance)
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "RUNTIME_ADAPTER_WITNESS_CAS_MISSING.*RUNTIME_ADAPTER_DATABASE_TRANSACTION_MISSING",
            ):
                prepare_physical_postgres_promotion_execution_boundary(
                    prepared_promotion=prepared,
                    runtime_adapters=PhysicalPostgresPromotionRuntimeAdapters(),
                    now=NOW,
                )

            witness = _WitnessCas()
            former_writer = _FormerWriterFence()
            recovery = _TargetRecovery()
            traffic = _TrafficFence()
            database = _DatabaseTransaction()
            adapters = PhysicalPostgresPromotionRuntimeAdapters(
                witness_cas=witness,
                former_writer_fence=former_writer,
                target_recovery=recovery,
                traffic_fence=traffic,
                promotion_database_transaction=database,
            )
            boundary = prepare_physical_postgres_promotion_execution_boundary(
                prepared_promotion=prepared,
                runtime_adapters=adapters,
                now=NOW,
            )
            self.assertIs(
                boundary,
                require_prepared_physical_postgres_promotion_execution_boundary(
                    boundary,
                    now=NOW,
                ),
            )

        self.assertEqual(
            (witness.calls, former_writer.calls, recovery.calls, traffic.calls, database.calls),
            (0, 0, 0, 0, 0),
        )

    def test_dataclass_replace_cannot_forge_prepared_or_execution_capability(self) -> None:
        acceptance = self.pre_cas_acceptance()
        with self.patch_pre_cas_acceptance(acceptance):
            prepared = self.prepare(acceptance)
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "PREPARED_PROMOTION_UNAUTHORIZED",
            ):
                require_prepared_physical_postgres_promotion(replace(prepared), now=NOW)

            adapters = PhysicalPostgresPromotionRuntimeAdapters(
                witness_cas=_WitnessCas(),
                former_writer_fence=_FormerWriterFence(),
                target_recovery=_TargetRecovery(),
                traffic_fence=_TrafficFence(),
                promotion_database_transaction=_DatabaseTransaction(),
            )
            boundary = prepare_physical_postgres_promotion_execution_boundary(
                prepared_promotion=prepared,
                runtime_adapters=adapters,
                now=NOW,
            )
            with self.assertRaisesRegex(
                PhysicalPostgresPromotionCoordinatorError,
                "PREPARED_EXECUTION_BOUNDARY_UNAUTHORIZED",
            ):
                require_prepared_physical_postgres_promotion_execution_boundary(
                    replace(boundary),
                    now=NOW,
                )

    def test_contract_contains_no_runtime_io_or_former_v2_revalidation_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_postgres_promotion_coordinator.py"
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
            "require_physical_wal_promotion_v2_blob_requirement",
            "PhysicalBlobReceiverPromotionEvidenceConfig",
            "VerifiedPhysicalWalPromotionV2BlobRequirement",
        )
        self.assertFalse([item for item in forbidden if item in source])


if __name__ == "__main__":
    import unittest

    unittest.main()
