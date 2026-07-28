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

    def inputs(self) -> coordinator.CoordinatorInputs:
        return coordinator.load_inputs(
            **self.paths,
            known_hosts=self.known_hosts,
        )

    def live_checkpoint_transcript(
        self,
        *,
        role: str,
        lease: coordinator.CoordinatorLiveLease,
        normalized_kinds: tuple[str, ...] = (),
    ) -> list[dict]:
        checkpoints = [
            checkpoint
            for kind in normalized_kinds
            for checkpoint in (
                f"before-stop:{kind}",
                f"after-stop:{kind}",
            )
        ]
        checkpoints.extend(
            [
                checkpoint
                for kind in coordinator.LEGACY_WRITER_KINDS[role]
                for checkpoint in (
                    f"before-start:{kind}",
                    f"after-start:{kind}",
                )
            ]
        )
        checkpoints.extend(
            [
                "readiness-http:1",
                "readiness-stability:1",
                "readiness-stability:2",
                "readiness-stability:3",
                "before-result",
            ]
        )
        previous = "0" * 64
        transcript = []
        for sequence, checkpoint in enumerate(checkpoints, 1):
            challenge = {
                "schema": (
                    coordinator.LEGACY_WRITER_LIVE_CHALLENGE_SCHEMA
                ),
                "status": "controller-response-required",
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "role": role,
                "live_lease_claim_sha256": lease.claim_sha256,
                "live_lease_claim_epoch": lease.claim["claim_epoch"],
                "sequence": sequence,
                "checkpoint": checkpoint,
                "challenge_nonce": hashlib.sha256(
                    f"challenge:{role}:{sequence}".encode("ascii")
                ).hexdigest(),
                "previous_transcript_sha256": previous,
            }
            response = {
                "schema": (
                    coordinator.LEGACY_WRITER_LIVE_RESPONSE_SCHEMA
                ),
                "status": "controller-flock-verified",
                **{
                    key: challenge[key]
                    for key in challenge
                    if key not in {"schema", "status"}
                },
                "challenge_sha256": hashlib.sha256(
                    canonical_json_bytes(challenge)
                ).hexdigest(),
                "controller_flock_verified": True,
                "response_nonce": hashlib.sha256(
                    f"response:{role}:{sequence}".encode("ascii")
                ).hexdigest(),
            }
            entry = {
                "challenge": challenge,
                "response": response,
                "entry_sha256": "0" * 64,
            }
            entry["entry_sha256"] = (
                coordinator._legacy_writer_transcript_entry_sha256(
                    entry
                )
            )
            transcript.append(entry)
            previous = entry["entry_sha256"]
        return transcript

    def readiness_receipt(
        self,
        lease: coordinator.CoordinatorLiveLease,
        name: str = "writers-ready",
        normalized_kinds: dict[str, tuple[str, ...]] | None = None,
    ) -> tuple[Path, str]:
        path = self.root / f"{name}.json"
        inputs = self.inputs()
        claim = lease.claim
        roles = {}
        for role in coordinator.ROLE_ORDER:
            expected_count = coordinator.LEGACY_WRITER_COUNTS[role]
            transcript = self.live_checkpoint_transcript(
                role=role,
                lease=lease,
                normalized_kinds=(
                    normalized_kinds.get(role, ())
                    if normalized_kinds is not None
                    else ()
                ),
            )
            restored_result = {
                "schema": coordinator.LEGACY_WRITER_RESULT_SCHEMA,
                "status": "restored-ready",
                "action": "restore",
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "legacy_release_sha": "b" * 40,
                "role": role,
                "binding_sha256": (
                    ("5" if role == "bot_fi" else "6") * 64
                ),
                "nginx_manifest_sha256": (
                    inputs.roles[role].manifest_sha256
                ),
                "nginx_aggregate_sha256": inputs.aggregate_sha256,
                "coordinated_state_receipt_sha256": claim[
                    "legacy_frozen_receipt_sha256"
                ],
                "live_lease_claim_sha256": lease.claim_sha256,
                "live_lease_claim_epoch": claim["claim_epoch"],
                "role_freeze_generation_sha256": claim[
                    "receipt_role_generation_sha256"
                ][role],
                "freeze_generation_sha256": claim[
                    "receipt_global_generation_sha256"
                ],
                "journal_sha256": (
                    ("7" if role == "bot_fi" else "8") * 64
                ),
                "freeze_evidence_sha256": None,
                "freeze_evidence_revoked": True,
                "all_exact_writer_containers_ready": True,
                "expected_writer_container_count": expected_count,
                "legacy_writer_process_count": None,
                "writer_database_client_count": None,
                "file_mutator_process_count": None,
                "database_container_running": True,
                "redis_container_running": True,
                "application_http_status": 200,
                "legacy_ready_for_nginx_restore": True,
                "ready_writer_container_count": expected_count,
                "readiness_sha256": (
                    ("3" if role == "bot_fi" else "4") * 64
                ),
                "stable_sample_count": 3,
                "interactive_lease_checkpoint_count": len(transcript),
                "interactive_lease_transcript": transcript,
                "interactive_lease_transcript_sha256": transcript[-1][
                    "entry_sha256"
                ],
                "interactive_lease_authority_handoff_complete": True,
                "production_mutated": True,
            }
            roles[role] = {
                "restored_ready_result": restored_result,
                "restored_ready_result_sha256": hashlib.sha256(
                    canonical_json_bytes(restored_result)
                ).hexdigest(),
                **{
                    field: restored_result[field]
                    for field in (
                        "status",
                        "legacy_ready_for_nginx_restore",
                        "freeze_evidence_sha256",
                        "freeze_evidence_revoked",
                        "all_exact_writer_containers_ready",
                        "expected_writer_container_count",
                        "ready_writer_container_count",
                        "readiness_sha256",
                        "stable_sample_count",
                        "application_http_status",
                        "database_container_running",
                        "redis_container_running",
                        "production_mutated",
                    )
                },
            }
        _write_private(
            path,
            canonical_json_bytes(
                {
                    "schema": (
                        coordinator.LEGACY_WRITER_READINESS_SET_SCHEMA
                    ),
                    "status": "legacy-writers-ready",
                    "operation_id": OPERATION_ID,
                    "release_sha": RELEASE_SHA,
                    "release_tree_sha": RELEASE_TREE_SHA,
                    "aggregate_sha256": hashlib.sha256(
                        self.aggregate.read_bytes()
                    ).hexdigest(),
                    "live_lease_claim_sha256": lease.claim_sha256,
                    "live_lease_claim_nonce": claim["nonce"],
                    "live_lease_claim_epoch": claim["claim_epoch"],
                    "legacy_frozen_receipt_sha256": claim[
                        "legacy_frozen_receipt_sha256"
                    ],
                    "roles": roles,
                    "roles_sha256": hashlib.sha256(
                        canonical_json_bytes(roles)
                    ).hexdigest(),
                }
            ),
        )
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

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

        rollback_plan = coordinator.execute_coordinator(
            **self.paths,
            known_hosts=self.known_hosts,
            action="rollback-freeze",
            target_state="legacy-frozen",
            runner=forbidden_runner,
        )
        self.assertEqual(rollback_plan["status"], "planned")
        with self.assertRaises(coordinator.NginxCoordinatorError):
            coordinator.execute_coordinator(
                **self.paths,
                known_hosts=self.known_hosts,
                action="rollback-freeze",
                target_state="shadow-readonly",
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

        inputs = self.inputs()
        with coordinator.hold_coordinator_live_lease(
            inputs=inputs,
            owner_action="restore-legacy-writers",
            legacy_frozen_receipt_path=receipt_path,
            legacy_frozen_receipt_sha256=digest,
        ) as lease:
            verified = lease.verify()
            self.assertEqual(verified["phase"], "legacy-frozen")
            self.assertTrue(
                verified["controller_lock_authority_observed"]
            )
            with self.assertRaisesRegex(
                coordinator.NginxCoordinatorError,
                "outside its owner action",
            ):
                lease.consume(
                    outcome="handoff-shadow-readonly",
                    outcome_sha256="8" * 64,
                )
            with self.assertRaisesRegex(
                coordinator.NginxCoordinatorError,
                "lock is busy",
            ):
                self.execute("readback")
            with self.assertRaisesRegex(
                coordinator.NginxCoordinatorError,
                "lock is busy",
            ):
                with coordinator._CoordinatorLock(  # noqa: SLF001
                    inputs.coordinator_root
                ):
                    pass
            ready_path, ready_sha256 = self.readiness_receipt(lease)
            restored = lease.restore_legacy_normal(
                readiness_receipt_path=ready_path,
                readiness_receipt_sha256=ready_sha256,
                runner=self.runner,
            )
            self.assertEqual(restored["status"], "restored")
            self.assertEqual(restored["state"], "legacy-normal")
            consumption_path, consumption_sha256 = lease.consume(
                outcome="legacy-restored",
                outcome_sha256="8" * 64,
            )
            self.assertEqual(
                stat.S_IMODE(consumption_path.stat().st_mode),
                0o600,
            )
            self.assertRegex(consumption_sha256, r"^[0-9a-f]{64}$")
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

    def test_readonly_rollback_freezes_first_then_ready_restore_is_resumable(
        self,
    ) -> None:
        self.install()
        self.execute("activate", "legacy-frozen")
        readonly = self.execute("activate", "shadow-readonly")
        readonly_path = Path(readonly["state_receipt_path"])
        readonly_sha256 = readonly["state_receipt_sha256"]
        inputs = self.inputs()

        self.runner.fail_once(
            "webapp_fi",
            "rollback-freeze",
            "legacy-frozen",
        )
        with self.assertRaises(
            coordinator.NginxCoordinatorRollbackPending
        ) as blocked:
            with coordinator.hold_coordinator_rollback_live_lease(
                inputs=inputs,
                shadow_readonly_receipt_path=readonly_path,
                shadow_readonly_receipt_sha256=readonly_sha256,
                runner=self.runner,
            ):
                pass
        partial = blocked.exception.result
        self.assertEqual(
            partial["status"],
            "rollback-freeze-partial-resumable",
        )
        self.assertEqual(
            partial["policy"],
            "rollback-to-frozen-write-blocked",
        )
        self.assertTrue(partial["active_configuration_mutated"])
        self.assertEqual(
            self.runner.states,
            {
                "bot_fi": "legacy-frozen",
                "webapp_fi": "shadow-readonly",
            },
        )
        for probes in partial["external_readback"]["vhosts"].values():
            self.assertEqual(
                set(probes),
                {"get", "post", "websocket"},
            )
            self.assertEqual(probes["post"], 503)
            self.assertEqual(probes["websocket"], 503)
        before_retry = len(self.runner.calls)
        with self.assertRaisesRegex(
            coordinator.NginxCoordinatorError,
            "locked rollback live lease API",
        ):
            self.execute("readback")
        self.assertEqual(len(self.runner.calls), before_retry)

        with coordinator.hold_coordinator_rollback_live_lease(
            inputs=inputs,
            shadow_readonly_receipt_path=readonly_path,
            shadow_readonly_receipt_sha256=readonly_sha256,
            runner=self.runner,
        ) as lease:
            self.assertEqual(
                lease.transition_result["status"],
                "rollback-frozen",
            )
            self.assertEqual(
                set(self.runner.states.values()),
                {"legacy-frozen"},
            )
            before_restore = len(self.runner.host_calls)
            with self.assertRaises(coordinator.NginxCoordinatorError):
                lease.consume(
                    outcome="legacy-restored",
                    outcome_sha256="8" * 64,
                )
            self.assertEqual(
                self.runner.host_calls[before_restore:],
                [],
            )
            with self.assertRaises(coordinator.NginxCoordinatorError):
                lease.restore_legacy_normal(
                    readiness_receipt_path=(
                        self.root / "missing-readiness.json"
                    ),
                    readiness_receipt_sha256="e" * 64,
                    runner=self.runner,
                )
            self.assertEqual(
                self.runner.host_calls[before_restore:],
                [],
            )
            opaque_path = self.root / "opaque-readiness.json"
            _write_private(
                opaque_path,
                canonical_json_bytes(
                    {"schema": "opaque-v1", "status": "ready"}
                ),
            )
            with self.assertRaisesRegex(
                coordinator.NginxCoordinatorError,
                "readiness set identity",
            ):
                lease.restore_legacy_normal(
                    readiness_receipt_path=opaque_path,
                    readiness_receipt_sha256=hashlib.sha256(
                        opaque_path.read_bytes()
                    ).hexdigest(),
                    runner=self.runner,
                )
            self.assertEqual(
                self.runner.host_calls[before_restore:],
                [],
            )
            invalid_stop_path, invalid_stop_sha256 = self.readiness_receipt(
                lease,
                "invalid-stop-order",
                normalized_kinds={
                    "bot_fi": ("bot", "application"),
                },
            )
            with self.assertRaisesRegex(
                coordinator.NginxCoordinatorError,
                "checkpoint semantics differ",
            ):
                lease.restore_legacy_normal(
                    readiness_receipt_path=invalid_stop_path,
                    readiness_receipt_sha256=invalid_stop_sha256,
                    runner=self.runner,
                )
            self.assertEqual(
                self.runner.host_calls[before_restore:],
                [],
            )
            ready_path, ready_sha256 = self.readiness_receipt(
                lease,
                normalized_kinds={
                    "bot_fi": ("application", "sync_worker"),
                    "webapp_fi": ("application", "sync_worker"),
                },
            )
            tampered_path = self.root / "tampered-readiness-set.json"
            tampered_set = json.loads(ready_path.read_text())
            tampered_row = tampered_set["roles"]["webapp_fi"]
            tampered_row["ready_writer_container_count"] = 1
            tampered_row["restored_ready_result"][
                "ready_writer_container_count"
            ] = 1
            tampered_row["restored_ready_result_sha256"] = (
                hashlib.sha256(
                    canonical_json_bytes(
                        tampered_row["restored_ready_result"]
                    )
                ).hexdigest()
            )
            tampered_set["roles_sha256"] = hashlib.sha256(
                canonical_json_bytes(tampered_set["roles"])
            ).hexdigest()
            _write_private(
                tampered_path,
                canonical_json_bytes(tampered_set),
            )
            with self.assertRaisesRegex(
                coordinator.NginxCoordinatorError,
                "restored-ready result semantics",
            ):
                lease.restore_legacy_normal(
                    readiness_receipt_path=tampered_path,
                    readiness_receipt_sha256=hashlib.sha256(
                        tampered_path.read_bytes()
                    ).hexdigest(),
                    runner=self.runner,
                )
            self.assertEqual(
                self.runner.host_calls[before_restore:],
                [],
            )
            transcript_tampered_path = (
                self.root / "tampered-live-transcript.json"
            )
            transcript_tampered = json.loads(ready_path.read_text())
            transcript_row = transcript_tampered["roles"]["bot_fi"]
            transcript_row["restored_ready_result"][
                "interactive_lease_transcript"
            ][0]["challenge"]["challenge_nonce"] = "e" * 64
            transcript_row["restored_ready_result_sha256"] = (
                hashlib.sha256(
                    canonical_json_bytes(
                        transcript_row["restored_ready_result"]
                    )
                ).hexdigest()
            )
            transcript_tampered["roles_sha256"] = hashlib.sha256(
                canonical_json_bytes(transcript_tampered["roles"])
            ).hexdigest()
            _write_private(
                transcript_tampered_path,
                canonical_json_bytes(transcript_tampered),
            )
            with self.assertRaisesRegex(
                coordinator.NginxCoordinatorError,
                "transcript binding differs",
            ):
                lease.restore_legacy_normal(
                    readiness_receipt_path=transcript_tampered_path,
                    readiness_receipt_sha256=hashlib.sha256(
                        transcript_tampered_path.read_bytes()
                    ).hexdigest(),
                    runner=self.runner,
                )
            self.assertEqual(
                self.runner.host_calls[before_restore:],
                [],
            )
            self.runner.fail_once("webapp_fi", "restore", None)
            restore_partial = lease.restore_legacy_normal(
                readiness_receipt_path=ready_path,
                readiness_receipt_sha256=ready_sha256,
                runner=self.runner,
            )
            self.assertEqual(
                restore_partial["status"],
                "restore-partial-resumable",
            )
            self.assertEqual(
                self.runner.states,
                {
                    "bot_fi": "legacy-normal",
                    "webapp_fi": "legacy-frozen",
                },
            )
            resumed = lease.restore_legacy_normal(
                readiness_receipt_path=ready_path,
                readiness_receipt_sha256=ready_sha256,
                runner=self.runner,
            )
            self.assertEqual(resumed["status"], "restored")
            lease.consume(
                outcome="legacy-restored",
                outcome_sha256="9" * 64,
            )
        self.assertEqual(set(self.runner.states.values()), {"legacy-normal"})

    def test_live_lease_crash_blocks_and_exact_resume_consumes_same_claim(
        self,
    ) -> None:
        self.install()
        frozen = self.execute("activate", "legacy-frozen")
        receipt_path = Path(frozen["state_receipt_path"])
        receipt_sha256 = frozen["state_receipt_sha256"]
        inputs = self.inputs()
        lease_identity: dict[str, object] = {}
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            with coordinator.hold_coordinator_live_lease(
                inputs=inputs,
                owner_action="capture-frozen-final-snapshots",
                legacy_frozen_receipt_path=receipt_path,
                legacy_frozen_receipt_sha256=receipt_sha256,
            ) as lease:
                lease_identity = {
                    "path": lease.claim_path,
                    "sha256": lease.claim_sha256,
                    "nonce": lease.claim["nonce"],
                    "payload": lease.claim_payload,
                }
                observed = coordinator.load_unconsumed_live_lease_claim(
                    inputs,
                    claim_path=lease.claim_path,
                    expected_claim_sha256=lease.claim_sha256,
                    expected_nonce=lease.claim["nonce"],
                )
                self.assertEqual(observed["phase"], "legacy-frozen")
                self.assertFalse(
                    observed["controller_lock_authority_observed"]
                )
                with self.assertRaisesRegex(
                    coordinator.NginxCoordinatorError,
                    "cannot restore legacy writers",
                ):
                    lease.restore_legacy_normal(
                        readiness_receipt_path=Path("/not-authorized"),
                        readiness_receipt_sha256="7" * 64,
                        runner=self.runner,
                    )
                with self.assertRaisesRegex(
                    coordinator.NginxCoordinatorError,
                    "outside its owner action",
                ):
                    lease.consume(
                        outcome="legacy-restored",
                        outcome_sha256="7" * 64,
                    )
                before = len(self.runner.calls)
                with self.assertRaisesRegex(
                    coordinator.NginxCoordinatorError,
                    "lock is busy",
                ):
                    self.execute("readback")
                self.assertEqual(len(self.runner.calls), before)
                raise RuntimeError("simulated crash")

        before = len(self.runner.calls)
        with self.assertRaisesRegex(
            coordinator.NginxCoordinatorError,
            "reconciliation",
        ):
            self.execute("readback")
        self.assertEqual(len(self.runner.calls), before)

        remote_copy = self.root / "remote-live-lease-copy"
        remote_copy.mkdir(mode=0o700)
        copied_claim = (
            remote_copy / f"{lease_identity['sha256']}.json"
        )
        _write_private(copied_claim, lease_identity["payload"])
        copied_receipt = remote_copy / "legacy-frozen-receipt.json"
        _write_private(copied_receipt, receipt_path.read_bytes())
        copied, copied_sha256 = (
            coordinator.load_live_lease_claim_material(
                copied_claim,
                state_receipt_path=copied_receipt,
                expected_claim_sha256=lease_identity["sha256"],
                expected_state_receipt_sha256=receipt_sha256,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                release_tree_sha=RELEASE_TREE_SHA,
                aggregate_sha256=hashlib.sha256(
                    self.aggregate.read_bytes()
                ).hexdigest(),
            )
        )
        self.assertEqual(copied["nonce"], lease_identity["nonce"])
        self.assertEqual(copied_sha256, lease_identity["sha256"])

        with self.assertRaisesRegex(
            coordinator.NginxCoordinatorError,
            "nonce differs",
        ):
            with coordinator.resume_coordinator_live_lease(
                inputs=inputs,
                expected_owner_action=(
                    "capture-frozen-final-snapshots"
                ),
                claim_path=lease_identity["path"],
                expected_claim_sha256=lease_identity["sha256"],
                expected_nonce="f" * 64,
            ):
                pass
        with self.assertRaisesRegex(
            coordinator.NginxCoordinatorError,
            "digest or nonce differs",
        ):
            with coordinator.resume_coordinator_live_lease(
                inputs=inputs,
                expected_owner_action="restore-legacy-writers",
                claim_path=lease_identity["path"],
                expected_claim_sha256=lease_identity["sha256"],
                expected_nonce=lease_identity["nonce"],
            ):
                pass

        claim_path = lease_identity["path"]
        claim_path.chmod(0o644)
        with self.assertRaises(coordinator.NginxCoordinatorError):
            with coordinator.resume_coordinator_live_lease(
                inputs=inputs,
                expected_owner_action=(
                    "capture-frozen-final-snapshots"
                ),
                claim_path=claim_path,
                expected_claim_sha256=lease_identity["sha256"],
                expected_nonce=lease_identity["nonce"],
            ):
                pass
        claim_path.chmod(0o600)

        saved = self.root / "saved-live-lease-claim.json"
        claim_path.rename(saved)
        claim_path.symlink_to(saved)
        with self.assertRaises(coordinator.NginxCoordinatorError):
            with coordinator.resume_coordinator_live_lease(
                inputs=inputs,
                expected_owner_action=(
                    "capture-frozen-final-snapshots"
                ),
                claim_path=claim_path,
                expected_claim_sha256=lease_identity["sha256"],
                expected_nonce=lease_identity["nonce"],
            ):
                pass
        claim_path.unlink()
        saved.rename(claim_path)

        original = claim_path.read_bytes()
        tampered = json.loads(original)
        tampered["controller_pid"] += 1
        _write_private(claim_path, canonical_json_bytes(tampered))
        with self.assertRaises(coordinator.NginxCoordinatorError):
            with coordinator.resume_coordinator_live_lease(
                inputs=inputs,
                expected_owner_action=(
                    "capture-frozen-final-snapshots"
                ),
                claim_path=claim_path,
                expected_claim_sha256=lease_identity["sha256"],
                expected_nonce=lease_identity["nonce"],
            ):
                pass
        _write_private(claim_path, original)

        resumed_handle = None
        with coordinator.reconcile_coordinator_live_lease(
            inputs=inputs,
            expected_owner_action="capture-frozen-final-snapshots",
            claim_path=claim_path,
            expected_claim_sha256=lease_identity["sha256"],
            expected_nonce=lease_identity["nonce"],
        ) as resumed:
            resumed_handle = resumed
            self.assertEqual(resumed.claim_payload, lease_identity["payload"])
            consumption_path = (
                inputs.coordinator_root
                / "live-leases"
                / "consumptions"
                / f"{resumed.claim_sha256}.json"
            )
            consumption_path.symlink_to(claim_path)
            with self.assertRaises(coordinator.NginxCoordinatorError):
                resumed.consume(
                    outcome="handoff-shadow-readonly",
                    outcome_sha256="a" * 64,
                )
            consumption_path.unlink()
            resumed.consume(
                outcome="handoff-shadow-readonly",
                outcome_sha256="a" * 64,
            )
        with self.assertRaisesRegex(
            coordinator.NginxCoordinatorError,
            "lock is not held",
        ):
            resumed_handle.verify()
        self.assertEqual(
            len(
                list(
                    (
                        inputs.coordinator_root
                        / "live-leases"
                        / "claims"
                    ).glob("*.json")
                )
            ),
            1,
        )
        with self.assertRaises(coordinator.NginxCoordinatorError):
            coordinator.load_unconsumed_live_lease_claim(
                inputs,
                claim_path=claim_path,
                expected_claim_sha256=lease_identity["sha256"],
                expected_nonce=lease_identity["nonce"],
            )
        self.assertEqual(self.execute("readback")["state"], "legacy-frozen")

    def test_final_shadow_restore_owner_is_least_privilege(self) -> None:
        self.install()
        frozen = self.execute("activate", "legacy-frozen")
        receipt_path = Path(frozen["state_receipt_path"])
        receipt_sha256 = frozen["state_receipt_sha256"]
        inputs = self.inputs()
        with coordinator.hold_coordinator_live_lease(
            inputs=inputs,
            owner_action="restore-shadow-frozen-final",
            legacy_frozen_receipt_path=receipt_path,
            legacy_frozen_receipt_sha256=receipt_sha256,
        ) as lease:
            self.assertEqual(lease.verify()["phase"], "legacy-frozen")
            for forbidden_outcome in (
                "handoff-shadow-readonly",
                "legacy-restored",
            ):
                with self.assertRaisesRegex(
                    coordinator.NginxCoordinatorError,
                    "outside its owner action",
                ):
                    lease.consume(
                        outcome=forbidden_outcome,
                        outcome_sha256="c" * 64,
                    )
            consumption_path, consumption_sha256 = lease.consume(
                outcome="frozen-final-shadow-restored",
                outcome_sha256="c" * 64,
            )
            audit = json.loads(consumption_path.read_text())
            self.assertEqual(audit["final_state"], "legacy-frozen")
            self.assertEqual(
                audit["final_state_receipt_sha256"],
                receipt_sha256,
            )
            self.assertIsNone(audit["readiness_audit_sha256"])
            self.assertRegex(consumption_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(self.execute("readback")["state"], "legacy-frozen")

    def test_rollback_r2_and_normal_receipt_crashes_resume_without_new_claim(
        self,
    ) -> None:
        self.install()
        self.execute("activate", "legacy-frozen")
        readonly = self.execute("activate", "shadow-readonly")
        readonly_path = Path(readonly["state_receipt_path"])
        readonly_sha256 = readonly["state_receipt_sha256"]
        inputs = self.inputs()

        original_persist = coordinator._persist_state_receipt  # noqa: SLF001
        with mock.patch.object(
            coordinator,
            "_persist_state_receipt",
            side_effect=coordinator.NginxCoordinatorError(
                "simulated R2 persistence crash"
            ),
        ):
            with self.assertRaisesRegex(
                coordinator.NginxCoordinatorError,
                "simulated R2",
            ):
                with coordinator.hold_coordinator_rollback_live_lease(
                    inputs=inputs,
                    shadow_readonly_receipt_path=readonly_path,
                    shadow_readonly_receipt_sha256=readonly_sha256,
                    runner=self.runner,
                ):
                    pass
        self.assertEqual(set(self.runner.states.values()), {"legacy-frozen"})
        self.assertEqual(
            list(
                (
                    inputs.coordinator_root
                    / "live-leases"
                    / "claims"
                ).glob("*.json")
            ),
            [],
        )

        lease_identity: dict[str, object] = {}
        with self.assertRaisesRegex(
            coordinator.NginxCoordinatorError,
            "unconsumed",
        ):
            with coordinator.hold_coordinator_rollback_live_lease(
                inputs=inputs,
                shadow_readonly_receipt_path=readonly_path,
                shadow_readonly_receipt_sha256=readonly_sha256,
                runner=self.runner,
            ) as lease:
                lease_identity = {
                    "path": lease.claim_path,
                    "sha256": lease.claim_sha256,
                    "nonce": lease.claim["nonce"],
                }
                ready_path, ready_sha256 = self.readiness_receipt(
                    lease,
                    "writers-ready-after-r2"
                )
                with mock.patch.object(
                    coordinator,
                    "_persist_state_receipt",
                    side_effect=coordinator.NginxCoordinatorError(
                        "simulated normal receipt crash"
                    ),
                ):
                    with self.assertRaisesRegex(
                        coordinator.NginxCoordinatorError,
                        "simulated normal",
                    ):
                        lease.restore_legacy_normal(
                            readiness_receipt_path=ready_path,
                            readiness_receipt_sha256=ready_sha256,
                            runner=self.runner,
                        )
                self.assertEqual(
                    set(self.runner.states.values()),
                    {"legacy-normal"},
                )
        self.assertIs(
            coordinator._persist_state_receipt,  # noqa: SLF001
            original_persist,
        )

        with coordinator.resume_coordinator_live_lease(
            inputs=inputs,
            expected_owner_action="restore-legacy-writers",
            claim_path=lease_identity["path"],
            expected_claim_sha256=lease_identity["sha256"],
            expected_nonce=lease_identity["nonce"],
        ) as resumed:
            ready_path, ready_sha256 = self.readiness_receipt(
                resumed,
                "writers-ready-after-r2"
            )
            result = resumed.restore_legacy_normal(
                readiness_receipt_path=ready_path,
                readiness_receipt_sha256=ready_sha256,
                runner=self.runner,
            )
            self.assertEqual(result["status"], "already-restored")
            resumed.consume(
                outcome="legacy-restored",
                outcome_sha256="b" * 64,
            )

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
