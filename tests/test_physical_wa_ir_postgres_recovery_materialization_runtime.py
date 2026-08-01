"""Focused local-only tests for the WA-IR phase-3 recovery runtime.

The runner below is an FD-only fake.  No test imports Docker, starts
PostgreSQL, opens a socket, contacts Object Storage, or makes an FI-to-IR
connection.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from core import physical_release_seal_admission as seal
from core import physical_wa_ir_postgres_recovery_materialization_runtime as runtime
from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_postgres_recovery_preflight import (
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryReceiverReadbackEvidence,
)
from core.physical_postgres_recovery_readback_collector import (
    PhysicalPostgresRecoveryLocalInspection,
    PhysicalPostgresRecoveryReadbackRootConfig,
)
from core.physical_postgres_standby_bootstrap_materialization import (
    PhysicalPostgresStandbyBootstrapMaterializationAck,
)
from core.physical_wa_ir_postgres_recovery_pull_runtime import (
    PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA,
    PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED,
    PhysicalWaIrPostgresRecoveryPullRedactedReceipt,
    PhysicalWaIrPostgresRecoveryPullResult,
)
from tests import test_physical_postgres_standby_bootstrap_materialization as bootstrap_tests


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


class _SealFilesystemInspector:
    def inspect_worktree(self, *, worktree: Path) -> seal.PhysicalReleaseSealWorktreeInspection:
        def item(path: Path, *, mode: int, regular: bool, directory: bool, executable: bool):
            return seal.PhysicalReleaseSealFilesystemObject(
                path=path,
                owner_uid=0,
                mode=mode,
                regular_file=regular,
                directory=directory,
                symlink=False,
                executable=executable,
                ancestors_root_controlled=True,
                device=1,
                inode={
                    worktree: 101,
                    worktree / ".git": 102,
                    seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY: 103,
                }[path],
                ctime_ns=1_000_000_000,
                mtime_ns=1_000_000_000,
            )

        return seal.PhysicalReleaseSealWorktreeInspection(
            worktree=item(worktree, mode=0o750, regular=False, directory=True, executable=True),
            git_metadata=item(worktree / ".git", mode=0o700, regular=False, directory=True, executable=True),
            git_binary=item(seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY, mode=0o755, regular=True, directory=False, executable=True),
        )


class _SealGitRunner:
    def __init__(self, *, release: str) -> None:
        self.release = release
        self.heads = [release, release]

    def run(self, *, invocation: seal.PhysicalReleaseSealGitInvocation):
        arguments = invocation.arguments[3:]
        if arguments == ("rev-parse", "--verify", "HEAD^{commit}"):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=(self.heads.pop(0) + "\n").encode("ascii"),
            )
        if arguments == (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        ):
            return seal.PhysicalReleaseSealGitCommandResult(exit_code=0, stdout_bytes=b"")
        if arguments == ("rev-parse", "--verify", self.release + "^{tree}"):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=("b" * 40 + "\n").encode("ascii"),
            )
        if arguments == ("ls-tree", "-r", "-z", "--full-tree", self.release):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=(
                    b"100644 blob " + b"c" * 40 + b"\tREADME.md\0"
                    b"100755 blob " + b"d" * 40 + b"\tscripts/recovery.sh\0"
                ),
            )
        raise AssertionError(arguments)


def sealed_release(*, campaign: str, release: str) -> tuple[seal.SealedPhysicalReleaseDescriptor, str]:
    images = tuple(
        seal.PhysicalReleaseSealImage(
            role=role,
            reference=(
                "registry.example:5000/gold-trade/"
                + role
                + "@sha256:"
                + sha("image:" + role)
            ),
        )
        for role in seal.REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES
    )
    postgres_image = next(item.reference for item in images if item.role == "postgres_15")
    config = seal.PhysicalReleaseSealAdmissionConfig(
        worktree=Path("/srv/trading-bot-three-site/phase3-seal"),
        campaign_id=campaign,
        expected_release_sha=release,
        images=images,
        seal_id=UUID("bf4de196-1b52-4c56-82d4-97bb0e3e799d"),
        sealed_at=NOW - timedelta(seconds=1),
        enabled=True,
        maximum_freshness_seconds=180,
    )
    with patch.object(seal.os, "geteuid", return_value=0):
        descriptor = seal.admit_physical_release_seal(
            config=config,
            filesystem_inspector=_SealFilesystemInspector(),
            git_runner=_SealGitRunner(release=release),
            now=NOW,
        )
    return descriptor, postgres_image


class _Runner:
    def __init__(
        self,
        *,
        inspection_overrides: dict[str, object] | None = None,
        mutate_term: object | None = None,
    ) -> None:
        self.inspection_overrides = inspection_overrides or {}
        self.mutate_term = mutate_term
        self.materialization_calls: list[object] = []
        self.inspection_calls: list[object] = []

    def materialize_socket_only_standby(
        self,
        *,
        invocation: runtime.PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation,
        plan: object,
        source_stage_fd: int,
        target_pgdata_fd: int,
        recovery_signal_seed_fd: int,
    ) -> PhysicalPostgresStandbyBootstrapMaterializationAck:
        self.materialization_calls.append(invocation)
        self.assert_safe_fds(source_stage_fd, target_pgdata_fd, recovery_signal_seed_fd)
        rendered = json.loads(invocation.rendered_inputs.canonical_input)
        if rendered["network_mode"] != "none":
            raise AssertionError("runner invocation must remain network-isolated")
        if rendered["unix_socket_directory"] != "/var/run/postgresql":
            raise AssertionError("runner invocation must use the fixed Unix socket")
        if self.mutate_term is not None:
            object.__setattr__(self.mutate_term, "writer_epoch", True)
        marker = os.open("PG_VERSION", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=target_pgdata_fd)
        try:
            os.write(marker, b"15\n")
        finally:
            os.close(marker)
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

    def inspect_socket_only_standby(
        self,
        *,
        invocation: runtime.PhysicalWaIrPostgresSocketOnlyRecoveryInspectionInvocation,
        target_pgdata_fd: int,
        request: object,
    ) -> PhysicalPostgresRecoveryLocalInspection:
        self.inspection_calls.append(invocation)
        metadata = os.fstat(target_pgdata_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            raise AssertionError("expected only detached PGDATA FD")
        values: dict[str, object] = {
            "observed_at": NOW,
            "receiver_site": request.receiver_site,
            "source_site": request.source_site,
            "destination_site": request.destination_site,
            "stage_bundle_id": request.stage_bundle_id,
            "stage_receipt_sha256": request.stage_receipt_sha256,
            "route_binding_sha256": request.route_binding_sha256,
            "bundle_terminal_wal_lsn": request.bundle_terminal_wal_lsn,
            "writer_holder_site": request.writer_holder_site,
            "writer_epoch": request.writer_epoch,
            "writer_lease_id": request.writer_lease_id,
            "witness_transition_id": request.witness_transition_id,
            "witnessed_term_proof_sha256": request.witnessed_term_proof_sha256,
            "in_recovery": True,
            "role": "standby",
            "database_system_identifier": request.database_system_identifier,
            "timeline_id": request.timeline_id,
            "wal_segment_size_bytes": request.wal_segment_size_bytes,
            "baseline_generation_id": request.baseline_generation_id,
            "replay_lsn": request.bundle_terminal_wal_lsn,
        }
        values.update(self.inspection_overrides)
        return PhysicalPostgresRecoveryLocalInspection(**values)

    @staticmethod
    def assert_safe_fds(source_stage_fd: int, target_pgdata_fd: int, recovery_signal_seed_fd: int) -> None:
        for descriptor in (source_stage_fd, target_pgdata_fd):
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise AssertionError("directory FD expected")
        seed = os.fstat(recovery_signal_seed_fd)
        if not stat.S_ISREG(seed.st_mode) or seed.st_size != 0:
            raise AssertionError("empty recovery-signal seed FD expected")


@unittest.skipUnless(os.geteuid() == 0, "phase-3 recovery runtime is root-only")
class PhysicalWaIrPostgresRecoveryMaterializationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = bootstrap_tests.PhysicalPostgresStandbyBootstrapMaterializationTests(
            methodName="runTest"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.bootstrap = replace(
            self.fixture.config,
            maximum_recovery_evidence_age_seconds=120,
        )
        self.receipt_root = self.fixture.root / "phase3-receipts"
        self.receipt_root.mkdir(mode=0o700)
        os.chmod(self.receipt_root, 0o700)
        self.sealed, self.postgres_image = sealed_release(
            campaign=self.fixture.bundle.baseline.campaign_id,
            release=self.fixture.bundle.baseline.release_sha,
        )
        self.readback = PhysicalPostgresRecoveryReadbackRootConfig(
            enabled=True,
            source_site="webapp_fi",
            receiver_site="webapp_ir",
            stage_bundle_id=self.fixture.stage_binding.bundle_id,
            stage_receipt_sha256=self.fixture.stage_binding.stage_receipt_sha256,
            route_binding_sha256=self.fixture.stage_binding.route_binding_sha256,
            maximum_evidence_age_seconds=120,
        )
        self.deployment = runtime.PhysicalWaIrPostgresSocketOnlyRecoveryDeployment(
            campaign_id=self.fixture.bundle.baseline.campaign_id,
            release_sha=self.fixture.bundle.baseline.release_sha,
            deployment_manifest_lock_sha256=sha("phase3 deployment manifest lock"),
            route_binding_sha256=self.fixture.stage_binding.route_binding_sha256,
            postgres_image=self.postgres_image,
        )
        self.config = runtime.RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig(
            socket_only_deployment=self.deployment,
            sealed_release_descriptor=self.sealed,
            bootstrap_root_config=self.bootstrap,
            readback_root_config=self.readback,
            redacted_receipt_root=self.receipt_root,
            maximum_recovery_evidence_age_seconds=120,
            maximum_release_seal_freshness_seconds=180,
            enabled=True,
        )

    def pull_result(self) -> PhysicalWaIrPostgresRecoveryPullResult:
        receipt_mapping = {
            "schema": "gold-trade-physical-wa-ir-postgres-recovery-pull-redacted-receipt-v1",
            "status": "staged-not-replay-verified",
            "bundle_id": self.fixture.stage_binding.bundle_id,
            "stage_receipt_sha256": self.fixture.stage_binding.stage_receipt_sha256,
            "route_binding_sha256": self.fixture.stage_binding.route_binding_sha256,
        }
        raw = canonical_json_bytes(receipt_mapping)
        return PhysicalWaIrPostgresRecoveryPullResult(
            schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_RUNTIME_SCHEMA,
            status=PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED,
            reason_codes=(),
            redacted_receipt=PhysicalWaIrPostgresRecoveryPullRedactedReceipt(
                raw_receipt=raw,
                receipt_sha256=sha(raw),
                bundle_id=self.fixture.stage_binding.bundle_id,
                stage_receipt_sha256=self.fixture.stage_binding.stage_receipt_sha256,
                route_binding_sha256=self.fixture.stage_binding.route_binding_sha256,
            ),
            recovery_preflight_binding=self.fixture.binding,
            standby_bootstrap_stage_evidence=self.fixture.stage_evidence(),
        )

    def invoke_runtime(self, runner: object, **overrides: object):
        values: dict[str, object] = {
            "config": self.config,
            "bundle": self.fixture.bundle,
            "pull_result": self.pull_result(),
            "current_witnessed_term": self.fixture.term,
            "admission_recovery_readback_evidence": self.fixture.recovery_evidence,
            "runner": runner,
            "now": NOW,
        }
        values.update(overrides)
        return runtime.run_root_owned_wa_ir_postgres_recovery_materialization(**values)

    def test_exact_pull_stage_materializes_and_collects_fresh_redacted_phase3_evidence(self) -> None:
        runner = _Runner()
        result = self.invoke_runtime(runner)

        self.assertTrue(result.replay_observed)
        self.assertEqual((), result.reason_codes)
        self.assertEqual(1, len(runner.materialization_calls))
        self.assertEqual(1, len(runner.inspection_calls))
        self.assertFalse(result.idempotent)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.traffic_switch_authorized)
        self.assertFalse(result.full_matrix_authorized)
        self.assertIsNotNone(result.materialization)
        self.assertIsNotNone(result.recovery_evidence)
        self.assertIsNotNone(result.recovery_result)
        self.assertIsNotNone(result.durable_evidence)
        self.assertEqual("replay-evidence-observed", result.recovery_result.status)
        self.assertEqual(result.recovery_evidence.evidence_sha256, result.durable_evidence.recovery_evidence_sha256)
        self.assertEqual(NOW, result.durable_evidence.observed_at)
        self.assertEqual(sha(result.durable_evidence.raw_receipt), result.durable_evidence.receipt_sha256)
        receipt = json.loads(result.durable_evidence.raw_receipt)
        self.assertEqual(canonical_json_bytes(receipt), result.durable_evidence.raw_receipt)
        self.assertEqual("recovery-replay-observed-not-promoted", receipt["status"])
        self.assertEqual(False, receipt["promotion_authorized"])
        self.assertEqual(False, receipt["writer_authorized"])
        self.assertEqual(False, receipt["traffic_switch_authorized"])
        self.assertEqual(False, receipt["full_matrix_authorized"])
        self.assertEqual(self.fixture.term.writer_lease_id, receipt["writer_lease_id"])
        self.assertEqual(self.fixture.term.witness_transition_id, receipt["witness_transition_id"])
        redacted = result.durable_evidence.raw_receipt.lower()
        for forbidden in (b"credential", b"password", b"object_key", b"version_id", b"candidate_path", b"/tmp"):
            self.assertNotIn(forbidden, redacted)
        rendered = json.loads(result.rendered_inputs.canonical_input)
        self.assertEqual("none", rendered["network_mode"])
        self.assertEqual("disabled", rendered["tcp_listener"])
        self.assertEqual("/var/run/postgresql", rendered["unix_socket_directory"])
        self.assertFalse(rendered["promotion_authorized"])
        self.assertFalse(rendered["full_matrix_authorized"])

    def test_same_bound_candidate_is_idempotently_materialized_but_freshly_reinspected(self) -> None:
        first_runner = _Runner()
        first = self.invoke_runtime(first_runner)
        second_runner = _Runner()
        second = self.invoke_runtime(second_runner)

        self.assertTrue(first.replay_observed)
        self.assertTrue(second.replay_observed)
        self.assertEqual(1, len(first_runner.materialization_calls))
        self.assertEqual(1, len(first_runner.inspection_calls))
        self.assertEqual(0, len(second_runner.materialization_calls))
        self.assertEqual(1, len(second_runner.inspection_calls))
        self.assertTrue(second.idempotent)
        self.assertEqual(first.materialization.plan.plan_sha256, second.materialization.plan.plan_sha256)
        self.assertEqual(first.durable_evidence.raw_receipt, second.durable_evidence.raw_receipt)

    def test_disabled_bad_socket_or_stale_admission_evidence_never_reaches_runner(self) -> None:
        stale_payload = json.loads(self.fixture.recovery_evidence.raw_evidence)
        stale_payload["observed_at"] = (NOW - timedelta(seconds=121)).isoformat()
        stale_raw = canonical_json_bytes(stale_payload)
        stale = PhysicalPostgresRecoveryReceiverReadbackEvidence(
            raw_evidence=stale_raw,
            evidence_sha256=sha(stale_raw),
        )
        cases = (
            {"config": replace(self.config, enabled=False)},
            {
                "config": replace(
                    self.config,
                    socket_only_deployment=replace(self.deployment, network_mode="bridge"),
                )
            },
            {"admission_recovery_readback_evidence": stale},
            {
                "config": replace(
                    self.config,
                    readback_root_config=replace(self.readback, stage_receipt_sha256="f" * 64),
                )
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                runner = _Runner()
                result = self.invoke_runtime(runner, **changes)
                self.assertEqual("blocked", result.status)
                self.assertEqual([], runner.materialization_calls)
                self.assertEqual([], runner.inspection_calls)

    def test_nonstandby_post_materialization_inspection_is_blocked_without_phase3_receipt(self) -> None:
        runner = _Runner(inspection_overrides={"in_recovery": False})
        result = self.invoke_runtime(runner)

        self.assertEqual("blocked", result.status)
        self.assertEqual(1, len(runner.materialization_calls))
        self.assertEqual(1, len(runner.inspection_calls))
        self.assertFalse(list((self.receipt_root / "phase3-recovery-receipts").glob("*.json")))

    def test_term_mutation_by_materializer_blocks_before_socket_inspection_or_phase3_receipt(self) -> None:
        runner = _Runner(mutate_term=self.fixture.term)
        result = self.invoke_runtime(runner)

        self.assertEqual("blocked", result.status)
        self.assertEqual(1, len(runner.materialization_calls))
        self.assertEqual([], runner.inspection_calls)
        self.assertFalse(list((self.receipt_root / "phase3-recovery-receipts").glob("*.json")))

    def test_runtime_surface_excludes_commands_paths_network_and_authority(self) -> None:
        config_fields = {item.name for item in runtime.RootOwnedWaIrPostgresRecoveryMaterializationRuntimeConfig.__dataclass_fields__.values()}
        deployment_fields = {item.name for item in runtime.PhysicalWaIrPostgresSocketOnlyRecoveryDeployment.__dataclass_fields__.values()}
        for forbidden in ("command", "environment", "env", "host", "url", "credential", "password", "token"):
            self.assertNotIn(forbidden, config_fields)
            self.assertNotIn(forbidden, deployment_fields)
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_wa_ir_postgres_recovery_materialization_runtime.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {"boto3", "botocore", "docker", "http", "httpx", "paramiko", "requests", "socket", "subprocess", "urllib"}
        )


if __name__ == "__main__":
    unittest.main()
