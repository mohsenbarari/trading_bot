from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


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
            import tarfile
            import io

            with tarfile.open(uploads, "w:gz") as archive:
                directory = tarfile.TarInfo("uploads")
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
                entry = tarfile.TarInfo("uploads/file.txt")
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))
            payload = MODULE.make_manifest(
                generation="snapshot-20260729-0001",
                release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                alembic_revision="f2c7d8e9a0b1",
                database=database,
                uploads=uploads,
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


if __name__ == "__main__":
    unittest.main()
