from __future__ import annotations

import hashlib
import io
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py"
LAUNCHER_PATH = ROOT / "scripts/production_shadow_convergence_source_set_launcher"
SPEC = importlib.util.spec_from_file_location(
    "production_shadow_convergence_source_set_runtime_bootstrap",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"


class HeldPlanFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="source-set-bootstrap-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "release"
        self.plan_path = self.root / "held-plan.json"
        self.release.mkdir(mode=0o700)
        self.uid = os.geteuid()

    def plan(self, *, schema: str | None = None, blobs: dict[str, str] | None = None) -> dict[str, object]:
        required = {
            path: ("a" if index % 2 else "b") * 64
            for index, path in enumerate(sorted(MODULE.STATIC_REQUIRED_BLOBS))
        }
        if blobs is not None:
            required = blobs
        return {
            "schema": schema or MODULE.HELD_PLAN_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "release": {"commit_sha": "1" * 40, "tree_sha": "2" * 40},
            "source_policy_sha256": "c" * 64,
            "controller_wheelhouse_sha256": "d" * 64,
            "wheel_input_receipt_sha256": "e" * 64,
            "closure_scope": MODULE.PRE_RUNTIME_CLOSURE_SCOPE,
            "bootstrap_path": MODULE.BOOTSTRAP_SOURCE,
            "required_blobs": required,
        }

    def write_plan(self, document: dict[str, object] | None = None, *, mode: int = 0o600) -> None:
        self.plan_path.write_bytes(MODULE.canonical_json_bytes(document or self.plan()))
        self.plan_path.chmod(mode)

    def open_plan(self, *, writable: bool = False) -> int:
        return os.open(
            self.plan_path,
            (os.O_RDWR if writable else os.O_RDONLY)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )


class HeldPlanParsingTests(HeldPlanFixture):
    def test_reads_exact_v3_pre_runtime_plan_from_held_root_only_descriptor(self) -> None:
        self.write_plan()
        descriptor = self.open_plan()
        self.addCleanup(os.close, descriptor)
        plan = MODULE.read_held_runtime_plan_fd(descriptor, expected_uid=self.uid)
        self.assertEqual(plan.campaign_id, CAMPAIGN_ID)
        self.assertEqual(plan.bootstrap_path, MODULE.BOOTSTRAP_SOURCE)
        self.assertEqual(set(plan.required_blobs), MODULE.STATIC_REQUIRED_BLOBS)

    def test_v2_or_non_exact_pre_runtime_blob_plan_is_rejected(self) -> None:
        for document, message in (
            (self.plan(schema="production-shadow-controller-runtime-held-plan-v2"), "schema or fields differ"),
            (
                self.plan(
                    blobs={
                        path: "a" * 64
                        for path in MODULE.STATIC_REQUIRED_BLOBS
                        if path != MODULE.BUILDER_SOURCE
                    }
                ),
                "pre-runtime blob map differs",
            ),
            (
                self.plan(
                    blobs={
                        **{
                            path: "a" * 64
                            for path in MODULE.STATIC_REQUIRED_BLOBS
                        },
                        "scripts/unreachable.py": "b" * 64,
                    }
                ),
                "pre-runtime blob map differs",
            ),
        ):
            with self.subTest(message=message):
                self.write_plan(document)
                descriptor = self.open_plan()
                try:
                    with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, message):
                        MODULE.read_held_runtime_plan_fd(descriptor, expected_uid=self.uid)
                finally:
                    os.close(descriptor)

    def test_post_runtime_sources_cannot_enter_the_pre_runtime_plan(self) -> None:
        document = self.plan(
            blobs={
                **{
                    path: "a" * 64
                    for path in MODULE.STATIC_REQUIRED_BLOBS
                },
                MODULE.PRODUCER_SOURCE: "b" * 64,
            }
        )
        self.write_plan(document)
        descriptor = self.open_plan()
        self.addCleanup(os.close, descriptor)
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "unavailable post-runtime"):
            MODULE.read_held_runtime_plan_fd(descriptor, expected_uid=self.uid)

    def test_missing_or_wrong_closure_scope_plan_is_rejected(self) -> None:
        missing = self.plan()
        missing.pop("closure_scope")
        wrong = self.plan()
        wrong["closure_scope"] = "post-runtime-controller-closure"
        for document, message in (
            (missing, "schema or fields differ"),
            (wrong, "closure scope differs"),
        ):
            with self.subTest(message=message):
                self.write_plan(document)
                descriptor = self.open_plan()
                try:
                    with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, message):
                        MODULE.read_held_runtime_plan_fd(descriptor, expected_uid=self.uid)
                finally:
                    os.close(descriptor)

    def test_non_root_only_plan_mode_is_rejected(self) -> None:
        self.write_plan(mode=0o644)
        descriptor = self.open_plan()
        self.addCleanup(os.close, descriptor)
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "root-controlled regular file"):
            MODULE.read_held_runtime_plan_fd(descriptor, expected_uid=self.uid)

    def test_held_descriptor_rejects_path_replacement(self) -> None:
        self.write_plan()
        descriptor = self.open_plan()
        self.addCleanup(os.close, descriptor)
        identity = MODULE.capture_held_regular_file(
            descriptor,
            label="test plan",
            expected_uid=self.uid,
            exact_mode=0o600,
        )
        replacement = self.root / "replacement.json"
        replacement.write_bytes(MODULE.canonical_json_bytes(self.plan()))
        replacement.chmod(0o600)
        os.replace(replacement, self.plan_path)
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "descriptor changed"):
            MODULE.read_held_bytes(identity, label="test plan", maximum=MODULE.MAX_PLAN_BYTES)

    def test_changed_held_descriptor_is_rejected(self) -> None:
        self.write_plan()
        descriptor = self.open_plan(writable=True)
        self.addCleanup(os.close, descriptor)
        identity = MODULE.capture_held_regular_file(
            descriptor,
            label="test plan",
            expected_uid=self.uid,
            exact_mode=0o600,
        )
        os.ftruncate(descriptor, 1)
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "descriptor changed"):
            MODULE.read_held_bytes(identity, label="test plan", maximum=MODULE.MAX_PLAN_BYTES)


class ExactGitBootstrapFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="source-set-bootstrap-git-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "release"
        self.plan_path = self.root / "held-plan.json"
        self.release.mkdir(mode=0o700)
        self.uid = os.geteuid()
        self._write_release()
        self._initialize_git()
        self._write_exact_plan()

    @staticmethod
    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def write(self, relative: str, payload: bytes, *, mode: int = 0o600) -> Path:
        path = self.release / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        cursor = path.parent
        while cursor != self.release.parent:
            cursor.chmod(0o700)
            if cursor == self.release:
                break
            cursor = cursor.parent
        path.write_bytes(payload)
        path.chmod(mode)
        return path

    def _write_release(self) -> None:
        source = {
            MODULE.BOOTSTRAP_SOURCE: b"import argparse\n",
            MODULE.VERIFIER_SOURCE: b"import hashlib\n",
            MODULE.BUILDER_SOURCE: (
                b"from scripts import verify_production_shadow_controller_runtime_closure as VERIFY\n"
            ),
            # Deliberately unsafe post-runtime sources are Git-tracked but not
            # part of the v3 pre-runtime map or graph.
            MODULE.PRODUCER_SOURCE: b"import sys\nsys.path.insert(0, '/unsafe')\n",
            MODULE.GATE_SOURCE: (
                b"def deferred():\n"
                b"    from scripts import orchestrate_production_shadow_prepared_clone_inventory\n"
            ),
            MODULE.CUTOVER_CONTROLLER_SOURCE: b"import sys\nsys.path.insert(0, '/unsafe')\n",
            MODULE.PHASE_VERIFIER_SOURCE: b"import sys\nsys.path.insert(0, '/unsafe')\n",
            MODULE.LAUNCHER_SOURCE: b"#!/bin/sh\nexit 0\n",
            MODULE.POLICY_SOURCE: b"{}\n",
            MODULE.REQUIREMENTS_SOURCE: b"cffi==2.1.0\n",
            MODULE.WHEELHOUSE_SOURCE: b"a" * 64 + b"  synthetic.whl\n",
            "scripts/__init__.py": b"# explicit package\n",
        }
        for relative, payload in source.items():
            self.write(relative, payload, mode=0o755 if relative == MODULE.LAUNCHER_SOURCE else 0o600)

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(self.release), *arguments],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def _initialize_git(self) -> None:
        self.git("init", "--quiet")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Synthetic Test")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "synthetic release")
        self.release_sha = self.git("rev-parse", "HEAD")
        self.release_tree_sha = self.git("rev-parse", "HEAD^{tree}")
        self.git("checkout", "--quiet", "--detach")

    def _open_release(self) -> int:
        return os.open(
            self.release,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )

    def _write_plan(self, blobs: dict[str, str]) -> None:
        policy = (self.release / MODULE.POLICY_SOURCE).read_bytes()
        wheelhouse = (self.release / MODULE.WHEELHOUSE_SOURCE).read_bytes()
        document = {
            "schema": MODULE.HELD_PLAN_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "release": {"commit_sha": self.release_sha, "tree_sha": self.release_tree_sha},
            "source_policy_sha256": self.digest(policy),
            "controller_wheelhouse_sha256": self.digest(wheelhouse),
            "wheel_input_receipt_sha256": "f" * 64,
            "closure_scope": MODULE.PRE_RUNTIME_CLOSURE_SCOPE,
            "bootstrap_path": MODULE.BOOTSTRAP_SOURCE,
            "required_blobs": blobs,
        }
        self.plan_path.write_bytes(MODULE.canonical_json_bytes(document))
        self.plan_path.chmod(0o600)

    def _write_exact_plan(self) -> None:
        provisional = {
            relative: self.digest((self.release / relative).read_bytes())
            for relative in MODULE.STATIC_REQUIRED_BLOBS
        }
        self._write_plan(provisional)
        release_descriptor = self._open_release()
        try:
            release_identity = MODULE.capture_held_directory(
                release_descriptor,
                label="test release",
                expected_uid=self.uid,
            )
            plan_descriptor = os.open(self.plan_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
            try:
                plan = MODULE.read_held_runtime_plan_fd(plan_descriptor, expected_uid=self.uid)
                tracked = MODULE._verify_exact_git_state(release_identity, plan)  # noqa: SLF001
                graph = MODULE.discover_reachable_controller_sources(
                    release_identity,
                    tracked_blobs=tracked,
                    expected_uid=self.uid,
                )
            finally:
                os.close(plan_descriptor)
        finally:
            os.close(release_descriptor)
        self.graph = graph
        self._write_plan(
            {
                relative: self.digest((self.release / relative).read_bytes())
                for relative in graph.paths
            }
        )

    def open_inputs(self) -> tuple[int, int, int]:
        release_descriptor = self._open_release()
        plan_descriptor = os.open(
            self.plan_path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        bootstrap_descriptor = os.open(
            self.release / MODULE.BOOTSTRAP_SOURCE,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        self.addCleanup(os.close, release_descriptor)
        self.addCleanup(os.close, plan_descriptor)
        self.addCleanup(os.close, bootstrap_descriptor)
        return release_descriptor, plan_descriptor, bootstrap_descriptor

    def verified_inputs(self):
        release_descriptor, plan_descriptor, bootstrap_descriptor = self.open_inputs()
        return MODULE.verify_held_bootstrap_inputs(
            release_descriptor=release_descriptor,
            plan_descriptor=plan_descriptor,
            bootstrap_descriptor=bootstrap_descriptor,
            expected_uid=self.uid,
        )


class ExactGitBootstrapTests(ExactGitBootstrapFixture):
    def test_proves_exact_pre_runtime_graph_and_excludes_post_runtime_sources(self) -> None:
        inputs = self.verified_inputs()
        self.assertIn("scripts/__init__.py", inputs.reachable_blobs)
        self.assertEqual(inputs.reachable_blobs, self.graph.paths)
        self.assertEqual(set(inputs.reachable_blobs), MODULE.STATIC_REQUIRED_BLOBS)
        self.assertFalse(set(inputs.reachable_blobs) & MODULE.POST_RUNTIME_UNAVAILABLE_SOURCES)
        self.assertEqual(len(inputs.source_graph_sha256), 64)

    def test_plan_rejects_missing_extra_or_wrong_graph_blob(self) -> None:
        exact = {
            relative: self.digest((self.release / relative).read_bytes())
            for relative in self.graph.paths
        }
        cases = {
            "missing": {key: value for key, value in exact.items() if key != MODULE.BUILDER_SOURCE},
            "extra": {**exact, "scripts/unreachable.py": "a" * 64},
            "wrong": {**exact, MODULE.BUILDER_SOURCE: "b" * 64},
        }
        for label, blobs in cases.items():
            with self.subTest(label=label):
                self._write_plan(blobs)
                with self.assertRaisesRegex(
                    MODULE.SourceSetRuntimeBootstrapError,
                    "omits required|blob map|blob digest",
                ):
                    self.verified_inputs()
        self._write_exact_plan()

    def test_git_worktree_drift_is_rejected_before_blob_use(self) -> None:
        builder = self.release / MODULE.BUILDER_SOURCE
        builder.write_bytes(b"VALUE = 2\n")
        builder.chmod(0o600)
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "exact detached clean"):
            self.verified_inputs()

    def test_pre_runtime_path_mutation_is_rejected(self) -> None:
        builder = self.release / MODULE.BUILDER_SOURCE
        builder.write_bytes(
            b"import sys\n"
            b"sys.path.insert(0, '/unsafe')\n"
        )
        builder.chmod(0o600)
        self.git("add", MODULE.BUILDER_SOURCE)
        self.git("commit", "--quiet", "-m", "bad graph")
        self.release_sha = self.git("rev-parse", "HEAD")
        self.release_tree_sha = self.git("rev-parse", "HEAD^{tree}")
        self.git("checkout", "--quiet", "--detach")
        self._write_plan(
            {
                relative: self.digest((self.release / relative).read_bytes())
                for relative in MODULE.STATIC_REQUIRED_BLOBS
            }
        )
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "dynamic|deferred|sys.path"):
            self.verified_inputs()

    def test_capability_is_single_use_and_rejects_cross_campaign_and_closed_state(self) -> None:
        capability = MODULE.activate_verified_held_bootstrap(self.verified_inputs(), expected_uid=self.uid)
        self.addCleanup(capability.close)
        binding = capability.binding
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "binding differs"):
            capability.consume_for(
                operation="attest-runtime-closure",
                campaign_id="9c3020b5-03d2-49ba-a575-ca26d842a18f",
                release_sha=binding.release_sha,
                release_tree_sha=binding.release_tree_sha,
                held_plan_sha256=binding.held_plan_sha256,
            )
        capability.consume_for(
            operation="attest-runtime-closure",
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            release_tree_sha=binding.release_tree_sha,
            held_plan_sha256=binding.held_plan_sha256,
        )
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "already consumed"):
            capability.consume_for(
                operation="attest-runtime-closure",
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
                release_tree_sha=binding.release_tree_sha,
                held_plan_sha256=binding.held_plan_sha256,
            )
        capability.close()
        capability.close()
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "unavailable"):
            capability.consume_for(
                operation="materialize-runtime-closure",
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
                release_tree_sha=binding.release_tree_sha,
                held_plan_sha256=binding.held_plan_sha256,
            )

    def test_capability_rejects_replaced_plan_and_wrong_release_descriptor(self) -> None:
        capability = MODULE.activate_verified_held_bootstrap(self.verified_inputs(), expected_uid=self.uid)
        self.addCleanup(capability.close)
        binding = capability.binding
        other_release = self.root / "other-release"
        other_release.mkdir(mode=0o700)
        other_descriptor = os.open(other_release, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        self.addCleanup(os.close, other_descriptor)
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "release descriptor differs"):
            capability.consume_for(
                operation="attest-runtime-closure",
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
                release_tree_sha=binding.release_tree_sha,
                held_plan_sha256=binding.held_plan_sha256,
                release_descriptor=other_descriptor,
            )
        replacement = self.root / "replacement-plan.json"
        replacement.write_bytes(self.plan_path.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, self.plan_path)
        with self.assertRaisesRegex(MODULE.SourceSetRuntimeBootstrapError, "descriptor changed|root-controlled"):
            capability.consume_for(
                operation="attest-runtime-closure",
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
                release_tree_sha=binding.release_tree_sha,
                held_plan_sha256=binding.held_plan_sha256,
            )

    def test_registration_helper_passes_its_exact_concrete_types(self) -> None:
        class Receiver:
            registered: tuple[type[object], type[object]] | None = None

            def _register_held_bootstrap_types(
                self,
                *,
                capability_type: type[object],
                lease_type: type[object],
            ) -> None:
                self.registered = (capability_type, lease_type)

        receiver = Receiver()
        MODULE.register_held_bootstrap_types(receiver)
        self.assertEqual(
            receiver.registered,
            (MODULE.HeldFdBootstrapCapability, MODULE.HeldBootstrapLease),
        )

    def test_release_sourced_bootstrap_cli_is_disabled_before_any_proof(self) -> None:
        with mock.patch.object(MODULE, "verify_held_bootstrap_inputs") as proof, mock.patch(
            "sys.stderr",
            new_callable=io.StringIO,
        ) as stderr:
            result = MODULE.main(
                [
                    "--release-fd",
                    "4",
                    "--plan-fd",
                    "5",
                    "--bootstrap-fd",
                    "6",
                    "prove",
                ]
            )
        self.assertEqual(result, 1)
        proof.assert_not_called()
        self.assertIn("separately installed immutable bootstrap", stderr.getvalue())


class LauncherContractTests(unittest.TestCase):
    def test_launcher_is_explicitly_unavailable_without_a_trusted_bootstrap(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="ascii")
        metadata = LAUNCHER_PATH.stat(follow_symlinks=False)
        self.assertEqual(metadata.st_mode & 0o777, 0o755)
        self.assertIn("separately installed immutable bootstrap", source)
        self.assertNotIn("/proc/self/fd", source)
        self.assertNotIn("/usr/bin/python", source)
        self.assertNotIn("production_shadow_convergence_source_set_runtime_bootstrap.py", source)

    def test_launcher_never_executes_a_release_sourced_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-set-launcher-") as temporary:
            root = Path(temporary)
            release = root / "release"
            bootstrap = release / MODULE.BOOTSTRAP_SOURCE
            marker = root / "release-bootstrap-executed"
            bootstrap.parent.mkdir(parents=True)
            bootstrap.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed', encoding='ascii')\n",
                encoding="ascii",
            )
            bootstrap.chmod(0o700)
            held_plan = root / "held-plan.json"
            held_plan.write_text("{}", encoding="ascii")
            held_plan.chmod(0o600)

            completed = subprocess.run(
                [
                    str(LAUNCHER_PATH),
                    "--release-root",
                    str(release),
                    "--held-plan",
                    str(held_plan),
                    "prove",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 69)
            self.assertIn("separately installed immutable bootstrap", completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
