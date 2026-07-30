"""Focused tests for immutable presigned WA-IR release artifact staging."""

from __future__ import annotations

import base64
import datetime as dt
import importlib.util
import json
import stat
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "manage_webapp_ir_artifact_stage.py"
SPEC = importlib.util.spec_from_file_location("manage_webapp_ir_artifact_stage", MODULE_PATH)
assert SPEC and SPEC.loader
stage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stage
SPEC.loader.exec_module(stage)


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
RELEASE_SHA = "a" * 40
BUNDLE_ID = "20260730T120000Z-0123456789abcdef01234567"
AGE_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._offset
        result = self._data[self._offset : self._offset + size]
        self._offset += len(result)
        return result


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, list[dict[str, Any]]] = {}
        self.delete_markers: dict[str, list[dict[str, Any]]] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.version_calls: list[dict[str, Any]] = []
        self.presign_calls: list[dict[str, Any]] = []
        self._sequence = 0

    def get_bucket_versioning(self, **_kwargs: Any) -> dict[str, Any]:
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "Owner": {"ID": "owner"},
            "Grants": [{"Grantee": {"Type": "CanonicalUser", "ID": "owner"}, "Permission": "FULL_CONTROL"}],
        }

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        self.version_calls.append(kwargs)
        prefix = kwargs["Prefix"]
        versions = [
            {"Key": key, "VersionId": entry["version_id"], "IsLatest": index == len(entries) - 1}
            for key, entries in self.objects.items()
            if key.startswith(prefix)
            for index, entry in enumerate(entries)
        ]
        markers = [
            {"Key": key, "VersionId": entry["version_id"], "IsLatest": index == len(entries) - 1}
            for key, entries in self.delete_markers.items()
            if key.startswith(prefix)
            for index, entry in enumerate(entries)
        ]
        return {"Versions": versions, "DeleteMarkers": markers, "IsTruncated": False}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
        if kwargs.get("IfNoneMatch") != "*":
            raise AssertionError("immutable uploads must use IfNoneMatch: *")
        key = kwargs["Key"]
        if key in self.objects or key in self.delete_markers:
            raise FileExistsError(key)
        self._sequence += 1
        version_id = f"version-{self._sequence}"
        self.objects[key] = [
            {
                "version_id": version_id,
                "data": kwargs["Body"].read(),
                "metadata": dict(kwargs["Metadata"]),
            }
        ]
        return {"VersionId": version_id}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        selected = self._entry(kwargs["Key"], kwargs.get("VersionId"))
        result: dict[str, Any] = {
            "VersionId": selected["version_id"],
            "Metadata": dict(selected["metadata"]),
            "Body": FakeBody(selected["data"]),
        }
        if selected.get("server_side_encryption"):
            result["ServerSideEncryption"] = selected["server_side_encryption"]
        return result

    def generate_presigned_url(self, _method: str, *, Params: dict[str, Any], ExpiresIn: int, HttpMethod: str) -> str:
        self.presign_calls.append({"Params": Params, "ExpiresIn": ExpiresIn, "HttpMethod": HttpMethod})
        if HttpMethod != "GET":
            raise AssertionError("only GET URLs are allowed")
        return (
            "https://s3.ir-thr-at1.arvanstorage.ir/"
            + urllib.parse.quote(Params["Bucket"], safe="")
            + "/"
            + urllib.parse.quote(Params["Key"], safe="/")
            + "?versionId="
            + urllib.parse.quote(Params["VersionId"], safe="")
            + "&X-Amz-Algorithm=AWS4-HMAC-SHA256"
            + "&X-Amz-Credential=fake"
            + "&X-Amz-Signature=fake"
        )

    def _entry(self, key: str, version_id: str | None) -> dict[str, Any]:
        entries = self.objects[key]
        if version_id is None:
            return entries[-1]
        return next(entry for entry in entries if entry["version_id"] == version_id)


class FakeDownloadResponse(FakeBody):
    def __init__(self, data: bytes, *, url: str, headers: dict[str, str], status: int = 200) -> None:
        super().__init__(data)
        self._url = url
        self.headers = headers
        self.status = status
        self.closed = False

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self.closed = True


class FakeDownloader:
    def __init__(self, client: FakeS3) -> None:
        self.client = client
        self.calls: list[str] = []

    def __call__(self, request: Any, _timeout: int) -> FakeDownloadResponse:
        url = request.full_url
        self.calls.append(url)
        parsed = urllib.parse.urlparse(url)
        bucket_and_key = urllib.parse.unquote(parsed.path).lstrip("/").split("/", 1)
        if len(bucket_and_key) != 2:
            raise OSError("bad object path")
        _bucket, key = bucket_and_key
        query = urllib.parse.parse_qs(parsed.query)
        version_id = query["versionId"][0]
        entry = self.client._entry(key, version_id)
        headers = {
            "x-amz-version-id": version_id,
            "x-amz-meta-transport-schema": entry["metadata"]["transport-schema"],
            "x-amz-meta-encryption": entry["metadata"]["encryption"],
            "x-amz-meta-ciphertext-sha256": entry["metadata"]["ciphertext-sha256"],
            "content-length": str(len(entry["data"])),
        }
        if entry.get("server_side_encryption"):
            headers["x-amz-server-side-encryption"] = entry["server_side_encryption"]
        return FakeDownloadResponse(entry["data"], url=url, headers=headers)


def fake_encrypt(_binary: str, _recipient: str, source: Path, output: Path) -> None:
    output.write_bytes(b"FAKE-AGE\x00" + source.read_bytes())
    output.chmod(0o600)


def fake_decrypt(_binary: str, _identity: Path, source: Path, output: Path) -> None:
    ciphertext = source.read_bytes()
    if not ciphertext.startswith(b"FAKE-AGE\x00"):
        raise stage.ArtifactStageError("test ciphertext does not contain the fake age envelope")
    output.write_bytes(ciphertext[len(b"FAKE-AGE\x00") :])
    output.chmod(0o600)


class ArtifactStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="artifact-stage-test-")
        self.root = Path(self._temporary.name)
        self.root.chmod(0o700)
        self.workspace = self.root / "workspace"
        self.staging_root = self.root / "detached-staging"
        self.release_bundle = self.root / "release.bundle"
        self.release_bundle.write_bytes(b"# v2 git bundle\nrelease bytes\n")
        self.release_bundle.chmod(0o600)
        self.image_bundle = self.root / "images.tar"
        self.image_bundle.write_bytes(b"docker image archive\x00payload")
        self.image_bundle.chmod(0o600)
        self.identity = self.root / "age-identity.txt"
        self.identity.write_text("AGE-SECRET-KEY-1TEST\n", encoding="utf-8")
        self.identity.chmod(0o600)
        private = Ed25519PrivateKey.generate()
        self.private_key = self.root / "source.key"
        self.private_key.write_bytes(
            private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self.private_key.chmod(0o600)
        self.public_key = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.publisher_config = stage.PublisherConfig(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-artifacts",
            prefix="campaigns/standby",
            credentials_file=self.root / "credentials.json",
            age_binary="/usr/bin/age",
            age_recipient=AGE_RECIPIENT,
            workspace=self.workspace,
            source_site="webapp_fi",
            source_signing_private_key_file=self.private_key,
            maximum_artifact_bytes=1024 * 1024,
            presign_expires_seconds=300,
        )
        self.consumer_config = stage.ConsumerConfig(
            endpoint=self.publisher_config.endpoint,
            region=self.publisher_config.region,
            bucket=self.publisher_config.bucket,
            prefix=self.publisher_config.prefix,
            age_binary="/usr/bin/age",
            age_identity_file=self.identity,
            workspace=self.workspace,
            source_site="webapp_fi",
            source_signing_public_key=self.public_key,
            maximum_artifact_bytes=1024 * 1024,
        )
        self.client = FakeS3()
        self.downloader = FakeDownloader(self.client)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def artifacts(self) -> list[Any]:
        return [
            stage.ArtifactInput(
                "image-bundle",
                self.image_bundle,
                bindings={
                    "image_digest": "sha256:example",
                    "image_tag": "trading_bot_base_iran:rollback-example",
                },
            ),
            stage.ArtifactInput("release-bundle", self.release_bundle),
        ]

    def publish(self) -> dict[str, Any]:
        return stage.publish_bundle(
            self.client,
            config=self.publisher_config,
            destination_site="webapp_ir",
            release_sha=RELEASE_SHA,
            artifacts=self.artifacts(),
            bundle_id=BUNDLE_ID,
            now=NOW,
            encryptor=fake_encrypt,
        )

    def consume(self, published: dict[str, Any], **overrides: Any) -> dict[str, Any]:
        manifest = published["manifest"]
        arguments: dict[str, Any] = {
            "config": self.consumer_config,
            "destination_site": "webapp_ir",
            "release_sha": RELEASE_SHA,
            "bundle_id": BUNDLE_ID,
            "manifest_url": manifest["presigned_url"],
            "manifest_version_id": manifest["version_id"],
            "manifest_ciphertext_sha256": manifest["ciphertext_sha256"],
            "manifest_ciphertext_bytes": manifest["ciphertext_bytes"],
            "staging_root": self.staging_root,
            "now": NOW + dt.timedelta(seconds=1),
            "downloader": self.downloader,
            "decryptor": fake_decrypt,
        }
        arguments.update(overrides)
        return stage.stage_bundle(**arguments)

    def test_publish_and_stage_exact_immutable_release_and_image_artifacts(self) -> None:
        published = self.publish()

        self.assertEqual(stage.PUBLISH_RECEIPT_SCHEMA, published["schema"])
        self.assertEqual("published", published["status"])
        self.assertEqual(3, len(self.client.put_calls))
        self.assertTrue(all(call["IfNoneMatch"] == "*" for call in self.client.put_calls))
        self.assertTrue(all("ServerSideEncryption" not in call for call in self.client.put_calls))
        self.assertTrue(all(call["HttpMethod"] == "GET" for call in self.client.presign_calls))
        self.assertTrue(all("VersionId" in call["Params"] for call in self.client.presign_calls))
        self.assertEqual(3, len(self.client.get_calls), "publisher must exact-VersionId read-back every upload")
        self.assertTrue(all(call.get("VersionId") for call in self.client.get_calls))

        receipt = self.consume(published)
        candidate = self.staging_root / "webapp_fi" / RELEASE_SHA / BUNDLE_ID
        self.assertEqual(str(candidate), receipt["candidate_directory"])
        self.assertEqual(self.release_bundle.read_bytes(), (candidate / "release-bundle").read_bytes())
        self.assertEqual(self.image_bundle.read_bytes(), (candidate / "image-bundle").read_bytes())
        self.assertEqual(0o600, stat.S_IMODE((candidate / "release-bundle").stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE((candidate / "stage-receipt.json").stat().st_mode))
        self.assertNotIn("download_url", json.dumps(receipt, sort_keys=True))
        self.assertEqual(
            {
                "image_digest": "sha256:example",
                "image_tag": "trading_bot_base_iran:rollback-example",
            },
            next(item for item in receipt["artifacts"] if item["name"] == "image-bundle")["bindings"],
        )
        self.assertEqual(3, len(self.downloader.calls), "consumer gets only manifest plus exact signed artifacts")
        self.assertEqual(
            stage.sha256_bytes(
                stage.canonical_json_bytes({key: value for key, value in receipt.items() if key != "receipt_sha256"})
            ),
            receipt["receipt_sha256"],
        )

    def test_publisher_rejects_prior_version_before_conditional_upload(self) -> None:
        base = stage.artifact_base_key(
            prefix=self.publisher_config.prefix,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            release_sha=RELEASE_SHA,
            bundle_id=BUNDLE_ID,
        )
        self.client.objects[base + "/artifacts/image-bundle.age"] = [{"version_id": "old"}]

        with self.assertRaisesRegex(stage.ArtifactStageError, "prior versions"):
            self.publish()
        self.assertEqual([], self.client.put_calls)

    def test_bootstrap_publisher_creates_one_exact_readback_object_without_signing_key_use(self) -> None:
        bootstrap = self.root / "stage-consumer-bootstrap.tar"
        bootstrap.write_bytes(b"trusted bootstrap package")
        bootstrap.chmod(0o600)
        # The bootstrap has no manifest to sign yet.  Its integrity is bound by
        # the returned plaintext/ciphertext hashes and exact Object VersionId.
        self.private_key.unlink()

        published = stage.publish_bootstrap_package(
            self.client,
            config=self.publisher_config,
            destination_site="webapp_ir",
            control_release_sha=RELEASE_SHA,
            bootstrap_path=bootstrap,
            bootstrap_id=BUNDLE_ID,
            now=NOW,
            encryptor=fake_encrypt,
        )

        self.assertEqual(stage.BOOTSTRAP_PUBLISH_RECEIPT_SCHEMA, published["schema"])
        self.assertEqual("published", published["status"])
        self.assertEqual(1, len(self.client.put_calls))
        self.assertEqual(1, len(self.client.get_calls), "bootstrap upload must exact-VersionId read-back")
        self.assertEqual(1, len(self.client.presign_calls))
        expected_key = (
            f"{self.publisher_config.prefix}/bootstrap-artifacts/v1/webapp_fi/webapp_ir/"
            f"{RELEASE_SHA}/{BUNDLE_ID}/stage-consumer-bootstrap.tar.age"
        )
        self.assertEqual(expected_key, published["bootstrap"]["object_key"])
        self.assertEqual(stage.sha256_file(bootstrap)[0], published["bootstrap"]["plaintext_sha256"])
        self.assertEqual(bootstrap.stat().st_size, published["bootstrap"]["plaintext_bytes"])
        self.assertIn("versionId=" + published["bootstrap"]["version_id"], published["bootstrap"]["presigned_url"])
        self.assertTrue(all("ServerSideEncryption" not in call for call in self.client.put_calls))

    def test_bootstrap_publisher_rejects_existing_object_before_upload(self) -> None:
        bootstrap = self.root / "stage-consumer-bootstrap.tar"
        bootstrap.write_bytes(b"trusted bootstrap package")
        bootstrap.chmod(0o600)
        key = (
            f"{self.publisher_config.prefix}/bootstrap-artifacts/v1/webapp_fi/webapp_ir/"
            f"{RELEASE_SHA}/{BUNDLE_ID}/stage-consumer-bootstrap.tar.age"
        )
        self.client.objects[key] = [{"version_id": "old"}]

        with self.assertRaisesRegex(stage.ArtifactStageError, "prior versions"):
            stage.publish_bootstrap_package(
                self.client,
                config=self.publisher_config,
                destination_site="webapp_ir",
                control_release_sha=RELEASE_SHA,
                bootstrap_path=bootstrap,
                bootstrap_id=BUNDLE_ID,
                now=NOW,
                encryptor=fake_encrypt,
            )
        self.assertEqual([], self.client.put_calls)

    def test_consumer_rejects_manifest_url_with_a_different_version_before_network(self) -> None:
        published = self.publish()
        wrong_url = published["manifest"]["presigned_url"].replace("version-3", "wrong-version")

        with self.assertRaisesRegex(stage.ArtifactStageError, "matching VersionId"):
            self.consume(published, manifest_url=wrong_url)
        self.assertEqual([], self.downloader.calls)
        self.assertFalse(self.staging_root.exists())

    def test_consumer_rejects_tampered_signed_manifest_before_artifact_downloads(self) -> None:
        published = self.publish()
        manifest = published["manifest"]
        entry = self.client._entry(manifest["object_key"], manifest["version_id"])
        plaintext = entry["data"][len(b"FAKE-AGE\x00") :]
        value = json.loads(plaintext)
        value["artifacts"][0]["object_key"] = "campaigns/standby/attacker.age"
        tampered = b"FAKE-AGE\x00" + json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        entry["data"] = tampered
        entry["metadata"]["ciphertext-sha256"] = stage.sha256_bytes(tampered)

        with self.assertRaisesRegex(stage.ArtifactStageError, "source_signature verification failed"):
            self.consume(
                published,
                manifest_ciphertext_sha256=stage.sha256_bytes(tampered),
                manifest_ciphertext_bytes=len(tampered),
            )
        self.assertEqual(1, len(self.downloader.calls), "signature failure must precede artifact URLs")

    def test_consumer_rejects_provider_side_encryption_header_before_decrypt(self) -> None:
        published = self.publish()
        manifest = published["manifest"]
        self.client._entry(manifest["object_key"], manifest["version_id"])["server_side_encryption"] = "AES256"

        with self.assertRaisesRegex(stage.ArtifactStageError, "provider-side"):
            self.consume(published)
        self.assertEqual(1, len(self.downloader.calls))

    def test_consumer_refuses_to_overwrite_a_detached_candidate(self) -> None:
        published = self.publish()
        candidate = self.staging_root / "webapp_fi" / RELEASE_SHA / BUNDLE_ID
        candidate.mkdir(parents=True, mode=0o700)
        self.staging_root.chmod(0o700)
        (self.staging_root / "webapp_fi").chmod(0o700)
        (self.staging_root / "webapp_fi" / RELEASE_SHA).chmod(0o700)
        candidate.chmod(0o700)
        (candidate / "existing").write_text("keep", encoding="utf-8")
        (candidate / "existing").chmod(0o600)

        with self.assertRaisesRegex(stage.ArtifactStageError, "overwrite"):
            self.consume(published)
        self.assertTrue((candidate / "existing").is_file())
        self.assertEqual([], self.downloader.calls)

    def test_consumer_config_rejects_credentials_or_unknown_fields(self) -> None:
        config_path = self.root / "consumer.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema": stage.CONFIG_SCHEMA,
                    "endpoint": self.consumer_config.endpoint,
                    "region": self.consumer_config.region,
                    "bucket": self.consumer_config.bucket,
                    "prefix": self.consumer_config.prefix,
                    "age_binary": self.consumer_config.age_binary,
                    "age_identity_file": str(self.identity),
                    "workspace": str(self.workspace),
                    "source_site": self.consumer_config.source_site,
                    "source_signing_public_key_base64": base64.b64encode(self.public_key).decode("ascii"),
                    "credentials_file": "/root/should-not-be-here.json",
                }
            ),
            encoding="utf-8",
        )
        config_path.chmod(0o600)

        with self.assertRaisesRegex(stage.ArtifactStageError, "unsupported fields"):
            stage.load_consumer_config(config_path)

    def test_artifact_binding_parser_binds_non_secret_image_metadata(self) -> None:
        artifacts = stage.apply_artifact_bindings(
            stage.parse_artifact_specifications(["image-bundle=" + str(self.image_bundle)]),
            [
                "image-bundle=image_digest=sha256:example",
                "image-bundle=image_tag=trading_bot_base_iran:rollback-example",
            ],
        )

        self.assertEqual(
            {
                "image_digest": "sha256:example",
                "image_tag": "trading_bot_base_iran:rollback-example",
            },
            artifacts[0].bindings,
        )


if __name__ == "__main__":
    unittest.main()
