from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "restore_webapp_ir_snapshot",
    ROOT / "scripts/restore_webapp_ir_snapshot.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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
                f"WA_IR_STANDBY_DATABASE_ENV_FILE={database_env}\n"
                "WA_IR_POSTGRES_IMAGE=postgres:15-alpine\n"
            ).encode("utf-8"),
        )
        return standby_env, receipt_path, uploads

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


if __name__ == "__main__":
    unittest.main()
