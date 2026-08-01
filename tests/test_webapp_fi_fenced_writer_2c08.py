from __future__ import annotations

import json
import re
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts import preflight_fenced_fi_writer as preflight

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy/production/docker-compose.webapp-fi-writer-2c08.yml"
ENV_EXAMPLE = ROOT / "deploy/production/webapp-fi-fenced-writer-2c08.env.example"
AGENT_CONFIG_EXAMPLE = (
    ROOT / "deploy/production/production-writer-lease-agent.webapp-fi-fenced-2c08.json.example"
)
GUARD_TEMPLATE = ROOT / "deploy/systemd/trading-bot-production-writer-fi-fenced-lease-guard.service.template"
PREFLIGHT = ROOT / "scripts/preflight_fenced_fi_writer.py"


def compose_service_block(source: str, service_name: str) -> str:
    lines = source.splitlines()
    start: int | None = None
    end = len(lines)
    for index, raw_line in enumerate(lines):
        if raw_line == f"  {service_name}:":
            start = index
            continue
        if start is None or index <= start:
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == 0 or (indent == 2 and raw_line.strip().endswith(":")):
            end = index
            break
    if start is None:
        raise AssertionError(f"{service_name} service not found")
    return "\n".join(lines[start:end])


def static_services() -> tuple[preflight.StaticServiceExpectation, ...]:
    return (
        preflight.StaticServiceExpectation(
            name="app",
            container_name=preflight.FENCED_CONTAINER_NAMES["app"],
            image_ref="registry.example.invalid/trading-bot-app:2c08",
            image_id="sha256:" + "a" * 64,
        ),
        preflight.StaticServiceExpectation(
            name="bot",
            container_name=preflight.FENCED_CONTAINER_NAMES["bot"],
            image_ref="registry.example.invalid/trading-bot-bot:2c08",
            image_id="sha256:" + "b" * 64,
        ),
    )


def static_config() -> preflight.FencedFiWriterPreflightConfig:
    return preflight.FencedFiWriterPreflightConfig(
        control_release_root=Path("/srv/control"),
        application_release_root=Path("/srv/releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"),
        agent_config=Path("/etc/agent.json"),
        preflight_config=Path("/etc/preflight.json"),
        unit_file=preflight.APPROVED_FENCED_UNIT_FILE,
        lease_file=Path("/var/lib/trading-bot-three-site/writer-terms/writer-lease.json"),
        runtime_env_file=Path("/etc/runtime.env"),
        term_parent_directory=Path("/var/lib/trading-bot-three-site/writer-terms"),
        app_local_port=8000,
        compose_file=Path("/srv/control/deploy/production/docker-compose.webapp-fi-writer-2c08.yml"),
        compose_project="trading_bot_wa_fi_writer_2c08",
        services=static_services(),
    )


def runtime_receipt(config: preflight.FencedFiWriterPreflightConfig) -> dict[str, object]:
    containers: dict[str, dict[str, str]] = {}
    for index, service in enumerate(config.services):
        containers[service.name] = {
            "container_id": chr(ord("c") + index) * 64,
            "container_name": service.container_name,
            "image": service.image_ref,
            "image_id": service.image_id,
            "labels_sha256": chr(ord("e") + index) * 64,
            "restart_policy": "no",
        }
    payload: dict[str, object] = {
        "schema": preflight.lease_agent.WA_FI_FENCED_RUNTIME_RECEIPT_SCHEMA,
        "release_sha": preflight.lease_agent.WA_IR_APPLICATION_RELEASE_SHA,
        "compose_project": config.compose_project,
        "profile": preflight.lease_agent.WA_FI_FENCED_WRITER_PROFILE,
        "writer_epoch": 7,
        "lease_id": "lease-7",
        "witness_proof_sha256": "d" * 64,
        "containers": containers,
    }
    payload["runtime_receipt_sha256"] = preflight.lease_agent._runtime_binding_hash(payload)
    return payload


class WebappFiFencedWriter2c08Tests(unittest.TestCase):
    def test_compose_is_a_profiled_fixed_scope_without_local_dependencies(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        app = compose_service_block(text, "app")
        bot = compose_service_block(text, "bot")

        self.assertEqual(text.count('profiles: ["fenced-fi-writer"]'), 2)
        self.assertNotRegex(text, re.compile(r"(?m)^  (?:db|redis|migration|sync_worker):"))
        self.assertNotIn("build:", text)
        self.assertIn("container_name: trading_bot_wa_fi_writer_2c08_app", app)
        self.assertIn("container_name: trading_bot_wa_fi_writer_2c08_bot", bot)
        self.assertEqual(text.count('restart: "no"'), 2)
        self.assertEqual(text.count("pull_policy: never"), 2)
        self.assertIn("external: true", text)
        self.assertIn("WA_FI_WRITER_RUNTIME_NETWORK_NAME", text)
        self.assertIn("WA_FI_WRITER_UPLOADS_VOLUME", text)
        self.assertIn("WA_FI_WRITER_AUDIT_VOLUME", text)
        self.assertIn("127.0.0.1:${WA_FI_WRITER_APP_LOCAL_PORT:?explicit loopback app port is required}:8000", app)

        for block in (app, bot):
            self.assertIn('SINGLE_WRITER_RUNTIME_ENABLED: "true"', block)
            self.assertIn('APPLICATION_WRITER_TERM_ENFORCED: "true"', block)
            self.assertIn("APPLICATION_WRITER_TERM_LOCAL_SITE: webapp_fi", block)
            self.assertIn(
                "APPLICATION_WRITER_TERM_LEASE_FILE: /run/trading-bot-writer-term/writer-lease.json",
                block,
            )
            self.assertIn("APPLICATION_WRITER_TERM_SAFETY_MARGIN_SECONDS:", block)
            self.assertIn("APPLICATION_WRITER_TERM_MAX_LEASE_DURATION_SECONDS:", block)
            self.assertIn('DATABASE_SCHEMA_BOOTSTRAP_ENABLED: "false"', block)
            self.assertIn("target: /run/trading-bot-writer-term", block)
            self.assertIn("read_only: true", block)
            self.assertIn("create_host_path: false", block)
            self.assertNotIn("writer-lease.json:/run/trading-bot-writer-term", block)

        self.assertIn('BACKGROUND_JOBS_ENABLED: "false"', app)
        self.assertIn('TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"', app)
        self.assertIn('TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"', bot)
        self.assertIn("dynamic bot Writer Witness middleware and", bot)
        self.assertIn("watchdog", bot)
        self.assertIn("BOT_WRITER_READY_MARKER_PATH: /run/trading-bot-bot-readiness/ready", bot)
        self.assertIn("python -m bot.writer_readiness --healthcheck", bot)
        self.assertIn("tmpfs:", bot)
        self.assertIn("/run/trading-bot-bot-readiness:mode=0700,uid=0,gid=0", bot)
        self.assertIn("healthcheck:", app)
        self.assertIn("http://127.0.0.1:8000/api/config", app)

    def test_examples_are_explicit_about_preexisting_inputs_and_fixed_scope(self) -> None:
        env = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("RELEASE_SHA=2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5", env)
        for name in (
            "WA_FI_WRITER_APP_IMAGE",
            "WA_FI_WRITER_BOT_IMAGE",
            "WA_FI_WRITER_RUNTIME_ENV_FILE",
            "WA_FI_WRITER_APPLICATION_RELEASE_ROOT",
            "WA_FI_WRITER_TERM_PARENT_DIRECTORY",
            "WA_FI_WRITER_RUNTIME_NETWORK_NAME",
            "WA_FI_WRITER_UPLOADS_VOLUME",
            "WA_FI_WRITER_AUDIT_VOLUME",
            "WA_FI_WRITER_APP_LOCAL_PORT",
        ):
            self.assertRegex(env, re.compile(rf"(?m)^{name}="))
        self.assertIn("DATABASE_SCHEMA_BOOTSTRAP_ENABLED=false", env)
        self.assertIn("WA_FI_WRITER_APP_LOCAL_PORT=8000", env)
        self.assertIn("APPLICATION_WRITER_TERM_SAFETY_MARGIN_SECONDS=15", env)
        self.assertIn("APPLICATION_WRITER_TERM_MAX_LEASE_DURATION_SECONDS=60", env)
        self.assertNotRegex(env, re.compile(r"(?:AWS_SECRET|POSTGRES_PASSWORD|JWT_SECRET_KEY)="))

        config = json.loads(AGENT_CONFIG_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(config["mode"], "fenced_fi_writer")
        self.assertEqual(config["site"], "webapp_fi")
        self.assertEqual(config["runtime"]["services"], ["app", "bot"])
        self.assertTrue(
            config["runtime"]["compose_file"].endswith(
                "docker-compose.webapp-fi-writer-2c08.yml"
            )
        )
        self.assertIsNone(config["runtime"]["selection_env_file"])
        self.assertEqual(config["witness"]["lease_duration_seconds"], 60)
        self.assertEqual(config["witness"]["safety_margin_seconds"], 15)
        self.assertEqual(config["witness"]["renew_interval_seconds"], 10)

    def test_guard_has_a_distinct_unit_and_post_health_preflight(self) -> None:
        unit = GUARD_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "Conflicts=trading-bot-production-writer-fi-lease-guard.service",
            unit,
        )
        self.assertIn("--phase guard-start", unit)
        self.assertIn("WA_FI_FENCED_RUNTIME_RECEIPT", PREFLIGHT.read_text(encoding="utf-8"))
        self.assertIn('choices=("cutover-pre", "stage", "guard-start")', PREFLIGHT.read_text(encoding="utf-8"))

    def test_preflight_accepts_the_required_term_bound_bot_healthcheck(self) -> None:
        """A healthy fenced bot must pass the post-cutover guard gate."""

        static = static_services()[1]
        labels = {
            "com.docker.compose.project": "trading_bot_wa_fi_writer_2c08",
            "com.docker.compose.service": "bot",
        }
        expectation = preflight.RuntimeServiceExpectation(
            name=static.name,
            container_name=static.container_name,
            container_id="a" * 64,
            image_ref=static.image_ref,
            image_id=static.image_id,
            labels_sha256=preflight.lease_agent._runtime_binding_hash(labels),
        )
        config = static_config()
        environment = preflight._expected_service_environment("bot")
        inspected = {
            "Id": expectation.container_id,
            "Image": expectation.image_id,
            "Config": {
                "Image": expectation.image_ref,
                "Env": [f"{key}={value}" for key, value in environment.items()],
                "Labels": labels,
            },
            "State": {
                "Running": True,
                "Status": "running",
                "Health": {"Status": "healthy"},
            },
            "HostConfig": {"RestartPolicy": {"Name": "no"}, "PortBindings": {}},
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(config.term_parent_directory),
                    "Destination": preflight.TERM_PATH,
                    "RW": False,
                    "Bind": {"CreateHostPath": False},
                }
            ],
        }

        with mock.patch.object(preflight, "_run_read_only", return_value=json.dumps(inspected)):
            preflight._inspect_container(config, expectation)

    def test_cutover_pre_config_has_only_static_image_bindings(self) -> None:
        """The pre-start config must be installable before Docker creates IDs."""

        payload = {
            "schema": preflight.PREFLIGHT_SCHEMA,
            "control_release_root": "/srv/control",
            "application_release_root": "/srv/releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
            "agent_config": "/etc/agent.json",
            "preflight_config": "/tmp/fenced-fi-preflight.json",
            "unit_file": str(preflight.APPROVED_FENCED_UNIT_FILE),
            "lease_file": "/var/lib/trading-bot-three-site/writer-terms/writer-lease.json",
            "runtime_env_file": "/etc/runtime.env",
            "term_parent_directory": "/var/lib/trading-bot-three-site/writer-terms",
            "app_local_port": 8000,
            "runtime": {
                "compose_file": "/srv/control/deploy/production/docker-compose.webapp-fi-writer-2c08.yml",
                "compose_project": "trading_bot_wa_fi_writer_2c08",
                "services": [
                    {
                        "name": service.name,
                        "container_name": service.container_name,
                        "image_ref": service.image_ref,
                        "image_id": service.image_id,
                    }
                    for service in static_services()
                ],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fenced-fi-preflight.json"
            payload["preflight_config"] = str(path)
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)
            loaded = preflight._load_config(path)

        self.assertEqual(loaded.services, static_services())
        self.assertFalse(any(hasattr(service, "container_id") for service in loaded.services))

    def test_cutover_pre_does_not_touch_post_start_runtime_identity(self) -> None:
        config = static_config()
        agent_config = SimpleNamespace(witness=SimpleNamespace(safety_margin_seconds=15))
        with (
            mock.patch.object(preflight, "_load_config", return_value=config),
            mock.patch.object(preflight, "_validate_release_layout"),
            mock.patch.object(preflight, "_validate_agent_config", return_value=agent_config),
            mock.patch.object(preflight, "_validate_installed_unit"),
            mock.patch.object(preflight, "_validate_static_image_bindings") as static_bindings,
            mock.patch.object(preflight, "_validate_legacy_scope_is_disabled"),
            mock.patch.object(preflight, "_validate_runtime_receipt") as runtime_receipt_check,
            mock.patch.object(preflight, "_validate_runtime_identity") as runtime_identity,
        ):
            result = preflight.run(config_path=Path("/etc/preflight.json"), phase="cutover-pre")

        self.assertEqual(result["status"], "ready")
        static_bindings.assert_called_once_with(config)
        runtime_receipt_check.assert_not_called()
        runtime_identity.assert_not_called()

    def test_guard_receipt_binds_runtime_ids_to_static_image_bindings(self) -> None:
        config = static_config()
        receipt = runtime_receipt(config)
        local_lease = SimpleNamespace(writer_epoch=7, lease_id="lease-7")
        with (
            mock.patch.object(preflight, "_secure_read", return_value=json.dumps(receipt).encode("utf-8")),
            mock.patch.object(preflight, "load_production_writer_lease", return_value=local_lease),
        ):
            runtime = preflight._validate_runtime_receipt(config)

        self.assertEqual([service.container_id for service in runtime], ["c" * 64, "d" * 64])
        self.assertEqual([service.image_ref for service in runtime], [service.image_ref for service in config.services])

    def test_guard_receipt_rejects_image_not_bound_by_static_preflight(self) -> None:
        config = static_config()
        receipt = runtime_receipt(config)
        containers = receipt["containers"]
        self.assertIsInstance(containers, dict)
        containers["bot"]["image_id"] = "sha256:" + "f" * 64
        unsigned = {key: value for key, value in receipt.items() if key != "runtime_receipt_sha256"}
        receipt["runtime_receipt_sha256"] = preflight.lease_agent._runtime_binding_hash(unsigned)
        with mock.patch.object(preflight, "_secure_read", return_value=json.dumps(receipt).encode("utf-8")):
            with self.assertRaisesRegex(
                preflight.FencedFiWriterPreflightError,
                "does not match the static image binding",
            ):
                preflight._validate_runtime_receipt(config)


if __name__ == "__main__":
    unittest.main()
