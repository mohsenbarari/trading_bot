from __future__ import annotations

import base64
import asyncio
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError

from sqlalchemy.orm import Session

from core.dr_blob_crypto import (
    DrBlobCryptoError,
    DrBlobKeyring,
    decrypt_blob_stream,
    encrypted_object_key,
    encrypt_local_blob,
    load_blob_keyring,
    metadata_for_ciphertext,
)
from core.dr_blob_plane import (
    PENDING_PUBLICATIONS_KEY,
    persist_content_addressed_bytes,
    reconcile_orphaned_local_blobs,
    stage_content_addressed_bytes,
)
from core.dr_blob_worker import S3Config, _head_or_upload


class DrBlobCryptoTests(unittest.TestCase):
    def setUp(self):
        self.key_v1 = bytes(range(32))
        self.key_v2 = bytes(reversed(range(32)))
        self.keyring = DrBlobKeyring(
            active_key_id="staging-v2",
            keys={"staging-v1": self.key_v1, "staging-v2": self.key_v2},
        )
        self.plaintext = (b"DO-NOT-EXPOSE-THIS-USER-FILE\x00" * 4096) + b"tail"
        self.content_hash = hashlib.sha256(self.plaintext).hexdigest()
        self.mime_type = "application/octet-stream"

    def _encrypt(self, directory: str, *, key_id: str = "staging-v1"):
        source_path = Path(directory) / "source"
        source_path.write_bytes(self.plaintext)
        os.chmod(source_path, 0o600)
        object_key = encrypted_object_key(
            self.content_hash,
            prefix="staging/campaign/blobs/encrypted",
            keyring=self.keyring,
            key_id=key_id,
        )
        encrypted, identity, _ = encrypt_local_blob(
            local_path=str(source_path),
            content_hash=self.content_hash,
            size_bytes=len(self.plaintext),
            mime_type=self.mime_type,
            object_key=object_key,
            key_id=key_id,
            keyring=self.keyring,
        )
        ciphertext = encrypted.read()
        encrypted.close()
        return ciphertext, identity

    def _decrypt(self, ciphertext: bytes, identity, *, keyring=None, mime_type=None):
        output = BytesIO()
        decrypt_blob_stream(
            BytesIO(ciphertext),
            ciphertext_size=len(ciphertext),
            expected_ciphertext_hash=hashlib.sha256(ciphertext).hexdigest(),
            content_hash=self.content_hash,
            size_bytes=len(self.plaintext),
            mime_type=mime_type or self.mime_type,
            object_key=identity.object_key,
            key_id=identity.key_id,
            keyring=keyring or self.keyring,
            plaintext_sink=output,
        )
        return output.getvalue()

    def test_keyring_file_is_owner_only_and_supports_rotation(self):
        payload = {
            "schema": "trading-bot-dr-blob-keyring-v1",
            "active_key_id": "staging-v2",
            "keys": {
                "staging-v1": base64.b64encode(self.key_v1).decode(),
                "staging-v2": base64.b64encode(self.key_v2).decode(),
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keyring.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(path, 0o600)
            loaded = load_blob_keyring(path)
            self.assertEqual(loaded.active_key_id, "staging-v2")
            self.assertEqual(loaded.discovery_order(), ("staging-v2", "staging-v1"))
            os.chmod(path, 0o644)
            with self.assertRaises(DrBlobCryptoError):
                load_blob_keyring(path)

    def test_uploaded_object_is_ciphertext_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            ciphertext, identity = self._encrypt(directory)
        self.assertNotIn(b"DO-NOT-EXPOSE-THIS-USER-FILE", ciphertext)
        self.assertNotIn(self.content_hash.encode(), ciphertext)
        self.assertEqual(identity.ciphertext_hash, hashlib.sha256(ciphertext).hexdigest())
        self.assertEqual(
            set(metadata_for_ciphertext(identity)),
            {"ciphertext-sha256", "encryption-key-id", "encryption-format"},
        )
        self.assertEqual(self._decrypt(ciphertext, identity), self.plaintext)

    def test_wrong_key_corruption_truncation_and_swapped_context_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            ciphertext, identity = self._encrypt(directory)
        wrong = DrBlobKeyring(active_key_id="staging-v1", keys={"staging-v1": b"x" * 32})
        with self.assertRaisesRegex(DrBlobCryptoError, "authentication"):
            self._decrypt(ciphertext, identity, keyring=wrong)

        corrupted = bytearray(ciphertext)
        corrupted[len(corrupted) // 2] ^= 1
        with self.assertRaisesRegex(DrBlobCryptoError, "authentication|hash"):
            self._decrypt(bytes(corrupted), identity)
        with self.assertRaisesRegex(DrBlobCryptoError, "truncated|identity"):
            self._decrypt(ciphertext[:-1], identity)
        with self.assertRaisesRegex(DrBlobCryptoError, "authentication"):
            self._decrypt(ciphertext, identity, mime_type="image/png")

    def test_old_ciphertext_remains_decryptable_after_active_key_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            ciphertext, identity = self._encrypt(directory, key_id="staging-v1")
        self.assertEqual(self.keyring.active_key_id, "staging-v2")
        self.assertEqual(self._decrypt(ciphertext, identity), self.plaintext)

    def test_staged_blob_publishes_only_after_root_commit_and_rollback_cleans_it(self):
        with tempfile.TemporaryDirectory() as directory:
            digest, final_path, publication = stage_content_addressed_bytes(
                self.plaintext,
                root=directory,
            )
            self.assertFalse(Path(final_path).exists())
            session = Session()
            session.info.setdefault(PENDING_PUBLICATIONS_KEY, []).append(publication)
            session.commit()
            self.assertEqual(Path(final_path).read_bytes(), self.plaintext)
            self.assertEqual(Path(final_path).name, digest)

            other = b"rollback-content"
            _, rolled_back_path, pending = stage_content_addressed_bytes(other, root=directory)
            session = Session()
            session.begin()
            session.info.setdefault(PENDING_PUBLICATIONS_KEY, []).append(pending)
            session.rollback()
            self.assertFalse(Path(rolled_back_path).exists())
            self.assertFalse(Path(pending["staged_path"]).exists())

    def test_orphan_scanner_quarantines_old_unknown_bytes_with_evidence(self):
        class EmptyResult:
            def all(self):
                return []

        class EmptyDatabase:
            async def execute(self, _statement):
                return EmptyResult()

        with tempfile.TemporaryDirectory() as directory:
            _, orphan_path = persist_content_addressed_bytes(b"orphan", root=directory)
            old = 1_700_000_000
            os.utime(orphan_path, (old, old))
            with patch.multiple(
                "core.dr_blob_plane.settings",
                dr_blob_root=directory,
                dr_blob_orphan_grace_seconds=60,
                dr_blob_orphan_scan_max_entries=100,
                dr_blob_local_quota_bytes=1024 * 1024,
            ):
                changed = asyncio.run(reconcile_orphaned_local_blobs(EmptyDatabase()))
            self.assertEqual(changed, 1)
            self.assertFalse(Path(orphan_path).exists())
            evidence = Path(directory, ".orphan-evidence.jsonl")
            self.assertTrue(evidence.exists())
            self.assertIn("orphan_quarantine_planned", evidence.read_text())
            self.assertIn("untracked_content_addressed_blob", evidence.read_text())
            self.assertEqual(len(list(Path(directory, ".orphan-quarantine").rglob("*-*"))), 1)

    def test_s3_upload_receives_only_ciphertext_and_opaque_metadata(self):
        class FakeS3:
            def __init__(self):
                self.upload = None
                self.head_count = 0

            def head_object(self, **_kwargs):
                self.head_count += 1
                if self.head_count == 1:
                    raise ClientError(
                        {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                        "HeadObject",
                    )
                return {
                    "ContentLength": self.upload["ContentLength"],
                    "Metadata": self.upload["Metadata"],
                    "VersionId": "version-1",
                    "ETag": '"cipher-etag"',
                }

            def put_object(self, **kwargs):
                ciphertext = kwargs["Body"].read()
                self.upload = {**kwargs, "ciphertext": ciphertext}
                return {"VersionId": "version-1"}

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.write_bytes(self.plaintext)
            os.chmod(source, 0o600)
            object_key = encrypted_object_key(
                self.content_hash,
                prefix="staging/campaign/blobs/encrypted",
                keyring=self.keyring,
                key_id=self.keyring.active_key_id,
            )
            manifest = {
                "content_hash": self.content_hash,
                "size_bytes": len(self.plaintext),
                "mime_type": self.mime_type,
                "local_path": str(source),
                "object_key": object_key,
                "encryption_key_id": self.keyring.active_key_id,
                "object_ciphertext_hash": None,
            }
            fake = FakeS3()
            config = S3Config(
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="staging",
                access_key="access-key",
                secret_key="s" * 32,
            )
            with patch("core.dr_blob_worker._client", return_value=fake):
                stored = _head_or_upload(config, manifest, self.keyring)

        self.assertNotIn(self.plaintext[:32], fake.upload["ciphertext"])
        self.assertNotIn(self.content_hash, fake.upload["Key"])
        self.assertNotIn(self.content_hash, repr(fake.upload["Metadata"]))
        self.assertNotIn("ServerSideEncryption", fake.upload)
        self.assertEqual(fake.upload["ContentType"], "application/octet-stream")
        self.assertEqual(stored.identity.ciphertext_hash, fake.upload["Metadata"]["ciphertext-sha256"])


if __name__ == "__main__":
    unittest.main()
