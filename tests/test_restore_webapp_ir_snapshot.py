from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "restore_webapp_ir_snapshot",
    ROOT / "scripts/restore_webapp_ir_snapshot.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

try:
    from core.production_snapshot_promotion import parse_restore_receipt
except ModuleNotFoundError:  # The Writer Witness change is merged independently.
    parse_restore_receipt = None


def secure_write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    os.chmod(path, 0o600)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_upload_archive(path: Path, *, unsafe_link: bool = False) -> None:
    with tarfile.open(path, "w:gz") as archive:
        directory = tarfile.TarInfo("uploads")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        if unsafe_link:
            link = tarfile.TarInfo("uploads/unsafe")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            archive.addfile(link)
            return
        body = b"snapshot upload"
        entry = tarfile.TarInfo("uploads/example.txt")
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    os.chmod(path, 0o600)


def write_audit_archive(path: Path) -> None:
    with tarfile.open(path, "w:gz") as archive:
        directory = tarfile.TarInfo("audit_trail")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        body = b'{"audit":true}\n'
        entry = tarfile.TarInfo("audit_trail/audit.jsonl")
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    os.chmod(path, 0o600)


class RestoreWebappIrSnapshotTests(unittest.TestCase):
    def make_docker_root(self, root: Path) -> Path:
        docker_root = root / "docker-root"
        docker_root.mkdir()
        os.chmod(docker_root, 0o700)
        return docker_root

    def make_inputs(self, root: Path) -> tuple[Path, Path, Path]:
        data_root = root / "standby-data"
        workspace = data_root / "work"
        state = data_root / "state"
        workspace.mkdir(parents=True)
        state.mkdir()
        os.chmod(data_root, 0o700)
        os.chmod(workspace, 0o700)
        os.chmod(state, 0o700)
        staged_candidate = workspace / "snapshot-20260729-0001"
        staged_candidate.mkdir()
        os.chmod(staged_candidate, 0o700)
        database = staged_candidate / "database.dump"
        secure_write(database, b"PGDMP" + b"fixture")
        uploads = staged_candidate / "uploads.tar.gz"
        write_upload_archive(uploads)
        now = datetime.now(timezone.utc)
        source_started = now - timedelta(seconds=2)
        source_completed = now - timedelta(seconds=1)
        published = now - timedelta(milliseconds=500)
        ready = now - timedelta(milliseconds=250)
        def rfc3339(value: datetime) -> str:
            return value.isoformat().replace("+00:00", "Z")
        receipt = {
            "schema": "gold-trade-snapshot-ready-v1",
            "status": "ready",
            "source_generation": "snapshot-20260729-0001",
            "snapshot_id": "snapshot-20260729-0001",
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "source_db_snapshot_started_at": rfc3339(source_started),
            "source_capture_completed_at": rfc3339(source_completed),
            "published_at": rfc3339(published),
            "ready_at": rfc3339(ready),
            "source_database_capture": {
                "client_mode": "short_lived_read_only",
                "client_lifetime_seconds": 1,
            },
            "source_volume_capture": {"mode": "read_only_no_mutation"},
            "release_sha": "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
            "alembic_revision": "f2c7d8e9a0b1",
            "candidate_directory": str(staged_candidate),
            "database_dump_path": str(database),
            "uploads_archive_path": str(uploads),
            "database": {
                "sha256": sha256(database),
                "bytes": database.stat().st_size,
                "format": "pg_dump_custom",
            },
            "uploads": {
                "sha256": sha256(uploads),
                "bytes": uploads.stat().st_size,
                "format": "tar_gz_uploads_root",
            },
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        receipt_path = staged_candidate / "snapshot-ready.json"
        secure_write(receipt_path, json.dumps(receipt).encode("utf-8"))
        database_env = root / "db.env"
        secure_write(
            database_env,
            b"POSTGRES_USER=app\nPOSTGRES_PASSWORD=not-printed\nPOSTGRES_DB=trading\n",
        )
        standby_env = root / "standby.env"
        secure_write(
            standby_env,
            (
                "RELEASE_SHA=2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5\n"
                "EXPECTED_ALEMBIC_REVISION=f2c7d8e9a0b1\n"
                f"WA_IR_STANDBY_DATA_ROOT={data_root}\n"
                f"WA_IR_SNAPSHOT_WORK_ROOT={workspace}\n"
                f"WA_IR_SNAPSHOT_STATE_ROOT={state}\n"
                "WA_IR_SNAPSHOT_MAX_AGE_SECONDS=30\n"
                "WA_IR_SNAPSHOT_MIN_FREE_BYTES=0\n"
                "WA_IR_SNAPSHOT_DATABASE_RESTORE_RESERVE_BYTES=1024\n"
                f"WA_IR_STANDBY_DATABASE_ENV_FILE={database_env}\n"
                "WA_IR_POSTGRES_IMAGE=postgres:15-alpine\n"
            ).encode("utf-8"),
        )
        return standby_env, receipt_path, uploads

    def add_witness_transport_bindings(self, receipt_path: Path) -> None:
        """Add the remote descriptors a real generic S3 ready receipt carries."""

        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        audit = receipt_path.parent / "audit.tar.gz"
        write_audit_archive(audit)
        receipt["audit_archive_path"] = str(audit)
        receipt["audit"] = {
            "sha256": sha256(audit),
            "bytes": audit.stat().st_size,
            "format": "tar_gz_audit_trail_root",
            "object_key": "snapshot-fi-ir/audit.age",
            "version_id": "audit-version-1",
            "ciphertext_sha256": "a" * 64,
            "ciphertext_bytes": 100,
        }
        receipt["database"].update(
            {
                "object_key": "snapshot-fi-ir/database.age",
                "version_id": "database-version-1",
                "ciphertext_sha256": "b" * 64,
                "ciphertext_bytes": 101,
            }
        )
        receipt["uploads"].update(
            {
                "object_key": "snapshot-fi-ir/uploads.age",
                "version_id": "uploads-version-1",
                "ciphertext_sha256": "c" * 64,
                "ciphertext_bytes": 102,
            }
        )
        receipt["manifest"] = {
            "object_key": "snapshot-fi-ir/manifest.json.age",
            "version_id": "manifest-version-1",
            "ciphertext_sha256": "d" * 64,
            "ciphertext_bytes": 103,
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                {key: value for key, value in receipt.items() if key != "receipt_sha256"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        secure_write(receipt_path, json.dumps(receipt).encode("utf-8"))

    def make_witness_receipt_fixture(self, root: Path) -> tuple[object, object, dict, dict]:
        standby_env, receipt_path, _ = self.make_inputs(root)
        self.add_witness_transport_bindings(receipt_path)
        values = MODULE.parse_env_file(standby_env, label="standby env")
        receipt = MODULE.load_receipt(
            receipt_path,
            workspace_root=Path(values["WA_IR_SNAPSHOT_WORK_ROOT"]),
        )
        candidate = MODULE.build_candidate(
            Path(values["WA_IR_STANDBY_DATA_ROOT"]), receipt.snapshot_id
        )
        restored_at = (receipt.ready_at_value + timedelta(milliseconds=1)).isoformat().replace(
            "+00:00", "Z"
        )
        verified_at = (receipt.ready_at_value + timedelta(milliseconds=2)).isoformat().replace(
            "+00:00", "Z"
        )
        witness = MODULE.build_witness_restore_receipt(
            receipt=receipt,
            restored_at=restored_at,
            restore_verified_at=verified_at,
        )
        active = MODULE.candidate_payload(
            receipt=receipt,
            candidate=candidate,
            table_count=1,
            upload_members=1,
            upload_bytes=1,
            audit_members=1,
            audit_bytes=1,
            maximum_snapshot_age_seconds=30,
            source_db_snapshot_age_seconds=1,
        )
        return receipt, candidate, witness, active

    def test_validation_plan_never_invokes_docker_or_starts_an_app(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            standby_env, receipt, _ = self.make_inputs(Path(temporary))
            payload = MODULE.execute(
                MODULE.build_parser().parse_args(
                    ["--standby-env", str(standby_env), "--receipt", str(receipt)]
                )
            )
        self.assertEqual(payload["status"], "planned")
        self.assertFalse(payload["app_started"])
        self.assertFalse(payload["direct_sync_started"])
        self.assertFalse(payload["migration_started"])
        self.assertFalse(payload["public_routing_changed"])
        self.assertEqual(payload["candidate"]["db_volume"], "trading_bot_wa_ir_pg_snapshot-20260729-0001")

    def test_capacity_failure_precedes_journal_and_docker_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            docker_root = self.make_docker_root(root)
            arguments = MODULE.build_parser().parse_args(
                ["--standby-env", str(standby_env), "--receipt", str(receipt_path), "--apply"]
            )
            state_root = Path(MODULE.parse_env_file(standby_env, label="standby env")["WA_IR_SNAPSHOT_STATE_ROOT"])
            with mock.patch.object(
                MODULE,
                "require_capacity",
                side_effect=MODULE.SnapshotCapacityError("fixture capacity failure"),
            ), mock.patch.object(
                MODULE,
                "require_docker_root_directory",
                return_value=docker_root,
            ), mock.patch.object(MODULE, "docker_volume_absent") as volume_absent:
                with self.assertRaisesRegex(MODULE.RestoreError, "fixture capacity failure"):
                    MODULE.execute(arguments)

        self.assertFalse(MODULE.restore_inflight_journal_path(state_root).exists())
        volume_absent.assert_not_called()

    def test_docker_root_capacity_failure_precedes_journal_and_volume_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            docker_root = self.make_docker_root(root)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            data_root = Path(values["WA_IR_STANDBY_DATA_ROOT"])
            state_root = Path(values["WA_IR_SNAPSHOT_STATE_ROOT"])
            receipt = MODULE.load_receipt(
                receipt_path,
                workspace_root=Path(values["WA_IR_SNAPSHOT_WORK_ROOT"]),
            )
            candidate = MODULE.build_candidate(data_root, receipt.snapshot_id)
            arguments = MODULE.build_parser().parse_args(
                ["--standby-env", str(standby_env), "--receipt", str(receipt_path), "--apply"]
            )

            def capacity(path: Path, **kwargs: object) -> dict[str, object]:
                if kwargs["label"] == "WA-IR DockerRootDir temporary restore workspace":
                    self.assertEqual(path, docker_root)
                    self.assertEqual(
                        kwargs["required_new_bytes"],
                        receipt.database.byte_count + 1024,
                    )
                    raise MODULE.SnapshotCapacityError("fixture DockerRootDir capacity failure")
                return {"path": str(path), "label": kwargs["label"]}

            with (
                mock.patch.object(
                    MODULE,
                    "require_docker_root_directory",
                    return_value=docker_root,
                ),
                mock.patch.object(MODULE, "paths_share_filesystem", return_value=False),
                mock.patch.object(MODULE, "require_capacity", side_effect=capacity),
                mock.patch.object(MODULE, "docker_volume_absent") as volume_absent,
            ):
                with self.assertRaisesRegex(MODULE.RestoreError, "DockerRootDir capacity failure"):
                    MODULE.execute(arguments)

        self.assertFalse(MODULE.restore_inflight_journal_path(state_root).exists())
        self.assertFalse(candidate.root.exists())
        volume_absent.assert_not_called()

    def test_shared_filesystem_capacity_combines_candidate_and_docker_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            docker_root = root / "docker"
            data_root.mkdir()
            docker_root.mkdir()
            os.chmod(data_root, 0o700)
            os.chmod(docker_root, 0o700)
            admission = {"available_bytes": 1000}
            with mock.patch.object(MODULE, "require_capacity", return_value=admission) as capacity:
                result = MODULE.require_restore_capacity(
                    data_root,
                    docker_root=docker_root,
                    candidate_required_new_bytes=101,
                    docker_restore_required_new_bytes=37,
                    minimum_free_bytes=11,
                )

        capacity.assert_called_once_with(
            data_root,
            required_new_bytes=138,
            minimum_free_bytes=11,
            label="WA-IR snapshot candidate data root and DockerRootDir",
        )
        self.assertEqual(result["filesystem_layout"], "shared")
        self.assertEqual(result["candidate_required_new_bytes"], 101)
        self.assertEqual(result["docker_restore_required_new_bytes"], 37)
        self.assertEqual(result["admission"], admission)

    def test_docker_root_inspection_uses_read_only_daemon_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docker_root = self.make_docker_root(root)
            runner = mock.Mock()
            runner.run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"{docker_root}\n",
                stderr="",
            )
            observed = MODULE.require_docker_root_directory(runner)

        self.assertEqual(observed, docker_root)
        runner.run.assert_called_once_with(
            ["docker", "info", "--format", MODULE.DOCKER_ROOT_DIR_FORMAT],
            timeout=30,
        )

    def test_failed_apply_leaves_durable_inflight_journal_and_closes_future_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            docker_root = self.make_docker_root(root)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            state_root = Path(values["WA_IR_SNAPSHOT_STATE_ROOT"])
            data_root = Path(values["WA_IR_STANDBY_DATA_ROOT"])
            receipt = MODULE.load_receipt(
                receipt_path,
                workspace_root=Path(values["WA_IR_SNAPSHOT_WORK_ROOT"]),
            )
            candidate = MODULE.build_candidate(data_root, receipt.snapshot_id)
            arguments = MODULE.build_parser().parse_args(
                ["--standby-env", str(standby_env), "--receipt", str(receipt_path), "--apply"]
            )

            with (
                mock.patch.object(
                    MODULE,
                    "require_docker_root_directory",
                    return_value=docker_root,
                ),
                mock.patch.object(MODULE, "docker_volume_absent", return_value=True),
                mock.patch.object(MODULE, "docker_container_absent", return_value=True),
                mock.patch.object(
                    MODULE,
                    "create_bound_volume",
                    side_effect=MODULE.RestoreError("fixture volume failure"),
                ),
            ):
                with self.assertRaisesRegex(MODULE.RestoreError, "fixture volume failure"):
                    MODULE.execute(arguments)

            journal_path = MODULE.restore_inflight_journal_path(state_root)
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertEqual(journal["schema"], MODULE.RESTORE_INFLIGHT_SCHEMA)
            self.assertEqual(journal["status"], "in_progress")
            self.assertEqual(journal["phase"], "directories_created")
            self.assertEqual(journal["candidate"]["generation"], receipt.snapshot_id)
            self.assertEqual(journal["candidate"]["root"], str(candidate.root))
            self.assertEqual(journal_path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(candidate.root.is_dir())
            with self.assertRaisesRegex(MODULE.RestoreError, "recovery is required"):
                MODULE.require_no_restore_inflight(state_root)

    def test_apply_refuses_existing_inflight_journal_before_docker_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            receipt = MODULE.load_receipt(
                receipt_path,
                workspace_root=Path(values["WA_IR_SNAPSHOT_WORK_ROOT"]),
            )
            candidate = MODULE.build_candidate(
                Path(values["WA_IR_STANDBY_DATA_ROOT"]), "manual-restore-1"
            )
            MODULE.write_restore_inflight_journal(
                Path(values["WA_IR_SNAPSHOT_STATE_ROOT"]),
                receipt=receipt,
                candidate=candidate,
            )
            arguments = MODULE.build_parser().parse_args(
                [
                    "--standby-env",
                    str(standby_env),
                    "--receipt",
                    str(receipt_path),
                    "--generation",
                    "manual-restore-1",
                    "--apply",
                ]
            )
            with mock.patch.object(
                MODULE,
                "docker_volume_absent",
                side_effect=AssertionError("Docker inspection must not run"),
            ):
                with self.assertRaisesRegex(MODULE.RestoreError, "recovery is required"):
                    MODULE.execute(arguments)

    def test_successful_apply_clears_inflight_journal_only_after_pointer_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            docker_root = self.make_docker_root(root)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            state_root = Path(values["WA_IR_SNAPSHOT_STATE_ROOT"])
            arguments = MODULE.build_parser().parse_args(
                ["--standby-env", str(standby_env), "--receipt", str(receipt_path), "--apply"]
            )
            with (
                mock.patch.object(
                    MODULE,
                    "require_docker_root_directory",
                    return_value=docker_root,
                ),
                mock.patch.object(MODULE, "docker_volume_absent", return_value=True),
                mock.patch.object(MODULE, "docker_container_absent", return_value=True),
                mock.patch.object(MODULE, "create_bound_volume"),
                mock.patch.object(MODULE.DockerRunner, "run", return_value=None),
                mock.patch.object(MODULE, "wait_for_database"),
                mock.patch.object(
                    MODULE,
                    "restore_database",
                    return_value=("f2c7d8e9a0b1", 7),
                ),
            ):
                payload = MODULE.execute(arguments)

            self.assertEqual(payload["status"], "ready")
            self.assertTrue((state_root / "active-snapshot.json").is_file())
            self.assertFalse(MODULE.restore_inflight_journal_path(state_root).exists())

    def test_failure_after_active_pointer_keeps_the_recovery_gate_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            docker_root = self.make_docker_root(root)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            state_root = Path(values["WA_IR_SNAPSHOT_STATE_ROOT"])
            arguments = MODULE.build_parser().parse_args(
                ["--standby-env", str(standby_env), "--receipt", str(receipt_path), "--apply"]
            )
            original_advance = MODULE.advance_restore_inflight_journal

            def fail_after_pointer(
                path: Path,
                journal: dict[str, object],
                *,
                phase: str,
            ) -> dict[str, object]:
                if phase == "active_pointer_committed":
                    raise MODULE.RestoreError("fixture interruption after pointer")
                return original_advance(path, journal, phase=phase)

            with (
                mock.patch.object(
                    MODULE,
                    "require_docker_root_directory",
                    return_value=docker_root,
                ),
                mock.patch.object(MODULE, "docker_volume_absent", return_value=True),
                mock.patch.object(MODULE, "docker_container_absent", return_value=True),
                mock.patch.object(MODULE, "create_bound_volume"),
                mock.patch.object(MODULE.DockerRunner, "run", return_value=None),
                mock.patch.object(MODULE, "wait_for_database"),
                mock.patch.object(
                    MODULE,
                    "restore_database",
                    return_value=("f2c7d8e9a0b1", 7),
                ),
                mock.patch.object(
                    MODULE,
                    "advance_restore_inflight_journal",
                    side_effect=fail_after_pointer,
                ),
            ):
                with self.assertRaisesRegex(MODULE.RestoreError, "interruption after pointer"):
                    MODULE.execute(arguments)

            self.assertTrue((state_root / "active-snapshot.json").is_file())
            journal = json.loads(
                MODULE.restore_inflight_journal_path(state_root).read_text(encoding="utf-8")
            )
            self.assertEqual(journal["status"], "in_progress")
            self.assertEqual(journal["phase"], "database_verified")

    def test_receipt_outside_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            outside = root / "outside.dump"
            secure_write(outside, b"PGDMPoutside")
            receipt["database_dump_path"] = str(outside)
            receipt["database"]["sha256"] = sha256(outside)
            receipt["database"]["bytes"] = outside.stat().st_size
            receipt["receipt_sha256"] = hashlib.sha256(
                json.dumps({key: value for key, value in receipt.items() if key != "receipt_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            secure_write(receipt_path, json.dumps(receipt).encode("utf-8"))
            with self.assertRaisesRegex(MODULE.RestoreError, "workspace"):
                MODULE.execute(
                    MODULE.build_parser().parse_args(
                        ["--standby-env", str(standby_env), "--receipt", str(receipt_path)]
                    )
                )

    def test_upload_archive_with_a_link_is_rejected_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, uploads = self.make_inputs(root)
            write_upload_archive(uploads, unsafe_link=True)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["uploads"]["sha256"] = sha256(uploads)
            receipt["uploads"]["bytes"] = uploads.stat().st_size
            receipt["receipt_sha256"] = hashlib.sha256(
                json.dumps({key: value for key, value in receipt.items() if key != "receipt_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            secure_write(receipt_path, json.dumps(receipt).encode("utf-8"))
            with self.assertRaisesRegex(MODULE.RestoreError, "links or special"):
                MODULE.execute(
                    MODULE.build_parser().parse_args(
                        ["--standby-env", str(standby_env), "--receipt", str(receipt_path)]
                    )
                )

    def test_wrong_release_is_rejected_before_volume_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["release_sha"] = "a" * 40
            receipt["receipt_sha256"] = hashlib.sha256(
                json.dumps({key: value for key, value in receipt.items() if key != "receipt_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            secure_write(receipt_path, json.dumps(receipt).encode("utf-8"))
            with self.assertRaisesRegex(MODULE.RestoreError, "pinned standby release"):
                MODULE.execute(
                    MODULE.build_parser().parse_args(
                        ["--standby-env", str(standby_env), "--receipt", str(receipt_path)]
                    )
                )

    def test_database_snapshot_start_outside_bound_is_rejected_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["source_db_snapshot_started_at"] = "2000-01-01T00:00:00Z"
            receipt["source_capture_completed_at"] = "2000-01-01T00:00:01Z"
            receipt["published_at"] = "2000-01-01T00:00:02Z"
            receipt["ready_at"] = "2000-01-01T00:00:03Z"
            receipt["receipt_sha256"] = hashlib.sha256(
                json.dumps({key: value for key, value in receipt.items() if key != "receipt_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            secure_write(receipt_path, json.dumps(receipt).encode("utf-8"))
            with self.assertRaisesRegex(MODULE.RestoreError, "database snapshot start"):
                MODULE.execute(
                    MODULE.build_parser().parse_args(
                        ["--standby-env", str(standby_env), "--receipt", str(receipt_path)]
                    )
                )

    def test_restore_marker_binds_the_canonical_ready_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            receipt = MODULE.load_receipt(
                receipt_path,
                workspace_root=Path(values["WA_IR_SNAPSHOT_WORK_ROOT"]),
            )
            marker = MODULE.build_restore_marker(
                receipt=receipt,
                candidate=MODULE.build_candidate(
                    Path(values["WA_IR_STANDBY_DATA_ROOT"]), receipt.snapshot_id
                ),
            )
        self.assertEqual(marker["schema"], "gold-trade-snapshot-restore-receipt-v1")
        self.assertEqual(marker["status"], "restored_verified")
        self.assertEqual(marker["active_pointer_state"], "active")
        self.assertEqual(marker["ready_receipt_sha256"], receipt.raw["receipt_sha256"])
        self.assertEqual(
            marker["receipt_sha256"], MODULE.canonical_payload_sha256(marker, omit="receipt_sha256")
        )

    def test_only_a_bound_prior_marker_is_marked_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            data_root = Path(values["WA_IR_STANDBY_DATA_ROOT"])
            workspace = Path(values["WA_IR_SNAPSHOT_WORK_ROOT"])
            receipt = MODULE.load_receipt(receipt_path, workspace_root=workspace)
            candidate = MODULE.build_candidate(data_root, receipt.snapshot_id)
            marker = MODULE.build_restore_marker(receipt=receipt, candidate=candidate)
            marker_path = receipt.staged_candidate_directory / "snapshot-restore.json"
            MODULE.write_new_json(marker_path, marker)
            previous = MODULE.candidate_payload(
                receipt=receipt,
                candidate=candidate,
                table_count=1,
                upload_members=1,
                upload_bytes=1,
                audit_members=None,
                audit_bytes=None,
                maximum_snapshot_age_seconds=30,
                source_db_snapshot_age_seconds=1,
            )
            state = MODULE.mark_previous_transport_candidate_inactive(
                previous, workspace_root=workspace
            )
            updated = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(state, "inactive")
        self.assertEqual(updated["active_pointer_state"], "inactive")
        self.assertEqual(
            updated["receipt_sha256"], MODULE.canonical_payload_sha256(updated, omit="receipt_sha256")
        )

    def test_optional_audit_archive_is_bound_and_planned_for_its_own_volume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            audit = receipt_path.parent / "audit.tar.gz"
            write_audit_archive(audit)
            receipt["audit_archive_path"] = str(audit)
            receipt["audit"] = {
                "sha256": sha256(audit),
                "bytes": audit.stat().st_size,
                "format": "tar_gz_audit_trail_root",
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                json.dumps({key: value for key, value in receipt.items() if key != "receipt_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            secure_write(receipt_path, json.dumps(receipt).encode("utf-8"))
            payload = MODULE.execute(
                MODULE.build_parser().parse_args(
                    ["--standby-env", str(standby_env), "--receipt", str(receipt_path)]
                )
            )
        self.assertEqual(payload["audit"]["status"], "planned")
        self.assertEqual(payload["candidate"]["audit_volume"], "trading_bot_wa_ir_audit_snapshot-20260729-0001")

    def test_witness_receipt_is_strict_hash_bound_and_strips_transport_format(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            self.add_witness_transport_bindings(receipt_path)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            receipt = MODULE.load_receipt(
                receipt_path,
                workspace_root=Path(values["WA_IR_SNAPSHOT_WORK_ROOT"]),
            )
            candidate = MODULE.build_candidate(
                Path(values["WA_IR_STANDBY_DATA_ROOT"]), receipt.snapshot_id
            )
            restored_at = (receipt.ready_at_value + timedelta(milliseconds=1)).isoformat().replace(
                "+00:00", "Z"
            )
            verified_at = (receipt.ready_at_value + timedelta(milliseconds=2)).isoformat().replace(
                "+00:00", "Z"
            )
            witness = MODULE.build_witness_restore_receipt(
                receipt=receipt,
                restored_at=restored_at,
                restore_verified_at=verified_at,
            )
            active = MODULE.candidate_payload(
                receipt=receipt,
                candidate=candidate,
                table_count=1,
                upload_members=1,
                upload_bytes=1,
                audit_members=1,
                audit_bytes=1,
                maximum_snapshot_age_seconds=30,
                source_db_snapshot_age_seconds=1,
            )
            witness_root = root / "witness"
            witness_root.mkdir()
            os.chmod(witness_root, 0o700)
            target = witness_root / "latest-restore-receipt.json"
            binding = MODULE.bind_witness_receipt_to_active_snapshot(
                active_snapshot=active,
                candidate=candidate,
                witness_path=target,
                witness_receipt=witness,
            )
            prepared = MODULE.prepare_root_only_atomic_json(target, witness)
            self.assertFalse(target.exists())
            MODULE.commit_prepared_atomic_json(prepared)
            persisted = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(set(witness), MODULE.WITNESS_RECEIPT_FIELDS)
        self.assertEqual(witness["stage_receipt_sha256"], receipt.raw["receipt_sha256"])
        self.assertEqual(witness["restored_database_sha256"], receipt.database.sha256)
        self.assertEqual(witness["restored_uploads_sha256"], receipt.uploads.sha256)
        self.assertEqual(set(witness["database"]), MODULE.WITNESS_PLAINTEXT_ARTIFACT_FIELDS)
        self.assertNotIn("format", witness["database"])
        self.assertEqual(witness["manifest"], receipt.raw["manifest"])
        self.assertEqual(witness["receipt_sha256"], MODULE.canonical_witness_receipt_sha256(witness))
        self.assertEqual(persisted, witness)
        self.assertEqual(
            binding,
            {
                "path": str(target),
                "receipt_sha256": witness["receipt_sha256"],
                "stage_receipt_sha256": receipt.raw["receipt_sha256"],
                "source_generation": receipt.source_generation,
                "snapshot_id": receipt.snapshot_id,
            },
        )

    @unittest.skipIf(parse_restore_receipt is None, "Writer Witness core is merged independently")
    def test_witness_receipt_is_accepted_by_the_exact_writer_core_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _receipt, _candidate, witness, _active = self.make_witness_receipt_fixture(Path(temporary))
            parsed = parse_restore_receipt(
                witness,
                action="promote_ir",
                now=datetime.now(timezone.utc),
            )
        self.assertEqual(parsed.receipt_sha256, witness["receipt_sha256"])
        self.assertEqual(parsed.stage_receipt_sha256, witness["stage_receipt_sha256"])

    def test_witness_receipt_requires_verified_audit_and_matching_candidate_volume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standby_env, receipt_path, _ = self.make_inputs(root)
            values = MODULE.parse_env_file(standby_env, label="standby env")
            receipt = MODULE.load_receipt(
                receipt_path,
                workspace_root=Path(values["WA_IR_SNAPSHOT_WORK_ROOT"]),
            )
            candidate = MODULE.build_candidate(
                Path(values["WA_IR_STANDBY_DATA_ROOT"]), receipt.snapshot_id
            )
            with self.assertRaisesRegex(MODULE.RestoreError, "verified audit artifact"):
                MODULE.build_witness_restore_receipt(
                    receipt=receipt,
                    restored_at=receipt.ready_at,
                    restore_verified_at=receipt.ready_at,
                )

            self.add_witness_transport_bindings(receipt_path)
            receipt = MODULE.load_receipt(
                receipt_path,
                workspace_root=Path(values["WA_IR_SNAPSHOT_WORK_ROOT"]),
            )
            witness = MODULE.build_witness_restore_receipt(
                receipt=receipt,
                restored_at=receipt.ready_at,
                restore_verified_at=receipt.ready_at,
            )
            active = MODULE.candidate_payload(
                receipt=receipt,
                candidate=candidate,
                table_count=1,
                upload_members=1,
                upload_bytes=1,
                audit_members=1,
                audit_bytes=1,
                maximum_snapshot_age_seconds=30,
                source_db_snapshot_age_seconds=1,
            )
            active["audit"]["status"] = "planned"
            witness_root = root / "witness"
            witness_root.mkdir()
            os.chmod(witness_root, 0o700)
            with self.assertRaisesRegex(MODULE.RestoreError, "audit.status=verified"):
                MODULE.bind_witness_receipt_to_active_snapshot(
                    active_snapshot=active,
                    candidate=candidate,
                    witness_path=witness_root / "latest-restore-receipt.json",
                    witness_receipt=witness,
                )

            active["audit"]["status"] = "verified"
            active["candidate"]["audit_volume"] = "wrong-candidate-audit-volume"
            with self.assertRaisesRegex(MODULE.RestoreError, "active candidate audit volume"):
                MODULE.bind_witness_receipt_to_active_snapshot(
                    active_snapshot=active,
                    candidate=candidate,
                    witness_path=witness_root / "latest-restore-receipt.json",
                    witness_receipt=witness,
                )

    def test_witness_receipt_replaces_only_the_canonical_latest_file_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "latest-restore-receipt.json"
            secure_write(target, b'{"old":true}\n')
            payload = {"new": True}
            prepared = MODULE.prepare_root_only_atomic_json(target, payload)
            self.assertEqual(target.read_bytes(), b'{"old":true}\n')
            MODULE.commit_prepared_atomic_json(prepared)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), payload)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_witness_publication_never_exposes_an_unbound_new_latest_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, candidate, witness, active = self.make_witness_receipt_fixture(root)
            witness_root = root / "witness"
            witness_root.mkdir()
            os.chmod(witness_root, 0o700)
            target = witness_root / "latest-restore-receipt.json"
            old = dict(witness)
            old["snapshot_id"] = "snapshot-20260729-old"
            old["receipt_sha256"] = MODULE.canonical_witness_receipt_sha256(old)
            secure_write(target, json.dumps(old).encode("utf-8"))
            binding = MODULE.bind_witness_receipt_to_active_snapshot(
                active_snapshot=active,
                candidate=candidate,
                witness_path=target,
                witness_receipt=witness,
            )
            prepared = MODULE.prepare_root_only_atomic_json(target, witness)

            # This is the only permitted crash window: the pointer may bind the
            # new hash while the old canonical path remains visible.  A Writer
            # controller that checks the binding rejects the old receipt.
            active["witness_restore_receipt"] = binding
            visible_before_commit = json.loads(target.read_text(encoding="utf-8"))
            self.assertNotEqual(visible_before_commit["receipt_sha256"], binding["receipt_sha256"])
            self.assertTrue(prepared.temporary.exists())

            MODULE.commit_prepared_atomic_json(prepared)
            visible_after_commit = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(visible_after_commit["receipt_sha256"], binding["receipt_sha256"])
        self.assertEqual(active["candidate"]["audit_volume"], candidate.audit_volume)
        self.assertEqual(receipt.raw["receipt_sha256"], binding["stage_receipt_sha256"])

    def test_witness_receipt_path_must_be_root_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            insecure = root / "insecure"
            insecure.mkdir()
            os.chmod(insecure, 0o755)
            with self.assertRaisesRegex(MODULE.RestoreError, "root-only"):
                MODULE.require_witness_receipt_path(insecure / "latest-restore-receipt.json")

            secure = root / "secure"
            secure.mkdir()
            os.chmod(secure, 0o700)
            target = secure / "latest-restore-receipt.json"
            secure_write(target, b"{}")
            os.chmod(target, 0o644)
            with self.assertRaisesRegex(MODULE.RestoreError, "existing Witness restore receipt"):
                MODULE.require_witness_receipt_path(target)


if __name__ == "__main__":
    unittest.main()
