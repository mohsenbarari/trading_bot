from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
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
AGENT = ROOT / "scripts/production_writer_lease_agent.py"


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
            image_repo_digest="registry.example.invalid/trading-bot-app@sha256:" + "c" * 64,
            image_id="sha256:" + "a" * 64,
        ),
        preflight.StaticServiceExpectation(
            name="bot",
            container_name=preflight.FENCED_CONTAINER_NAMES["bot"],
            image_ref="registry.example.invalid/trading-bot-bot:2c08",
            image_repo_digest="registry.example.invalid/trading-bot-bot@sha256:" + "d" * 64,
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
        runtime_env_sha256="f" * 64,
        term_parent_directory=Path("/var/lib/trading-bot-three-site/writer-terms"),
        app_local_port=18001,
        release_identity=preflight.FencedFiReleaseIdentityInputs(
            descriptor_path=Path("/etc/trading-bot-three-site/release-identity.json"),
            authority_path=Path("/etc/trading-bot-three-site/release-identity-authority.pub"),
            expected_identity_sha256="e" * 64,
        ),
        runtime_resources=preflight.FencedFiRuntimeResources(
            network_name="trading_bot_fi_runtime",
            uploads_volume="trading_bot_fi_uploads",
            audit_volume="trading_bot_fi_audit",
        ),
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
        self.assertIn("WA_FI_WRITER_APP_LOCAL_PORT=18001", env)
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
        self.assertEqual(
            "/etc/trading-bot-three-site/webapp-fi-fenced-writer-preflight.json",
            config["fenced_preflight_config"],
        )
        self.assertEqual(config["witness"]["lease_duration_seconds"], 60)
        self.assertEqual(config["witness"]["safety_margin_seconds"], 15)
        self.assertEqual(config["witness"]["renew_interval_seconds"], 10)

    def test_guard_has_a_distinct_unit_and_post_health_preflight(self) -> None:
        unit = GUARD_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "Conflicts=trading-bot-production-writer-fi-lease-guard.service",
            unit,
        )
        self.assertNotIn("ExecStartPre=", unit)
        self.assertIn("production_writer_lease_agent.py", unit)
        self.assertIn("_run_fenced_fi_guard_start_preflight", AGENT.read_text(encoding="utf-8"))
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
            "runtime_env_sha256": "f" * 64,
            "term_parent_directory": "/var/lib/trading-bot-three-site/writer-terms",
            "app_local_port": 18001,
            "release_identity": {
                "descriptor": "/etc/trading-bot-three-site/release-identity.json",
                "authority_public_key": "/etc/trading-bot-three-site/release-identity-authority.pub",
                "expected_identity_sha256": "e" * 64,
            },
            "runtime_resources": {
                "network_name": "trading_bot_fi_runtime",
                "uploads_volume": "trading_bot_fi_uploads",
                "audit_volume": "trading_bot_fi_audit",
            },
            "runtime": {
                "compose_file": "/srv/control/deploy/production/docker-compose.webapp-fi-writer-2c08.yml",
                "compose_project": "trading_bot_wa_fi_writer_2c08",
                "services": [
                    {
                        "name": service.name,
                        "container_name": service.container_name,
                        "image_ref": service.image_ref,
                        "image_repo_digest": service.image_repo_digest,
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
        signed_identity = SimpleNamespace()
        with (
            mock.patch.object(preflight, "_load_config", return_value=config),
            mock.patch.object(preflight, "_validate_release_layout"),
            mock.patch.object(preflight, "_validate_release_identity", return_value=signed_identity) as identity,
            mock.patch.object(preflight, "_validate_release_trees") as release_trees,
            mock.patch.object(preflight, "_reject_unfenced_legacy_application_release") as application_gate,
            mock.patch.object(preflight, "_validate_agent_config", return_value=agent_config),
            mock.patch.object(preflight, "_validate_runtime_environment_binding") as environment,
            mock.patch.object(preflight, "_validate_installed_unit"),
            mock.patch.object(preflight, "_validate_static_image_bindings") as static_bindings,
            mock.patch.object(preflight, "_validate_legacy_scope_is_disabled"),
            mock.patch.object(preflight, "_validate_fenced_runtime_scope_is_absent") as clean_scope,
            mock.patch.object(preflight, "_validate_runtime_receipt") as runtime_receipt_check,
            mock.patch.object(preflight, "_validate_runtime_identity") as runtime_identity,
        ):
            result = preflight.run(config_path=Path("/etc/preflight.json"), phase="cutover-pre")

        self.assertEqual(result["status"], "ready")
        identity.assert_called_once_with(config)
        release_trees.assert_called_once()
        application_gate.assert_called_once_with(signed_identity)
        environment.assert_called_once_with(config, agent_config)
        static_bindings.assert_called_once_with(config)
        clean_scope.assert_called_once_with()
        runtime_receipt_check.assert_not_called()
        runtime_identity.assert_not_called()

    def test_guard_start_rechecks_the_local_term_after_runtime_inspection(self) -> None:
        config = static_config()
        agent_config = SimpleNamespace(witness=SimpleNamespace(safety_margin_seconds=15))
        signed_identity = SimpleNamespace()
        runtime_services = ()
        with (
            mock.patch.object(preflight, "_load_config", return_value=config),
            mock.patch.object(preflight, "_validate_release_layout"),
            mock.patch.object(preflight, "_validate_release_identity", return_value=signed_identity),
            mock.patch.object(preflight, "_validate_release_trees"),
            mock.patch.object(preflight, "_reject_unfenced_legacy_application_release"),
            mock.patch.object(preflight, "_validate_installed_unit"),
            mock.patch.object(preflight, "_validate_agent_config", return_value=agent_config),
            mock.patch.object(preflight, "_validate_runtime_environment_binding"),
            mock.patch.object(preflight, "_validate_static_image_bindings"),
            mock.patch.object(preflight, "_validate_legacy_scope_is_disabled"),
            mock.patch.object(preflight, "_validate_live_local_lease") as live_lease,
            mock.patch.object(
                preflight,
                "_validate_runtime_receipt",
                return_value=runtime_services,
            ) as receipt,
            mock.patch.object(preflight, "_validate_runtime_identity") as runtime_identity,
        ):
            result = preflight.run(config_path=Path("/etc/preflight.json"), phase="guard-start")

        self.assertEqual(result["phase"], "guard-start")
        self.assertEqual(live_lease.call_count, 2)
        live_lease.assert_has_calls(
            [mock.call(config, agent_config), mock.call(config, agent_config)]
        )
        receipt.assert_called_once_with(config)
        runtime_identity.assert_called_once_with(config, runtime_services)

    def test_runtime_environment_hash_and_resource_binding_are_exact(self) -> None:
        runtime_env = b"reviewed private runtime environment\n"
        config = replace(
            static_config(),
            runtime_env_sha256=hashlib.sha256(runtime_env).hexdigest(),
        )
        values = {
            "RELEASE_SHA": preflight.lease_agent.WA_IR_APPLICATION_RELEASE_SHA,
            "WA_FI_WRITER_APP_IMAGE": config.services[0].image_ref,
            "WA_FI_WRITER_BOT_IMAGE": config.services[1].image_ref,
            "WA_FI_WRITER_RUNTIME_ENV_FILE": str(config.runtime_env_file),
            "WA_FI_WRITER_APPLICATION_RELEASE_ROOT": str(config.application_release_root),
            "WA_FI_WRITER_TERM_PARENT_DIRECTORY": str(config.term_parent_directory),
            "WA_FI_WRITER_RUNTIME_NETWORK_NAME": config.runtime_resources.network_name,
            "WA_FI_WRITER_UPLOADS_VOLUME": config.runtime_resources.uploads_volume,
            "WA_FI_WRITER_AUDIT_VOLUME": config.runtime_resources.audit_volume,
            "WA_FI_WRITER_APP_LOCAL_PORT": "18001",
            "APPLICATION_WRITER_TERM_SAFETY_MARGIN_SECONDS": "15",
            "APPLICATION_WRITER_TERM_MAX_LEASE_DURATION_SECONDS": "60",
        }
        with (
            mock.patch.object(preflight, "_secure_read", return_value=runtime_env),
            mock.patch.object(
                preflight.lease_agent,
                "_verify_fenced_fi_runtime_environment",
                return_value=values,
            ),
        ):
            preflight._validate_runtime_environment_binding(config, SimpleNamespace())

        wrong_root = dict(values)
        wrong_root["WA_FI_WRITER_APPLICATION_RELEASE_ROOT"] = "/srv/other/" + preflight.lease_agent.WA_IR_APPLICATION_RELEASE_SHA
        with (
            mock.patch.object(preflight, "_secure_read", return_value=runtime_env),
            mock.patch.object(
                preflight.lease_agent,
                "_verify_fenced_fi_runtime_environment",
                return_value=wrong_root,
            ),
            self.assertRaisesRegex(
                preflight.FencedFiWriterPreflightError,
                "does not bind the reviewed release resources",
            ),
        ):
            preflight._validate_runtime_environment_binding(config, SimpleNamespace())

    def test_rendered_runtime_binds_reviewed_external_network_and_writable_volumes(self) -> None:
        config = static_config()

        def rendered_service(name: str) -> dict[str, object]:
            service = next(item for item in config.services if item.name == name)
            volumes: list[dict[str, object]] = [
                {
                    "type": "bind",
                    "source": str(config.application_release_root / "trading_settings.json"),
                    "target": "/app/trading_settings.json",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": str(config.term_parent_directory),
                    "target": preflight.TERM_PATH,
                    "read_only": True,
                    "bind": {"create_host_path": False},
                },
            ]
            result: dict[str, object] = {
                "image": service.image_ref,
                "container_name": service.container_name,
                "restart": "no",
                "pull_policy": "never",
                "profiles": [preflight.lease_agent.WA_FI_FENCED_WRITER_PROFILE],
                "environment": preflight._expected_service_environment(name),
                "volumes": volumes,
                "networks": {"runtime": None},
            }
            if name == "app":
                volumes[:0] = [
                    {"type": "volume", "source": "writer_uploads_data", "target": "/app/uploads"},
                    {"type": "volume", "source": "writer_audit_data", "target": "/app/audit_trail"},
                ]
                result["ports"] = [{
                    "host_ip": "127.0.0.1",
                    "target": 8000,
                    "published": "18001",
                    "protocol": "tcp",
                }]
                result["healthcheck"] = {}
            return result

        payload = {
            "services": {name: rendered_service(name) for name in preflight.FENCED_SERVICES},
            "networks": {"runtime": {"external": True, "name": config.runtime_resources.network_name}},
            "volumes": {
                "writer_uploads_data": {"external": True, "name": config.runtime_resources.uploads_volume},
                "writer_audit_data": {"external": True, "name": config.runtime_resources.audit_volume},
            },
        }
        with mock.patch.object(preflight, "_compose_config", return_value=payload):
            preflight._validate_rendered_runtime(config)

        payload["networks"]["runtime"]["name"] = "unexpected_runtime_network"
        with (
            mock.patch.object(preflight, "_compose_config", return_value=payload),
            self.assertRaisesRegex(
                preflight.FencedFiWriterPreflightError,
                "reviewed external network",
            ),
        ):
            preflight._validate_rendered_runtime(config)

    def test_signed_identity_binds_fixed_2c08_roots_compose_and_images(self) -> None:
        config = static_config()
        compose_bytes = b"services: {}\n"
        identity = SimpleNamespace(
            release_sha=preflight.lease_agent.WA_IR_APPLICATION_RELEASE_SHA,
            application_release_root=str(config.application_release_root),
            control_release_root=str(config.control_release_root),
            compose_relative_path=str(preflight.FENCED_COMPOSE_RELATIVE_PATH),
            compose_sha256=hashlib.sha256(compose_bytes).hexdigest(),
            app_image_repo_digest=config.services[0].image_repo_digest,
            app_image_id=config.services[0].image_id,
            bot_image_repo_digest=config.services[1].image_repo_digest,
            bot_image_id=config.services[1].image_id,
        )
        with (
            mock.patch.object(
                preflight.release_identity_verifier,
                "load_verified_fenced_fi_release_identity",
                return_value=identity,
            ) as verified,
            mock.patch.object(preflight, "_secure_read", return_value=compose_bytes),
        ):
            preflight._validate_release_identity(config)

        verified.assert_called_once_with(
            descriptor_path=config.release_identity.descriptor_path,
            authority_path=config.release_identity.authority_path,
            expected_identity_sha256=config.release_identity.expected_identity_sha256,
        )

    def test_signed_identity_refuses_a_non_2c08_release_before_static_images(self) -> None:
        config = static_config()
        identity = SimpleNamespace(
            release_sha="a" * 40,
            application_release_root=str(config.application_release_root),
            control_release_root=str(config.control_release_root),
            compose_relative_path=str(preflight.FENCED_COMPOSE_RELATIVE_PATH),
            compose_sha256="b" * 64,
            app_image_repo_digest=config.services[0].image_repo_digest,
            app_image_id=config.services[0].image_id,
            bot_image_repo_digest=config.services[1].image_repo_digest,
            bot_image_id=config.services[1].image_id,
        )
        with mock.patch.object(
            preflight.release_identity_verifier,
            "load_verified_fenced_fi_release_identity",
            return_value=identity,
        ), self.assertRaisesRegex(
            preflight.FencedFiWriterPreflightError,
            "does not bind the fixed 2c08 runtime",
        ):
            preflight._validate_release_identity(config)

    def test_fixed_2c08_application_is_a_hard_pre_start_block(self) -> None:
        with self.assertRaisesRegex(
            preflight.FencedFiWriterPreflightError,
            "not term-fenced or schema-safe",
        ):
            preflight._reject_unfenced_legacy_application_release(
                SimpleNamespace(release_sha=preflight.lease_agent.WA_IR_APPLICATION_RELEASE_SHA)
            )

        # A later release still needs its own signed identity and real-image
        # capability gate; this narrow predicate only prevents the known
        # unsafe 2c08 tree from being made runnable by host-side controls.
        preflight._reject_unfenced_legacy_application_release(
            SimpleNamespace(release_sha="a" * 40)
        )

    def test_signed_identity_requires_clean_matching_application_and_control_git_trees(self) -> None:
        config = static_config()
        identity = SimpleNamespace(
            release_sha="a" * 40,
            release_tree_sha="b" * 40,
            control_release_sha="c" * 40,
            control_release_tree_sha="d" * 40,
        )
        with mock.patch.object(
            preflight,
            "_run_read_only",
            side_effect=[
                identity.release_sha + "\n",
                identity.release_tree_sha + "\n",
                "",
                identity.control_release_sha + "\n",
                identity.control_release_tree_sha + "\n",
                "",
            ],
        ):
            preflight._validate_release_trees(config, identity)

        with (
            mock.patch.object(
                preflight,
                "_run_read_only",
                side_effect=["x" * 40 + "\n", identity.release_tree_sha + "\n"],
            ),
            self.assertRaisesRegex(
                preflight.FencedFiWriterPreflightError,
                "application release Git identity does not match",
            ),
        ):
            preflight._validate_release_trees(config, identity)

    def test_static_image_requires_the_signed_repository_digest_as_well_as_id(self) -> None:
        config = static_config()
        expectation = config.services[0]
        with mock.patch.object(
            preflight,
            "_run_read_only",
            side_effect=[expectation.image_id, json.dumps([expectation.image_repo_digest])],
        ):
            preflight._inspect_image(config, expectation)

        with mock.patch.object(
            preflight,
            "_run_read_only",
            side_effect=[expectation.image_id, json.dumps([])],
        ), self.assertRaisesRegex(
            preflight.FencedFiWriterPreflightError,
            "repository digest does not match",
        ):
            preflight._inspect_image(config, expectation)

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
