from __future__ import annotations

import importlib.util
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RESTORE_SPEC = importlib.util.spec_from_file_location(
    "restore_webapp_ir_snapshot",
    ROOT / "scripts/restore_webapp_ir_snapshot.py",
)
assert RESTORE_SPEC and RESTORE_SPEC.loader
RESTORE_MODULE = importlib.util.module_from_spec(RESTORE_SPEC)
sys.modules[RESTORE_SPEC.name] = RESTORE_MODULE
RESTORE_SPEC.loader.exec_module(RESTORE_MODULE)
SPEC = importlib.util.spec_from_file_location(
    "create_webapp_fi_snapshot_artifacts",
    ROOT / "scripts/create_webapp_fi_snapshot_artifacts.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CreateWebappFiSnapshotArtifactsTests(unittest.TestCase):
    def test_default_plan_has_no_remote_transfer_or_service_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture_env = Path(temporary) / "capture.env"
            capture_env.write_text("CAPTURE_DB_USER=snapshot_reader\nCAPTURE_DB_PASSWORD=not-printed\n", encoding="utf-8")
            capture_env.chmod(0o600)
            payload = MODULE.execute(
                MODULE.build_parser().parse_args(
                    [
                        "--output-root",
                        temporary,
                        "--release-sha",
                        "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                        "--alembic-revision",
                        "f2c7d8e9a0b1",
                        "--generation",
                        "snapshot-20260729-0001",
                        "--db-capture-env",
                        str(capture_env),
                    ]
                )
            )
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["remote_transfer"], "none")
        self.assertFalse(payload["services_stopped"])
        self.assertFalse(payload["source_data_mutated"])
        self.assertFalse(payload["audit_included"])

    def test_invalid_container_name_is_rejected_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture_env = Path(temporary) / "capture.env"
            capture_env.write_text("CAPTURE_DB_USER=snapshot_reader\nCAPTURE_DB_PASSWORD=not-printed\n", encoding="utf-8")
            capture_env.chmod(0o600)
            with self.assertRaisesRegex(RESTORE_MODULE.RestoreError, "safe Docker"):
                MODULE.execute(
                    MODULE.build_parser().parse_args(
                        [
                            "--output-root",
                            temporary,
                            "--release-sha",
                            "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                            "--alembic-revision",
                            "f2c7d8e9a0b1",
                            "--generation",
                            "snapshot-20260729-0001",
                            "--db-capture-env",
                            str(capture_env),
                            "--db-container",
                            "bad;command",
                        ]
                    )
                )

    def test_manifest_describes_only_transport_input_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "database.dump"
            database.write_bytes(b"PGDMPfixture")
            uploads = root / "uploads.tar.gz"
            audit = root / "audit.tar.gz"
            import tarfile
            import io

            with tarfile.open(uploads, "w:gz") as archive:
                directory = tarfile.TarInfo("uploads")
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
                entry = tarfile.TarInfo("uploads/file.txt")
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))
            with tarfile.open(audit, "w:gz") as archive:
                directory = tarfile.TarInfo("audit_trail")
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
                entry = tarfile.TarInfo("audit_trail/audit.jsonl")
                entry.size = 2
                archive.addfile(entry, io.BytesIO(b"{}"))
            payload = MODULE.make_manifest(
                generation="snapshot-20260729-0001",
                release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                alembic_revision="f2c7d8e9a0b1",
                database=database,
                uploads=uploads,
                audit=audit,
                source_db_snapshot_started_at="2026-07-29T00:00:00Z",
                source_capture_completed_at="2026-07-29T00:00:01Z",
                source_db_client_lifetime_seconds=1,
            )
        self.assertEqual(payload["database"]["format"], "pg_dump_custom")
        self.assertEqual(payload["uploads"]["format"], "tar_gz_uploads_root")
        self.assertEqual(payload["source_site"], "webapp_fi")
        self.assertEqual(payload["destination_site"], "webapp_ir")
        self.assertEqual(payload["source_database_capture"]["client_mode"], "short_lived_read_only")
        self.assertEqual(payload["source_db_snapshot_started_at"], "2026-07-29T00:00:00Z")
        self.assertEqual(payload["source_capture_completed_at"], "2026-07-29T00:00:01Z")
        self.assertEqual(payload["audit"]["format"], "tar_gz_audit_trail_root")

    def test_client_lifetime_uses_ceil_and_never_underreports(self) -> None:
        self.assertEqual(MODULE.conservative_client_lifetime_seconds(300.0001), 301)
        self.assertEqual(MODULE.conservative_client_lifetime_seconds(0.001), 1)

    def test_applied_capture_reports_ready_only_after_manifest_is_written(self) -> None:
        def fake_capture(_arguments, *, stdout_path=None, **_kwargs):
            assert stdout_path is not None
            if stdout_path.name == "database.dump":
                stdout_path.write_bytes(b"PGDMPfixture")
                return ""
            root_name = "audit_trail" if stdout_path.name == "audit.tar.gz" else "uploads"
            with tarfile.open(stdout_path, "w:gz") as archive:
                directory = tarfile.TarInfo(root_name + "/")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o700
                archive.addfile(directory)
                entry = tarfile.TarInfo(root_name + "/sample.txt")
                entry.size = 1
                entry.mode = 0o600
                archive.addfile(entry, io.BytesIO(b"x"))
            return ""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            capture_env = root / "capture.env"
            capture_env.write_text(
                "CAPTURE_DB_USER=snapshot_reader\nCAPTURE_DB_PASSWORD=not-printed\n",
                encoding="utf-8",
            )
            os.chmod(capture_env, 0o600)
            arguments = MODULE.build_parser().parse_args(
                [
                    "--output-root", str(root),
                    "--release-sha", "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                    "--alembic-revision", "f2c7d8e9a0b1",
                    "--generation", "snapshot-20260729-0002",
                    "--db-capture-env", str(capture_env),
                    "--include-audit",
                    "--apply",
                ]
            )
            with (
                mock.patch.object(MODULE, "assert_source_role_read_only"),
                mock.patch.object(MODULE, "source_alembic_revision", return_value="f2c7d8e9a0b1"),
                mock.patch.object(MODULE, "run_capture", side_effect=fake_capture),
            ):
                payload = MODULE.execute(arguments)

            self.assertEqual(payload["status"], "ready")
            self.assertTrue(Path(payload["manifest_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
