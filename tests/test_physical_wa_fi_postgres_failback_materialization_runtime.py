"""No-network tests for the distinct detached WA-FI failback materializer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_ir_to_fi_object_storage_failback_preflight as preflight
from core import physical_wa_fi_postgres_failback_materialization_runtime as materializer
from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_postgres_recovery_preflight import (
    PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryReceiverReadbackEvidence,
    PhysicalPostgresRecoveryStageBinding,
)
from core.physical_release_candidate_writer_quiescence_receipt import (
    PhysicalReleaseCandidateWriterQuiescenceAuthorityPin,
    PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy,
    RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig,
    build_signed_physical_release_candidate_writer_quiescence_receipt,
    verify_physical_release_candidate_writer_quiescence_receipt,
)
from core.physical_wa_fi_postgres_failback_pull_runtime import (
    PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA,
    PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED,
    PhysicalWaFiPostgresFailbackPullRedactedReceipt,
    PhysicalWaFiPostgresFailbackPullResult,
    PhysicalWaFiPostgresFailbackStageEvidence,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    build_physical_wal_base_backup_manifest,
    build_physical_wal_blob_frontier_manifest,
    build_physical_wal_segment_manifest,
    verify_physical_wal_object_storage_bundle,
)
from tests.physical_arvan_s3_four_role_fixture import make_four_role_fixture
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "wa-fi-failback-materialize-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1" + "d" * 30
WAL_BYTES = 16 * 1024 * 1024


def _sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _Runner:
    def __init__(self, owner: "PhysicalWaFiPostgresFailbackMaterializationRuntimeTests", *, forged: bool = False) -> None:
        self.owner = owner
        self.forged = forged
        self.calls: list[materializer.PhysicalWaFiPostgresFailbackMaterializationInvocation] = []

    def materialize_and_inspect_detached_failback_standby(self, *, invocation, source_stage):
        self.calls.append(invocation)
        self.owner.assertEqual(self.owner.source_candidate, source_stage.source_candidate)
        self.owner.assertEqual("none", invocation.network_mode)
        self.owner.assertEqual("disabled", invocation.tcp_listener)
        self.owner.assertEqual("standby-replay-only", invocation.recovery_mode)
        target = invocation.target_pgdata_candidate
        target.mkdir(mode=0o700)
        target.chmod(0o700)
        info = target.stat()
        return materializer.PhysicalWaFiPostgresFailbackMaterializationAck(
            schema="gold-trade-physical-wa-fi-postgres-failback-runner-ack-v1",
            status="local-detached-standby-replay-observed",
            invocation_sha256=("f" * 64 if self.forged else invocation.invocation_sha256),
            target_pgdata_candidate=target,
            target_pgdata_device=info.st_dev,
            target_pgdata_inode=info.st_ino,
            recovery_readback_evidence=self.owner.recovery_evidence,
        )


@unittest.skipUnless(os.geteuid() == 0, "FI failback materializer is root-only")
class PhysicalWaFiPostgresFailbackMaterializationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-fi-failback-materializer-")
        self.root = Path(self.temporary.name).resolve()
        self.stage_root = self.root / "source-stage-candidates"
        self.target_root = self.root / "detached-pgdata-candidates"
        self.receipt_root = self.root / "redacted-receipts"
        self.fenced_writer_root = self.root / "fenced-writer-root"
        for path in (
            self.stage_root,
            self.target_root,
            self.receipt_root,
            self.fenced_writer_root,
        ):
            path.mkdir(mode=0o700)
            path.chmod(0o700)
        self.source_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.term = self._term()
        self.bundle = self._bundle()
        self.four_role = make_four_role_fixture(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            fi_publisher_identity_sha256="a" * 64,
            ir_receiver_identity_sha256="b" * 64,
            ir_publisher_identity_sha256="c" * 64,
            fi_receiver_identity_sha256="d" * 64,
        )
        self.live_iam = make_four_role_live_iam_durable_admission_fixture(
            binding=self.four_role.binding,
            observed_at=NOW,
        )
        observed = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
            binding=self.four_role.binding,
            four_role_projection_binding=self.four_role.verified_binding,
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
            observed_at=NOW,
        )
        self.preflight = preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
            observed,
            binding=self.four_role.binding,
            four_role_projection_binding=self.four_role.verified_binding,
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
            now=NOW,
        )
        self.preflight_config = self.four_role.preflight_config(
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
        )
        self.bundle_id = "9" * 64
        self.stage_receipt_sha256 = _sha(b"{}")
        self.source_candidate = self.stage_root / self.bundle_id
        self.source_candidate.mkdir(mode=0o700)
        self.source_candidate.chmod(0o700)
        self.pulled = self._pulled()
        self.quiescence_config, self.quiescence_receipt, self.quiescence_binding = self._quiescence()
        self.recovery_evidence = self._recovery_evidence()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _term(self):
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_ir",
            writer_epoch=91,
            writer_lease_id="ir-failback-writer-lease-91",
            witness_transition_id="ir-failback-transition-91",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=100),
            witness_signer=self.witness_signer,
        )
        return verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=_public_key(self.witness_signer),
            maximum_lease_duration_seconds=120,
            safety_margin_seconds=5,
            now=NOW,
        )

    def _descriptor(self, *, kind: str, key: str, version: str, payload: bytes) -> dict[str, object]:
        return {
            "schema": "gold-trade-physical-wal-object-descriptor-v1",
            "version": 1,
            "object_kind": kind,
            "object_key": key,
            "version_id": version,
            "ciphertext_sha256": _sha(payload),
            "ciphertext_bytes": len(payload),
            "encryption": "age-v1",
            "age_recipient": RECIPIENT,
            "immutability": "versioned_create_only_readback_v1",
        }

    def _bundle(self):
        base_key = "physical-failback/materialize/base-001.age"
        wal_key = "physical-failback/materialize/wal-001.age"
        blob_key = "physical-failback/materialize/blob-001.age"
        base_payload = b"age-encryption.org/v1\nbase"
        wal_payload = b"age-encryption.org/v1\nwal"
        blob_payload = b"age-encryption.org/v1\nblob"
        base = build_physical_wal_base_backup_manifest(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=self.term.writer_epoch,
            writer_lease_id=self.term.writer_lease_id,
            witnessed_term_proof_sha256=self.term.proof_sha256,
            baseline_generation_id="wa-fi-failback-materialize-generation-20260731",
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
            baseline_wal_lsn="0/1000000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/1800000",
            base_backup_object=self._descriptor(
                kind="physical_postgresql_base_backup",
                key=base_key,
                version="base-version-001",
                payload=base_payload,
            ),
            source_signer=self.source_signer,
        )
        base_hash = _sha(canonical_json_bytes(base))
        wal = build_physical_wal_segment_manifest(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=self.term.writer_epoch,
            writer_lease_id=self.term.writer_lease_id,
            witnessed_term_proof_sha256=self.term.proof_sha256,
            baseline_generation_id="wa-fi-failback-materialize-generation-20260731",
            baseline_manifest_sha256=base_hash,
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
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
                    "object": self._descriptor(
                        kind="postgresql_wal_segment",
                        key=wal_key,
                        version="wal-version-001",
                        payload=wal_payload,
                    ),
                },
            ),
            source_signer=self.source_signer,
        )
        blob = build_physical_wal_blob_frontier_manifest(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=self.term.writer_epoch,
            writer_lease_id=self.term.writer_lease_id,
            witnessed_term_proof_sha256=self.term.proof_sha256,
            baseline_generation_id="wa-fi-failback-materialize-generation-20260731",
            baseline_manifest_sha256=base_hash,
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
            previous_manifest_sha256=PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
            previous_frontier_wal_lsn="0/1000000",
            blob_object_frontier_wal_lsn="0/2000000",
            inventory_shards=(
                {
                    "ordinal": 1,
                    "plaintext_sha256": _sha(b"inventory"),
                    "plaintext_bytes": len(b"inventory"),
                    "entry_count": 1,
                    "object": self._descriptor(
                        kind="blob_inventory_shard",
                        key=blob_key,
                        version="blob-version-001",
                        payload=blob_payload,
                    ),
                },
            ),
            source_signer=self.source_signer,
        )
        return verify_physical_wal_object_storage_bundle(
            base_backup_manifest=base,
            wal_segment_manifests=(wal,),
            blob_frontier_manifest=blob,
            expected_source_public_key=_public_key(self.source_signer),
            expected_source_site="webapp_ir",
            expected_destination_site="webapp_fi",
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_writer_epoch=self.term.writer_epoch,
            expected_writer_lease_id=self.term.writer_lease_id,
            expected_witnessed_term_proof_sha256=self.term.proof_sha256,
            expected_baseline_generation_id="wa-fi-failback-materialize-generation-20260731",
            expected_wal_segment_size_bytes=WAL_BYTES,
            expected_destination_age_recipient=RECIPIENT,
        )

    def _pulled(self) -> PhysicalWaFiPostgresFailbackPullResult:
        route_binding = "8" * 64
        receipt = PhysicalWaFiPostgresFailbackPullRedactedReceipt(
            raw_receipt=b"{}",
            receipt_sha256=_sha(b"{}"),
            bundle_id=self.bundle_id,
            stage_receipt_sha256=self.stage_receipt_sha256,
            route_binding_sha256=route_binding,
        )
        binding = PhysicalPostgresRecoveryPreflightBinding(
            local_standby_site="webapp_fi",
            stage_binding=PhysicalPostgresRecoveryStageBinding(
                bundle_id=self.bundle_id,
                stage_receipt_sha256=self.stage_receipt_sha256,
                route_binding_sha256=route_binding,
            ),
            expected_witnessed_term=self.term,
        )
        return PhysicalWaFiPostgresFailbackPullResult(
            schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_RUNTIME_SCHEMA,
            status=PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED,
            reason_codes=(),
            redacted_receipt=receipt,
            recovery_preflight_binding=binding,
            failback_stage_evidence=PhysicalWaFiPostgresFailbackStageEvidence(
                source_candidate=self.source_candidate,
                raw_stage_receipt=b"{}",
                stage_receipt_sha256=self.stage_receipt_sha256,
            ),
            promotion_authorized=False,
            full_matrix_authorized=False,
        )

    def _quiescence(self):
        signer = Ed25519PrivateKey.generate()
        policy = PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy(
            source_root=self.fenced_writer_root,
            required_mode=0o700,
        )
        public_key = _public_key(signer)
        config = RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig(
            source_root_policy=policy,
            authority=PhysicalReleaseCandidateWriterQuiescenceAuthorityPin(
                public_key=public_key,
                key_id="ed25519-sha256:" + _sha(public_key),
            ),
            enabled=True,
            maximum_receipt_age_seconds=120,
        )
        inventory = "1" * 64
        frozen = "2" * 64
        evidence = "3" * 64
        raw = build_signed_physical_release_candidate_writer_quiescence_receipt(
            source_root_policy=policy,
            inventory_manifest_sha256=inventory,
            frozen_generation_sha256=frozen,
            quiescence_evidence_sha256=evidence,
            writer_lease_id="wa-fi-fenced-writer-lease-1",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=100),
            authority_signer=signer,
        )
        verified = verify_physical_release_candidate_writer_quiescence_receipt(
            raw,
            config=config,
            source_root=self.fenced_writer_root,
            inventory_manifest_sha256=inventory,
            frozen_generation_sha256=frozen,
            quiescence_evidence_sha256=evidence,
            now=NOW,
        )
        binding = materializer.PhysicalWaFiPostgresFailbackWriterQuiescenceBinding(
            fenced_writer_root=self.fenced_writer_root,
            inventory_manifest_sha256=inventory,
            frozen_generation_sha256=frozen,
            quiescence_evidence_sha256=evidence,
        )
        return config, verified, binding

    def _recovery_evidence(self) -> PhysicalPostgresRecoveryReceiverReadbackEvidence:
        baseline = self.bundle.baseline
        objects = [baseline.base_backup_object]
        objects.extend(segment.object for manifest in self.bundle.wal_manifests for segment in manifest.segments)
        objects.extend(shard.object for shard in self.bundle.blob_frontier.inventory_shards)
        raw = canonical_json_bytes(
            {
                "schema": PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA,
                "status": PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
                "observed_at": NOW.isoformat(),
                "receiver_site": "webapp_fi",
                "source_site": "webapp_ir",
                "destination_site": "webapp_fi",
                "stage_bundle_id": self.bundle_id,
                "stage_receipt_sha256": self.stage_receipt_sha256,
                "route_binding_sha256": "8" * 64,
                "manifest_sha256es": list(self.bundle.manifest_sha256es),
                "object_versions": [
                    {"object_key": item.object_key, "version_id": item.version_id}
                    for item in objects
                ],
                "base_backup_manifest_sha256": baseline.manifest_sha256,
                "bundle_terminal_wal_lsn": self.bundle.terminal_wal_lsn,
                "writer_term": {
                    "holder_site": self.term.holder_site,
                    "writer_epoch": self.term.writer_epoch,
                    "writer_lease_id": self.term.writer_lease_id,
                    "witness_transition_id": self.term.witness_transition_id,
                    "witnessed_term_proof_sha256": self.term.proof_sha256,
                },
                "postgresql": {
                    "in_recovery": True,
                    "role": "standby",
                    "database_system_identifier": baseline.database_system_identifier,
                    "timeline_id": baseline.timeline_id,
                    "wal_segment_size_bytes": baseline.wal_segment_size_bytes,
                    "baseline_generation_id": baseline.baseline_generation_id,
                    "replay_lsn": self.bundle.terminal_wal_lsn,
                },
            }
        )
        return PhysicalPostgresRecoveryReceiverReadbackEvidence(
            raw_evidence=raw,
            evidence_sha256=_sha(raw),
        )

    def _config(self, **changes: object):
        values: dict[str, object] = {
            "preflight_config": self.preflight_config,
            "preflight": self.preflight,
            "writer_quiescence_config": self.quiescence_config,
            "writer_quiescence_receipt": self.quiescence_receipt,
            "writer_quiescence_binding": self.quiescence_binding,
            "source_stage_candidates_root": self.stage_root,
            "target_pgdata_candidates_root": self.target_root,
            "redacted_receipt_root": self.receipt_root,
            "runner_profile_sha256": "4" * 64,
            "enabled": True,
        }
        values.update(changes)
        return materializer.RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig(**values)

    def _run(self, config, runner):
        return materializer.run_root_owned_wa_fi_postgres_failback_materialization(
            config=config,
            bundle=self.bundle,
            pulled=self.pulled,
            current_witnessed_term=self.term,
            runner=runner,
            now=NOW,
        )

    def test_exact_reverse_pull_replays_only_detached_fi_standby(self) -> None:
        runner = _Runner(self)
        result = self._run(self._config(), runner)

        self.assertEqual(
            materializer.PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS,
            result.status,
        )
        self.assertEqual((), result.reason_codes)
        self.assertEqual(1, len(runner.calls))
        self.assertIsNotNone(result.recovery_preflight)
        self.assertEqual(
            PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
            result.recovery_preflight.status,
        )
        self.assertIsNotNone(result.durable_evidence)
        durable = result.durable_evidence
        assert durable is not None
        self.assertEqual(0o400, durable.receipt_path.stat().st_mode & 0o777)
        self.assertNotIn(str(self.source_candidate).encode(), durable.raw_receipt)
        self.assertNotIn(str(self.target_root).encode(), durable.raw_receipt)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.traffic_switch_authorized)
        self.assertFalse(result.full_matrix_authorized)

    def test_disabled_policy_blocks_before_runner(self) -> None:
        runner = _Runner(self)
        result = self._run(self._config(enabled=False), runner)
        self.assertEqual(("WA_FI_FAILBACK_MATERIALIZATION_DISABLED",), result.reason_codes)
        self.assertEqual([], runner.calls)

    def test_forged_runner_ack_blocks_before_durable_receipt(self) -> None:
        runner = _Runner(self, forged=True)
        result = self._run(self._config(), runner)
        self.assertEqual(("WA_FI_FAILBACK_MATERIALIZATION_RUNNER_ACK_INVALID",), result.reason_codes)
        self.assertEqual(1, len(runner.calls))
        self.assertFalse((self.receipt_root / "failback-replay-receipts").exists())

    def test_preexisting_target_blocks_before_runner_without_cleanup(self) -> None:
        stale = self.target_root / self.bundle_id
        stale.mkdir(mode=0o700)
        stale.chmod(0o700)
        runner = _Runner(self)

        result = self._run(self._config(), runner)

        self.assertEqual(("WA_FI_FAILBACK_MATERIALIZATION_TARGET_PREEXISTS",), result.reason_codes)
        self.assertEqual([], runner.calls)
        self.assertTrue(stale.is_dir())

    def test_prior_durable_receipt_blocks_before_runner(self) -> None:
        directory = self.receipt_root / "failback-replay-receipts"
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
        prior = directory / f"{self.bundle_id}.json"
        prior.write_bytes(b"prior-replay-receipt")
        prior.chmod(0o400)
        runner = _Runner(self)

        result = self._run(self._config(), runner)

        self.assertEqual(("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_PREEXISTS",), result.reason_codes)
        self.assertEqual([], runner.calls)
        self.assertEqual(b"prior-replay-receipt", prior.read_bytes())

    def test_source_cannot_reuse_normal_materializer_or_execution_surface(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_wa_fi_postgres_failback_materialization_runtime.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "physical_wa_ir_postgres_recovery_materialization_runtime",
            "physical_wa_fi_postgres_helper_capture_bridge",
            "physical_wa_fi_postgres_object_storage_handoff_runtime",
            "import subprocess",
            "from subprocess",
            "import socket",
            "from socket",
            "import docker",
            "from docker",
            "import boto3",
            "import requests",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
