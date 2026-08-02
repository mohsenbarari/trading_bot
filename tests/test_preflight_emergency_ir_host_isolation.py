"""Focused no-mutation tests for the Emergency WA-IR host collision gate."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = ROOT / "scripts" / "preflight_emergency_ir_host_isolation.py"
SOURCE_BASE_COMPOSE = ROOT / "deploy/emergency-ir/docker-compose.standalone.yml"
SOURCE_SMS_COMPOSE = ROOT / "deploy/emergency-ir/docker-compose.sms-otp.yml"
SPEC = importlib.util.spec_from_file_location(
    "preflight_emergency_ir_host_isolation",
    PREFLIGHT_PATH,
)
assert SPEC and SPEC.loader
PREFLIGHT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PREFLIGHT
SPEC.loader.exec_module(PREFLIGHT)
REAL_REQUIRE_TRUSTED_EXECUTABLE = PREFLIGHT._require_trusted_executable


def _write(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def _completed(command: object, stdout: str) -> subprocess.CompletedProcess[object]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def _runtime_env(*, secret: str = "not-emitted") -> bytes:
    return (
        f"COMPOSE_PROJECT_NAME={PREFLIGHT.PROJECT_NAME}\n"
        f"EMERGENCY_RUNTIME_ENV_FILE={PREFLIGHT.EMERGENCY_RUNTIME_ENV_FILE}\n"
        f"EMERGENCY_APP_PORT={PREFLIGHT.EMERGENCY_APP_PORT}\n"
        f"EMERGENCY_TRADING_SETTINGS_FILE={PREFLIGHT.EMERGENCY_TRADING_SETTINGS_FILE}\n"
        f"JWT_SECRET_KEY={secret}\n"
    ).encode("utf-8")


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("ascii")


def _container(
    identifier: str,
    *,
    name: str = "permanent-wa-ir-api",
    project: str = "trading-bot-iran",
    mounts: list[dict[str, object]] | None = None,
    networks: dict[str, object] | None = None,
    host_port: str | None = None,
) -> dict[str, object]:
    bindings: dict[str, object] = {}
    if host_port is not None:
        bindings = {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]}
    return {
        "Id": identifier,
        "Name": "/" + name,
        "Config": {"Labels": {"com.docker.compose.project": project}},
        "Mounts": [] if mounts is None else mounts,
        "HostConfig": {"PortBindings": bindings},
        "NetworkSettings": {"Networks": {"trading-bot-iran-net": {}} if networks is None else networks, "Ports": bindings},
    }


def _volume(name: str, *, project: str = "trading-bot-iran") -> dict[str, object]:
    return {"Name": name, "Labels": {"com.docker.compose.project": project}}


def _network(
    identifier: str,
    name: str,
    subnet: str,
    *,
    project: str = "trading-bot-iran",
) -> dict[str, object]:
    return {
        "Id": identifier,
        "Name": name,
        "Labels": {"com.docker.compose.project": project},
        "IPAM": {"Config": [{"Subnet": subnet}]},
    }


class InventoryRunner:
    def __init__(
        self,
        *,
        containers: list[dict[str, object]] | None = None,
        volumes: list[dict[str, object]] | None = None,
        networks: list[dict[str, object]] | None = None,
        listeners: str = "LISTEN 0 4096 127.0.0.1:8213 0.0.0.0:*\n",
    ) -> None:
        self.containers = containers or []
        self.volumes = volumes or []
        self.networks = networks or []
        self.listeners = listeners
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
        observed = tuple(command)
        self.commands.append(observed)
        docker = PREFLIGHT._docker_command
        if observed == docker("ps", "-a", "--no-trunc", "--format", "{{.ID}}"):
            return _completed(command, "".join(str(item["Id"]) + "\n" for item in self.containers))
        if observed == docker("volume", "ls", "-q"):
            return _completed(command, "".join(str(item["Name"]) + "\n" for item in self.volumes))
        if observed == docker("network", "ls", "-q"):
            return _completed(command, "".join(str(item["Id"]) + "\n" for item in self.networks))
        if observed == (PREFLIGHT.SS_BINARY, "-H", "-ltn"):
            return _completed(command, self.listeners)
        for prefix, records, selector in (
            (docker("inspect"), self.containers, "Id"),
            (docker("volume", "inspect"), self.volumes, "Name"),
            (docker("network", "inspect"), self.networks, "Id"),
        ):
            if observed[: len(prefix)] == prefix:
                identifiers = set(observed[len(prefix) :])
                selected = [item for item in records if str(item[selector]) in identifiers]
                if len(selected) != len(identifiers):
                    raise AssertionError(f"unexpected inventory lookup: {observed!r}")
                return _completed(command, json.dumps(selected))
        raise AssertionError(f"unexpected command: {observed!r}")


class EmergencyHostIsolationPreflightCliTests(unittest.TestCase):
    def _run_help(self, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *flags, str(PREFLIGHT_PATH), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_cli_accepts_only_isolated_no_bytecode_execution(self) -> None:
        result = self._run_help("-I", "-B")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--runtime-env", result.stdout)

    def test_cli_rejects_missing_isolation_or_no_bytecode_flag_before_parsing(self) -> None:
        for flags in ((), ("-I",), ("-B",)):
            with self.subTest(flags=flags):
                result = self._run_help(*flags)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    "must be launched with python3 -I -B",
                    result.stderr,
                )


@unittest.skipUnless(os.geteuid() == 0, "host preflight enforces root-owned inputs")
class EmergencyHostIsolationPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="emergency-host-isolation-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.runtime = self.root / "runtime.env"
        self.compose = self.root / "docker-compose.standalone.yml"
        self.sms_compose = self.root / "docker-compose.sms-otp.yml"
        _write(self.runtime, _runtime_env(), mode=0o600)
        shutil.copyfile(SOURCE_BASE_COMPOSE, self.compose)
        shutil.copyfile(SOURCE_SMS_COMPOSE, self.sms_compose)
        self.compose.chmod(0o644)
        self.sms_compose.chmod(0o644)
        self.executable_guard = patch.object(PREFLIGHT, "_require_trusted_executable")
        self.mock_executable_guard = self.executable_guard.start()

    def tearDown(self) -> None:
        self.executable_guard.stop()
        self.temporary.cleanup()

    def _evaluate(self, runner: InventoryRunner, *, profile: str = "telegram-only") -> dict[str, object]:
        return PREFLIGHT.evaluate_host_isolation(
            runtime_env=self.runtime,
            compose=self.compose,
            profile=profile,
            sms_compose=self.sms_compose if profile == "sms-otp" else None,
            runner=runner,
        )

    def test_canonical_json_contract_passes_and_all_inventory_commands_are_read_only(self) -> None:
        runner = InventoryRunner(
            containers=[_container("a" * 64)],
            volumes=[_volume("trading-bot-postgres")],
            networks=[_network("b" * 64, "trading-bot-iran-net", "172.30.0.0/24")],
        )
        report = self._evaluate(runner)
        self.assertEqual("ready", report["status"])
        self.assertEqual([], report["collisions"])
        self.assertEqual([], report["mutating_actions"])
        self.assertFalse(report["docker_or_service_changed"])
        self.assertFalse(report["authorizes_activation"])
        self.assertTrue(all(PREFLIGHT._is_permitted_read_only_command(command) for command in runner.commands))
        self.assertEqual(len(runner.commands), self.mock_executable_guard.call_count)
        self.assertTrue(
            all(call.args[0] in {Path(PREFLIGHT.DOCKER_BINARY), Path(PREFLIGHT.SS_BINARY)} for call in self.mock_executable_guard.call_args_list)
        )
        forbidden = {"compose", "up", "start", "pull", "load", "rm", "down", "restart", "stop", "kill"}
        self.assertFalse(any(forbidden & set(command) for command in runner.commands))
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("not-emitted", serialized)
        self.assertNotIn("JWT_SECRET_KEY", serialized)

    def test_detects_project_container_and_volume_collisions(self) -> None:
        runner = InventoryRunner(
            containers=[
                _container("a" * 64, project=PREFLIGHT.PROJECT_NAME),
                _container("b" * 64, name="trading-bot-emergency-ir-api-1"),
            ],
            volumes=[_volume(PREFLIGHT.BASE_VOLUMES[0])],
            networks=[_network("c" * 64, "trading-bot-iran-net", "172.30.0.0/24")],
        )
        report = self._evaluate(runner)
        self.assertEqual("blocked", report["status"])
        self.assertEqual(
            ["docker-compose-project", "docker-container-name", "docker-volume-name"],
            report["collisions"],
        )

    def test_detects_name_and_subnet_network_collisions(self) -> None:
        runner = InventoryRunner(
            networks=[
                _network("a" * 64, PREFLIGHT.BASE_NETWORKS[0], "172.30.0.0/24"),
                _network("b" * 64, "permanent-overlap", "172.29.250.8/29"),
            ]
        )
        report = self._evaluate(runner)
        self.assertEqual("blocked", report["status"])
        self.assertEqual(["docker-network-name", "docker-network-subnet"], report["collisions"])

    def test_detects_docker_and_non_docker_port_plus_bind_path_collisions(self) -> None:
        runner = InventoryRunner(
            containers=[
                _container(
                    "a" * 64,
                    host_port="18000",
                    mounts=[{"Type": "bind", "Source": "/srv/trading-bot-emergency/current"}],
                )
            ],
            listeners="LISTEN 0 4096 127.0.0.1:18000 0.0.0.0:*\n",
        )
        report = self._evaluate(runner)
        self.assertEqual("blocked", report["status"])
        self.assertEqual(
            ["docker-bind-path", "docker-host-port", "host-listening-port"],
            report["collisions"],
        )

    def test_sms_profile_adds_sms_network_collision_surface(self) -> None:
        runner = InventoryRunner(
            networks=[_network("a" * 64, "permanent-sms-overlap", "172.29.252.0/29")]
        )
        report = self._evaluate(runner, profile="sms-otp")
        self.assertEqual("blocked", report["status"])
        self.assertEqual(["docker-network-subnet"], report["collisions"])

    def test_refuses_broadened_compose_and_non_allowlisted_command(self) -> None:
        value = json.loads(self.compose.read_text(encoding="utf-8"))
        value["services"]["api"]["container_name"] = "unsafe"
        self.compose.write_bytes(_canonical_json(value))
        self.compose.chmod(0o644)
        with self.assertRaisesRegex(PREFLIGHT.EmergencyHostIsolationError, "fixed isolation contract"):
            self._evaluate(InventoryRunner())
        self.assertFalse(
            PREFLIGHT._is_permitted_read_only_command(
                PREFLIGHT._docker_command("compose", "up", "-d")
            )
        )
        self.assertFalse(
            PREFLIGHT._is_permitted_read_only_command(
                PREFLIGHT._docker_command("volume", "rm", "trading-bot-emergency-ir-postgres")
            )
        )

    def test_rejects_canonical_json_nested_topology_drift(self) -> None:
        value = json.loads(self.compose.read_text(encoding="utf-8"))
        value["networks"]["emergency_ir_internal"]["ipam"]["config"][0]["subnet"] = (
            "172.29.249.0/28"
        )
        self.compose.write_bytes(_canonical_json(value))
        self.compose.chmod(0o644)
        with self.assertRaisesRegex(PREFLIGHT.EmergencyHostIsolationError, "fixed isolation contract"):
            self._evaluate(InventoryRunner())

    def test_runtime_input_remains_private_and_root_owned(self) -> None:
        self.runtime.chmod(0o644)
        with self.assertRaisesRegex(PREFLIGHT.EmergencyHostIsolationError, "root-controlled regular file"):
            self._evaluate(InventoryRunner())
        self.assertEqual(0o644, stat.S_IMODE(self.runtime.stat().st_mode))

    def test_bind_path_resolving_into_emergency_root_is_a_collision(self) -> None:
        protected = self.root / "protected"
        protected.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(protected, target_is_directory=True)
        runner = InventoryRunner(
            containers=[_container("a" * 64, mounts=[{"Type": "bind", "Source": str(alias / "current")}])]
        )
        with patch.object(PREFLIGHT, "PROTECTED_HOST_ROOTS", (str(protected),)):
            report = self._evaluate(runner)
        self.assertEqual("blocked", report["status"])
        self.assertEqual(["docker-bind-path"], report["collisions"])

    def test_fixed_inspection_binary_must_be_root_controlled_and_non_writable(self) -> None:
        binary = self.root / "docker"
        _write(binary, b"#!/bin/false\n", mode=0o777)
        with self.assertRaisesRegex(PREFLIGHT.EmergencyHostIsolationError, "root-controlled executable"):
            REAL_REQUIRE_TRUSTED_EXECUTABLE(binary, label="test inspection binary")


if __name__ == "__main__":
    unittest.main()
