"""Focused contract tests for the local, direction-bound age-v1 adapters."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core.physical_age_v1_adapter import (
    PhysicalAgeV1AdapterError,
    PhysicalAgeV1Decryptor,
    PhysicalAgeV1DecryptorConfig,
    PhysicalAgeV1Encryptor,
    PhysicalAgeV1EncryptorConfig,
    PhysicalAgeV1FdDecryptor,
)


RECIPIENT = "age1" + ("q" * 58)
OTHER_RECIPIENT = "age1" + ("p" * 58)
AGE_HEADER = b"age-encryption.org/v1\n"


class PhysicalAgeV1AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        os.chmod(self.root, 0o700)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(mode=0o700)
        os.chmod(self.workspace, 0o700)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _private_file(self, name: str, contents: bytes, *, mode: int = 0o600) -> Path:
        path = self.root / name
        path.write_bytes(contents)
        os.chmod(path, mode)
        return path

    @staticmethod
    def _fake_run_factory(*, keygen_recipient: str = RECIPIENT, plaintext: bytes = b"plaintext"):
        commands: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            copied = list(command)
            commands.append((copied, dict(kwargs)))
            if copied[0].endswith("age-keygen"):
                return subprocess.CompletedProcess(copied, 0, stdout=(keygen_recipient + "\n").encode("ascii"))
            output = Path(copied[copied.index("-o") + 1])
            if "--decrypt" in copied:
                output.write_bytes(plaintext)
            else:
                output.write_bytes(AGE_HEADER + b"test-ciphertext")
            os.chmod(output, 0o600)
            return subprocess.CompletedProcess(copied, 0)

        return commands, fake_run

    @staticmethod
    def _fake_binary(path: Path, *, code: str) -> Path:
        return path

    def _encryptor(self, **overrides: object) -> PhysicalAgeV1Encryptor:
        values: dict[str, object] = {
            "workspace_root": self.workspace,
            "recipient": RECIPIENT,
            "enabled": True,
            "maximum_plaintext_bytes": 1024,
            "maximum_ciphertext_bytes": 2048,
        }
        values.update(overrides)
        return PhysicalAgeV1Encryptor(PhysicalAgeV1EncryptorConfig(**values))

    def _decryptor(self, **overrides: object) -> PhysicalAgeV1Decryptor:
        identity = self._private_file("identity.txt", b"AGE-SECRET-KEY-1TEST\n")
        values: dict[str, object] = {
            "workspace_root": self.workspace,
            "identity_path": identity,
            "recipient": RECIPIENT,
            "enabled": True,
            "maximum_plaintext_bytes": 1024,
            "maximum_ciphertext_bytes": 2048,
        }
        values.update(overrides)
        return PhysicalAgeV1Decryptor(PhysicalAgeV1DecryptorConfig(**values))

    def test_encrypts_only_the_pinned_recipient_from_a_private_snapshot(self) -> None:
        plaintext = self._private_file("payload.bin", b"source payload")
        output = self.root / "payload.age"
        commands, fake_run = self._fake_run_factory()
        with (
            patch("core.physical_age_v1_adapter._require_root_controlled_executable", self._fake_binary),
            patch("core.physical_age_v1_adapter.subprocess.run", fake_run),
        ):
            self._encryptor().encrypt(
                recipient=RECIPIENT,
                plaintext_path=plaintext,
                ciphertext_path=output,
            )

        self.assertEqual(output.read_bytes(), AGE_HEADER + b"test-ciphertext")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(len(commands), 1)
        command, kwargs = commands[0]
        self.assertEqual(command[1:3], ["-r", RECIPIENT])
        self.assertNotEqual(Path(command[-1]), plaintext)
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LC_ALL": "C"})

    def test_encrypt_refuses_recipient_drift_before_invoking_a_process(self) -> None:
        plaintext = self._private_file("payload.bin", b"source payload")
        output = self.root / "payload.age"
        commands, fake_run = self._fake_run_factory()
        with patch("core.physical_age_v1_adapter.subprocess.run", fake_run):
            with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_ENCRYPTOR_RECIPIENT_MISMATCH"):
                self._encryptor().encrypt(
                    recipient=OTHER_RECIPIENT,
                    plaintext_path=plaintext,
                    ciphertext_path=output,
                )
        self.assertEqual(commands, [])
        self.assertFalse(output.exists())

    def test_encrypt_is_default_disabled(self) -> None:
        plaintext = self._private_file("payload.bin", b"source payload")
        output = self.root / "payload.age"
        with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_ENCRYPTOR_DISABLED"):
            PhysicalAgeV1Encryptor(
                PhysicalAgeV1EncryptorConfig(
                    workspace_root=self.workspace,
                    recipient=RECIPIENT,
                )
            ).encrypt(
                recipient=RECIPIENT,
                plaintext_path=plaintext,
                ciphertext_path=output,
            )
        self.assertFalse(output.exists())

    def test_encrypt_rejects_unsafe_plaintext_and_existing_destination(self) -> None:
        unsafe_plaintext = self._private_file("unsafe.bin", b"source payload", mode=0o644)
        output = self.root / "payload.age"
        with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_ENCRYPTOR_PLAINTEXT_UNSAFE"):
            self._encryptor().encrypt(
                recipient=RECIPIENT,
                plaintext_path=unsafe_plaintext,
                ciphertext_path=output,
            )

        plaintext = self._private_file("payload.bin", b"source payload")
        output.write_bytes(b"existing")
        os.chmod(output, 0o600)
        with self.assertRaisesRegex(
            PhysicalAgeV1AdapterError,
            "AGE_ENCRYPTOR_CIPHERTEXT_DESTINATION_UNSAFE",
        ):
            self._encryptor().encrypt(
                recipient=RECIPIENT,
                plaintext_path=plaintext,
                ciphertext_path=output,
            )

    def test_encrypt_rejects_non_age_output_without_leaving_destination(self) -> None:
        plaintext = self._private_file("payload.bin", b"source payload")
        output = self.root / "payload.age"

        def bad_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            generated = Path(command[command.index("-o") + 1])
            generated.write_bytes(b"not an age ciphertext")
            os.chmod(generated, 0o600)
            return subprocess.CompletedProcess(command, 0)

        with (
            patch("core.physical_age_v1_adapter._require_root_controlled_executable", self._fake_binary),
            patch("core.physical_age_v1_adapter.subprocess.run", bad_run),
        ):
            with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_ENCRYPTOR_CIPHERTEXT_UNSAFE"):
                self._encryptor().encrypt(
                    recipient=RECIPIENT,
                    plaintext_path=plaintext,
                    ciphertext_path=output,
                )
        self.assertFalse(output.exists())

    def test_decrypt_verifies_identity_recipient_then_uses_private_staging(self) -> None:
        ciphertext = self._private_file("payload.age", AGE_HEADER + b"ciphertext")
        output = self.root / "payload.bin"
        commands, fake_run = self._fake_run_factory(plaintext=b"restored payload")
        with (
            patch("core.physical_age_v1_adapter._require_root_controlled_executable", self._fake_binary),
            patch("core.physical_age_v1_adapter.subprocess.run", fake_run),
        ):
            self._decryptor().decrypt(
                expected_recipient=RECIPIENT,
                ciphertext_path=ciphertext,
                plaintext_path=output,
            )

        self.assertEqual(output.read_bytes(), b"restored payload")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][0][1], "-y")
        self.assertNotEqual(Path(commands[0][0][-1]), self.root / "identity.txt")
        self.assertIn("--decrypt", commands[1][0])
        self.assertIn("-i", commands[1][0])
        self.assertNotEqual(
            Path(commands[1][0][commands[1][0].index("-i") + 1]),
            self.root / "identity.txt",
        )
        self.assertNotEqual(Path(commands[1][0][-1]), ciphertext)

    def test_decrypt_rejects_identity_recipient_mismatch_before_decrypt(self) -> None:
        ciphertext = self._private_file("payload.age", AGE_HEADER + b"ciphertext")
        output = self.root / "payload.bin"
        commands, fake_run = self._fake_run_factory(keygen_recipient=OTHER_RECIPIENT)
        with (
            patch("core.physical_age_v1_adapter._require_root_controlled_executable", self._fake_binary),
            patch("core.physical_age_v1_adapter.subprocess.run", fake_run),
        ):
            with self.assertRaisesRegex(
                PhysicalAgeV1AdapterError,
                "AGE_DECRYPTOR_IDENTITY_RECIPIENT_MISMATCH",
            ):
                self._decryptor().decrypt(
                    expected_recipient=RECIPIENT,
                    ciphertext_path=ciphertext,
                    plaintext_path=output,
                )
        self.assertEqual(len(commands), 1)
        self.assertFalse(output.exists())

    def test_decrypt_rejects_expected_recipient_drift_without_opening_identity(self) -> None:
        ciphertext = self._private_file("payload.age", AGE_HEADER + b"ciphertext")
        output = self.root / "payload.bin"
        commands, fake_run = self._fake_run_factory()
        with patch("core.physical_age_v1_adapter.subprocess.run", fake_run):
            with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_DECRYPTOR_RECIPIENT_MISMATCH"):
                self._decryptor().decrypt(
                    expected_recipient=OTHER_RECIPIENT,
                    ciphertext_path=ciphertext,
                    plaintext_path=output,
                )
        self.assertEqual(commands, [])

    def test_decrypt_rejects_non_age_ciphertext_before_decrypt(self) -> None:
        ciphertext = self._private_file("payload.age", b"not-age")
        output = self.root / "payload.bin"
        commands, fake_run = self._fake_run_factory()
        with (
            patch("core.physical_age_v1_adapter._require_root_controlled_executable", self._fake_binary),
            patch("core.physical_age_v1_adapter.subprocess.run", fake_run),
        ):
            with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_DECRYPTOR_CIPHERTEXT_UNSAFE"):
                self._decryptor().decrypt(
                    expected_recipient=RECIPIENT,
                    ciphertext_path=ciphertext,
                    plaintext_path=output,
                )
        # Malformed ciphertext is rejected before either identity derivation
        # or a decrypt command can be reached.
        self.assertEqual(len(commands), 0)
        self.assertFalse(output.exists())

    def test_rejects_boolean_limits_and_invalid_route_policy(self) -> None:
        plaintext = self._private_file("payload.bin", b"source payload")
        output = self.root / "payload.age"
        with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_ENCRYPTOR_PLAINTEXT_BOUND_INVALID"):
            self._encryptor(maximum_plaintext_bytes=True).encrypt(
                recipient=RECIPIENT,
                plaintext_path=plaintext,
                ciphertext_path=output,
            )
        with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_ENCRYPTOR_ROUTE_POLICY_INVALID"):
            self._encryptor(direct_site_control="allowed").encrypt(
                recipient=RECIPIENT,
                plaintext_path=plaintext,
                ciphertext_path=output,
            )

    def test_fd_decryptor_is_directly_compatible_with_wal_receiver_staging(self) -> None:
        identity = self._private_file("fd-identity.txt", b"AGE-SECRET-KEY-1TEST\n")
        ciphertext = self._private_file("wal.age", AGE_HEADER + b"ciphertext", mode=0o400)
        destination = self.root / "wal.plain"
        ciphertext_fd = os.open(ciphertext, os.O_RDONLY)
        destination_fd = os.open(destination, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(destination_fd, 0o600)
        commands, fake_run = self._fake_run_factory(plaintext=b"wal-segment-bytes")
        adapter = PhysicalAgeV1FdDecryptor(
            PhysicalAgeV1DecryptorConfig(
                workspace_root=self.workspace,
                identity_path=identity,
                recipient=RECIPIENT,
                enabled=True,
                maximum_plaintext_bytes=1024,
                maximum_ciphertext_bytes=2048,
            )
        )
        try:
            with (
                patch("core.physical_age_v1_adapter._require_root_controlled_executable", self._fake_binary),
                patch("core.physical_age_v1_adapter.subprocess.run", fake_run),
            ):
                receipt = adapter.decrypt_to_fd(
                    ciphertext_fd=ciphertext_fd,
                    destination_fd=destination_fd,
                    object_key="physical/wal/000000010000000000000001.age",
                    version_id="version-0001",
                    expected_age_recipient=RECIPIENT,
                )
            self.assertEqual(receipt.object_key, "physical/wal/000000010000000000000001.age")
            self.assertEqual(receipt.version_id, "version-0001")
            self.assertEqual(receipt.age_recipient, RECIPIENT)
            self.assertEqual(receipt.plaintext_bytes, len(b"wal-segment-bytes"))
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            os.lseek(destination_fd, 0, os.SEEK_SET)
            self.assertEqual(os.read(destination_fd, 1024), b"wal-segment-bytes")
            self.assertEqual(os.lseek(ciphertext_fd, 0, os.SEEK_CUR), 0)
            self.assertEqual(len(commands), 2)
            self.assertNotEqual(Path(commands[1][0][-1]), ciphertext)
        finally:
            os.close(ciphertext_fd)
            os.close(destination_fd)

    def test_fd_decryptor_rejects_bad_ciphertext_or_nonempty_destination_before_decrypt(self) -> None:
        identity = self._private_file("fd-identity.txt", b"AGE-SECRET-KEY-1TEST\n")
        bad_ciphertext = self._private_file("bad.age", b"not-age", mode=0o400)
        destination = self.root / "wal.plain"
        ciphertext_fd = os.open(bad_ciphertext, os.O_RDONLY)
        destination_fd = os.open(destination, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(destination_fd, 0o600)
        commands, fake_run = self._fake_run_factory()
        adapter = PhysicalAgeV1FdDecryptor(
            PhysicalAgeV1DecryptorConfig(
                workspace_root=self.workspace,
                identity_path=identity,
                recipient=RECIPIENT,
                enabled=True,
                maximum_plaintext_bytes=1024,
                maximum_ciphertext_bytes=2048,
            )
        )
        try:
            with patch("core.physical_age_v1_adapter.subprocess.run", fake_run):
                with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_FD_DECRYPTOR_CIPHERTEXT_UNSAFE"):
                    adapter.decrypt_to_fd(
                        ciphertext_fd=ciphertext_fd,
                        destination_fd=destination_fd,
                        object_key="physical/wal/000000010000000000000001.age",
                        version_id="version-0001",
                        expected_age_recipient=RECIPIENT,
                    )
            self.assertEqual(commands, [])
        finally:
            os.close(ciphertext_fd)
            os.close(destination_fd)

        ciphertext = self._private_file("good.age", AGE_HEADER + b"ciphertext", mode=0o400)
        ciphertext_fd = os.open(ciphertext, os.O_RDONLY)
        nonempty = self.root / "nonempty.plain"
        nonempty_fd = os.open(nonempty, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.fchmod(nonempty_fd, 0o600)
            os.write(nonempty_fd, b"already-there")
            os.lseek(nonempty_fd, 0, os.SEEK_SET)
            with patch("core.physical_age_v1_adapter.subprocess.run", fake_run):
                with self.assertRaisesRegex(PhysicalAgeV1AdapterError, "AGE_FD_DECRYPTOR_DESTINATION_UNSAFE"):
                    adapter.decrypt_to_fd(
                        ciphertext_fd=ciphertext_fd,
                        destination_fd=nonempty_fd,
                        object_key="physical/wal/000000010000000000000001.age",
                        version_id="version-0001",
                        expected_age_recipient=RECIPIENT,
                    )
            self.assertEqual(commands, [])
        finally:
            os.close(ciphertext_fd)
            os.close(nonempty_fd)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
