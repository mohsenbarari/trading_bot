"""Focused tests for immutable presigned WA-IR release artifact staging."""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import datetime as dt
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.parse
from types import SimpleNamespace
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

BOOTSTRAP_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_webapp_ir_stage_bootstrap.py"
BOOTSTRAP_SPEC = importlib.util.spec_from_file_location("prepare_webapp_ir_stage_bootstrap", BOOTSTRAP_MODULE_PATH)
assert BOOTSTRAP_SPEC and BOOTSTRAP_SPEC.loader
bootstrap = importlib.util.module_from_spec(BOOTSTRAP_SPEC)
sys.modules[BOOTSTRAP_SPEC.name] = bootstrap
BOOTSTRAP_SPEC.loader.exec_module(bootstrap)


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
RELEASE_SHA = "a" * 40
BUNDLE_ID = "20260730T120000Z-0123456789abcdef01234567"
CAMPAIGN_ID = "wa-ir-standby-97265988-4b12-444e-abda-165573b2769f"
AGE_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


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
        query = urllib.parse.urlencode(
            [
                ("versionId", Params["VersionId"]),
                ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
                ("X-Amz-Credential", "FAKEACCESSKEY/20260730/ir-thr-at1/s3/aws4_request"),
                ("X-Amz-Date", "20260730T120000Z"),
                ("X-Amz-Expires", str(ExpiresIn)),
                ("X-Amz-SignedHeaders", "host"),
                ("X-Amz-Signature", "a" * 64),
            ],
            quote_via=urllib.parse.quote,
            safe="-_.~",
        )
        return (
            "https://s3.ir-thr-at1.arvanstorage.ir/"
            + urllib.parse.quote(Params["Bucket"], safe="")
            + "/"
            + urllib.parse.quote(Params["Key"], safe="/")
            + "?"
            + query
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
        self.fi_source_attestation_public_key = b"f" * 32
        self.controller_authorization_public_key = b"c" * 32
        self.publisher_config = stage.PublisherConfig(
            campaign_id=CAMPAIGN_ID,
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
            campaign_id=CAMPAIGN_ID,
            endpoint=self.publisher_config.endpoint,
            region=self.publisher_config.region,
            bucket=self.publisher_config.bucket,
            prefix=self.publisher_config.prefix,
            age_binary="/usr/bin/age",
            age_identity_file=self.identity,
            age_recipient=AGE_RECIPIENT,
            workspace=self.workspace,
            source_site="webapp_fi",
            source_signing_public_key=self.public_key,
            webapp_fi_source_attestation_public_key=self.fi_source_attestation_public_key,
            webapp_fi_controller_authorization_public_key=self.controller_authorization_public_key,
            maximum_artifact_bytes=1024 * 1024,
        )
        self.client = FakeS3()
        self.downloader = FakeDownloader(self.client)

        self.bootstrap_source = self.root / "bootstrap-source"
        (self.bootstrap_source / "scripts").mkdir(parents=True, mode=0o700)
        (self.bootstrap_source / "core").mkdir(mode=0o700)
        (self.bootstrap_source / "scripts/manage_webapp_ir_artifact_stage.py").write_text(
            "# stage consumer\nVALUE = 'stage'\n", encoding="utf-8"
        )
        (self.bootstrap_source / "scripts/manage_webapp_ir_snapshot.py").write_text(
            "# snapshot primitives\nVALUE = 'snapshot'\n", encoding="utf-8"
        )
        (self.bootstrap_source / "scripts/manage_webapp_ir_release_provenance.py").write_text(
            "# release provenance primitives\nVALUE = 'provenance'\n", encoding="utf-8"
        )
        (self.bootstrap_source / "scripts/prepare_webapp_ir_artifact_bundle.py").write_text(
            "# image archive verifier\nVALUE = 'image-preparer'\n", encoding="utf-8"
        )
        (self.bootstrap_source / "scripts/verify_webapp_fi_source_provenance.py").write_text(
            "# pure WebApp-FI source provenance verifier\nVALUE = 'source-provenance'\n",
            encoding="utf-8",
        )
        (self.bootstrap_source / "scripts/install_webapp_ir_static_assets.py").write_text(
            "# detached static installer\nVALUE = 'static-installer'\n", encoding="utf-8"
        )
        (self.bootstrap_source / "core/standby_snapshot_capacity.py").write_text(
            "# capacity primitives\nVALUE = 'capacity'\n", encoding="utf-8"
        )
        (self.bootstrap_source / "scripts/webapp_ir_image_archive_contract.py").write_text(
            "# image archive contract\nVALUE = 'image-contract'\n", encoding="utf-8"
        )
        self._run_git("init", "-q", cwd=self.bootstrap_source)
        self._run_git("add", ".", cwd=self.bootstrap_source)
        self._run_git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "bootstrap-control",
            cwd=self.bootstrap_source,
        )
        self.bootstrap_commit = self._run_git("rev-parse", "HEAD", cwd=self.bootstrap_source)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _run_git(self, *arguments: str, cwd: Path) -> str:
        result = subprocess.run(
            ["/usr/bin/git", *arguments],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip()

    def prepare_bootstrap_package(
        self,
        *,
        name: str,
        source_signing_public_key: bytes | None = None,
        age_identity_file: str | None = None,
    ) -> tuple[Path, Path, dict[str, Any]]:
        config = {
            "schema": stage.CONFIG_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "endpoint": self.publisher_config.endpoint,
            "region": self.publisher_config.region,
            "bucket": self.publisher_config.bucket,
            "prefix": self.publisher_config.prefix,
            "age_binary": "/usr/bin/age",
            "age_identity_file": age_identity_file or bootstrap.wa_ir_bootstrap_identity_file(CAMPAIGN_ID),
            "age_recipient": AGE_RECIPIENT,
            "workspace": "/srv/trading-bot-three-site-staging-data/workspace",
            "source_site": bootstrap.WA_IR_BOOTSTRAP_SOURCE_SITE,
            "source_signing_public_key_base64": base64.b64encode(
                source_signing_public_key or self.public_key
            ).decode("ascii"),
            "webapp_fi_source_attestation_public_key_base64": base64.b64encode(
                self.fi_source_attestation_public_key
            ).decode("ascii"),
            "webapp_fi_controller_authorization_public_key_base64": base64.b64encode(
                self.controller_authorization_public_key
            ).decode("ascii"),
            "maximum_artifact_bytes": 1024 * 1024,
        }
        config_path = self.root / (name + "-consumer.json")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        config_path.chmod(0o600)
        package_directory = self.root / name
        bootstrap.prepare_bootstrap_package(
            source_repository=self.bootstrap_source,
            control_release_sha=self.bootstrap_commit,
            consumer_config=config_path,
            destination=package_directory,
        )
        receipt = package_directory / bootstrap.PREPARATION_RECEIPT_NAME
        return (
            package_directory,
            receipt,
            bootstrap.verify_prepared_bootstrap_package(
                package_directory=package_directory,
                preparation_receipt=receipt,
            ),
        )

    def replace_bootstrap_archive_with_unexpected_member(self, package_directory: Path, receipt_path: Path) -> None:
        archive_path = package_directory / bootstrap.PACKAGE_ARCHIVE_NAME
        files = bootstrap._read_archive_members(archive_path.read_bytes())
        files["unexpected.txt"] = b"unexpected"
        replacement = package_directory / "replacement.tar"
        archive_sha256, archive_bytes = bootstrap._write_deterministic_archive(replacement, files)
        os.replace(replacement, archive_path)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["bootstrap_archive"]["sha256"] = archive_sha256
        receipt["bootstrap_archive"]["bytes"] = archive_bytes
        receipt.pop("receipt_sha256")
        receipt["receipt_sha256"] = bootstrap._sha256_bytes(bootstrap._canonical_json_bytes(receipt))
        replacement_receipt = package_directory / "replacement-receipt.json"
        replacement_receipt.write_bytes(bootstrap._canonical_json_bytes(receipt) + b"\n")
        replacement_receipt.chmod(0o600)
        os.replace(replacement_receipt, receipt_path)

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

    @staticmethod
    def _rewrite_query(url: str, mutate: Any) -> str:
        parsed = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        mutate(pairs)
        return urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote, safe="-_.~"),
                parsed.fragment,
            )
        )

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

    def test_publisher_rejects_a_stale_controller_snapshot_before_encryption_or_upload(self) -> None:
        artifact = self.artifacts()[0]
        expected_sha256, expected_bytes = stage.sha256_file(artifact.path)
        artifact.path.write_bytes(artifact.path.read_bytes() + b"changed-after-preflight")
        artifact.path.chmod(0o600)
        snapshot_bound = dataclasses.replace(
            artifact,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
        )
        encryptor = mock.Mock(side_effect=fake_encrypt)

        with self.assertRaisesRegex(stage.ArtifactStageError, "no longer matches its controller preflight snapshot"):
            stage.publish_bundle(
                self.client,
                config=self.publisher_config,
                destination_site="webapp_ir",
                release_sha=RELEASE_SHA,
                artifacts=[snapshot_bound],
                bundle_id=BUNDLE_ID,
                now=NOW,
                encryptor=encryptor,
            )

        encryptor.assert_not_called()
        self.assertEqual([], self.client.put_calls)

    def test_publisher_rejects_a_snapshot_change_during_encryption_before_upload(self) -> None:
        artifact = self.artifacts()[0]
        expected_sha256, expected_bytes = stage.sha256_file(artifact.path)
        snapshot_bound = dataclasses.replace(
            artifact,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
        )

        def mutate_after_encrypt(_binary: str, _recipient: str, source: Path, output: Path) -> None:
            fake_encrypt(_binary, _recipient, source, output)
            source.write_bytes(source.read_bytes() + b"changed-during-encryption")
            source.chmod(0o600)

        with self.assertRaisesRegex(stage.ArtifactStageError, "no longer matches its controller preflight snapshot"):
            stage.publish_bundle(
                self.client,
                config=self.publisher_config,
                destination_site="webapp_ir",
                release_sha=RELEASE_SHA,
                artifacts=[snapshot_bound],
                bundle_id=BUNDLE_ID,
                now=NOW,
                encryptor=mutate_after_encrypt,
            )

        self.assertEqual([], self.client.put_calls)

    def test_publisher_encrypts_the_private_snapshot_when_the_source_is_swapped_and_restored(self) -> None:
        artifact = self.artifacts()[0]
        original = artifact.path.read_bytes()
        expected_sha256, expected_bytes = stage.sha256_file(artifact.path)
        snapshot_bound = dataclasses.replace(
            artifact,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
        )
        encrypted_sources: list[Path] = []

        def swap_source_before_encrypt(_binary: str, _recipient: str, source: Path, output: Path) -> None:
            if source.name == "manifest.json":
                fake_encrypt(_binary, _recipient, source, output)
                return
            self.assertNotEqual(artifact.path, source)
            self.assertTrue(source.name.startswith("plaintext-snapshot-"))
            self.assertEqual(0o600, stat.S_IMODE(source.stat().st_mode))
            encrypted_sources.append(source)
            artifact.path.write_bytes(b"replacement visible only at the original pathname")
            artifact.path.chmod(0o600)
            try:
                fake_encrypt(_binary, _recipient, source, output)
            finally:
                artifact.path.write_bytes(original)
                artifact.path.chmod(0o600)

        published = stage.publish_bundle(
            self.client,
            config=self.publisher_config,
            destination_site="webapp_ir",
            release_sha=RELEASE_SHA,
            artifacts=[snapshot_bound],
            bundle_id=BUNDLE_ID,
            now=NOW,
            encryptor=swap_source_before_encrypt,
        )

        self.assertEqual(1, len(encrypted_sources))
        self.assertEqual(original, artifact.path.read_bytes())
        descriptor = published["artifacts"][0]
        encrypted = self.client._entry(descriptor["object_key"], descriptor["version_id"])["data"]
        self.assertEqual(b"FAKE-AGE\x00" + original, encrypted)
        self.assertEqual(expected_sha256, descriptor["sha256"])
        self.assertEqual(expected_bytes, descriptor["bytes"])

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

    def test_bootstrap_publisher_creates_one_exact_readback_object_from_a_verified_preparation(self) -> None:
        package_directory, receipt_path, prepared = self.prepare_bootstrap_package(name="bootstrap-package")
        published = stage.publish_bootstrap_package(
            self.client,
            config=self.publisher_config,
            bootstrap_package_directory=package_directory,
            bootstrap_preparation_receipt=receipt_path,
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
            f"{self.bootstrap_commit}/{BUNDLE_ID}/stage-consumer-bootstrap.tar.age"
        )
        self.assertEqual(expected_key, published["bootstrap"]["object_key"])
        self.assertEqual(self.bootstrap_commit, published["control_commit"])
        self.assertEqual(prepared["control_tree"], published["control_tree"])
        self.assertEqual(prepared["archive_sha256"], published["bootstrap"]["plaintext_sha256"])
        self.assertEqual(prepared["archive_bytes"], published["bootstrap"]["plaintext_bytes"])
        self.assertEqual(prepared["package_manifest_sha256"], published["bootstrap"]["manifest_sha256"])
        self.assertEqual(
            prepared["preparation_receipt_sha256"],
            published["bootstrap"]["preparation_receipt_sha256"],
        )
        self.assertIn("versionId=" + published["bootstrap"]["version_id"], published["bootstrap"]["presigned_url"])
        self.assertTrue(all("ServerSideEncryption" not in call for call in self.client.put_calls))

    def test_bootstrap_publisher_encrypts_the_private_snapshot_when_the_archive_is_swapped_and_restored(self) -> None:
        package_directory, receipt_path, prepared = self.prepare_bootstrap_package(name="bootstrap-snapshot")
        archive_path = Path(prepared["archive_path"])
        original = archive_path.read_bytes()
        encrypted_sources: list[Path] = []

        def swap_archive_before_encrypt(_binary: str, _recipient: str, source: Path, output: Path) -> None:
            self.assertNotEqual(archive_path, source)
            self.assertTrue(source.name.startswith("plaintext-snapshot-"))
            self.assertEqual(0o600, stat.S_IMODE(source.stat().st_mode))
            encrypted_sources.append(source)
            archive_path.write_bytes(b"replacement visible only at the original pathname")
            archive_path.chmod(0o600)
            try:
                fake_encrypt(_binary, _recipient, source, output)
            finally:
                archive_path.write_bytes(original)
                archive_path.chmod(0o600)

        published = stage.publish_bootstrap_package(
            self.client,
            config=self.publisher_config,
            bootstrap_package_directory=package_directory,
            bootstrap_preparation_receipt=receipt_path,
            bootstrap_id=BUNDLE_ID,
            now=NOW,
            encryptor=swap_archive_before_encrypt,
        )

        self.assertEqual(1, len(encrypted_sources))
        self.assertEqual(original, archive_path.read_bytes())
        encrypted = self.client._entry(
            published["bootstrap"]["object_key"], published["bootstrap"]["version_id"]
        )["data"]
        self.assertEqual(b"FAKE-AGE\x00" + original, encrypted)
        self.assertEqual(prepared["archive_sha256"], published["bootstrap"]["plaintext_sha256"])
        self.assertEqual(prepared["archive_bytes"], published["bootstrap"]["plaintext_bytes"])

    def test_bootstrap_publisher_rejects_existing_object_before_upload(self) -> None:
        package_directory, receipt_path, _prepared = self.prepare_bootstrap_package(name="bootstrap-existing")
        key = (
            f"{self.publisher_config.prefix}/bootstrap-artifacts/v1/webapp_fi/webapp_ir/"
            f"{self.bootstrap_commit}/{BUNDLE_ID}/stage-consumer-bootstrap.tar.age"
        )
        self.client.objects[key] = [{"version_id": "old"}]

        with self.assertRaisesRegex(stage.ArtifactStageError, "prior versions"):
            stage.publish_bootstrap_package(
                self.client,
                config=self.publisher_config,
                bootstrap_package_directory=package_directory,
                bootstrap_preparation_receipt=receipt_path,
                bootstrap_id=BUNDLE_ID,
                now=NOW,
                encryptor=fake_encrypt,
            )
        self.assertEqual([], self.client.put_calls)

    def test_bootstrap_publisher_rejects_a_consumer_key_that_does_not_match_the_publisher(self) -> None:
        wrong_key = Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        package_directory, receipt_path, _prepared = self.prepare_bootstrap_package(
            name="bootstrap-wrong-key",
            source_signing_public_key=wrong_key,
        )

        with self.assertRaisesRegex(stage.ArtifactStageError, "does not match the publisher key"):
            stage.publish_bootstrap_package(
                self.client,
                config=self.publisher_config,
                bootstrap_package_directory=package_directory,
                bootstrap_preparation_receipt=receipt_path,
                bootstrap_id=BUNDLE_ID,
                now=NOW,
                encryptor=fake_encrypt,
            )
        self.assertEqual([], self.client.put_calls)

    def test_bootstrap_publisher_rejects_an_unpinned_recipient_before_object_storage(self) -> None:
        package_directory, receipt_path, _prepared = self.prepare_bootstrap_package(name="bootstrap-wrong-recipient")
        wrong_recipient = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"

        with self.assertRaisesRegex(stage.ArtifactStageError, "age_recipient does not match"):
            stage.publish_bootstrap_package(
                self.client,
                config=dataclasses.replace(self.publisher_config, age_recipient=wrong_recipient),
                bootstrap_package_directory=package_directory,
                bootstrap_preparation_receipt=receipt_path,
                bootstrap_id=BUNDLE_ID,
                now=NOW,
                encryptor=fake_encrypt,
            )
        self.assertEqual([], self.client.put_calls)

    def test_bootstrap_publisher_rejects_a_non_webapp_fi_source_before_object_storage(self) -> None:
        package_directory, receipt_path, _prepared = self.prepare_bootstrap_package(name="bootstrap-wrong-source")

        with self.assertRaisesRegex(stage.ArtifactStageError, "source_site must be webapp_fi"):
            stage.publish_bootstrap_package(
                self.client,
                config=dataclasses.replace(self.publisher_config, source_site="bot_fi"),
                bootstrap_package_directory=package_directory,
                bootstrap_preparation_receipt=receipt_path,
                bootstrap_id=BUNDLE_ID,
                now=NOW,
                encryptor=fake_encrypt,
            )
        self.assertEqual([], self.client.put_calls)

    def test_bootstrap_publisher_rejects_transport_config_drift_before_object_storage(self) -> None:
        package_directory, receipt_path, _prepared = self.prepare_bootstrap_package(name="bootstrap-transport-drift")

        with self.assertRaisesRegex(stage.ArtifactStageError, "transport config does not match"):
            stage.publish_bootstrap_package(
                self.client,
                config=dataclasses.replace(self.publisher_config, prefix="campaigns/other"),
                bootstrap_package_directory=package_directory,
                bootstrap_preparation_receipt=receipt_path,
                bootstrap_id=BUNDLE_ID,
                now=NOW,
                encryptor=fake_encrypt,
            )
        self.assertEqual([], self.client.put_calls)

    def test_bootstrap_publisher_rejects_an_archive_with_an_unexpected_member_before_object_storage(self) -> None:
        package_directory, receipt_path, _prepared = self.prepare_bootstrap_package(name="bootstrap-extra-member")
        self.replace_bootstrap_archive_with_unexpected_member(package_directory, receipt_path)

        with self.assertRaisesRegex(stage.ArtifactStageError, "member schema"):
            stage.publish_bootstrap_package(
                self.client,
                config=self.publisher_config,
                bootstrap_package_directory=package_directory,
                bootstrap_preparation_receipt=receipt_path,
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

    def test_presigned_url_requires_canonical_sigv4_scope_and_only_known_query_fields(self) -> None:
        published = self.publish()
        manifest = published["manifest"]
        valid = str(manifest["presigned_url"])

        def require(url: str) -> None:
            stage.require_version_bound_presigned_url(
                url,
                endpoint=self.consumer_config.endpoint,
                bucket=self.consumer_config.bucket,
                object_key=manifest["object_key"],
                version_id=manifest["version_id"],
            )

        require(valid)
        mutations = (
            lambda pairs: pairs.__delitem__(next(index for index, pair in enumerate(pairs) if pair[0] == "X-Amz-Date")),
            lambda pairs: pairs.__setitem__(next(index for index, pair in enumerate(pairs) if pair[0] == "X-Amz-Expires"), ("X-Amz-Expires", "30")),
            lambda pairs: pairs.__setitem__(next(index for index, pair in enumerate(pairs) if pair[0] == "X-Amz-SignedHeaders"), ("X-Amz-SignedHeaders", "host;x-amz-meta-encryption")),
            lambda pairs: pairs.__setitem__(next(index for index, pair in enumerate(pairs) if pair[0] == "X-Amz-Credential"), ("X-Amz-Credential", "FAKEACCESSKEY/20260730/us-east-1/s3/aws4_request")),
            lambda pairs: pairs.append(("unexpected", "value")),
            lambda pairs: pairs.append(("versionId", str(manifest["version_id"]))),
            lambda pairs: pairs.extend((("AWSAccessKeyId", "legacy"), ("Signature", "legacy"), ("Expires", "1"))),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                with self.assertRaises(stage.ArtifactStageError):
                    require(self._rewrite_query(valid, mutate))

    def test_literal_null_version_id_is_rejected_before_any_download_or_stage_side_effect(self) -> None:
        published = self.publish()
        with self.assertRaises(stage.ArtifactStageError):
            stage.require_version_id("null", "test VersionId")
        with self.assertRaises(stage.ArtifactStageError):
            self.consume(published, manifest_version_id="null")
        self.assertEqual([], self.downloader.calls)
        self.assertFalse(self.staging_root.exists())

    def test_download_rejects_sse_kms_and_sse_c_headers_and_removes_fresh_output(self) -> None:
        payload = b"ciphertext"
        expected_sha = stage.sha256_bytes(payload)
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.consumer_config.bucket, "Key": "campaigns/standby/test.age", "VersionId": "version-test"},
            ExpiresIn=300,
            HttpMethod="GET",
        )
        headers = {
            "x-amz-version-id": "version-test",
            "x-amz-meta-transport-schema": stage.TRANSPORT_SCHEMA,
            "x-amz-meta-encryption": stage.OBJECT_ENCRYPTION,
            "x-amz-meta-ciphertext-sha256": expected_sha,
            "content-length": str(len(payload)),
        }
        for name in (
            "x-amz-server-side-encryption",
            "x-amz-server-side-encryption-aws-kms-key-id",
            "x-amz-server-side-encryption-customer-algorithm",
            "x-amz-sse-customer-algorithm",
            "x-amz-kms-key-id",
            "x-amz-bucket-key-enabled",
        ):
            with self.subTest(header=name), tempfile.TemporaryDirectory(prefix="wa-ir-header-") as temporary:
                output = Path(temporary) / "ciphertext.age"
                response = FakeDownloadResponse(payload, url=url, headers={**headers, name: "present"})
                with self.assertRaisesRegex(stage.ArtifactStageError, "provider-side"):
                    stage.download_presigned_object(
                        url=url,
                        config=self.consumer_config,
                        object_key="campaigns/standby/test.age",
                        version_id="version-test",
                        expected_sha256=expected_sha,
                        expected_bytes=len(payload),
                        output_path=output,
                        downloader=lambda _request, _timeout: response,
                    )
                self.assertFalse(output.exists())
                self.assertTrue(response.closed)
        self.assertTrue(stage._response_has_provider_side_encryption({"SSEKMSKeyId": "key"}))
        self.assertTrue(
            stage._response_has_provider_side_encryption(
                {"ResponseMetadata": {"HTTPHeaders": {"x-amz-server-side-encryption-bucket-key-enabled": "true"}}}
            )
        )

    def test_download_enforces_exact_stream_bound_and_removes_short_or_excess_output(self) -> None:
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.consumer_config.bucket, "Key": "campaigns/standby/bounded.age", "VersionId": "version-bounded"},
            ExpiresIn=300,
            HttpMethod="GET",
        )
        for payload in (b"abc", b"abcde"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory(prefix="wa-ir-stream-") as temporary:
                output = Path(temporary) / "bounded.age"
                response = FakeDownloadResponse(
                    payload,
                    url=url,
                    headers={
                        "x-amz-version-id": "version-bounded",
                        "x-amz-meta-transport-schema": stage.TRANSPORT_SCHEMA,
                        "x-amz-meta-encryption": stage.OBJECT_ENCRYPTION,
                        "x-amz-meta-ciphertext-sha256": stage.sha256_bytes(b"abcd"),
                    },
                )
                with self.assertRaises(stage.ArtifactStageError):
                    stage.download_presigned_object(
                        url=url,
                        config=self.consumer_config,
                        object_key="campaigns/standby/bounded.age",
                        version_id="version-bounded",
                        expected_sha256=stage.sha256_bytes(b"abcd"),
                        expected_bytes=4,
                        output_path=output,
                        downloader=lambda _request, _timeout: response,
                    )
                self.assertFalse(output.exists())
                self.assertTrue(response.closed)

    def test_presigned_download_opener_disables_ambient_proxies(self) -> None:
        opener = mock.Mock()
        opener.open.return_value = object()
        request = stage.urllib.request.Request("https://s3.ir-thr-at1.arvanstorage.ir/private/object")
        with mock.patch.object(stage.urllib.request, "build_opener", return_value=opener) as build_opener:
            self.assertIs(opener.open.return_value, stage.open_presigned_request(request, 17))
        handlers = build_opener.call_args.args
        proxy = next(handler for handler in handlers if isinstance(handler, stage.urllib.request.ProxyHandler))
        self.assertEqual({}, proxy.proxies)
        self.assertTrue(any(isinstance(handler, stage._NoRedirectHandler) for handler in handlers))
        opener.open.assert_called_once_with(request, timeout=17)

    def test_atomic_no_replace_promotion_preserves_a_racing_candidate(self) -> None:
        parent = self.root / "promotion"
        parent.mkdir(mode=0o700)
        incoming = parent / ".incoming-race"
        incoming.mkdir(mode=0o700)
        (incoming / "new").write_text("new", encoding="utf-8")
        (incoming / "new").chmod(0o600)
        candidate = parent / "candidate"

        # This models a peer creating the final name after preflight but before
        # the only permitted promotion syscall.
        candidate.mkdir(mode=0o700)
        (candidate / "existing").write_text("keep", encoding="utf-8")
        (candidate / "existing").chmod(0o600)
        with self.assertRaisesRegex(stage.ArtifactStageError, "overwrite"):
            stage._rename_directory_no_replace(incoming, candidate)
        self.assertEqual("keep", (candidate / "existing").read_text(encoding="utf-8"))
        self.assertTrue((incoming / "new").is_file())

    def test_direct_cli_redacts_url_and_command_fields_while_in_memory_result_remains_available(self) -> None:
        url = "https://s3.ir-thr-at1.arvanstorage.ir/private/object?X-Amz-Signature=" + "a" * 64
        in_memory_result = {
            "status": "published",
            "manifest": {"object_key": "campaigns/standby/manifest.age", "presigned_url": url},
            "nested": [{"download_url": url, "command": "ssh root@example.invalid " + url}],
        }
        arguments = SimpleNamespace(
            command="publish",
            config=self.root / "unused.json",
            destination_site="webapp_ir",
            release_sha=RELEASE_SHA,
            artifact=[],
            artifact_binding=[],
            bundle_id=BUNDLE_ID,
        )
        stdout = io.StringIO()
        with (
            mock.patch.object(stage, "parse_args", return_value=arguments),
            mock.patch.object(stage, "load_publisher_config", return_value=self.publisher_config),
            mock.patch.object(stage, "create_s3_client", return_value=object()),
            mock.patch.object(stage, "parse_artifact_specifications", return_value=[]),
            mock.patch.object(stage, "apply_artifact_bindings", return_value=[]),
            mock.patch.object(stage, "publish_bundle", return_value=in_memory_result),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, stage.main([]))
        emitted = stdout.getvalue().strip()
        self.assertNotIn(url, emitted)
        self.assertNotIn("ssh ", emitted)
        self.assertEqual(
            {"manifest": {"object_key": "campaigns/standby/manifest.age"}, "nested": [{}], "status": "published"},
            json.loads(emitted),
        )
        self.assertEqual(url, in_memory_result["manifest"]["presigned_url"])
        blocked = stage._cli_blocked_payload(stage.ArtifactStageError("cannot use " + url))
        self.assertNotIn(url, json.dumps(blocked))

    def test_direct_cli_parse_rejection_does_not_reflect_a_url_to_stdout_or_stderr(self) -> None:
        url = "https://s3.ir-thr-at1.arvanstorage.ir/private/object?X-Amz-Signature=" + "a" * 64
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(2, stage.main(["consume", "--manifest-ciphertext-bytes", url]))
        self.assertNotIn(url, stdout.getvalue())
        self.assertNotIn(url, stderr.getvalue())

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

    def test_consumer_preserves_a_failed_fresh_incoming_candidate_for_inspection(self) -> None:
        published = self.publish()

        def fail_release_bundle(request: Any, timeout: int) -> FakeDownloadResponse:
            if "/release-bundle.age" in request.full_url:
                raise OSError("simulated exact-version download failure")
            return self.downloader(request, timeout)

        with self.assertRaisesRegex(stage.ArtifactStageError, "cannot download the exact presigned"):
            self.consume(published, downloader=fail_release_bundle)

        parent = self.staging_root / "webapp_fi" / RELEASE_SHA
        incoming = list(parent.glob(".incoming-" + BUNDLE_ID + "-*"))
        self.assertEqual(1, len(incoming))
        self.assertEqual(self.image_bundle.read_bytes(), (incoming[0] / "image-bundle").read_bytes())
        self.assertFalse((incoming[0] / "stage-receipt.json").exists())
        self.assertFalse((parent / BUNDLE_ID).exists())

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
                    "campaign_id": CAMPAIGN_ID,
                    "endpoint": self.consumer_config.endpoint,
                    "region": self.consumer_config.region,
                    "bucket": self.consumer_config.bucket,
                    "prefix": self.consumer_config.prefix,
                    "age_binary": self.consumer_config.age_binary,
                    "age_identity_file": str(self.identity),
                    "age_recipient": AGE_RECIPIENT,
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

    def test_consumer_config_requires_two_exact_provenance_keys(self) -> None:
        config_path = self.root / "consumer-provenance-keys.json"
        payload = {
            "schema": stage.CONFIG_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "endpoint": self.consumer_config.endpoint,
            "region": self.consumer_config.region,
            "bucket": self.consumer_config.bucket,
            "prefix": self.consumer_config.prefix,
            "age_binary": self.consumer_config.age_binary,
            "age_identity_file": bootstrap.wa_ir_bootstrap_identity_file(CAMPAIGN_ID),
            "age_recipient": AGE_RECIPIENT,
            "workspace": str(self.workspace),
            "source_site": self.consumer_config.source_site,
            "source_signing_public_key_base64": base64.b64encode(self.public_key).decode("ascii"),
            "webapp_fi_source_attestation_public_key_base64": base64.b64encode(
                self.fi_source_attestation_public_key
            ).decode("ascii"),
            "webapp_fi_controller_authorization_public_key_base64": base64.b64encode(b"x" * 31).decode("ascii"),
            "maximum_artifact_bytes": self.consumer_config.maximum_artifact_bytes,
        }
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        config_path.chmod(0o600)

        with self.assertRaisesRegex(stage.ArtifactStageError, "webapp_fi_controller_authorization_public_key_base64"):
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
