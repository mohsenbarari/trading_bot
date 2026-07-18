import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "monitoring" / "docker-compose.monitoring.yml"
ENV_EXAMPLE = ROOT / "deploy" / "monitoring" / "monitoring.env.example"
SCRIPT = ROOT / "scripts" / "monitoring_dev_stack.sh"
GITIGNORE = ROOT / ".gitignore"


class MonitoringDevStackContractTests(unittest.TestCase):
    def test_compose_has_no_fixed_or_shared_runtime_identity(self):
        text = COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("container_name:", text)
        self.assertNotIn("external: true", text)
        self.assertNotIn("trading_bot_postgres_data", text)
        self.assertNotIn("trading_bot_redis_data", text)
        self.assertIn("trading_bot_monitoring_dev", text)

    def test_default_runtime_is_loopback_and_telegram_safe(self):
        text = COMPOSE.read_text(encoding="utf-8")
        self.assertIn('127.0.0.1:${MONITORING_APP_PORT:-18100}:8000', text)
        self.assertIn('BACKGROUND_JOBS_ENABLED: "false"', text)
        self.assertIn('TELEGRAM_MONITORING_CHANNEL_ENABLED: "false"', text)
        self.assertIn("profiles:\n      - monitoring-telegram", text)

    def test_compose_never_loads_the_shared_dotenv(self):
        text = COMPOSE.read_text(encoding="utf-8")
        self.assertIn(".env.monitoring.local", text)
        self.assertNotIn("../../.env", text)
        self.assertNotIn("/root/", text)

    def test_generated_secret_file_is_explicitly_ignored(self):
        text = GITIGNORE.read_text(encoding="utf-8")
        self.assertIn("deploy/monitoring/.env.monitoring.local", text.splitlines())

    def test_example_uses_dedicated_database_and_redis(self):
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("POSTGRES_DB=trading_bot_monitoring_dev", text)
        self.assertIn("REDIS_URL=redis://redis:6379/0", text)
        self.assertIn("TELEGRAM_MONITORING_CHANNEL_ENABLED=false", text)
        self.assertIn("BOT_TOKEN=\n", text)
        self.assertIn("CHANNEL_ID=\n", text)
        self.assertIn("TELEGRAM_MONITORING_CHANNEL_ID=\n", text)

    def test_script_guards_main_and_destructive_cleanup(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'MONITORING_BRANCH="feature/admin-market-monitoring-channel"', text
        )
        self.assertIn('[[ "$branch" == "$MONITORING_BRANCH" ]]', text)
        self.assertIn('RESET_CONFIRMATION="RESET trading_bot_monitoring_dev"', text)
        self.assertIn("compose down --volumes --remove-orphans", text)
        self.assertIn("DEDICATED_NON_PRODUCTION_TELEGRAM", text)

    def test_runtime_identity_tracks_the_current_branch_head(self):
        script = SCRIPT.read_text(encoding="utf-8")
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertIn('release_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"', script)
        self.assertIn('MONITORING_IMAGE_TAG="$image_tag" docker compose', script)
        self.assertEqual(compose.count("RELEASE_SHA: ${MONITORING_RELEASE_SHA:-local}"), 3)

    def test_live_telegram_path_requires_dedicated_resources(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("primary CHANNEL_ID must stay empty", text)
        self.assertIn("primary and monitoring bot tokens must be different", text)
        self.assertIn("dedicated monitoring channel ID is required", text)

    def test_smoke_respects_foreign_surface_guard(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("compose exec -T app python -c", text)
        self.assertIn("http://127.0.0.1:8000/api/config", text)
        self.assertNotIn('curl --fail', text)
        readme = (ROOT / "deploy" / "monitoring" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("HTTP 404", readme)
        self.assertIn("foreign surface guard", readme)


if __name__ == "__main__":
    unittest.main()
