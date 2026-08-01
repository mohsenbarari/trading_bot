from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import preflight_production_writer_lease_guard as preflight


RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
IMAGE_ID = "sha256:b518196d78189d8eb06950ae3cb1ece2e096331859de25f812016bd0fdacd271"
IMAGE_REF = "trading_bot_base_iran"
ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)


class WriterGuardPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.release_root = self.base / "srv" / "trading-bot-three-site" / "releases" / RELEASE_SHA
        self.release_root.mkdir(parents=True)
        _write(self.release_root / "main.py", "main release source\n")
        _write(self.release_root / "core" / "background_job_authority.py", "authority release source\n")
        _write(self.release_root / "scripts" / "production_writer_lease_agent.py", "# staged agent\n")
        _write(self.release_root / "scripts" / "preflight_production_writer_lease_guard.py", "# staged preflight\n")
        unit_template = (
            self.release_root
            / "deploy"
            / "systemd"
            / "trading-bot-production-writer-fi-lease-guard.service.template"
        )
        unit_template.parent.mkdir(parents=True)
        shutil.copyfile(
            ROOT / "deploy" / "systemd" / "trading-bot-production-writer-fi-lease-guard.service.template",
            unit_template,
        )
        os.chmod(unit_template, 0o644)
        self.compose_file = self.release_root / "docker-compose.yml"
        _write(self.compose_file, "services: {}\n")
        self.runtime_env = self.base / "secure" / "wa-fi-runtime.env"
        _write(self.runtime_env, "UNUSED=1\n", mode=0o600)
        self.secret = self.base / "secure" / "witness.secret"
        self.public_key = self.base / "secure" / "witness.pub"
        _write(self.secret, "s" * 32, mode=0o600)
        _write(self.public_key, base64.b64encode(b"p" * 32).decode("ascii"), mode=0o600)
        self.lease_file = self.base / "state" / "writer-lease.json"
        self.agent_config = self.base / "etc" / "agent.json"
        self.preflight_config = self.base / "etc" / "preflight.json"
        self.unit_file = self.base / "systemd" / "trading-bot-production-writer-fi-lease-guard.service"
        self.unit_patch = mock.patch.object(preflight, "APPROVED_UNIT_FILE", self.unit_file)
        self.unit_patch.start()
        self.addCleanup(self.unit_patch.stop)
        self.app_container_id = "a" * 64
        self.sync_container_id = "b" * 64
        self._write_configs()
        self._write_expected_unit()

    def _write_configs(self) -> None:
        agent_config = {
            "schema": "production-writer-lease-agent-v1",
            "mode": "writer",
            "site": "webapp_fi",
            "lease_file": str(self.lease_file),
            "runtime": {
                "compose_file": str(self.compose_file),
                "env_file": str(self.runtime_env),
                "selection_env_file": None,
                "services": ["app", "sync_worker"],
            },
            "witness": {
                "url": "https://witness.example.test",
                "key_id": "wa-fi-key",
                "secret_file": str(self.secret),
                "public_key_file": str(self.public_key),
                "ca_bundle": None,
                "timeout_seconds": 3,
                "lease_duration_seconds": 60,
                "safety_margin_seconds": 15,
                "renew_interval_seconds": 10,
            },
        }
        _write(self.agent_config, json.dumps(agent_config), mode=0o600)
        preflight_config = {
            "schema": preflight.PREFLIGHT_SCHEMA,
            "release_sha": RELEASE_SHA,
            "release_root": str(self.release_root),
            "agent_config": str(self.agent_config),
            "preflight_config": str(self.preflight_config),
            "unit_file": str(self.unit_file),
            "lease_file": str(self.lease_file),
            "runtime_env_file": str(self.runtime_env),
            "witness_timing": {
                "lease_duration_seconds": 60,
                "safety_margin_seconds": 15,
                "renew_interval_seconds": 10,
            },
            "release_files": [
                {
                    "path": "main.py",
                    "sha256": hashlib.sha256((self.release_root / "main.py").read_bytes()).hexdigest(),
                },
                {
                    "path": "core/background_job_authority.py",
                    "sha256": hashlib.sha256(
                        (self.release_root / "core" / "background_job_authority.py").read_bytes()
                    ).hexdigest(),
                },
            ],
            "runtime": {
                "compose_file": str(self.compose_file),
                "compose_project": "trading_bot",
                "services": [
                    {
                        "name": "app",
                        "container_name": "trading_bot_app",
                        "container_id": self.app_container_id,
                        "image_ref": IMAGE_REF,
                        "image_id": IMAGE_ID,
                    },
                    {
                        "name": "sync_worker",
                        "container_name": "trading_bot_sync_worker",
                        "container_id": self.sync_container_id,
                        "image_ref": IMAGE_REF,
                        "image_id": IMAGE_ID,
                    },
                ],
            },
        }
        _write(self.preflight_config, json.dumps(preflight_config), mode=0o600)

    def _write_expected_unit(self) -> None:
        config = preflight._load_config(self.preflight_config)
        self.unit_file.parent.mkdir(parents=True, exist_ok=True)
        self.unit_file.write_bytes(preflight._render_expected_unit(config))
        os.chmod(self.unit_file, 0o644)

    def _compose_payload(self, *, app_restart: str = "no") -> str:
        return json.dumps(
            {
                "services": {
                    "app": {
                        "image": IMAGE_REF,
                        "container_name": "trading_bot_app",
                        "restart": app_restart,
                        "pull_policy": "never",
                    },
                    "sync_worker": {
                        "image": IMAGE_REF,
                        "container_name": "trading_bot_sync_worker",
                        "restart": "no",
                        "pull_policy": "never",
                    },
                }
            }
        )

    def _container_output(self, *, name: str, container_id: str, health: str) -> str:
        return "\n".join(
            (
                container_id,
                IMAGE_ID,
                IMAGE_REF,
                "true",
                "running",
                "no",
                health,
                name,
                "trading_bot",
            )
        )

    def _runtime_results(self) -> list[subprocess.CompletedProcess[str]]:
        return [
            _completed(self._compose_payload()),
            _completed(f"{IMAGE_ID}\n"),
            _completed(self._container_output(name="app", container_id=self.app_container_id, health="healthy")),
            _completed(f"{IMAGE_ID}\n"),
            _completed(
                self._container_output(
                    name="sync_worker", container_id=self.sync_container_id, health="none"
                )
            ),
        ]

    def test_legacy_fi_guard_is_retired_before_any_runtime_inspection(self) -> None:
        self.assertFalse(self.lease_file.exists())
        with mock.patch.object(preflight.subprocess, "run") as run:
            with self.assertRaisesRegex(preflight.WriterGuardPreflightError, "retired"):
                preflight.run(config_path=self.preflight_config, phase="stage")
        run.assert_not_called()

    def test_legacy_fi_guard_cannot_be_revived_by_a_compose_variant(self) -> None:
        with mock.patch.object(
            preflight.subprocess,
            "run",
            return_value=_completed(self._compose_payload(app_restart="always")),
        ) as run:
            with self.assertRaisesRegex(preflight.WriterGuardPreflightError, "retired"):
                preflight.run(config_path=self.preflight_config, phase="stage")

        run.assert_not_called()

    def test_stage_rejects_an_installed_unit_that_differs_from_the_pinned_template(self) -> None:
        self.unit_file.write_bytes(self.unit_file.read_bytes() + b"# unauthorized change\n")
        with mock.patch.object(preflight.subprocess, "run") as run:
            with self.assertRaisesRegex(preflight.WriterGuardPreflightError, "does not match"):
                preflight.run(config_path=self.preflight_config, phase="stage")
        run.assert_not_called()

    def test_legacy_fi_guard_remains_retired_when_its_timing_is_changed(self) -> None:
        payload = json.loads(self.agent_config.read_text(encoding="utf-8"))
        payload["witness"]["lease_duration_seconds"] = 180
        payload["witness"]["renew_interval_seconds"] = 30
        _write(self.agent_config, json.dumps(payload), mode=0o600)
        with mock.patch.object(preflight.subprocess, "run") as run:
            with self.assertRaisesRegex(preflight.WriterGuardPreflightError, "retired"):
                preflight.run(config_path=self.preflight_config, phase="stage")
        run.assert_not_called()

    def test_rendered_systemd_unit_is_valid(self) -> None:
        unit = self.unit_file.read_text(encoding="utf-8")
        self.assertIn("--phase stage", unit)
        self.assertNotIn("--phase guard-start", unit)
        self.assertFalse(self.lease_file.exists())
        if shutil.which("systemd-analyze") is None:
            self.skipTest("systemd-analyze is unavailable")
        result = subprocess.run(
            ["systemd-analyze", "verify", str(self.unit_file)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_legacy_fi_guard_remains_retired_even_with_a_local_lease(self) -> None:
        stale = {
            "schema": "production-writer-lease-v1",
            "holder_site": "webapp_fi",
            "writer_epoch": 3,
            "lease_id": "lease-3",
            "issued_at": (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat(),
            "witness_transition_id": "transition-3",
            "proof_sha256": "c" * 64,
        }
        _write(self.lease_file, json.dumps(stale), mode=0o600)
        with mock.patch.object(preflight.subprocess, "run", side_effect=self._runtime_results()):
            with self.assertRaisesRegex(preflight.WriterGuardPreflightError, "retired"):
                preflight.run(config_path=self.preflight_config, phase="guard-start")

    def test_examples_require_reviewed_container_and_image_identity_without_secret_values(self) -> None:
        payload = json.loads(
            (ROOT / "deploy" / "production" / "webapp-fi-writer-lease-guard-preflight.json.example").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["schema"], "fenced-fi-writer-preflight-v2")
        self.assertEqual(
            payload["application_release_root"].rsplit("/", 1)[-1],
            "REPLACE_WITH_NEW_TERM_FENCED_APPLICATION_GIT_SHA",
        )
        self.assertIn("term_fenced_application_evidence", payload)
        self.assertIn("REPLACE_WITH_64_HEX", payload["runtime"]["services"][0]["image_id"])
        self.assertIn("REPLACE_WITH_REVIEWED", payload["runtime"]["services"][0]["image_ref"])
        self.assertIn(
            "REPLACE_WITH_64_HEX",
            payload["release_identity"]["expected_identity_sha256"],
        )
        self.assertIn("REPLACE_WITH_64_HEX", payload["runtime_env_sha256"])
        self.assertIn("REPLACE_WITH_REVIEWED", payload["runtime_resources"]["network_name"])
        self.assertIn(
            "@sha256:", payload["runtime"]["services"][0]["image_repo_digest"]
        )
        self.assertNotIn("secret", json.dumps(payload).lower())


if __name__ == "__main__":
    unittest.main()
