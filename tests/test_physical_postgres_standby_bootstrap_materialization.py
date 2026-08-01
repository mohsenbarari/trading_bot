from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from unittest import TestCase

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_postgres_recovery_preflight import (
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryReceiverReadbackEvidence,
    PhysicalPostgresRecoveryStageBinding,
)
from core.physical_postgres_standby_bootstrap_materialization import (
    PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_DEFAULT_ENABLED,
    PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_RECEIPT_SCHEMA,
    PhysicalPostgresStandbyBootstrapMaterializationAck,
    PhysicalPostgresStandbyBootstrapMaterializationError,
    PhysicalPostgresStandbyBootstrapRootConfig,
    PhysicalPostgresStandbyBootstrapStageEvidence,
    materialize_physical_postgres_standby_bootstrap,
)
from tests import test_physical_postgres_recovery_preflight as recovery_preflight_tests


NOW = recovery_preflight_tests.NOW


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


class _Materializer:
    def __init__(
        self,
        *,
        failure: bool = False,
        mutate_term: object | None = None,
        race_root: Path | None = None,
        mutate_source: Path | None = None,
        mutate_seed: Path | None = None,
        write_target_then_fail: bool = False,
    ) -> None:
        self.failure = failure
        self.mutate_term = mutate_term
        self.race_root = race_root
        self.mutate_source = mutate_source
        self.mutate_seed = mutate_seed
        self.write_target_then_fail = write_target_then_fail
        self.calls: list[object] = []

    def materialize_standby_bootstrap(
        self,
        *,
        plan: object,
        source_stage_fd: int,
        target_pgdata_fd: int,
        recovery_signal_seed_fd: int,
    ) -> PhysicalPostgresStandbyBootstrapMaterializationAck:
        self.calls.append(plan)
        self.assert_safe_fds(source_stage_fd, target_pgdata_fd, recovery_signal_seed_fd)
        if self.race_root is not None:
            target = self.race_root / plan.bootstrap_id
            moved = self.race_root / (plan.bootstrap_id + "-replaced")
            os.rename(target, moved)
            target.mkdir(mode=0o700)
        if self.mutate_source is not None:
            moved = self.mutate_source.with_name(self.mutate_source.name + "-replaced")
            os.rename(self.mutate_source, moved)
            self.mutate_source.mkdir(mode=0o700)
        if self.mutate_seed is not None:
            self.mutate_seed.unlink()
            self.mutate_seed.write_bytes(b"")
            os.chmod(self.mutate_seed, 0o600)
        if self.mutate_term is not None:
            object.__setattr__(self.mutate_term, "writer_epoch", True)
        if self.write_target_then_fail:
            descriptor = os.open(
                "materializer-partial",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=target_pgdata_fd,
            )
            try:
                os.write(descriptor, b"do-not-recursively-delete")
            finally:
                os.close(descriptor)
        if self.failure:
            raise RuntimeError("injected materializer failure")
        return PhysicalPostgresStandbyBootstrapMaterializationAck(
            status="local-standby-bootstrap-materialized",
            plan_sha256=plan.plan_sha256,
            source_stage_device=plan.source_stage_device,
            source_stage_inode=plan.source_stage_inode,
            target_pgdata_device=plan.target_pgdata_device,
            target_pgdata_inode=plan.target_pgdata_inode,
            recovery_signal_seed_sha256=plan.recovery_signal_seed_sha256,
            materialized_at=NOW,
        )

    @staticmethod
    def assert_safe_fds(source_stage_fd: int, target_pgdata_fd: int, recovery_signal_seed_fd: int) -> None:
        for descriptor in (source_stage_fd, target_pgdata_fd):
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise AssertionError("directory FD expected")
        seed_metadata = os.fstat(recovery_signal_seed_fd)
        if not stat.S_ISREG(seed_metadata.st_mode) or seed_metadata.st_size != 0:
            raise AssertionError("empty regular seed FD expected")


class PhysicalPostgresStandbyBootstrapMaterializationTests(TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("root-owned boundary tests require the root-owned CI container")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_root = self.make_dir("source-candidates")
        self.pgdata_root = self.make_dir("pgdata-candidates")
        self.receipt_root = self.make_dir("receipts")
        self.failed_root = self.make_dir("failed")
        self.seed_root = self.make_dir("recovery-seed")
        self.seed = self.seed_root / "recovery.signal.seed"
        self.seed.write_bytes(b"")
        os.chmod(self.seed, 0o600)

        self.fixture = recovery_preflight_tests.PhysicalPostgresRecoveryPreflightTests(
            methodName="runTest"
        )
        self.fixture.setUp()
        self.bundle, self.term = self.fixture.bundle()
        self.route_binding_sha256 = sha("bootstrap-route-fi-ir")
        self.bundle_id = sha(
            canonical_json_bytes(
                {
                    "schema": "gold-trade-physical-wal-receiver-staging-v1",
                    "route_binding_sha256": self.route_binding_sha256,
                    "manifest_sha256es": list(self.bundle.manifest_sha256es),
                }
            )
        )
        self.stage_dir = self.source_root / self.bundle_id
        self.stage_dir.mkdir(mode=0o700)
        self.stage_receipt_raw, self.stage_receipt_sha256 = self.stage_receipt()
        stage_file = self.stage_dir / "stage-receipt.json"
        stage_file.write_bytes(self.stage_receipt_raw)
        # The real receiver stager freezes canonical receipts after it fsyncs
        # them, so the bootstrap must be compatible with that exact 0400 form.
        os.chmod(stage_file, 0o400)
        self.stage_binding = PhysicalPostgresRecoveryStageBinding(
            bundle_id=self.bundle_id,
            stage_receipt_sha256=self.stage_receipt_sha256,
            route_binding_sha256=self.route_binding_sha256,
        )
        self.binding = PhysicalPostgresRecoveryPreflightBinding(
            local_standby_site="webapp_ir",
            stage_binding=self.stage_binding,
            expected_witnessed_term=self.term,
        )
        recovery_raw = canonical_json_bytes(
            self.fixture.readback_payload(
                bundle=self.bundle,
                term=self.term,
                stage=self.stage_binding,
            )
        )
        self.recovery_evidence = PhysicalPostgresRecoveryReceiverReadbackEvidence(
            raw_evidence=recovery_raw,
            evidence_sha256=sha(recovery_raw),
        )
        self.config = PhysicalPostgresStandbyBootstrapRootConfig(
            enabled=True,
            source_staging_candidates_root=self.source_root,
            pgdata_candidates_root=self.pgdata_root,
            receipt_root=self.receipt_root,
            failed_candidates_root=self.failed_root,
            recovery_signal_seed_root=self.seed_root,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_dir(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        return path

    def object_versions(self) -> list[dict[str, str]]:
        pairs = [
            (
                self.bundle.baseline.base_backup_object.object_key,
                self.bundle.baseline.base_backup_object.version_id,
            )
        ]
        for manifest in self.bundle.wal_manifests:
            pairs.extend((segment.object.object_key, segment.object.version_id) for segment in manifest.segments)
        pairs.extend(
            (shard.object.object_key, shard.object.version_id)
            for shard in self.bundle.blob_frontier.inventory_shards
        )
        return [{"object_key": key, "version_id": version} for key, version in pairs]

    def artifacts(self) -> list[dict[str, object]]:
        base = self.bundle.baseline.base_backup_object
        entries: list[dict[str, object]] = [
            {
                "artifact_id": "base-backup",
                "kind": "base-backup",
                "object_key": base.object_key,
                "version_id": base.version_id,
                "ciphertext_relative_path": "material/base-backup.age",
                "ciphertext_sha256": base.ciphertext_sha256,
                "ciphertext_bytes": base.ciphertext_bytes,
                "plaintext_relative_path": "material/base-backup.plain",
                "plaintext_sha256": "1" * 64,
                "plaintext_bytes": 1,
                "wal_segment_name": None,
                "wal_start_lsn": None,
                "wal_end_lsn": None,
            }
        ]
        for manifest in self.bundle.wal_manifests:
            for segment in manifest.segments:
                entries.append(
                    {
                        "artifact_id": "wal-" + segment.wal_segment_name,
                        "kind": "wal",
                        "object_key": segment.object.object_key,
                        "version_id": segment.object.version_id,
                        "ciphertext_relative_path": "material/wal/" + segment.wal_segment_name + ".age",
                        "ciphertext_sha256": segment.object.ciphertext_sha256,
                        "ciphertext_bytes": segment.object.ciphertext_bytes,
                        "plaintext_relative_path": "material/wal/" + segment.wal_segment_name,
                        "plaintext_sha256": "2" * 64,
                        "plaintext_bytes": self.bundle.baseline.wal_segment_size_bytes,
                        "wal_segment_name": segment.wal_segment_name,
                        "wal_start_lsn": segment.start_lsn,
                        "wal_end_lsn": segment.end_lsn,
                    }
                )
        for shard in self.bundle.blob_frontier.inventory_shards:
            ordinal = f"{shard.ordinal:08d}"
            entries.append(
                {
                    "artifact_id": "blob-inventory-" + ordinal,
                    "kind": "blob-inventory",
                    "object_key": shard.object.object_key,
                    "version_id": shard.object.version_id,
                    "ciphertext_relative_path": "material/blob-inventory/" + ordinal + ".age",
                    "ciphertext_sha256": shard.object.ciphertext_sha256,
                    "ciphertext_bytes": shard.object.ciphertext_bytes,
                    "plaintext_relative_path": "material/blob-inventory/" + ordinal + ".inventory",
                    "plaintext_sha256": shard.plaintext_sha256,
                    "plaintext_bytes": shard.plaintext_bytes,
                    "wal_segment_name": None,
                    "wal_start_lsn": None,
                    "wal_end_lsn": None,
                }
            )
        return entries

    def stage_receipt(self) -> tuple[bytes, str]:
        unsigned = {
            "schema": "gold-trade-physical-wal-receiver-stage-receipt-v1",
            "status": "staged-not-replay-verified",
            "bundle_id": self.bundle_id,
            "route_binding_sha256": self.route_binding_sha256,
            "candidate_path": str(self.stage_dir),
            "manifest_sha256es": list(self.bundle.manifest_sha256es),
            "object_versions": self.object_versions(),
            "artifacts": self.artifacts(),
        }
        receipt_sha256 = sha(canonical_json_bytes(unsigned))
        return canonical_json_bytes({**unsigned, "receipt_sha256": receipt_sha256}), receipt_sha256

    def stage_evidence(self, **overrides: object) -> PhysicalPostgresStandbyBootstrapStageEvidence:
        values: dict[str, object] = {
            "source_candidate": self.stage_dir,
            "raw_stage_receipt": self.stage_receipt_raw,
            "stage_receipt_sha256": self.stage_receipt_sha256,
        }
        values.update(overrides)
        return PhysicalPostgresStandbyBootstrapStageEvidence(**values)

    def materialize(self, materializer: object, **overrides: object):
        values: dict[str, object] = {
            "root_config": self.config,
            "bundle": self.bundle,
            "binding": self.binding,
            "current_witnessed_term": self.term,
            "recovery_readback_evidence": self.recovery_evidence,
            "stage_evidence": self.stage_evidence(),
            "materializer": materializer,
            "now": NOW,
        }
        values.update(overrides)
        return materialize_physical_postgres_standby_bootstrap(**values)

    def test_materializes_only_a_detached_candidate_and_emits_canonical_non_authorizing_plan_receipt(self) -> None:
        materializer = _Materializer()
        result = self.materialize(materializer)

        self.assertFalse(PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_DEFAULT_ENABLED)
        self.assertEqual(1, len(materializer.calls))
        self.assertTrue(result.target_pgdata_candidate.is_dir())
        self.assertTrue(result.target_pgdata_candidate.parent.samefile(self.pgdata_root))
        self.assertFalse((result.target_pgdata_candidate / "recovery.signal").exists())
        self.assertFalse((result.target_pgdata_candidate / "PG_VERSION").exists())
        self.assertFalse(result.idempotent)
        plan = json.loads(result.plan.canonical_plan)
        receipt = json.loads(result.receipt.raw_receipt)
        self.assertEqual("webapp_fi", plan["source_site"])
        self.assertEqual("webapp_ir", plan["receiver_site"])
        self.assertEqual("standby", plan["receiver_role"])
        self.assertEqual(self.bundle.terminal_wal_lsn, plan["terminal_wal_lsn"])
        self.assertEqual(self.term.proof_sha256, plan["writer_term"]["witnessed_term_proof_sha256"])
        self.assertEqual(canonical_json_bytes(plan), result.plan.canonical_plan)
        self.assertEqual(sha(result.plan.canonical_plan), result.plan.plan_sha256)
        self.assertEqual(PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_RECEIPT_SCHEMA, receipt["schema"])
        self.assertEqual("local-standby-bootstrap-materialized", receipt["status"])
        self.assertEqual(result.plan.plan_sha256, receipt["plan_sha256"])
        self.assertEqual(canonical_json_bytes(receipt), result.receipt.raw_receipt)
        self.assertEqual(sha(result.receipt.raw_receipt), result.receipt.receipt_sha256)

    def test_same_exact_bootstrap_is_idempotent_without_a_second_materializer_call(self) -> None:
        first = _Materializer()
        initial = self.materialize(first)
        second = _Materializer()
        repeated = self.materialize(second)

        self.assertEqual(1, len(first.calls))
        self.assertEqual(0, len(second.calls))
        self.assertTrue(repeated.idempotent)
        self.assertEqual(initial.plan.plan_sha256, repeated.plan.plan_sha256)
        self.assertEqual(initial.receipt.receipt_sha256, repeated.receipt.receipt_sha256)

    def test_invalid_config_bundle_stage_term_recovery_and_target_never_call_materializer(self) -> None:
        other_term = self.fixture.witnessed_term(holder_site="webapp_fi", epoch=8)
        forged_bundle = replace(self.bundle, terminal_wal_lsn="0/4000000")
        object.__setattr__(forged_bundle, "_capability", self.bundle._capability)
        bad_raw = self.stage_receipt_raw[:-1] + b"\n"
        bad_recovery = replace(self.recovery_evidence, evidence_sha256="f" * 64)
        cases = (
            ({"root_config": PhysicalPostgresStandbyBootstrapRootConfig()}, "BOOTSTRAP_DISABLED"),
            ({"bundle": forged_bundle}, "BOOTSTRAP_BUNDLE_UNVERIFIED"),
            ({"stage_evidence": self.stage_evidence(raw_stage_receipt=bad_raw)}, "BOOTSTRAP_STAGE_RECEIPT_READBACK_MISMATCH"),
            ({"current_witnessed_term": other_term}, "BOOTSTRAP_CURRENT_TERM_MISMATCH"),
            ({"recovery_readback_evidence": bad_recovery}, "BOOTSTRAP_RECOVERY_EVIDENCE_INVALID"),
            ({"root_config": replace(self.config, pgdata_candidates_root=self.source_root)}, "BOOTSTRAP_ROOTS_OVERLAP"),
        )
        for overrides, code in cases:
            with self.subTest(code=code):
                materializer = _Materializer()
                with self.assertRaisesRegex(PhysicalPostgresStandbyBootstrapMaterializationError, code):
                    self.materialize(materializer, **overrides)
                self.assertEqual([], materializer.calls)

    def test_source_symlink_fails_before_materializer(self) -> None:
        moved_stage = self.source_root / (self.bundle_id + "-original")
        os.rename(self.stage_dir, moved_stage)
        self.stage_dir.symlink_to(moved_stage, target_is_directory=True)
        materializer = _Materializer()
        with self.assertRaisesRegex(
            PhysicalPostgresStandbyBootstrapMaterializationError,
            "BOOTSTRAP_STAGE_SOURCE_UNSAFE",
        ):
            self.materialize(materializer)
        self.assertEqual([], materializer.calls)

    def test_reused_nonempty_target_fails_before_materializer(self) -> None:
        initial = self.materialize(_Materializer())
        receipt_path = self.receipt_root / (initial.plan.bootstrap_id + ".json")
        receipt_path.unlink()
        (initial.target_pgdata_candidate / "unexpected").write_bytes(b"reused")
        materializer = _Materializer()
        with self.assertRaisesRegex(
            PhysicalPostgresStandbyBootstrapMaterializationError,
            "BOOTSTRAP_PGDATA_TARGET_REUSED",
        ):
            self.materialize(materializer)
        self.assertEqual([], materializer.calls)

    def test_materializer_failure_cleans_an_empty_new_target_without_recursive_deletion(self) -> None:
        materializer = _Materializer(failure=True)
        with self.assertRaisesRegex(
            PhysicalPostgresStandbyBootstrapMaterializationError,
            "BOOTSTRAP_MATERIALIZER_FAILED",
        ):
            self.materialize(materializer)
        self.assertEqual(1, len(materializer.calls))
        self.assertEqual([], list(self.pgdata_root.iterdir()))
        self.assertEqual([], list(self.failed_root.iterdir()))

    def test_materializer_failure_preserves_a_nonempty_candidate_in_failed_root(self) -> None:
        materializer = _Materializer(failure=True, write_target_then_fail=True)
        with self.assertRaisesRegex(
            PhysicalPostgresStandbyBootstrapMaterializationError,
            "BOOTSTRAP_MATERIALIZER_FAILED",
        ):
            self.materialize(materializer)

        self.assertEqual(1, len(materializer.calls))
        self.assertEqual([], list(self.pgdata_root.iterdir()))
        quarantined = list(self.failed_root.iterdir())
        self.assertEqual(1, len(quarantined))
        self.assertTrue((quarantined[0] / "materializer-partial").is_file())
        self.assertEqual(b"do-not-recursively-delete", (quarantined[0] / "materializer-partial").read_bytes())

    def test_target_race_after_materializer_is_detected_and_never_receipted(self) -> None:
        race = _Materializer(race_root=self.pgdata_root)
        with self.assertRaisesRegex(
            PhysicalPostgresStandbyBootstrapMaterializationError,
            "BOOTSTRAP_PGDATA_TARGET_RACE_DETECTED",
        ):
            self.materialize(race)
        self.assertEqual(1, len(race.calls))
        self.assertEqual([], list(self.receipt_root.iterdir()))

    def test_term_change_after_materializer_is_detected_and_never_receipted(self) -> None:
        term_change = _Materializer(mutate_term=self.term)
        with self.assertRaisesRegex(
            PhysicalPostgresStandbyBootstrapMaterializationError,
            "BOOTSTRAP_CURRENT_TERM_CHANGED",
        ):
            self.materialize(term_change)
        self.assertEqual(1, len(term_change.calls))
        self.assertEqual([], list(self.receipt_root.iterdir()))

    def test_source_inode_race_after_materializer_is_detected(self) -> None:
        source_race = _Materializer(mutate_source=self.stage_dir)
        with self.assertRaisesRegex(
            PhysicalPostgresStandbyBootstrapMaterializationError,
            "BOOTSTRAP_SOURCE_STAGE_RACE_DETECTED",
        ):
            self.materialize(source_race)
        self.assertEqual(1, len(source_race.calls))
        self.assertEqual([], list(self.receipt_root.iterdir()))

    def test_recovery_signal_seed_inode_race_after_materializer_is_detected(self) -> None:
        seed_race = _Materializer(mutate_seed=self.seed)
        with self.assertRaisesRegex(
            PhysicalPostgresStandbyBootstrapMaterializationError,
            "BOOTSTRAP_RECOVERY_SIGNAL_RACE_DETECTED",
        ):
            self.materialize(seed_race)
        self.assertEqual(1, len(seed_race.calls))
        self.assertEqual([], list(self.receipt_root.iterdir()))

    def test_materializer_surface_has_only_fixed_fds_and_no_runtime_execution_client_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_postgres_standby_bootstrap_materialization.py"
        ).read_text(encoding="utf-8")
        forbidden = (
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
