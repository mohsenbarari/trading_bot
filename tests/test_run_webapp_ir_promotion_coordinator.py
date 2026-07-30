from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_webapp_ir_promotion_coordinator.py"
SYSTEMD_UNIT = ROOT / "deploy/systemd/trading-bot-production-writer-ir-promotion-watch.service"
GUARD_SYSTEMD_UNIT = ROOT / "deploy/systemd/trading-bot-production-writer-lease-guard.service"
COORDINATOR_EXAMPLE = ROOT / "deploy/production/webapp-ir-promotion-coordinator.json.example"
SPEC = importlib.util.spec_from_file_location("run_webapp_ir_promotion_coordinator", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_file(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


class PromotionRunner:
    def __init__(self, *, receipt: Path, listener_status: str = "reloaded") -> None:
        self.receipt = receipt
        self.listener_status = listener_status
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        normalized = tuple(command)
        self.calls.append(normalized)
        script = Path(normalized[3]).name
        if script == "production_writer_lease_agent.py":
            payload = {"status": "activated", "site": "webapp_ir", "action": "promote_ir"}
        elif script == "activate_webapp_ir_promoted_listener.py":
            if self.listener_status == "reloaded":
                write_file(self.receipt, '{"status":"reloaded"}\n')
            payload = {
                "status": self.listener_status,
                "external_route_changed": False,
                "receipt_path": str(self.receipt),
            }
        elif script == "route_webapp_ir_from_promotion_proof.py":
            payload = {"status": "switched", "applied": True}
        else:
            raise AssertionError(f"unexpected command {normalized}")
        return subprocess.CompletedProcess(normalized, 0, json.dumps(payload) + "\n", "")


class WebappIrPromotionCoordinatorTests(unittest.TestCase):
    def make_fixture(self) -> dict[str, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)

        control_sha = "c" * 40
        release = root / "control-releases" / control_sha
        application_release = root / "releases" / MODULE.APPLICATION_RELEASE_SHA
        scripts = release / "scripts"
        scripts.mkdir(parents=True)
        application_release.mkdir(parents=True)
        release.chmod(0o755)
        application_release.chmod(0o755)
        scripts.chmod(0o755)
        for name in (
            "production_writer_lease_agent.py",
            "activate_webapp_ir_promoted_listener.py",
            "route_webapp_ir_from_promotion_proof.py",
        ):
            write_file(scripts / name, "# fixture\n", 0o700)

        secure = root / "secure"
        state = root / "state"
        audit = root / "audit"
        for directory in (secure, state, audit):
            directory.mkdir()
            directory.chmod(0o700)
        proof_directory = state / "proofs"
        proof_directory.mkdir()
        proof_directory.chmod(0o700)

        writer_config = secure / "writer.json"
        restore_receipt = state / "restore.json"
        active_snapshot = state / "active-snapshot.json"
        listener_config = secure / "listener.env"
        token = secure / "route-token"
        provenance_receipt = state / "release-provenance.json"
        for path in (writer_config, restore_receipt, active_snapshot, listener_config, token, provenance_receipt):
            write_file(path, "fixture\n")
        listener_receipt = state / "listener.json"
        route_audit = audit / "route.jsonl"
        config = secure / "coordinator.json"
        payload = {
            "schema": MODULE.SCHEMA,
            "control_release_root": str(release),
            "control_release_sha": control_sha,
            "application_release_sha": MODULE.APPLICATION_RELEASE_SHA,
            "application_release_root": str(application_release),
            "release_provenance_receipt": str(provenance_receipt),
            "writer_agent_config": str(writer_config),
            "restore_receipt": str(restore_receipt),
            "active_snapshot": str(active_snapshot),
            "proof_directory": str(proof_directory),
            "listener_config": str(listener_config),
            "listener_receipt": str(listener_receipt),
            "route_token_file": str(token),
            "route_audit_log": str(route_audit),
            "poll_seconds": 2,
        }
        write_file(config, json.dumps(payload) + "\n")
        return {
            "root": root,
            "release": release,
            "application_release": application_release,
            "control_sha": Path(control_sha),
            "scripts": scripts,
            "writer_config": writer_config,
            "restore_receipt": restore_receipt,
            "active_snapshot": active_snapshot,
            "proof_directory": proof_directory,
            "listener_config": listener_config,
            "listener_receipt": listener_receipt,
            "token": token,
            "route_audit": route_audit,
            "config": config,
        }

    def installed_receipt(self, fixture: dict[str, Path]) -> dict:
        return {
            "application": {
                "release_sha": MODULE.APPLICATION_RELEASE_SHA,
                "release_root": str(fixture["application_release"]),
            },
            "control": {
                "release_sha": fixture["control_sha"].name,
                "release_root": str(fixture["release"]),
            },
        }

    def execute_coordinator(self, fixture: dict[str, Path], *, apply: bool, runner: PromotionRunner) -> dict:
        with (
            mock.patch.object(MODULE, "CONTROL_RUNTIME_ROOT", fixture["release"]),
            mock.patch.object(MODULE, "load_installed_release_receipt", return_value=self.installed_receipt(fixture)),
        ):
            return MODULE.run_coordinator(fixture["config"], apply=apply, command_runner=runner)

    def test_apply_runs_only_the_three_fixed_stages_in_order(self) -> None:
        fixture = self.make_fixture()
        runner = PromotionRunner(receipt=fixture["listener_receipt"])

        result = self.execute_coordinator(fixture, apply=True, runner=runner)

        python = "/usr/bin/python3"
        writer = str(fixture["scripts"] / "production_writer_lease_agent.py")
        listener = str(fixture["scripts"] / "activate_webapp_ir_promoted_listener.py")
        route = str(fixture["scripts"] / "route_webapp_ir_from_promotion_proof.py")
        self.assertEqual(
            runner.calls,
            [
                (
                    python,
                    "-I",
                    "-B",
                    writer,
                    "--config",
                    str(fixture["writer_config"]),
                    "promote-watch",
                    "--restore-receipt",
                    str(fixture["restore_receipt"]),
                    "--active-snapshot",
                    str(fixture["active_snapshot"]),
                    "--proof-directory",
                    str(fixture["proof_directory"]),
                    "--poll-seconds",
                    "2",
                ),
                (
                    python,
                    "-I",
                    "-B",
                    listener,
                    "--config",
                    str(fixture["listener_config"]),
                    "--apply",
                    "--json",
                ),
                (
                    python,
                    "-I",
                    "-B",
                    route,
                    "--proof-directory",
                    str(fixture["proof_directory"]),
                    "--token-file",
                    str(fixture["token"]),
                    "--audit-log",
                    str(fixture["route_audit"]),
                    "--listener-receipt",
                    str(fixture["listener_receipt"]),
                    "--apply",
                ),
            ],
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["external_route_changed"])
        self.assertEqual(set(result["stages"]), {"promote_watch", "listener_activation", "route"})

    def test_watch_stage_can_use_the_persistent_runner_without_affecting_followups(self) -> None:
        fixture = self.make_fixture()
        watch_runner = PromotionRunner(receipt=fixture["listener_receipt"])
        stage_runner = PromotionRunner(receipt=fixture["listener_receipt"])

        with (
            mock.patch.object(MODULE, "CONTROL_RUNTIME_ROOT", fixture["release"]),
            mock.patch.object(MODULE, "load_installed_release_receipt", return_value=self.installed_receipt(fixture)),
        ):
            result = MODULE.run_coordinator(
                fixture["config"],
                apply=True,
                command_runner=stage_runner,
                watch_command_runner=watch_runner,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(watch_runner.calls), 1)
        self.assertIn("production_writer_lease_agent.py", watch_runner.calls[0][3])
        self.assertEqual(len(stage_runner.calls), 2)
        self.assertIn("activate_webapp_ir_promoted_listener.py", stage_runner.calls[0][3])
        self.assertIn("route_webapp_ir_from_promotion_proof.py", stage_runner.calls[1][3])

    def test_persistent_watch_runner_has_no_local_timeout_or_stderr_pipe(self) -> None:
        completed = subprocess.CompletedProcess(("fixed",), 0, "{}\n", None)
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            MODULE._run_watch(("fixed",))

        kwargs = run.call_args.kwargs
        self.assertNotIn("timeout", kwargs)
        self.assertNotIn("stderr", kwargs)

    def test_systemd_uses_only_the_complete_coordinator_sequence(self) -> None:
        unit = SYSTEMD_UNIT.read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=/etc/trading-bot-three-site/webapp-ir-control-release.env", unit)
        self.assertIn("WorkingDirectory=/", unit)
        self.assertIn(
            "/srv/trading-bot-three-site/control-dispatcher/manage_webapp_ir_release_provenance.py exec-bound-control",
            unit,
        )
        self.assertIn("--target promotion-coordinator", unit)
        self.assertIn("${WA_IR_CONTROL_RELEASE_ROOT}", unit)
        self.assertIn("${WA_IR_CONTROL_RELEASE_SHA}", unit)
        self.assertIn("${WA_IR_RELEASE_PROVENANCE_RECEIPT}", unit)
        self.assertNotIn("/releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5", unit)
        self.assertIn("Requires=trading-bot-production-writer-lease-guard.service", unit)
        self.assertNotIn("ExecStartPost=", unit)

        guard = GUARD_SYSTEMD_UNIT.read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=/etc/trading-bot-three-site/webapp-ir-control-release.env", guard)
        self.assertIn("WorkingDirectory=/", guard)
        self.assertIn(
            "/srv/trading-bot-three-site/control-dispatcher/manage_webapp_ir_release_provenance.py exec-bound-control",
            guard,
        )
        self.assertIn("--target lease-guard", guard)
        self.assertIn("${WA_IR_CONTROL_RELEASE_ROOT}", guard)
        self.assertIn("${WA_IR_CONTROL_RELEASE_SHA}", guard)
        self.assertIn("${WA_IR_RELEASE_PROVENANCE_RECEIPT}", guard)
        self.assertNotIn("/releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5", guard)
        for service in (unit, guard):
            working_directories = [
                line.split("=", 1)[1]
                for line in service.splitlines()
                if line.startswith("WorkingDirectory=")
            ]
            self.assertEqual(working_directories, ["/"])
            self.assertTrue(all("$" not in value for value in working_directories))

    def test_coordinator_config_requires_separate_control_and_application_identities(self) -> None:
        payload = json.loads(COORDINATOR_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(payload["application_release_sha"], MODULE.APPLICATION_RELEASE_SHA)
        self.assertIn("releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5", payload["application_release_root"])
        self.assertIn("control-releases/REPLACE_WITH_EXACT_CONTROL_GIT_SHA", payload["control_release_root"])
        self.assertEqual(payload["control_release_sha"], "REPLACE_WITH_EXACT_CONTROL_GIT_SHA")
        self.assertIn("release-provenance", payload["release_provenance_receipt"])

    def test_plan_runs_no_stage(self) -> None:
        fixture = self.make_fixture()
        runner = PromotionRunner(receipt=fixture["listener_receipt"])

        result = self.execute_coordinator(fixture, apply=False, runner=runner)

        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["application_release_sha"], MODULE.APPLICATION_RELEASE_SHA)
        self.assertEqual(result["control_release_sha"], fixture["control_sha"].name)
        self.assertFalse(result["external_route_changed"])
        self.assertEqual(runner.calls, [])

    def test_failed_promotion_stops_before_listener_and_route(self) -> None:
        fixture = self.make_fixture()

        class FailingPromotion(PromotionRunner):
            def __call__(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                normalized = tuple(command)
                self.calls.append(normalized)
                return subprocess.CompletedProcess(normalized, 1, '{"status":"blocked"}\n', "failed")

        runner = FailingPromotion(receipt=fixture["listener_receipt"])
        with self.assertRaisesRegex(MODULE.PromotionCoordinatorError, "promote-watch exited"):
            self.execute_coordinator(fixture, apply=True, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        self.assertFalse(fixture["listener_receipt"].exists())

    def test_listener_failure_or_missing_receipt_stops_before_route(self) -> None:
        fixture = self.make_fixture()
        failed_listener = PromotionRunner(receipt=fixture["listener_receipt"], listener_status="planned")
        with self.assertRaisesRegex(MODULE.PromotionCoordinatorError, "listener activation did not"):
            self.execute_coordinator(fixture, apply=True, runner=failed_listener)
        self.assertEqual(len(failed_listener.calls), 2)

        fixture = self.make_fixture()

        class MissingReceipt(PromotionRunner):
            def __call__(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                normalized = tuple(command)
                self.calls.append(normalized)
                script = Path(normalized[3]).name
                if script == "production_writer_lease_agent.py":
                    payload = {"status": "activated", "site": "webapp_ir"}
                elif script == "activate_webapp_ir_promoted_listener.py":
                    payload = {
                        "status": "reloaded",
                        "external_route_changed": False,
                        "receipt_path": str(self.receipt),
                    }
                else:
                    raise AssertionError("route must not run")
                return subprocess.CompletedProcess(normalized, 0, json.dumps(payload) + "\n", "")

        missing_receipt = MissingReceipt(receipt=fixture["listener_receipt"])
        with self.assertRaisesRegex(MODULE.PromotionCoordinatorError, "listener receipt does not exist"):
            self.execute_coordinator(fixture, apply=True, runner=missing_receipt)
        self.assertEqual(len(missing_receipt.calls), 2)

    def test_closed_config_rejects_arbitrary_command_or_nonprivate_token(self) -> None:
        fixture = self.make_fixture()
        payload = json.loads(fixture["config"].read_text(encoding="utf-8"))
        payload["ssh_command"] = "ssh root@example.invalid"
        write_file(fixture["config"], json.dumps(payload) + "\n")
        with self.assertRaisesRegex(MODULE.PromotionCoordinatorError, "unexpected ssh_command"):
            MODULE.load_config(fixture["config"])

        payload.pop("ssh_command")
        write_file(fixture["config"], json.dumps(payload) + "\n")
        fixture["token"].chmod(0o640)
        with self.assertRaisesRegex(MODULE.PromotionCoordinatorError, "route token file must be"):
            MODULE.load_config(fixture["config"])

    def test_receipt_or_runtime_root_mismatch_stops_before_any_stage(self) -> None:
        fixture = self.make_fixture()
        runner = PromotionRunner(receipt=fixture["listener_receipt"])
        invalid = self.installed_receipt(fixture)
        invalid["control"]["release_sha"] = "d" * 40
        with (
            mock.patch.object(MODULE, "CONTROL_RUNTIME_ROOT", fixture["release"]),
            mock.patch.object(MODULE, "load_installed_release_receipt", return_value=invalid),
        ):
            with self.assertRaisesRegex(MODULE.PromotionCoordinatorError, "does not bind"):
                MODULE.run_coordinator(fixture["config"], apply=True, command_runner=runner)
        self.assertEqual(runner.calls, [])

    def test_same_named_alternate_application_root_stops_before_any_stage(self) -> None:
        fixture = self.make_fixture()
        runner = PromotionRunner(receipt=fixture["listener_receipt"])
        alternate = fixture["root"] / "alternate-releases" / MODULE.APPLICATION_RELEASE_SHA
        alternate.mkdir(parents=True)
        alternate.chmod(0o755)
        payload = json.loads(fixture["config"].read_text(encoding="utf-8"))
        payload["application_release_root"] = str(alternate)
        write_file(fixture["config"], json.dumps(payload) + "\n")
        with (
            mock.patch.object(MODULE, "CONTROL_RUNTIME_ROOT", fixture["release"]),
            mock.patch.object(MODULE, "load_installed_release_receipt", return_value=self.installed_receipt(fixture)),
        ):
            with self.assertRaisesRegex(MODULE.PromotionCoordinatorError, "does not bind"):
                MODULE.run_coordinator(fixture["config"], apply=True, command_runner=runner)
        self.assertEqual(runner.calls, [])

    def test_implementation_does_not_expose_remote_or_arbitrary_command_knobs(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("boto3", "paramiko", "subprocess.shell", "shell=True", "scp ", "ssh "):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
