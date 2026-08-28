import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.render_runtime_envs import (
    TELEGRAM_PROVIDER_TOKEN_KEYS,
    build_runtime_env,
    collect_runtime_values,
    main as render_runtime_envs_main,
    write_env_file,
)


class RenderRuntimeEnvsTests(unittest.TestCase):
    def sample_values(self) -> dict[str, str]:
        return {
            "BOT_TOKEN": "bot-token",
            "BOT_USERNAME": "bot-name",
            "DATABASE_URL": "postgresql+asyncpg://db",
            "SYNC_DATABASE_URL": "postgresql+psycopg2://db",
            "POSTGRES_DB": "trading_bot_db",
            "POSTGRES_USER": "admin",
            "POSTGRES_PASSWORD": "secret",
            "REDIS_URL": "redis://redis:6379/0",
            "JWT_SECRET_KEY": "jwt-secret",
            "DEV_API_KEY": "dev-key",
            "SYNC_API_KEY": "sync-key",
            "SYNC_VERIFY_TLS": "true",
            "SYNC_CA_BUNDLE": "",
            "OBSERVABILITY_API_KEY": "obs-key",
            "CHANNEL_ID": "-100123",
            "CHANNEL_INVITE_LINK": "https://t.me/example",
            "SMSIR_API_KEY": "sms-key",
            "SMSIR_LINE_NUMBER": "3000",
            "SMSIR_OTP_TEMPLATE_ID": "123456",
            "SMSIR_OTP_TEMPLATE_PARAMETER": "CODE",
            "SMSIR_INVITATION_TEMPLATE_ID": "657938",
            "SMSIR_INVITATION_TEMPLATE_PARAMETER": "NAME",
            "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID": "162103",
            "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID": "903643",
            "ERROR_TRACKING_DSN": "dsn",
            "TRUSTED_PROXY_CIDRS": "127.0.0.1/32,::1/128,10.0.0.0/24",
            "OBSERVABILITY_TELEGRAM_USER_HASH_SALT": "salt",
            "GRAFANA_ALERT_DEFAULT_RECEIVER": "default",
            "GRAFANA_ALERT_CRITICAL_RECEIVER": "critical",
            "GRAFANA_ALERT_WARNING_RECEIVER": "warning",
            "GRAFANA_ALERT_WEBHOOK_URL": "https://alerts.example/api",
            "GRAFANA_ALERT_EMAIL_ADDRESSES": "ops@example.com",
            "WEB_PUSH_ENABLED": "true",
            "WEB_PUSH_VAPID_PUBLIC_KEY": "web-push-public",
            "WEB_PUSH_VAPID_PRIVATE_KEY": "web-push-private",
            "WEB_PUSH_VAPID_SUBJECT": "mailto:ops@example.com",
            "WEB_PUSH_TTL_SECONDS": "7200",
            "WEB_PUSH_TIMEOUT_SECONDS": "7.5",
            "PUBLIC_WEBAPP_URL": "https://app.gold-trade.ir",
            "FOREIGN_SERVER_ALIASES": "sync-foreign.example.com,foreign-app",
            "IRAN_SERVER_ALIASES": "sync-iran.example.com,iran-app",
            "IRAN_OTP_DELIVERY_STATE_SECRET": "iran-only-otp-state-secret-0123456789abcdef",
            "TELEGRAM_OTP_QUEUE_SECRET": "foreign-only-otp-queue-secret-0123456789abcdef",
            # Keep this production renderer fixture independent from the
            # ambient unit-test profile, where provider authority is enabled
            # deliberately for isolated transport tests.
            "TELEGRAM_PROVIDER_TEST_AUTHORITY": "false",
            "OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED": "true",
            "RELEASE_SHA": "abc123release",
            "DB_POOL_SIZE": "15",
            "DB_MAX_OVERFLOW": "10",
            "IRAN_DB_POOL_SIZE": "8",
            "IRAN_DB_MAX_OVERFLOW": "4",
            "DB_POOL_RECYCLE_SECONDS": "3600",
            "DB_POOL_PRE_PING": "true",
            "BACKGROUND_LEADER_LOCK_TTL_SECONDS": "90",
            "BACKGROUND_LEADER_LOCK_REFRESH_SECONDS": "30",
            "BACKGROUND_LEADER_RETRY_SECONDS": "10",
            "POSTGRES_MAX_CONNECTIONS": "500",
            "POSTGRES_SHARED_BUFFERS": "128MB",
            "POSTGRES_EFFECTIVE_CACHE_SIZE": "4GB",
            "POSTGRES_WORK_MEM": "4MB",
            "POSTGRES_MAINTENANCE_WORK_MEM": "64MB",
            "POSTGRES_RANDOM_PAGE_COST": "4",
            "POSTGRES_EFFECTIVE_IO_CONCURRENCY": "1",
            "POSTGRES_CHECKPOINT_TIMEOUT": "5min",
            "POSTGRES_MAX_WAL_SIZE": "1GB",
            "POSTGRES_MIN_WAL_SIZE": "80MB",
            "POSTGRES_WAL_BUFFERS": "4MB",
            "IRAN_POSTGRES_MAX_CONNECTIONS": "150",
            "IRAN_POSTGRES_SHARED_BUFFERS": "2GB",
            "IRAN_POSTGRES_EFFECTIVE_CACHE_SIZE": "5GB",
            "IRAN_POSTGRES_WORK_MEM": "4MB",
            "IRAN_POSTGRES_MAINTENANCE_WORK_MEM": "256MB",
            "IRAN_POSTGRES_RANDOM_PAGE_COST": "1.2",
            "IRAN_POSTGRES_EFFECTIVE_IO_CONCURRENCY": "200",
            "IRAN_POSTGRES_CHECKPOINT_TIMEOUT": "15min",
            "IRAN_POSTGRES_MAX_WAL_SIZE": "2GB",
            "IRAN_POSTGRES_MIN_WAL_SIZE": "512MB",
            "IRAN_POSTGRES_WAL_BUFFERS": "16MB",
            "REDIS_APPENDONLY": "yes",
            "REDIS_APPENDFSYNC": "everysec",
            "REDIS_MAXMEMORY": "0",
            "REDIS_MAXMEMORY_POLICY": "noeviction",
        }

    def queue_values(self) -> dict[str, str]:
        values = self.sample_values()
        values.update(
            {
                "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
                "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
                "TELEGRAM_MULTI_PUBLISHER_ENABLED": "true",
                "TELEGRAM_B2B_DISPATCH_ENABLED": "true",
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "100",
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100123",
                "TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED": "true",
                "TELEGRAM_MONITORING_BOT_TOKEN": "monitor-token",
                "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED": "true",
                "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED": "true",
                "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED": "false",
                "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED": "true",
            }
        )
        for index in range(1, 6):
            values.update(
                {
                    f"TELEGRAM_PUBLISHER_{index}_ENABLED": "true",
                    f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN": f"publisher-token-{index}",
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_BOT_ID": str(100 + index),
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_USERNAME": f"publisher_{index}_bot",
                }
            )
        return values

    def test_build_runtime_env_switches_role_and_frontend_url(self):
        values = self.sample_values()
        foreign = build_runtime_env(
            role="foreign",
            frontend_url="https://coin.362514.ir",
            public_webapp_url="https://coin.gold-trade.ir",
            foreign_server_url="https://coin.362514.ir",
            foreign_server_domain="coin.362514.ir",
            iran_server_url="https://coin.gold-trade.ir",
            iran_server_domain="coin.gold-trade.ir",
            metrics_backend="memory",
            audit_trail_path="/app/audit.jsonl",
            api_workers="2",
            values=values,
        )
        iran = build_runtime_env(
            role="iran",
            frontend_url="https://coin.gold-trade.ir",
            public_webapp_url="https://coin.gold-trade.ir",
            foreign_server_url="https://coin.362514.ir",
            foreign_server_domain="coin.362514.ir",
            iran_server_url="https://coin.gold-trade.ir",
            iran_server_domain="coin.gold-trade.ir",
            metrics_backend="memory",
            audit_trail_path="/app/audit.jsonl",
            api_workers="4",
            values=values,
        )

        self.assertEqual(foreign["SERVER_MODE"], "foreign")
        self.assertEqual(iran["SERVER_MODE"], "iran")
        self.assertEqual(foreign["API_WORKERS"], "2")
        self.assertEqual(iran["API_WORKERS"], "4")
        self.assertEqual(foreign["OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED"], "true")
        self.assertEqual(iran["OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED"], "true")
        self.assertEqual(foreign["RELEASE_SHA"], "abc123release")
        self.assertEqual(iran["RELEASE_SHA"], "abc123release")
        self.assertEqual(foreign["FRONTEND_URL"], "https://coin.362514.ir")
        self.assertEqual(iran["FRONTEND_URL"], "https://coin.gold-trade.ir")
        self.assertEqual(foreign["PUBLIC_WEBAPP_URL"], "https://app.gold-trade.ir")
        self.assertEqual(iran["PUBLIC_WEBAPP_URL"], "https://app.gold-trade.ir")
        self.assertEqual(foreign["FOREIGN_SERVER_ALIASES"], "sync-foreign.example.com,foreign-app")
        self.assertEqual(iran["IRAN_SERVER_ALIASES"], "sync-iran.example.com,iran-app")
        self.assertEqual(foreign["SYNC_VERIFY_TLS"], "true")
        self.assertEqual(iran["SYNC_VERIFY_TLS"], "true")
        self.assertEqual(foreign["SYNC_CA_BUNDLE"], "")
        self.assertEqual(iran["SYNC_CA_BUNDLE"], "")
        self.assertEqual(foreign["DB_POOL_SIZE"], "15")
        self.assertEqual(foreign["DB_MAX_OVERFLOW"], "10")
        self.assertEqual(iran["DB_POOL_SIZE"], "8")
        self.assertEqual(iran["DB_MAX_OVERFLOW"], "4")
        self.assertEqual(foreign["POSTGRES_SHARED_BUFFERS"], "128MB")
        self.assertEqual(iran["POSTGRES_MAX_CONNECTIONS"], "150")
        self.assertEqual(iran["POSTGRES_SHARED_BUFFERS"], "2GB")
        self.assertEqual(iran["POSTGRES_EFFECTIVE_CACHE_SIZE"], "5GB")
        self.assertEqual(iran["POSTGRES_WORK_MEM"], "4MB")
        self.assertEqual(iran["POSTGRES_MAINTENANCE_WORK_MEM"], "256MB")
        self.assertEqual(iran["POSTGRES_MAX_WAL_SIZE"], "2GB")
        self.assertEqual(iran["POSTGRES_MIN_WAL_SIZE"], "512MB")
        self.assertEqual(iran["REDIS_APPENDONLY"], "yes")
        self.assertEqual(iran["REDIS_APPENDFSYNC"], "everysec")
        self.assertEqual(iran["REDIS_MAXMEMORY_POLICY"], "noeviction")
        self.assertEqual(foreign["FOREIGN_SERVER_DOMAIN"], "coin.362514.ir")
        self.assertEqual(iran["IRAN_SERVER_DOMAIN"], "coin.gold-trade.ir")
        self.assertEqual(foreign["OTP_DELIVERY_STATE_SECRET"], "")
        self.assertEqual(
            iran["OTP_DELIVERY_STATE_SECRET"],
            "iran-only-otp-state-secret-0123456789abcdef",
        )
        self.assertNotIn("IRAN_OTP_DELIVERY_STATE_SECRET", foreign)
        self.assertNotIn("IRAN_OTP_DELIVERY_STATE_SECRET", iran)

    def test_build_runtime_env_uses_iran_frontend_as_public_webapp_fallback(self):
        values = self.sample_values()
        values["PUBLIC_WEBAPP_URL"] = ""

        rendered = build_runtime_env(
            role="foreign",
            frontend_url="https://foreign.example.com",
            public_webapp_url="https://webapp.example.ir",
            foreign_server_url="https://sync.example.com",
            foreign_server_domain="sync.example.com",
            iran_server_url="http://iran-app:8000",
            iran_server_domain="",
            metrics_backend="memory",
            audit_trail_path="/app/audit.jsonl",
            api_workers="2",
            values=values,
        )

        self.assertEqual(rendered["PUBLIC_WEBAPP_URL"], "https://webapp.example.ir")

    def test_role_projection_preserves_legacy_api_token_but_queue_and_iran_are_token_free(self):
        def render(role: str, values: dict[str, str]):
            return build_runtime_env(
                role=role,
                frontend_url="https://example.invalid",
                public_webapp_url="https://example.invalid",
                foreign_server_url="https://foreign.invalid",
                foreign_server_domain="foreign.invalid",
                iran_server_url="https://iran.invalid",
                iran_server_domain="iran.invalid",
                metrics_backend="memory",
                audit_trail_path="/app/audit.jsonl",
                api_workers="2",
                values=values,
            )

        legacy = render("foreign", self.sample_values())
        queue = render("foreign", self.queue_values())
        iran = render("iran", self.queue_values())

        self.assertEqual(legacy["TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER"], "legacy")
        self.assertEqual(legacy["TELEGRAM_NON_BOT_BOT_TOKEN"], "bot-token")
        self.assertEqual(queue["TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER"], "producer-only")
        self.assertEqual(queue["TELEGRAM_NON_BOT_BOT_TOKEN"], "")
        self.assertEqual(
            queue["TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED"],
            "true",
        )
        self.assertEqual(iran["TELEGRAM_DELIVERY_EXECUTION_OWNER"], "producer-only")
        self.assertEqual(iran["TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER"], "producer-only")
        self.assertEqual(iran["TELEGRAM_NON_BOT_BOT_TOKEN"], "")
        for key in TELEGRAM_PROVIDER_TOKEN_KEYS:
            self.assertIn(key, iran)
            self.assertEqual(iran[key], "", key)
        for index in range(1, 6):
            self.assertEqual(iran[f"TELEGRAM_PUBLISHER_{index}_ENABLED"], "false")
        self.assertEqual(iran["TELEGRAM_MULTI_PUBLISHER_ENABLED"], "true")
        self.assertEqual(iran["TELEGRAM_B2B_DISPATCH_ENABLED"], "true")

    def test_main_renders_both_files_from_environment(self):
        values = self.sample_values()
        with tempfile.TemporaryDirectory() as tmpdir:
            foreign_path = Path(tmpdir) / "foreign.env"
            iran_path = Path(tmpdir) / "iran.env"
            argv = [
                "render_runtime_envs.py",
                "--local-output",
                str(foreign_path),
                "--iran-output",
                str(iran_path),
                "--foreign-frontend-url",
                "https://coin.362514.ir",
                "--iran-frontend-url",
                "https://coin.gold-trade.ir",
                "--foreign-server-url",
                "https://coin.362514.ir",
                "--foreign-server-domain",
                "coin.362514.ir",
                "--iran-server-url",
                "https://coin.gold-trade.ir",
                "--iran-server-domain",
                "coin.gold-trade.ir",
                "--foreign-api-workers",
                "2",
                "--iran-api-workers",
                "4",
            ]
            with patch.dict(os.environ, values, clear=False):
                with patch("sys.argv", argv):
                    self.assertEqual(render_runtime_envs_main(), 0)

            foreign_lines = foreign_path.read_text(encoding="utf-8").splitlines()
            iran_lines = iran_path.read_text(encoding="utf-8").splitlines()

            self.assertIn("SERVER_MODE=foreign", foreign_lines)
            self.assertIn("SERVER_MODE=iran", iran_lines)
            self.assertIn("API_WORKERS=2", foreign_lines)
            self.assertIn("API_WORKERS=4", iran_lines)
            self.assertIn("DB_POOL_SIZE=8", iran_lines)
            self.assertIn("DB_MAX_OVERFLOW=4", iran_lines)
            self.assertIn("POSTGRES_MAX_CONNECTIONS=150", iran_lines)
            self.assertIn("POSTGRES_SHARED_BUFFERS=2GB", iran_lines)
            self.assertIn("POSTGRES_EFFECTIVE_CACHE_SIZE=5GB", iran_lines)
            self.assertIn("POSTGRES_WORK_MEM=4MB", iran_lines)
            self.assertIn("POSTGRES_MAINTENANCE_WORK_MEM=256MB", iran_lines)
            self.assertIn("POSTGRES_MAX_WAL_SIZE=2GB", iran_lines)
            self.assertIn("POSTGRES_MIN_WAL_SIZE=512MB", iran_lines)
            self.assertIn("POSTGRES_SHARED_BUFFERS=128MB", foreign_lines)
            self.assertIn("REDIS_APPENDONLY=yes", iran_lines)
            self.assertIn("REDIS_APPENDFSYNC=everysec", iran_lines)
            self.assertIn("DB_POOL_SIZE=15", foreign_lines)
            self.assertIn("FRONTEND_URL=https://coin.362514.ir", foreign_lines)
            self.assertIn("FRONTEND_URL=https://coin.gold-trade.ir", iran_lines)
            self.assertIn("PUBLIC_WEBAPP_URL=https://app.gold-trade.ir", foreign_lines)
            self.assertIn("PUBLIC_WEBAPP_URL=https://app.gold-trade.ir", iran_lines)
            self.assertIn("FOREIGN_SERVER_ALIASES=sync-foreign.example.com,foreign-app", foreign_lines)
            self.assertIn("IRAN_SERVER_ALIASES=sync-iran.example.com,iran-app", iran_lines)
            self.assertIn("IRAN_SERVER_URL=https://coin.gold-trade.ir", foreign_lines)
            self.assertIn("FOREIGN_SERVER_URL=https://coin.362514.ir", iran_lines)
            self.assertIn("SMSIR_API_KEY=", foreign_lines)
            self.assertNotIn("SMSIR_API_KEY=sms-key", foreign_lines)
            self.assertIn("OTP_SMS_AUTO_FALLBACK_ENABLED=false", foreign_lines)
            self.assertIn("SMSIR_API_KEY=sms-key", iran_lines)
            self.assertIn("SMSIR_OTP_TEMPLATE_ID=123456", iran_lines)
            self.assertIn("SMSIR_OTP_TEMPLATE_PARAMETER=CODE", iran_lines)
            self.assertIn(
                "TELEGRAM_OTP_QUEUE_SECRET=foreign-only-otp-queue-secret-0123456789abcdef",
                foreign_lines,
            )
            self.assertIn("TELEGRAM_OTP_QUEUE_SECRET=", iran_lines)
            self.assertNotIn(
                "TELEGRAM_OTP_QUEUE_SECRET=foreign-only-otp-queue-secret-0123456789abcdef",
                iran_lines,
            )
            self.assertIn("SMSIR_INVITATION_TEMPLATE_ID=657938", iran_lines)
            self.assertIn("SMSIR_INVITATION_TEMPLATE_PARAMETER=NAME", iran_lines)
            self.assertIn("SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID=162103", iran_lines)
            self.assertIn("SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID=903643", iran_lines)
            self.assertIn("WEB_PUSH_ENABLED=true", foreign_lines)
            self.assertIn("WEB_PUSH_ENABLED=true", iran_lines)
            self.assertIn("WEB_PUSH_VAPID_PUBLIC_KEY=web-push-public", iran_lines)
            self.assertIn("WEB_PUSH_VAPID_PRIVATE_KEY=web-push-private", iran_lines)
            self.assertIn("WEB_PUSH_VAPID_SUBJECT=mailto:ops@example.com", iran_lines)
            self.assertIn("WEB_PUSH_TTL_SECONDS=7200", iran_lines)
            self.assertIn("WEB_PUSH_TIMEOUT_SECONDS=7.5", iran_lines)
            self.assertIn("OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED=true", foreign_lines)
            self.assertIn("OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED=true", iran_lines)
            self.assertIn("RELEASE_SHA=abc123release", foreign_lines)
            self.assertIn("RELEASE_SHA=abc123release", iran_lines)
            self.assertIn("OTP_DELIVERY_STATE_SECRET=", foreign_lines)
            self.assertIn(
                "OTP_DELIVERY_STATE_SECRET=iran-only-otp-state-secret-0123456789abcdef",
                iran_lines,
            )
            self.assertNotIn(
                "IRAN_OTP_DELIVERY_STATE_SECRET=iran-only-otp-state-secret-0123456789abcdef",
                foreign_lines,
            )

    def test_repeated_source_render_is_idempotent_and_keeps_master_immutable(self):
        values = self.sample_values()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "master.env"
            foreign_path = root / "runtime" / "foreign.env"
            iran_path = root / "runtime" / "iran.env"
            source_path.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            source_before = source_path.read_bytes()
            source_digest = hashlib.sha256(source_before).hexdigest()
            argv = [
                "render_runtime_envs.py",
                "--source-env-file",
                str(source_path),
                "--local-output",
                str(foreign_path),
                "--iran-output",
                str(iran_path),
                "--foreign-frontend-url",
                "https://coin.362514.ir",
                "--iran-frontend-url",
                "https://coin.gold-trade.ir",
                "--foreign-server-url",
                "https://coin.362514.ir",
                "--foreign-server-domain",
                "coin.362514.ir",
                "--iran-server-url",
                "https://coin.gold-trade.ir",
                "--iran-server-domain",
                "coin.gold-trade.ir",
            ]

            rendered_runs: list[tuple[bytes, bytes]] = []
            for _ in range(2):
                with patch.dict(os.environ, {}, clear=True), patch("sys.argv", argv):
                    self.assertEqual(render_runtime_envs_main(), 0)
                self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), source_digest)
                rendered_runs.append((foreign_path.read_bytes(), iran_path.read_bytes()))

            self.assertEqual(rendered_runs[0], rendered_runs[1])
            self.assertIn(b"SMSIR_API_KEY=sms-key\n", source_before)
            self.assertIn(b"SMSIR_API_KEY=\n", rendered_runs[-1][0])
            self.assertNotIn(b"SMSIR_API_KEY=sms-key\n", rendered_runs[-1][0])
            self.assertIn(b"SMSIR_API_KEY=sms-key\n", rendered_runs[-1][1])
            self.assertEqual(stat.S_IMODE(foreign_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(iran_path.stat().st_mode), 0o600)

    def test_source_output_alias_is_rejected_before_writing(self):
        values = self.sample_values()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "master.env"
            iran_path = root / "iran.env"
            source_path.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            source_before = source_path.read_bytes()
            argv = [
                "render_runtime_envs.py",
                "--source-env-file",
                str(source_path),
                "--local-output",
                str(source_path),
                "--iran-output",
                str(iran_path),
                "--foreign-frontend-url",
                "https://coin.362514.ir",
                "--iran-frontend-url",
                "https://coin.gold-trade.ir",
                "--foreign-server-url",
                "https://coin.362514.ir",
                "--foreign-server-domain",
                "coin.362514.ir",
                "--iran-server-url",
                "https://coin.gold-trade.ir",
                "--iran-server-domain",
                "coin.gold-trade.ir",
            ]

            with patch.dict(os.environ, {}, clear=True), patch("sys.argv", argv):
                with self.assertRaisesRegex(SystemExit, "source must be different"):
                    render_runtime_envs_main()

            self.assertEqual(source_path.read_bytes(), source_before)
            self.assertFalse(iran_path.exists())

    def test_source_output_symlink_alias_is_rejected(self):
        values = self.sample_values()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "master.env"
            alias_path = root / "master-alias.env"
            iran_path = root / "iran.env"
            source_path.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            alias_path.symlink_to(source_path)
            argv = [
                "render_runtime_envs.py",
                "--source-env-file",
                str(source_path),
                "--local-output",
                str(alias_path),
                "--iran-output",
                str(iran_path),
                "--foreign-frontend-url",
                "https://coin.362514.ir",
                "--iran-frontend-url",
                "https://coin.gold-trade.ir",
                "--foreign-server-url",
                "https://coin.362514.ir",
                "--foreign-server-domain",
                "coin.362514.ir",
                "--iran-server-url",
                "https://coin.gold-trade.ir",
                "--iran-server-domain",
                "coin.gold-trade.ir",
            ]
            with patch.dict(os.environ, {}, clear=True), patch("sys.argv", argv):
                with self.assertRaisesRegex(SystemExit, "source must be different"):
                    render_runtime_envs_main()

            self.assertTrue(alias_path.is_symlink())
            self.assertFalse(iran_path.exists())

    def test_equal_runtime_outputs_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "runtime.env"
            argv = [
                "render_runtime_envs.py",
                "--local-output",
                str(output_path),
                "--iran-output",
                str(output_path),
                "--foreign-frontend-url",
                "https://coin.362514.ir",
                "--iran-frontend-url",
                "https://coin.gold-trade.ir",
                "--foreign-server-url",
                "https://coin.362514.ir",
                "--foreign-server-domain",
                "coin.362514.ir",
                "--iran-server-url",
                "https://coin.gold-trade.ir",
                "--iran-server-domain",
                "coin.gold-trade.ir",
            ]
            with patch("sys.argv", argv):
                with self.assertRaisesRegex(SystemExit, "outputs must be different"):
                    render_runtime_envs_main()
            self.assertFalse(output_path.exists())

    def test_atomic_write_failure_keeps_previous_runtime_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "runtime.env"
            output_path.write_text("ORIGINAL=value\n", encoding="utf-8")
            with patch("scripts.render_runtime_envs.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    write_env_file(str(output_path), {"NEW": "value"})

            self.assertEqual(output_path.read_text(encoding="utf-8"), "ORIGINAL=value\n")
            self.assertEqual(list(output_path.parent.glob(".runtime.env.*.tmp")), [])

    def test_collect_runtime_values_reads_non_shell_safe_source_env_and_rejects_unlisted_override(self):
        values = self.sample_values()
        values.pop("CHANNEL_INVITE_LINK")
        values.pop("ERROR_TRACKING_DSN")
        values.pop("SMSIR_OTP_TEMPLATE_ID")
        values.pop("SMSIR_OTP_TEMPLATE_PARAMETER")
        values.pop("SMSIR_INVITATION_TEMPLATE_ID")
        values.pop("SMSIR_INVITATION_TEMPLATE_PARAMETER")
        values.pop("SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID")
        values.pop("SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID")
        values.pop("WEB_PUSH_ENABLED")
        values.pop("WEB_PUSH_VAPID_PUBLIC_KEY")
        values.pop("WEB_PUSH_VAPID_PRIVATE_KEY")
        values.pop("WEB_PUSH_VAPID_SUBJECT")
        values.pop("WEB_PUSH_TTL_SECONDS")
        values.pop("WEB_PUSH_TIMEOUT_SECONDS")
        values.pop("OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED")
        values.pop("RELEASE_SHA")
        values["GRAFANA_ALERT_DEFAULT_RECEIVER"] = "Trading Bot Production Webhook"
        values["GRAFANA_ALERT_CRITICAL_RECEIVER"] = "Trading Bot Production Webhook"
        values["GRAFANA_ALERT_WARNING_RECEIVER"] = "Trading Bot Production Email"

        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "runtime.env"
            source_path.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "GRAFANA_ALERT_WEBHOOK_URL": "https://override.example/alerts",
                    "RELEASE_SHA": "allowed-release-override",
                },
                clear=True,
            ):
                collected = collect_runtime_values(str(source_path))

        self.assertEqual(collected["CHANNEL_INVITE_LINK"], "")
        self.assertEqual(collected["ERROR_TRACKING_DSN"], "")
        self.assertEqual(collected["SMSIR_OTP_TEMPLATE_ID"], "585147")
        self.assertEqual(collected["SMSIR_OTP_TEMPLATE_PARAMETER"], "CODE")
        self.assertEqual(collected["SMSIR_INVITATION_TEMPLATE_ID"], "657938")
        self.assertEqual(collected["SMSIR_INVITATION_TEMPLATE_PARAMETER"], "NAME")
        self.assertEqual(collected["SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID"], "162103")
        self.assertEqual(collected["SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID"], "903643")
        self.assertEqual(collected["WEB_PUSH_ENABLED"], "false")
        self.assertEqual(collected["WEB_PUSH_VAPID_PUBLIC_KEY"], "")
        self.assertEqual(collected["WEB_PUSH_VAPID_PRIVATE_KEY"], "")
        self.assertEqual(collected["WEB_PUSH_VAPID_SUBJECT"], "")
        self.assertEqual(collected["WEB_PUSH_TTL_SECONDS"], "3600")
        self.assertEqual(collected["WEB_PUSH_TIMEOUT_SECONDS"], "5.0")
        self.assertEqual(collected["OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED"], "false")
        self.assertEqual(collected["RELEASE_SHA"], "allowed-release-override")
        self.assertEqual(collected["GRAFANA_ALERT_DEFAULT_RECEIVER"], "Trading Bot Production Webhook")
        self.assertEqual(collected["GRAFANA_ALERT_WARNING_RECEIVER"], "Trading Bot Production Email")
        self.assertEqual(collected["GRAFANA_ALERT_WEBHOOK_URL"], "https://alerts.example/api")

    def test_source_is_authoritative_for_queue_credentials_expected_ids_and_inference(self):
        values = self.queue_values()
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "master.env"
            source_path.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            pollution = {
                "BOT_TOKEN": "polluted-primary",
                "TELEGRAM_DELIVERY_PRODUCER_MODE": "legacy",
                "TELEGRAM_PUBLISHER_1_BOT_TOKEN": "polluted-publisher",
                "TELEGRAM_PUBLISHER_1_EXPECTED_BOT_ID": "999999",
                "TELEGRAM_PUBLISHER_1_EXPECTED_USERNAME": "polluted_bot",
                "TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED": "false",
                "IRAN_OTP_DELIVERY_STATE_SECRET": "polluted-otp-state-secret",
                "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED": "false",
                "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_PATH": "/polluted/path.json",
                "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH": "/polluted/alias.json",
                "DB_POOL_SIZE": "19",
            }
            with patch.dict(os.environ, pollution, clear=True):
                collected = collect_runtime_values(str(source_path))

        self.assertEqual(collected["BOT_TOKEN"], "bot-token")
        self.assertEqual(collected["TELEGRAM_DELIVERY_PRODUCER_MODE"], "queue-v1")
        self.assertEqual(
            collected["TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED"],
            "true",
        )
        self.assertEqual(collected["TELEGRAM_PUBLISHER_1_BOT_TOKEN"], "publisher-token-1")
        self.assertEqual(collected["TELEGRAM_PUBLISHER_1_EXPECTED_BOT_ID"], "101")
        self.assertEqual(collected["TELEGRAM_PUBLISHER_1_EXPECTED_USERNAME"], "publisher_1_bot")
        self.assertEqual(
            collected["IRAN_OTP_DELIVERY_STATE_SECRET"],
            "iran-only-otp-state-secret-0123456789abcdef",
        )
        self.assertEqual(collected["PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED"], "true")
        self.assertEqual(
            collected["PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_PATH"],
            "/app/runtime/coin-inference/coin-rates.json",
        )
        self.assertEqual(
            collected["PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH"],
            "/app/runtime/coin-inference/coin-rates.json",
        )
        self.assertEqual(collected["DB_POOL_SIZE"], "19")

    def test_split_brain_source_profile_fails_closed(self):
        values = self.queue_values()
        values["TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER"] = "legacy"
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "master.env"
            source_path.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(SystemExit, "split-brain"):
                    collect_runtime_values(str(source_path))

    def test_production_inference_maximum_age_is_exactly_120(self):
        values = self.queue_values()
        values["PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS"] = "121"
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "master.env"
            source_path.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(SystemExit, "must be exactly 120"):
                    collect_runtime_values(str(source_path))


if __name__ == "__main__":
    unittest.main()
