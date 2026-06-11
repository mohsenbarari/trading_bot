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
            "OBSERVABILITY_API_KEY": "obs-key",
            "CHANNEL_ID": "-100123",
            "CHANNEL_INVITE_LINK": "https://t.me/example",
            "SMSIR_API_KEY": "sms-key",
            "SMSIR_LINE_NUMBER": "3000",
            "ERROR_TRACKING_DSN": "dsn",
            "TRUSTED_PROXY_CIDRS": "127.0.0.1/32,::1/128,10.0.0.0/24",
            "OBSERVABILITY_TELEGRAM_USER_HASH_SALT": "salt",
            "GRAFANA_ALERT_DEFAULT_RECEIVER": "default",
            "GRAFANA_ALERT_CRITICAL_RECEIVER": "critical",
            "GRAFANA_ALERT_WARNING_RECEIVER": "warning",
            "GRAFANA_ALERT_WEBHOOK_URL": "https://alerts.example/api",
            "GRAFANA_ALERT_EMAIL_ADDRESSES": "ops@example.com",
            "DB_POOL_SIZE": "15",
            "DB_MAX_OVERFLOW": "10",
            "IRAN_DB_POOL_SIZE": "8",
            "IRAN_DB_MAX_OVERFLOW": "6",
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
            "IRAN_POSTGRES_SHARED_BUFFERS": "8GB",
            "IRAN_POSTGRES_EFFECTIVE_CACHE_SIZE": "80GB",
            "IRAN_POSTGRES_WORK_MEM": "8MB",
            "IRAN_POSTGRES_MAINTENANCE_WORK_MEM": "512MB",
            "IRAN_POSTGRES_RANDOM_PAGE_COST": "1.2",
            "IRAN_POSTGRES_EFFECTIVE_IO_CONCURRENCY": "200",
            "IRAN_POSTGRES_CHECKPOINT_TIMEOUT": "15min",
            "IRAN_POSTGRES_MAX_WAL_SIZE": "8GB",
            "IRAN_POSTGRES_MIN_WAL_SIZE": "1GB",
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
            foreign_server_url="https://coin.362514.ir",
            foreign_server_domain="coin.362514.ir",
            iran_server_url="https://coin.gold-trade.ir",
            iran_server_domain="coin.gold-trade.ir",
            metrics_backend="memory",
            audit_trail_path="/app/audit.jsonl",
            api_workers="8",
            values=values,
        )

        self.assertEqual(foreign["SERVER_MODE"], "foreign")
        self.assertEqual(iran["SERVER_MODE"], "iran")
        self.assertEqual(foreign["API_WORKERS"], "2")
        self.assertEqual(iran["API_WORKERS"], "8")
        self.assertEqual(foreign["FRONTEND_URL"], "https://coin.362514.ir")
        self.assertEqual(iran["FRONTEND_URL"], "https://coin.gold-trade.ir")
        self.assertEqual(foreign["DB_POOL_SIZE"], "15")
        self.assertEqual(foreign["DB_MAX_OVERFLOW"], "10")
        self.assertEqual(iran["DB_POOL_SIZE"], "8")
        self.assertEqual(iran["DB_MAX_OVERFLOW"], "6")
        self.assertEqual(foreign["POSTGRES_SHARED_BUFFERS"], "128MB")
        self.assertEqual(iran["POSTGRES_SHARED_BUFFERS"], "8GB")
        self.assertEqual(iran["POSTGRES_EFFECTIVE_CACHE_SIZE"], "80GB")
        self.assertEqual(iran["POSTGRES_WORK_MEM"], "8MB")
        self.assertEqual(iran["REDIS_APPENDONLY"], "yes")
        self.assertEqual(iran["REDIS_APPENDFSYNC"], "everysec")
        self.assertEqual(iran["REDIS_MAXMEMORY_POLICY"], "noeviction")
        self.assertEqual(foreign["FOREIGN_SERVER_DOMAIN"], "coin.362514.ir")
        self.assertEqual(iran["IRAN_SERVER_DOMAIN"], "coin.gold-trade.ir")

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
                "8",
            ]
            with patch.dict(os.environ, values, clear=False):
                with patch("sys.argv", argv):
                    self.assertEqual(render_runtime_envs_main(), 0)

            foreign_lines = foreign_path.read_text(encoding="utf-8").splitlines()
            iran_lines = iran_path.read_text(encoding="utf-8").splitlines()

            self.assertIn("SERVER_MODE=foreign", foreign_lines)
            self.assertIn("SERVER_MODE=iran", iran_lines)
            self.assertIn("API_WORKERS=2", foreign_lines)
            self.assertIn("API_WORKERS=8", iran_lines)
            self.assertIn("DB_POOL_SIZE=8", iran_lines)
            self.assertIn("DB_MAX_OVERFLOW=6", iran_lines)
            self.assertIn("POSTGRES_SHARED_BUFFERS=8GB", iran_lines)
            self.assertIn("POSTGRES_EFFECTIVE_CACHE_SIZE=80GB", iran_lines)
            self.assertIn("POSTGRES_SHARED_BUFFERS=128MB", foreign_lines)
            self.assertIn("REDIS_APPENDONLY=yes", iran_lines)
            self.assertIn("REDIS_APPENDFSYNC=everysec", iran_lines)
            self.assertIn("DB_POOL_SIZE=15", foreign_lines)
            self.assertIn("FRONTEND_URL=https://coin.362514.ir", foreign_lines)
            self.assertIn("FRONTEND_URL=https://coin.gold-trade.ir", iran_lines)
            self.assertIn("IRAN_SERVER_URL=https://coin.gold-trade.ir", foreign_lines)
            self.assertIn("FOREIGN_SERVER_URL=https://coin.362514.ir", iran_lines)

    def test_collect_runtime_values_reads_non_shell_safe_source_env_and_allows_overrides(self):
        values = self.sample_values()
        values.pop("CHANNEL_INVITE_LINK")
        values.pop("ERROR_TRACKING_DSN")
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
        self.assertEqual(collected["GRAFANA_ALERT_DEFAULT_RECEIVER"], "Trading Bot Production Webhook")
        self.assertEqual(collected["GRAFANA_ALERT_WARNING_RECEIVER"], "Trading Bot Production Email")
        self.assertEqual(collected["GRAFANA_ALERT_WEBHOOK_URL"], "https://override.example/alerts")


if __name__ == "__main__":
    unittest.main()
