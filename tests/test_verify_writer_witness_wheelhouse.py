from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/verify_writer_witness_wheelhouse.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_writer_witness_wheelhouse", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
wheelhouse = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wheelhouse
SPEC.loader.exec_module(wheelhouse)


class WheelhouseFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="writer-witness-wheelhouse-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.wheels = self.root / "wheels"
        self.wheels.mkdir(mode=0o700)
        self.uid = os.getuid()

    @staticmethod
    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def add_wheel(self, name: str, value: bytes) -> Path:
        path = self.wheels / name
        path.write_bytes(value)
        path.chmod(0o600)
        return path

    def write_manifest(
        self,
        entries: list[tuple[str, str]],
        *,
        inside: bool = True,
        value: bytes | None = None,
    ) -> Path:
        path = (self.wheels if inside else self.root) / "SHA256SUMS"
        if value is None:
            value = b"".join(
                f"{digest}  {name}\n".encode("ascii") for name, digest in entries
            )
        path.write_bytes(value)
        path.chmod(0o600)
        return path

    def valid_fixture(self, *, inside: bool = True) -> tuple[Path, dict[str, str]]:
        values = {
            "alpha_pkg-1.0-py3-none-any.whl": b"alpha wheel bytes",
            "beta-2.0-cp312-cp312-manylinux2014_x86_64.whl": b"beta wheel bytes",
        }
        entries: list[tuple[str, str]] = []
        for name, value in values.items():
            self.add_wheel(name, value)
            entries.append((name, self.digest(value)))
        manifest = self.write_manifest(entries, inside=inside)
        return manifest, {name: self.digest(value) for name, value in values.items()}


class SuccessfulAttestationTests(WheelhouseFixture):
    def test_internal_manifest_attests_exact_inventory(self) -> None:
        manifest, entries = self.valid_fixture()
        result = wheelhouse.attest_wheelhouse(
            self.wheels, manifest, expected_uid=self.uid
        )
        self.assertEqual(result["wheelhouse_attested"], "yes")
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(
            result["manifest_sha256"], hashlib.sha256(manifest.read_bytes()).hexdigest()
        )
        self.assertEqual(result["aggregate_sha256"], wheelhouse._aggregate_digest(entries))

    def test_external_release_bound_manifest_is_supported(self) -> None:
        manifest, _ = self.valid_fixture(inside=False)
        result = wheelhouse.attest_wheelhouse(
            self.wheels, manifest, expected_uid=self.uid
        )
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(
            set(os.listdir(self.wheels)),
            set(wheelhouse._parse_manifest(manifest.read_bytes()).entries),
        )

    def test_aggregate_is_deterministic_across_mapping_order(self) -> None:
        entries = {
            "b-1-py3-none-any.whl": "b" * 64,
            "a-1-py3-none-any.whl": "a" * 64,
        }
        reverse = dict(reversed(list(entries.items())))
        self.assertEqual(
            wheelhouse._aggregate_digest(entries), wheelhouse._aggregate_digest(reverse)
        )

    def test_cli_emits_one_deterministic_json_document(self) -> None:
        manifest, _ = self.valid_fixture()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = wheelhouse.main(
                [
                    "--wheelhouse",
                    str(self.wheels),
                    "--manifest",
                    str(manifest),
                    "--expected-uid",
                    str(self.uid),
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        raw = stdout.getvalue()
        self.assertEqual(raw.count("\n"), 1)
        self.assertEqual(
            raw, json.dumps(json.loads(raw), separators=(",", ":"), sort_keys=True) + "\n"
        )
        self.assertEqual(json.loads(raw)["wheelhouse_attested"], "yes")


class ManifestValidationTests(WheelhouseFixture):
    def assert_manifest_rejected(self, value: bytes, message: str) -> None:
        manifest = self.write_manifest([], value=value)
        with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, message):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid
            )

    def test_manifest_requires_canonical_hash_spacing_and_final_newline(self) -> None:
        name = "package-1-py3-none-any.whl"
        digest = "a" * 64
        cases = (
            (f"{digest.upper()}  {name}\n".encode(), "invalid SHA-256"),
            (f"{digest} {name}\n".encode(), "canonical SHA256SUMS"),
            (f"{digest} *{name}\n".encode(), "canonical SHA256SUMS"),
            (f"{digest}  {name}".encode(), "exactly one final newline"),
            (f"{digest}  {name}\n\n".encode(), "exactly one final newline"),
            (f"{digest}  {name}\r\n".encode(), "canonical UTF-8"),
            (b"\xef\xbb\xbf" + f"{digest}  {name}\n".encode(), "canonical UTF-8"),
        )
        for value, message in cases:
            with self.subTest(value=value):
                self.assert_manifest_rejected(value, message)

    def test_manifest_rejects_unsafe_or_non_ascii_basenames(self) -> None:
        digest = "a" * 64
        names = (
            "../package.whl",
            "nested/package.whl",
            r"nested\package.whl",
            ".hidden.whl",
            "not-a-wheel.txt",
            "bad name.whl",
            "پکیج.whl",
        )
        for name in names:
            with self.subTest(name=name):
                self.assert_manifest_rejected(
                    f"{digest}  {name}\n".encode("utf-8"),
                    "unsafe wheel basename|canonical ASCII/UTF-8",
                )

    def test_manifest_entries_must_be_sorted_and_unique(self) -> None:
        alpha = "alpha-1-py3-none-any.whl"
        beta = "beta-1-py3-none-any.whl"
        digest = "a" * 64
        self.assert_manifest_rejected(
            f"{digest}  {beta}\n{digest}  {alpha}\n".encode(), "not sorted"
        )
        self.assert_manifest_rejected(
            f"{digest}  {alpha}\n{digest}  {alpha}\n".encode(), "duplicate"
        )

    def test_empty_manifest_is_rejected(self) -> None:
        self.assert_manifest_rejected(b"", "empty")


class AdversarialFilesystemTests(WheelhouseFixture):
    def test_missing_wheel_is_rejected(self) -> None:
        manifest = self.write_manifest(
            [("missing-1-py3-none-any.whl", "a" * 64)]
        )
        with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "missing"):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid
            )

    def test_extra_wheel_and_non_wheel_file_are_rejected(self) -> None:
        for extra_name, message in (
            ("extra-1-py3-none-any.whl", "unmanifested wheels"),
            ("notes.txt", "unmanifested non-wheel"),
        ):
            with self.subTest(extra_name=extra_name):
                with tempfile.TemporaryDirectory(
                    prefix="writer-witness-wheelhouse-extra-", dir=self.root
                ) as temporary:
                    directory = Path(temporary)
                    directory.chmod(0o700)
                    wheel = directory / "expected-1-py3-none-any.whl"
                    wheel.write_bytes(b"expected")
                    wheel.chmod(0o600)
                    manifest = directory / "SHA256SUMS"
                    manifest.write_text(
                        f"{self.digest(b'expected')}  {wheel.name}\n", encoding="ascii"
                    )
                    manifest.chmod(0o600)
                    extra = directory / extra_name
                    extra.write_bytes(b"extra")
                    extra.chmod(0o600)
                    with self.assertRaisesRegex(
                        wheelhouse.WheelhouseAttestationError, message
                    ):
                        wheelhouse.attest_wheelhouse(
                            directory, manifest, expected_uid=self.uid
                        )

    def test_hidden_entry_and_subdirectory_are_rejected(self) -> None:
        for kind, message in (("hidden", "hidden"), ("directory", "subdirectory")):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory(
                    prefix="writer-witness-wheelhouse-node-", dir=self.root
                ) as temporary:
                    directory = Path(temporary)
                    directory.chmod(0o700)
                    wheel = directory / "expected-1-py3-none-any.whl"
                    wheel.write_bytes(b"expected")
                    wheel.chmod(0o600)
                    manifest = directory / "SHA256SUMS"
                    manifest.write_text(
                        f"{self.digest(b'expected')}  {wheel.name}\n", encoding="ascii"
                    )
                    manifest.chmod(0o600)
                    if kind == "hidden":
                        (directory / ".residue").write_bytes(b"residue")
                    else:
                        (directory / "nested").mkdir()
                    with self.assertRaisesRegex(
                        wheelhouse.WheelhouseAttestationError, message
                    ):
                        wheelhouse.attest_wheelhouse(
                            directory, manifest, expected_uid=self.uid
                        )

    def test_symlinked_wheel_manifest_and_root_are_rejected(self) -> None:
        with self.subTest("wheel"):
            target = self.root / "target.whl"
            target.write_bytes(b"target")
            name = "linked-1-py3-none-any.whl"
            (self.wheels / name).symlink_to(target)
            manifest = self.write_manifest([(name, self.digest(b"target"))])
            with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "symlink"):
                wheelhouse.attest_wheelhouse(
                    self.wheels, manifest, expected_uid=self.uid
                )

        with self.subTest("manifest"):
            second = self.root / "second"
            second.mkdir(mode=0o700)
            wheel = second / "pkg-1-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            wheel.chmod(0o600)
            real_manifest = self.root / "real-manifest"
            real_manifest.write_text(
                f"{self.digest(b'wheel')}  {wheel.name}\n", encoding="ascii"
            )
            real_manifest.chmod(0o600)
            linked_manifest = second / "SHA256SUMS"
            linked_manifest.symlink_to(real_manifest)
            with self.assertRaisesRegex(
                wheelhouse.WheelhouseAttestationError, "cannot safely open"
            ):
                wheelhouse.attest_wheelhouse(
                    second, linked_manifest, expected_uid=self.uid
                )

        with self.subTest("root"):
            real_root = self.root / "real-root"
            real_root.mkdir(mode=0o700)
            linked_root = self.root / "linked-root"
            linked_root.symlink_to(real_root, target_is_directory=True)
            external_manifest = self.root / "external-manifest"
            external_manifest.write_text(
                f"{'a' * 64}  pkg-1-py3-none-any.whl\n", encoding="ascii"
            )
            external_manifest.chmod(0o600)
            with self.assertRaisesRegex(
                wheelhouse.WheelhouseAttestationError, "cannot safely open wheelhouse"
            ):
                wheelhouse.attest_wheelhouse(
                    linked_root, external_manifest, expected_uid=self.uid
                )

    def test_hardlinked_wheel_is_rejected(self) -> None:
        name = "package-1-py3-none-any.whl"
        wheel = self.add_wheel(name, b"wheel")
        os.link(wheel, self.root / "second-link.whl")
        manifest = self.write_manifest([(name, self.digest(b"wheel"))])
        with self.assertRaisesRegex(
            wheelhouse.WheelhouseAttestationError, "exactly one hard link"
        ):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid
            )

    def test_hardlinked_manifest_is_rejected(self) -> None:
        name = "package-1-py3-none-any.whl"
        self.add_wheel(name, b"wheel")
        manifest = self.write_manifest([(name, self.digest(b"wheel"))])
        os.link(manifest, self.root / "second-manifest-link")
        with self.assertRaisesRegex(
            wheelhouse.WheelhouseAttestationError, "exactly one hard link"
        ):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid
            )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX mkfifo")
    def test_fifo_is_rejected_without_blocking(self) -> None:
        name = "package-1-py3-none-any.whl"
        os.mkfifo(self.wheels / name)
        manifest = self.write_manifest([(name, "a" * 64)])
        with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "special node"):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid
            )

    def test_digest_drift_is_rejected(self) -> None:
        name = "package-1-py3-none-any.whl"
        self.add_wheel(name, b"observed")
        manifest = self.write_manifest([(name, self.digest(b"expected"))])
        with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "digest"):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid
            )

    def test_owner_and_write_permissions_are_enforced(self) -> None:
        manifest, _ = self.valid_fixture()
        with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "unexpected owner"):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid + 1
            )

        manifest.chmod(0o620)
        with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "writable"):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid
            )

        manifest.chmod(0o600)
        wheel = next(self.wheels.glob("*.whl"))
        wheel.chmod(0o602)
        with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "writable"):
            wheelhouse.attest_wheelhouse(
                self.wheels, manifest, expected_uid=self.uid
            )

    def test_negative_expected_uid_is_rejected(self) -> None:
        manifest, _ = self.valid_fixture()
        with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "non-negative"):
            wheelhouse.attest_wheelhouse(self.wheels, manifest, expected_uid=-1)

    def test_metadata_mutation_during_manifest_read_is_rejected(self) -> None:
        name = "package-1-py3-none-any.whl"
        self.add_wheel(name, b"wheel")
        manifest = self.write_manifest([(name, self.digest(b"wheel"))])
        real_fstat = wheelhouse.os.fstat
        manifest_inode = manifest.stat().st_ino
        manifest_fstat_calls = 0

        def changing_fstat(descriptor: int):
            nonlocal manifest_fstat_calls
            observed = real_fstat(descriptor)
            if observed.st_ino == manifest_inode and stat.S_ISREG(observed.st_mode):
                manifest_fstat_calls += 1
                if manifest_fstat_calls == 2:
                    values = list(observed)
                    values[8] += 1
                    return os.stat_result(values)
            return observed

        with mock.patch.object(wheelhouse.os, "fstat", side_effect=changing_fstat):
            with self.assertRaisesRegex(wheelhouse.WheelhouseAttestationError, "changed"):
                wheelhouse.attest_wheelhouse(
                    self.wheels, manifest, expected_uid=self.uid
                )

    def test_operating_system_failure_is_wrapped_without_sensitive_detail(self) -> None:
        manifest, _ = self.valid_fixture()
        with mock.patch.object(
            wheelhouse.os, "listdir", side_effect=OSError("sensitive mount detail")
        ):
            with self.assertRaisesRegex(
                wheelhouse.WheelhouseAttestationError, "cannot safely enumerate"
            ) as raised:
                wheelhouse.attest_wheelhouse(
                    self.wheels, manifest, expected_uid=self.uid
                )
        self.assertNotIn("sensitive mount detail", str(raised.exception))

    def test_cli_failure_does_not_emit_json_or_file_contents(self) -> None:
        secret = b"private wheel contents must not appear"
        name = "package-1-py3-none-any.whl"
        self.add_wheel(name, secret)
        manifest = self.write_manifest([(name, "a" * 64)])
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = wheelhouse.main(
                [
                    "--wheelhouse",
                    str(self.wheels),
                    "--manifest",
                    str(manifest),
                    "--expected-uid",
                    str(self.uid),
                ]
            )
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(secret.decode(), stderr.getvalue())
        self.assertIn("attestation failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
