from __future__ import annotations

import hashlib
import io
import inspect
import os
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from scripts import fetch_three_site_staging_seed as fetch
from scripts.fetch_three_site_staging_seed import (
    SeedFetchError,
    _exclusive_output_lock,
    _fetch_one,
    _identity_recipient,
    _load_secure_json,
    _manifest_recipient_fingerprint,
    _assert_output_binding,
    _object_evidence,
    _open_output_directory,
    _prepare_output,
    _reconcile_output_directory,
    confirmation_phrase,
    execute,
    main,
    build_plan,
)


class _Client:
    def __init__(self, payload: bytes, item: dict):
        self.payload = payload
        self.item = item

    def get_object(self, *, Bucket, Key, VersionId):  # noqa: N803
        metadata = {
            "plaintext-sha256": self.item["plaintext_sha256"],
            "ciphertext-sha256": self.item["ciphertext_sha256"],
            "artifact-kind": self.item["kind"],
        }
        if "publication_intent" in self.item:
            metadata["publication-intent"] = self.item["publication_intent"]
        return {
            "Body": io.BytesIO(self.payload),
            "ContentLength": len(self.payload),
            "VersionId": VersionId,
            "Metadata": metadata,
        }

    def get_object_acl(self, **_kwargs):
        return {
            "Owner": {"ID": "owner-1"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "owner-1"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        }


class _MapClient:
    def __init__(self, rows: dict[str, tuple[bytes, dict]]):
        self.rows = rows
        self.get_calls: list[str] = []

    def get_object(self, *, Bucket, Key, VersionId):  # noqa: N803, ARG002
        payload, item = self.rows[Key]
        self.get_calls.append(Key)
        return {
            "Body": io.BytesIO(payload),
            "ContentLength": len(payload),
            "VersionId": VersionId,
            "Metadata": {
                "plaintext-sha256": item["plaintext_sha256"],
                "ciphertext-sha256": item["ciphertext_sha256"],
                "artifact-kind": item["kind"],
                "publication-intent": item["publication_intent"],
            },
        }

    def get_object_acl(self, **_kwargs):
        return _Client(b"", {}).get_object_acl()


def _decrypt(*, identity_descriptor, encrypted, temporary):  # noqa: ANN001, ARG001
    temporary.write_bytes(encrypted.read_bytes()[3:])
    temporary.chmod(0o600)


def _tar_payload(name: str, payload: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def _seed_rows() -> tuple[dict, dict[str, bytes]]:
    plaintext = {
        "postgres": b"database-seed",
        "uploads": _tar_payload("upload.txt", b"upload-seed"),
        "audit": _tar_payload("audit.jsonl", b'{"event":"seed"}\n'),
    }
    rows = []
    for kind, payload in plaintext.items():
        ciphertext = b"AGE" + payload
        rows.append(
            {
                "kind": kind,
                "object_key": f"fixed/bot_fi/{kind}.age",
                "version_id": f"version-{kind}",
                "plaintext_sha256": hashlib.sha256(payload).hexdigest(),
                "plaintext_bytes": len(payload),
                "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
                "ciphertext_bytes": len(ciphertext),
                "publication_intent": hashlib.sha256(
                    f"intent-{kind}".encode()
                ).hexdigest(),
            }
        )
    recipient = "age1" + "q" * 58
    return (
        {
            "schema": "three-site-staging-seed-manifest-v2",
            "bucket": "staging-bucket",
            "bucket_owner_id": "owner-1",
            "recipient_fingerprints": {
                "bot_fi": hashlib.sha256((recipient + "\n").encode()).hexdigest()
            },
            "objects": rows,
        },
        plaintext,
    )


class FetchThreeSiteStagingSeedTests(unittest.TestCase):
    def test_exact_version_ciphertext_and_plaintext_are_verified(self):
        plain = b"database-seed"
        cipher = b"AGE" + plain
        item = {
            "kind": "postgres",
            "object_key": "staging/campaign/seed/webapp_fi/object.age",
            "version_id": "v1",
            "plaintext_sha256": hashlib.sha256(plain).hexdigest(),
            "plaintext_bytes": len(plain),
            "ciphertext_sha256": hashlib.sha256(cipher).hexdigest(),
            "ciphertext_bytes": len(cipher),
            "publication_intent": "a" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "postgres.custom"
            result = _fetch_one(
                _Client(cipher, item),
                bucket="staging-bucket",
                item=item,
                identity_path=Path(directory) / "identity",
                output=output,
                bucket_owner_id="owner-1",
                decrypt=_decrypt,
            )
            self.assertEqual(output.read_bytes(), plain)
            self.assertEqual(result["plaintext_sha256"], item["plaintext_sha256"])
            self.assertFalse(any(Path(directory).glob(".*.ciphertext")))
            self.assertFalse(any(Path(directory).glob(".*.decrypting")))

    def test_provider_size_mismatch_fails_before_decryption(self):
        plain = b"database-seed"
        cipher = b"AGE" + plain
        item = {
            "kind": "postgres",
            "object_key": "staging/campaign/seed/bot_fi/object.age",
            "version_id": "v1",
            "plaintext_sha256": hashlib.sha256(plain).hexdigest(),
            "plaintext_bytes": len(plain),
            "ciphertext_sha256": hashlib.sha256(cipher).hexdigest(),
            "ciphertext_bytes": len(cipher) + 1,
            "publication_intent": "b" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SeedFetchError, "provider identity"):
                _fetch_one(
                    _Client(cipher, item),
                    bucket="staging-bucket",
                    item=item,
                    identity_path=Path(directory) / "identity",
                    output=Path(directory) / "postgres.custom",
                    bucket_owner_id="owner-1",
                    decrypt=_decrypt,
                )

    def test_witness_plan_has_no_seed_objects(self):
        plan = build_plan(
            campaign_id="11111111-1111-4111-8111-111111111111",
            target_role="witness",
            plan_hash="a" * 64,
            source_role=None,
        )
        self.assertEqual(plan["object_count"], 0)

    def test_v2_target_role_selects_only_its_distinct_recipient(self):
        manifest = {
            "schema": "three-site-staging-seed-manifest-v2",
            "recipient_fingerprints": {
                "webapp_fi": "a" * 64,
                "webapp_ir": "b" * 64,
            },
        }
        self.assertEqual(
            _manifest_recipient_fingerprint(manifest, target_role="webapp_fi"),
            "a" * 64,
        )
        self.assertEqual(
            _manifest_recipient_fingerprint(manifest, target_role="webapp_ir"),
            "b" * 64,
        )
        with self.assertRaisesRegex(SeedFetchError, "not an authorized recipient"):
            _manifest_recipient_fingerprint(manifest, target_role="bot_fi")
        with self.assertRaisesRegex(SeedFetchError, "only the sealed v2"):
            _manifest_recipient_fingerprint(
                {
                    "schema": "three-site-staging-seed-manifest-v1",
                    "recipient_fingerprint": "a" * 64,
                },
                target_role="bot_fi",
            )

    def test_v2_publication_intent_metadata_is_exact(self):
        plain = b"database-seed"
        cipher = b"AGE" + plain
        item = {
            "kind": "postgres",
            "object_key": "staging/campaign/seed-v2/bot_fi/postgres.age",
            "version_id": "v1",
            "plaintext_sha256": hashlib.sha256(plain).hexdigest(),
            "plaintext_bytes": len(plain),
            "ciphertext_sha256": hashlib.sha256(cipher).hexdigest(),
            "ciphertext_bytes": len(cipher),
            "publication_intent": "c" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            result = _fetch_one(
                _Client(cipher, item),
                bucket="staging-bucket",
                item=item,
                identity_path=Path(directory) / "identity",
                output=Path(directory) / "postgres.custom",
                bucket_owner_id="owner-1",
                decrypt=_decrypt,
            )
        self.assertEqual(result["ciphertext_sha256"], item["ciphertext_sha256"])

    def test_recipient_is_derived_from_exactly_one_private_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / "identity.agekey"
            identity.write_text(
                "# created: test\nAGE-SECRET-KEY-TEST123\n",
                encoding="ascii",
            )
            identity.chmod(0o600)
            recipient, fingerprint = _identity_recipient(
                identity,
                derive=lambda _path: "age1" + "q" * 58,
            )
            self.assertEqual(recipient, "age1" + "q" * 58)
            self.assertEqual(
                fingerprint,
                hashlib.sha256((recipient + "\n").encode()).hexdigest(),
            )
            identity.write_text(
                "AGE-SECRET-KEY-ONE\nAGE-SECRET-KEY-TWO\n",
                encoding="ascii",
            )
            identity.chmod(0o600)
            with self.assertRaisesRegex(SeedFetchError, "exactly one"):
                _identity_recipient(
                    identity,
                    derive=lambda _path: "age1" + "q" * 58,
                )

    def test_identity_path_swap_is_detected_before_any_decrypt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / "identity.agekey"
            identity.write_text("AGE-SECRET-KEY-ORIGINAL\n", encoding="ascii")
            identity.chmod(0o600)
            replacement = root / "replacement.agekey"
            replacement.write_text("AGE-SECRET-KEY-REPLACEMENT\n", encoding="ascii")
            replacement.chmod(0o600)

            def swap_path(_path):
                identity.rename(root / "original.agekey")
                replacement.rename(identity)
                return "age1" + "q" * 58

            with self.assertRaisesRegex(SeedFetchError, "changed while pinned"):
                _identity_recipient(identity, derive=swap_path)

    def test_failed_decrypt_leaves_no_ciphertext_or_plaintext_residue(self):
        plain = b"database-seed"
        cipher = b"AGE" + plain
        item = {
            "kind": "postgres",
            "object_key": "staging/campaign/seed-v2/bot_fi/postgres.age",
            "version_id": "v1",
            "plaintext_sha256": hashlib.sha256(plain).hexdigest(),
            "plaintext_bytes": len(plain),
            "ciphertext_sha256": hashlib.sha256(cipher).hexdigest(),
            "ciphertext_bytes": len(cipher),
            "publication_intent": "d" * 64,
        }

        def bad_decrypt(*, identity_descriptor, encrypted, temporary):  # noqa: ANN001, ARG001
            temporary.write_bytes(b"wrong")
            temporary.chmod(0o600)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(SeedFetchError, "decrypted target seed"):
                _fetch_one(
                    _Client(cipher, item),
                    bucket="staging-bucket",
                    item=item,
                    identity_path=root / "identity",
                    output=root / "postgres.custom",
                    bucket_owner_id="owner-1",
                    decrypt=bad_decrypt,
                )
            self.assertEqual(list(root.iterdir()), [])

    def test_production_cli_has_no_repo_or_public_recipient_override(self):
        source = inspect.getsource(main)
        self.assertNotIn('"--repo"', source)
        self.assertNotIn('"--recipient"', source)

    def test_output_directory_must_remain_root_owned_mode_0700_and_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "seed"
            _prepare_output(output)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            (output / "foreign").write_text("x", encoding="ascii")
            with self.assertRaisesRegex(SeedFetchError, "empty root-owned"):
                _prepare_output(output)

    def test_output_rejects_unsafe_ancestors_and_detects_path_swap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe_parent = root / "unsafe-parent"
            unsafe_parent.mkdir(mode=0o700)
            unsafe_parent.chmod(0o777)
            with self.assertRaisesRegex(SeedFetchError, "empty root-owned"):
                _prepare_output(unsafe_parent / "seed")

            real_parent = root / "real-parent"
            real_parent.mkdir(mode=0o700)
            alias = root / "output-alias"
            alias.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(SeedFetchError, "empty root-owned"):
                _prepare_output(alias / "seed")

            output = root / "pinned-output"
            moved = root / "moved-output"
            descriptor = _open_output_directory(output)
            try:
                output.rename(moved)
                output.symlink_to(moved, target_is_directory=True)
                with self.assertRaisesRegex(SeedFetchError, "path changed"):
                    _assert_output_binding(output, descriptor)
            finally:
                os.close(descriptor)

    def test_output_reconciliation_purges_only_owned_temps_and_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "seed"
            descriptor = _open_output_directory(output, require_empty=False)
            try:
                stale = output / (
                    ".postgres.custom." + "a" * 32 + ".decrypting"
                )
                stale.write_bytes(b"root-only-plaintext")
                stale.chmod(0o600)
                _reconcile_output_directory(
                    output,
                    descriptor,
                    allowed_final_names={"postgres.custom", "target-seed.json"},
                )
                self.assertFalse(stale.exists())
                for canonical_name, suffix, payload in (
                    ("postgres.custom", "decrypting", b"published-plaintext"),
                    ("target-seed.json", "writing", b'{"published":true}\n'),
                ):
                    temporary = output / (
                        f".{canonical_name}." + "b" * 32 + f".{suffix}"
                    )
                    temporary.write_bytes(payload)
                    temporary.chmod(0o600)
                    canonical = output / canonical_name
                    os.link(temporary, canonical)
                    self.assertEqual(temporary.stat().st_nlink, 2)
                    _reconcile_output_directory(
                        output,
                        descriptor,
                        allowed_final_names={
                            "postgres.custom",
                            "target-seed.json",
                        },
                    )
                    self.assertFalse(temporary.exists())
                    self.assertEqual(canonical.read_bytes(), payload)
                with _exclusive_output_lock(output, descriptor):
                    with self.assertRaisesRegex(SeedFetchError, "already running"):
                        with _exclusive_output_lock(output, descriptor):
                            pass
                foreign = output / "foreign"
                foreign.write_text("x", encoding="ascii")
                foreign.chmod(0o600)
                with self.assertRaisesRegex(SeedFetchError, "foreign path"):
                    _reconcile_output_directory(
                        output,
                        descriptor,
                        allowed_final_names={"postgres.custom", "target-seed.json"},
                    )
            finally:
                os.close(descriptor)

    def test_restart_resumes_exact_one_two_or_three_published_artifacts(self):
        manifest, plaintext = _seed_rows()
        recipient = "age1" + "q" * 58
        verified = {
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": "a" * 40,
            "plan_sha256": "b" * 64,
        }
        inventory = {
            "object_storage": {"credential_id": "staging-seed-publisher"}
        }
        ordered = sorted(manifest["objects"], key=lambda row: row["kind"])
        for existing_count in (1, 2, 3):
            with self.subTest(existing_count=existing_count), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "seed"
                output.mkdir(mode=0o700)
                identity = root / "identity.agekey"
                identity.write_text("AGE-SECRET-KEY-TEST123\n", encoding="ascii")
                identity.chmod(0o600)
                for item in ordered[:existing_count]:
                    target = output / fetch.ARTIFACT_FILENAME[item["kind"]]
                    target.write_bytes(plaintext[item["kind"]])
                    target.chmod(0o600)
                    _object_evidence(item, target)
                rows = {
                    item["object_key"]: (
                        b"AGE" + plaintext[item["kind"]],
                        item,
                    )
                    for item in ordered
                }
                client = _MapClient(rows)
                seen_identity_inodes: list[int] = []

                def decrypt_with_pinned_identity(
                    *,
                    identity_descriptor,
                    encrypted,
                    temporary,
                ):
                    seen_identity_inodes.append(
                        os.fstat(identity_descriptor).st_ino
                    )
                    _decrypt(
                        identity_descriptor=identity_descriptor,
                        encrypted=encrypted,
                        temporary=temporary,
                    )

                args = SimpleNamespace(
                    target_role="bot_fi",
                    output_dir=output,
                    identity=identity,
                    credentials=root / "credentials.json",
                    confirm=confirmation_phrase(
                        verified["campaign_id"],
                        "bot_fi",
                        verified["plan_sha256"],
                    ),
                )
                with patch.object(fetch, "_verify_exact_release"), patch.object(
                    fetch,
                    "_credentials",
                    return_value=("access", "secret"),
                ), patch.object(
                    fetch,
                    "_require_private_versioned_bucket",
                    return_value="owner-1",
                ):
                    first = execute(
                        args,
                        verified_plan=verified,
                        inventory=inventory,
                        seed_manifests={"bot_fi": manifest},
                        derive_recipient=lambda _path: recipient,
                        client_factory=lambda _access, _secret: client,
                        decrypt=decrypt_with_pinned_identity,
                    )
                    calls_after_first = list(client.get_calls)
                    second = execute(
                        args,
                        verified_plan=verified,
                        inventory=inventory,
                        seed_manifests={"bot_fi": manifest},
                        derive_recipient=lambda _path: recipient,
                        client_factory=lambda _access, _secret: client,
                        decrypt=decrypt_with_pinned_identity,
                    )
                self.assertEqual(
                    len(calls_after_first),
                    3 - existing_count,
                )
                self.assertEqual(client.get_calls, calls_after_first)
                self.assertEqual(first["evidence_sha256"], second["evidence_sha256"])
                self.assertEqual(len(set(seen_identity_inodes)), min(1, len(seen_identity_inodes)))
                self.assertTrue((output / "target-seed.json").is_file())
                self.assertEqual(
                    {
                        path.name
                        for path in output.iterdir()
                        if path.name != fetch.OUTPUT_LOCK_NAME
                    },
                    {
                        "postgres.custom",
                        "uploads.tar.gz",
                        "audit.tar.gz",
                        "target-seed.json",
                    },
                )

    def test_security_sensitive_json_requires_root_mode_0600_and_no_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = root / "value.json"
            value.write_text('{"schema":"test"}\n', encoding="utf-8")
            value.chmod(0o644)
            with self.assertRaisesRegex(SeedFetchError, "mode-0600"):
                _load_secure_json(value, label="test input")
            value.chmod(0o600)
            self.assertEqual(
                _load_secure_json(value, label="test input"),
                {"schema": "test"},
            )
            alias = root / "alias.json"
            alias.symlink_to(value)
            with self.assertRaisesRegex(SeedFetchError, "unavailable"):
                _load_secure_json(alias, label="test input")


if __name__ == "__main__":
    unittest.main()
