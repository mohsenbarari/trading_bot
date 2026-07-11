from pathlib import Path
import unittest

from scripts.run_registration_scratch_suite import RUNTIME_DATABASE_DENYLIST, SUITES


class RegistrationScratchSuiteStaticTests(unittest.TestCase):
    def test_wrapper_is_no_deps_and_never_invokes_migration_service(self):
        wrapper = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_registration_scratch_suite.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("docker compose run --rm --no-deps", wrapper)
        self.assertNotIn(" compose up", wrapper)
        self.assertNotIn(" run --rm migration", wrapper)

    def test_every_suite_uses_an_allowlisted_scratch_prefix(self):
        self.assertEqual(
            {prefix for prefix, _env_name, _module in SUITES},
            {"stage1_migration", "stage3_registration", "stage4_registration"},
        )
        self.assertIn("trading_bot_db", RUNTIME_DATABASE_DENYLIST)


if __name__ == "__main__":
    unittest.main()
