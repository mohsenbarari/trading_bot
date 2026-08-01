"""Focused tests for the bounded dedicated-host read-only probe agent."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.dedicated_host_preflight_receipt import parse_preflight_receipt


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_dedicated_host_readonly_preflight.py"
)
SPEC = importlib.util.spec_from_file_location("dedicated_host_readonly_preflight", MODULE_PATH)
assert SPEC and SPEC.loader
agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = agent
SPEC.loader.exec_module(agent)


CAMPAIGN_ID = "full-matrix-destructive-20260730"
OPERATION_ID = "f6d5dabe-9c52-4517-b6de-7ebbc55355c9"
RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
MANIFEST_SHA256 = "a" * 64


def request() -> dict[str, str]:
    return {
        "schema": agent.REQUEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "bot_fi",
        "manifest_sha256": MANIFEST_SHA256,
    }


def request_payload() -> bytes:
    return agent.canonical_request_bytes(request()) + b"\n"


def valid_observations() -> tuple[dict[str, object], tuple[str, int], int, bool, dict[str, object]]:
    return (
        {"state": "present", "release_sha": RELEASE_SHA, "clean": True},
        ("active", 2),
        0,
        False,
        {
            "present": True,
            "filesystem": "ext4",
            "available_bytes": 52_000_000_000,
            "options": ["nodev", "noexec", "nosuid", "rw"],
        },
    )


class DedicatedHostReadOnlyPreflightTests(unittest.TestCase):
    def test_request_is_strict_small_canonical_and_source_owned(self) -> None:
        normalized = agent.parse_request_payload(request_payload())

        self.assertEqual(normalized["role"], "bot_fi")
        self.assertEqual(normalized["operation_id"], OPERATION_ID)
        self.assertEqual(normalized["release_sha"], RELEASE_SHA)
        self.assertEqual(
            agent._source_owned_instance("bot_fi")["server_id"],
            agent.EXPECTED_HOSTS["bot_fi"]["instance_id"],
        )

        for invalid in (
            b'{"schema":"x","schema":"y"}\n',
            json.dumps(request()).encode("ascii") + b"\n",
            agent.canonical_request_bytes({**request(), "schema": "wrong-schema"}) + b"\n",
            agent.canonical_request_bytes({**request(), "path": "/tmp/never"}) + b"\n",
            agent.canonical_request_bytes({**request(), "url": "https://never.example"}) + b"\n",
            b"{" + b"x" * agent.MAX_REQUEST_BYTES,
        ):
            with self.subTest(invalid=invalid[:48]), self.assertRaises(
                agent.DedicatedHostReadOnlyPreflightError
            ):
                agent.parse_request_payload(invalid)

    def test_collect_receipt_uses_only_mocked_observations_and_validates_core_schema(self) -> None:
        release, docker, process_count, current, staging = valid_observations()
        with (
            patch.object(agent, "_require_root"),
            patch.object(agent, "_observe_release", return_value=release),
            patch.object(agent, "_observe_docker", return_value=docker),
            patch.object(agent, "_observe_matrix_process_count", return_value=process_count),
            patch.object(agent, "_observe_current_link_present", return_value=current),
            patch.object(agent, "_observe_staging_mount", return_value=staging),
            patch.object(agent, "_observed_at", return_value="2026-07-30T16:00:00Z"),
        ):
            receipt = agent.collect_preflight_receipt(request())

        self.assertEqual(parse_preflight_receipt(agent.canonical_json_bytes(receipt) + b"\n"), receipt)
        self.assertEqual(receipt["instance"], agent._source_owned_instance("bot_fi"))
        self.assertEqual(receipt["observation"]["runtime"]["container_count"], 2)

    def test_exact_release_observation_is_bound_and_cleanliness_is_read_only(self) -> None:
        responses = iter(
            (
                subprocess.CompletedProcess([], 0, stdout=(RELEASE_SHA + "\n").encode("ascii")),
                subprocess.CompletedProcess([], 1, stdout=b""),
                subprocess.CompletedProcess([], 0, stdout=b"untracked\x00"),
            )
        )
        with (
            patch.object(agent, "_release_directory", return_value=agent.FIXED_RELEASE_ROOT / RELEASE_SHA),
            patch.object(agent, "_run_fixed_command", side_effect=lambda *args, **kwargs: next(responses)),
        ):
            observed = agent._observe_release(RELEASE_SHA)

        self.assertEqual(
            observed,
            {"state": "present", "release_sha": RELEASE_SHA, "clean": False},
        )

        with patch.object(agent, "_release_directory", return_value=None):
            self.assertEqual(
                agent._observe_release(RELEASE_SHA),
                {"state": "missing", "release_sha": None, "clean": None},
            )

    def test_existing_release_with_bad_or_mismatched_git_state_is_not_missing(self) -> None:
        release_path = agent.FIXED_RELEASE_ROOT / RELEASE_SHA
        cases = (
            (
                subprocess.CompletedProcess([], 128, stdout=b""),
                "cannot be observed",
            ),
            (
                subprocess.CompletedProcess([], 0, stdout=("f" * 40 + "\n").encode("ascii")),
                "does not match",
            ),
            (
                subprocess.CompletedProcess([], 0, stdout=b"not-a-commit\n"),
                "does not match",
            ),
        )
        for head, message in cases:
            with self.subTest(head=head.stdout):
                with (
                    patch.object(agent, "_release_directory", return_value=release_path),
                    patch.object(agent, "_run_fixed_command", return_value=head),
                    self.assertRaisesRegex(agent.DedicatedHostReadOnlyPreflightError, message),
                ):
                    agent._observe_release(RELEASE_SHA)

    def test_release_layout_requires_root_controlled_non_symlink_paths_and_keeps_absence_missing(self) -> None:
        release_path = agent.FIXED_RELEASE_ROOT / RELEASE_SHA

        def safe_directory(path: object) -> SimpleNamespace:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

        def absent_root(path: object) -> SimpleNamespace:
            if Path(path) == agent.FIXED_RELEASE_ROOT:
                raise FileNotFoundError
            return safe_directory(path)

        with patch.object(agent.os, "lstat", side_effect=absent_root):
            self.assertIsNone(agent._release_directory(RELEASE_SHA))

        def absent_release(path: object) -> SimpleNamespace:
            if Path(path) == release_path:
                raise FileNotFoundError
            return safe_directory(path)

        with patch.object(agent.os, "lstat", side_effect=absent_release):
            self.assertIsNone(agent._release_directory(RELEASE_SHA))

        def symlinked_ancestor(path: object) -> SimpleNamespace:
            if Path(path) == agent.FIXED_RELEASE_ROOT.parent:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
            return safe_directory(path)

        with (
            patch.object(agent.os, "lstat", side_effect=symlinked_ancestor),
            self.assertRaisesRegex(agent.DedicatedHostReadOnlyPreflightError, "root-controlled"),
        ):
            agent._release_directory(RELEASE_SHA)

        def non_root_release(path: object) -> SimpleNamespace:
            if Path(path) == release_path:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1000)
            return safe_directory(path)

        with (
            patch.object(agent.os, "lstat", side_effect=non_root_release),
            self.assertRaisesRegex(agent.DedicatedHostReadOnlyPreflightError, "root-controlled"),
        ):
            agent._release_directory(RELEASE_SHA)

        for bad_mode in (stat.S_IFLNK | 0o777, stat.S_IFREG | 0o600):
            def malformed_release(path: object, *, mode=bad_mode) -> SimpleNamespace:
                if Path(path) == release_path:
                    return SimpleNamespace(st_mode=mode, st_uid=0)
                return safe_directory(path)

            with self.subTest(mode=bad_mode):
                with (
                    patch.object(agent.os, "lstat", side_effect=malformed_release),
                    self.assertRaisesRegex(agent.DedicatedHostReadOnlyPreflightError, "root-controlled"),
                ):
                    agent._release_directory(RELEASE_SHA)

    def test_runtime_and_staging_observations_reduce_raw_output_to_safe_counts_and_flags(self) -> None:
        def fixed_probe(name: str, **_: object) -> subprocess.CompletedProcess[bytes]:
            responses = {
                "docker_info": subprocess.CompletedProcess([], 0, stdout=b"26.1.0\n"),
                "docker_ps": subprocess.CompletedProcess([], 0, stdout=b"id-a\nid-b\n"),
                "matrix_count": subprocess.CompletedProcess([], 0, stdout=b"3\n"),
                "staging_mount": subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=(
                        b"/srv/trading-bot-three-site-staging-data "
                        b"ext4 rw,nosuid,nodev,noexec,relatime\n"
                    ),
                ),
            }
            return responses[name]

        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700)
        capacity = SimpleNamespace(f_frsize=4096, f_bavail=10)
        with (
            patch.object(agent, "_run_fixed_command", side_effect=fixed_probe),
            patch.object(agent, "_root_controlled_directory_chain", return_value=True),
            patch.object(agent.os, "lstat", return_value=directory),
            patch.object(agent.os, "statvfs", return_value=capacity),
        ):
            self.assertEqual(agent._observe_docker(), ("active", 2))
            self.assertEqual(agent._observe_matrix_process_count(), 3)
            self.assertFalse(agent._observe_current_link_present())
            self.assertEqual(
                agent._observe_staging_mount(),
                {
                    "present": True,
                    "filesystem": "ext4",
                    "available_bytes": 40_960,
                    "options": ["nodev", "noexec", "nosuid", "rw"],
                },
            )

    def test_staging_mount_requires_the_fixed_path_to_be_a_distinct_mountpoint(self) -> None:
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700)
        capacity = SimpleNamespace(f_frsize=4096, f_bavail=10)
        with (
            patch.object(
                agent,
                "_run_fixed_command",
                return_value=subprocess.CompletedProcess(
                    [], 0, stdout=b"/ ext4 rw,nosuid,nodev,noexec,relatime\n"
                ),
            ),
            patch.object(agent, "_root_controlled_directory_chain", return_value=True),
            patch.object(agent.os, "lstat", return_value=directory),
            patch.object(agent.os, "statvfs", return_value=capacity),
            self.assertRaisesRegex(
                agent.DedicatedHostReadOnlyPreflightError,
                "fixed mountpoint",
            ),
        ):
            agent._observe_staging_mount()

        command = agent._fixed_command("staging_mount")
        self.assertIn("--mountpoint", command)
        self.assertNotIn("--target", command)

    def test_staging_mount_rejects_unsafe_path_and_explicit_unsafe_options(self) -> None:
        safe_directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

        for mode, message in (
            (stat.S_IFLNK | 0o777, "root-controlled"),
            (stat.S_IFREG | 0o600, "root-controlled"),
            (stat.S_IFDIR | 0o775, "root-controlled"),
        ):
            def lstat(path: object, *, target=agent.FIXED_STAGING_MOUNT, bad_mode=mode) -> SimpleNamespace:
                if Path(path) == target:
                    return SimpleNamespace(st_mode=bad_mode, st_uid=0)
                return safe_directory

            with self.subTest(mode=mode):
                with (
                    patch.object(agent.os, "lstat", side_effect=lstat),
                    self.assertRaisesRegex(agent.DedicatedHostReadOnlyPreflightError, message),
                ):
                    agent._observe_staging_mount()

        capacity = SimpleNamespace(f_frsize=4096, f_bavail=10)
        for flag in ("suid", "dev", "exec", "ro"):
            output = (
                b"/srv/trading-bot-three-site-staging-data ext4 rw,"
                + flag.encode("ascii")
                + b",nosuid,nodev,noexec\n"
            )
            with self.subTest(flag=flag):
                with (
                    patch.object(agent, "_root_controlled_directory_chain", return_value=True),
                    patch.object(
                        agent,
                        "_run_fixed_command",
                        return_value=subprocess.CompletedProcess([], 0, stdout=output),
                    ),
                    patch.object(agent.os, "statvfs", return_value=capacity),
                    self.assertRaisesRegex(agent.DedicatedHostReadOnlyPreflightError, "not writable"),
                ):
                    agent._observe_staging_mount()

    def test_git_probes_disable_local_external_features(self) -> None:
        command = agent._fixed_command("git_tracked", release_sha=RELEASE_SHA)

        self.assertIn("--no-pager", command)
        for setting in (
            "core.hooksPath=/dev/null",
            "core.fsmonitor=false",
            "core.untrackedCache=false",
            "core.preloadIndex=false",
            "maintenance.auto=false",
            "gc.auto=0",
            "diff.external=false",
            "core.pager=cat",
        ):
            self.assertIn(setting, command)
        self.assertEqual(agent.FIXED_ENV["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(agent.FIXED_ENV["GIT_PROTOCOL_FROM_USER"], "0")

        completed = subprocess.CompletedProcess([], 0, stdout=b"")
        with patch.object(agent.subprocess, "run", return_value=completed) as run:
            self.assertIs(agent._run_fixed_command("git_head", release_sha=RELEASE_SHA), completed)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["env"], dict(agent.FIXED_ENV))

    def test_non_root_execution_is_rejected_before_probes(self) -> None:
        with patch.object(agent.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(agent.DedicatedHostReadOnlyPreflightError, "root"):
                agent._require_root()

    def test_main_emits_a_direct_valid_receipt_not_a_transport_wrapper(self) -> None:
        release, docker, process_count, current, staging = valid_observations()
        output = io.BytesIO()
        fake_stdout = SimpleNamespace(buffer=output)
        with (
            patch.object(agent, "_read_request_stdin", return_value=request_payload()),
            patch.object(agent, "_require_root"),
            patch.object(agent, "_observe_release", return_value=release),
            patch.object(agent, "_observe_docker", return_value=docker),
            patch.object(agent, "_observe_matrix_process_count", return_value=process_count),
            patch.object(agent, "_observe_current_link_present", return_value=current),
            patch.object(agent, "_observe_staging_mount", return_value=staging),
            patch.object(agent, "_observed_at", return_value="2026-07-30T16:00:00Z"),
            patch.object(agent.sys, "stdout", fake_stdout),
        ):
            self.assertEqual(agent.main([]), 0)

        self.assertEqual(parse_preflight_receipt(output.getvalue())["release_sha"], RELEASE_SHA)

    def test_source_has_no_write_network_or_destructive_docker_capability(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {
                "boto3",
                "botocore",
                "docker",
                "ftplib",
                "http",
                "paramiko",
                "requests",
                "socket",
                "urllib",
            }
        )

        forbidden_calls = {
            "chmod",
            "chown",
            "link",
            "makedirs",
            "mkdir",
            "mount",
            "open",
            "remove",
            "rename",
            "replace",
            "rmdir",
            "rmtree",
            "symlink",
            "system",
            "umount",
            "unlink",
            "write_bytes",
            "write_text",
        }
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Name) and node.func.id in forbidden_calls)
                    or (isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls)
                )
                for node in ast.walk(tree)
            )
        )

        runs = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ]
        self.assertEqual(len(runs), 1)
        self.assertTrue(
            any(
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in runs[0].keywords
            )
        )

        commands = [
            agent._fixed_command("git_head", release_sha=RELEASE_SHA),
            agent._fixed_command("git_tracked", release_sha=RELEASE_SHA),
            agent._fixed_command("git_untracked", release_sha=RELEASE_SHA),
            agent._fixed_command("docker_info"),
            agent._fixed_command("docker_ps"),
            agent._fixed_command("matrix_count"),
            agent._fixed_command("staging_mount"),
        ]
        destructive_docker_tokens = {
            "build",
            "compose",
            "exec",
            "kill",
            "load",
            "pull",
            "rm",
            "run",
            "save",
            "start",
            "stop",
        }
        self.assertFalse(
            any(
                command[0] == agent.DOCKER_BINARY
                and destructive_docker_tokens.intersection(command[1:])
                for command in commands
            )
        )
        self.assertTrue(all("ssh" not in command and "curl" not in command for command in commands))


if __name__ == "__main__":
    unittest.main()
