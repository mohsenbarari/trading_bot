#!/usr/bin/env python3
"""Focused tests for the repository-local immutable dispatcher prototype."""

from __future__ import annotations

import hashlib
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
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "immutable_production_shadow_controller_dispatcher.py"
)
SPEC = importlib.util.spec_from_file_location("immutable_dispatcher_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


class ImmutableDispatcherFixture:
    campaign_id = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"

    def __init__(self, temporary: Path) -> None:
        self.temporary = temporary
        self.plan_root = temporary / "plans"
        self.releases_root = temporary / "releases"
        self.plan_root.mkdir(mode=0o700)
        self.releases_root.mkdir(mode=0o700)
        self.campaign_root = self.plan_root / self.campaign_id
        self.campaign_root.mkdir(mode=0o700)
        self.marker = temporary / "release-code-was-executed"
        self.runtime_output = temporary / "runtime-output"
        self.git = Path(shutil.which("git") or "/usr/bin/git").resolve()
        self.release_root = temporary / "release-source"
        self.release_root.mkdir(mode=0o700)
        self._git("init", "--quiet")
        self._write_release_sources()
        self._git("add", ".")
        self._git(
            "-c",
            "user.name=immutable-dispatcher-test",
            "-c",
            "user.email=immutable-dispatcher@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        )
        self.release_sha = self._git_output("rev-parse", "HEAD")
        self.release_tree_sha = self._git_output("rev-parse", "HEAD^{tree}")
        self._git("checkout", "--quiet", "--detach", self.release_sha)
        destination = self.releases_root / self.release_sha
        self.release_root.rename(destination)
        self.release_root = destination
        self._write_plan()

    def _git(self, *arguments: str) -> None:
        subprocess.run(
            (os.fspath(self.git), "-C", os.fspath(self.release_root), *arguments),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _git_output(self, *arguments: str) -> str:
        completed = subprocess.run(
            (os.fspath(self.git), "-C", os.fspath(self.release_root), *arguments),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return completed.stdout.decode("ascii").strip()

    def _write_release_sources(self) -> None:
        sources = {
            "scripts/__init__.py": b"# package marker\n",
            MODULE.BOOTSTRAP_SOURCE: (
                b"from pathlib import Path\n"
                + f"Path({str(self.marker)!r}).write_text('executed', encoding='ascii')\n".encode("ascii")
                + f"Path({str(self.runtime_output)!r}).mkdir()\n".encode("ascii")
                + b"raise RuntimeError('release code must never run')\n"
            ),
            "scripts/verify_production_shadow_controller_runtime_closure.py": b"raise RuntimeError('never import verifier')\n",
            "scripts/build_production_shadow_controller_runtime_closure.py": b"raise RuntimeError('never import builder')\n",
            MODULE.SOURCE_POLICY_SOURCE: b'{"fixture":"policy"}\n',
            MODULE.REQUIREMENTS_SOURCE: b"fixture==1\n",
            MODULE.WHEELHOUSE_SOURCE: b"0" * 64 + b"  fixture.whl\n",
        }
        for relative, payload in sources.items():
            target = self.release_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            target.chmod(0o600)
        for directory in (self.release_root / "scripts", self.release_root / "deploy"):
            directory.chmod(0o700)
        (self.release_root / "deploy" / "production-shadow-controller-runtime").chmod(0o700)

    def _blob_map(self) -> dict[str, str]:
        return {
            relative: hashlib.sha256((self.release_root / relative).read_bytes()).hexdigest()
            for relative in sorted(MODULE.PRE_RUNTIME_SOURCE_PATHS)
        }

    def _plan_document(self) -> dict[str, object]:
        blobs = self._blob_map()
        return {
            "schema": MODULE.HELD_PLAN_SCHEMA,
            "campaign_id": self.campaign_id,
            "release": {"commit_sha": self.release_sha, "tree_sha": self.release_tree_sha},
            "source_policy_sha256": blobs[MODULE.SOURCE_POLICY_SOURCE],
            "controller_wheelhouse_sha256": blobs[MODULE.WHEELHOUSE_SOURCE],
            "wheel_input_receipt_sha256": "1" * 64,
            "closure_scope": MODULE.PRE_RUNTIME_CLOSURE_SCOPE,
            "bootstrap_path": MODULE.BOOTSTRAP_SOURCE,
            "required_blobs": blobs,
        }

    @property
    def plan_path(self) -> Path:
        return self.campaign_root / MODULE.HELD_PLAN_FILENAME

    def _write_plan(self, document: dict[str, object] | None = None) -> None:
        self.plan_path.write_bytes(_canonical(document if document is not None else self._plan_document()))
        self.plan_path.chmod(0o600)

    def config(self, *, clean_process: bool = False) -> object:
        return MODULE.DispatcherConfig(
            plan_root=self.plan_root,
            releases_root=self.releases_root,
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            git_binary=self.git,
            require_clean_process=clean_process,
            repository_local_test_mode=True,
        )


class ImmutableProductionShadowControllerDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.fixture = ImmutableDispatcherFixture(Path(self.temporary_directory.name))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_proves_exact_local_pre_runtime_bytes_without_executing_release_code(self) -> None:
        result = MODULE.prove_pre_runtime(
            self.fixture.campaign_id,
            config=self.fixture.config(),
        )

        self.assertEqual(result["status"], "pre_runtime_proven")
        self.assertEqual(result["campaign_id"], self.fixture.campaign_id)
        self.assertEqual(result["release_sha"], self.fixture.release_sha)
        self.assertEqual(result["required_blob_count"], len(MODULE.PRE_RUNTIME_SOURCE_PATHS))
        self.assertIs(result["release_python_executed"], False)
        self.assertIs(result["runtime_created"], False)
        self.assertFalse(self.fixture.marker.exists())
        self.assertFalse(self.fixture.runtime_output.exists())

    def test_dirty_release_fails_closed_without_executing_bootstrap(self) -> None:
        bootstrap = self.fixture.release_root / MODULE.BOOTSTRAP_SOURCE
        bootstrap.write_bytes(bootstrap.read_bytes() + b"# uncommitted mutation\n")
        bootstrap.chmod(0o600)

        with self.assertRaisesRegex(MODULE.ImmutableDispatcherError, "exact detached clean"):
            MODULE.prove_pre_runtime(self.fixture.campaign_id, config=self.fixture.config())

        self.assertFalse(self.fixture.marker.exists())
        self.assertFalse(self.fixture.runtime_output.exists())

    def test_invalid_plan_fails_before_release_is_opened_or_executed(self) -> None:
        plan = self.fixture._plan_document()
        plan["closure_scope"] = "post-runtime-controller-closure"
        self.fixture._write_plan(plan)

        with self.assertRaisesRegex(MODULE.ImmutableDispatcherError, "closure scope differs"):
            MODULE.prove_pre_runtime(self.fixture.campaign_id, config=self.fixture.config())

        self.assertFalse(self.fixture.marker.exists())
        self.assertFalse(self.fixture.runtime_output.exists())

    def test_remote_config_is_rejected_without_contacting_it(self) -> None:
        self.fixture._git("remote", "add", "origin", "https://example.invalid/not-contacted.git")

        with self.assertRaisesRegex(MODULE.ImmutableDispatcherError, "remote-free"):
            MODULE.prove_pre_runtime(self.fixture.campaign_id, config=self.fixture.config())

        self.assertFalse(self.fixture.marker.exists())
        self.assertFalse(self.fixture.runtime_output.exists())

    def test_production_cli_is_unavailable_without_test_roots(self) -> None:
        completed = subprocess.run(
            (sys.executable, os.fspath(MODULE_PATH)),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(completed.returncode, 69)
        self.assertEqual(completed.stdout, "")
        self.assertIn("not a production installation", completed.stderr)
        self.assertFalse(self.fixture.marker.exists())

    def test_library_refuses_production_root_configuration_before_any_open(self) -> None:
        production = MODULE.DispatcherConfig(
            plan_root=MODULE.PRODUCTION_PLAN_ROOT,
            releases_root=MODULE.PRODUCTION_RELEASES_ROOT,
            expected_uid=0,
            expected_gid=0,
            git_binary=self.fixture.git,
            require_clean_process=False,
            repository_local_test_mode=False,
        )

        with self.assertRaisesRegex(MODULE.ImmutableDispatcherError, "limited to local test roots"):
            MODULE.prove_pre_runtime(self.fixture.campaign_id, config=production)

        self.assertFalse(self.fixture.marker.exists())
        self.assertFalse(self.fixture.runtime_output.exists())

    def test_proof_invokes_only_fixed_local_git_queries(self) -> None:
        with mock.patch.object(MODULE.subprocess, "run", wraps=subprocess.run) as run:
            MODULE.prove_pre_runtime(self.fixture.campaign_id, config=self.fixture.config())

        commands = [tuple(call.args[0]) for call in run.call_args_list]
        self.assertGreaterEqual(len(commands), len(MODULE.PRE_RUNTIME_SOURCE_PATHS) + 5)
        forbidden = {"clone", "fetch", "pull", "push", "ls-remote", "archive", "daemon"}
        for command in commands:
            self.assertEqual(command[0], os.fspath(self.fixture.git))
            self.assertIn("-C", command)
            self.assertTrue(any(part.startswith("/proc/self/fd/") for part in command))
            self.assertFalse(forbidden.intersection(command))
        self.assertFalse(self.fixture.marker.exists())
        self.assertFalse(self.fixture.runtime_output.exists())

    def test_repository_local_cli_requires_clean_isolated_startup(self) -> None:
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        completed = subprocess.run(
            (
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                os.fspath(MODULE_PATH),
                "--repository-local-test-mode",
                "--test-plan-root",
                os.fspath(self.fixture.plan_root),
                "--test-releases-root",
                os.fspath(self.fixture.releases_root),
                "--campaign-id",
                self.fixture.campaign_id,
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            cwd="/",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "pre_runtime_proven")
        self.assertFalse(self.fixture.marker.exists())
        self.assertFalse(self.fixture.runtime_output.exists())

    def test_existing_release_launcher_remains_exact_fail_closed(self) -> None:
        launcher = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "production_shadow_convergence_source_set_launcher"
        )
        before = launcher.read_bytes()
        completed = subprocess.run(
            ("/bin/sh", os.fspath(launcher)),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(completed.returncode, 69)
        self.assertIn("separately installed immutable bootstrap", completed.stderr)
        self.assertEqual(launcher.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o755)


if __name__ == "__main__":
    unittest.main()
