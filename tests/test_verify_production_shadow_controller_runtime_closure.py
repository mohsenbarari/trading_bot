from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/verify_production_shadow_controller_runtime_closure.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_production_shadow_controller_runtime_closure",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


RELEASE_SHA = "1" * 40
TREE_SHA = "2" * 40


class RuntimeClosureFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="controller-runtime-closure-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.release = self.root / "release"
        self.site = self.runtime / MODULE.SITE_PACKAGES_DIRECTORY
        self.runtime.mkdir(mode=0o700)
        self.release.mkdir(mode=0o700)
        self.site.mkdir(mode=0o700)
        self.uid = os.getuid()
        self.policy = b'{"synthetic":"controller-policy"}'
        self.wheelhouse_manifest = b"synthetic wheel manifest\n"
        self.write_release(MODULE.SOURCE_POLICY_RELATIVE, self.policy)
        self.write_release(MODULE.WHEELHOUSE_MANIFEST_RELATIVE, self.wheelhouse_manifest)
        self.sources: dict[str, bytes] = {}
        for relative in sorted(MODULE.CONTROL_SOURCE_PATHS):
            self.sources[relative] = f"source:{relative}".encode("ascii")
            self.write_release(relative, self.sources[relative])
        self.site_files: dict[str, bytes] = {
            "_cffi_backend.cpython-312-x86_64-linux-gnu.so": b"synthetic cffi extension",
            "cffi/__init__.py": b"# synthetic cffi\n",
            "cryptography/__init__.py": b"# synthetic cryptography\n",
            "cryptography/hazmat/bindings/_rust.abi3.so": b"synthetic rust extension",
            "pycparser/__init__.py": b"# synthetic pycparser\n",
        }
        for relative, payload in self.site_files.items():
            self.write_site(relative, payload)

    @staticmethod
    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def _write(self, root: Path, relative: str, value: bytes) -> Path:
        destination = root / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for parent in (destination.parent, *destination.parents):
            if parent == root.parent:
                break
            parent.chmod(0o700)
            if parent == root:
                break
        destination.write_bytes(value)
        destination.chmod(0o600)
        return destination

    def write_release(self, relative: str, value: bytes) -> Path:
        return self._write(self.release, relative, value)

    def write_site(self, relative: str, value: bytes) -> Path:
        return self._write(self.site, relative, value)

    def manifest(self) -> dict[str, object]:
        site_files = {path: self.digest(value) for path, value in self.site_files.items()}
        project_sources = {path: self.digest(value) for path, value in self.sources.items()}
        document: dict[str, object] = {
            "schema": MODULE.RUNTIME_CLOSURE_SCHEMA,
            "namespace": MODULE.RUNTIME_NAMESPACE,
            "release": {"commit_sha": RELEASE_SHA, "tree_sha": TREE_SHA},
            "python": {
                "implementation": "cpython",
                "major": 3,
                "minor": 12,
                "architecture": "x86_64",
            },
            "source_policy_sha256": self.digest(self.policy),
            "wheelhouse_manifest_sha256": self.digest(self.wheelhouse_manifest),
            "packages": list(MODULE.REQUIRED_PACKAGES),
            "site_packages": {
                "path": MODULE.SITE_PACKAGES_DIRECTORY,
                "files": site_files,
                "files_sha256": MODULE._hash_mapping(site_files),
                "import_origins": dict(MODULE.REQUIRED_IMPORT_ORIGINS),
            },
            "project_sources": project_sources,
            "control_sources": dict(project_sources),
        }
        document["runtime_binding_sha256"] = MODULE._sha256(document)
        return document

    def write_manifest(self, document: dict[str, object] | None = None) -> Path:
        payload = MODULE.canonical_json_bytes(document or self.manifest())
        path = self.runtime / MODULE.RUNTIME_MANIFEST_FILENAME
        path.write_bytes(payload)
        path.chmod(0o600)
        return path

    def attest(self):
        self.write_manifest()
        attestation = MODULE.attest_runtime_closure(
            self.runtime,
            self.release,
            expected_uid=self.uid,
            expected_release_sha=RELEASE_SHA,
            expected_release_tree_sha=TREE_SHA,
        )
        self.addCleanup(attestation.close)
        return attestation


class SuccessfulRuntimeClosureTests(RuntimeClosureFixture):
    def test_clean_preimport_state_accepts_real_isolated_python_startup(self) -> None:
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
                "-c",
                (
                    "import runpy; value=runpy.run_path("
                    + repr(str(MODULE_PATH))
                    + "); value['require_clean_preimport_state'](); print('ok')"
                ),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "ok\n")

    def test_attests_exact_synthetic_closure_and_project_sources(self) -> None:
        attestation = self.attest()
        self.assertEqual(attestation.release_sha, RELEASE_SHA)
        self.assertEqual(attestation.release_tree_sha, TREE_SHA)
        self.assertEqual(attestation.site_file_count, len(self.site_files))
        self.assertEqual(attestation.project_source_count, len(self.sources))
        self.assertEqual(
            attestation.site_packages_root,
            f"/proc/self/fd/{attestation.site_packages_descriptor}",
        )

    def test_manifest_bytes_are_canonical_and_binding_is_required(self) -> None:
        document = self.manifest()
        document["runtime_binding_sha256"] = "f" * 64
        self.write_manifest(document)
        with self.assertRaisesRegex(MODULE.RuntimeClosureError, "binding digest differs"):
            MODULE.attest_runtime_closure(
                self.runtime,
                self.release,
                expected_uid=self.uid,
                expected_release_sha=RELEASE_SHA,
                expected_release_tree_sha=TREE_SHA,
            )

    def test_site_capability_close_is_idempotent(self) -> None:
        attestation = self.attest()
        descriptor = attestation.site_packages_descriptor
        attestation.close()
        self.assertEqual(attestation.site_packages_descriptor, -1)
        attestation.close()
        with self.assertRaises(OSError):
            os.fstat(descriptor)


class RejectionRuntimeClosureTests(RuntimeClosureFixture):
    def assert_preimport_subprocess_rejected(self, setup: str) -> None:
        completed = subprocess.run(
            [
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
                "-c",
                (
                    "import runpy,sys,types; "
                    + setup
                    + "; value=runpy.run_path("
                    + repr(str(MODULE_PATH))
                    + "); value['require_clean_preimport_state']()"
                ),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("RuntimeClosureError", completed.stderr)

    def assert_rejected(self, message: str) -> None:
        with self.assertRaisesRegex(MODULE.RuntimeClosureError, message):
            MODULE.attest_runtime_closure(
                self.runtime,
                self.release,
                expected_uid=self.uid,
                expected_release_sha=RELEASE_SHA,
                expected_release_tree_sha=TREE_SHA,
            )

    def test_uninventoried_runtime_file_is_rejected(self) -> None:
        self.write_manifest()
        self.write_site("cryptography/extra.py", b"unexpected")
        self.assert_rejected("site-packages inventory differs")

    def test_preimport_rejects_arbitrary_interpreter_path(self) -> None:
        self.assert_preimport_subprocess_rejected("sys.path.append('/tmp/evil')")

    def test_preimport_rejects_preloaded_project_namespace(self) -> None:
        self.assert_preimport_subprocess_rejected("sys.modules['core']=types.ModuleType('core')")

    def test_preimport_rejects_preloaded_cryptography_submodule(self) -> None:
        self.assert_preimport_subprocess_rejected(
            "sys.modules['cryptography.exceptions']=types.ModuleType('cryptography.exceptions')"
        )

    def test_preimport_rejects_preloaded_cffi_binary_extension(self) -> None:
        self.assert_preimport_subprocess_rejected(
            "sys.modules['_cffi_backend']=types.ModuleType('_cffi_backend')"
        )

    def test_preimport_rejects_preloaded_site_module(self) -> None:
        self.assert_preimport_subprocess_rejected("sys.modules['site']=types.ModuleType('site')")

    def test_runtime_pth_hook_is_rejected_even_when_inventoried(self) -> None:
        self.site_files["injected.pth"] = b"/tmp/untrusted\n"
        self.write_site("injected.pth", self.site_files["injected.pth"])
        self.write_manifest()
        self.assert_rejected("startup hook")

    def test_hidden_runtime_file_is_rejected(self) -> None:
        self.write_manifest()
        self.write_site(".hidden.py", b"unexpected")
        self.assert_rejected("unsafe path")

    def test_tampered_release_source_is_rejected(self) -> None:
        self.write_manifest()
        relative = sorted(self.sources)[0]
        self.write_release(relative, b"tampered")
        self.assert_rejected("release source digest differs")

    def test_tampered_release_bound_policy_is_rejected(self) -> None:
        self.write_manifest()
        self.write_release(MODULE.SOURCE_POLICY_RELATIVE, b"tampered policy")
        self.assert_rejected("source policy digest differs")

    def test_tampered_release_bound_wheelhouse_manifest_is_rejected(self) -> None:
        self.write_manifest()
        self.write_release(MODULE.WHEELHOUSE_MANIFEST_RELATIVE, b"tampered manifest")
        self.assert_rejected("wheelhouse manifest digest differs")

    def test_wrong_release_binding_is_rejected(self) -> None:
        self.write_manifest()
        with self.assertRaisesRegex(MODULE.RuntimeClosureError, "release commit differs"):
            MODULE.attest_runtime_closure(
                self.runtime,
                self.release,
                expected_uid=self.uid,
                expected_release_sha="9" * 40,
            )

    def test_unexpected_runtime_root_entry_is_rejected(self) -> None:
        self.write_manifest()
        extra = self.runtime / "extra"
        extra.write_bytes(b"unexpected")
        extra.chmod(0o600)
        self.assert_rejected("root contains unexpected entries")

    def test_manifest_rejects_abi_origin_substitution(self) -> None:
        document = self.manifest()
        site = dict(document["site_packages"])
        origins = dict(site["import_origins"])
        origins["_cffi_backend"] = "_cffi_backend.cpython-311-x86_64-linux-gnu.so"
        site["import_origins"] = origins
        document["site_packages"] = site
        document["runtime_binding_sha256"] = MODULE._sha256(
            {key: value for key, value in document.items() if key != "runtime_binding_sha256"}
        )
        self.write_manifest(document)
        self.assert_rejected("import origins differ")

    def test_symlinked_runtime_file_is_rejected(self) -> None:
        self.write_manifest()
        target = self.root / "target"
        target.write_bytes(b"target")
        target.chmod(0o600)
        os.symlink(target, self.site / "linked.py")
        self.assert_rejected("non-regular file")


if __name__ == "__main__":
    unittest.main()
