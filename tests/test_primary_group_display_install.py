from pathlib import Path
import unittest

from scripts.install_primary_group_display import render


class PrimaryGroupDisplayInstallTests(unittest.TestCase):
    def test_only_projects_existing_primary_facts_and_preserves_writer_lock(self):
        unit = render(Path("/srv/releases") / ("a" * 40))
        self.assertIn("ExecStart=\n", unit)
        self.assertIn("--market-store /srv/trading-bot/staging-data/coin-intelligence/private-pipeline-shadow/market-store/market-store.sqlite", unit)
        self.assertIn("project_group_market_to_estimator.py", unit)
        self.assertIn(".conversation-writer.lock", unit)
        self.assertIn("ReadOnlyPaths=", unit)
        self.assertIn("EnvironmentFile=\n", unit)
        self.assertNotIn("collect_coin_group_event_telegram", unit)
        self.assertNotIn("--session", unit)
        self.assertNotIn("PRODUCT_ESTIMATOR_SNAPSHOT_MODE=", unit)
