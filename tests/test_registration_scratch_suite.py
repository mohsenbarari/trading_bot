from pathlib import Path
import unittest

from scripts.run_registration_scratch_suite import (
    RUNTIME_DATABASE_DENYLIST,
    SUITES,
    _test_command,
)


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
            {
                "stage1_migration",
                "stage2_registration",
                "stage3_registration",
                "stage4_registration",
            },
        )
        self.assertIn("trading_bot_db", RUNTIME_DATABASE_DENYLIST)

    def test_wrapper_coverage_mode_writes_only_to_mounted_tmp(self):
        wrapper = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_registration_scratch_suite.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("STAGE9_COVERAGE_FILE=/app/tmp/stage9-postgres-coverage/.coverage", wrapper)
        self.assertIn("PYTHONPATH=/app/stage9-test-packages-py311", wrapper)
        self.assertIn('-v "$repo_root/tmp:/app/tmp"', wrapper)
        self.assertIn(
            '-v "$repo_root/tmp/stage9-site-packages-py311:/app/stage9-test-packages-py311:ro"',
            wrapper,
        )
        self.assertIn(
            'find "$repo_root/tmp/stage9-postgres-coverage" -maxdepth 1 -type f '
            "-name '.coverage.*' -delete",
            wrapper,
        )

    def test_coverage_mode_collects_branch_data(self):
        command = _test_command("tests.example", coverage_file="/tmp/.coverage")
        self.assertIn("--branch", command)
        self.assertIn("--parallel-mode", command)


if __name__ == "__main__":
    unittest.main()
