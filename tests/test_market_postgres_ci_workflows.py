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
            self.assertIn("trading_bot_boottime", workflow, path.name)
            self.assertIn("pg_available_extensions", workflow, path.name)
            self.assertNotIn("image: postgres:15-alpine", workflow, path.name)

    def test_market_postgres_gate_cleans_up_its_disposable_container(self) -> None:
        for path in WORKFLOWS:
            workflow = path.read_text(encoding="utf-8")
            self.assertIn("docker rm --force market-postgres || true", workflow, path.name)


if __name__ == "__main__":
    unittest.main()
