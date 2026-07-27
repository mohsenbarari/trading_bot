from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from scripts import receive_wa_ir_production_artifact as MODULE
from scripts.wa_ir_production_object_storage_transport import (
    ArvanCredentials,
    build_client,
)
from scripts.wa_ir_production_transport_contract import ARVAN_ENDPOINT, ARVAN_REGION


NOW = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)
OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
ARTIFACT_KIND = "release-bundle"
DESTINATION_NAME = "release.bundle"
VERSION_ID = "version-1"
PLAINTEXT = b"verified production artifact"
CIPHERTEXT = b"age ciphertext fixture"


class FakeResponse:
    def __init__(
        self,
        url: str,
        payload: bytes,
        *,
        version_id: str = VERSION_ID,
        content_length: int | None = None,
        status: int = 200,
        final_url: str | None = None,
    ) -> None:
        self.status = status
        self._url = url if final_url is None else final_url
        self._body = io.BytesIO(payload)
        self.headers = {
            "Content-Length": str(
                len(payload) if content_length is None else content_length
            ),
            "Content-Encoding": "identity",
            "x-amz-version-id": version_id,
        }

    def read(self, size: int) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self._body.close()


def descriptor_document(
    *,
    plaintext: bytes = PLAINTEXT,
    ciphertext: bytes = CIPHERTEXT,
    version_id: str = VERSION_ID,
    issued_at: datetime = NOW,
    ttl: int = 300,
) -> dict[str, object]:
    ciphertext_sha256 = hashlib.sha256(ciphertext).hexdigest()
    object_key = (
        f"dark-standby/production-receive/{OPERATION_ID}/{ARTIFACT_KIND}/"
        f"{'1' * 32}-{ciphertext_sha256}.age"
    )
    query = urlencode(
        {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": (
                f"access-key/{issued_at:%Y%m%d}/"
                "ir-thr-at1/s3/aws4_request"
            ),
            "X-Amz-Date": issued_at.strftime("%Y%m%dT%H%M%SZ"),
            "X-Amz-Expires": str(ttl),
            "X-Amz-SignedHeaders": "host",
            "X-Amz-Signature": "a" * 64,
            "versionId": version_id,
        }
    )
    return {
        "schema": MODULE.DESCRIPTOR_SCHEMA,
        "operation_id": OPERATION_ID,
        "artifact_kind": ARTIFACT_KIND,
        "destination_name": DESTINATION_NAME,
        "bucket": MODULE.PRODUCTION_BUCKET,
        "object_key": object_key,
        "version_id": version_id,
        "url": (
            f"https://{MODULE.ARVAN_HOST}/{MODULE.PRODUCTION_BUCKET}/"
            f"{object_key}?{query}"
        ),
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": len(ciphertext),
        "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
        "plaintext_bytes": len(plaintext),
    }


def encoded(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


class ProductionArtifactReceiverTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path]:
        operations = root / "operations"
        operations.mkdir(mode=0o700, exist_ok=True)
        identity = root / "identity.txt"
        if not identity.exists():
            identity.write_text(
                "AGE-SECRET-KEY-1TESTONLYTESTONLYTESTONLYTESTONLY\n",
                encoding="utf-8",
            )
            identity.chmod(0o600)
        return operations, identity

    def receive(
        self,
        root: Path,
        *,
        document: dict[str, object] | None = None,
        response: FakeResponse | None = None,
        decrypted: bytes = PLAINTEXT,
        operations: Path | None = None,
        identity: Path | None = None,
        decrypt_writer=None,  # noqa: ANN001
    ) -> tuple[dict[str, object], Path]:
        default_operations, default_identity = self.fixture(root)
        operations = operations or default_operations
        identity = identity or default_identity
        document = document or descriptor_document()
        descriptor = MODULE.parse_descriptor(encoded(document), now=NOW)
        response = response or FakeResponse(
            str(document["url"]),
            CIPHERTEXT,
        )

        def fake_age(arguments, **kwargs):  # noqa: ANN001, ANN202
            self.assertNotIn(str(document["url"]), arguments)
            self.assertEqual(kwargs["env"], MODULE._SAFE_AGE_ENV)
            self.assertEqual(arguments[arguments.index("--identity") + 1][:14], "/proc/self/fd/")
            output = Path(arguments[arguments.index("--output") + 1])
            if decrypt_writer is None:
                output.write_bytes(decrypted)
                output.chmod(0o600)
            else:
                decrypt_writer(output)
            return subprocess.CompletedProcess(arguments, 0)

        with (
            patch.object(MODULE, "_open_url", return_value=response),
            patch.object(MODULE.subprocess, "run", side_effect=fake_age),
        ):
            attestation = MODULE.receive_one(
                descriptor,
                operations_root=operations,
                identity_file=identity,
                required_uid=os.geteuid(),
            )
        destination = (
            operations / OPERATION_ID / "incoming" / DESTINATION_NAME
        )
        return attestation, destination

    def test_exact_artifact_is_installed_create_only_with_nonsecret_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            document = descriptor_document()
            attestation, destination = self.receive(root, document=document)

            self.assertEqual(destination.read_bytes(), PLAINTEXT)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(destination.stat().st_nlink, 1)
            durable = json.dumps(attestation, sort_keys=True)
            self.assertNotIn(str(document["url"]), durable)
            self.assertFalse(attestation["presigned_url_persisted"])
            self.assertFalse(attestation["presigned_url_logged"])
            self.assertFalse(attestation["archive_extracted"])
            self.assertFalse(attestation["docker_image_loaded"])
            self.assertFalse(attestation["compose_started"])
            self.assertEqual(attestation["installation_result"], "created")
            self.assertEqual(
                sorted(path.name for path in destination.parent.iterdir()),
                [DESTINATION_NAME],
            )

    def test_exact_retry_is_idempotent_after_lost_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first, destination = self.receive(root)
            second, repeated_destination = self.receive(root)
            self.assertEqual(first["installation_result"], "created")
            self.assertEqual(second["installation_result"], "already-present")
            self.assertEqual(repeated_destination, destination)
            self.assertEqual(destination.read_bytes(), PLAINTEXT)
            self.assertEqual(destination.stat().st_nlink, 1)

    def test_receiver_import_does_not_require_controller_boto3(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                (
                    "import runpy;"
                    "runpy.run_path("
                    "'scripts/receive_wa_ir_production_artifact.py',"
                    "run_name='receiver_import_test')"
                ),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_rejects_descriptor_from_a_regular_stdin_file(self) -> None:
        with tempfile.TemporaryFile() as descriptor_file:
            descriptor_file.write(encoded(descriptor_document()))
            descriptor_file.seek(0)
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/receive_wa_ir_production_artifact.py",
                ],
                cwd=Path(__file__).resolve().parents[1],
                stdin=descriptor_file,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "blocked")
        self.assertIn("ephemeral stdin", payload["error"])

    @unittest.skipUnless(
        shutil.which("age") and shutil.which("age-keygen"),
        "age CLI is unavailable",
    )
    def test_real_age_cli_round_trip_stays_local_and_installs_exact_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            operations = root / "operations"
            operations.mkdir(mode=0o700)
            identity = root / "identity.txt"
            subprocess.run(
                ["age-keygen", "--output", str(identity)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            identity.chmod(0o600)
            recipient = subprocess.run(
                ["age-keygen", "-y", str(identity)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            plaintext_path = root / "plaintext"
            plaintext_path.write_bytes(PLAINTEXT)
            plaintext_path.chmod(0o600)
            ciphertext_path = root / "ciphertext.age"
            subprocess.run(
                [
                    "age",
                    "--encrypt",
                    "--recipient",
                    recipient,
                    "--output",
                    str(ciphertext_path),
                    str(plaintext_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ciphertext = ciphertext_path.read_bytes()
            document = descriptor_document(ciphertext=ciphertext)
            descriptor = MODULE.parse_descriptor(encoded(document), now=NOW)
            with patch.object(
                MODULE,
                "_open_url",
                return_value=FakeResponse(str(document["url"]), ciphertext),
            ):
                attestation = MODULE.receive_one(
                    descriptor,
                    operations_root=operations,
                    identity_file=identity,
                    required_uid=os.geteuid(),
                )
            destination = (
                operations / OPERATION_ID / "incoming" / DESTINATION_NAME
            )
            self.assertEqual(destination.read_bytes(), PLAINTEXT)
            self.assertEqual(attestation["status"], "installed")

    def test_url_scheme_host_path_version_and_time_are_pinned(self) -> None:
        mutations = {
            "http": lambda url: url.replace("https://", "http://", 1),
            "host": lambda url: url.replace(
                MODULE.ARVAN_HOST, "attacker.invalid", 1
            ),
            "path": lambda url: url.replace(
                f"/{MODULE.PRODUCTION_BUCKET}/", "/other-bucket/", 1
            ),
            "version": lambda url: url.replace(
                "versionId=version-1", "versionId=wrong", 1
            ),
            "ttl": lambda url: url.replace(
                "X-Amz-Expires=300", "X-Amz-Expires=3600", 1
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                document = descriptor_document()
                document["url"] = mutate(str(document["url"]))
                with self.assertRaises(MODULE.ProductionReceiveError) as captured:
                    MODULE.parse_descriptor(encoded(document), now=NOW)
                self.assertNotIn(str(document["url"]), str(captured.exception))

        expired = descriptor_document(issued_at=NOW - timedelta(hours=1))
        with self.assertRaisesRegex(
            MODULE.ProductionReceiveError,
            "expired",
        ):
            MODULE.parse_descriptor(encoded(expired), now=NOW)

    def test_duplicate_descriptor_key_is_rejected(self) -> None:
        document = descriptor_document()
        raw = encoded(document)
        duplicate = raw[:-1] + b',"schema":"' + MODULE.DESCRIPTOR_SCHEMA.encode() + b'"}'
        with self.assertRaisesRegex(
            MODULE.ProductionReceiveError,
            "not valid JSON",
        ):
            MODULE.parse_descriptor(duplicate, now=NOW)

    def test_transport_core_generated_presign_is_receiver_compatible(self) -> None:
        document = descriptor_document()
        client = build_client(
            ArvanCredentials(
                "access-key",
                "s" * 40,
                ARVAN_ENDPOINT,
                ARVAN_REGION,
            )
        )
        document["url"] = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": document["bucket"],
                "Key": document["object_key"],
                "VersionId": document["version_id"],
            },
            ExpiresIn=300,
        )
        parsed = MODULE.parse_descriptor(
            encoded(document),
            now=datetime.now(timezone.utc),
        )
        self.assertEqual(parsed.object_key, document["object_key"])

    def test_ciphertext_hash_size_and_response_version_fail_closed(self) -> None:
        cases = (
            (
                "hash",
                lambda document: FakeResponse(
                    str(document["url"]),
                    b"X" * len(CIPHERTEXT),
                ),
            ),
            (
                "size",
                lambda document: FakeResponse(
                    str(document["url"]),
                    CIPHERTEXT,
                    content_length=len(CIPHERTEXT) + 1,
                ),
            ),
            (
                "version",
                lambda document: FakeResponse(
                    str(document["url"]),
                    CIPHERTEXT,
                    version_id="wrong-version",
                ),
            ),
        )
        for label, response_factory in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                operations, identity = self.fixture(root)
                document = descriptor_document()
                descriptor = MODULE.parse_descriptor(encoded(document), now=NOW)
                with patch.object(
                    MODULE,
                    "_open_url",
                    return_value=response_factory(document),
                ):
                    with self.assertRaises(MODULE.ProductionReceiveError):
                        MODULE.receive_one(
                            descriptor,
                            operations_root=operations,
                            identity_file=identity,
                            required_uid=os.geteuid(),
                        )
                self.assertEqual(list(operations.iterdir()), [])

    def test_plaintext_hash_and_size_fail_without_residue(self) -> None:
        for decrypted in (b"wrong", PLAINTEXT + b"x"):
            with (
                self.subTest(decrypted=decrypted),
                tempfile.TemporaryDirectory() as raw,
            ):
                root = Path(raw)
                with self.assertRaisesRegex(
                    MODULE.ProductionReceiveError,
                    "plaintext hash or size",
                ):
                    self.receive(root, decrypted=decrypted)
                incoming = root / "operations" / OPERATION_ID / "incoming"
                self.assertTrue(incoming.is_dir())
                self.assertEqual(list(incoming.iterdir()), [])

    def test_identity_symlink_hardlink_and_permissions_are_rejected(self) -> None:
        for kind in ("symlink", "hardlink", "permissions"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                operations, identity = self.fixture(root)
                original = root / "identity-original.txt"
                identity.rename(original)
                if kind == "symlink":
                    identity.symlink_to(original)
                elif kind == "hardlink":
                    identity.hardlink_to(original)
                else:
                    original.rename(identity)
                    identity.chmod(0o640)
                document = descriptor_document()
                descriptor = MODULE.parse_descriptor(encoded(document), now=NOW)
                with patch.object(
                    MODULE,
                    "_open_url",
                    return_value=FakeResponse(str(document["url"]), CIPHERTEXT),
                ):
                    with self.assertRaisesRegex(
                        MODULE.ProductionReceiveError,
                        "identity",
                    ):
                        MODULE.receive_one(
                            descriptor,
                            operations_root=operations,
                            identity_file=identity,
                            required_uid=os.geteuid(),
                        )
                incoming = operations / OPERATION_ID / "incoming"
                self.assertEqual(list(incoming.iterdir()), [])

    def test_operation_root_symlink_and_permissions_are_rejected(self) -> None:
        for kind in ("symlink", "permissions"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                operations, identity = self.fixture(root)
                real_operations = root / "real-operations"
                operations.rename(real_operations)
                if kind == "symlink":
                    operations.symlink_to(real_operations, target_is_directory=True)
                else:
                    real_operations.rename(operations)
                    operations.chmod(0o750)
                document = descriptor_document()
                descriptor = MODULE.parse_descriptor(encoded(document), now=NOW)
                with patch.object(
                    MODULE,
                    "_open_url",
                    return_value=FakeResponse(str(document["url"]), CIPHERTEXT),
                ):
                    with self.assertRaisesRegex(
                        MODULE.ProductionReceiveError,
                        "directory",
                    ):
                        MODULE.receive_one(
                            descriptor,
                            operations_root=operations,
                            identity_file=identity,
                            required_uid=os.geteuid(),
                        )
                inspected = (
                    real_operations if real_operations.exists() else operations
                )
                self.assertEqual(list(inspected.iterdir()), [])

    def test_existing_regular_symlink_or_hardlink_destination_is_never_overwritten(self) -> None:
        for kind in ("regular", "symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                operations, identity = self.fixture(root)
                operation = operations / OPERATION_ID
                operation.mkdir(mode=0o700)
                incoming = operation / "incoming"
                incoming.mkdir(mode=0o700)
                sentinel = root / "sentinel"
                sentinel.write_bytes(b"do-not-change")
                sentinel.chmod(0o600)
                destination = incoming / DESTINATION_NAME
                if kind == "regular":
                    destination.write_bytes(b"existing")
                    destination.chmod(0o600)
                elif kind == "symlink":
                    destination.symlink_to(sentinel)
                else:
                    destination.hardlink_to(sentinel)

                document = descriptor_document()
                descriptor = MODULE.parse_descriptor(encoded(document), now=NOW)

                def fake_age(arguments, **kwargs):  # noqa: ANN001, ANN202, ARG001
                    output = Path(arguments[arguments.index("--output") + 1])
                    output.write_bytes(PLAINTEXT)
                    output.chmod(0o600)
                    return subprocess.CompletedProcess(arguments, 0)

                with (
                    patch.object(
                        MODULE,
                        "_open_url",
                        return_value=FakeResponse(
                            str(document["url"]),
                            CIPHERTEXT,
                        ),
                    ),
                    patch.object(
                        MODULE.subprocess,
                        "run",
                        side_effect=fake_age,
                    ),
                ):
                    with self.assertRaisesRegex(
                        MODULE.ProductionReceiveError,
                        "already exists",
                    ):
                        MODULE.receive_one(
                            descriptor,
                            operations_root=operations,
                            identity_file=identity,
                            required_uid=os.geteuid(),
                        )
                if kind == "regular":
                    self.assertEqual(destination.read_bytes(), b"existing")
                else:
                    self.assertEqual(sentinel.read_bytes(), b"do-not-change")
                self.assertEqual(
                    sorted(path.name for path in incoming.iterdir()),
                    [DESTINATION_NAME],
                )

    def test_decryptor_symlink_or_hardlink_output_is_rejected(self) -> None:
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                source = root / "decrypted-source"
                source.write_bytes(PLAINTEXT)
                source.chmod(0o400 if kind == "hardlink" else 0o600)

                def unsafe_writer(output: Path) -> None:
                    if kind == "symlink":
                        output.symlink_to(source)
                    else:
                        output.hardlink_to(source)

                with self.assertRaisesRegex(
                    MODULE.ProductionReceiveError,
                    "decrypted artifact",
                ):
                    self.receive(root, decrypt_writer=unsafe_writer)
                incoming = root / "operations" / OPERATION_ID / "incoming"
                self.assertEqual(list(incoming.iterdir()), [])
                self.assertEqual(source.read_bytes(), PLAINTEXT)
                self.assertEqual(
                    stat.S_IMODE(source.stat().st_mode),
                    0o400 if kind == "hardlink" else 0o600,
                )

    def test_cli_rejects_url_in_argv_without_echoing_it(self) -> None:
        secret_url = str(descriptor_document()["url"])
        stdout = io.StringIO()
        with (
            patch.object(sys, "argv", ["receiver", secret_url]),
            patch.object(sys, "stdout", stdout),
        ):
            result = MODULE.main()
        self.assertEqual(result, 1)
        payload = stdout.getvalue()
        self.assertNotIn(secret_url, payload)
        self.assertEqual(json.loads(payload)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
