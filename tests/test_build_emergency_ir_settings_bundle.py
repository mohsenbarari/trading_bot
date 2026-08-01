import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_emergency_ir_settings_bundle.py"
SPEC = importlib.util.spec_from_file_location("build_emergency_ir_settings_bundle", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


def root_file(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


class BuildEmergencyIrSettingsBundleTests(unittest.TestCase):
    def test_default_bundle_has_exact_two_member_layout_and_activation_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-settings-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            settings = root / "trading_settings.json"
            token = root / "webapp-token"
            output = root / "settings.tar"
            root_file(settings, b'{"currency":"IRR","spread":1}', 0o640)
            root_file(token, b"123456789:emergency-webapp-hmac-token-only\n")

            result = BUILDER.build_settings_bundle(
                output=output,
                trading_settings=settings,
                webapp_initdata_token=token,
            )

            self.assertEqual(result["status"], "built-local-only")
            self.assertEqual(result["member_names"], ["trading_settings.json", "webapp_initdata_token"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with tarfile.open(output, mode="r:") as archive:
                self.assertEqual(
                    sorted(member.name for member in archive.getmembers()),
                    ["trading_settings.json", "webapp_initdata_token"],
                )
                token_member = archive.extractfile("webapp_initdata_token")
                assert token_member is not None
                self.assertEqual(token_member.read(), b"123456789:emergency-webapp-hmac-token-only")
            parsed = BUILDER.activation.read_settings_bundle(
                settings_tar=output,
                profile="telegram-only",
            )
            self.assertEqual(parsed.webapp_initdata_token, "123456789:emergency-webapp-hmac-token-only")

    def test_sms_bundle_requires_exact_complete_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-settings-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            settings = root / "trading_settings.json"
            token = root / "webapp-token"
            api_key = root / "sms-api-key"
            template_id = root / "template-id"
            parameter = root / "template-parameter"
            output = root / "settings.tar"
            root_file(settings, b'{"currency":"IRR"}')
            root_file(token, b"123456789:emergency-webapp-hmac-token-only")
            root_file(api_key, b"smsir-test-key")
            root_file(template_id, b"585147")
            root_file(parameter, b"CODE")

            result = BUILDER.build_settings_bundle(
                output=output,
                trading_settings=settings,
                webapp_initdata_token=token,
                profile="sms-otp",
                smsir_api_key=api_key,
                smsir_otp_template_id=template_id,
                smsir_otp_template_parameter=parameter,
            )

            self.assertEqual(result["profile"], "sms-otp")
            self.assertEqual(
                result["member_names"],
                [
                    "smsir_api_key",
                    "smsir_otp_template_id",
                    "smsir_otp_template_parameter",
                    "trading_settings.json",
                    "webapp_initdata_token",
                ],
            )
            parsed = BUILDER.activation.read_settings_bundle(settings_tar=output, profile="sms-otp")
            self.assertEqual(parsed.smsir_otp_template_id, "585147")
            self.assertEqual(parsed.smsir_otp_template_parameter, "CODE")

    def test_rejects_sms_drift_private_token_and_existing_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-settings-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            settings = root / "trading_settings.json"
            token = root / "webapp-token"
            output = root / "settings.tar"
            root_file(settings, b'{"currency":"IRR"}')
            root_file(token, b"token", 0o644)
            with self.assertRaisesRegex(BUILDER.EmergencySettingsBundleError, "WebApp initData token"):
                BUILDER.build_settings_bundle(
                    output=output,
                    trading_settings=settings,
                    webapp_initdata_token=token,
                )
            root_file(token, b"token")
            root_file(output, b"preserve")
            with self.assertRaisesRegex(BUILDER.EmergencySettingsBundleError, "overwrite"):
                BUILDER.build_settings_bundle(
                    output=output,
                    trading_settings=settings,
                    webapp_initdata_token=token,
                )
            self.assertEqual(output.read_bytes(), b"preserve")
            with self.assertRaisesRegex(BUILDER.EmergencySettingsBundleError, "complete"):
                BUILDER.build_settings_bundle(
                    output=root / "sms-settings.tar",
                    trading_settings=settings,
                    webapp_initdata_token=token,
                    profile="sms-otp",
                    smsir_api_key=root / "missing",
                )

    def test_rejects_whitespace_that_would_change_a_secret_value(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-settings-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            settings = root / "trading_settings.json"
            token = root / "webapp-token"
            root_file(settings, b'{"currency":"IRR"}')
            root_file(token, b" accidental-leading-space\n")
            with self.assertRaisesRegex(BUILDER.EmergencySettingsBundleError, "WebApp initData token"):
                BUILDER.build_settings_bundle(
                    output=root / "settings.tar",
                    trading_settings=settings,
                    webapp_initdata_token=token,
                )

    def test_cli_requires_isolated_interpreter(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("python3 -I -B", completed.stdout)


if __name__ == "__main__":
    unittest.main()
