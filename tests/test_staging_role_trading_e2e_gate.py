import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StagingRoleTradingE2EGateTests(unittest.TestCase):
    def test_accountant_activation_fixture_has_a_cleanup_prefix(self):
        gate = (ROOT / "scripts/run_staging_role_trading_e2e_gate.sh").read_text(encoding="utf-8")
        spec = (ROOT / "frontend/e2e/accountant-owner-flow.spec.ts").read_text(encoding="utf-8")

        self.assertIn("`pwacct_${suffix}`", spec)
        self.assertIn('"pwacct_"', gate)


if __name__ == "__main__":
    unittest.main()
