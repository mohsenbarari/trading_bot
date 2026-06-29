import unittest
import tempfile
from pathlib import Path

from scripts.check_deployment_surface_guard import check_runtime_env_identity, run_guard


def active_compose_services(path: Path) -> dict[str, dict[str, object]]:
    services: dict[str, dict[str, object]] = {}
    in_services = False
    current_service: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_services = stripped == "services:"
            current_service = None
            continue

        if not in_services:
            continue

        if indent == 2 and stripped.endswith(":"):
            current_service = stripped[:-1]
            services[current_service] = {"raw": [], "environment": {}}
            continue

        if not current_service or indent < 4:
            continue

        services[current_service]["raw"].append(stripped)
        if stripped.startswith("command:"):
            services[current_service]["command"] = stripped.split(":", 1)[1].strip()
        if "TRADING_BOT_SERVICE:" in stripped:
            _, value = stripped.split("TRADING_BOT_SERVICE:", 1)
            services[current_service]["environment"]["TRADING_BOT_SERVICE"] = value.strip()
        if "SERVER_MODE:" in stripped:
            _, value = stripped.split("SERVER_MODE:", 1)
            services[current_service]["environment"]["SERVER_MODE"] = value.strip()

    return services


def compose_service_block(path: Path, service_name: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start: int | None = None
    end = len(lines)
    for index, raw_line in enumerate(lines):
        if raw_line.startswith(f"  {service_name}:"):
            start = index
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if start is not None and index > start and indent == 2 and raw_line.strip().endswith(":"):
            end = index
            break
    if start is None:
        raise AssertionError(f"{service_name} service not found in {path}")
    return "\n".join(lines[start:end])


class DeploymentSurfaceGuardTests(unittest.TestCase):
    def production_manifest(self) -> dict[str, str]:
        return {
            "FOREIGN_SERVER_URL": "https://coin.362514.ir",
            "FOREIGN_SERVER_DOMAIN": "coin.362514.ir",
            "IRAN_SERVER_URL": "https://coin.gold-trade.ir",
            "IRAN_SERVER_DOMAIN": "coin.gold-trade.ir",
            "FOREIGN_FRONTEND_URL": "https://coin.362514.ir",
            "IRAN_FRONTEND_URL": "https://coin.gold-trade.ir",
        }

    def write_runtime_env(self, directory: Path, role: str, *, overrides: dict[str, str] | None = None) -> Path:
        values = {
            "SERVER_MODE": role,
            "FOREIGN_SERVER_URL": "https://coin.362514.ir",
            "FOREIGN_SERVER_DOMAIN": "coin.362514.ir",
            "IRAN_SERVER_URL": "https://coin.gold-trade.ir",
            "IRAN_SERVER_DOMAIN": "coin.gold-trade.ir",
            "FRONTEND_URL": "https://coin.362514.ir" if role == "foreign" else "https://coin.gold-trade.ir",
            "SYNC_VERIFY_TLS": "true",
            "SYNC_CA_BUNDLE": "",
            "DEV_API_KEY": "secret-dev-key-that-must-not-leak",
        }
        if overrides:
            values.update(overrides)
        path = directory / f"{role}.env"
        path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
        return path

    def test_repository_has_no_runtime_or_entrypoint_deployment_identity_leaks(self):
        repo_root = Path(__file__).resolve().parents[1]
        findings = run_guard(repo_root)

        self.assertEqual(
            findings,
            [],
            "\n".join(f"{finding.path}: {finding.detail}" for finding in findings),
        )

    def test_runtime_identity_guard_accepts_valid_foreign_and_iran_envs(self):
        manifest = self.production_manifest()
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            foreign_env = self.write_runtime_env(tmp_path, "foreign")
            iran_env = self.write_runtime_env(tmp_path, "iran")

            foreign_findings = check_runtime_env_identity(
                manifest=manifest,
                role="foreign",
                env_path=foreign_env,
                repo_root=repo_root,
            )
            iran_findings = check_runtime_env_identity(
                manifest=manifest,
                role="iran",
                env_path=iran_env,
                repo_root=repo_root,
            )

        self.assertEqual(foreign_findings, [])
        self.assertEqual(iran_findings, [])

    def test_runtime_identity_guard_rejects_retired_iran_peer(self):
        manifest = self.production_manifest()
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = self.write_runtime_env(
                Path(tmpdir),
                "iran",
                overrides={
                    "FOREIGN_SERVER_URL": "https://kharej.362514.ir",
                    "FOREIGN_SERVER_DOMAIN": "kharej.362514.ir",
                },
            )
            findings = check_runtime_env_identity(
                manifest=manifest,
                role="iran",
                env_path=env_path,
                repo_root=repo_root,
            )

        details = "\n".join(finding.detail for finding in findings)
        self.assertIn("retired deployment identity", details)
        self.assertIn("does not match production manifest", details)

    def test_runtime_identity_guard_rejects_missing_and_mismatched_domain_fields_without_secret_leak(self):
        manifest = self.production_manifest()
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = self.write_runtime_env(
                Path(tmpdir),
                "foreign",
                overrides={
                    "IRAN_SERVER_DOMAIN": "",
                    "FOREIGN_SERVER_DOMAIN": "wrong.example",
                },
            )
            findings = check_runtime_env_identity(
                manifest=manifest,
                role="foreign",
                env_path=env_path,
                repo_root=repo_root,
            )

        rendered = "\n".join(f"{finding.path}: {finding.detail}" for finding in findings)
        self.assertIn("missing required runtime identity field IRAN_SERVER_DOMAIN", rendered)
        self.assertIn("FOREIGN_SERVER_URL host does not match FOREIGN_SERVER_DOMAIN", rendered)
        self.assertNotIn("secret-dev-key-that-must-not-leak", rendered)

    def test_runtime_identity_guard_rejects_project_root_env_source(self):
        manifest = self.production_manifest()
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            env_path = repo_root / ".env.iran"
            env_path.write_text(
                "\n".join(
                    [
                        "SERVER_MODE=iran",
                        "FOREIGN_SERVER_URL=https://coin.362514.ir",
                        "FOREIGN_SERVER_DOMAIN=coin.362514.ir",
                        "IRAN_SERVER_URL=https://coin.gold-trade.ir",
                        "IRAN_SERVER_DOMAIN=coin.gold-trade.ir",
                        "FRONTEND_URL=https://coin.gold-trade.ir",
                        "SYNC_VERIFY_TLS=true",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            findings = check_runtime_env_identity(
                manifest=manifest,
                role="iran",
                env_path=env_path,
                repo_root=repo_root,
            )

        self.assertIn(
            "production runtime env source must not be project-root .env or .env.iran",
            "\n".join(finding.detail for finding in findings),
        )

    def test_runtime_identity_guard_allows_project_env_source_only_with_emergency_flag(self):
        manifest = self.production_manifest()
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            env_path = repo_root / ".env.iran"
            env_path.write_text(
                "\n".join(
                    [
                        "SERVER_MODE=iran",
                        "FOREIGN_SERVER_URL=https://coin.362514.ir",
                        "FOREIGN_SERVER_DOMAIN=coin.362514.ir",
                        "IRAN_SERVER_URL=https://coin.gold-trade.ir",
                        "IRAN_SERVER_DOMAIN=coin.gold-trade.ir",
                        "FRONTEND_URL=https://coin.gold-trade.ir",
                        "SYNC_VERIFY_TLS=true",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            findings = check_runtime_env_identity(
                manifest=manifest,
                role="iran",
                env_path=env_path,
                repo_root=repo_root,
                allow_project_env_source=True,
            )

        self.assertEqual(findings, [])

    def test_compose_surface_keeps_telegram_bot_foreign_only(self):
        repo_root = Path(__file__).resolve().parents[1]
        foreign_services = active_compose_services(repo_root / "docker-compose.yml")
        iran_services = active_compose_services(repo_root / "docker-compose.iran.yml")

        self.assertIn("bot", foreign_services)
        foreign_bot = foreign_services["bot"]
        self.assertEqual(foreign_bot["environment"]["TRADING_BOT_SERVICE"], "bot")
        self.assertIn("run_bot.py", str(foreign_bot.get("command", "")))

        self.assertNotIn("bot", iran_services)
        for service_name, service in iran_services.items():
            raw_service = "\n".join(service["raw"])
            self.assertNotEqual(
                service["environment"].get("TRADING_BOT_SERVICE"),
                "bot",
                msg=f"{service_name} must not identify as Telegram bot on Iran surface",
            )
            self.assertNotIn("run_bot.py", raw_service)
            self.assertNotIn("BOT_TOKEN:", raw_service)

    def test_staging_load_runners_are_profile_gated_and_role_bound(self):
        repo_root = Path(__file__).resolve().parents[1]
        staging_compose = repo_root / "deploy/staging/docker-compose.staging.yml"

        telegram_runner = compose_service_block(staging_compose, "load_telegram_foreign")
        webapp_runner = compose_service_block(staging_compose, "load_webapp_iran")

        for service_name, block in (
            ("load_telegram_foreign", telegram_runner),
            ("load_webapp_iran", webapp_runner),
        ):
            self.assertIn("profiles:", block, msg=f"{service_name} must be profile-gated")
            self.assertIn("- staging-load", block, msg=f"{service_name} must stay out of normal staging deploys")
            self.assertIn("TRADING_BOT_SERVICE: load_runner", block)
            self.assertIn('BOT_TOKEN: ""', block)
            self.assertNotIn("run_bot.py", block)
            self.assertNotIn("uvicorn main:app", block)
            self.assertNotIn("python -m core.sync_worker", block)

        self.assertIn('"--role", "telegram_foreign"', telegram_runner)
        self.assertIn("SERVER_MODE: foreign", telegram_runner)
        self.assertIn('"--role", "webapp_iran"', webapp_runner)
        self.assertIn("SERVER_MODE: iran", webapp_runner)

    def test_staging_bot_profile_is_foreign_only_when_enabled(self):
        repo_root = Path(__file__).resolve().parents[1]
        staging_compose = repo_root / "deploy/staging/docker-compose.staging.yml"
        staging_services = active_compose_services(staging_compose)
        staging_app = staging_services["app"]
        staging_bot = staging_services["bot"]
        staging_bot_block = compose_service_block(staging_compose, "bot")

        self.assertEqual(staging_app["environment"]["SERVER_MODE"], "iran")
        self.assertIn("profiles:", staging_bot_block)
        self.assertIn("- staging-bot", staging_bot_block)
        self.assertEqual(staging_bot["environment"]["TRADING_BOT_SERVICE"], "bot")
        self.assertEqual(staging_bot["environment"]["SERVER_MODE"], "foreign")
        self.assertIn("run_bot.py", str(staging_bot.get("command", "")))
        self.assertIn("RELEASE_SHA: ${STAGING_RELEASE_SHA:-staging}", staging_bot_block)
        self.assertIn("IRAN_SERVER_URL: ${STAGING_FOREIGN_IRAN_SERVER_URL", staging_bot_block)
        self.assertIn("FRONTEND_URL: ${STAGING_FOREIGN_FRONTEND_URL", staging_bot_block)
        self.assertIn("FOREIGN_SERVER_URL: ${STAGING_FOREIGN_FOREIGN_SERVER_URL", staging_bot_block)

    def test_staging_bot_profile_includes_internal_foreign_api(self):
        repo_root = Path(__file__).resolve().parents[1]
        staging_compose = repo_root / "deploy/staging/docker-compose.staging.yml"
        staging_services = active_compose_services(staging_compose)
        foreign_app = staging_services["foreign_app"]
        foreign_app_block = compose_service_block(staging_compose, "foreign_app")
        staging_bot_block = compose_service_block(staging_compose, "bot")

        self.assertIn("profiles:", foreign_app_block)
        self.assertIn("- staging-bot", foreign_app_block)
        self.assertEqual(foreign_app["environment"]["TRADING_BOT_SERVICE"], "api")
        self.assertEqual(foreign_app["environment"]["SERVER_MODE"], "foreign")
        self.assertIn("IRAN_SERVER_URL: ${STAGING_FOREIGN_IRAN_SERVER_URL", foreign_app_block)
        self.assertIn("FRONTEND_URL: ${STAGING_FOREIGN_FRONTEND_URL", foreign_app_block)
        self.assertIn("FOREIGN_SERVER_URL: ${STAGING_FOREIGN_FOREIGN_SERVER_URL", foreign_app_block)
        self.assertIn("uvicorn main:app", str(foreign_app.get("command", "")))
        self.assertIn("ports:", foreign_app_block)
        self.assertIn('"127.0.0.1:${STAGING_FOREIGN_APP_PORT:-8121}:8000"', foreign_app_block)
        self.assertIn("foreign_app:", staging_bot_block)
        self.assertIn("condition: service_healthy", staging_bot_block)

    def test_staging_sync_workers_are_profile_gated_and_role_bound(self):
        repo_root = Path(__file__).resolve().parents[1]
        staging_compose = repo_root / "deploy/staging/docker-compose.staging.yml"
        staging_services = active_compose_services(staging_compose)

        sync_worker = staging_services["sync_worker"]
        foreign_sync_worker = staging_services["foreign_sync_worker"]
        sync_worker_block = compose_service_block(staging_compose, "sync_worker")
        foreign_sync_worker_block = compose_service_block(staging_compose, "foreign_sync_worker")

        for service_name, service, block in (
            ("sync_worker", sync_worker, sync_worker_block),
            ("foreign_sync_worker", foreign_sync_worker, foreign_sync_worker_block),
        ):
            self.assertIn("profiles:", block, msg=f"{service_name} must be profile-gated")
            self.assertIn("- staging-sync", block, msg=f"{service_name} must stay out of normal staging deploys")
            self.assertEqual(service["environment"]["TRADING_BOT_SERVICE"], "sync_worker")
            self.assertIn("python -m core.sync_worker", str(service.get("command", "")))
            self.assertNotIn("run_bot.py", block)
            self.assertNotIn("BOT_TOKEN:", block)
            self.assertNotIn("ports:", block)

        self.assertEqual(sync_worker["environment"]["SERVER_MODE"], "iran")
        self.assertEqual(foreign_sync_worker["environment"]["SERVER_MODE"], "foreign")
        self.assertIn("IRAN_SERVER_URL: ${STAGING_FOREIGN_IRAN_SERVER_URL", foreign_sync_worker_block)
        self.assertIn("FRONTEND_URL: ${STAGING_FOREIGN_FRONTEND_URL", foreign_sync_worker_block)
        self.assertIn("FOREIGN_SERVER_URL: ${STAGING_FOREIGN_FOREIGN_SERVER_URL", foreign_sync_worker_block)
        self.assertIn("app:", sync_worker_block)
        self.assertIn("foreign_app:", foreign_sync_worker_block)

    def test_staging_foreign_load_runner_uses_real_iran_peer_override(self):
        repo_root = Path(__file__).resolve().parents[1]
        staging_compose = repo_root / "deploy/staging/docker-compose.staging.yml"
        telegram_runner_block = compose_service_block(staging_compose, "load_telegram_foreign")

        self.assertIn("SERVER_MODE: foreign", telegram_runner_block)
        self.assertIn("RELEASE_SHA: ${STAGING_RELEASE_SHA:-staging}", telegram_runner_block)
        self.assertIn("IRAN_SERVER_URL: ${STAGING_FOREIGN_IRAN_SERVER_URL", telegram_runner_block)
        self.assertIn("FRONTEND_URL: ${STAGING_FOREIGN_FRONTEND_URL", telegram_runner_block)
        self.assertIn("FOREIGN_SERVER_URL: ${STAGING_FOREIGN_FOREIGN_SERVER_URL", telegram_runner_block)


if __name__ == "__main__":
    unittest.main()
