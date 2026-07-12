from pathlib import Path
import unittest


class Stage9RedisRunnerStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_stage9_redis_suite.sh"
        ).read_text(encoding="utf-8")

    def test_uses_disposable_stage9_container_and_no_runtime_redis_url(self):
        self.assertIn('container_name="trading_bot_stage9_redis_$$"', self.wrapper)
        self.assertIn('redis_url="redis://${container_name}:6379/0"', self.wrapper)
        self.assertNotIn("redis://redis:6379", self.wrapper)
        self.assertNotIn("STAGE5_TEST_REDIS_URL=redis://redis", self.wrapper)
        self.assertNotIn("STAGE6_TEST_REDIS_URL=redis://redis", self.wrapper)

    def test_cleanup_is_scoped_to_the_disposable_container(self):
        self.assertIn('docker rm -f "$container_name"', self.wrapper)
        self.assertNotIn("docker compose down", self.wrapper)
        self.assertNotIn("docker compose stop", self.wrapper)
        self.assertNotIn("docker volume", self.wrapper)

    def test_coverage_is_written_only_to_mounted_tmp(self):
        self.assertIn("COVERAGE_FILE=/app/tmp/stage9-redis-coverage/.coverage", self.wrapper)
        self.assertIn('-v "$repo_root/tmp:/app/tmp"', self.wrapper)
        self.assertIn(
            'find "$repo_root/tmp/stage9-redis-coverage" -maxdepth 1 -type f '
            "-name '.coverage.*' -delete",
            self.wrapper,
        )
        self.assertIn("coverage run --branch --parallel-mode", self.wrapper)


if __name__ == "__main__":
    unittest.main()
