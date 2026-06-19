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

    return services


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


if __name__ == "__main__":
    unittest.main()
