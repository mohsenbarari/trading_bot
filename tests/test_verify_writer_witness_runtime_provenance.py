from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/verify_writer_witness_runtime_provenance.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_writer_witness_runtime_provenance", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
provenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provenance
SPEC.loader.exec_module(provenance)


RELEASE_SHA256 = "a" * 64
WHEELHOUSE_SHA256 = "b" * 64
REQUIREMENTS_SHA256 = "c" * 64
PYTHON_SHA256 = "d" * 64
PYTHON_VERSION = "3.12.3"
SYSTEM_MANIFEST_SHA256 = "9" * 64
HOST_TOOLCHAIN_SHA256 = "0" * 64


def valid_runtime() -> dict[str, object]:
    return {
        "bootstrap_extra_count": 0,
        "implementation": "CPython",
        "installed_file_count": 30,
        "installed_files_sha256": "e" * 64,
        "installed_package_count": 6,
        "lock_package_count": 6,
        "python_sha256": PYTHON_SHA256,
        "python_version": PYTHON_VERSION,
        "requirements_lock_sha256": REQUIREMENTS_SHA256,
        "runtime_attested": "yes",
        "runtime_sha256": "f" * 64,
        "runtime_structure_count": 8,
        "runtime_structure_sha256": "1" * 64,
        "runtime_tree_entry_count": 50,
        "runtime_tree_sha256": "2" * 64,
        "system_elf_closure_sha256": "3" * 64,
        "system_elf_object_count": 20,
        "system_os_release_sha256": "4" * 64,
        "system_package_count": 10,
        "system_package_set_sha256": "5" * 64,
        "system_runtime_attested": "yes",
        "system_runtime_manifest_sha256": SYSTEM_MANIFEST_SHA256,
        "system_runtime_sha256": "6" * 64,
        "system_stdlib_entry_count": 100,
        "system_stdlib_tree_sha256": "7" * 64,
        "venv_elf_closure_sha256": "8" * 64,
        "venv_elf_object_count": 12,
    }


def valid_provenance(runtime: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "host_toolchain_inventory_sha256": HOST_TOOLCHAIN_SHA256,
        "release_manifest_sha256": RELEASE_SHA256,
        "requirements_lock_sha256": REQUIREMENTS_SHA256,
        "runtime": runtime if runtime is not None else valid_runtime(),
        "schema_version": provenance.PROVENANCE_SCHEMA_VERSION,
        "system_runtime_manifest_sha256": SYSTEM_MANIFEST_SHA256,
        "wheelhouse_manifest_sha256": WHEELHOUSE_SHA256,
    }


class RuntimeProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(
            prefix="writer-witness-runtime-provenance-"
        )
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.path = self.root / "runtime-provenance.json"
        self.write_document(valid_provenance())

    def write_raw(self, value: bytes, *, mode: int = 0o644) -> None:
        self.path.write_bytes(value)
        self.path.chmod(mode)

    def write_document(self, value: object, *, mode: int = 0o644) -> None:
        self.write_raw(
            (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode(),
            mode=mode,
        )

    def attest(
        self,
        *,
        runtime: dict[str, object] | None = None,
        expected_release: str = RELEASE_SHA256,
        expected_wheelhouse: str = WHEELHOUSE_SHA256,
        expected_requirements: str = REQUIREMENTS_SHA256,
        expected_python_version: str = PYTHON_VERSION,
        expected_python_sha256: str = PYTHON_SHA256,
        expected_system_manifest: str = SYSTEM_MANIFEST_SHA256,
        expected_host_toolchain: str = HOST_TOOLCHAIN_SHA256,
        expected_uid: int | None = None,
        expected_gid: int | None = None,
    ) -> dict[str, object]:
        return provenance.attest_runtime_provenance(
            self.path,
            json.dumps(runtime if runtime is not None else valid_runtime()),
            expected_release_manifest_sha256=expected_release,
            expected_wheelhouse_manifest_sha256=expected_wheelhouse,
            expected_requirements_lock_sha256=expected_requirements,
            expected_python_version=expected_python_version,
            expected_python_sha256=expected_python_sha256,
            expected_system_runtime_manifest_sha256=expected_system_manifest,
            expected_host_toolchain_inventory_sha256=expected_host_toolchain,
            expected_uid=os.getuid() if expected_uid is None else expected_uid,
            expected_gid=os.getgid() if expected_gid is None else expected_gid,
        )

    def test_exact_provenance_and_fresh_runtime_pass(self) -> None:
        result = self.attest()
        self.assertEqual(result["runtime_provenance_attested"], "yes")
        self.assertEqual(result["runtime_sha256"], "f" * 64)
        self.assertEqual(result["installed_files_sha256"], "e" * 64)
        self.assertEqual(result["installed_package_count"], 6)
        self.assertEqual(result["installed_file_count"], 30)
        self.assertEqual(result["runtime_structure_sha256"], "1" * 64)
        self.assertEqual(result["runtime_tree_sha256"], "2" * 64)
        self.assertEqual(result["system_runtime_manifest_sha256"], SYSTEM_MANIFEST_SHA256)
        self.assertEqual(result["system_runtime_sha256"], "6" * 64)
        self.assertEqual(result["system_stdlib_tree_sha256"], "7" * 64)
        self.assertEqual(result["venv_elf_closure_sha256"], "8" * 64)
        self.assertEqual(result["venv_elf_object_count"], 12)
        self.assertEqual(
            result["provenance_sha256"],
            hashlib.sha256(self.path.read_bytes()).hexdigest(),
        )

    def test_fresh_runtime_must_equal_stored_runtime_completely(self) -> None:
        fresh = valid_runtime()
        fresh["runtime_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError,
            "differs from fresh runtime",
        ):
            self.attest(runtime=fresh)

    def test_all_release_bindings_are_exact(self) -> None:
        cases = (
            (
                "host_toolchain_inventory_sha256",
                HOST_TOOLCHAIN_SHA256,
                "8" * 64,
            ),
            ("release_manifest_sha256", RELEASE_SHA256, "0" * 64),
            ("wheelhouse_manifest_sha256", WHEELHOUSE_SHA256, "1" * 64),
            ("requirements_lock_sha256", REQUIREMENTS_SHA256, "2" * 64),
            (
                "system_runtime_manifest_sha256",
                SYSTEM_MANIFEST_SHA256,
                "3" * 64,
            ),
        )
        for field, original, drifted in cases:
            with self.subTest(field=field):
                document = valid_provenance()
                document[field] = drifted
                self.write_document(document)
                kwargs = {
                    "expected_release": RELEASE_SHA256,
                    "expected_wheelhouse": WHEELHOUSE_SHA256,
                    "expected_requirements": REQUIREMENTS_SHA256,
                    "expected_system_manifest": SYSTEM_MANIFEST_SHA256,
                    "expected_host_toolchain": HOST_TOOLCHAIN_SHA256,
                }
                with self.assertRaisesRegex(
                    provenance.RuntimeProvenanceAttestationError,
                    "differs from its release binding",
                ):
                    self.attest(**kwargs)
                document[field] = original
                self.write_document(document)

    def test_python_and_requirements_runtime_bindings_are_exact(self) -> None:
        cases = (
            ("python_version", "3.12.4", "Python version"),
            ("python_sha256", "0" * 64, "Python SHA-256"),
            ("requirements_lock_sha256", "0" * 64, "requirements SHA-256"),
            (
                "system_runtime_manifest_sha256",
                "0" * 64,
                "system runtime manifest SHA-256",
            ),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                runtime = valid_runtime()
                runtime[field] = value
                self.write_document(valid_provenance(runtime))
                with self.assertRaisesRegex(
                    provenance.RuntimeProvenanceAttestationError, message
                ):
                    self.attest(runtime=runtime)

    def test_runtime_attested_implementation_and_bootstrap_are_strict(self) -> None:
        cases = (
            ("runtime_attested", "no", "runtime_attested"),
            ("system_runtime_attested", "no", "system_runtime_attested"),
            ("implementation", "PyPy", "implementation"),
            ("bootstrap_extra_count", 1, "bootstrap_extra_count"),
            ("bootstrap_extra_count", False, "bootstrap_extra_count"),
            ("bootstrap_extra_count", 0.0, "bootstrap_extra_count"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                runtime = valid_runtime()
                runtime[field] = value
                self.write_document(valid_provenance(runtime))
                with self.assertRaisesRegex(
                    provenance.RuntimeProvenanceAttestationError, message
                ):
                    self.attest(runtime=runtime)

    def test_hashes_must_be_lowercase_sha256(self) -> None:
        for field in (
            "runtime_sha256",
            "installed_files_sha256",
            "runtime_structure_sha256",
            "runtime_tree_sha256",
            "system_elf_closure_sha256",
            "system_os_release_sha256",
            "system_package_set_sha256",
            "system_runtime_manifest_sha256",
            "system_runtime_sha256",
            "system_stdlib_tree_sha256",
            "venv_elf_closure_sha256",
        ):
            for value in ("a" * 63, "A" * 64, 4):
                with self.subTest(field=field, value=value):
                    runtime = valid_runtime()
                    runtime[field] = value
                    self.write_document(valid_provenance(runtime))
                    with self.assertRaisesRegex(
                        provenance.RuntimeProvenanceAttestationError,
                        "64 lowercase hexadecimal",
                    ):
                        self.attest(runtime=runtime)

    def test_counts_are_positive_non_boolean_and_consistent(self) -> None:
        cases = (
            ("lock_package_count", 0, "positive integer"),
            ("installed_package_count", True, "positive integer"),
            ("installed_file_count", -1, "positive integer"),
            ("installed_package_count", 5, "package counts"),
            ("installed_file_count", 5, "file count"),
            ("runtime_structure_count", 0, "positive integer"),
            ("runtime_tree_entry_count", 30, "closed inventory"),
            ("system_elf_object_count", 0, "positive integer"),
            ("system_package_count", True, "positive integer"),
            ("system_stdlib_entry_count", -1, "positive integer"),
            ("venv_elf_object_count", 0, "positive integer"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                runtime = valid_runtime()
                runtime[field] = value
                self.write_document(valid_provenance(runtime))
                with self.assertRaisesRegex(
                    provenance.RuntimeProvenanceAttestationError, message
                ):
                    self.attest(runtime=runtime)

    def test_provenance_and_runtime_schema_reject_missing_and_extra_fields(self) -> None:
        document = valid_provenance()
        del document["schema_version"]
        self.write_document(document)
        with self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "schema fields differ"
        ):
            self.attest()

        document = valid_provenance()
        document["unexpected"] = True
        self.write_document(document)
        with self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "schema fields differ"
        ):
            self.attest()

        for mutation in ("missing", "extra"):
            runtime = valid_runtime()
            if mutation == "missing":
                del runtime["runtime_sha256"]
            else:
                runtime["unexpected"] = "value"
            self.write_document(valid_provenance(runtime))
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                provenance.RuntimeProvenanceAttestationError, "schema fields differ"
            ):
                self.attest(runtime=runtime)

    def test_schema_version_and_runtime_object_type_are_strict(self) -> None:
        document = valid_provenance()
        document["schema_version"] = "writer_witness_runtime_provenance_v4"
        self.write_document(document)
        with self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "unsupported"
        ):
            self.attest()
        document["schema_version"] = provenance.PROVENANCE_SCHEMA_VERSION
        document["runtime"] = []
        self.write_document(document)
        with self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "must be an object"
        ):
            self.attest()

    def test_duplicate_keys_are_rejected_in_stored_and_fresh_json(self) -> None:
        raw = self.path.read_text(encoding="utf-8").rstrip()
        self.write_raw((raw[:-1] + ',"schema_version":"duplicate"}\n').encode())
        with self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "duplicate object key"
        ):
            self.attest()

        self.write_document(valid_provenance())
        fresh = json.dumps(valid_runtime())
        fresh = fresh[:-1] + ',"runtime_attested":"yes"}'
        with self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "duplicate object key"
        ):
            provenance.attest_runtime_provenance(
                self.path,
                fresh,
                expected_release_manifest_sha256=RELEASE_SHA256,
                expected_wheelhouse_manifest_sha256=WHEELHOUSE_SHA256,
                expected_requirements_lock_sha256=REQUIREMENTS_SHA256,
                expected_python_version=PYTHON_VERSION,
                expected_python_sha256=PYTHON_SHA256,
                expected_system_runtime_manifest_sha256=SYSTEM_MANIFEST_SHA256,
                expected_host_toolchain_inventory_sha256=HOST_TOOLCHAIN_SHA256,
                expected_uid=os.getuid(),
                expected_gid=os.getgid(),
            )

    def test_invalid_json_bom_nonfinite_and_non_object_are_rejected(self) -> None:
        cases = (
            (b"not-json\n", "valid bounded JSON"),
            (b"\xef\xbb\xbf{}\n", "BOM"),
            (b'{"value":NaN}\n', "non-finite"),
            (b"[]\n", "root must be an object"),
            (b"\xff\n", "valid UTF-8"),
        )
        for value, message in cases:
            with self.subTest(message=message):
                self.write_raw(value)
                with self.assertRaisesRegex(
                    provenance.RuntimeProvenanceAttestationError, message
                ):
                    self.attest()

    def test_secure_file_rejects_symlink_hardlink_special_mode_and_owner(self) -> None:
        real = self.path
        symlink = self.root / "linked-provenance.json"
        symlink.symlink_to(real)
        original = self.path
        self.path = symlink
        with self.subTest("symlink"), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "cannot safely open"
        ):
            self.attest()
        self.path = original

        hardlink = self.root / "hardlink-provenance.json"
        os.link(real, hardlink)
        with self.subTest("hardlink"), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "exactly one hard link"
        ):
            self.attest()
        hardlink.unlink()

        real.chmod(0o600)
        with self.subTest("mode"), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "exactly 0644"
        ):
            self.attest()
        real.chmod(0o644)

        with self.subTest("uid"), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "unexpected owner"
        ):
            self.attest(expected_uid=os.getuid() + 1)
        with self.subTest("gid"), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "unexpected owner"
        ):
            self.attest(expected_gid=os.getgid() + 1)

        self.path = self.root
        with self.subTest("directory"), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "not a regular file"
        ):
            self.attest()
        if hasattr(os, "mkfifo"):
            fifo = self.root / "provenance.fifo"
            os.mkfifo(fifo)
            self.path = fifo
            with self.subTest("fifo"), self.assertRaisesRegex(
                provenance.RuntimeProvenanceAttestationError, "not a regular file"
            ):
                self.attest()

    def test_empty_oversized_and_changed_files_are_rejected(self) -> None:
        self.write_raw(b"")
        with self.subTest("empty"), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "empty"
        ):
            self.attest()

        self.write_raw(b"x" * (provenance.MAXIMUM_JSON_BYTES + 1))
        with self.subTest("oversized"), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "safe size limit"
        ):
            self.attest()

        self.write_document(valid_provenance())
        real_fstat = provenance.os.fstat
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

        with self.subTest("changed"), mock.patch.object(
            provenance.os, "fstat", side_effect=changing_fstat
        ), self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "changed during"
        ):
            self.attest()

    def test_fresh_json_is_bounded_and_strict(self) -> None:
        with self.assertRaisesRegex(
            provenance.RuntimeProvenanceAttestationError, "safe size limit"
        ):
            provenance.attest_runtime_provenance(
                self.path,
                "x" * (provenance.MAXIMUM_JSON_BYTES + 1),
                expected_release_manifest_sha256=RELEASE_SHA256,
                expected_wheelhouse_manifest_sha256=WHEELHOUSE_SHA256,
                expected_requirements_lock_sha256=REQUIREMENTS_SHA256,
                expected_python_version=PYTHON_VERSION,
                expected_python_sha256=PYTHON_SHA256,
                expected_system_runtime_manifest_sha256=SYSTEM_MANIFEST_SHA256,
                expected_host_toolchain_inventory_sha256=HOST_TOOLCHAIN_SHA256,
                expected_uid=os.getuid(),
                expected_gid=os.getgid(),
            )

    def test_read_failures_are_wrapped_without_leaking_paths(self) -> None:
        with mock.patch.object(
            provenance.os, "read", side_effect=OSError("secret path detail")
        ):
            with self.assertRaisesRegex(
                provenance.RuntimeProvenanceAttestationError,
                "cannot safely read runtime provenance",
            ) as raised:
                self.attest()
        self.assertNotIn("secret path detail", str(raised.exception))


class CommandLineTests(unittest.TestCase):
    def required_args(self) -> list[str]:
        return [
            "--provenance",
            "/opt/trading-bot-witness/active/runtime-provenance.json",
            "--runtime-attestation-json",
            json.dumps(valid_runtime()),
            "--expected-release-manifest-sha256",
            RELEASE_SHA256,
            "--expected-wheelhouse-manifest-sha256",
            WHEELHOUSE_SHA256,
            "--expected-requirements-lock-sha256",
            REQUIREMENTS_SHA256,
            "--expected-python-version",
            PYTHON_VERSION,
            "--expected-python-sha256",
            PYTHON_SHA256,
            "--expected-system-runtime-manifest-sha256",
            SYSTEM_MANIFEST_SHA256,
            "--expected-host-toolchain-inventory-sha256",
            HOST_TOOLCHAIN_SHA256,
        ]

    def test_default_production_owner_is_root_and_json_is_deterministic(self) -> None:
        expected = {
            "runtime_provenance_attested": "yes",
            "runtime_sha256": "f" * 64,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                provenance, "attest_runtime_provenance", return_value=expected
            ) as attest,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = provenance.main(self.required_args())
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            stdout.getvalue(),
            json.dumps(expected, separators=(",", ":"), sort_keys=True) + "\n",
        )
        positional, keyword = attest.call_args
        self.assertEqual(positional[0], Path(self.required_args()[1]))
        self.assertEqual(keyword["expected_uid"], 0)
        self.assertEqual(keyword["expected_gid"], 0)
        self.assertEqual(
            keyword["expected_system_runtime_manifest_sha256"],
            SYSTEM_MANIFEST_SHA256,
        )
        self.assertEqual(
            keyword["expected_host_toolchain_inventory_sha256"],
            HOST_TOOLCHAIN_SHA256,
        )

    def test_cli_accepts_explicit_owner_and_reports_attestation_failure(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(
                provenance,
                "attest_runtime_provenance",
                side_effect=provenance.RuntimeProvenanceAttestationError("drift"),
            ) as attest,
            redirect_stdout(io.StringIO()),
            redirect_stderr(stderr),
        ):
            status = provenance.main(
                self.required_args()
                + ["--expected-uid", "123", "--expected-gid", "456"]
            )
        self.assertEqual(status, 1)
        self.assertIn("runtime provenance attestation failed: drift", stderr.getvalue())
        self.assertEqual(attest.call_args.kwargs["expected_uid"], 123)
        self.assertEqual(attest.call_args.kwargs["expected_gid"], 456)

    def test_cli_rejects_malformed_hash_version_and_owner(self) -> None:
        cases = (
            self.required_args()[:-1] + ["A" * 64],
            self.required_args()[:-3]
            + ["3.12", "--expected-python-sha256", PYTHON_SHA256],
            self.required_args() + ["--expected-uid", "-1"],
            self.required_args() + ["--expected-gid", "not-a-number"],
        )
        for values in cases:
            with self.subTest(values=values[-3:]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    provenance.parse_args(values)
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
