import unittest
from pathlib import Path

from scripts.check_deployment_surface_guard import run_guard


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
    def test_repository_has_no_runtime_or_entrypoint_deployment_identity_leaks(self):
        repo_root = Path(__file__).resolve().parents[1]
        findings = run_guard(repo_root)

        self.assertEqual(
            findings,
            [],
            "\n".join(f"{finding.path}: {finding.detail}" for finding in findings),
        )

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
        self.assertIn("app:", sync_worker_block)
        self.assertIn("foreign_app:", foreign_sync_worker_block)


if __name__ == "__main__":
    unittest.main()
