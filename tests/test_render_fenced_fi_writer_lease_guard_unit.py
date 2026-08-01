from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import preflight_fenced_fi_writer as preflight
from scripts import render_fenced_fi_writer_lease_guard_unit as renderer


RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


class RenderFencedFiWriterLeaseGuardUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control_root = self.root / "srv" / "trading-bot-three-site" / "control-releases" / ("a" * 40)
        self.application_root = (
            self.root / "srv" / "trading-bot-three-site" / "releases" / RELEASE_SHA
        )
        self.term_parent = self.root / "var" / "lib" / "trading-bot-three-site" / "writer-terms"
        self.config_path = self.root / "etc" / "webapp-fi-fenced-writer-preflight.json"
        self.agent_config = self.root / "etc" / "production-writer-lease-agent.webapp-fi-fenced-2c08.json"
        self.runtime_env = self.root / "secure" / "wa-fi-fenced-writer-runtime.env"
        self.unit_file = self.root / "systemd" / preflight.FENCED_UNIT_NAME
        self.control_root.mkdir(parents=True)
        self.application_root.mkdir(parents=True)
        self.term_parent.mkdir(parents=True)
        template = self.control_root / preflight.FENCED_UNIT_TEMPLATE_RELATIVE
        template.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / preflight.FENCED_UNIT_TEMPLATE_RELATIVE, template)
        os.chmod(template, 0o644)
        _write(self.agent_config, "{}\n")
        _write(self.runtime_env, "UNUSED=1\n")
        self.unit_patch = mock.patch.object(preflight, "APPROVED_FENCED_UNIT_FILE", self.unit_file)
        self.term_patch = mock.patch.object(preflight, "FENCED_TERM_PARENT_DIRECTORY", self.term_parent)
        self.unit_patch.start()
        self.term_patch.start()
        self.addCleanup(self.term_patch.stop)
        self.addCleanup(self.unit_patch.stop)
        self._write_config()

    def _write_config(self) -> None:
        payload = {
            "schema": preflight.PREFLIGHT_SCHEMA,
            "control_release_root": str(self.control_root),
            "application_release_root": str(self.application_root),
            "agent_config": str(self.agent_config),
            "preflight_config": str(self.config_path),
            "unit_file": str(self.unit_file),
            "lease_file": str(self.term_parent / preflight.TERM_LEASE_NAME),
            "runtime_env_file": str(self.runtime_env),
            "term_parent_directory": str(self.term_parent),
            "app_local_port": 8000,
            "runtime": {
                "compose_file": str(
                    self.control_root
                    / "deploy/production/docker-compose.webapp-fi-writer-2c08.yml"
                ),
                "compose_project": "trading_bot_wa_fi_writer_2c08",
                "services": [
                    {
                        "name": "app",
                        "container_name": "trading_bot_wa_fi_writer_2c08_app",
                        "image_ref": "trading-bot-app:2c08",
                        "image_id": "sha256:" + "b" * 64,
                    },
                    {
                        "name": "bot",
                        "container_name": "trading_bot_wa_fi_writer_2c08_bot",
                        "image_ref": "trading-bot-bot:2c08",
                        "image_id": "sha256:" + "c" * 64,
                    },
                ],
            },
        }
        _write(self.config_path, json.dumps(payload), mode=0o600)

    def test_render_uses_literal_paths_and_legacy_equivalent_hardening(self) -> None:
        config, rendered = renderer.render(self.config_path)
        unit = rendered.decode("utf-8")

        self.assertEqual(config.unit_file, self.unit_file)
        self.assertIn("WorkingDirectory=/", unit)
        self.assertIn(f"ConditionPathExists={self.control_root}/scripts/preflight_fenced_fi_writer.py", unit)
        self.assertIn(
            f"ExecStartPre=/usr/bin/python3 -I {self.control_root}/scripts/preflight_fenced_fi_writer.py --config {self.config_path} --phase guard-start",
            unit,
        )
        self.assertIn(
            f"ExecStart=/usr/bin/python3 -I {self.control_root}/scripts/production_writer_lease_agent.py --config {self.agent_config} guard",
            unit,
        )
        self.assertNotIn("EnvironmentFile=", unit)
        self.assertNotIn("${", unit)
        for line in (
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=full",
            "ProtectHome=read-only",
            "ReadWritePaths=/var/lib/trading-bot-three-site",
            "Requires=docker.service",
            "Conflicts=trading-bot-production-writer-fi-lease-guard.service",
        ):
            self.assertIn(line, unit)

    def test_install_is_atomic_targeted_and_requires_explicit_replacement(self) -> None:
        self.unit_file.parent.mkdir(parents=True)
        result = renderer.install(self.config_path, replace_existing=False)
        self.assertEqual(result["unit_file"], str(self.unit_file))
        self.assertTrue(self.unit_file.exists())
        self.assertEqual(stat.S_IMODE(self.unit_file.stat().st_mode), 0o644)
        with self.assertRaisesRegex(renderer.FencedFiWriterUnitRenderError, "replace-existing"):
            renderer.install(self.config_path, replace_existing=False)
        renderer.install(self.config_path, replace_existing=True)

    def test_preflight_rejects_an_installed_unit_with_environment_expansion(self) -> None:
        config, rendered = renderer.render(self.config_path)
        self.unit_file.parent.mkdir(parents=True, exist_ok=True)
        self.unit_file.write_bytes(
            rendered.replace(b"WorkingDirectory=/", b"WorkingDirectory=${WA_FI_CONTROL_RELEASE_ROOT}")
        )
        os.chmod(self.unit_file, 0o644)
        with self.assertRaisesRegex(
            preflight.FencedFiWriterPreflightError,
            "does not match the pinned rendered template",
        ):
            preflight._validate_installed_unit(config)

    def test_rendered_unit_passes_systemd_static_verification_when_available(self) -> None:
        if shutil.which("systemd-analyze") is None:
            self.skipTest("systemd-analyze is unavailable")
        self.unit_file.parent.mkdir(parents=True, exist_ok=True)
        self.unit_file.write_bytes(renderer.render(self.config_path)[1])
        os.chmod(self.unit_file, 0o644)
        result = subprocess.run(
            ["systemd-analyze", "verify", str(self.unit_file)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
