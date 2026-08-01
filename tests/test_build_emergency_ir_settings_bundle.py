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


def valid_settings(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "anti_abuse_daily_base": 2,
        "anti_abuse_monthly_base": 7,
        "anti_abuse_weekly_base": 5,
        "competitive_price_validation_enabled": False,
        "invitation_expiry_days": 2,
        "max_active_offers": 4,
        "offer_expire_daily_limit_after_threshold": 10,
        "offer_expire_rate_per_minute": 2,
        "offer_expiry_minutes": 2,
        "offer_max_quantity": 50,
        "offer_min_quantity": 5,
        "offer_price_warning_enabled": True,
    }
    value.update(overrides)
    import json

    return json.dumps(value, sort_keys=True).encode("utf-8")


class BuildEmergencyIrSettingsBundleTests(unittest.TestCase):
    def test_default_bundle_has_exact_two_member_layout_and_activation_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-settings-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            settings = root / "trading_settings.json"
            token = root / "webapp-token"
            output = root / "settings.tar"
            root_file(settings, valid_settings(), 0o640)
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
            root_file(settings, valid_settings())
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
            root_file(settings, valid_settings())
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

    def test_rejects_secret_or_nonpublic_trading_settings_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-settings-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            token = root / "webapp-token"
            root_file(token, b"123456789:emergency-webapp-hmac-token-only")
            secret_settings = root / "secret.json"
            root_file(secret_settings, valid_settings(bot_token="must-not-transfer"))
            with self.assertRaisesRegex(BUILDER.EmergencySettingsBundleError, "public Emergency schema"):
                BUILDER.build_settings_bundle(
                    output=root / "secret.tar",
                    trading_settings=secret_settings,
                    webapp_initdata_token=token,
                )
            malformed_settings = root / "malformed.json"
            root_file(malformed_settings, valid_settings(offer_min_quantity=True))
            with self.assertRaisesRegex(BUILDER.EmergencySettingsBundleError, "positive integer"):
                BUILDER.build_settings_bundle(
                    output=root / "malformed.tar",
                    trading_settings=malformed_settings,
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
