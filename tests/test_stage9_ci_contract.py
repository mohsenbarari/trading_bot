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
        cls.triggers = yaml.load(cls.source, Loader=yaml.BaseLoader)["on"]

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

    def test_pull_requests_target_main_and_candidate_production_shadow(self):
        self.assertEqual(
            self.triggers["pull_request"]["branches"],
            ["main", "candidate/production-three-site-shadow"],
        )

    def test_non_pr_coverage_base_is_explicit_and_fail_closed(self):
        dispatch_inputs = self.triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(dispatch_inputs["compare_ref"]["required"], "true")

        steps = self.workflow["jobs"]["repository-coverage"]["steps"]
        base_step = next(step for step in steps if step.get("id") == "coverage_base")
        self.assertEqual(
            base_step["env"]["PR_BASE_SHA"],
            "${{ github.event.pull_request.base.sha }}",
        )
        self.assertEqual(base_step["env"]["PUSH_BEFORE_SHA"], "${{ github.event.before }}")
        self.assertEqual(base_step["env"]["MANUAL_COMPARE_REF"], "${{ inputs.compare_ref }}")
        run = base_step["run"]
        self.assertNotIn("HEAD~1", run)
        self.assertIn('case "$EVENT_NAME" in', run)
        self.assertIn('pull_request)\n    base="$PR_BASE_SHA"', run)
        self.assertIn('push)\n    base="$PUSH_BEFORE_SHA"', run)
        self.assertIn("workflow_dispatch)", run)
        self.assertIn('git rev-parse --verify --end-of-options "${base}^{commit}"', run)
        self.assertIn('git merge-base --is-ancestor "$base_sha" "$head_sha"', run)
        self.assertIn('[[ "$base_sha" == "$head_sha" ]]', run)
        self.assertIn("coverage base must differ from HEAD", run)

    def test_backend_coverage_requires_every_shard_artifact_before_combine(self):
        steps = self.workflow["jobs"]["repository-coverage"]["steps"]
        combine_step = next(
            step
            for step in steps
            if step.get("name") == "Combine verified backend coverage shards"
        )
        run = combine_step["run"]
        self.assertIn("for shard in 0 1 2 3; do", run)
        self.assertIn(
            'coverage_files=(tmp/backend-coverage-inputs/.coverage.backend-shard-"${shard}".*)',
            run,
        )
        self.assertIn("if (( ${#coverage_files[@]} == 0 )); then", run)
        self.assertIn("missing backend coverage artifact for shard", run)
        self.assertIn('if [[ "$missing_coverage_artifacts" -ne 0 ]]; then', run)
        self.assertLess(
            run.index('if [[ "$missing_coverage_artifacts" -ne 0 ]]; then'),
            run.index("coverage combine --append"),
        )

    def test_backend_coverage_is_partitioned_and_recombined_exactly(self):
        backend = self.workflow["jobs"]["backend-coverage"]
        self.assertEqual(backend["strategy"]["matrix"]["shard"], [0, 1, 2, 3])
        self.assertIn("scripts/run_repository_unittest_shard.py", self.source)
        self.assertIn("scripts/verify_repository_unittest_shards.py", self.source)
        self.assertIn("merge-multiple: true", self.source)
        self.assertIn("include-hidden-files: true", self.source)
        self.assertIn("backend-shard-verification.json", self.source)

    def test_stage9_traceability_static_generation_is_not_a_maintained_gate(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("build_stage9_traceability.py --validate-only", makefile)
        self.assertIn("stage9-evidence-gate:", makefile)
        self.assertIn("--require-runtime-evidence", makefile)
        self.assertIn("build_stage9_runtime_evidence.py", self.source)
        self.assertIn("make stage9-evidence-gate", self.source)
        self.assertIn("stage9_evidence_commit=", self.source)
        self.assertIn("-m unittest -v", self.source)


if __name__ == "__main__":
    unittest.main()
