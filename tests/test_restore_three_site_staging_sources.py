from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.restore_three_site_staging_sources import (
    SourceRestoreError,
    _load_legacy_restore_bundle,
    confirmation_phrase,
    execute,
    verify_restore_input,
)


def _evidence():
    return {
        "schema": "three-site-staging-source-freeze-v1",
        "campaign_id": "11111111-1111-4111-8111-111111111111",
        "target_release_sha": "a" * 40,
        "project_name": "trading_bot_staging",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_roles": [
            {"source_role": "webapp_fi", "app_service": "app", "source_release_sha": "b" * 40}
        ],
        "previously_running_services": ["app", "db", "redis", "sync_worker"],
        "stopped_services": ["app", "sync_worker"],
        "running_services": ["db", "redis"],
        "postgres": {
            "system_id": "8000000000000000001",
            "alembic_revision": "f1b6e7f8a9dc",
            "database_fingerprint_sha256": "c" * 64,
            "database_row_count": 2,
            "public_table_count": 3,
        },
        "redis_observation": {
            "dbsize": 1, "appendonly": True, "lastsave_unix": 1700000000, "restore": False
        },
        "legacy_restore_bundle": {
            "schema": "three-site-staging-legacy-restore-bundle-reference-v1",
            "path": "/secure/legacy-restore-bundle.json",
            "sha256": "d" * 64,
            "size": 1024,
        },
    }


class RestoreThreeSiteStagingSourcesTests(unittest.TestCase):
    def _materialize_bundle(self, directory: str, evidence: dict) -> dict[str, str | int]:
        root = Path(directory)
        compose = root / "legacy-compose.yaml"
        compose_bytes = b"services:\n  app:\n    image: pinned\n"
        compose.write_bytes(compose_bytes)
        compose.chmod(0o600)
        images = {
            service: "sha256:" + format(number, "064x")
            for number, service in enumerate(evidence["previously_running_services"], 1)
        }
        manifest = {
            "schema": "three-site-staging-legacy-restore-bundle-v1",
            "campaign_id": evidence["campaign_id"],
            "target_release_sha": evidence["target_release_sha"],
            "project_name": evidence["project_name"],
            "captured_at": evidence["observed_at"],
            "source_releases": {
                row["source_role"]: row["source_release_sha"]
                for row in evidence["source_roles"]
            },
            "previously_running_services": evidence["previously_running_services"],
            "compose": {
                "path": str(compose),
                "sha256": hashlib.sha256(compose_bytes).hexdigest(),
                "size": len(compose_bytes),
            },
            "service_images": images,
        }
        manifest_path = root / "legacy-restore-bundle.json"
        manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        manifest_path.write_bytes(manifest_bytes)
        manifest_path.chmod(0o600)
        reference = {
            "schema": "three-site-staging-legacy-restore-bundle-reference-v1",
            "path": str(manifest_path),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "size": len(manifest_bytes),
        }
        evidence["legacy_restore_bundle"] = reference
        return reference

    def test_exact_previous_service_set_is_the_only_restore_target(self):
        evidence = _evidence()
        result = verify_restore_input(
            evidence,
            campaign_id=evidence["campaign_id"],
            release_sha=evidence["target_release_sha"],
            project_name="trading_bot_staging",
        )
        self.assertEqual(result["services_to_start"], ["app", "sync_worker"])

    def test_webapp_freeze_can_restore_only_from_exact_iran_staging_project(self):
        evidence = _evidence()
        evidence["project_name"] = "trading_bot_staging_iran"
        result = verify_restore_input(
            evidence,
            campaign_id=evidence["campaign_id"],
            release_sha=evidence["target_release_sha"],
            project_name="trading_bot_staging_iran",
        )
        self.assertEqual(result["services_to_start"], ["app", "sync_worker"])
        evidence["source_roles"][0]["source_role"] = "bot_fi"
        evidence["source_roles"][0]["app_service"] = "foreign_app"
        with self.assertRaisesRegex(SourceRestoreError, "cannot authorize"):
            verify_restore_input(
                evidence,
                campaign_id=evidence["campaign_id"],
                release_sha=evidence["target_release_sha"],
                project_name="trading_bot_staging_iran",
            )

    def test_evidence_can_record_a_verified_noop_stop_without_restarting_it(self):
        evidence = _evidence()
        evidence["stopped_services"].append("bot")
        result = verify_restore_input(
            evidence,
            campaign_id=evidence["campaign_id"],
            release_sha=evidence["target_release_sha"],
            project_name="trading_bot_staging",
        )
        self.assertEqual(result["services_to_start"], ["app", "sync_worker"])

    def test_evidence_rejects_missing_actual_stop_or_data_service_stop(self):
        evidence = _evidence()
        evidence["stopped_services"] = ["app"]
        with self.assertRaisesRegex(SourceRestoreError, "cannot authorize"):
            verify_restore_input(
                evidence,
                campaign_id=evidence["campaign_id"],
                release_sha=evidence["target_release_sha"],
                project_name="trading_bot_staging",
            )

    def test_malformed_service_evidence_fails_closed(self):
        evidence = _evidence()
        evidence["previously_running_services"] = [{"service": "app"}]
        with self.assertRaisesRegex(SourceRestoreError, "cannot authorize"):
            verify_restore_input(
                evidence,
                campaign_id=evidence["campaign_id"],
                release_sha=evidence["target_release_sha"],
                project_name="trading_bot_staging",
            )
        evidence = _evidence()
        evidence["stopped_services"].append("db")
        with self.assertRaisesRegex(SourceRestoreError, "cannot authorize"):
            verify_restore_input(
                evidence,
                campaign_id=evidence["campaign_id"],
                release_sha=evidence["target_release_sha"],
                project_name="trading_bot_staging",
            )

    def test_restore_requires_content_addressed_legacy_bundle(self):
        evidence = _evidence()
        evidence["legacy_restore_bundle"]["sha256"] = "0" * 63
        with self.assertRaisesRegex(SourceRestoreError, "cannot authorize"):
            verify_restore_input(
                evidence,
                campaign_id=evidence["campaign_id"],
                release_sha=evidence["target_release_sha"],
                project_name="trading_bot_staging",
            )

    def test_bundle_loader_rejects_compose_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = _evidence()
            reference = self._materialize_bundle(directory, evidence)
            _manifest, compose_path = _load_legacy_restore_bundle(reference, evidence=evidence)
            compose_path.write_text("services: {}\n", encoding="utf-8")
            with self.assertRaisesRegex(SourceRestoreError, "Compose differs"):
                _load_legacy_restore_bundle(reference, evidence=evidence)

    def test_execute_restores_from_bundle_with_no_pull_or_build(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = _evidence()
            self._materialize_bundle(directory, evidence)
            verified = verify_restore_input(
                evidence,
                campaign_id=evidence["campaign_id"],
                release_sha=evidence["target_release_sha"],
                project_name="trading_bot_staging",
            )
            args = SimpleNamespace(
                project_name="trading_bot_staging",
                confirm=confirmation_phrase(evidence["campaign_id"], verified["evidence_sha256"]),
                output=Path(directory) / "restore.json",
            )
            inventory = {
                "campaign_id": evidence["campaign_id"],
                "release_sha": evidence["target_release_sha"],
            }
            manifest, _compose = _load_legacy_restore_bundle(
                evidence["legacy_restore_bundle"], evidence=evidence
            )
            ps_round = 0
            calls: list[list[str]] = []

            def fake_run(arguments, *, timeout=30):  # noqa: ANN001, ARG001
                nonlocal ps_round
                calls.append(arguments)
                if "config" in arguments and "--services" in arguments:
                    return "app\ndb\nredis\nsync_worker"
                if "config" in arguments:
                    return ""
                if arguments[:3] == ["/usr/bin/docker", "image", "inspect"]:
                    return arguments[-1]
                if "ps" in arguments and "--status" in arguments:
                    ps_round += 1
                    return "db\nredis" if ps_round == 1 else "app\ndb\nredis\nsync_worker"
                if "ps" in arguments and "-q" in arguments:
                    return "container-" + arguments[-1]
                if arguments[:2] == ["/usr/bin/docker", "inspect"]:
                    service = arguments[-1].removeprefix("container-")
                    return manifest["service_images"][service]
                if "up" in arguments:
                    return ""
                raise AssertionError(arguments)

            with patch("scripts.restore_three_site_staging_sources._run", side_effect=fake_run):
                result = execute(args, inventory_result=inventory, evidence=evidence)
            self.assertEqual(result["status"], "restored")
            up = next(call for call in calls if "up" in call)
            self.assertIn("--no-build", up)
            self.assertEqual(up[up.index("--pull") + 1], "never")


if __name__ == "__main__":
    unittest.main()
