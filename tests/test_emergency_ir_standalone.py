from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RENDER_SPEC = importlib.util.spec_from_file_location(
    "render_emergency_ir_standalone_env",
    ROOT / "scripts" / "render_emergency_ir_standalone_env.py",
)
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "verify_emergency_ir_standalone",
    ROOT / "scripts" / "verify_emergency_ir_standalone.py",
)
IMAGE_VERIFY_SPEC = importlib.util.spec_from_file_location(
    "verify_emergency_ir_image_provenance",
    ROOT / "scripts" / "verify_emergency_ir_image_provenance.py",
)
SMS_EGRESS_IMAGE_VERIFY_SPEC = importlib.util.spec_from_file_location(
    "verify_emergency_ir_sms_egress_image",
    ROOT / "scripts" / "verify_emergency_ir_sms_egress_image.py",
)
assert (
    RENDER_SPEC and RENDER_SPEC.loader and VERIFY_SPEC and VERIFY_SPEC.loader
    and IMAGE_VERIFY_SPEC and IMAGE_VERIFY_SPEC.loader
    and SMS_EGRESS_IMAGE_VERIFY_SPEC and SMS_EGRESS_IMAGE_VERIFY_SPEC.loader
)
RENDER = importlib.util.module_from_spec(RENDER_SPEC)
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
RENDER_SPEC.loader.exec_module(RENDER)
VERIFY_SPEC.loader.exec_module(VERIFY)
IMAGE_VERIFY = importlib.util.module_from_spec(IMAGE_VERIFY_SPEC)
IMAGE_VERIFY_SPEC.loader.exec_module(IMAGE_VERIFY)
SMS_EGRESS_IMAGE_VERIFY = importlib.util.module_from_spec(SMS_EGRESS_IMAGE_VERIFY_SPEC)
SMS_EGRESS_IMAGE_VERIFY_SPEC.loader.exec_module(SMS_EGRESS_IMAGE_VERIFY)


SOURCE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
PATCH_SHA = "c598cba8c7dd14a123b4c7a70a7bd6cdfd1011f8"
WEBAPP_INITDATA_TOKEN = "123456789:emergency-webapp-hmac-token-only"
SMSIR_API_KEY = "test-only-smsir-api-key"


def runtime_values(profile: str = VERIFY.AUTH_PROFILE_TELEGRAM_ONLY) -> dict[str, str]:
    values = dict(VERIFY.COMMON_EXPECTED)
    if profile == VERIFY.AUTH_PROFILE_TELEGRAM_ONLY:
        values.update(VERIFY.TELEGRAM_ONLY_EXPECTED)
    else:
        values.update(VERIFY.SMS_OTP_EXPECTED)
    values.update(
        SOURCE_RELEASE_SHA=SOURCE_SHA,
        EMERGENCY_PATCH_SHA=PATCH_SHA,
        RELEASE_SHA=PATCH_SHA,
        EMERGENCY_APP_IMAGE=f"trading_bot_emergency_ir_app:{PATCH_SHA}",
        EMERGENCY_POSTGRES_IMAGE="trading_bot_emergency_ir_postgres:15-alpine-a1b2",
        EMERGENCY_REDIS_IMAGE="trading_bot_emergency_ir_redis:7-alpine-a1b2",
        POSTGRES_USER="emergency_webapp",
        POSTGRES_DB="trading_bot_emergency",
        POSTGRES_PASSWORD="not-a-real-secret",
        DATABASE_URL="postgresql+asyncpg://emergency_webapp:not-a-real-secret@db/trading_bot_emergency",
        SYNC_DATABASE_URL="postgresql://emergency_webapp:not-a-real-secret@db/trading_bot_emergency",
        REDIS_URL="redis://redis:6379/0",
        JWT_SECRET_KEY="not-a-real-secret",
        DEV_API_KEY="not-a-real-secret",
        WEBAPP_INITDATA_BOT_TOKEN=WEBAPP_INITDATA_TOKEN,
        FRONTEND_URL="https://coin.gold-trade.ir",
        PUBLIC_WEBAPP_URL="https://coin.gold-trade.ir",
    )
    if profile == VERIFY.AUTH_PROFILE_SMS_OTP:
        values.update(
            SMSIR_API_KEY=SMSIR_API_KEY,
            SMSIR_OTP_TEMPLATE_ID="123456",
            SMSIR_OTP_TEMPLATE_PARAMETER="CODE",
            OTP_DELIVERY_STATE_SECRET="test-only-otp-delivery-state-secret-0123456789",
            EMERGENCY_SMS_EGRESS_IMAGE=(
                f"trading_bot_emergency_ir_sms_egress:{PATCH_SHA}"
            ),
        )
    return values


class EmergencyStandaloneTests(unittest.TestCase):
    def test_compose_is_strictly_isolated(self) -> None:
        failures = VERIFY.verify_compose(ROOT / "deploy/emergency-ir/docker-compose.standalone.yml")
        self.assertEqual(failures, [])

    def test_nginx_blocks_cross_site_surfaces(self) -> None:
        failures = VERIFY.verify_nginx(ROOT / "deploy/emergency-ir/nginx.standalone.conf.template")
        self.assertEqual(failures, [])

    def test_session_reset_preserves_users_and_makes_inherited_auth_terminal(self) -> None:
        reset = ROOT / "deploy/emergency-ir/reset-emergency-sessions.sql"
        self.assertEqual(VERIFY.verify_session_reset(reset), [])
        self.assertNotIn("delete ", reset.read_text(encoding="utf-8").lower())

    def test_image_provenance_rejects_staging_or_embedded_sync_secret(self) -> None:
        payload = {
            "RepoTags": [f"trading_bot_emergency_ir_app:{PATCH_SHA}"],
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": PATCH_SHA,
                    "org.goldtrade.emergency.base-revision": SOURCE_SHA,
                    "org.goldtrade.emergency.scope": "ir-standalone",
                    "org.goldtrade.emergency.auth": "webapp-initdata-and-local-sms-otp",
                },
                "Env": ["PATH=/usr/local/bin"],
            },
        }
        self.assertEqual(
            IMAGE_VERIFY.verify_payload(
                payload=payload,
                source_release_sha=SOURCE_SHA,
                emergency_patch_sha=PATCH_SHA,
            ),
            [],
        )
        payload["RepoTags"].append("trading_bot_three_site_staging:unsafe")
        payload["Config"]["Env"].append("SYNC_API_KEY=forbidden")
        failures = IMAGE_VERIFY.verify_payload(
            payload=payload,
            source_release_sha=SOURCE_SHA,
            emergency_patch_sha=PATCH_SHA,
        )
        self.assertTrue(any("staging" in failure for failure in failures))
        self.assertTrue(any("SYNC_API_KEY" in failure for failure in failures))

    def test_nginx_verifier_requires_a_certificate_for_the_default_tls_vhost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "nginx.conf"
            source = (ROOT / "deploy/emergency-ir/nginx.standalone.conf.template").read_text(
                encoding="utf-8"
            )
            candidate.write_text(source.replace("    ssl_certificate /etc/trading-bot-emergency/acme/config/live/emergency-coin-gold-trade-ir/fullchain.pem;\n", "", 1), encoding="utf-8")
            self.assertIn(
                "both TLS virtual hosts must load the pinned emergency certificate",
                VERIFY.verify_nginx(candidate),
            )

    def test_renderer_generates_independent_credentials_and_rejects_staging_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "runtime.env"
            with mock.patch.object(RENDER, "RUNTIME_PATH", output), mock.patch.object(RENDER.os, "geteuid", return_value=0):
                RENDER.render(
                    output=output,
                    source_release_sha=SOURCE_SHA,
                    emergency_patch_sha=PATCH_SHA,
                    app_image=f"trading_bot_emergency_ir_app:{PATCH_SHA}",
                    postgres_image="trading_bot_emergency_ir_postgres:15-alpine-a1b2",
                    redis_image="trading_bot_emergency_ir_redis:7-alpine-a1b2",
                    webapp_initdata_token=WEBAPP_INITDATA_TOKEN,
                )
                values = {}
                for line in output.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith("#"):
                        key, value = line.split("=", 1)
                        values[key] = value
                self.assertEqual(values["BACKGROUND_JOBS_ENABLED"], "false")
                self.assertEqual(values["DATABASE_URL"].split("@")[-1], "db/trading_bot_emergency")
                self.assertNotIn("SYNC_API_KEY", values)
                self.assertEqual(values["WEBAPP_INITDATA_BOT_TOKEN"], WEBAPP_INITDATA_TOKEN)
                self.assertEqual(values["TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH"], "true")
                self.assertEqual(values["EMERGENCY_IR_STANDALONE"], "true")
                self.assertEqual(values["EMERGENCY_AUTH_PROFILE"], "telegram-only")
                self.assertNotIn("SMSIR_API_KEY", values)
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                with self.assertRaises(RENDER.EmergencyEnvError):
                    RENDER.render(
                        output=output,
                        source_release_sha=SOURCE_SHA,
                        emergency_patch_sha=PATCH_SHA,
                        app_image=f"trading_bot_three_site_staging:{PATCH_SHA}",
                        postgres_image="trading_bot_emergency_ir_postgres:15-alpine-a1b2",
                        redis_image="trading_bot_emergency_ir_redis:7-alpine-a1b2",
                        webapp_initdata_token=WEBAPP_INITDATA_TOKEN,
                    )

    def test_verifier_rejects_cross_site_credential(self) -> None:
        values = runtime_values()
        values["SYNC_API_KEY"] = "forbidden"
        self.assertIn("forbidden runtime keys: SYNC_API_KEY", VERIFY.verify_values(values))

    def test_sms_otp_overlay_is_explicit_and_strictly_bounded(self) -> None:
        self.assertEqual(
            VERIFY.verify_sms_otp_compose(
                ROOT / "deploy/emergency-ir/docker-compose.sms-otp.yml"
            ),
            [],
        )
        self.assertEqual(
            VERIFY.verify_sms_egress_relay(
                ROOT / "deploy/emergency-ir/sms-egress.nginx.conf"
            ),
            [],
        )
        self.assertEqual(
            VERIFY.verify_sms_otp_nginx(
                ROOT / "deploy/emergency-ir/nginx.sms-otp.conf.template",
                ROOT / "deploy/emergency-ir/nginx.sms-otp.rate-limit.conf",
            ),
            [],
        )

    def test_renderer_requires_explicit_sms_opt_in_and_renders_stage6_direct_sms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runtime.env"
            with mock.patch.object(RENDER, "RUNTIME_PATH", output), mock.patch.object(
                RENDER.os, "geteuid", return_value=0
            ):
                with self.assertRaises(RENDER.EmergencyEnvError):
                    RENDER.render(
                        output=output,
                        source_release_sha=SOURCE_SHA,
                        emergency_patch_sha=PATCH_SHA,
                        app_image=f"trading_bot_emergency_ir_app:{PATCH_SHA}",
                        postgres_image="trading_bot_emergency_ir_postgres:15-alpine-a1b2",
                        redis_image="trading_bot_emergency_ir_redis:7-alpine-a1b2",
                        webapp_initdata_token=WEBAPP_INITDATA_TOKEN,
                        smsir_api_key=SMSIR_API_KEY,
                    )
                RENDER.render(
                    output=output,
                    source_release_sha=SOURCE_SHA,
                    emergency_patch_sha=PATCH_SHA,
                    app_image=f"trading_bot_emergency_ir_app:{PATCH_SHA}",
                    postgres_image="trading_bot_emergency_ir_postgres:15-alpine-a1b2",
                    redis_image="trading_bot_emergency_ir_redis:7-alpine-a1b2",
                    webapp_initdata_token=WEBAPP_INITDATA_TOKEN,
                    enable_sms_otp=True,
                    smsir_api_key=SMSIR_API_KEY,
                    smsir_otp_template_id="123456",
                    smsir_otp_template_parameter="CODE",
                    sms_egress_image=f"trading_bot_emergency_ir_sms_egress:{PATCH_SHA}",
                )
            values = {
                key: value
                for line in output.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
                for key, value in [line.split("=", 1)]
            }
            self.assertEqual(values["EMERGENCY_AUTH_PROFILE"], "sms-otp")
            self.assertEqual(values["TELEGRAM_LOGIN_OTP_ENABLED"], "true")
            self.assertEqual(values["OTP_SMS_AUTO_FALLBACK_ENABLED"], "false")
            self.assertEqual(values["SMSIR_BASE_URL"], "http://sms-egress:8080")
            self.assertEqual(values["SMSIR_TRUST_ENV"], "false")
            values["EMERGENCY_RUNTIME_ENV_FILE"] = (
                "/etc/trading-bot-emergency/standalone/runtime.env"
            )
            self.assertEqual(
                VERIFY.verify_values(values, expected_profile=VERIFY.AUTH_PROFILE_SMS_OTP),
                [],
            )

    def test_sms_otp_verifier_rejects_generic_proxy_and_wrong_relay_image(self) -> None:
        values = runtime_values(VERIFY.AUTH_PROFILE_SMS_OTP)
        values["HTTPS_PROXY"] = "http://unsafe-proxy:3128"
        values["EMERGENCY_SMS_EGRESS_IMAGE"] = "trading_bot_emergency_ir_sms_egress:wrong"
        failures = VERIFY.verify_values(
            values, expected_profile=VERIFY.AUTH_PROFILE_SMS_OTP
        )
        self.assertTrue(any("HTTPS_PROXY" in failure for failure in failures))
        self.assertTrue(any("SMS egress image" in failure for failure in failures))

    def test_sms_egress_image_provenance_rejects_secret_embedding(self) -> None:
        payload = {
            "RepoTags": [f"trading_bot_emergency_ir_sms_egress:{PATCH_SHA}"],
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": PATCH_SHA,
                    "org.goldtrade.emergency.base-revision": SOURCE_SHA,
                    "org.goldtrade.emergency.scope": "ir-standalone-sms-egress",
                    "org.goldtrade.emergency.egress": "fixed-api.sms.ir-v1-send-verify",
                },
                "Env": ["PATH=/usr/local/bin"],
            },
        }
        self.assertEqual(
            SMS_EGRESS_IMAGE_VERIFY.verify_payload(
                payload=payload,
                source_release_sha=SOURCE_SHA,
                emergency_patch_sha=PATCH_SHA,
            ),
            [],
        )
        payload["Config"]["Env"].append("SMSIR_API_KEY=forbidden")
        self.assertTrue(
            any(
                "SMSIR_API_KEY" in failure
                for failure in SMS_EGRESS_IMAGE_VERIFY.verify_payload(
                    payload=payload,
                    source_release_sha=SOURCE_SHA,
                    emergency_patch_sha=PATCH_SHA,
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
