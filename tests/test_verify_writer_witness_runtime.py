from __future__ import annotations

import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/verify_writer_witness_runtime.py"
SPEC = importlib.util.spec_from_file_location("verify_writer_witness_runtime", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


class FakeFileHash:
    def __init__(self, mode: str, value: str) -> None:
        self.mode = mode
        self.value = value


class FakePackagePath:
    def __init__(
        self,
        value: str,
        *,
        file_hash: FakeFileHash | None,
        size: int | None,
    ) -> None:
        self.value = value
        self.hash = file_hash
        self.size = size

    def __str__(self) -> str:
        return self.value

    @property
    def name(self) -> str:
        return PurePosixPath(self.value).name


class FakeDistribution:
    def __init__(
        self,
        name: object,
        version: object,
        *,
        metadata_root: Path,
        site_packages: Path,
        files: list[FakePackagePath] | None,
    ) -> None:
        self.metadata = {"Name": name}
        self.version = version
        self._path = metadata_root
        self._site_packages = site_packages
        self.files = files

    def locate_file(self, entry: object) -> Path:
        return self._site_packages / str(entry)


def _record_hash(value: bytes) -> FakeFileHash:
    digest = hashlib.sha256(value).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return FakeFileHash("sha256", encoded)


CPYTHON_312 = runtime.RuntimeIdentity("cpython", 3, 12, 9, True)


def valid_system_attestation() -> dict[str, object]:
    return {
        "system_elf_closure_sha256": "1" * 64,
        "system_elf_object_count": 20,
        "system_os_release_sha256": "2" * 64,
        "system_package_count": 10,
        "system_package_set_sha256": "3" * 64,
        "system_runtime_attested": "yes",
        "system_runtime_manifest_sha256": "4" * 64,
        "system_runtime_sha256": "5" * 64,
        "system_stdlib_entry_count": 100,
        "system_stdlib_tree_sha256": "6" * 64,
    }


class LockParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="writer-witness-runtime-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def write_lock(self, value: bytes) -> Path:
        path = self.root / "requirements.lock"
        path.write_bytes(value)
        return path

    def test_repository_lock_is_strictly_parseable(self) -> None:
        path = ROOT / "deploy/writer-witness/requirements.lock"
        inventory = runtime.load_lock(path)
        expected_entries = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        ]
        self.assertEqual(len(inventory.packages), len(expected_entries))
        self.assertEqual(inventory.packages["jinja2"], "3.1.6")
        self.assertEqual(inventory.packages["pydantic-core"], "2.46.4")
        self.assertEqual(inventory.packages["typing-extensions"], "4.16.0")
        self.assertEqual(inventory.sha256, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_comments_blank_lines_case_and_separators_normalize(self) -> None:
        path = self.write_lock(
            b"# reviewed lock\nExample_Package==1.2.0RC1+Linux_01\n\nOther.Name==2!3.4-1\n"
        )
        inventory = runtime.load_lock(path)
        self.assertEqual(
            inventory.packages,
            {
                "example-package": "1.2.0rc1+linux.1",
                "other-name": "2!3.4.post1",
            },
        )

    def test_normalized_duplicate_is_rejected(self) -> None:
        path = self.write_lock(b"Example_Package==1.0\nexample.package==1.0\n")
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "duplicate normalized"):
            runtime.load_lock(path)

    def test_empty_lock_is_rejected(self) -> None:
        path = self.write_lock(b"# comments only\n\n")
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "no package entries"):
            runtime.load_lock(path)

    def test_non_utf8_bom_and_crlf_are_rejected(self) -> None:
        invalid = (
            (b"package==1.0\xff\n", "valid UTF-8"),
            ("\ufeffpackage==1.0\n".encode(), "canonical UTF-8"),
            (b"package==1.0\r\n", "canonical UTF-8"),
        )
        for value, message in invalid:
            with self.subTest(message=message):
                path = self.write_lock(value)
                with self.assertRaisesRegex(runtime.RuntimeAttestationError, message):
                    runtime.load_lock(path)

    def test_non_exact_requirement_syntax_is_rejected(self) -> None:
        invalid_lines = (
            " package==1.0",
            "package ==1.0",
            "package==1.0 ",
            "package>=1.0",
            "package==1.0; python_version >= '3.12'",
            "package==1.0 --hash=sha256:abc",
            "--index-url=https://example.invalid",
            "package @ https://example.invalid/package.whl",
            "package[extra]==1.0",
            "package==1.0 # inline comment",
        )
        for line in invalid_lines:
            with self.subTest(line=line):
                path = self.write_lock(f"{line}\n".encode())
                with self.assertRaisesRegex(runtime.RuntimeAttestationError, "exact name==version"):
                    runtime.load_lock(path)

    def test_invalid_pep440_version_is_rejected(self) -> None:
        path = self.write_lock(b"package==not_a_version\n")
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "invalid PEP 440"):
            runtime.load_lock(path)

    def test_symlinked_hardlinked_and_special_locks_are_rejected(self) -> None:
        real = self.write_lock(b"package==1.0\n")
        symlink = self.root / "linked.lock"
        symlink.symlink_to(real)
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "cannot safely open"):
            runtime.load_lock(symlink)

        hardlink = self.root / "second-name.lock"
        os.link(real, hardlink)
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "exactly one hard link"):
            runtime.load_lock(real)
        hardlink.unlink()

        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "not a regular file"):
            runtime.load_lock(self.root)
        if hasattr(os, "mkfifo"):
            fifo = self.root / "requirements.fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "not a regular file"):
                runtime.load_lock(fifo)

    def test_expected_owner_is_optional_and_enforced_when_requested(self) -> None:
        path = self.write_lock(b"package==1.0\n")
        self.assertEqual(runtime.load_lock(path).packages, {"package": "1.0"})
        self.assertEqual(
            runtime.load_lock(path, expected_uid=os.getuid()).packages,
            {"package": "1.0"},
        )
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "unexpected owner"):
            runtime.load_lock(path, expected_uid=os.getuid() + 1)

    def test_file_change_during_read_is_rejected(self) -> None:
        path = self.write_lock(b"package==1.0\n")
        real_fstat = runtime.os.fstat
        calls = 0

        def changing_fstat(descriptor: int):
            nonlocal calls
            calls += 1
            observed = real_fstat(descriptor)
            if calls == 2:
                values = list(observed)
                values[8] += 1
                return os.stat_result(values)
            return observed

        with mock.patch.object(runtime.os, "fstat", side_effect=changing_fstat):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "changed during"):
                runtime.load_lock(path)

    def test_operating_system_read_failure_is_safely_wrapped(self) -> None:
        path = self.write_lock(b"package==1.0\n")
        with mock.patch.object(runtime.os, "read", side_effect=OSError("sensitive detail")):
            with self.assertRaisesRegex(
                runtime.RuntimeAttestationError, "cannot safely read requirements lock"
            ) as raised:
                runtime.load_lock(path)
        self.assertNotIn("sensitive detail", str(raised.exception))


class RuntimeInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="writer-witness-runtime-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.prefix = self.root / "venv"
        self.site_packages = self.prefix / "lib/python3.12/site-packages"
        self.site_packages.mkdir(parents=True)
        (self.prefix / "bin").mkdir()
        (self.prefix / "include/python3.12").mkdir(parents=True)
        self.base_python = self.root / "base-python3.12"
        self.base_python.write_bytes(b"bounded-test-cpython-3.12\n")
        self.base_python.chmod(0o755)
        self.interpreter = self.prefix / "bin/python"
        (self.prefix / "bin/python3.12").symlink_to(self.base_python)
        self.interpreter.symlink_to("python3.12")
        (self.prefix / "bin/python3").symlink_to("python3.12")
        (self.prefix / "lib64").symlink_to("lib")
        for name in ("Activate.ps1", "activate", "activate.csh", "activate.fish"):
            (self.prefix / "bin" / name).write_text(
                f"# structural test activation file: {name}\n", encoding="utf-8"
            )
        (self.prefix / "pyvenv.cfg").write_text(
            "\n".join(
                (
                    f"home = {self.base_python.parent}",
                    "include-system-site-packages = false",
                    "version = 3.12.9",
                    f"executable = {self.base_python}",
                    f"command = {self.base_python} -m venv --without-pip {self.prefix}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        self.python_sha256 = hashlib.sha256(self.base_python.read_bytes()).hexdigest()
        self.lock = self.root / "requirements.lock"
        self.lock.write_text("Alpha_Pkg==1.0\nbeta==2.0\n", encoding="utf-8")
        self.alpha, self.alpha_paths = self.install_distribution("Alpha_Pkg", "1.0")
        self.beta, self.beta_paths = self.install_distribution("beta", "2.0")

    def install_distribution(
        self,
        name: object,
        version: object,
        *,
        storage_slug: str | None = None,
    ) -> tuple[FakeDistribution, dict[str, Path]]:
        slug = storage_slug or str(name).lower().replace("-", "_").replace(".", "_")
        version_text = str(version)
        metadata_root = self.site_packages / f"{slug}-{version_text}.dist-info"
        metadata_root.mkdir(parents=True)
        package_relative = f"{slug}/__init__.py"
        metadata_relative = f"{metadata_root.name}/METADATA"
        record_relative = f"{metadata_root.name}/RECORD"
        values = {
            package_relative: f"__version__ = {version_text!r}\n".encode(),
            metadata_relative: f"Name: {name}\nVersion: {version_text}\n".encode(),
        }
        paths: dict[str, Path] = {}
        entries: list[FakePackagePath] = []
        record_rows: list[str] = []
        for relative, value in sorted(values.items()):
            path = self.site_packages / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            paths[relative] = path
            file_hash = _record_hash(value)
            entries.append(FakePackagePath(relative, file_hash=file_hash, size=len(value)))
            record_rows.append(f"{relative},sha256={file_hash.value},{len(value)}")
        record_rows.append(f"{record_relative},,")
        record_value = ("\n".join(record_rows) + "\n").encode()
        record_path = self.site_packages / record_relative
        record_path.write_bytes(record_value)
        paths[record_relative] = record_path
        entries.append(FakePackagePath(record_relative, file_hash=None, size=None))
        distribution = FakeDistribution(
            name,
            version,
            metadata_root=metadata_root,
            site_packages=self.site_packages,
            files=entries,
        )
        return distribution, paths

    def attest(
        self,
        distributions: Iterable[object],
        *,
        expected_bootstrap: dict[str, str] | None = None,
        identity: object = CPYTHON_312,
        expected_python_version: str = "3.12.9",
        expected_python_sha256: str | None = None,
        interpreter_path: Path | None = None,
    ) -> dict[str, object]:
        return runtime.attest_runtime(
            self.lock,
            system_runtime_attestation=valid_system_attestation(),
            expected_python_version=expected_python_version,
            expected_python_sha256=expected_python_sha256 or self.python_sha256,
            expected_bootstrap=expected_bootstrap,
            distributions=distributions,
            identity=identity,
            runtime_prefix=self.prefix,
            interpreter_path=interpreter_path or self.interpreter,
            expected_runtime_uid=os.getuid(),
            require_virtual_environment=True,
        )

    def test_exact_files_interpreter_and_bound_bootstrap_pass(self) -> None:
        pip, _ = self.install_distribution("pip", "25.1")
        setuptools, _ = self.install_distribution("setuptools", "80.0.0")
        wheel, _ = self.install_distribution("wheel", "0.45.1")
        result = self.attest(
            [wheel, self.beta, pip, self.alpha, setuptools],
            expected_bootstrap={
                "pip": "25.1",
                "setuptools": "80.0.0",
                "wheel": "0.45.1",
            },
        )
        self.assertEqual(result["runtime_attested"], "yes")
        self.assertEqual(result["implementation"], "CPython")
        self.assertEqual(result["python_version"], "3.12.9")
        self.assertEqual(result["python_sha256"], self.python_sha256)
        self.assertEqual(result["lock_package_count"], 2)
        self.assertEqual(result["installed_package_count"], 5)
        self.assertEqual(result["bootstrap_extra_count"], 3)
        self.assertEqual(result["installed_file_count"], 15)
        self.assertEqual(result["runtime_structure_count"], 9)
        self.assertGreater(result["runtime_tree_entry_count"], 20)
        self.assertRegex(str(result["installed_files_sha256"]), r"^[0-9a-f]{64}$")
        self.assertRegex(str(result["runtime_structure_sha256"]), r"^[0-9a-f]{64}$")
        self.assertRegex(str(result["runtime_tree_sha256"]), r"^[0-9a-f]{64}$")
        self.assertRegex(str(result["runtime_sha256"]), r"^[0-9a-f]{64}$")

    def test_runtime_digest_is_deterministic_across_distribution_order(self) -> None:
        first = self.attest([self.alpha, self.beta])
        second = self.attest([self.beta, self.alpha])
        self.assertEqual(first, second)

    def test_unclaimed_runtime_files_of_every_executable_class_are_rejected(self) -> None:
        candidates = (
            (self.site_packages / "unclaimed.pth", b"import forbidden\n", 0o644),
            (self.site_packages / "unclaimed.py", b"raise RuntimeError\n", 0o644),
            (self.site_packages / "unclaimed.so", b"native-placeholder\n", 0o755),
            (self.prefix / "bin/unclaimed-tool", b"#!/bin/sh\nexit 0\n", 0o755),
            (self.prefix / "unmodeled.data", b"opaque\n", 0o644),
        )
        for path, value, mode in candidates:
            with self.subTest(path=path.name):
                try:
                    path.write_bytes(value)
                    path.chmod(mode)
                    expected = (
                        "forbidden startup customization"
                        if path.suffix == ".pth"
                        else "unclaimed or unmodeled"
                    )
                    with self.assertRaisesRegex(runtime.RuntimeAttestationError, expected):
                        self.attest([self.alpha, self.beta])
                finally:
                    path.unlink(missing_ok=True)

    def test_python_bytecode_and_pycache_are_forbidden_even_before_record_matching(self) -> None:
        bytecode = self.site_packages / "claimed.pyc"
        bytecode.write_bytes(b"forbidden-bytecode")
        assert self.alpha.files is not None
        self.alpha.files.append(
            FakePackagePath(
                "claimed.pyc",
                file_hash=_record_hash(bytecode.read_bytes()),
                size=bytecode.stat().st_size,
            )
        )
        with self.subTest("claimed-pyc"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "forbidden Python bytecode"):
                self.attest([self.alpha, self.beta])
        self.alpha.files.pop()
        bytecode.unlink()
        cache = self.site_packages / "empty/__pycache__"
        cache.mkdir(parents=True)
        with self.subTest("empty-pycache"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "forbidden __pycache__"):
                self.attest([self.alpha, self.beta])

    def test_record_claimed_startup_customizations_are_forbidden(self) -> None:
        pth = self.site_packages / "alpha-runtime.pth"
        pth.write_bytes(b"alpha_pkg\n")
        assert self.alpha.files is not None
        self.alpha.files.append(
            FakePackagePath(
                "alpha-runtime.pth",
                file_hash=_record_hash(pth.read_bytes()),
                size=pth.stat().st_size,
            )
        )
        with self.subTest("record-claimed-pth"), self.assertRaisesRegex(
            runtime.RuntimeAttestationError, "forbidden startup customization"
        ):
            self.attest([self.alpha, self.beta])
        self.alpha.files.pop()
        pth.unlink()

        for name in ("sitecustomize.py", "usercustomize.py"):
            customization = self.site_packages / name
            customization.write_bytes(b"raise RuntimeError('must never execute')\n")
            self.alpha.files.append(
                FakePackagePath(
                    name,
                    file_hash=_record_hash(customization.read_bytes()),
                    size=customization.stat().st_size,
                )
            )
            with self.subTest(name), self.assertRaisesRegex(
                runtime.RuntimeAttestationError, "forbidden startup customization"
            ):
                self.attest([self.alpha, self.beta])
            self.alpha.files.pop()
            customization.unlink()

    def test_unmodeled_symlink_is_rejected(self) -> None:
        rogue = self.prefix / "rogue-python"
        rogue.symlink_to(self.base_python)
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "unclaimed or unmodeled"):
            self.attest([self.alpha, self.beta])

    def test_pyvenv_policy_and_launcher_target_drift_are_rejected(self) -> None:
        pyvenv = self.prefix / "pyvenv.cfg"
        original = pyvenv.read_text(encoding="utf-8")
        pyvenv.write_text(
            original.replace("include-system-site-packages = false", "include-system-site-packages = true"),
            encoding="utf-8",
        )
        with self.subTest("system-site-packages"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "enables system site"):
                self.attest([self.alpha, self.beta])
        pyvenv.write_text(original, encoding="utf-8")

        launcher = self.prefix / "bin/python3"
        launcher.unlink()
        other = self.root / "other-python"
        other.write_bytes(b"other executable\n")
        other.chmod(0o755)
        launcher.symlink_to(other)
        with self.subTest("launcher-target"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "unexpected target"):
                self.attest([self.alpha, self.beta])

    def test_structural_file_drift_changes_bound_runtime_digest(self) -> None:
        before = self.attest([self.alpha, self.beta])
        activation = self.prefix / "bin/activate"
        activation.write_text("# reviewed but different activation body\n", encoding="utf-8")
        after = self.attest([self.alpha, self.beta])
        self.assertNotEqual(before["runtime_structure_sha256"], after["runtime_structure_sha256"])
        self.assertNotEqual(before["runtime_sha256"], after["runtime_sha256"])

    def test_real_importlib_distribution_and_venv_script_record_are_supported(self) -> None:
        metadata_root = self.site_packages / "alpha_pkg-1.0.dist-info"
        module = self.site_packages / "alpha_pkg/__init__.py"
        metadata_file = metadata_root / "METADATA"
        metadata_file.write_bytes(b"Metadata-Version: 2.1\nName: Alpha_Pkg\nVersion: 1.0\n")
        script = self.prefix / "bin/alpha-real"
        script.write_bytes(b"#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        rows = []
        for relative, path in (
            ("alpha_pkg/__init__.py", module),
            ("alpha_pkg-1.0.dist-info/METADATA", metadata_file),
            ("../../../bin/alpha-real", script),
        ):
            value = path.read_bytes()
            rows.append(f"{relative},sha256={_record_hash(value).value},{len(value)}")
        rows.append("alpha_pkg-1.0.dist-info/RECORD,,")
        (metadata_root / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
        distributions = list(
            runtime.importlib_metadata.distributions(path=[str(self.site_packages)])
        )
        real_alpha = next(
            distribution
            for distribution in distributions
            if getattr(distribution, "_path", None) == metadata_root
        )
        result = self.attest([real_alpha, self.beta])
        self.assertEqual(result["runtime_attested"], "yes")
        self.assertEqual(result["installed_file_count"], 7)

    def test_same_version_file_tamper_is_rejected_by_record_hash(self) -> None:
        module = self.alpha_paths["alpha_pkg/__init__.py"]
        original = module.read_bytes()
        module.write_bytes(b"X" * len(original))
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "declared RECORD hash"):
            self.attest([self.alpha, self.beta])

    def test_interpreter_hash_drift_is_rejected(self) -> None:
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "interpreter SHA-256"):
            self.attest(
                [self.alpha, self.beta],
                expected_python_sha256="0" * 64,
            )

    def test_resolved_interpreter_must_remain_executable_and_safely_modeled(self) -> None:
        self.base_python.chmod(0o644)
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "not executable"):
            self.attest([self.alpha, self.beta])

    def test_verifier_must_run_through_canonical_venv_python_launcher(self) -> None:
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "venv bin/python"):
            self.attest(
                [self.alpha, self.beta],
                interpreter_path=self.prefix / "bin/python3",
            )

    def test_full_micro_version_drift_is_rejected(self) -> None:
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "full version"):
            self.attest(
                [self.alpha, self.beta],
                identity=runtime.RuntimeIdentity("cpython", 3, 12, 8, True),
            )

    def test_non_cpython_wrong_minor_and_non_venv_are_rejected(self) -> None:
        cases = (
            (runtime.RuntimeIdentity("pypy", 3, 12, 9, True), "CPython implementation"),
            (runtime.RuntimeIdentity("cpython", 3, 11, 9, True), "CPython 3.12 exactly"),
            (runtime.RuntimeIdentity("cpython", 3, 12, 9, False), "virtual environment"),
        )
        for identity, message in cases:
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(runtime.RuntimeAttestationError, message):
                    self.attest([self.alpha, self.beta], identity=identity)

    def test_unbound_bootstrap_package_is_rejected(self) -> None:
        pip, _ = self.install_distribution("pip", "25.1")
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "unbound bootstrap.*pip"):
            self.attest([self.alpha, self.beta, pip])

    def test_bootstrap_version_drift_and_absent_binding_are_rejected(self) -> None:
        pip, _ = self.install_distribution("pip", "25.1")
        with self.subTest("drift"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "bootstrap version drift"):
                self.attest(
                    [self.alpha, self.beta, pip],
                    expected_bootstrap={"pip": "25.0"},
                )
        with self.subTest("absent"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "not installed"):
                self.attest(
                    [self.alpha, self.beta],
                    expected_bootstrap={"pip": "25.1"},
                )

    def test_missing_extra_and_locked_version_drift_are_rejected(self) -> None:
        with self.subTest("missing"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "missing locked.*beta"):
                self.attest([self.alpha])
        gamma, _ = self.install_distribution("gamma", "3.0")
        with self.subTest("extra"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "outside the exact lock.*gamma"):
                self.attest([self.alpha, self.beta, gamma])
        drifted_alpha, _ = self.install_distribution(
            "Alpha_Pkg", "1.0.0", storage_slug="alpha_drift"
        )
        with self.subTest("drift"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "locked version drift"):
                self.attest([drifted_alpha, self.beta])

    def test_duplicate_installed_normalized_name_is_rejected(self) -> None:
        duplicate, _ = self.install_distribution(
            "alpha.pkg", "1.0", storage_slug="alpha_duplicate"
        )
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "duplicate normalized"):
            self.attest([self.alpha, duplicate, self.beta])

    def test_invalid_installed_name_and_version_are_rejected(self) -> None:
        invalid_name, _ = self.install_distribution(
            "invalid package", "1.0", storage_slug="invalid_name"
        )
        with self.subTest("name"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "invalid package name"):
                self.attest([invalid_name, self.beta])
        invalid_version, _ = self.install_distribution(
            "Alpha_Pkg", "not_a_version", storage_slug="invalid_version"
        )
        with self.subTest("version"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "invalid PEP 440"):
                self.attest([invalid_version, self.beta])

    def test_distribution_enumeration_failure_is_safely_wrapped(self) -> None:
        def broken_distributions():
            yield self.alpha
            raise OSError("sensitive metadata location")

        with self.assertRaisesRegex(
            runtime.RuntimeAttestationError, "cannot safely enumerate"
        ) as raised:
            self.attest(broken_distributions())
        self.assertNotIn("sensitive metadata location", str(raised.exception))

    def test_unhashed_non_record_entry_and_bad_record_digest_are_rejected(self) -> None:
        module_entry = next(
            entry for entry in self.alpha.files or [] if str(entry) == "alpha_pkg/__init__.py"
        )
        original_hash = module_entry.hash
        with self.subTest("unhashed"):
            module_entry.hash = None
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "unhashed or unsized"):
                self.attest([self.alpha, self.beta])
        module_entry.hash = FakeFileHash("sha512", "A" * 43)
        with self.subTest("algorithm"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "valid SHA-256"):
                self.attest([self.alpha, self.beta])
        module_entry.hash = original_hash

    def test_symlink_hardlink_and_mutable_distribution_files_are_rejected(self) -> None:
        module = self.alpha_paths["alpha_pkg/__init__.py"]
        original = module.read_bytes()
        replacement = self.site_packages / "alpha_pkg/replacement.py"
        replacement.write_bytes(original)
        module.unlink()
        module.symlink_to(replacement)
        with self.subTest("symlink"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "symlink"):
                self.attest([self.alpha, self.beta])
        module.unlink()
        module.write_bytes(original)
        replacement.unlink()
        hardlink = self.root / "hardlink-copy"
        os.link(module, hardlink)
        with self.subTest("hardlink"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "hard link"):
                self.attest([self.alpha, self.beta])
        hardlink.unlink()
        module.chmod(0o666)
        with self.subTest("mutable"):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "group- or world-writable"):
                self.attest([self.alpha, self.beta])

    def test_record_path_outside_prefix_is_rejected(self) -> None:
        outside_value = self.base_python.read_bytes()
        assert self.alpha.files is not None
        self.alpha.files.append(
            FakePackagePath(
                "../../../../base-python3.12",
                file_hash=_record_hash(outside_value),
                size=len(outside_value),
            )
        )
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "outside its active prefix"):
            self.attest([self.alpha, self.beta])

    def test_record_must_declare_its_own_record_once(self) -> None:
        assert self.alpha.files is not None
        self.alpha.files = [entry for entry in self.alpha.files if entry.name != "RECORD"]
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "exactly its own RECORD"):
            self.attest([self.alpha, self.beta])

    def test_venv_native_elf_dependencies_must_close_into_system_manifest(self) -> None:
        native = self.site_packages / "fixture-native.so"
        native.write_bytes(Path("/usr/bin/true").read_bytes())
        prefix = runtime._resolve_runtime_prefix(
            self.prefix, expected_uid=os.getuid()
        )
        tree = runtime._scan_runtime_tree(prefix, expected_uid=os.getuid())
        system_manifest = json.loads(
            (ROOT / "deploy/writer-witness/python-runtime.json").read_text(
                encoding="utf-8"
            )
        )
        document, digest = runtime._attest_venv_elf_closure(
            tree,
            prefix,
            system_manifest["elf_objects"],
            expected_uid=os.getuid(),
        )
        self.assertEqual(len(document), 1)
        self.assertRegex(digest, r"\A[0-9a-f]{64}\Z")

        missing = runtime.ElfIdentity(
            interpreter=None,
            needed=("lib-not-release-bound.so",),
            soname="fixture-native.so",
        )
        with mock.patch.object(runtime, "_parse_elf_identity", return_value=missing):
            with self.assertRaisesRegex(
                runtime.RuntimeAttestationError, "outside the closed"
            ):
                runtime._attest_venv_elf_closure(
                    tree,
                    prefix,
                    system_manifest["elf_objects"],
                    expected_uid=os.getuid(),
                )


class SystemRuntimeManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(
            prefix="writer-witness-system-runtime-"
        )
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.stdlib = self.root / "stdlib"
        self.stdlib.mkdir()
        self.source = self.stdlib / "trusted_module.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        lib_dynload = self.stdlib / "lib-dynload"
        lib_dynload.mkdir()
        real_extension = next(
            Path(sysconfig_path).resolve(strict=True)
            for sysconfig_path in sorted(
                Path("/usr/lib/python3.12/lib-dynload").glob("*.so")
            )
        )
        self.extension_alias = lib_dynload / "bound-extension.so"
        self.extension_alias.symlink_to(real_extension)
        self.document = runtime.observe_system_runtime_manifest(
            stdlib_path=self.stdlib,
            expected_uid=None,
            require_stdlib_package_ownership=False,
        )
        self.manifest = self.root / "python-runtime.json"
        self.write_manifest(self.document)

    def write_manifest(self, document: object) -> str:
        value = (
            json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        self.manifest.write_bytes(value)
        self.manifest.chmod(0o644)
        return hashlib.sha256(value).hexdigest()

    def attest(self, *, expected_sha256: str | None = None) -> dict[str, object]:
        return runtime.attest_system_runtime(
            self.manifest,
            expected_manifest_sha256=expected_sha256
            or hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            expected_manifest_uid=os.getuid(),
            expected_system_uid=None,
            stdlib_path=self.stdlib,
            require_stdlib_package_ownership=False,
        )

    def test_exact_host_runtime_manifest_passes_and_binds_all_layers(self) -> None:
        result = self.attest()
        self.assertEqual(result["system_runtime_attested"], "yes")
        self.assertGreater(result["system_elf_object_count"], 1)
        self.assertGreater(result["system_package_count"], 1)
        self.assertGreater(result["system_stdlib_entry_count"], 1)
        for field in (
            "system_runtime_manifest_sha256",
            "system_runtime_sha256",
            "system_stdlib_tree_sha256",
            "system_elf_closure_sha256",
            "system_package_set_sha256",
        ):
            self.assertRegex(str(result[field]), r"\A[0-9a-f]{64}\Z")

    def test_release_binding_and_stdlib_byte_tamper_fail_closed(self) -> None:
        original_sha256 = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
        with self.subTest("manifest"), self.assertRaisesRegex(
            runtime.RuntimeAttestationError, "release binding"
        ):
            self.attest(expected_sha256=original_sha256)

        self.write_manifest(self.document)
        self.source.write_text("VALUE = 2\n", encoding="utf-8")
        with self.subTest("stdlib"), self.assertRaisesRegex(
            runtime.RuntimeAttestationError, "identity drifted"
        ):
            self.attest()

    def test_lib_dynload_target_tamper_fails_closed(self) -> None:
        alternatives = sorted(Path("/usr/lib/python3.12/lib-dynload").glob("*.so"))
        current = self.extension_alias.resolve(strict=True)
        replacement = next(path.resolve(strict=True) for path in alternatives if path.resolve() != current)
        self.extension_alias.unlink()
        self.extension_alias.symlink_to(replacement)
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "identity drifted"):
            self.attest()

    def test_shared_library_digest_tamper_fails_closed(self) -> None:
        target = next(
            Path(str(item["path"]))
            for item in self.document["elf_objects"]
            if "/x86_64-linux-gnu/" in str(item["path"])
        )
        real_read = runtime._read_elf_bytes

        def drifted_read(path: Path, *, expected_uid: int | None):
            value, digest = real_read(path, expected_uid=expected_uid)
            return (value, "0" * 64) if path == target else (value, digest)

        with mock.patch.object(runtime, "_read_elf_bytes", side_effect=drifted_read):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "identity drifted"):
                self.attest()

    def test_dpkg_package_version_tamper_fails_closed(self) -> None:
        package = self.document["packages"][0]
        observed = {
            key: dict(value) for key, value in runtime._read_dpkg_status().items()
        }
        key = (str(package["name"]), str(package["architecture"]))
        observed[key]["Version"] = str(observed[key]["Version"]) + "+drift"
        with mock.patch.object(runtime, "_read_dpkg_status", return_value=observed):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "identity drifted"):
                self.attest()

    def test_loader_manifest_excludes_host_global_cache_and_configuration(self) -> None:
        self.assertEqual(
            self.document["loader"],
            {"preload_path": "/etc/ld.so.preload", "preload_sha256": None},
        )
        legacy = json.loads(json.dumps(self.document))
        legacy["loader"]["cache_sha256"] = "0" * 64
        self.write_manifest(legacy)
        with self.assertRaisesRegex(runtime.RuntimeAttestationError, "unsupported schema"):
            self.attest()

    def test_dynamic_loader_preload_remains_fail_closed(self) -> None:
        original_exists = Path.exists

        def preload_present(path: Path) -> bool:
            if path == Path("/etc/ld.so.preload"):
                return True
            return original_exists(path)

        with mock.patch.object(runtime.Path, "exists", new=preload_present):
            with self.assertRaisesRegex(runtime.RuntimeAttestationError, "preload"):
                runtime.observe_system_runtime_manifest(
                    stdlib_path=self.stdlib,
                    expected_uid=None,
                    require_stdlib_package_ownership=False,
                )


class IsolatedCliIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.system_tempdir = tempfile.TemporaryDirectory(
            prefix="writer-witness-system-cli-"
        )
        cls.system_manifest = Path(cls.system_tempdir.name) / "python-runtime.json"
        document = runtime.observe_system_runtime_manifest()
        value = (
            json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        cls.system_manifest.write_bytes(value)
        cls.system_manifest.chmod(0o644)
        cls.system_manifest_sha256 = hashlib.sha256(value).hexdigest()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.system_tempdir.cleanup()

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="writer-witness-isolated-cli-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.prefix = self.root / "venv"
        self.site_packages = self.prefix / "lib/python3.12/site-packages"
        self.site_packages.mkdir(parents=True)
        (self.prefix / "include/python3.12").mkdir(parents=True)
        (self.prefix / "bin").mkdir()
        self.interpreter_target = Path(sys.executable).resolve(strict=True)
        self.python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        self.python_sha256 = hashlib.sha256(self.interpreter_target.read_bytes()).hexdigest()
        (self.prefix / "bin/python3.12").symlink_to(self.interpreter_target)
        (self.prefix / "bin/python3").symlink_to("python3.12")
        (self.prefix / "bin/python").symlink_to("python3.12")
        (self.prefix / "lib64").symlink_to("lib")
        for name in ("Activate.ps1", "activate", "activate.csh", "activate.fish"):
            (self.prefix / "bin" / name).write_text(
                f"# isolated CLI activation fixture: {name}\n", encoding="utf-8"
            )
        (self.prefix / "pyvenv.cfg").write_text(
            "\n".join(
                (
                    f"home = {self.interpreter_target.parent}",
                    "include-system-site-packages = false",
                    f"version = {self.python_version}",
                    f"executable = {self.interpreter_target}",
                    f"command = {self.interpreter_target} -m venv --without-pip {self.prefix}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        package = self.site_packages / "isolated_alpha/__init__.py"
        package.parent.mkdir()
        package.write_bytes(b"__version__ = '1.0'\n")
        metadata_root = self.site_packages / "isolated_alpha-1.0.dist-info"
        metadata_root.mkdir()
        metadata_file = metadata_root / "METADATA"
        metadata_file.write_bytes(
            b"Metadata-Version: 2.1\nName: isolated-alpha\nVersion: 1.0\n"
        )
        rows = []
        for relative, path in (
            ("isolated_alpha/__init__.py", package),
            ("isolated_alpha-1.0.dist-info/METADATA", metadata_file),
        ):
            value = path.read_bytes()
            rows.append(f"{relative},sha256={_record_hash(value).value},{len(value)}")
        rows.append("isolated_alpha-1.0.dist-info/RECORD,,")
        (metadata_root / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.lock = self.root / "requirements.lock"
        self.lock.write_text("isolated-alpha==1.0\n", encoding="utf-8")

    def command(self, *flags: str) -> list[str]:
        return [
            str(self.prefix / "bin/python"),
            *flags,
            str(MODULE_PATH),
            "--runtime-prefix",
            str(self.prefix),
            "--system-runtime-manifest",
            str(self.system_manifest),
            "--expected-system-runtime-manifest-sha256",
            self.system_manifest_sha256,
            "--requirements-lock",
            str(self.lock),
            "--expected-lock-uid",
            "0",
            "--expected-python-version",
            self.python_version,
            "--expected-python-sha256",
            self.python_sha256,
        ]

    def test_real_isolated_no_site_cli_attests_only_explicit_site_packages(self) -> None:
        completed = subprocess.run(
            self.command(
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
            ),
            check=False,
            env={"PATH": runtime.TRUSTED_SYSTEM_PATH},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["runtime_attested"], "yes")
        self.assertEqual(result["installed_package_count"], 1)
        self.assertEqual(result["installed_file_count"], 3)
        self.assertEqual(result["runtime_structure_count"], 9)
        self.assertEqual(completed.stderr, "")

    def test_non_isolated_cli_is_fail_closed(self) -> None:
        completed = subprocess.run(
            self.command("-I"),
            check=False,
            env={"PATH": runtime.TRUSTED_SYSTEM_PATH},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("requires -I -S -B", completed.stderr)

    def test_unclaimed_pth_is_rejected_without_execution_under_safe_cli(self) -> None:
        marker = self.root / "pth-was-executed"
        pth = self.site_packages / "hostile.pth"
        pth.write_text(
            f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            self.command(
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
            ),
            check=False,
            env={"PATH": runtime.TRUSTED_SYSTEM_PATH},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("forbidden startup customization", completed.stderr)
        self.assertFalse(marker.exists())


class BootstrapBindingTests(unittest.TestCase):
    def test_exact_repeatable_bindings_are_normalized(self) -> None:
        self.assertEqual(
            runtime.parse_bootstrap_bindings(
                ["PIP==25.1", "setuptools==80.0.0", "wheel==0.45.1"]
            ),
            {"pip": "25.1", "setuptools": "80.0.0", "wheel": "0.45.1"},
        )

    def test_malformed_duplicate_and_non_allowlisted_bindings_are_rejected(self) -> None:
        cases = (
            (["pip>=25.1"], "exact name==version"),
            (["pip==25.1", "PIP==25.1"], "duplicate normalized"),
            (["fastapi==1.0"], "outside the allowlist"),
        )
        for values, message in cases:
            with self.subTest(values=values):
                with self.assertRaisesRegex(runtime.RuntimeAttestationError, message):
                    runtime.parse_bootstrap_bindings(values)


class CommandLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="writer-witness-runtime-")
        self.addCleanup(self.tempdir.cleanup)
        self.lock = Path(self.tempdir.name) / "requirements.lock"
        self.lock.write_text("alpha==1.0\n", encoding="utf-8")
        self.python_sha256 = "a" * 64
        self.system_manifest = Path(self.tempdir.name) / "python-runtime.json"
        self.system_manifest.write_text("{}\n", encoding="utf-8")
        self.system_manifest_sha256 = "c" * 64

    def system_args(self) -> list[str]:
        return [
            "--system-runtime-manifest",
            str(self.system_manifest),
            "--expected-system-runtime-manifest-sha256",
            self.system_manifest_sha256,
        ]

    def required_args(self) -> list[str]:
        return self.system_args() + [
            "--requirements-lock",
            str(self.lock),
            "--runtime-prefix",
            str(Path(self.tempdir.name) / "venv"),
            "--expected-python-version",
            "3.12.9",
            "--expected-python-sha256",
            self.python_sha256,
        ]

    def test_python_version_and_hash_are_mandatory_cli_bindings(self) -> None:
        cases = (
            self.system_args() + ["--requirements-lock", str(self.lock)],
            self.system_args() + [
                "--requirements-lock",
                str(self.lock),
                "--expected-python-version",
                "3.12.9",
            ],
            self.system_args() + [
                "--requirements-lock",
                str(self.lock),
                "--expected-python-sha256",
                self.python_sha256,
            ],
        )
        for values in cases:
            with self.subTest(values=values), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    runtime.parse_args(values)
                self.assertEqual(raised.exception.code, 2)

    def test_main_passes_bindings_and_emits_deterministic_json(self) -> None:
        result = {
            "runtime_attested": "yes",
            "runtime_sha256": "b" * 64,
            "python_sha256": self.python_sha256,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(runtime, "attest_runtime", return_value=result) as attest,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = runtime.main(
                self.required_args()
                + [
                    "--expected-bootstrap",
                    "pip==25.1",
                    "--expected-bootstrap",
                    "wheel==0.45.1",
                    "--expected-lock-uid",
                    str(os.getuid()),
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            stdout.getvalue(),
            json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n",
        )
        attest.assert_called_once_with(
            self.lock,
            system_runtime_manifest=self.system_manifest,
            expected_system_runtime_manifest_sha256=self.system_manifest_sha256,
            expected_lock_uid=os.getuid(),
            expected_python_version="3.12.9",
            expected_python_sha256=self.python_sha256,
            expected_bootstrap={"pip": "25.1", "wheel": "0.45.1"},
            runtime_prefix=Path(self.tempdir.name) / "venv",
            expected_runtime_uid=0,
            require_isolated_startup=True,
        )

    def test_main_rejects_invalid_bootstrap_without_leaking_paths(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = runtime.main(
                self.required_args() + ["--expected-bootstrap", "secret-package==1.0"]
            )
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("runtime attestation failed", stderr.getvalue())
        self.assertNotIn(str(self.lock.parent), stderr.getvalue())

    def test_cli_rejects_non_exact_version_hash_and_negative_uid(self) -> None:
        cases = (
            self.required_args()[:-3] + ["3.12", "--expected-python-sha256", self.python_sha256],
            self.required_args()[:-1] + ["A" * 64],
            self.required_args() + ["--expected-lock-uid", "-1"],
        )
        for values in cases:
            with self.subTest(values=values), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    runtime.parse_args(values)
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
