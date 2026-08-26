import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import rehearse_market_archive_stage2 as rehearsal


class MarketArchiveStage2RehearsalTests(unittest.TestCase):
    def test_safe_names_and_database_gate_reject_broad_targets(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "unsafe_rehearsal_name"):
            rehearsal.ensure_unused("market-stage2-rehearsal-1", "production-data")
        with self.assertRaisesRegex(rehearsal.RehearsalError, "unsafe_database_name"):
            rehearsal.connect(5432, "trading_bot")
        with self.assertRaisesRegex(rehearsal.RehearsalError, "unsafe_database_name"):
            rehearsal.create_database("safe-container", "trading_bot")

    def test_migration_paths_are_separate_from_product_migrations(self):
        self.assertIn("deploy/market-data/migrations", str(rehearsal.UP_MIGRATION))
        self.assertIn("CREATE SCHEMA IF NOT EXISTS market_data", rehearsal.UP_MIGRATION.read_text())
        self.assertNotIn("alembic_version", rehearsal.UP_MIGRATION.read_text())
        self.assertEqual(
            rehearsal.DOWN_MIGRATION.read_text().count("DROP SCHEMA"),
            1,
        )

    def test_outbox_benchmark_claim_is_scoped_to_one_stream(self):
        source = Path(rehearsal.__file__).read_text(encoding="utf-8")
        self.assertIn("AND stream_id = 'market.fact.coin.group.1'", source)
        self.assertIn("ORDER BY delivery_sequence", source)

    def test_percentile_is_deterministic(self):
        self.assertEqual(rehearsal.percentile([5, 1, 4, 2, 3], 0.95), 4)

    def test_registry_rows_exclude_runtime_source_identity(self):
        rows = rehearsal.registry_rows()
        rendered = repr(rows)
        self.assertNotIn("source_id", rendered)
        self.assertNotIn("telegram", rendered.lower())
        self.assertEqual(len(rows), 10)


if __name__ == "__main__":
    unittest.main()
