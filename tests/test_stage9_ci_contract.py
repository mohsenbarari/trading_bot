from pathlib import Path
import unittest

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


class Stage9CIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = REPO_ROOT / ".github" / "workflows" / "coverage-report.yml"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)

    def test_coverage_job_has_isolated_postgres_and_redis_services(self):
        job = self.workflow["jobs"]["repository-coverage"]
        self.assertEqual(set(job["services"]), {"postgres", "redis"})
        self.assertIn("5432:5432", job["services"]["postgres"]["ports"])
        self.assertIn("6379:6379", job["services"]["redis"]["ports"])

    def test_opt_in_suites_are_branch_covered_and_joined_before_combine(self):
        self.assertIn("scripts/run_registration_scratch_suite.py", self.source)
        self.assertIn("tests.test_stage5_event_isolation_redis", self.source)
        self.assertIn("tests.test_stage6_otp_delivery_redis", self.source)
        self.assertIn("coverage run --branch --parallel-mode", self.source)
        self.assertIn("postgres_pid=$!", self.source)
        self.assertIn("redis_pid=$!", self.source)
        self.assertIn('wait "$postgres_pid"', self.source)
        self.assertIn('wait "$redis_pid"', self.source)
        self.assertLess(
            self.source.index('wait "$redis_pid"'),
            self.source.index("coverage combine --append"),
        )

    def test_opt_in_logs_are_uploaded(self):
        self.assertIn("tmp/backend-postgres-opt-in.log", self.source)
        self.assertIn("tmp/backend-redis-opt-in.log", self.source)


if __name__ == "__main__":
    unittest.main()
