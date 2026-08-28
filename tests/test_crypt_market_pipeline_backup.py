from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts import crypt_market_pipeline_backup as crypt


class MarketPipelineBackupCryptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-backup-crypt-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.source = self.root / "source.dump"
        self.key = self.root / "backup.key"
        self.artifact = self.root / "source.dump.enc"
        self.receipt = self.root / "encryption.json"
        self.source.write_bytes((b"postgres-custom-backup\0" * 4096) + b"tail")
        self.source.chmod(0o600)
        self.key.write_text("42" * 32 + "\n", encoding="ascii")
        self.key.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_encrypt_verify_is_idempotent_and_never_writes_offhost_plaintext(self) -> None:
        first = crypt.encrypt(
            source=self.source,
            destination=self.artifact,
            key_file=self.key,
            receipt=self.receipt,
        )
        encrypted = self.artifact.read_bytes()
        self.assertNotIn(b"postgres-custom-backup", encrypted)
        self.assertEqual(first["schema"], "market_pipeline_backup_encryption/1.1")
        self.assertFalse(first["plaintext_materialized_offhost"])
        self.assertNotIn("key_id_sha256", first)
        first_artifact = encrypted
        first_receipt = self.receipt.read_bytes()

        second = crypt.encrypt(
            source=self.source,
            destination=self.artifact,
            key_file=self.key,
            receipt=self.receipt,
        )
        self.assertEqual(second, first)
        self.assertEqual(self.artifact.read_bytes(), first_artifact)
        self.assertEqual(self.receipt.read_bytes(), first_receipt)

    def test_ciphertext_tamper_fails_before_plaintext_acceptance(self) -> None:
        crypt.encrypt(
            source=self.source,
            destination=self.artifact,
            key_file=self.key,
            receipt=self.receipt,
        )
        payload = bytearray(self.artifact.read_bytes())
        payload[len(payload) // 2] ^= 1
        self.artifact.write_bytes(payload)
        self.artifact.chmod(0o600)
        with self.assertRaisesRegex(
            crypt.BackupCryptError, "ciphertext_authentication_failed"
        ):
            crypt.verify(
                artifact=self.artifact,
                key_file=self.key,
                receipt=self.receipt,
            )

    def test_encrypt_recovers_durable_ciphertext_after_receipt_write_crash(self) -> None:
        first = crypt.encrypt(
            source=self.source,
            destination=self.artifact,
            key_file=self.key,
            receipt=self.receipt,
        )
        ciphertext = self.artifact.read_bytes()
        self.receipt.unlink()

        recovered = crypt.encrypt(
            source=self.source,
            destination=self.artifact,
            key_file=self.key,
            receipt=self.receipt,
        )

        self.assertEqual(recovered, first)
        self.assertEqual(self.artifact.read_bytes(), ciphertext)
        self.assertTrue(self.receipt.is_file())

    def test_orphan_ciphertext_with_wrong_plaintext_never_mints_receipt(self) -> None:
        crypt.encrypt(
            source=self.source,
            destination=self.artifact,
            key_file=self.key,
            receipt=self.receipt,
        )
        self.receipt.unlink()
        self.source.write_bytes(b"different-source")
        self.source.chmod(0o600)
        with self.assertRaisesRegex(crypt.BackupCryptError, "partial_output_drift"):
            crypt.encrypt(
                source=self.source,
                destination=self.artifact,
                key_file=self.key,
                receipt=self.receipt,
            )
        self.assertFalse(self.receipt.exists())

    def test_wrong_key_and_receipt_tamper_are_rejected(self) -> None:
        crypt.encrypt(
            source=self.source,
            destination=self.artifact,
            key_file=self.key,
            receipt=self.receipt,
        )
        wrong = self.root / "wrong.key"
        wrong.write_text("24" * 32 + "\n", encoding="ascii")
        wrong.chmod(0o600)
        with self.assertRaises(crypt.BackupCryptError):
            crypt.verify(
                artifact=self.artifact, key_file=wrong, receipt=self.receipt
            )

        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        receipt["plaintext_sha256"] = "0" * 64
        self.receipt.write_text(json.dumps(receipt), encoding="utf-8")
        self.receipt.chmod(0o600)
        with self.assertRaisesRegex(
            crypt.BackupCryptError, "plaintext_reconciliation_failed"
        ):
            crypt.verify(
                artifact=self.artifact, key_file=self.key, receipt=self.receipt
            )

    def test_key_and_parent_permissions_fail_closed(self) -> None:
        self.key.chmod(0o644)
        with self.assertRaisesRegex(crypt.BackupCryptError, "file_security_invalid"):
            crypt.encrypt(
                source=self.source,
                destination=self.artifact,
                key_file=self.key,
                receipt=self.receipt,
            )


if __name__ == "__main__":
    unittest.main()
