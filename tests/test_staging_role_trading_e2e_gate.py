import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StagingRoleTradingE2EGateTests(unittest.TestCase):
    def test_accountant_activation_fixture_has_a_cleanup_prefix(self):
        gate = (ROOT / "scripts/run_staging_role_trading_e2e_gate.sh").read_text(encoding="utf-8")
        spec = (ROOT / "frontend/e2e/accountant-owner-flow.spec.ts").read_text(encoding="utf-8")

        self.assertIn("`pwacct_${suffix}`", spec)
        self.assertIn('"pwacct_"', gate)

    def test_market_schedule_fixture_registers_sync_events_before_mutation(self):
        spec = (ROOT / "frontend/e2e/market-schedule.spec.ts").read_text(encoding="utf-8")
        configure_start = spec.index("function configureMarketRuntime")
        configure_end = spec.index("async function refreshMarketScheduleSettingsInApp", configure_start)
        configure_fixture = spec[configure_start:configure_end]

        self.assertIn("from core.events import setup_all_events", configure_fixture)
        self.assertIn("setup_all_events()", configure_fixture)
        self.assertLess(
            configure_fixture.index("setup_all_events()"),
            configure_fixture.index("async def main():"),
        )
        self.assertNotIn("db.execute(delete(MarketScheduleOverride))", configure_fixture)
        self.assertIn("select(MarketScheduleOverride)", configure_fixture)
        self.assertIn("await db.delete(override)", configure_fixture)


if __name__ == "__main__":
    unittest.main()
