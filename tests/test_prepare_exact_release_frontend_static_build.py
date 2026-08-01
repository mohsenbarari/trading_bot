"""Focused tests for the local-only exact-release frontend build primitive.

These tests intentionally never call npm, unshare, mount, Docker, SSH, or an
Object Storage API.  The sandbox execution boundary is asserted through its
strict preflight contract and command construction only.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "prepare_exact_release_frontend_static_build.py"
SPEC = importlib.util.spec_from_file_location("exact_release_frontend_static_build_test", MODULE_PATH)
assert SPEC and SPEC.loader
BUILD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILD
SPEC.loader.exec_module(BUILD)


def _sha(value: bytes) -> str:
    return BUILD.sha256_bytes(value)


def _pinned_toolchain() -> object:
    return BUILD.PinnedToolchain(
        Path("/tool/node"),
        "a" * 64,
        "20.19.5",
        Path("/tool/npm/bin/npm-cli.js"),
        "b" * 64,
        "11.12.1",
    )


def _pinned_sandbox() -> object:
    return BUILD.PinnedSandboxTools(
        Path("/tool/python3"),
        "c" * 64,
        "3.12.3",
        Path("/tool/unshare"),
        "d" * 64,
        "2.40.2",
        Path("/usr/bin/setpriv"),
        "e" * 64,
        "2.40.2",
        Path("/usr/bin/mount"),
        "f" * 64,
        "2.40.2",
    )


def _verified_toolchain() -> object:
    return BUILD.VerifiedToolchain(
        Path("/tool/node"),
        "a" * 64,
        "20.19.5",
        Path("/tool/npm/bin/npm-cli.js"),
        "b" * 64,
        "11.12.1",
        Path("/tool/npm"),
        "c" * 64,
        ({"path": "bin/npm-cli.js", "sha256": "b" * 64, "bytes": 1},),
    )


def _verified_sandbox() -> object:
    return BUILD.VerifiedSandbox(
        Path("/tool/python3"),
        "c" * 64,
        "3.12.3",
        Path("/tool/unshare"),
        "d" * 64,
        "2.40.2",
        Path("/usr/bin/setpriv"),
        "e" * 64,
        "2.40.2",
        Path("/usr/bin/mount"),
        "f" * 64,
        "2.40.2",
        "1" * 64,
    )


def _fixed_policy() -> object:
    return BUILD.FixedBuildToolPolicy(
        Path("/usr/bin/git"),
        "2" * 64,
        "2.43.0",
        _pinned_toolchain(),
        _pinned_sandbox(),
        Path("/etc/trading-bot-three-site/runtime-closure.json"),
        "4" * 64,
        "3" * 64,
    )


def _runtime_closure() -> object:
    return BUILD.VerifiedRuntimeClosure(
        Path("/etc/trading-bot-three-site/runtime-closure.json"),
        "4" * 64,
        (
            BUILD.RuntimeClosureEntry(Path("/runtime/libc.so.6"), "/lib/x86_64-linux-gnu/libc.so.6", "5" * 64),
            BUILD.RuntimeClosureEntry(Path("/usr/bin/setpriv"), "/tool/setpriv", "e" * 64),
            BUILD.RuntimeClosureEntry(Path("/runtime/sh"), "/tool/sh", "6" * 64),
            BUILD.RuntimeClosureEntry(Path("/runtime/env"), "/usr/bin/env", "7" * 64),
        ),
    )


@unittest.skipUnless(os.geteuid() == 0, "exact-release build primitive is root-only")
class ExactReleaseFrontendStaticBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="exact-release-static-build-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.repository = self.root / "release"
        self.repository.mkdir(mode=0o700)
        self.candidates = self.root / "candidates"
        self.candidates.mkdir(mode=0o700)
        self.offline = self.root / "offline"
        self.offline.mkdir(mode=0o700)
        self._git("init")
        self._git("config", "user.email", "fixture@example.invalid")
        self._git("config", "user.name", "Exact Static Fixture")
        frontend = self.repository / "frontend"
        frontend.mkdir(mode=0o700)
        (frontend / "package.json").write_text('{"name":"fixture","scripts":{"build":"vite build"}}\n', encoding="ascii")
        (frontend / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{}}\n', encoding="ascii")
        (self.repository / "README.md").write_text("fixture\n", encoding="ascii")
        self._git("add", ".")
        self._git("commit", "-m", "fixture")
        self.release = self._git("rev-parse", "HEAD", capture=True)
        self.offline_archive = self.offline / "npm-cache.tar"
        self.offline_archive.write_bytes(b"offline-fixture")
        self.offline_archive.chmod(0o600)

    def _git(self, *arguments: str, capture: bool = False) -> str:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(self.repository), *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=capture,
        )
        return result.stdout.strip() if capture else ""

    def _plan(self, *, candidate: str, preflight: object, environment: dict[str, str] | None = None) -> dict[str, object]:
        lock_sha, _ = BUILD.sha256_file(self.repository / "frontend" / "package-lock.json")
        offline_sha, _ = BUILD.sha256_file(self.offline_archive)
        with (
            mock.patch.object(BUILD, "_load_fixed_tool_policy", return_value=_fixed_policy()),
            mock.patch.object(BUILD, "_verify_sandbox_tool", return_value=(Path("/usr/bin/git"), "2" * 64, "2.43.0")),
            mock.patch.object(BUILD, "_verify_toolchain", return_value=_verified_toolchain()),
            mock.patch.object(BUILD, "_verify_sandbox", return_value=_verified_sandbox()),
            mock.patch.object(BUILD, "_load_runtime_closure", return_value=_runtime_closure()),
            mock.patch.object(BUILD, "_preflight_sandbox", side_effect=preflight),
        ):
            return BUILD.prepare_exact_release_frontend_static_build(
                source_repository=self.repository,
                release_sha=self.release,
                candidate_directory=self.candidates / candidate,
                offline_dependency_archive=self.offline_archive,
                offline_dependency_archive_sha256=offline_sha,
                expected_package_lock_sha256=lock_sha,
                build_environment=environment,
            )

    def test_preflight_is_required_before_any_candidate_side_effect(self) -> None:
        candidate = self.candidates / "must-not-exist"

        with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "sandbox unavailable"):
            self._plan(
                candidate=candidate.name,
                preflight=BUILD.ExactReleaseFrontendBuildError("sandbox unavailable"),
            )

        self.assertFalse(candidate.exists())

    def test_missing_runtime_closure_blocks_before_candidate_creation(self) -> None:
        candidate = self.candidates / "no-closure"
        lock_sha, _ = BUILD.sha256_file(self.repository / "frontend" / "package-lock.json")
        offline_sha, _ = BUILD.sha256_file(self.offline_archive)
        with (
            mock.patch.object(BUILD, "_load_fixed_tool_policy", return_value=_fixed_policy()),
            mock.patch.object(BUILD, "_verify_sandbox_tool", return_value=(Path("/usr/bin/git"), "2" * 64, "2.43.0")),
            mock.patch.object(BUILD, "_verify_toolchain", return_value=_verified_toolchain()),
            mock.patch.object(BUILD, "_verify_sandbox", return_value=_verified_sandbox()),
        ):
            with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "runtime closure manifest"):
                BUILD.prepare_exact_release_frontend_static_build(
                    source_repository=self.repository,
                    release_sha=self.release,
                    candidate_directory=candidate,
                    offline_dependency_archive=self.offline_archive,
                    offline_dependency_archive_sha256=offline_sha,
                    expected_package_lock_sha256=lock_sha,
                )
        self.assertFalse(candidate.exists())

    def test_fixed_root_only_policy_is_the_only_runtime_closure_selector(self) -> None:
        policy_path = self.root / "fixed-tool-policy.json"
        tools = {
            "git": {"path": "/usr/bin/git", "sha256": "1" * 64, "version": "2.43.0"},
            "node": {"path": "/opt/node/bin/node", "sha256": "2" * 64, "version": "20.19.5"},
            "npm": {"path": "/opt/node/lib/node_modules/npm/bin/npm-cli.js", "sha256": "3" * 64, "version": "11.12.1"},
            "sandbox": {
                "python": {"path": "/usr/bin/python3", "sha256": "4" * 64, "version": "3.12.3"},
                "unshare": {"path": "/usr/bin/unshare", "sha256": "5" * 64, "version": "2.40.2"},
                "setpriv": {"path": "/usr/bin/setpriv", "sha256": "6" * 64, "version": "2.40.2"},
                "mount": {"path": "/usr/bin/mount", "sha256": "7" * 64, "version": "2.40.2"},
            },
        }
        value = {
            "schema": BUILD.FIXED_TOOL_POLICY_SCHEMA,
            **tools,
            "runtime_closure": {"path": "/etc/trading-bot-three-site/runtime-closure.json", "sha256": "8" * 64},
        }
        policy_path.write_bytes(BUILD.canonical_json_bytes(value) + b"\n")
        policy_path.chmod(0o600)
        with mock.patch.object(BUILD, "FIXED_TOOL_POLICY_PATH", policy_path):
            loaded = BUILD._load_fixed_tool_policy()
        self.assertEqual(Path("/etc/trading-bot-three-site/runtime-closure.json"), loaded.runtime_closure_manifest_path)
        self.assertEqual("8" * 64, loaded.runtime_closure_manifest_sha256)

        value.pop("runtime_closure")
        policy_path.write_bytes(BUILD.canonical_json_bytes(value) + b"\n")
        with mock.patch.object(BUILD, "FIXED_TOOL_POLICY_PATH", policy_path):
            with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "policy fields differ"):
                BUILD._load_fixed_tool_policy()

    def test_plan_records_only_hash_of_allowlisted_build_environment_and_integration_block(self) -> None:
        public_url = "https://public.example.invalid/api"
        plan = self._plan(candidate="plan-only", preflight=None, environment={"VITE_API_BASE_URL": public_url})

        self.assertEqual("planned", plan["status"])
        self.assertEqual(
            _sha(BUILD.canonical_json_bytes({"VITE_API_BASE_URL": public_url})),
            plan["build_environment_sha256"],
        )
        self.assertNotIn(public_url, json.dumps(plan, sort_keys=True))
        self.assertFalse(plan["transport_authority"]["transport_or_install_authorized"])
        self.assertEqual("blocked-pending-external-controller-signature", plan["receipt_authority"]["integration_status"])
        self.assertFalse((self.candidates / "plan-only").exists())

    def test_exact_git_archive_is_materialized_only_when_every_blob_matches_tree(self) -> None:
        candidate = self.candidates / "archive-candidate"
        candidate.mkdir(mode=0o700)
        tree = BUILD._git_tree(Path("/usr/bin/git"), self.repository, self.release)
        archive = candidate / BUILD.RELEASE_ARCHIVE_NAME

        archive_sha, archive_bytes = BUILD._write_release_archive(
            git=Path("/usr/bin/git"),
            source_repository=self.repository,
            release_sha=self.release,
            target=archive,
        )
        material = BUILD._verify_and_materialize_release_archive(
            archive_path=archive,
            source_directory=candidate / BUILD.SOURCE_DIRECTORY_NAME,
            tree=tree,
            release_sha=self.release,
        )

        self.assertEqual((archive_sha, archive_bytes), (material["archive_sha256"], material["archive_bytes"]))
        self.assertEqual(set(tree), {item["path"] for item in material["files"]})
        self.assertEqual(
            (self.repository / "frontend" / "package-lock.json").read_bytes(),
            (candidate / BUILD.SOURCE_DIRECTORY_NAME / "frontend" / "package-lock.json").read_bytes(),
        )

    def test_runtime_closure_is_canonical_hash_pinned_and_detects_drift(self) -> None:
        files: dict[str, Path] = {}
        for name, payload in (("setpriv", b"setpriv"), ("sh", b"sh"), ("env", b"env"), ("libc", b"libc")):
            path = self.root / name
            path.write_bytes(payload)
            path.chmod(0o700)
            files[name] = path
        entries = [
            {"host_path": str(files["libc"]), "target_path": "/lib/x86_64-linux-gnu/libc.so.6", "sha256": _sha(b"libc")},
            {"host_path": str(files["setpriv"]), "target_path": "/tool/setpriv", "sha256": _sha(b"setpriv")},
            {"host_path": str(files["sh"]), "target_path": "/tool/sh", "sha256": _sha(b"sh")},
            {"host_path": str(files["env"]), "target_path": "/usr/bin/env", "sha256": _sha(b"env")},
        ]
        manifest = self.root / "runtime-closure.json"
        payload = BUILD.canonical_json_bytes({"schema": BUILD.RUNTIME_CLOSURE_SCHEMA, "entries": entries}) + b"\n"
        manifest.write_bytes(payload)
        manifest.chmod(0o600)

        closure = BUILD._load_runtime_closure(
            manifest_path=manifest,
            expected_sha256=_sha(payload),
            setpriv_path=files["setpriv"],
            setpriv_sha256=_sha(b"setpriv"),
        )
        receipt = BUILD._runtime_closure_receipt(closure)
        self.assertEqual(_sha(payload), receipt["manifest_sha256"])
        self.assertNotIn(str(files["setpriv"]), json.dumps(receipt, sort_keys=True))

        files["sh"].write_bytes(b"changed")
        with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "does not match its pin"):
            BUILD._load_runtime_closure(
                manifest_path=manifest,
                expected_sha256=_sha(payload),
                setpriv_path=files["setpriv"],
                setpriv_sha256=_sha(b"setpriv"),
            )

    def test_runtime_closure_is_rehashed_immediately_before_individual_mount(self) -> None:
        host = self.root / "closure-file"
        host.write_bytes(b"before")
        host.chmod(0o700)
        entry = BUILD.RuntimeClosureEntry(host, "/lib/x86_64-linux-gnu/libx.so.1", _sha(b"before"))
        host.write_bytes(b"after")
        with (
            mock.patch.object(BUILD, "_sandbox_prepare_file_target") as target,
            mock.patch.object(BUILD, "_sandbox_bind") as bind,
        ):
            with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "changed before"):
                BUILD._sandbox_bind_runtime_closure(
                    mount=Path("/mount"),
                    sandbox_root=self.root / "sandbox",
                    entries=(entry,),
                    field="fixture runtime closure",
                )
        target.assert_not_called()
        bind.assert_not_called()

    def test_runtime_closure_rejects_directory_and_broad_mount_targets(self) -> None:
        for target in ("/lib", "/lib64", "/usr", "/usr/bin", "/bin", "/tool"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "outside the fixed minimal runtime"):
                    BUILD._runtime_closure_target(target)
        for target in ("/lib/", "/usr/lib/", "/lib//x.so"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "canonical file"):
                    BUILD._runtime_closure_target(target)

    def test_sandbox_preflight_uses_full_closure_chroot_contract_before_candidate(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b"", None)
        with (
            mock.patch.object(BUILD, "_require_root_controlled_file", return_value=Path("/root/helper.py")),
            mock.patch.object(
                BUILD,
                "_require_pinned_root_tool_unchanged",
                side_effect=[Path("/tool/unshare"), Path("/tool/python3")],
            ) as recheck,
            mock.patch.object(BUILD, "_run", return_value=completed) as run,
        ):
            BUILD._preflight_sandbox(
                _verified_sandbox(),
                _verified_toolchain(),
                _runtime_closure(),
                build_uid=BUILD.DEFAULT_BUILD_UID,
                tmpfs_bytes=BUILD.MIN_SANDBOX_TMPFS_BYTES,
            )
        argv = run.call_args.args[0]
        self.assertEqual("/tool/unshare", argv[0])
        self.assertTrue({"--mount", "--net", "--pid", "--fork", "--mount-proc"}.issubset(argv))
        self.assertIn("_sandbox-probe", argv)
        self.assertIn("--node", argv)
        self.assertIn("--npm-runtime-root", argv)
        self.assertIn("--runtime-closure-manifest", argv)
        self.assertNotIn("_sandbox-unprivileged-probe", argv)
        probe = inspect.getsource(BUILD._sandbox_probe_main)
        self.assertIn("_sandbox_probe_layout", probe)
        self.assertIn("os.chroot(root)", probe)
        self.assertIn("_sandbox_drop_prefix", probe)
        self.assertLess(probe.index("os.chroot(root)"), probe.index('field="sandbox dropped-UID node version"'))
        self.assertEqual(
            [
                mock.call(Path("/tool/unshare"), "d" * 64, field="sandbox preflight unshare tool"),
                mock.call(Path("/tool/python3"), "c" * 64, field="sandbox preflight python tool"),
            ],
            recheck.call_args_list,
        )

    def test_node_and_npm_are_not_version_executed_as_root(self) -> None:
        tools = self.root / "tools"
        tools.mkdir(mode=0o700)
        node = tools / "node"
        node.write_bytes(b"node fixture")
        node.chmod(0o700)
        npm_root = tools / "npm"
        (npm_root / "bin").mkdir(parents=True, mode=0o700)
        (npm_root / "package.json").write_bytes(b"{}\n")
        npm = npm_root / "bin" / "npm-cli.js"
        npm.write_bytes(b"npm fixture")
        npm.chmod(0o700)
        pin = BUILD.PinnedToolchain(
            node,
            BUILD.sha256_file(node)[0],
            "20.19.5",
            npm,
            BUILD.sha256_file(npm)[0],
            "11.12.1",
        )
        with mock.patch.object(BUILD, "_run", side_effect=AssertionError("tool version must not run here")):
            verified = BUILD._verify_toolchain(pin)
        self.assertEqual(node, verified.node_path)
        self.assertNotIn("_run(", inspect.getsource(BUILD._verify_toolchain))
        source = inspect.getsource(BUILD._sandbox_probe_main)
        self.assertIn("_sandbox_drop_prefix", source)
        self.assertIn('"/tool/node", "--version"', source)
        self.assertEqual("20.19.5", BUILD._extract_simple_version("v20.19.5", field="fixture node"))

    def test_git_prefix_disables_local_checkout_hooks_and_fsmonitor(self) -> None:
        prefix = BUILD._git_command_prefix(Path("/usr/bin/git"), self.repository)
        self.assertEqual("/usr/bin/git", prefix[0])
        for setting in (
            "core.fsmonitor=false",
            "core.useBuiltinFSMonitor=false",
            "core.hooksPath=/dev/null",
            "core.pager=cat",
            "credential.helper=",
        ):
            self.assertIn(setting, prefix)
        self.assertEqual(str(self.repository), prefix[-1])
        self.assertEqual("/dev/null", BUILD._git_environment()["GIT_CONFIG_GLOBAL"])
        self.assertEqual("1", BUILD._git_environment()["GIT_NO_LAZY_FETCH"])
        self.assertIn("_git_command_prefix", inspect.getsource(BUILD._write_release_archive))

    def test_git_metadata_accepts_root_controlled_linked_worktree(self) -> None:
        linked = self.root / "linked-release"
        subprocess.run(
            ["/usr/bin/git", "-C", str(self.repository), "worktree", "add", "--detach", str(linked), self.release],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        BUILD._require_root_controlled_git_metadata(linked)
        prefix = BUILD._git_command_prefix(Path("/usr/bin/git"), linked)
        self.assertEqual(str(linked), prefix[-1])

    def test_git_metadata_rejects_unsafe_linked_gitdir_and_commondir_chain(self) -> None:
        linked = self.root / "linked-release"
        subprocess.run(
            ["/usr/bin/git", "-C", str(self.repository), "worktree", "add", "--detach", str(linked), self.release],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pointer = BUILD._read_root_controlled_git_pointer(
            linked / ".git", field="fixture linked .git pointer", prefix=b"gitdir: "
        )
        gitdir = BUILD._resolve_root_controlled_git_pointer(
            pointer,
            base=linked,
            expected_kind="directory",
            field="fixture linked gitdir",
        )
        original_gitdir_mode = gitdir.stat().st_mode & 0o777
        gitdir.chmod(0o777)
        try:
            with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "gitdir"):
                BUILD._require_root_controlled_git_metadata(linked)
        finally:
            gitdir.chmod(original_gitdir_mode)

        common_pointer = gitdir / "commondir"
        original_common_pointer_mode = common_pointer.stat().st_mode & 0o777
        common_pointer.chmod(0o666)
        try:
            with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "commondir"):
                BUILD._require_root_controlled_git_metadata(linked)
        finally:
            common_pointer.chmod(original_common_pointer_mode)

    def test_git_metadata_rejects_a_local_config_include(self) -> None:
        config = self.repository / ".git" / "config"
        config.write_bytes(config.read_bytes() + b"\n[include]\n\tpath = /tmp/untrusted-git-config\n")
        config.chmod(0o600)

        with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "must not include"):
            BUILD._require_root_controlled_git_metadata(self.repository)

    def test_pinned_root_launcher_recheck_detects_drift_before_execution(self) -> None:
        launcher = self.root / "root-launcher"
        launcher.write_bytes(b"before")
        launcher.chmod(0o700)
        pin = BUILD.sha256_file(launcher)[0]
        self.assertEqual(launcher, BUILD._require_pinned_root_tool_unchanged(launcher, pin, field="fixture launcher"))
        launcher.write_bytes(b"after")
        with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "changed before execution"):
            BUILD._require_pinned_root_tool_unchanged(launcher, pin, field="fixture launcher")

    def test_actual_sandbox_launch_rechecks_root_launchers_before_unshare(self) -> None:
        source = inspect.getsource(BUILD._run_sandboxed_build)
        self.assertIn("sandbox build unshare tool", source)
        self.assertIn("sandbox build python tool", source)
        self.assertLess(source.index("sandbox build unshare tool"), source.index('field="isolated offline frontend static build"'))

    def test_offline_cache_member_limit_fails_closed(self) -> None:
        archive = self.offline / "bounded-cache.tar"
        with tarfile.open(archive, "w") as handle:
            for name in ("npm-cache/one", "npm-cache/two"):
                info = tarfile.TarInfo(name)
                info.size = 1
                handle.addfile(info, io.BytesIO(b"x"))
        archive.chmod(0o600)
        candidate = self.candidates / "cache-candidate"
        candidate.mkdir(mode=0o700)
        with mock.patch.object(BUILD, "MAX_OFFLINE_CACHE_MEMBERS", 1):
            with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "too many members"):
                BUILD._extract_offline_cache(
                    archive_path=archive,
                    expected_sha256=BUILD.sha256_file(archive)[0],
                    candidate_directory=candidate,
                )

    def test_output_scan_is_ordered_and_receipt_rejects_urls_and_secret_fields(self) -> None:
        output = self.candidates / "output"
        output.mkdir(mode=0o700)
        (output / "z.js").write_bytes(b"z\n")
        (output / "assets").mkdir(mode=0o700)
        (output / "assets" / "a.js").write_bytes(b"a\n")
        for path in (output / "z.js", output / "assets" / "a.js"):
            path.chmod(0o600)

        scanned = BUILD._scan_regular_tree(output, field="output", maximum_files=10, maximum_bytes=1024)
        self.assertEqual(["assets/a.js", "z.js"], [item["path"] for item in scanned["files"]])
        self.assertEqual(scanned["files"], sorted(scanned["files"], key=lambda item: item["path"]))

        with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "prohibited key"):
            BUILD._write_receipt(
                output / "bad-key.json",
                {
                    "api_url": "hashed",
                    "receipt_authority": {
                        "unsigned": True,
                        "provenance": BUILD.LOCAL_RECEIPT_PROVENANCE,
                        "integration_status": BUILD.LOCAL_RECEIPT_INTEGRATION_STATUS,
                    },
                },
            )
        with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "URL or secret-shaped"):
            BUILD._write_receipt(
                output / "bad-value.json",
                {
                    "value": "https://example.invalid",
                    "receipt_authority": {
                        "unsigned": True,
                        "provenance": BUILD.LOCAL_RECEIPT_PROVENANCE,
                        "integration_status": BUILD.LOCAL_RECEIPT_INTEGRATION_STATUS,
                    },
                },
            )
        with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "integration block"):
            BUILD._write_receipt(output / "missing-authority.json", {"schema": BUILD.SCHEMA})

        receipt = BUILD._write_receipt(
            output / "receipt.json",
            {
                "schema": BUILD.SCHEMA,
                "output": scanned,
                "build_environment_sha256": "a" * 64,
                "receipt_authority": {
                    "unsigned": True,
                    "provenance": BUILD.LOCAL_RECEIPT_PROVENANCE,
                    "integration_status": BUILD.LOCAL_RECEIPT_INTEGRATION_STATUS,
                },
            },
        )
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        self.assertEqual(BUILD.sha256_bytes(BUILD.canonical_json_bytes(unsigned)), receipt["receipt_sha256"])

    def test_sandbox_policy_has_all_boundaries_limits_and_no_runtime_directory_mounts(self) -> None:
        policy = BUILD._sandbox_policy(tmpfs_bytes=BUILD.MIN_SANDBOX_TMPFS_BYTES, build_uid=BUILD.DEFAULT_BUILD_UID)
        self.assertTrue(policy["mount_namespace_required"])
        self.assertTrue(policy["network_namespace_required"])
        self.assertTrue(policy["pid_namespace_required"])
        self.assertTrue(policy["no_inherited_file_descriptors"])
        self.assertTrue(policy["explicit_capability_drop"])
        self.assertTrue(policy["no_untrusted_processes_before_output_handoff"])
        self.assertTrue(policy["runtime_closure_individual_files_only"])
        self.assertTrue(policy["host_runtime_directory_bindings_denied"])
        self.assertFalse(policy["lifecycle_scripts_enabled"])
        self.assertNotIn("/lib", policy["read_only_bindings"])
        self.assertNotIn("/lib64", policy["read_only_bindings"])
        self.assertNotIn("/usr", policy["read_only_bindings"])
        self.assertEqual(BUILD.MAX_BUILD_PROCESSES, policy["rlimit_nproc"])
        self.assertEqual(BUILD.MAX_BUILD_ADDRESS_SPACE_BYTES, policy["rlimit_as_bytes"])
        self.assertEqual(BUILD.MAX_BUILD_CPU_SECONDS, policy["rlimit_cpu_seconds"])
        self.assertEqual(BUILD.MAX_OUTPUT_BYTES, policy["rlimit_fsize_bytes"])
        self.assertEqual(BUILD.MAX_CAPTURED_COMMAND_BYTES, policy["command_stdout_bytes"])
        self.assertEqual(BUILD.MAX_COMMAND_STDERR_BYTES, policy["command_stderr_bytes"])
        prefix = BUILD._sandbox_drop_prefix(Path("/usr/bin/setpriv"), BUILD.DEFAULT_BUILD_UID)
        self.assertIn("--no-new-privs", prefix)
        self.assertIn("--inh-caps=-all", prefix)
        self.assertIn("--ambient-caps=-all", prefix)
        self.assertIn("--bounding-set=-all", prefix)
        runner = inspect.getsource(BUILD._run)
        self.assertIn("close_fds=True", runner)
        self.assertIn("pass_fds=()", runner)

    def test_sandbox_npm_install_includes_dev_dependencies_without_relaxing_offline_guards(self) -> None:
        control = {
            "setpriv": Path("/tool/setpriv"),
            "build_uid": BUILD.DEFAULT_BUILD_UID,
        }
        build_environment = {"VITE_API_BASE_URL": "https://public.example.invalid/api"}

        with mock.patch.object(BUILD, "_run") as run:
            BUILD._sandbox_run_npm(control=control, build_environment=build_environment)

        self.assertEqual(2, run.call_count)
        install = run.call_args_list[0]
        install_argv = install.args[0]
        drop_prefix = BUILD._sandbox_drop_prefix(Path("/tool/setpriv"), BUILD.DEFAULT_BUILD_UID)
        self.assertEqual(
            drop_prefix,
            install_argv[: len(drop_prefix)],
        )
        self.assertEqual(
            [
                "/tool/node",
                "/tool/npm/bin/npm-cli.js",
                "ci",
                "--offline",
                "--include=dev",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            install_argv[len(drop_prefix) :],
        )
        self.assertEqual(Path("/scratch/source/frontend"), install.kwargs["cwd"])
        self.assertEqual("production", install.kwargs["env"]["NODE_ENV"])
        self.assertEqual("true", install.kwargs["env"]["npm_config_offline"])
        self.assertEqual("true", install.kwargs["env"]["npm_config_ignore_scripts"])
        self.assertEqual("http://127.0.0.1:9", install.kwargs["env"]["npm_config_registry"])
        self.assertEqual(build_environment["VITE_API_BASE_URL"], install.kwargs["env"]["VITE_API_BASE_URL"])

        build = run.call_args_list[1]
        self.assertIn("--ignore-scripts", build.args[0])
        self.assertEqual(install.kwargs["env"], build.kwargs["env"])

    def test_sandbox_quiescence_kills_descendants_before_root_output_handoff(self) -> None:
        with (
            mock.patch.object(BUILD, "_reap_sandbox_children") as reap,
            mock.patch.object(BUILD, "_remaining_sandbox_pids", side_effect=[{19, 23}, set(), set()]),
            mock.patch.object(BUILD.os, "kill") as kill,
            mock.patch.object(BUILD.time, "sleep") as sleep,
        ):
            BUILD._require_quiescent_sandbox_before_output_handoff()
        self.assertEqual([mock.call(19, BUILD.signal.SIGKILL), mock.call(23, BUILD.signal.SIGKILL)], kill.call_args_list)
        self.assertGreaterEqual(reap.call_count, 3)
        self.assertGreaterEqual(sleep.call_count, 2)

    def test_sandbox_quiescence_fails_closed_when_descendants_remain(self) -> None:
        with (
            mock.patch.object(BUILD, "MAX_SANDBOX_QUIESCENCE_PASSES", 2),
            mock.patch.object(BUILD, "_reap_sandbox_children"),
            mock.patch.object(BUILD, "_remaining_sandbox_pids", return_value={19}),
            mock.patch.object(BUILD.os, "kill"),
            mock.patch.object(BUILD.time, "sleep"),
        ):
            with self.assertRaisesRegex(BUILD.ExactReleaseFrontendBuildError, "did not quiesce"):
                BUILD._require_quiescent_sandbox_before_output_handoff()

    def test_root_output_handoff_requires_quiescent_build_namespace(self) -> None:
        child = inspect.getsource(BUILD._sandbox_child_main)
        self.assertLess(
            child.index("_require_quiescent_sandbox_before_output_handoff"),
            child.index("_sandbox_copy_static_output"),
        )

    def test_build_resource_limits_are_set_as_hard_limits(self) -> None:
        with mock.patch.object(BUILD.resource, "setrlimit") as setrlimit:
            BUILD._apply_build_rlimits()
        self.assertEqual(
            [
                mock.call(BUILD.resource.RLIMIT_NPROC, (BUILD.MAX_BUILD_PROCESSES, BUILD.MAX_BUILD_PROCESSES)),
                mock.call(
                    BUILD.resource.RLIMIT_AS,
                    (BUILD.MAX_BUILD_ADDRESS_SPACE_BYTES, BUILD.MAX_BUILD_ADDRESS_SPACE_BYTES),
                ),
                mock.call(BUILD.resource.RLIMIT_CPU, (BUILD.MAX_BUILD_CPU_SECONDS, BUILD.MAX_BUILD_CPU_SECONDS)),
                mock.call(BUILD.resource.RLIMIT_FSIZE, (BUILD.MAX_OUTPUT_BYTES, BUILD.MAX_OUTPUT_BYTES)),
            ],
            setrlimit.call_args_list,
        )

    def test_cli_has_no_caller_selected_tool_anchors(self) -> None:
        options = {option for action in BUILD._parser()._actions for option in action.option_strings}
        for option in (
            "--node",
            "--node-sha256",
            "--npm",
            "--git",
            "--sandbox-python",
            "--sandbox-unshare",
            "--sandbox-setpriv",
            "--sandbox-mount",
            "--runtime-closure-manifest",
            "--runtime-closure-manifest-sha256",
        ):
            self.assertNotIn(option, options)
        parameters = inspect.signature(BUILD.prepare_exact_release_frontend_static_build).parameters
        self.assertNotIn("toolchain", parameters)
        self.assertNotIn("sandbox_tools", parameters)
        self.assertNotIn("git_binary", parameters)
        self.assertNotIn("runtime_closure_manifest", parameters)
        self.assertNotIn("runtime_closure_manifest_sha256", parameters)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("FIXED_READ_ONLY_SYSTEM_DIRECTORIES", source)
        self.assertNotIn("_sandbox-unprivileged-probe", source)

    def test_command_capture_retains_bounded_stdout(self) -> None:
        result = BUILD._run(
            ["/usr/bin/printf", "bounded-output"],
            field="fixture command",
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(b"bounded-output", result.stdout)

    def test_module_has_no_transport_or_container_client(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(imports & {"boto3", "botocore", "docker", "paramiko", "requests", "socket", "urllib"})
        self.assertNotIn("allow_offline_lifecycle_scripts", MODULE_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
