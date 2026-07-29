"""Keep CI migration proofs on the same PostgreSQL feature set as runtime."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ROOT / ".github/workflows/merge-gate.yml",
    ROOT / ".github/workflows/pre-release-gate.yml",
)


class MarketPostgresCiWorkflowTests(unittest.TestCase):
    def test_market_postgres_gate_builds_the_repository_boottime_image(self) -> None:
        for path in WORKFLOWS:
            workflow = path.read_text(encoding="utf-8")

            self.assertIn("deploy/postgres-boottime/Dockerfile", workflow, path.name)
            self.assertIn("trading-bot-ci-postgres-boottime", workflow, path.name)
            self.assertIn("docker run -d --name market-postgres", workflow, path.name)
            self.assertIn("pg_isready -h 127.0.0.1 -U market_ci -d postgres", workflow, path.name)
            self.assertIn("psql -h 127.0.0.1 -U market_ci -d postgres", workflow, path.name)
            self.assertIn("trading_bot_boottime", workflow, path.name)
            self.assertIn("pg_available_extensions", workflow, path.name)
            self.assertIn("POSTGRES_DB: postgres", workflow, path.name)
            self.assertIn("POSTGRES_USER: market_ci", workflow, path.name)
            self.assertIn("POSTGRES_PASSWORD: market_ci", workflow, path.name)
            self.assertIn("REDIS_URL: redis://127.0.0.1:6379/14", workflow, path.name)
            self.assertIn("install -m 600 .env .env.api", workflow, path.name)
            self.assertIn("install -m 600 .env .env.migration", workflow, path.name)
            self.assertIn("trading-bot-ci-browser-postgres-boottime", workflow, path.name)
            self.assertIn('echo "POSTGRES_IMAGE=$image" >> "$GITHUB_ENV"', workflow, path.name)
            self.assertIn("E2E_TARGET_ENV: local", workflow, path.name)
            self.assertIn("E2E_ALLOW_LOCAL_MUTATION: local-dev-only", workflow, path.name)
            self.assertNotIn("image: postgres:15-alpine", workflow, path.name)

    def test_market_postgres_gate_cleans_up_its_disposable_container(self) -> None:
        for path in WORKFLOWS:
            workflow = path.read_text(encoding="utf-8")
            self.assertIn("docker rm --force market-postgres || true", workflow, path.name)

    def test_compose_keeps_stock_postgres_as_the_default_image(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("image: ${POSTGRES_IMAGE:-postgres:15-alpine}", compose)


if __name__ == "__main__":
    unittest.main()
