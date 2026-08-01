"""Adversarial tests for the non-executing WA-IR phase-3 FD binder."""

from __future__ import annotations

from dataclasses import replace
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_postgres_standby_bootstrap_materialization as bootstrap
from core import physical_wa_ir_postgres_recovery_fd_boundary as boundary
from core import physical_wa_ir_postgres_recovery_materialization_runtime as runtime
from core.physical_postgres_standby_bootstrap_materialization import (
    PhysicalPostgresStandbyBootstrapMaterializationAck,
)
from tests import test_physical_postgres_standby_bootstrap_materialization as bootstrap_tests


def sha(value: bytes | str) -> str:
    if type(value) is str:
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


class PhysicalWaIrPostgresRecoveryFdBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("the root-only FD binder requires the root-owned CI container")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self._directory("source")
        self.target = self._directory("target")
        self.seed = self.root / "recovery.signal.seed"
        self.seed.write_bytes(b"")
        os.chmod(self.seed, 0o600)
        self.route = sha("route")
        self.bundle = sha("bundle")
        self.manifest = sha("manifest")
        self.version = "v1"
        self.object_key = "normal/base-backup.age"
        self.stage_receipt = self._stage_receipt()
        stage_file = self.source / "stage-receipt.json"
        stage_file.write_bytes(self.stage_receipt)
        os.chmod(stage_file, 0o400)
        self.plan = self._plan()
        self.invocation = self._invocation(self.plan)
        self.source_fd = self._open_directory(self.source)
        self.target_fd = self._open_directory(self.target)
        self.seed_fd = os.open(self.seed, os.O_RDONLY | os.O_NOFOLLOW)

    def tearDown(self) -> None:
        for descriptor in (getattr(self, "seed_fd", -1), getattr(self, "target_fd", -1), getattr(self, "source_fd", -1)):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self.temp.cleanup()

    def _directory(self, name: str) -> Path:
        result = self.root / name
        result.mkdir(mode=0o700)
        os.chmod(result, 0o700)
        return result

    @staticmethod
    def _open_directory(path: Path) -> int:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def _stage_receipt(self) -> bytes:
        unsigned = {
            "schema": "gold-trade-physical-wal-receiver-stage-receipt-v1",
            "status": "staged-not-replay-verified",
            "bundle_id": self.bundle,
            "route_binding_sha256": self.route,
            "candidate_path": "/not-used-by-fd-boundary",
            "manifest_sha256es": [self.manifest],
            "object_versions": [{"object_key": self.object_key, "version_id": self.version}],
            "artifacts": [{"opaque": "validated-by-bootstrap-before-this-binder"}],
        }
        return canonical_json_bytes(
            {**unsigned, "receipt_sha256": sha(canonical_json_bytes(unsigned))}
        )

    def _plan(self) -> bootstrap.PhysicalPostgresStandbyBootstrapMaterializationPlan:
        source_stat = os.stat(self.source)
        target_stat = os.stat(self.target)
        writer_term = {
            "holder_site": "webapp_fi",
            "writer_epoch": 7,
            "writer_lease_id": "lease-fi-7",
            "witness_transition_id": "transition-fi-7",
            "witnessed_term_proof_sha256": sha("term"),
        }
        payload = {
            "schema": bootstrap.PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_PLAN_SCHEMA,
            "kind": "local_standby_bootstrap_materialization_intent",
            "bootstrap_id": sha("bootstrap"),
            "source_site": "webapp_fi",
            "receiver_site": "webapp_ir",
            "receiver_role": "standby",
            "bundle_id": self.bundle,
            "stage_receipt_sha256": json.loads(self.stage_receipt)["receipt_sha256"],
            "route_binding_sha256": self.route,
            "manifest_sha256es": [self.manifest],
            "object_versions": [{"object_key": self.object_key, "version_id": self.version}],
            "terminal_wal_lsn": "0/1",
            "writer_term": writer_term,
            "recovery_evidence_sha256": sha("recovery-evidence"),
            "source_stage_device": source_stat.st_dev,
            "source_stage_inode": source_stat.st_ino,
            "target_pgdata_device": target_stat.st_dev,
            "target_pgdata_inode": target_stat.st_ino,
            "recovery_signal_seed_sha256": sha(b""),
        }
        raw = canonical_json_bytes(payload)
        return bootstrap.PhysicalPostgresStandbyBootstrapMaterializationPlan(
            canonical_plan=raw,
            plan_sha256=sha(raw),
            bootstrap_id=payload["bootstrap_id"],
            source_site=payload["source_site"],
            receiver_site=payload["receiver_site"],
            bundle_id=payload["bundle_id"],
            stage_receipt_sha256=payload["stage_receipt_sha256"],
            route_binding_sha256=payload["route_binding_sha256"],
            terminal_wal_lsn=payload["terminal_wal_lsn"],
            writer_epoch=writer_term["writer_epoch"],
            writer_lease_id=writer_term["writer_lease_id"],
            witnessed_term_proof_sha256=writer_term["witnessed_term_proof_sha256"],
            source_stage_device=payload["source_stage_device"],
            source_stage_inode=payload["source_stage_inode"],
            target_pgdata_device=payload["target_pgdata_device"],
            target_pgdata_inode=payload["target_pgdata_inode"],
            recovery_signal_seed_sha256=payload["recovery_signal_seed_sha256"],
        )

    def _invocation(
        self,
        plan: bootstrap.PhysicalPostgresStandbyBootstrapMaterializationPlan,
        *,
        route_binding_sha256: str | None = None,
    ) -> runtime.PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation:
        route = self.route if route_binding_sha256 is None else route_binding_sha256
        rendered_payload = {
            "schema": runtime.PHYSICAL_WA_IR_POSTGRES_SOCKET_ONLY_RECOVERY_INPUT_SCHEMA,
            "status": "default-off-socket-only-recovery-input",
            "campaign_id": "campaign-2026",
            "release_sha": "a" * 40,
            "sealed_release_descriptor_sha256": sha("seal"),
            "deployment_manifest_lock_sha256": sha("lock"),
            "route_binding_sha256": route,
            "postgres_image": "registry.example/gold-trade/postgres@sha256:" + sha("postgres"),
            "postgres_major": 15,
            "network_mode": "none",
            "tcp_listener": "disabled",
            "unix_socket_directory": "/var/run/postgresql",
            "unix_socket_port": 5432,
            "socket_authentication": "peer-local-only",
            "recovery_mode": "standby-replay-only",
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
            "promotion_authorized": False,
            "full_matrix_authorized": False,
        }
        rendered_raw = canonical_json_bytes(rendered_payload)
        rendered = runtime.PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs(
            canonical_input=rendered_raw,
            input_sha256=sha(rendered_raw),
            campaign_id=rendered_payload["campaign_id"],
            release_sha=rendered_payload["release_sha"],
            route_binding_sha256=rendered_payload["route_binding_sha256"],
            deployment_manifest_lock_sha256=rendered_payload["deployment_manifest_lock_sha256"],
            sealed_release_descriptor_sha256=rendered_payload["sealed_release_descriptor_sha256"],
            postgres_image=rendered_payload["postgres_image"],
        )
        plan_payload = json.loads(plan.canonical_plan)
        payload = {
            "schema": runtime.PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
            "kind": "socket-only-standby-materialization",
            "socket_only_recovery_input_sha256": rendered.input_sha256,
            "bootstrap_id": plan.bootstrap_id,
            "bootstrap_plan_sha256": plan.plan_sha256,
            "source_site": plan.source_site,
            "receiver_site": plan.receiver_site,
            "writer_epoch": plan.writer_epoch,
            "writer_lease_id": plan.writer_lease_id,
            "witness_transition_id": plan_payload["writer_term"]["witness_transition_id"],
            "witnessed_term_proof_sha256": plan.witnessed_term_proof_sha256,
        }
        return runtime.PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation(
            schema=runtime.PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
            rendered_inputs=rendered,
            bootstrap_id=plan.bootstrap_id,
            bootstrap_plan_sha256=plan.plan_sha256,
            source_site=plan.source_site,
            receiver_site=plan.receiver_site,
            writer_epoch=plan.writer_epoch,
            writer_lease_id=plan.writer_lease_id,
            witness_transition_id=payload["witness_transition_id"],
            witnessed_term_proof_sha256=plan.witnessed_term_proof_sha256,
            invocation_sha256=sha(canonical_json_bytes(payload)),
        )

    def _bind(self, **overrides: object):
        values: dict[str, object] = {
            "invocation": self.invocation,
            "plan": self.plan,
            "source_stage_fd": self.source_fd,
            "target_pgdata_fd": self.target_fd,
            "recovery_signal_seed_fd": self.seed_fd,
        }
        values.update(overrides)
        return boundary.bind_wa_ir_postgres_socket_only_recovery_materialization_fds(**values)

    @staticmethod
    def _close_bound(value: boundary.PhysicalWaIrPostgresRecoveryBoundMaterializationFds) -> None:
        for descriptor in (value.recovery_signal_seed_fd, value.target_pgdata_fd, value.source_stage_fd):
            os.close(descriptor)

    def test_valid_input_returns_only_independent_noninheritable_local_fds(self) -> None:
        bound = self._bind()
        self.addCleanup(self._close_bound, bound)
        self.assertEqual(boundary.PHYSICAL_WA_IR_POSTGRES_RECOVERY_FD_BOUNDARY_SCHEMA, bound.schema)
        self.assertEqual(self.plan.plan_sha256, bound.bootstrap_plan_sha256)
        self.assertEqual(self.invocation.invocation_sha256, bound.invocation_sha256)
        for original, duplicate in (
            (self.source_fd, bound.source_stage_fd),
            (self.target_fd, bound.target_pgdata_fd),
            (self.seed_fd, bound.recovery_signal_seed_fd),
        ):
            self.assertNotEqual(original, duplicate)
            self.assertFalse(os.get_inheritable(duplicate))
            original_stat = os.fstat(original)
            duplicate_stat = os.fstat(duplicate)
            self.assertEqual((original_stat.st_dev, original_stat.st_ino), (duplicate_stat.st_dev, duplicate_stat.st_ino))
        self.assertTrue(stat.S_ISDIR(os.fstat(bound.source_stage_fd).st_mode))
        self.assertTrue(stat.S_ISDIR(os.fstat(bound.target_pgdata_fd).st_mode))
        self.assertTrue(stat.S_ISREG(os.fstat(bound.recovery_signal_seed_fd).st_mode))

    def test_accepts_the_actual_prevalidated_bootstrap_fd_boundary(self) -> None:
        fixture = bootstrap_tests.PhysicalPostgresStandbyBootstrapMaterializationTests(
            methodName="runTest"
        )
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        owner = self

        class BinderMaterializer:
            def materialize_standby_bootstrap(
                self,
                *,
                plan: bootstrap.PhysicalPostgresStandbyBootstrapMaterializationPlan,
                source_stage_fd: int,
                target_pgdata_fd: int,
                recovery_signal_seed_fd: int,
            ) -> PhysicalPostgresStandbyBootstrapMaterializationAck:
                invocation = owner._invocation(
                    plan,
                    route_binding_sha256=plan.route_binding_sha256,
                )
                bound = boundary.bind_wa_ir_postgres_socket_only_recovery_materialization_fds(
                    invocation=invocation,
                    plan=plan,
                    source_stage_fd=source_stage_fd,
                    target_pgdata_fd=target_pgdata_fd,
                    recovery_signal_seed_fd=recovery_signal_seed_fd,
                )
                try:
                    self.assert_bound(bound)
                finally:
                    owner._close_bound(bound)
                return PhysicalPostgresStandbyBootstrapMaterializationAck(
                    status="local-standby-bootstrap-materialized",
                    plan_sha256=plan.plan_sha256,
                    source_stage_device=plan.source_stage_device,
                    source_stage_inode=plan.source_stage_inode,
                    target_pgdata_device=plan.target_pgdata_device,
                    target_pgdata_inode=plan.target_pgdata_inode,
                    recovery_signal_seed_sha256=plan.recovery_signal_seed_sha256,
                    materialized_at=bootstrap_tests.NOW,
                )

            @staticmethod
            def assert_bound(
                bound: boundary.PhysicalWaIrPostgresRecoveryBoundMaterializationFds,
            ) -> None:
                if not all(
                    stat.S_ISDIR(os.fstat(descriptor).st_mode)
                    for descriptor in (bound.source_stage_fd, bound.target_pgdata_fd)
                ):
                    raise AssertionError("expected only bound directory descriptors")
                if not stat.S_ISREG(os.fstat(bound.recovery_signal_seed_fd).st_mode):
                    raise AssertionError("expected only the bound seed descriptor")

        result = fixture.materialize(BinderMaterializer())
        self.assertFalse(result.idempotent)
        self.assertTrue(result.target_pgdata_candidate.is_dir())

    def test_tampered_plan_or_invocation_is_rejected_before_a_bound_fd_exists(self) -> None:
        cases = (
            {"plan": replace(self.plan, canonical_plan=self.plan.canonical_plan + b"\n")},
            {"invocation": replace(self.invocation, invocation_sha256="f" * 64)},
            {"invocation": replace(self.invocation, receiver_site="webapp_fi")},
            {"invocation": self._invocation(self.plan, route_binding_sha256=sha("other-route"))},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError,
                    "WA_IR_RECOVERY_FD_BOUNDARY_",
                ):
                    self._bind(**changes)

    def test_descriptor_alias_swap_nonempty_target_and_seed_mode_are_rejected(self) -> None:
        with self.assertRaisesRegex(boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError, "DESCRIPTOR_ALIAS"):
            self._bind(target_pgdata_fd=self.source_fd)
        wrong_target_fd = self._open_directory(self.source)
        try:
            with self.assertRaisesRegex(boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError, "TARGET_UNSAFE"):
                self._bind(target_pgdata_fd=wrong_target_fd)
        finally:
            os.close(wrong_target_fd)

        marker = os.open("unexpected", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self.target_fd)
        os.close(marker)
        with self.assertRaisesRegex(boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError, "TARGET_UNSAFE"):
            self._bind()
        os.unlink(self.target / "unexpected")

        os.fchmod(self.seed_fd, 0o644)
        with self.assertRaisesRegex(boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError, "SEED_UNSAFE"):
            self._bind()

    def test_frozen_stage_receipt_and_root_requirement_are_enforced(self) -> None:
        os.chmod(self.source / "stage-receipt.json", 0o600)
        with self.assertRaisesRegex(boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError, "STAGE_RECEIPT_UNSAFE"):
            self._bind()
        os.chmod(self.source / "stage-receipt.json", 0o400)
        with patch.object(boundary.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError, "ROOT_REQUIRED"):
                self._bind()

    def test_stage_receipt_binding_and_single_link_seed_are_enforced(self) -> None:
        stage_file = self.source / "stage-receipt.json"
        os.chmod(stage_file, 0o600)
        stage_file.write_bytes(self.stage_receipt.replace(b"staged-not-replay-verified", b"staged-not-replay-observed"))
        os.chmod(stage_file, 0o400)
        with self.assertRaisesRegex(boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError, "STAGE_RECEIPT_INVALID"):
            self._bind()

        stage_file.write_bytes(self.stage_receipt)
        os.chmod(stage_file, 0o400)
        seed_link = self.root / "recovery.signal.seed.link"
        os.link(self.seed, seed_link)
        with self.assertRaisesRegex(boundary.PhysicalWaIrPostgresRecoveryFdBoundaryError, "SEED_UNSAFE"):
            self._bind()

    def test_surface_has_no_runner_execution_network_or_path_input(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_wa_ir_postgres_recovery_fd_boundary.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imported
            & {"boto3", "botocore", "docker", "http", "httpx", "paramiko", "requests", "socket", "subprocess", "urllib"}
        )
        self.assertNotIn("Path", source)
        self.assertNotIn("/proc", source)
        self.assertNotIn("def materialize_socket_only_standby", source)
        self.assertNotIn("def inspect_socket_only_standby", source)


if __name__ == "__main__":
    unittest.main()
