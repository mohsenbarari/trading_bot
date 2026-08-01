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
assert RENDER_SPEC and RENDER_SPEC.loader and VERIFY_SPEC and VERIFY_SPEC.loader
RENDER = importlib.util.module_from_spec(RENDER_SPEC)
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
RENDER_SPEC.loader.exec_module(RENDER)
VERIFY_SPEC.loader.exec_module(VERIFY)


SOURCE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
PATCH_SHA = "c598cba8c7dd14a123b4c7a70a7bd6cdfd1011f8"
WEBAPP_INITDATA_TOKEN = "123456789:emergency-webapp-hmac-token-only"


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
        values = {
            key: expected for key, expected in VERIFY.EXPECTED.items()
        }
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
            SYNC_API_KEY="forbidden",
        )
        self.assertIn("forbidden runtime keys: SYNC_API_KEY", VERIFY.verify_values(values))


if __name__ == "__main__":
    unittest.main()
