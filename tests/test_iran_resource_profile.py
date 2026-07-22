import unittest
from pathlib import Path

from scripts.render_runtime_envs import ROLE_PERFORMANCE_DEFAULTS, ROLE_POSTGRES_TUNING_DEFAULTS


REPO_ROOT = Path(__file__).resolve().parents[1]


class IranResourceProfileTests(unittest.TestCase):
    def test_bootstrap_selects_compose_package_supported_by_remote_os(self):
        release = (REPO_ROOT / "scripts/production_deploy_online.sh").read_text(encoding="utf-8")

        self.assertIn(
            'IRAN_BOOTSTRAP_COMPOSE_PACKAGES="docker-compose-v2 docker-compose"',
            release,
        )
        bootstrap_packages = release.split('IRAN_BOOTSTRAP_APT_PACKAGES="', 1)[1].split('"', 1)[0].split()
        self.assertNotIn("docker-compose", bootstrap_packages)
        self.assertNotIn("docker-compose-v2", bootstrap_packages)
        self.assertGreaterEqual(release.count("for candidate in $IRAN_BOOTSTRAP_COMPOSE_PACKAGES"), 2)
        self.assertIn('apt-cache show "\\$candidate"', release)
        self.assertIn('install -y --fix-missing $IRAN_BOOTSTRAP_APT_PACKAGES "\\$compose_package"', release)

    def test_standalone_bootstrap_does_not_require_synced_project_payload(self):
        release = (REPO_ROOT / "scripts/production_deploy_online.sh").read_text(encoding="utf-8")

        dispatcher = release.split("main() {", 1)[1]
        command = dispatcher.split("bootstrap-iran)", 1)[1].split(";;", 1)[0]
        self.assertIn("check_local; bootstrap_iran", command)
        self.assertNotIn("install_sync_sampler_remote", command)
        self.assertNotIn("verify_sync_sampler_remote", command)

        full_release = release.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(full_release.index("sync_project"), full_release.index("install_sync_sampler_remote"))
        self.assertLess(full_release.index("install_sync_sampler_remote"), full_release.index("deploy_iran"))

    def test_iran_deploy_preserves_source_sequence_before_workers_start(self):
        release = (REPO_ROOT / "scripts/production_deploy_online.sh").read_text(encoding="utf-8")

        self.assertIn("foreign_iran_source_sequence_floor() {", release)
        self.assertIn(
            "watermark-floor --source-server iran --format value",
            release,
        )
        deploy_body = release.split("deploy_iran() {", 1)[1].split("\n}", 1)[0]
        align_command = (
            "python scripts/align_change_log_source_sequence.py align "
            "--floor '$iran_source_sequence_floor'"
        )
        self.assertIn(align_command, deploy_body)
        self.assertLess(deploy_body.index(align_command), deploy_body.rindex("app sync_worker"))

    def test_shared_reset_preserves_source_sequence_in_same_transaction(self):
        release = (REPO_ROOT / "scripts/production_deploy_online.sh").read_text(encoding="utf-8")
        reset_body = release.split("reset_iran_shared_tables() {", 1)[1].split("\n}", 1)[0]

        self.assertIn("BEGIN;", reset_body)
        self.assertIn("TRUNCATE TABLE change_log", reset_body)
        self.assertIn("pg_get_serial_sequence('change_log', 'id')", reset_body)
        self.assertIn("GREATEST($iran_source_sequence_floor, 1)", reset_body)
        self.assertIn("COMMIT;", reset_body)

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
            "${BACKGROUND_JOBS_ENABLED:-true}",
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
