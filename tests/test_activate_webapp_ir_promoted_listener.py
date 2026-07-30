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
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/activate_webapp_ir_promoted_listener.py"
SPEC = importlib.util.spec_from_file_location("activate_webapp_ir_promoted_listener", SCRIPT)
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


class WebappIrPromotedListenerActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provenance_by_receipt: dict[Path, dict] = {}
        patcher = mock.patch.object(
            MODULE,
            "load_installed_release_receipt",
            side_effect=lambda path: self.provenance_by_receipt[Path(path)],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_fixture(self) -> dict[str, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)

        release = root / "releases" / MODULE.RELEASE_SHA
        static = release / "mini_app_dist"
        static.mkdir(parents=True)
        release.chmod(0o755)
        static.chmod(0o755)
        write_file(static / "index.html", "<!doctype html>\n", 0o644)

        tls = root / "tls"
        tls.mkdir()
        tls.chmod(0o700)
        certificate = tls / "fullchain.pem"
        key = tls / "privkey.pem"
        write_file(certificate, "local certificate\n", 0o600)
        write_file(key, "local private key\n", 0o600)

        site_directory = root / "nginx" / "sites-available"
        enabled_directory = root / "nginx" / "sites-enabled"
        site_directory.mkdir(parents=True)
        enabled_directory.mkdir(parents=True)
        site_directory.chmod(0o755)
        enabled_directory.chmod(0o755)
        site = site_directory / "trading-bot"
        write_file(site, "server { return 503; }\n", 0o644)
        enabled = enabled_directory / "trading-bot"
        enabled.symlink_to(site)

        receipts = root / "receipts"
        receipts.mkdir()
        receipts.chmod(0o700)
        provenance_receipt = receipts / "release-provenance.json"
        write_file(provenance_receipt, "fixture\n", 0o600)
        self.provenance_by_receipt[provenance_receipt] = {
            "application": {
                "release_sha": MODULE.RELEASE_SHA,
                "release_root": str(release),
            },
            "control": {"release_root": str(MODULE.REPO_ROOT.resolve())},
        }

        binary = root / "bin" / "nginx"
        write_file(binary, "#!/bin/false\n", 0o700)
        config = root / "listener.env"
        values = {
            "WA_IR_LISTENER_SERVER_NAME": MODULE.SERVER_NAME,
            "WA_IR_LISTENER_APPLICATION_RELEASE_ROOT": str(release),
            "WA_IR_LISTENER_RELEASE_PROVENANCE_RECEIPT": str(provenance_receipt),
            "WA_IR_LISTENER_TLS_ROOT": str(tls),
            "WA_IR_LISTENER_CERTIFICATE_PATH": str(certificate),
            "WA_IR_LISTENER_CERTIFICATE_KEY_PATH": str(key),
            "WA_IR_LISTENER_SITE_PATH": str(site),
            "WA_IR_LISTENER_ENABLED_PATH": str(enabled),
            "WA_IR_LISTENER_RECEIPT_PATH": str(receipts / "activation.json"),
        }
        write_file(config, "".join(f"{key}={value}\n" for key, value in values.items()), 0o600)
        return {
            "root": root,
            "release": release,
            "tls": tls,
            "certificate": certificate,
            "key": key,
            "site": site,
            "enabled": enabled,
            "receipts": receipts,
            "provenance_receipt": provenance_receipt,
            "config": config,
            "binary": binary,
            "template": ROOT / "deploy/production/nginx-webapp-ir-promoted-2c08-https.conf.template",
        }

    def rewrite_config(self, fixture: dict[str, Path], **changes: str) -> None:
        values = MODULE._read_config_values(fixture["config"])
        values.update(changes)
        write_file(
            fixture["config"],
            "".join(f"{key}={value}\n" for key, value in values.items()),
            0o600,
        )

    def test_plan_validates_only_and_cannot_change_external_routing(self) -> None:
        fixture = self.make_fixture()
        fake = FakeNginx()
        before = fixture["site"].read_bytes()

        result = MODULE.activate_listener(
            config_path=fixture["config"],
            template_path=fixture["template"],
            nginx_binary=fixture["binary"],
            apply=False,
            command_runner=fake,
        )

        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["external_route_changed"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(fixture["site"].read_bytes(), before)
        self.assertFalse((fixture["receipts"] / "activation.json").exists())

    def test_apply_tests_and_reloads_local_nginx_before_emitting_receipt(self) -> None:
        fixture = self.make_fixture()
        fake = FakeNginx()
        now = datetime(2026, 7, 29, 19, 0, tzinfo=timezone.utc)

        result = MODULE.activate_listener(
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
        rendered = fixture["site"].read_text(encoding="utf-8")
        self.assertIn("proxy_pass http://127.0.0.1:18000;", rendered)
        self.assertIn(str(fixture["certificate"]), rendered)
        self.assertIn(str(fixture["release"] / "mini_app_dist"), rendered)
        self.assertEqual(stat.S_IMODE(fixture["site"].stat().st_mode), 0o600)

        receipt_path = fixture["receipts"] / "activation.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "reloaded")
        self.assertEqual(receipt["release_sha"], MODULE.RELEASE_SHA)
        self.assertEqual(receipt["loopback_upstream"], MODULE.LOOPBACK_UPSTREAM)
        self.assertEqual(receipt["activated_at"], "2026-07-29T19:00:00Z")
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)

    def test_failed_nginx_configuration_test_restores_prior_listener_without_reload(self) -> None:
        fixture = self.make_fixture()
        binary = str(fixture["binary"])
        fake = FakeNginx({(binary, "-t"): 1})
        before = fixture["site"].read_bytes()

        with self.assertRaisesRegex(MODULE.ListenerActivationError, "nginx configuration test failed"):
            MODULE.activate_listener(
                config_path=fixture["config"],
                template_path=fixture["template"],
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )

        self.assertEqual(fake.calls, [(binary, "-t")])
        self.assertEqual(fixture["site"].read_bytes(), before)
        self.assertEqual(stat.S_IMODE(fixture["site"].stat().st_mode), 0o644)
        self.assertFalse((fixture["receipts"] / "activation.json").exists())

    def test_failed_reload_restores_and_reloads_prior_listener(self) -> None:
        fixture = self.make_fixture()
        binary = str(fixture["binary"])

        class ReloadFailsOnce(FakeNginx):
            def __call__(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                normalized = tuple(command)
                self.calls.append(normalized)
                failure = 1 if normalized == (binary, "-s", "reload") and self.calls.count(normalized) == 1 else 0
                return subprocess.CompletedProcess(normalized, failure, "", "reload failed")

        fake = ReloadFailsOnce()
        before = fixture["site"].read_bytes()
        with self.assertRaisesRegex(MODULE.ListenerActivationError, "nginx reload failed"):
            MODULE.activate_listener(
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
        self.assertEqual(fixture["site"].read_bytes(), before)
        self.assertFalse((fixture["receipts"] / "activation.json").exists())

    def test_rejects_tls_outside_local_root_and_unknown_source_site_material(self) -> None:
        fixture = self.make_fixture()
        self.rewrite_config(fixture, WA_IR_LISTENER_CERTIFICATE_PATH=str(fixture["release"] / "mini_app_dist/index.html"))
        with self.assertRaisesRegex(MODULE.ListenerActivationError, "remain under WA-IR local TLS root"):
            MODULE.load_listener_config(fixture["config"])

        values = MODULE._read_config_values(fixture["config"])
        values["FI_TLS_PRIVATE_KEY"] = "/not/allowed"
        write_file(
            fixture["config"],
            "".join(f"{key}={value}\n" for key, value in values.items()),
            0o600,
        )
        with self.assertRaisesRegex(MODULE.ListenerActivationError, "unexpected FI_TLS_PRIVATE_KEY"):
            MODULE.load_listener_config(fixture["config"])

    def test_rejects_same_named_application_root_not_bound_by_receipt_before_nginx(self) -> None:
        fixture = self.make_fixture()
        alternate = fixture["root"] / "alternate-releases" / MODULE.RELEASE_SHA
        alternate_static = alternate / "mini_app_dist"
        alternate_static.mkdir(parents=True)
        alternate.chmod(0o755)
        alternate_static.chmod(0o755)
        write_file(alternate_static / "index.html", "<!doctype html>\n", 0o644)
        self.rewrite_config(
            fixture,
            WA_IR_LISTENER_APPLICATION_RELEASE_ROOT=str(alternate),
        )
        fake = FakeNginx()

        with self.assertRaisesRegex(
            MODULE.ListenerActivationError,
            "does not bind this application release root",
        ):
            MODULE.activate_listener(
                config_path=fixture["config"],
                template_path=fixture["template"],
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )

        self.assertEqual(fake.calls, [])

    def test_rejects_unsafe_template_without_running_nginx(self) -> None:
        fixture = self.make_fixture()
        unsafe = fixture["root"] / "unsafe.template"
        source = fixture["template"].read_text(encoding="utf-8")
        write_file(unsafe, source.replace(MODULE.LOOPBACK_UPSTREAM, "https://198.51.100.10:18000"), 0o644)
        fake = FakeNginx()

        with self.assertRaisesRegex(MODULE.ListenerActivationError, "unsafe upstream"):
            MODULE.activate_listener(
                config_path=fixture["config"],
                template_path=unsafe,
                nginx_binary=fixture["binary"],
                apply=True,
                command_runner=fake,
            )
        self.assertEqual(fake.calls, [])

    def test_implementation_has_no_route_or_cross_host_client(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "manage_three_site_mvp_arvan_routing",
            "route_webapp_ir_from_promotion_proof",
            "urlopen(",
            "requests.",
            "scp ",
            "ssh ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
