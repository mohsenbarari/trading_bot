from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from core.canonical_json import canonical_json_bytes
import scripts.orchestrate_production_shadow_nginx_generations as coordinator
from tests.test_production_shadow_nginx_generation import (
    OPERATION_ID,
    ProducerFixture,
    RELEASE_SHA,
    RELEASE_TREE_SHA,
)


WORKER_PAYLOAD = b"release-owned-nginx-worker-v1\n"
NONZERO_HASH = "1" * 64


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


class _CapturedStdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()


class TwoHostRunner:
    def __init__(
        self,
        *,
        role_manifests: dict[str, dict],
        known_hosts: Path,
        ssh_identity: Path,
    ) -> None:
        self.role_manifests = role_manifests
        self.known_hosts = known_hosts
        self.ssh_identity = ssh_identity
        self.calls: list[tuple[tuple[str, ...], int]] = []
        self.host_calls: list[tuple[str, str, str | None]] = []
        self.curl_calls: list[tuple[str, str]] = []
        self.states = {role: "legacy-normal" for role in coordinator.ROLE_ORDER}
        self.installed: set[str] = set()
        self.tested: set[tuple[str, str]] = set()
        self.failures: dict[tuple[str, str, str | None], int] = {}
        self.remote_directories = {
            os.fspath(coordinator.HOST_CONTROL_PARENT.parent): 0o755,
            os.fspath(
                coordinator.PROJECT_ROOT_PREFIX
                / OPERATION_ID
                / "incoming"
            ): 0o700,
        }
        self.remote_files: dict[str, tuple[bytes, int]] = {
            os.fspath(
                coordinator.PROJECT_ROOT_PREFIX
                / OPERATION_ID
                / "releases"
                / RELEASE_SHA
                / coordinator.WORKER_RELATIVE_PATH
            ): (WORKER_PAYLOAD, 0o755),
        }

    def fail_once(
        self,
        role: str,
        action: str,
        generation: str | None,
    ) -> None:
        self.failures[(role, action, generation)] = 1

    def _consume_failure(
        self,
        role: str,
        action: str,
        generation: str | None,
    ) -> bool:
        key = (role, action, generation)
        remaining = self.failures.get(key, 0)
        if not remaining:
            return False
        self.failures[key] = remaining - 1
        return True

    @staticmethod
    def _command_evidence() -> dict:
        return {
            "argv_sha256": NONZERO_HASH,
            "returncode": 0,
            "stdout_sha256": "2" * 64,
            "stderr_sha256": "3" * 64,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
        }

    def _identity(self, role: str) -> dict:
        manifest = self.role_manifests[role]
        return {
            "operation_id": manifest["operation_id"],
            "role": role,
            "expected_host": manifest["expected_host"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "manifest_sha256": hashlib.sha256(
                canonical_json_bytes(manifest)
            ).hexdigest(),
            "archive_sha256": manifest["archive"]["sha256"],
        }

    def _host_result(
        self,
        role: str,
        action: str,
        generation: str | None,
    ) -> coordinator.CommandResult:
        self.host_calls.append((role, action, generation))
        if self._consume_failure(role, action, generation):
            return coordinator.CommandResult(2, b"", b"simulated host failure")
        manifest = self.role_manifests[role]
        identity = self._identity(role)
        if action == "readback":
            state = self.states[role]
            document = {
                "schema": "production-shadow-nginx-host-readback-v1",
                "status": "read-back",
                **identity,
                "state": state,
                "generation_sha256": manifest["generation_sha256"][state],
                "enabled_inventory_sha256": "4" * 64,
                "enabled_inventory_count": 2 if role == "bot_fi" else 1,
                "active_configuration_mutated": False,
                "service_reloaded": False,
                "journal_sha256": "5" * 64,
            }
        elif action == "install":
            already = role in self.installed
            self.installed.add(role)
            document = {
                "schema": coordinator.GENERATION.HOST_ACTION_RESULT_SCHEMA,
                "status": "already-installed" if already else "installed",
                "action": action,
                "generation": None,
                "state": None,
                **identity,
                "active_configuration_mutated": False,
                "service_reloaded": False,
                "journal_sha256": "5" * 64,
            }
        elif action == "test":
            assert generation is not None
            key = (role, generation)
            already = key in self.tested
            self.tested.add(key)
            document = {
                "schema": coordinator.GENERATION.HOST_ACTION_RESULT_SCHEMA,
                "status": "already-tested" if already else "tested",
                "action": action,
                "generation": generation,
                "state": generation,
                **identity,
                "active_configuration_mutated": False,
                "service_reloaded": False,
                "journal_sha256": "5" * 64,
                "inventory_sha256": "6" * 64,
                "candidate_sha256": "7" * 64,
            }
            if not already:
                document["command"] = self._command_evidence()
        else:
            target = (
                "legacy-normal" if action == "restore" else generation
            )
            assert target is not None
            previous = self.states[role]
            already = previous == target
            if not already:
                self.states[role] = target
            document = {
                "schema": coordinator.GENERATION.HOST_ACTION_RESULT_SCHEMA,
                "status": "already-active" if already else "activated",
                "action": action,
                "generation": target,
                "state": target,
                **identity,
                "active_configuration_mutated": not already,
                "service_reloaded": not already,
                "journal_sha256": "5" * 64,
            }
            if not already:
                document["from_state"] = previous
                document["commands"] = {
                    "test": self._command_evidence(),
                    "reload": self._command_evidence(),
                }
        return coordinator.CommandResult(
            0,
            json.dumps(document, sort_keys=True).encode("utf-8") + b"\n",
            b"",
        )

    def _remote_command(
        self,
        arguments: tuple[str, ...],
    ) -> coordinator.CommandResult:
        command = arguments[0]
        if command == coordinator.PYTHON:
            role = arguments[arguments.index("--role") + 1]
            action = arguments[arguments.index("--action") + 1]
            generation = (
                arguments[arguments.index("--generation") + 1]
                if "--generation" in arguments
                else None
            )
            return self._host_result(role, action, generation)
        if command == "/usr/bin/stat":
            path = arguments[-1]
            output_format = arguments[1]
            if output_format == "--printf=%F:%u:%g:%a":
                if path not in self.remote_directories:
                    return coordinator.CommandResult(1, b"", b"missing")
                return coordinator.CommandResult(
                    0,
                    (
                        f"directory:0:0:"
                        f"{self.remote_directories[path]:o}"
                    ).encode("ascii"),
                    b"",
                )
            if path not in self.remote_files:
                return coordinator.CommandResult(1, b"", b"missing")
            payload, mode = self.remote_files[path]
            return coordinator.CommandResult(
                0,
                f"0:0:{mode:o}:1:{len(payload)}".encode("ascii"),
                b"",
            )
        if command == "/usr/bin/sha256sum":
            path = arguments[-1]
            if path not in self.remote_files:
                return coordinator.CommandResult(1, b"", b"missing")
            payload, _ = self.remote_files[path]
            output = (
                f"{hashlib.sha256(payload).hexdigest()}  {path}\n"
            ).encode("ascii")
            return coordinator.CommandResult(0, output, b"")
        if command == "/usr/bin/mkdir":
            path = arguments[-1]
            if path in self.remote_directories:
                return coordinator.CommandResult(1, b"", b"exists")
            if os.fspath(Path(path).parent) not in self.remote_directories:
                return coordinator.CommandResult(1, b"", b"missing parent")
            self.remote_directories[path] = 0o700
            return coordinator.CommandResult(0, b"", b"")
        if command == "/usr/bin/mv":
            source, destination = arguments[-2:]
            if source not in self.remote_files:
                return coordinator.CommandResult(1, b"", b"missing source")
            if destination not in self.remote_files:
                self.remote_files[destination] = self.remote_files.pop(source)
            return coordinator.CommandResult(0, b"", b"")
        if command == "/usr/bin/unlink":
            path = arguments[-1]
            if path not in self.remote_files:
                return coordinator.CommandResult(1, b"", b"missing")
            del self.remote_files[path]
            return coordinator.CommandResult(0, b"", b"")
        raise AssertionError(f"unexpected remote command: {arguments!r}")

    def __call__(
        self,
        argv,
        timeout: int,
    ) -> coordinator.CommandResult:
        arguments = tuple(argv)
        self.calls.append((arguments, timeout))
        self.assert_safe_argv(arguments)
        if arguments[0] == coordinator.CURL:
            resolve = arguments[arguments.index("--resolve") + 1]
            vhost = resolve.split(":", 1)[0]
            if "--request" in arguments:
                probe = "post"
            elif "Upgrade: websocket" in arguments:
                probe = "websocket"
            else:
                probe = "get"
            self.curl_calls.append((probe, vhost))
            status = b"503" if probe != "get" else b"200"
            return coordinator.CommandResult(0, status, b"")
        if arguments[0] == coordinator.SCP:
            source = Path(arguments[-2])
            destination = arguments[-1].split(":", 1)[1]
            if destination in self.remote_files:
                raise AssertionError("SCP would overwrite remote material")
            self.remote_files[destination] = (
                source.read_bytes(),
                stat.S_IMODE(source.stat().st_mode),
            )
            return coordinator.CommandResult(0, b"", b"")
        if arguments[0] == coordinator.SSH:
            remote_host = (
                f"{coordinator.WEBAPP_FI_SSH_USER}@"
                f"{coordinator.WEBAPP_FI_HOST}"
            )
            start = arguments.index(remote_host) + 1
            return self._remote_command(arguments[start:])
        if arguments[0] == coordinator.PYTHON:
            role = arguments[arguments.index("--role") + 1]
            action = arguments[arguments.index("--action") + 1]
            generation = (
                arguments[arguments.index("--generation") + 1]
                if "--generation" in arguments
                else None
            )
            return self._host_result(role, action, generation)
        raise AssertionError(f"unexpected coordinator command: {arguments!r}")

    def assert_safe_argv(self, arguments: tuple[str, ...]) -> None:
        self_test = unittest.TestCase()
        self_test.assertNotIn("/bin/sh", arguments)
        self_test.assertNotIn("/bin/bash", arguments)
        self_test.assertNotIn("-c", arguments)
        if arguments[0] in {coordinator.SSH, coordinator.SCP}:
            self_test.assertIn("BatchMode=yes", arguments)
            self_test.assertIn("IdentitiesOnly=yes", arguments)
            self_test.assertIn("StrictHostKeyChecking=yes", arguments)
            self_test.assertIn(
                f"UserKnownHostsFile={self.known_hosts}",
                arguments,
            )
            self_test.assertIn(os.fspath(self.ssh_identity), arguments)
            self_test.assertIn(str(coordinator.WEBAPP_FI_SSH_PORT), arguments)


class CoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output, produced = ProducerFixture(self.root).produce()
        self.aggregate = Path(produced["aggregate_path"])
        self.paths = {
            "aggregate_path": self.aggregate,
            "bot_fi_manifest": (
                self.output / "bot_fi" / "nginx-generations-manifest.json"
            ),
            "bot_fi_archive": (
                self.output / "bot_fi" / "nginx-generations.tar"
            ),
            "webapp_fi_manifest": (
                self.output
                / "webapp_fi"
                / "nginx-generations-manifest.json"
            ),
            "webapp_fi_archive": (
                self.output / "webapp_fi" / "nginx-generations.tar"
            ),
        }
        self.known_hosts = self.root / "known_hosts"
        _write_private(self.known_hosts, b"pinned-host-key\n")
        self.ssh_identity = self.root / "id_ed25519"
        _write_private(self.ssh_identity, b"pinned-private-identity\n")
        self.paths["ssh_identity"] = self.ssh_identity
        self.controller_secret = self.root / "controller-secret"
        self.controller_secret.mkdir(mode=0o700)
        (self.controller_secret / OPERATION_ID).mkdir(mode=0o700)
        self.local_etc = self.root / "local-etc"
        self.local_etc.mkdir(mode=0o700)
        self.host_parent = self.local_etc / "trading-bot-production-shadow"
        self.host_base = self.host_parent / "nginx-generations"
        self.patchers = [
            mock.patch.object(
                coordinator,
                "CONTROLLER_SECRET_PREFIX",
                self.controller_secret,
            ),
            mock.patch.object(
                coordinator,
                "HOST_CONTROL_PARENT",
                self.host_parent,
            ),
            mock.patch.object(
                coordinator,
                "HOST_OPERATION_BASE",
                self.host_base,
            ),
            mock.patch.object(
                coordinator,
                "_validate_release_worker",
                return_value=WORKER_PAYLOAD,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        role_manifests = {
            role: json.loads(
                (
                    self.output
                    / role
                    / "nginx-generations-manifest.json"
                ).read_text()
            )
            for role in coordinator.ROLE_ORDER
        }
        self.runner = TwoHostRunner(
            role_manifests=role_manifests,
            known_hosts=self.known_hosts,
            ssh_identity=self.ssh_identity,
        )

    def execute(
        self,
        action: str,
        target_state: str | None = None,
        *,
        runner=None,
    ) -> dict:
        confirm = coordinator.confirmation_phrase(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            action=action,
            target_state=target_state,
        )
        return coordinator.execute_coordinator(
            **self.paths,
            known_hosts=self.known_hosts,
            action=action,
            target_state=target_state,
            apply=True,
            confirm=confirm,
            runner=self.runner if runner is None else runner,
        )

    def install(self) -> dict:
        return self.execute("install")

    def test_plan_and_cli_are_exact_and_inert(self) -> None:
        def forbidden_runner(argv, timeout):
            raise AssertionError((argv, timeout))

        result = coordinator.execute_coordinator(
            **self.paths,
            known_hosts=self.known_hosts,
            action="install",
            runner=forbidden_runner,
        )
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["runner_invoked"])
        self.assertFalse(result["network_contacted"])
        self.assertFalse(result["controller_mutated"])
        self.assertFalse(result["active_configuration_mutated"])
        self.assertEqual(
            result["worker_path"],
            (
                f"/srv/trading-bot-three-site-production-shadow/"
                f"{OPERATION_ID}/releases/{RELEASE_SHA}/scripts/"
                "production_shadow_nginx_generation.py"
            ),
        )
        self.assertEqual(
            result["host_control_bootstrap"]["required_mode"],
            "0700",
        )
        self.assertFalse(self.host_parent.exists())
        self.assertFalse(
            (
                self.controller_secret
                / OPERATION_ID
                / "nginx-coordinator"
            ).exists()
        )

        with self.assertRaises(coordinator.NginxCoordinatorError):
            coordinator.execute_coordinator(
                **self.paths,
                known_hosts=self.known_hosts,
                action="install",
                apply=True,
                confirm="wrong",
                runner=forbidden_runner,
            )

        captured = _CapturedStdout()
        argv = [
            "--aggregate",
            os.fspath(self.paths["aggregate_path"]),
            "--bot-fi-manifest",
            os.fspath(self.paths["bot_fi_manifest"]),
            "--bot-fi-archive",
            os.fspath(self.paths["bot_fi_archive"]),
            "--webapp-fi-manifest",
            os.fspath(self.paths["webapp_fi_manifest"]),
            "--webapp-fi-archive",
            os.fspath(self.paths["webapp_fi_archive"]),
            "--known-hosts",
            os.fspath(self.known_hosts),
            "--ssh-identity",
            os.fspath(self.ssh_identity),
            "--action",
            "install",
        ]
        with mock.patch.object(coordinator.sys, "stdout", captured):
            self.assertEqual(coordinator.main(argv), 0)
        output = captured.buffer.getvalue()
        document = json.loads(output)
        self.assertEqual(
            output,
            canonical_json_bytes(document) + b"\n",
        )
        self.assertEqual(document["status"], "planned")

    def test_install_is_create_only_idempotent_and_evidence_is_bounded(self) -> None:
        installed = self.install()
        self.assertEqual(installed["status"], "installed")
        self.assertEqual(installed["state"], "legacy-normal")
        self.assertFalse(installed["active_configuration_mutated"])
        self.assertEqual(
            installed["global_generation_sha256"],
            json.loads(self.aggregate.read_text())["generation_sha256"][
                "legacy-normal"
            ],
        )
        for path in (self.host_parent, self.host_base):
            self.assertTrue(path.is_dir())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        for path in (
            os.fspath(coordinator.HOST_CONTROL_PARENT),
            os.fspath(coordinator.HOST_OPERATION_BASE),
        ):
            self.assertEqual(self.runner.remote_directories[path], 0o700)
        incoming_files = {
            path: value
            for path, value in self.runner.remote_files.items()
            if "/incoming/" in path
        }
        self.assertEqual(len(incoming_files), 2)
        self.assertFalse(
            any("coordinator-upload" in path for path in incoming_files)
        )
        self.assertTrue(
            all(mode == 0o600 for _, mode in incoming_files.values())
        )

        coordinator_root = (
            self.controller_secret
            / OPERATION_ID
            / "nginx-coordinator"
        )
        journal_path = coordinator_root / "journal.json"
        self.assertEqual(stat.S_IMODE(journal_path.stat().st_mode), 0o600)
        evidence = sorted((coordinator_root / "evidence").glob("*.json"))
        self.assertTrue(evidence)
        for path in evidence:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            row = json.loads(path.read_text())
            self.assertNotIn("stdout", row)
            self.assertNotIn("stderr", row)
            self.assertIn("stdout_sha256", row)
            self.assertIn("stderr_sha256", row)

        previous_scp_count = sum(
            call[0][0] == coordinator.SCP for call in self.runner.calls
        )
        repeated = self.install()
        self.assertEqual(repeated["status"], "already-installed")
        self.assertEqual(
            sum(call[0][0] == coordinator.SCP for call in self.runner.calls),
            previous_scp_count,
        )
        self.assertEqual(
            repeated["remote_material"],
            {
                name: "reused"
                for name in repeated["remote_material"]
            },
        )

    def test_frozen_second_host_failure_restores_first_and_verifies_both(self) -> None:
        self.install()
        self.runner.fail_once("webapp_fi", "activate", "legacy-frozen")
        result = self.execute("activate", "legacy-frozen")
        self.assertEqual(result["status"], "compensated-failed")
        self.assertEqual(result["state"], "legacy-normal")
        self.assertFalse(result["active_configuration_mutated"])
        self.assertEqual(
            self.runner.states,
            {"bot_fi": "legacy-normal", "webapp_fi": "legacy-normal"},
        )
        mutations = [
            call
            for call in self.runner.host_calls
            if call[1] in {"activate", "restore"}
        ]
        self.assertEqual(
            mutations,
            [
                ("bot_fi", "activate", "legacy-frozen"),
                ("webapp_fi", "activate", "legacy-frozen"),
                ("bot_fi", "restore", None),
            ],
        )
        self.assertEqual(
            {
                row["state"] for row in result["readbacks"].values()
            },
            {"legacy-normal"},
        )

    def test_freeze_receipt_public_validator_and_legal_restore(self) -> None:
        self.install()
        tested = self.execute("test", "legacy-frozen")
        self.assertEqual(tested["status"], "tested")
        self.assertEqual(tested["state"], "legacy-normal")
        self.assertEqual(
            set(tested["readbacks"]),
            set(coordinator.ROLE_ORDER),
        )
        self.assertEqual(
            tested["global_generation_sha256"],
            json.loads(self.aggregate.read_text())["generation_sha256"][
                "legacy-normal"
            ],
        )

        frozen = self.execute("activate", "legacy-frozen")
        receipt_path = Path(frozen["state_receipt_path"])
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        receipt, digest = coordinator.load_state_receipt(
            receipt_path,
            "legacy-frozen",
            OPERATION_ID,
            RELEASE_SHA,
            RELEASE_TREE_SHA,
            hashlib.sha256(self.aggregate.read_bytes()).hexdigest(),
        )
        self.assertEqual(digest, frozen["state_receipt_sha256"])
        self.assertEqual(receipt["state"], "legacy-frozen")
        self.assertEqual(
            {row["state"] for row in receipt["readbacks"].values()},
            {"legacy-frozen"},
        )
        for probes in receipt["external_readback"]["vhosts"].values():
            self.assertTrue(200 <= probes["get"] <= 399)
            self.assertEqual(probes["post"], 503)
            self.assertEqual(probes["websocket"], 503)

        tampered = self.root / "tampered-state-receipt.json"
        tampered_document = json.loads(receipt_path.read_text())
        tampered_document["external_readback"]["vhosts"][
            "coin.gold-trade.ir"
        ]["post"] = 200
        _write_private(
            tampered,
            canonical_json_bytes(tampered_document),
        )
        with self.assertRaises(coordinator.NginxCoordinatorError):
            coordinator.load_state_receipt(
                tampered,
                "legacy-frozen",
                OPERATION_ID,
                RELEASE_SHA,
                RELEASE_TREE_SHA,
                hashlib.sha256(self.aggregate.read_bytes()).hexdigest(),
            )

        restored = self.execute("restore")
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(restored["state"], "legacy-normal")
        self.assertEqual(set(self.runner.states.values()), {"legacy-normal"})

    def test_readonly_partial_stays_blocked_and_resumes_same_target(self) -> None:
        self.install()
        self.execute("activate", "legacy-frozen")
        self.runner.fail_once("webapp_fi", "activate", "shadow-readonly")
        before = len(self.runner.host_calls)
        partial = self.execute("activate", "shadow-readonly")
        transition_calls = self.runner.host_calls[before:]
        self.assertEqual(partial["status"], "partial-resumable")
        self.assertEqual(partial["policy"], "keep-write-blocked")
        self.assertTrue(partial["active_configuration_mutated"])
        self.assertFalse(partial["restore_performed"])
        self.assertNotIn(
            "restore",
            {action for _, action, _ in transition_calls},
        )
        self.assertEqual(
            set(self.runner.states.values()),
            {"legacy-frozen", "shadow-readonly"},
        )
        self.assertTrue(
            partial["external_readback"]["blocked_probes_performed"]
        )
        self.assertEqual(
            {
                probe
                for probes in partial["external_readback"]["vhosts"].values()
                for probe in probes
            },
            {"get", "post", "websocket"},
        )

        resumed = self.execute("activate", "shadow-readonly")
        self.assertEqual(resumed["status"], "activated")
        self.assertEqual(resumed["state"], "shadow-readonly")
        self.assertEqual(set(self.runner.states.values()), {"shadow-readonly"})

    def test_writable_partial_is_forward_only_and_never_write_probes(self) -> None:
        self.install()
        self.execute("activate", "legacy-frozen")
        self.execute("activate", "shadow-readonly")
        self.runner.fail_once("webapp_fi", "activate", "shadow-writable")
        partial = self.execute("activate", "shadow-writable")
        self.assertEqual(partial["status"], "forward-only-retry")
        self.assertEqual(partial["policy"], "forward-only-same-target")
        self.assertTrue(partial["active_configuration_mutated"])
        self.assertEqual(
            set(self.runner.states.values()),
            {"shadow-readonly", "shadow-writable"},
        )
        self.assertEqual(
            set(
                partial["external_readback"]["vhosts"][
                    "coin.362514.ir"
                ]
            ),
            {"get"},
        )
        self.assertEqual(
            set(
                partial["external_readback"]["vhosts"][
                    "mini-app.362514.ir"
                ]
            ),
            {"get"},
        )
        self.assertEqual(
            set(
                partial["external_readback"]["vhosts"][
                    "coin.gold-trade.ir"
                ]
            ),
            {"get", "post", "websocket"},
        )

        restore_count = sum(
            action == "restore"
            for _, action, _ in self.runner.host_calls
        )
        with self.assertRaises(coordinator.NginxCoordinatorError):
            self.execute("restore")
        self.assertEqual(
            sum(
                action == "restore"
                for _, action, _ in self.runner.host_calls
            ),
            restore_count,
        )
        resumed = self.execute("activate", "shadow-writable")
        self.assertEqual(resumed["status"], "activated")
        self.assertEqual(set(self.runner.states.values()), {"shadow-writable"})

    def test_restore_partial_is_resumable_without_probing_normal_host_writes(
        self,
    ) -> None:
        self.install()
        self.execute("activate", "legacy-frozen")
        self.execute("activate", "shadow-readonly")
        self.runner.fail_once("webapp_fi", "restore", None)
        partial = self.execute("restore")
        self.assertEqual(partial["status"], "restore-partial-resumable")
        self.assertTrue(partial["active_configuration_mutated"])
        self.assertEqual(
            self.runner.states,
            {
                "bot_fi": "legacy-normal",
                "webapp_fi": "shadow-readonly",
            },
        )
        self.assertEqual(
            set(
                partial["external_readback"]["vhosts"][
                    "coin.362514.ir"
                ]
            ),
            {"get"},
        )
        self.assertEqual(
            set(
                partial["external_readback"]["vhosts"][
                    "coin.gold-trade.ir"
                ]
            ),
            {"get", "post", "websocket"},
        )
        resumed = self.execute("restore")
        self.assertEqual(resumed["status"], "restored")
        self.assertEqual(set(self.runner.states.values()), {"legacy-normal"})

    def test_input_remote_worker_and_evidence_tampering_fail_closed(self) -> None:
        aggregate_document = json.loads(self.aggregate.read_text())
        aggregate_document[
            "nginx_freeze_generation_sha256"
        ] = "9" * 64
        bad_aggregate = self.root / "bad-aggregate.json"
        _write_private(
            bad_aggregate,
            canonical_json_bytes(aggregate_document),
        )
        with self.assertRaises(coordinator.NginxCoordinatorError):
            coordinator.execute_coordinator(
                **{**self.paths, "aggregate_path": bad_aggregate},
                known_hosts=self.known_hosts,
                action="install",
            )

        noncanonical = (
            self.aggregate.parent
            / "unused"
            / ".."
            / self.aggregate.name
        )
        with self.assertRaises(coordinator.NginxCoordinatorError):
            coordinator.execute_coordinator(
                **{**self.paths, "aggregate_path": noncanonical},
                known_hosts=self.known_hosts,
                action="install",
            )

        bad_archive = self.root / "tampered-nginx-generations.tar"
        archive_payload = bytearray(
            self.paths["webapp_fi_archive"].read_bytes()
        )
        archive_payload[0] ^= 1
        _write_private(bad_archive, bytes(archive_payload))
        with self.assertRaises(coordinator.NginxCoordinatorError):
            coordinator.execute_coordinator(
                **{
                    **self.paths,
                    "webapp_fi_archive": bad_archive,
                },
                known_hosts=self.known_hosts,
                action="install",
            )

        symlink = self.root / "known-hosts-link"
        symlink.symlink_to(self.known_hosts)
        with self.assertRaises(coordinator.NginxCoordinatorError):
            coordinator.execute_coordinator(
                **self.paths,
                known_hosts=symlink,
                action="install",
            )

        worker_path = next(
            path
            for path in self.runner.remote_files
            if path.endswith(
                "/scripts/production_shadow_nginx_generation.py"
            )
        )
        self.runner.remote_files[worker_path] = (b"tampered\n", 0o755)
        with self.assertRaises(coordinator.NginxCoordinatorError):
            self.install()
        self.assertFalse(self.runner.host_calls)
        self.runner.remote_files[worker_path] = (WORKER_PAYLOAD, 0o755)

        self.install()
        evidence_path = next(
            (
                self.controller_secret
                / OPERATION_ID
                / "nginx-coordinator"
                / "evidence"
            ).glob("*.json")
        )
        evidence = json.loads(evidence_path.read_text())
        evidence["stdout_bytes"] += 1
        _write_private(evidence_path, canonical_json_bytes(evidence))
        call_count = len(self.runner.calls)
        with self.assertRaises(coordinator.NginxCoordinatorError):
            self.execute("readback")
        self.assertEqual(len(self.runner.calls), call_count)

    def test_uniform_unjournaled_state_drift_is_not_adopted(self) -> None:
        self.install()
        self.runner.states = {
            role: "shadow-readonly" for role in coordinator.ROLE_ORDER
        }
        mutation_count = sum(
            action in {"activate", "restore"}
            for _, action, _ in self.runner.host_calls
        )
        with self.assertRaises(coordinator.NginxCoordinatorError):
            self.execute("activate", "shadow-writable")
        with self.assertRaises(coordinator.NginxCoordinatorError):
            self.execute("readback")
        self.assertEqual(
            sum(
                action in {"activate", "restore"}
                for _, action, _ in self.runner.host_calls
            ),
            mutation_count,
        )


if __name__ == "__main__":
    unittest.main()
