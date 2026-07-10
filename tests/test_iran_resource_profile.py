import unittest
from pathlib import Path

from scripts.render_runtime_envs import ROLE_PERFORMANCE_DEFAULTS, ROLE_POSTGRES_TUNING_DEFAULTS


REPO_ROOT = Path(__file__).resolve().parents[1]


class IranResourceProfileTests(unittest.TestCase):
    def test_renderer_defaults_fit_replacement_host_budget(self):
        performance = ROLE_PERFORMANCE_DEFAULTS["iran"]
        postgres = ROLE_POSTGRES_TUNING_DEFAULTS["iran"]

        self.assertEqual(performance, {"DB_POOL_SIZE": "8", "DB_MAX_OVERFLOW": "4"})
        self.assertEqual(postgres["POSTGRES_MAX_CONNECTIONS"], "150")
        self.assertEqual(postgres["POSTGRES_SHARED_BUFFERS"], "2GB")
        self.assertEqual(postgres["POSTGRES_EFFECTIVE_CACHE_SIZE"], "5GB")
        self.assertEqual(postgres["POSTGRES_WORK_MEM"], "4MB")
        self.assertEqual(postgres["POSTGRES_MAINTENANCE_WORK_MEM"], "256MB")
        self.assertEqual(postgres["POSTGRES_MAX_WAL_SIZE"], "2GB")
        self.assertEqual(postgres["POSTGRES_MIN_WAL_SIZE"], "512MB")

        workers = 4
        per_process_connections = int(performance["DB_POOL_SIZE"]) + int(performance["DB_MAX_OVERFLOW"])
        steady_connection_ceiling = (workers + 1) * per_process_connections
        self.assertEqual(steady_connection_ceiling, 60)
        self.assertLess(steady_connection_ceiling, int(postgres["POSTGRES_MAX_CONNECTIONS"]))

    def test_compose_manifest_and_release_fallbacks_match_profile(self):
        compose = (REPO_ROOT / "docker-compose.iran.yml").read_text(encoding="utf-8")
        manifest = (REPO_ROOT / "deploy/production/online.env.example").read_text(encoding="utf-8")
        release = (REPO_ROOT / "scripts/production_deploy_online.sh").read_text(encoding="utf-8")

        for expected in (
            "${API_WORKERS:-4}",
            "${DB_POOL_SIZE:-8}",
            "${DB_MAX_OVERFLOW:-4}",
            "${POSTGRES_MAX_CONNECTIONS:-150}",
            "${POSTGRES_SHARED_BUFFERS:-2GB}",
            "${POSTGRES_EFFECTIVE_CACHE_SIZE:-5GB}",
            "${POSTGRES_WORK_MEM:-4MB}",
            "${POSTGRES_MAINTENANCE_WORK_MEM:-256MB}",
            "${POSTGRES_MAX_WAL_SIZE:-2GB}",
            "${POSTGRES_MIN_WAL_SIZE:-512MB}",
        ):
            self.assertIn(expected, compose)

        for expected in (
            "IRAN_API_WORKERS=4",
            "IRAN_DB_POOL_SIZE=8",
            "IRAN_DB_MAX_OVERFLOW=4",
            "IRAN_POSTGRES_MAX_CONNECTIONS=150",
            "IRAN_POSTGRES_SHARED_BUFFERS=2GB",
            "IRAN_POSTGRES_EFFECTIVE_CACHE_SIZE=5GB",
            "IRAN_POSTGRES_WORK_MEM=4MB",
            "IRAN_POSTGRES_MAINTENANCE_WORK_MEM=256MB",
            "IRAN_POSTGRES_MAX_WAL_SIZE=2GB",
            "IRAN_POSTGRES_MIN_WAL_SIZE=512MB",
        ):
            self.assertIn(expected, manifest)

        self.assertEqual(release.count('${IRAN_API_WORKERS:-4}'), 2)


if __name__ == "__main__":
    unittest.main()
