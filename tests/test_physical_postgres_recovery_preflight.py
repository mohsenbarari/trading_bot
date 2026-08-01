from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_postgres_recovery_preflight import (
    PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_DEFAULT_ENABLED,
    PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED,
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryReceiverReadbackEvidence,
    PhysicalPostgresRecoveryStageBinding,
    assess_physical_postgres_recovery_preflight,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    build_physical_wal_base_backup_manifest,
    build_physical_wal_blob_frontier_manifest,
    build_physical_wal_segment_manifest,
    verify_physical_wal_object_storage_bundle,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "physical-recovery-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
BASE_GENERATION = "physical-recovery-generation-20260731"
SYSTEM_IDENTIFIER = "7234567890123456789"
WAL_SEGMENT_SIZE = 16 * 1024 * 1024
RECIPIENTS = {
    "webapp_fi": "age1" + "c" * 30,
    "webapp_ir": "age1" + "a" * 30,
}


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def object_descriptor(
    kind: str,
    object_key: str,
    *,
    version_id: str,
    recipient: str,
    marker: str,
) -> dict[str, object]:
    return {
        "schema": "gold-trade-physical-wal-object-descriptor-v1",
        "version": 1,
        "object_kind": kind,
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": marker * 64,
        "ciphertext_bytes": 4096,
        "encryption": "age-v1",
        "age_recipient": recipient,
        "immutability": "versioned_create_only_readback_v1",
    }


class PhysicalPostgresRecoveryPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi_signer = Ed25519PrivateKey.generate()
        self.ir_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()

    def witnessed_term(self, *, holder_site: str, epoch: int = 7):
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site=holder_site,
            writer_epoch=epoch,
            writer_lease_id=f"writer-lease-{epoch}",
            witness_transition_id=f"transition-{epoch}-{holder_site}",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=70),
            witness_signer=self.witness_signer,
        )
        return verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=public_key(self.witness_signer),
            maximum_lease_duration_seconds=90,
            safety_margin_seconds=5,
            now=NOW,
        )

    def bundle(self, *, source_site: str = "webapp_fi", destination_site: str = "webapp_ir"):
        signer = self.fi_signer if source_site == "webapp_fi" else self.ir_signer
        source_key = public_key(signer)
        term = self.witnessed_term(holder_site=source_site)
        recipient = RECIPIENTS[destination_site]
        route = f"{source_site}-{destination_site}"
        base = build_physical_wal_base_backup_manifest(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id=BASE_GENERATION,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            baseline_wal_lsn="0/1800000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/2800000",
            base_backup_object=object_descriptor(
                "physical_postgresql_base_backup",
                f"physical/{route}/base/backup-001.age",
                version_id=f"base-version-{route}",
                recipient=recipient,
                marker="a",
            ),
            source_signer=signer,
        )
        base_hash = sha(canonical_json_bytes(base))
        wal = build_physical_wal_segment_manifest(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=base_hash,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            previous_manifest_sha256=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
            previous_end_lsn="0/1000000",
            previous_segment_ordinal=0,
            segments=(
                {
                    "ordinal": 1,
                    "wal_segment_name": "000000010000000000000001",
                    "timeline_id": 1,
                    "start_lsn": "0/1000000",
                    "end_lsn": "0/2000000",
                    "object": object_descriptor(
                        "postgresql_wal_segment",
                        f"physical/{route}/wal/0001.age",
                        version_id=f"wal-version-{route}-0001",
                        recipient=recipient,
                        marker="b",
                    ),
                },
                {
                    "ordinal": 2,
                    "wal_segment_name": "000000010000000000000002",
                    "timeline_id": 1,
                    "start_lsn": "0/2000000",
                    "end_lsn": "0/3000000",
                    "object": object_descriptor(
                        "postgresql_wal_segment",
                        f"physical/{route}/wal/0002.age",
                        version_id=f"wal-version-{route}-0002",
                        recipient=recipient,
                        marker="c",
                    ),
                },
            ),
            source_signer=signer,
        )
        blob = build_physical_wal_blob_frontier_manifest(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=base_hash,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            previous_manifest_sha256=PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
            previous_frontier_wal_lsn="0/1800000",
            blob_object_frontier_wal_lsn="0/3000000",
            inventory_shards=(
                {
                    "ordinal": 1,
                    "plaintext_sha256": "d" * 64,
                    "plaintext_bytes": 1024,
                    "entry_count": 1,
                    "object": object_descriptor(
                        "blob_inventory_shard",
                        f"physical/{route}/blob/inventory-0001.age",
                        version_id=f"blob-version-{route}-0001",
                        recipient=recipient,
                        marker="d",
                    ),
                },
            ),
            source_signer=signer,
        )
        return (
            verify_physical_wal_object_storage_bundle(
                base_backup_manifest=base,
                wal_segment_manifests=(wal,),
                blob_frontier_manifest=blob,
                expected_source_public_key=source_key,
                expected_source_site=source_site,
                expected_destination_site=destination_site,
                expected_campaign_id=CAMPAIGN,
                expected_release_sha=RELEASE,
                expected_writer_epoch=term.writer_epoch,
                expected_writer_lease_id=term.writer_lease_id,
                expected_witnessed_term_proof_sha256=term.proof_sha256,
                expected_baseline_generation_id=BASE_GENERATION,
                expected_wal_segment_size_bytes=WAL_SEGMENT_SIZE,
                expected_destination_age_recipient=recipient,
            ),
            term,
        )

    @staticmethod
    def object_versions(bundle) -> list[dict[str, str]]:
        pairs = [
            (bundle.baseline.base_backup_object.object_key, bundle.baseline.base_backup_object.version_id)
        ]
        for manifest in bundle.wal_manifests:
            pairs.extend((segment.object.object_key, segment.object.version_id) for segment in manifest.segments)
        pairs.extend(
            (shard.object.object_key, shard.object.version_id)
            for shard in bundle.blob_frontier.inventory_shards
        )
        return [{"object_key": key, "version_id": version} for key, version in pairs]

    @staticmethod
    def stage_binding(*, source_site: str, destination_site: str) -> PhysicalPostgresRecoveryStageBinding:
        route = f"{source_site}-{destination_site}"
        return PhysicalPostgresRecoveryStageBinding(
            bundle_id=sha("bundle-" + route),
            stage_receipt_sha256=sha("stage-receipt-" + route),
            route_binding_sha256=sha("route-binding-" + route),
        )

    def binding(self, *, local_standby_site: str, term, stage: PhysicalPostgresRecoveryStageBinding):
        return PhysicalPostgresRecoveryPreflightBinding(
            local_standby_site=local_standby_site,
            stage_binding=stage,
            expected_witnessed_term=term,
        )

    def readback_payload(
        self,
        *,
        bundle,
        term,
        stage: PhysicalPostgresRecoveryStageBinding,
        status: str = PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
        receiver_site: str | None = None,
        source_site: str | None = None,
        destination_site: str | None = None,
        in_recovery: bool = True,
        role: str = "standby",
        system_identifier: str = SYSTEM_IDENTIFIER,
        timeline_id: int = 1,
        wal_segment_size_bytes: int = WAL_SEGMENT_SIZE,
        baseline_generation_id: str = BASE_GENERATION,
        replay_lsn: str | None = None,
        manifest_sha256es: list[str] | None = None,
        object_versions: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        baseline = bundle.baseline
        return {
            "schema": PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA,
            "status": status,
            "observed_at": NOW.isoformat(),
            "receiver_site": receiver_site or baseline.destination_site,
            "source_site": source_site or baseline.source_site,
            "destination_site": destination_site or baseline.destination_site,
            "stage_bundle_id": stage.bundle_id,
            "stage_receipt_sha256": stage.stage_receipt_sha256,
            "route_binding_sha256": stage.route_binding_sha256,
            "manifest_sha256es": list(bundle.manifest_sha256es)
            if manifest_sha256es is None
            else manifest_sha256es,
            "object_versions": self.object_versions(bundle)
            if object_versions is None
            else object_versions,
            "base_backup_manifest_sha256": baseline.manifest_sha256,
            "bundle_terminal_wal_lsn": bundle.terminal_wal_lsn,
            "writer_term": {
                "holder_site": term.holder_site,
                "writer_epoch": term.writer_epoch,
                "writer_lease_id": term.writer_lease_id,
                "witness_transition_id": term.witness_transition_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
            },
            "postgresql": {
                "in_recovery": in_recovery,
                "role": role,
                "database_system_identifier": system_identifier,
                "timeline_id": timeline_id,
                "wal_segment_size_bytes": wal_segment_size_bytes,
                "baseline_generation_id": baseline_generation_id,
                "replay_lsn": replay_lsn or bundle.terminal_wal_lsn,
            },
        }

    @staticmethod
    def evidence(raw: bytes) -> PhysicalPostgresRecoveryReceiverReadbackEvidence:
        return PhysicalPostgresRecoveryReceiverReadbackEvidence(
            raw_evidence=raw,
            evidence_sha256=sha(raw),
        )

    def assess(self, *, bundle, term, stage, evidence, local_standby_site: str):
        return assess_physical_postgres_recovery_preflight(
            bundle=bundle,
            binding=self.binding(
                local_standby_site=local_standby_site,
                term=term,
                stage=stage,
            ),
            receiver_readback_evidence=evidence,
            now=NOW,
        )

    def test_fi_to_ir_exact_terminal_replay_is_observed(self) -> None:
        bundle, term = self.bundle()
        stage = self.stage_binding(source_site="webapp_fi", destination_site="webapp_ir")
        raw = canonical_json_bytes(self.readback_payload(bundle=bundle, term=term, stage=stage))

        result = self.assess(
            bundle=bundle,
            term=term,
            stage=stage,
            evidence=self.evidence(raw),
            local_standby_site="webapp_ir",
        )

        self.assertFalse(PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_DEFAULT_ENABLED)
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED, result.status)
        self.assertEqual((), result.reason_codes)
        self.assertTrue(result.replay_evidence_observed)
        self.assertEqual(bundle.terminal_wal_lsn, result.replay_lsn)
        self.assertEqual(tuple(bundle.manifest_sha256es), result.manifest_sha256es)

    def test_ir_to_fi_exact_terminal_replay_is_observed(self) -> None:
        bundle, term = self.bundle(source_site="webapp_ir", destination_site="webapp_fi")
        stage = self.stage_binding(source_site="webapp_ir", destination_site="webapp_fi")
        raw = canonical_json_bytes(self.readback_payload(bundle=bundle, term=term, stage=stage))

        result = self.assess(
            bundle=bundle,
            term=term,
            stage=stage,
            evidence=self.evidence(raw),
            local_standby_site="webapp_fi",
        )

        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED, result.status)
        self.assertEqual("webapp_ir", result.source_site)
        self.assertEqual("webapp_fi", result.destination_site)

    def test_staged_state_stays_explicitly_not_replay_verified(self) -> None:
        bundle, term = self.bundle()
        stage = self.stage_binding(source_site="webapp_fi", destination_site="webapp_ir")
        raw = canonical_json_bytes(
            self.readback_payload(
                bundle=bundle,
                term=term,
                stage=stage,
                status=PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED,
            )
        )

        result = self.assess(
            bundle=bundle,
            term=term,
            stage=stage,
            evidence=self.evidence(raw),
            local_standby_site="webapp_ir",
        )

        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED, result.status)
        self.assertEqual(("REPLAY_EVIDENCE_NOT_OBSERVED",), result.reason_codes)
        self.assertFalse(result.replay_evidence_observed)

    def test_duplicate_and_noncanonical_receiver_evidence_fail_closed(self) -> None:
        bundle, term = self.bundle()
        stage = self.stage_binding(source_site="webapp_fi", destination_site="webapp_ir")
        canonical = canonical_json_bytes(self.readback_payload(bundle=bundle, term=term, stage=stage))
        duplicate = canonical[:-1] + b',"receiver_site":"webapp_ir"}'

        for raw, code in (
            (duplicate, "RECEIVER_EVIDENCE_DUPLICATE_JSON_FIELD"),
            (canonical + b"\n", "RECEIVER_EVIDENCE_NOT_CANONICAL"),
        ):
            with self.subTest(code=code):
                result = self.assess(
                    bundle=bundle,
                    term=term,
                    stage=stage,
                    evidence=self.evidence(raw),
                    local_standby_site="webapp_ir",
                )
                self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, result.status)
                self.assertEqual((code,), result.reason_codes)

    def test_tampered_bundle_and_exact_object_version_mismatch_fail_closed(self) -> None:
        bundle, term = self.bundle()
        stage = self.stage_binding(source_site="webapp_fi", destination_site="webapp_ir")
        raw = canonical_json_bytes(self.readback_payload(bundle=bundle, term=term, stage=stage))
        forged = replace(bundle, terminal_wal_lsn="0/4000000")
        object.__setattr__(forged, "_capability", bundle._capability)

        tampered_bundle_result = self.assess(
            bundle=forged,
            term=term,
            stage=stage,
            evidence=self.evidence(raw),
            local_standby_site="webapp_ir",
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, tampered_bundle_result.status)
        self.assertEqual(("BUNDLE_UNVERIFIED_OR_TAMPERED",), tampered_bundle_result.reason_codes)

        versions = self.object_versions(bundle)
        versions[0] = {
            "object_key": versions[0]["object_key"],
            "version_id": "foreign-version-0001",
        }
        mismatched_raw = canonical_json_bytes(
            self.readback_payload(
                bundle=bundle,
                term=term,
                stage=stage,
                object_versions=versions,
            )
        )
        mismatch_result = self.assess(
            bundle=bundle,
            term=term,
            stage=stage,
            evidence=self.evidence(mismatched_raw),
            local_standby_site="webapp_ir",
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, mismatch_result.status)
        self.assertEqual(("RECEIVER_EVIDENCE_EXACT_BUNDLE_MISMATCH",), mismatch_result.reason_codes)

    def test_incorrect_route_or_nonlocal_destination_fails_closed(self) -> None:
        bundle, term = self.bundle()
        stage = self.stage_binding(source_site="webapp_fi", destination_site="webapp_ir")
        wrong_route_raw = canonical_json_bytes(
            self.readback_payload(
                bundle=bundle,
                term=term,
                stage=stage,
                source_site="webapp_ir",
                destination_site="webapp_fi",
                receiver_site="webapp_fi",
            )
        )
        route_result = self.assess(
            bundle=bundle,
            term=term,
            stage=stage,
            evidence=self.evidence(wrong_route_raw),
            local_standby_site="webapp_ir",
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, route_result.status)
        self.assertEqual(("RECEIVER_EVIDENCE_ROUTE_OR_STAGE_MISMATCH",), route_result.reason_codes)

        local_destination_result = self.assess(
            bundle=bundle,
            term=term,
            stage=stage,
            evidence=self.evidence(
                canonical_json_bytes(self.readback_payload(bundle=bundle, term=term, stage=stage))
            ),
            local_standby_site="webapp_fi",
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, local_destination_result.status)
        self.assertEqual(
            ("BUNDLE_DESTINATION_IS_NOT_LOCAL_STANDBY",),
            local_destination_result.reason_codes,
        )

    def test_under_replay_and_promotion_state_fail_closed(self) -> None:
        bundle, term = self.bundle()
        stage = self.stage_binding(source_site="webapp_fi", destination_site="webapp_ir")
        under_replay = canonical_json_bytes(
            self.readback_payload(
                bundle=bundle,
                term=term,
                stage=stage,
                replay_lsn="0/2000000",
            )
        )
        under_replay_result = self.assess(
            bundle=bundle,
            term=term,
            stage=stage,
            evidence=self.evidence(under_replay),
            local_standby_site="webapp_ir",
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, under_replay_result.status)
        self.assertEqual(
            ("REPLAY_LSN_BEHIND_BUNDLE_TERMINAL_FRONTIER",),
            under_replay_result.reason_codes,
        )

        promoted_raw = canonical_json_bytes(
            self.readback_payload(
                bundle=bundle,
                term=term,
                stage=stage,
                in_recovery=False,
                role="primary",
            )
        )
        promoted_result = self.assess(
            bundle=bundle,
            term=term,
            stage=stage,
            evidence=self.evidence(promoted_raw),
            local_standby_site="webapp_ir",
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, promoted_result.status)
        self.assertEqual(("POSTGRES_NOT_RECOVERY_STANDBY",), promoted_result.reason_codes)

    def test_postgresql_identity_timeline_geometry_and_generation_mismatches_fail_closed(self) -> None:
        bundle, term = self.bundle()
        stage = self.stage_binding(source_site="webapp_fi", destination_site="webapp_ir")
        cases = (
            ({"system_identifier": "7234567890123456788"}, "POSTGRES_SYSTEM_IDENTIFIER_MISMATCH"),
            ({"timeline_id": 2}, "POSTGRES_TIMELINE_MISMATCH"),
            ({"wal_segment_size_bytes": 8 * 1024 * 1024}, "POSTGRES_READBACK_INVALID"),
            ({"baseline_generation_id": "foreign-generation-20260731"}, "POSTGRES_BASE_GENERATION_MISMATCH"),
        )
        for overrides, code in cases:
            with self.subTest(code=code):
                raw = canonical_json_bytes(
                    self.readback_payload(bundle=bundle, term=term, stage=stage, **overrides)
                )
                result = self.assess(
                    bundle=bundle,
                    term=term,
                    stage=stage,
                    evidence=self.evidence(raw),
                    local_standby_site="webapp_ir",
                )
                self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, result.status)
                self.assertEqual((code,), result.reason_codes)

    def test_module_keeps_no_runtime_or_receiver_staging_dependency(self) -> None:
        path = Path(__file__).resolve().parents[1] / "core/physical_postgres_recovery_preflight.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        forbidden = {
            "os",
            "subprocess",
            "socket",
            "requests",
            "httpx",
            "aiohttp",
            "boto3",
            "psycopg",
            "sqlalchemy",
        }
        self.assertFalse(imports & forbidden)
        self.assertNotIn("core.physical_wal_receiver_staging", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
