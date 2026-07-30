from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install_webapp_ir_dark_listener.py"
SPEC = importlib.util.spec_from_file_location("install_webapp_ir_dark_listener", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_file(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


class FakeNginx:
    def __init__(self, failures: dict[tuple[str, ...], int] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        normalized = tuple(command)
        self.calls.append(normalized)
        return subprocess.CompletedProcess(normalized, self.failures.get(normalized, 0), "", "test failure")


class WebappIrDarkListenerInstallTests(unittest.TestCase):
    def make_fixture(self, *, site_state: str = "absent") -> dict[str, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)

        certbot_root = root / "letsencrypt"
        lineage = certbot_root / "live" / MODULE.SERVER_NAME
        archive = certbot_root / "archive" / MODULE.SERVER_NAME
        lineage.mkdir(parents=True)
        archive.mkdir(parents=True)
        certbot_root.chmod(0o755)
        (certbot_root / "live").chmod(0o755)
        (certbot_root / "archive").chmod(0o755)
        lineage.chmod(0o755)
        archive.chmod(0o700)
        archive_certificate = archive / "fullchain1.pem"
        archive_key = archive / "privkey1.pem"
        write_file(
            archive_certificate,
            "-----BEGIN CERTIFICATE-----\nlocal cert\n-----END CERTIFICATE-----\n",
            0o644,
        )
        write_file(
            archive_key,
            "-----BEGIN PRIVATE KEY-----\nlocal key\n-----END PRIVATE KEY-----\n",
            0o600,
        )
        (lineage / MODULE.CERTIFICATE_NAME).symlink_to(archive_certificate)
        (lineage / MODULE.KEY_NAME).symlink_to(archive_key)

        tls = root / "tls"
        tls.mkdir()
        tls.chmod(0o700)
        certificate = tls / MODULE.CERTIFICATE_NAME
        key = tls / MODULE.KEY_NAME
        write_file(certificate, "old certificate\n", 0o600)
        write_file(key, "old private key\n", 0o600)

        site_directory = root / "nginx" / "sites-available"
        enabled_directory = root / "nginx" / "sites-enabled"
        site_directory.mkdir(parents=True)
        enabled_directory.mkdir(parents=True)
        site_directory.chmod(0o755)
        enabled_directory.chmod(0o755)
        site = site_directory / MODULE.SITE_NAME
        enabled = enabled_directory / MODULE.SITE_NAME

        receipts = root / "receipts"
        receipts.mkdir()
        receipts.chmod(0o700)

        binary = root / "bin" / "nginx"
        write_file(binary, "#!/bin/false\n", 0o700)
        config = root / "dark-listener.env"
        values = {
            "WA_IR_DARK_LISTENER_SERVER_NAME": MODULE.SERVER_NAME,
            "WA_IR_DARK_LISTENER_CERTBOT_ROOT": str(certbot_root),
            "WA_IR_DARK_LISTENER_CERTBOT_LINEAGE": str(lineage),
            "WA_IR_DARK_LISTENER_TLS_ROOT": str(tls),
            "WA_IR_DARK_LISTENER_CERTIFICATE_PATH": str(certificate),
            "WA_IR_DARK_LISTENER_CERTIFICATE_KEY_PATH": str(key),
            "WA_IR_DARK_LISTENER_SITE_PATH": str(site),
            "WA_IR_DARK_LISTENER_ENABLED_PATH": str(enabled),
            "WA_IR_DARK_LISTENER_RECEIPT_PATH": str(receipts / "dark-listener.json"),
        }
        write_file(config, "".join(f"{key}={value}\n" for key, value in values.items()), 0o600)
        template = ROOT / "deploy/production/nginx-webapp-ir-standby-dark-https.conf.template"
        if site_state == "legacy":
            write_file(site, "server { return 444; }\n", 0o644)
            enabled.symlink_to(site)
        elif site_state == "dark":
            rendered = MODULE.render_dark_listener(template, MODULE.load_dark_listener_config(config))
            write_file(site, rendered.decode("utf-8"), 0o644)
            enabled.symlink_to(site)
        elif site_state != "absent":
            raise ValueError(f"unknown site state: {site_state}")
        return {
            "root": root,
            "certbot_root": certbot_root,
            "lineage": lineage,
            "archive_certificate": archive_certificate,
            "archive_key": archive_key,
            "tls": tls,
            "certificate": certificate,
            "key": key,
            "site": site,
            "enabled": enabled,
            "receipts": receipts,
            "config": config,
            "binary": binary,
            "template": template,
        }

    def test_plan_is_local_only_and_does_not_mutate_files(self) -> None:
        fixture = self.make_fixture(site_state="dark")
        fake = FakeNginx()
        before = {
            "certificate": fixture["certificate"].read_bytes(),
            "key": fixture["key"].read_bytes(),
            "site": fixture["site"].read_bytes(),
            "enabled": os.readlink(fixture["enabled"]),
        }

        result = MODULE.install_dark_listener(
            config_path=fixture["config"],
            template_path=fixture["template"],
            nginx_binary=fixture["binary"],
            apply=False,
            command_runner=fake,
        )

        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["listener_mode"], "dark-503")
        self.assertFalse(result["external_route_changed"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(fixture["certificate"].read_bytes(), before["certificate"])
        self.assertEqual(fixture["key"].read_bytes(), before["key"])
        self.assertEqual(fixture["site"].read_bytes(), before["site"])
        self.assertEqual(os.readlink(fixture["enabled"]), before["enabled"])
        self.assertFalse((fixture["receipts"] / "dark-listener.json").exists())

    def test_apply_installs_local_pair_and_creates_fenced_site(self) -> None:
        fixture = self.make_fixture(site_state="absent")
        fake = FakeNginx()
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

        result = MODULE.install_dark_listener(
            config_path=fixture["config"],
            template_path=fixture["template"],
            nginx_binary=fixture["binary"],
            apply=True,
            command_runner=fake,
            now=now,
        )

        binary = str(fixture["binary"])
        self.assertEqual(fake.calls, [(binary, "-t"), (binary, "-s", "reload")])
        self.assertEqual(result["status"], "reloaded")
        self.assertFalse(result["external_route_changed"])
        self.assertEqual(fixture["certificate"].read_bytes(), fixture["archive_certificate"].read_bytes())
        self.assertEqual(fixture["key"].read_bytes(), fixture["archive_key"].read_bytes())
        self.assertEqual(stat.S_IMODE(fixture["certificate"].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(fixture["key"].stat().st_mode), 0o600)
        self.assertTrue(fixture["enabled"].is_symlink())
        self.assertEqual(fixture["enabled"].resolve(), fixture["site"].resolve())
        site = fixture["site"].read_text(encoding="utf-8")
        self.assertIn("return 503;", site)
        self.assertIn("location = /__standby/health", site)
        self.assertNotIn("proxy_pass", site)
        self.assertEqual(stat.S_IMODE(fixture["site"].stat().st_mode), 0o644)

        receipt = json.loads((fixture["receipts"] / "dark-listener.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "reloaded")
        self.assertEqual(receipt["operation"], "install")
        self.assertEqual(receipt["listener_mode"], "dark-503")
        self.assertEqual(receipt["completed_at"], "2026-07-30T12:00:00Z")
        self.assertFalse(receipt["external_route_changed"])

    def test_rejects_conflicting_existing_site_without_touching_tls_or_nginx(self) -> None:
        fixture = self.make_fixture(site_state="legacy")
        fake = FakeNginx()
        before = (
            fixture["certificate"].read_bytes(),
            fixture["key"].read_bytes(),
            fixture["site"].read_bytes(),
        )

        with self.assertRaisesRegex(MODULE.DarkListenerError, "refusing to overwrite a non-dark"):
            MODULE.install_dark_listener(
                config_path=fixture["config"],
                template_path=fixture["template"],
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )

        self.assertEqual(fake.calls, [])
        self.assertEqual(fixture["certificate"].read_bytes(), before[0])
        self.assertEqual(fixture["key"].read_bytes(), before[1])
        self.assertEqual(fixture["site"].read_bytes(), before[2])
        self.assertFalse((fixture["receipts"] / "dark-listener.json").exists())

    def test_accepts_an_idempotent_preexisting_pinned_dark_site(self) -> None:
        fixture = self.make_fixture(site_state="dark")
        fake = FakeNginx()
        before_site = fixture["site"].read_bytes()
        before_mode = stat.S_IMODE(fixture["site"].stat().st_mode)
        before_inode = fixture["site"].stat().st_ino

        result = MODULE.install_dark_listener(
            config_path=fixture["config"],
            template_path=fixture["template"],
            nginx_binary=fixture["binary"],
            apply=True,
            command_runner=fake,
        )

        binary = str(fixture["binary"])
        self.assertEqual(fake.calls, [(binary, "-t"), (binary, "-s", "reload")])
        self.assertEqual(result["status"], "reloaded")
        self.assertEqual(fixture["site"].read_bytes(), before_site)
        self.assertEqual(stat.S_IMODE(fixture["site"].stat().st_mode), before_mode)
        self.assertEqual(fixture["site"].stat().st_ino, before_inode)
        self.assertEqual(fixture["enabled"].resolve(), fixture["site"].resolve())

    def test_rejects_preexisting_dark_bytes_with_an_unexpected_site_mode(self) -> None:
        fixture = self.make_fixture(site_state="dark")
        fixture["site"].chmod(0o600)
        fake = FakeNginx()

        with self.assertRaisesRegex(MODULE.DarkListenerError, "root-owned mode 0644"):
            MODULE.install_dark_listener(
                config_path=fixture["config"],
                template_path=fixture["template"],
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )

        self.assertEqual(fake.calls, [])

    def test_failed_nginx_test_restores_prior_site_link_and_tls_without_reload(self) -> None:
        fixture = self.make_fixture(site_state="dark")
        binary = str(fixture["binary"])
        fake = FakeNginx({(binary, "-t"): 1})
        before = {
            "certificate": fixture["certificate"].read_bytes(),
            "key": fixture["key"].read_bytes(),
            "site": fixture["site"].read_bytes(),
            "enabled": os.readlink(fixture["enabled"]),
        }

        with self.assertRaisesRegex(MODULE.DarkListenerError, "nginx configuration test failed"):
            MODULE.install_dark_listener(
                config_path=fixture["config"],
                template_path=fixture["template"],
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )

        self.assertEqual(fake.calls, [(binary, "-t")])
        self.assertEqual(fixture["certificate"].read_bytes(), before["certificate"])
        self.assertEqual(fixture["key"].read_bytes(), before["key"])
        self.assertEqual(fixture["site"].read_bytes(), before["site"])
        self.assertEqual(os.readlink(fixture["enabled"]), before["enabled"])
        self.assertFalse((fixture["receipts"] / "dark-listener.json").exists())

    def test_failed_nginx_test_removes_only_the_new_site_and_link(self) -> None:
        fixture = self.make_fixture(site_state="absent")
        binary = str(fixture["binary"])
        fake = FakeNginx({(binary, "-t"): 1})
        before_certificate = fixture["certificate"].read_bytes()
        before_key = fixture["key"].read_bytes()

        with self.assertRaisesRegex(MODULE.DarkListenerError, "nginx configuration test failed"):
            MODULE.install_dark_listener(
                config_path=fixture["config"],
                template_path=fixture["template"],
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )

        self.assertEqual(fake.calls, [(binary, "-t")])
        self.assertFalse(fixture["site"].exists())
        self.assertFalse(fixture["enabled"].exists())
        self.assertEqual(fixture["certificate"].read_bytes(), before_certificate)
        self.assertEqual(fixture["key"].read_bytes(), before_key)

    def test_failed_nginx_reload_restores_and_reloads_prior_local_state(self) -> None:
        fixture = self.make_fixture(site_state="dark")
        binary = str(fixture["binary"])

        class ReloadFailsOnce(FakeNginx):
            def __call__(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                normalized = tuple(command)
                self.calls.append(normalized)
                failed = normalized == (binary, "-s", "reload") and self.calls.count(normalized) == 1
                return subprocess.CompletedProcess(normalized, 1 if failed else 0, "", "reload failure")

        fake = ReloadFailsOnce()
        before = (
            fixture["certificate"].read_bytes(),
            fixture["key"].read_bytes(),
            fixture["site"].read_bytes(),
            os.readlink(fixture["enabled"]),
        )
        with self.assertRaisesRegex(MODULE.DarkListenerError, "nginx reload failed"):
            MODULE.install_dark_listener(
                config_path=fixture["config"],
                template_path=fixture["template"],
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )

        self.assertEqual(
            fake.calls,
            [(binary, "-t"), (binary, "-s", "reload"), (binary, "-t"), (binary, "-s", "reload")],
        )
        self.assertEqual(fixture["certificate"].read_bytes(), before[0])
        self.assertEqual(fixture["key"].read_bytes(), before[1])
        self.assertEqual(fixture["site"].read_bytes(), before[2])
        self.assertEqual(os.readlink(fixture["enabled"]), before[3])
        self.assertFalse((fixture["receipts"] / "dark-listener.json").exists())

    def test_certbot_deploy_hook_refreshes_only_tls_and_requires_exact_environment(self) -> None:
        fixture = self.make_fixture(site_state="legacy")
        fake = FakeNginx()
        before_site = fixture["site"].read_bytes()
        before_link = os.readlink(fixture["enabled"])
        environment = {
            "RENEWED_LINEAGE": str(fixture["lineage"]),
            "RENEWED_DOMAINS": MODULE.SERVER_NAME,
        }
        with patch.dict(os.environ, environment, clear=False):
            result = MODULE.install_dark_listener(
                config_path=fixture["config"],
                template_path=fixture["root"] / "unused-by-certbot-hook.template",
                nginx_binary=fixture["binary"],
                apply=True,
                certbot_deploy_hook=True,
                command_runner=fake,
            )

        binary = str(fixture["binary"])
        self.assertEqual(fake.calls, [(binary, "-t"), (binary, "-s", "reload")])
        self.assertEqual(result["operation"], "certbot-deploy-hook")
        self.assertEqual(fixture["certificate"].read_bytes(), fixture["archive_certificate"].read_bytes())
        self.assertEqual(fixture["key"].read_bytes(), fixture["archive_key"].read_bytes())
        self.assertEqual(fixture["site"].read_bytes(), before_site)
        self.assertEqual(os.readlink(fixture["enabled"]), before_link)

        with patch.dict(os.environ, {"RENEWED_LINEAGE": "/wrong", "RENEWED_DOMAINS": MODULE.SERVER_NAME}, clear=False):
            with self.assertRaisesRegex(MODULE.DarkListenerError, "renewed lineage"):
                MODULE.install_dark_listener(
                    config_path=fixture["config"],
                    template_path=fixture["template"],
                    nginx_binary=fixture["binary"],
                    apply=True,
                    certbot_deploy_hook=True,
                    command_runner=fake,
                )
        self.assertEqual(fake.calls, [(binary, "-t"), (binary, "-s", "reload")])

    def test_rejects_certbot_source_that_resolves_outside_its_local_root(self) -> None:
        fixture = self.make_fixture()
        external = fixture["root"] / "external.pem"
        write_file(
            external,
            "-----BEGIN CERTIFICATE-----\nexternal\n-----END CERTIFICATE-----\n",
            0o644,
        )
        (fixture["lineage"] / MODULE.CERTIFICATE_NAME).unlink()
        (fixture["lineage"] / MODULE.CERTIFICATE_NAME).symlink_to(external)
        fake = FakeNginx()

        with self.assertRaisesRegex(MODULE.DarkListenerError, "must remain inside the local Certbot root"):
            MODULE.install_dark_listener(
                config_path=fixture["config"],
                template_path=fixture["template"],
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )
        self.assertEqual(fake.calls, [])

    def test_rejects_template_with_an_upstream_without_running_nginx(self) -> None:
        fixture = self.make_fixture()
        unsafe = fixture["root"] / "unsafe.template"
        source = fixture["template"].read_text(encoding="utf-8")
        write_file(
            unsafe,
            source.replace("return 503;", "return 503;\n        proxy_pass http://127.0.0.1:18000;", 1),
            0o644,
        )
        fake = FakeNginx()

        with self.assertRaisesRegex(MODULE.DarkListenerError, "upstream, sync path, or foreign source material"):
            MODULE.install_dark_listener(
                config_path=fixture["config"],
                template_path=unsafe,
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )
        self.assertEqual(fake.calls, [])

    def test_implementation_has_no_remote_or_certbot_invocation_capability(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "manage_three_site_mvp_arvan",
            "urlopen(",
            "requests.",
            "scp ",
            "ssh ",
            "subprocess.run([\"certbot\"",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
