import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.render_runtime_envs import build_runtime_env, main as render_runtime_envs_main


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
            values=values,
        )

        self.assertEqual(foreign["SERVER_MODE"], "foreign")
        self.assertEqual(iran["SERVER_MODE"], "iran")
        self.assertEqual(foreign["FRONTEND_URL"], "https://coin.362514.ir")
        self.assertEqual(iran["FRONTEND_URL"], "https://coin.gold-trade.ir")
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
            ]
            with patch.dict(os.environ, values, clear=False):
                with patch("sys.argv", argv):
                    self.assertEqual(render_runtime_envs_main(), 0)

            foreign_lines = foreign_path.read_text(encoding="utf-8").splitlines()
            iran_lines = iran_path.read_text(encoding="utf-8").splitlines()

            self.assertIn("SERVER_MODE=foreign", foreign_lines)
            self.assertIn("SERVER_MODE=iran", iran_lines)
            self.assertIn("FRONTEND_URL=https://coin.362514.ir", foreign_lines)
            self.assertIn("FRONTEND_URL=https://coin.gold-trade.ir", iran_lines)
            self.assertIn("IRAN_SERVER_URL=https://coin.gold-trade.ir", foreign_lines)
            self.assertIn("FOREIGN_SERVER_URL=https://coin.362514.ir", iran_lines)


if __name__ == "__main__":
    unittest.main()
