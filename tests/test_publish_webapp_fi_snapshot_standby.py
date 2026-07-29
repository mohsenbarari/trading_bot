from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "publish_webapp_fi_snapshot_standby",
    ROOT / "scripts/publish_webapp_fi_snapshot_standby.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
ALEMBIC_REVISION = "f2c7d8e9a0b1"
GENERATION = "snapshot-20260729t120000z-0123456789ab"
AGE_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def secure_write(path: Path, value: str | bytes, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_bytes(value)
    os.chmod(path, 0o700 if executable else 0o600)


class PublishWebappFiSnapshotStandbyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fi-snapshot-publisher-test-")
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.data_root = self.root / "standby-data"
        self.state_root = self.data_root / "state"
        self.workspace = self.data_root / "transport-work"
        for directory in (self.data_root, self.state_root, self.workspace):
            secure_directory(directory)
        self.capture_env = self.root / "capture.env"
        secure_write(self.capture_env, "CAPTURE_DB_USER=snapshot_reader\nCAPTURE_DB_PASSWORD=not-printed\n")
        self.signing_key = self.root / "webapp-fi-ed25519.raw"
        secure_write(self.signing_key, b"k" * 32)
        self.credentials = self.root / "s3-credentials.json"
        secure_write(self.credentials, "{\"access_key\":\"not-printed\",\"secret_key\":\"not-printed\"}\n")
        self.transport_config = self.root / "transport.json"
        secure_write(
            self.transport_config,
            json.dumps(
                {
                    "schema": MODULE.TRANSPORT_SCHEMA,
                    "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
                    "region": "ir-thr-at1",
                    "bucket": "private-snapshots",
                    "prefix": "webapp-ir-snapshot-2c08",
                    "credentials_file": str(self.credentials),
                    "age_binary": sys.executable,
                    "age_recipient": AGE_RECIPIENT,
                    "workspace": str(self.workspace),
                    "maximum_database_bytes": 1024,
                    "maximum_uploads_bytes": 1024,
                    "maximum_audit_bytes": 1024,
                    "maximum_snapshot_age_seconds": 30,
                    "signing_source_site": "webapp_fi",
                    "source_signing_private_key_file": str(self.signing_key),
                }
            )
            + "\n",
        )
        self.source_env = self.root / "source.env"
        secure_write(
            self.source_env,
            "\n".join(
                (
                    f"RELEASE_SHA={RELEASE_SHA}",
                    f"EXPECTED_ALEMBIC_REVISION={ALEMBIC_REVISION}",
                    f"WA_FI_SNAPSHOT_DATA_ROOT={self.data_root}",
                    f"WA_FI_SNAPSHOT_STATE_ROOT={self.state_root}",
                    f"WA_FI_SNAPSHOT_CAPTURE_ENV_FILE={self.capture_env}",
                    f"WA_FI_SNAPSHOT_TRANSPORT_CONFIG={self.transport_config}",
                    "WA_FI_SNAPSHOT_DB_CONTAINER=trading_bot_db",
                    "WA_FI_SNAPSHOT_APP_CONTAINER=trading_bot_app",
                    "WA_FI_SNAPSHOT_MAX_AGE_SECONDS=30",
                    "WA_FI_SNAPSHOT_CAPTURE_ATTEMPTS=1",
                )
            )
            + "\n",
        )
        self.capture_script = self.root / "capture.py"
        self.transport_script = self.root / "transport.py"
        secure_write(self.capture_script, "# trusted fixture\n", executable=True)
        secure_write(self.transport_script, "# trusted fixture\n", executable=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def arguments(self, *extra: str) -> list[str]:
        return [
            "--source-env",
            str(self.source_env),
            "--capture-script",
            str(self.capture_script),
            "--transport-script",
            str(self.transport_script),
            "--capture-python",
            sys.executable,
            "--transport-python",
            sys.executable,
            "--generation",
            GENERATION,
            *extra,
        ]

    def capture_payload(self) -> dict[str, object]:
        artifact_directory = self.data_root / "snapshots" / GENERATION
        secure_directory(artifact_directory)
        database = artifact_directory / "database.dump"
        uploads = artifact_directory / "uploads.tar.gz"
        audit = artifact_directory / "audit.tar.gz"
        manifest = artifact_directory / "snapshot-artifacts.json"
        for path in (database, uploads, audit, manifest):
            secure_write(path, b"fixture")
        return {
            "status": "ready",
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "snapshot_id": GENERATION,
            "release_sha": RELEASE_SHA,
            "alembic_revision": ALEMBIC_REVISION,
            "artifact_dir": str(artifact_directory),
            "manifest_path": str(manifest),
            "audit_included": True,
            "source_db_snapshot_started_at": "2026-07-29T12:00:00Z",
            "source_capture_completed_at": "2026-07-29T12:00:01Z",
            "source_database_capture": {"client_mode": "short_lived_read_only", "client_lifetime_seconds": 1},
            "source_volume_capture": {"mode": "read_only_no_mutation"},
            "database": {"path": str(database)},
            "uploads": {"path": str(uploads)},
            "audit": {"path": str(audit)},
        }

    @staticmethod
    def descriptor(name: str) -> dict[str, object]:
        return {
            "object_key": f"webapp-ir-snapshot-2c08/{name}.age",
            "version_id": f"version-{name}",
            "ciphertext_sha256": "a" * 64,
            "ciphertext_bytes": 1,
        }

    def publish_payload(self) -> dict[str, object]:
        return {
            "status": "published",
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "source_generation": GENERATION,
            "snapshot_id": "20260729T120001Z-0123456789abcdef01234567",
            "release_sha": RELEASE_SHA,
            "alembic_revision": ALEMBIC_REVISION,
            "source_db_snapshot_started_at": "2026-07-29T12:00:00Z",
            "source_capture_completed_at": "2026-07-29T12:00:01Z",
            "published_at": "2026-07-29T12:00:02Z",
            "source_database_capture": {"client_mode": "short_lived_read_only", "client_lifetime_seconds": 1},
            "source_volume_capture": {"mode": "read_only_no_mutation"},
            "database": self.descriptor("database"),
            "uploads": self.descriptor("uploads"),
            "audit": self.descriptor("audit"),
            "manifest": self.descriptor("manifest"),
        }

    def test_default_plan_creates_no_artifact_or_remote_action(self) -> None:
        with mock.patch.object(MODULE, "run_json_command", side_effect=AssertionError("children must not run")):
            payload = MODULE.execute(MODULE.build_parser().parse_args(self.arguments()))
        self.assertEqual(payload["status"], "planned")
        self.assertFalse(payload["direct_fi_to_ir_transfer"])
        self.assertEqual(payload["remote_execution"], "none")
        self.assertFalse(payload["services_stopped"])
        self.assertFalse(payload["source_data_mutated"])
        self.assertFalse((self.data_root / "snapshots").exists())
        self.assertFalse((self.state_root / "published").exists())

    def test_apply_runs_local_capture_then_local_transport_and_writes_new_receipt(self) -> None:
        calls: list[tuple[list[str], str]] = []

        def fake_run(arguments: list[str], *, label: str) -> dict[str, object]:
            calls.append((arguments, label))
            if label == "local source artifact capture":
                return self.capture_payload()
            self.assertEqual(label, "local immutable Object Storage publisher")
            return self.publish_payload()

        with mock.patch.object(MODULE, "run_json_command", side_effect=fake_run):
            payload = MODULE.execute(MODULE.build_parser().parse_args(self.arguments("--apply")))

        self.assertEqual(payload["status"], "published")
        self.assertEqual(len(calls), 2)
        capture_arguments, capture_label = calls[0]
        transport_arguments, transport_label = calls[1]
        self.assertEqual(capture_label, "local source artifact capture")
        self.assertEqual(transport_label, "local immutable Object Storage publisher")
        self.assertIn("--include-audit", capture_arguments)
        self.assertIn("--apply", capture_arguments)
        self.assertEqual(transport_arguments[2], "publish")
        self.assertIn("--audit-archive", transport_arguments)
        rendered = " ".join(capture_arguments + transport_arguments).lower()
        self.assertNotIn("ssh", rendered)
        self.assertNotIn("scp", rendered)
        self.assertNotIn("rsync", rendered)
        receipt = Path(str(payload["receipt_path"]))
        self.assertTrue(receipt.is_file())
        self.assertEqual(oct(receipt.stat().st_mode & 0o777), "0o600")
        recorded = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(recorded["status"], "published")
        self.assertFalse(recorded["direct_fi_to_ir_transfer"])
        self.assertEqual(recorded["remote_execution"], "none")

    def test_capture_contract_mismatch_stops_before_transport_publish(self) -> None:
        calls: list[str] = []
        bad_capture = {
            "status": "ready",
            "source_site": "webapp_ir",
            "destination_site": "webapp_ir",
            "snapshot_id": GENERATION,
            "release_sha": RELEASE_SHA,
            "alembic_revision": ALEMBIC_REVISION,
        }

        def fake_run(_arguments: list[str], *, label: str) -> dict[str, object]:
            calls.append(label)
            return bad_capture

        with mock.patch.object(MODULE, "run_json_command", side_effect=fake_run):
            with self.assertRaisesRegex(MODULE.SourceSnapshotPublishError, "unexpected source_site"):
                MODULE.execute(MODULE.build_parser().parse_args(self.arguments("--apply")))
        self.assertEqual(calls, ["local source artifact capture"])

    def test_existing_publication_lock_rejects_an_overlapping_apply(self) -> None:
        with MODULE.publication_lock(self.state_root):
            with self.assertRaisesRegex(MODULE.SourceSnapshotPublishError, "already active"):
                MODULE.execute(MODULE.build_parser().parse_args(self.arguments("--apply")))

    def test_source_env_must_be_root_only_before_any_child_runs(self) -> None:
        os.chmod(self.source_env, 0o644)
        with mock.patch.object(MODULE, "run_json_command", side_effect=AssertionError("children must not run")):
            with self.assertRaisesRegex(MODULE.SourceSnapshotPublishError, "source snapshot env must be root-only"):
                MODULE.execute(MODULE.build_parser().parse_args(self.arguments()))


if __name__ == "__main__":
    unittest.main()
