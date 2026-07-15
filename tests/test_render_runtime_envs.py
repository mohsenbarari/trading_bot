import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.render_runtime_envs import build_runtime_env, collect_runtime_values, main as render_runtime_envs_main


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
            "RELEASE_SHA": "release-sha",
            "IRAN_ORIGIN_READINESS_API_KEY": "origin-ready-key",
            "ORIGIN_EXPECTED_MIGRATION_REVISION": "d2e7f8a9b0c1",
            "ORIGIN_READINESS_MAX_EVIDENCE_AGE_SECONDS": "900",
            "WRITER_WITNESS_REQUIRED": "false",
            "WRITER_WITNESS_PUBLIC_KEY": "witness-public-key",
            "WRITER_WITNESS_LEASE_DURATION_SECONDS": "180",
            "WRITER_WITNESS_RENEW_INTERVAL_SECONDS": "30",
            "WRITER_WITNESS_SAFETY_MARGIN_SECONDS": "15",
            "WRITER_WITNESS_MAX_CLOCK_SKEW_SECONDS": "5",
            "WRITER_WITNESS_AUTHORITATIVE_SITE": "webapp_ir",
            "WRITER_WITNESS_HTTP_TIMEOUT_SECONDS": "3.0",
            "WRITER_WITNESS_AUTH_MAX_AGE_SECONDS": "15",
            "IRAN_WRITER_WITNESS_INTERNAL_URL": "https://witness.internal",
            "IRAN_WRITER_WITNESS_CLIENT_KEY_ID": "webapp-fi-v1",
            "IRAN_WRITER_WITNESS_CLIENT_SECRET": "fi-secret-0123456789abcdef-0123456789abcdef",
            "IRAN_WRITER_WITNESS_VERIFY_TLS": "true",
            "IRAN_WRITER_WITNESS_CA_BUNDLE": "/run/secrets/witness-ca.pem",
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
        self.assertEqual(foreign["LOGICAL_AUTHORITY"], "foreign")
        self.assertEqual(foreign["PHYSICAL_SITE"], "bot_fi")
        self.assertEqual(iran["LOGICAL_AUTHORITY"], "webapp")
        self.assertEqual(iran["PHYSICAL_SITE"], "webapp_fi")
        self.assertEqual(foreign["API_WORKERS"], "2")
        self.assertEqual(iran["API_WORKERS"], "4")
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
        self.assertEqual(foreign["ORIGIN_READINESS_API_KEY"], "")
        self.assertEqual(iran["ORIGIN_READINESS_API_KEY"], "origin-ready-key")
        self.assertEqual(iran["ORIGIN_EXPECTED_MIGRATION_REVISION"], "d2e7f8a9b0c1")
        self.assertEqual(iran["WRITER_WITNESS_REQUIRED"], "false")
        self.assertEqual(iran["WRITER_WITNESS_PUBLIC_KEY"], "witness-public-key")
        self.assertEqual(iran["WRITER_WITNESS_INTERNAL_URL"], "https://witness.internal")
        self.assertEqual(iran["WRITER_WITNESS_CLIENT_KEY_ID"], "webapp-fi-v1")
        self.assertEqual(
            iran["WRITER_WITNESS_CLIENT_SECRET"],
            "fi-secret-0123456789abcdef-0123456789abcdef",
        )
        self.assertEqual(foreign["WRITER_WITNESS_CLIENT_SECRET"], "")
        self.assertNotIn("IRAN_WRITER_WITNESS_CLIENT_SECRET", iran)
        self.assertEqual(iran["RELEASE_SHA"], "release-sha")
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
            self.assertIn("LOGICAL_AUTHORITY=foreign", foreign_lines)
            self.assertIn("PHYSICAL_SITE=bot_fi", foreign_lines)
            self.assertIn("LOGICAL_AUTHORITY=webapp", iran_lines)
            self.assertIn("PHYSICAL_SITE=webapp_fi", iran_lines)
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
            self.assertIn("SMSIR_OTP_TEMPLATE_ID=123456", iran_lines)
            self.assertIn("SMSIR_OTP_TEMPLATE_PARAMETER=CODE", iran_lines)
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
            self.assertIn("OTP_DELIVERY_STATE_SECRET=", foreign_lines)
            self.assertIn("ORIGIN_READINESS_API_KEY=", foreign_lines)
            self.assertIn("ORIGIN_READINESS_API_KEY=origin-ready-key", iran_lines)
            self.assertIn(
                "ORIGIN_EXPECTED_MIGRATION_REVISION=d2e7f8a9b0c1",
                iran_lines,
            )
            self.assertIn("WRITER_WITNESS_REQUIRED=false", iran_lines)
            self.assertIn("WRITER_WITNESS_PUBLIC_KEY=witness-public-key", iran_lines)
            self.assertIn(
                "OTP_DELIVERY_STATE_SECRET=iran-only-otp-state-secret-0123456789abcdef",
                iran_lines,
            )
            self.assertNotIn(
                "IRAN_OTP_DELIVERY_STATE_SECRET=iran-only-otp-state-secret-0123456789abcdef",
                foreign_lines,
            )

    def test_collect_runtime_values_reads_non_shell_safe_source_env_and_allows_overrides(self):
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
        values["GRAFANA_ALERT_DEFAULT_RECEIVER"] = "Trading Bot Production Webhook"
        values["GRAFANA_ALERT_CRITICAL_RECEIVER"] = "Trading Bot Production Webhook"
        values["GRAFANA_ALERT_WARNING_RECEIVER"] = "Trading Bot Production Email"

        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "runtime.env"
            source_path.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"GRAFANA_ALERT_WEBHOOK_URL": "https://override.example/alerts"}, clear=True):
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
        self.assertEqual(collected["GRAFANA_ALERT_DEFAULT_RECEIVER"], "Trading Bot Production Webhook")
        self.assertEqual(collected["GRAFANA_ALERT_WARNING_RECEIVER"], "Trading Bot Production Email")
        self.assertEqual(collected["GRAFANA_ALERT_WEBHOOK_URL"], "https://override.example/alerts")


if __name__ == "__main__":
    unittest.main()
