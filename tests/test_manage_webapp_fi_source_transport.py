"""Focused local tests for controller-owned WebApp-FI source transport."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.parse
from typing import Any


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "manage_webapp_fi_source_transport.py"
SPEC = importlib.util.spec_from_file_location("manage_webapp_fi_source_transport", MODULE_PATH)
assert SPEC and SPEC.loader
transport = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = transport
SPEC.loader.exec_module(transport)


def recipient(character: str) -> str:
    return "age1" + character * 40


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        value = self.payload[self.offset : self.offset + size]
        self.offset += len(value)
        return value


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, list[dict[str, Any]]] = {}
        self.delete_markers: dict[str, list[dict[str, Any]]] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.presign_calls: list[dict[str, Any]] = []
        self.bucket_calls = 0
        self.sequence = 0
        self.put_response_sse: str | None = None
        self.put_response_version: str | None = None
        self.put_response_extra: dict[str, Any] = {}
        self.readback_version_override: str | None = None
        self.readback_sse: str | None = None
        self.readback_response_extra: dict[str, Any] = {}

    def get_bucket_versioning(self, **_kwargs: Any) -> dict[str, Any]:
        self.bucket_calls += 1
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "Owner": {"ID": "owner"},
            "Grants": [{"Grantee": {"Type": "CanonicalUser", "ID": "owner"}, "Permission": "FULL_CONTROL"}],
        }

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        prefix = kwargs["Prefix"]
        versions = [
            {"Key": key, "VersionId": item["version_id"], "IsLatest": index == len(items) - 1}
            for key, items in self.objects.items()
            if key.startswith(prefix)
            for index, item in enumerate(items)
        ]
        markers = [
            {"Key": key, "VersionId": item["version_id"], "IsLatest": index == len(items) - 1}
            for key, items in self.delete_markers.items()
            if key.startswith(prefix)
            for index, item in enumerate(items)
        ]
        return {"Versions": versions, "DeleteMarkers": markers, "IsTruncated": False}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
        if kwargs.get("IfNoneMatch") != "*":
            raise AssertionError("source object PUT must be conditional create-only")
        key = kwargs["Key"]
        if key in self.objects or key in self.delete_markers:
            raise FileExistsError(key)
        self.sequence += 1
        actual_version = f"version-{self.sequence}"
        self.objects[key] = [
            {
                "version_id": actual_version,
                "data": kwargs["Body"].read(),
                "metadata": dict(kwargs["Metadata"]),
            }
        ]
        result: dict[str, Any] = {"VersionId": self.put_response_version or actual_version}
        if self.put_response_sse:
            result["ServerSideEncryption"] = self.put_response_sse
        result.update(self.put_response_extra)
        return result

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        key = kwargs["Key"]
        expected_version = kwargs.get("VersionId")
        item = next(entry for entry in self.objects[key] if entry["version_id"] == expected_version)
        result: dict[str, Any] = {
            "VersionId": self.readback_version_override or item["version_id"],
            "Metadata": dict(item["metadata"]),
            "Body": FakeBody(item["data"]),
        }
        if self.readback_sse:
            result["ServerSideEncryption"] = self.readback_sse
        result.update(self.readback_response_extra)
        return result

    def generate_presigned_url(self, method: str, *, Params: dict[str, Any], ExpiresIn: int, HttpMethod: str) -> str:
        self.presign_calls.append(
            {"method": method, "Params": Params, "ExpiresIn": ExpiresIn, "HttpMethod": HttpMethod}
        )
        signed_headers = (
            "content-type;host;if-none-match;x-amz-meta-ciphertext-sha256;"
            "x-amz-meta-encryption;x-amz-meta-recipient-mode;x-amz-meta-transport-schema"
            if HttpMethod == "PUT"
            else "host"
        )
        query = "&".join(
            (
                "X-Amz-Algorithm=AWS4-HMAC-SHA256",
                "X-Amz-Credential="
                + urllib.parse.quote("FIXTURE/20260730/ir-thr-at1/s3/aws4_request", safe=""),
                "X-Amz-Date=20260730T010203Z",
                "X-Amz-Expires=" + str(ExpiresIn),
                "X-Amz-SignedHeaders=" + urllib.parse.quote(signed_headers, safe=""),
                "X-Amz-Signature=" + "a" * 64,
            )
        )
        if "VersionId" in Params:
            query = "versionId=" + urllib.parse.quote(Params["VersionId"], safe="") + "&" + query
        return (
            "https://s3.ir-thr-at1.arvanstorage.ir/"
            + urllib.parse.quote(Params["Bucket"], safe="")
            + "/"
            + urllib.parse.quote(Params["Key"], safe="/")
            + "?"
            + query
        )

    def add_presigned_upload(self, *, key: str, version_id: str, data: bytes, metadata: dict[str, str]) -> None:
        if key in self.objects or key in self.delete_markers:
            raise AssertionError("test presigned upload must use a new immutable key")
        self.objects[key] = [{"version_id": version_id, "data": data, "metadata": metadata}]


def fake_encrypt(_binary: str, recipients: Any, source: Path, output: Path) -> None:
    output.write_bytes(b"FAKE-AGE\x00" + "|".join(recipients).encode("ascii") + b"\x00" + source.read_bytes())
    output.chmod(0o600)


class SourceTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="source-transport-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.plaintext = self.root / "source.bin"
        self.plaintext.write_bytes(b"opaque source phase payload\x00with bytes")
        self.plaintext.chmod(0o600)
        self.policy = transport.SourceTransportPolicy(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-artifacts",
            prefix="campaigns/three-site",
            age_binary="/usr/bin/age",
            workspace=self.root / "workspace",
            controller_age_recipient=recipient("a"),
            webapp_fi_age_recipient=recipient("c"),
            webapp_ir_age_recipient=recipient("d"),
            maximum_plaintext_bytes=1024 * 1024,
        )
        self.controller_config = transport.ControllerS3Config(
            policy=self.policy,
            credentials_file=self.root / "controller-s3-credentials.json",
            presign_expires_seconds=300,
        )
        self.client = FakeS3()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def static_request(self, recipients: tuple[str, ...] | None = None) -> Any:
        return transport.SourceObjectRequest(
            campaign_id="source-transport-fixture-20260730",
            release_sha="1" * 40,
            control_commit="2" * 40,
            control_tree="3" * 40,
            source_site="webapp_fi",
            destination_site=transport.STATIC_DESTINATION_SITE,
            object_kind=transport.STATIC_OBJECT_KIND,
            object_id="static-20260730-01",
            mode=transport.STATIC_MODE,
            recipients=recipients
            or (self.policy.controller_age_recipient, self.policy.webapp_ir_age_recipient),
        )

    def single_request(self) -> Any:
        return transport.SourceObjectRequest(
            campaign_id="source-transport-fixture-20260730",
            release_sha="1" * 40,
            control_commit="2" * 40,
            control_tree="3" * 40,
            source_site="controller",
            destination_site="webapp_fi",
            object_kind="static-provenance",
            object_id="provenance-20260730-01",
            mode=transport.SINGLE_MODE,
            recipients=(self.policy.webapp_fi_age_recipient,),
        )

    def expectation(self, ciphertext: bytes) -> Any:
        plaintext = self.plaintext.read_bytes()
        return transport.SourceObjectExpectation(
            plaintext_sha256=transport.sha256_bytes(plaintext),
            plaintext_bytes=len(plaintext),
            ciphertext_sha256=transport.sha256_bytes(ciphertext),
            ciphertext_bytes=len(ciphertext),
        )

    def receipt_for(self, request: Any | None = None, *, version_id: str = "version-fixture-receipt-1") -> dict[str, Any]:
        request = request or self.static_request()
        plaintext = self.plaintext.read_bytes()
        return transport.build_publish_receipt(
            config=self.policy,
            request=request,
            descriptor={
                "object_key": transport.source_object_key(self.policy, request),
                "version_id": version_id,
                "ciphertext_sha256": "a" * 64,
                "ciphertext_bytes": 64,
                "plaintext_sha256": transport.sha256_bytes(plaintext),
                "plaintext_bytes": len(plaintext),
            },
        )

    def test_static_publishes_one_version_for_the_two_exact_pinned_recipients(self) -> None:
        exact_recipients = (self.policy.controller_age_recipient, self.policy.webapp_ir_age_recipient)
        ciphertext = b"FAKE-AGE\x00" + "|".join(exact_recipients).encode("ascii") + b"\x00FI static bytes"
        expectation = self.expectation(ciphertext)
        request = self.static_request()
        plan = transport.prepare_presigned_upload(
            self.client,
            controller_config=self.controller_config,
            request=request,
            expectation=expectation,
        )
        self.client.add_presigned_upload(
            key=plan.object_key,
            version_id="version-1",
            data=ciphertext,
            metadata={
                "transport-schema": transport.TRANSPORT_SCHEMA,
                "encryption": transport.OBJECT_ENCRYPTION,
                "ciphertext-sha256": expectation.ciphertext_sha256,
                "recipient-mode": transport.STATIC_MODE,
            },
        )
        receipt_path = self.root / "static-receipt.json"
        receipt = transport.finalize_presigned_upload(
            self.client,
            policy=self.policy,
            request=request,
            expectation=expectation,
            version_id="version-1",
            receipt_path=receipt_path,
        )

        self.assertEqual(exact_recipients, tuple(receipt["recipients"]))
        self.assertIn(exact_recipients[0].encode("ascii"), ciphertext)
        self.assertIn(exact_recipients[1].encode("ascii"), ciphertext)
        self.assertEqual(0, len(self.client.put_calls), "FI direct PUT must not use controller credentials")
        self.assertEqual(1, len(self.client.get_calls), "the controller must read back the exact VersionId")
        self.assertEqual("*", plan.required_headers["if-none-match"])
        self.assertEqual(transport.TRANSPORT_SCHEMA, receipt["schema"])
        self.assertEqual("version-1", receipt["object"]["version_id"])
        self.assertEqual(
            receipt["object"],
            transport.contract.validate_object_descriptor(
                receipt["object"],
                maximum_plaintext_bytes=self.policy.maximum_plaintext_bytes,
            ),
        )
        persisted = receipt_path.read_bytes()
        self.assertNotIn(b"://", persisted.lower())
        self.assertNotIn(b"presigned", persisted.lower())
        self.assertNotIn(b'"url"', persisted.lower())
        self.assertEqual(receipt, transport.verify_publish_receipt(config=self.policy, payload=persisted))

    def test_single_mode_accepts_exactly_one_pinned_destination_recipient(self) -> None:
        receipt = transport.publish_controller_source_object(
            self.client,
            config=self.policy,
            request=self.single_request(),
            plaintext_path=self.plaintext,
            encryptor=fake_encrypt,
        )

        self.assertEqual(1, len(self.client.put_calls))
        self.assertEqual([self.policy.webapp_fi_age_recipient], receipt["recipients"])
        self.assertEqual(transport.SINGLE_MODE, receipt["recipient_mode"])
        self.assertEqual("webapp_fi", receipt["destination_site"])

    def test_bootstrap_record_uses_the_renderer_bound_bot_fi_direction(self) -> None:
        request = transport.SourceObjectRequest(
            campaign_id="source-transport-fixture-20260730",
            release_sha="1" * 40,
            control_commit="2" * 40,
            control_tree="3" * 40,
            source_site="bot_fi",
            destination_site="webapp_fi",
            object_kind=transport.BOOTSTRAP_OBJECT_KIND,
            object_id="bootstrap-package-20260730-01",
            mode=transport.SINGLE_MODE,
            recipients=(self.policy.webapp_fi_age_recipient,),
        )
        receipt = transport.publish_controller_source_object(
            self.client,
            config=self.policy,
            request=request,
            plaintext_path=self.plaintext,
            encryptor=fake_encrypt,
        )
        self.assertEqual("bot_fi", receipt["source_site"])
        self.assertEqual("webapp_fi", receipt["destination_site"])
        self.assertEqual(transport.BOOTSTRAP_OBJECT_KIND, receipt["object_kind"])
        self.assertEqual(request.object_id, receipt["object_id"])
        self.assertEqual(request.release_sha, receipt["release_sha"])
        self.assertEqual(request.control_commit, receipt["control_commit"])
        self.assertEqual(request.control_tree, receipt["control_tree"])

    def test_static_rejects_duplicate_third_and_missing_recipients_before_age_or_s3(self) -> None:
        exact = (self.policy.controller_age_recipient, self.policy.webapp_ir_age_recipient)
        malformed = (
            (self.policy.controller_age_recipient, self.policy.controller_age_recipient),
            exact + (self.policy.webapp_fi_age_recipient,),
            (self.policy.controller_age_recipient,),
        )
        expectation = self.expectation(b"FI age ciphertext")

        for recipients in malformed:
            with self.subTest(recipients=recipients), self.assertRaisesRegex(
                transport.SourceTransportError, "static transport requires exactly"
            ):
                transport.prepare_presigned_upload(
                    self.client,
                    controller_config=self.controller_config,
                    request=self.static_request(recipients),
                    expectation=expectation,
                )

        self.assertEqual([], self.client.put_calls)
        self.assertEqual(0, self.client.bucket_calls)

    def test_rejects_unlisted_direction_or_kind_before_age_or_s3(self) -> None:
        expectation = self.expectation(b"FI age ciphertext")
        invalid = (
            dataclasses.replace(self.static_request(), object_kind="unlisted-object"),
            dataclasses.replace(self.static_request(), source_site="controller"),
        )
        for request in invalid:
            with self.subTest(request=request), self.assertRaisesRegex(
                transport.SourceTransportError, "direction, object kind, or recipient mode is unsupported"
            ):
                transport.prepare_presigned_upload(
                    self.client,
                    controller_config=self.controller_config,
                    request=request,
                    expectation=expectation,
                )
        self.assertEqual([], self.client.put_calls)
        self.assertEqual(0, self.client.bucket_calls)

    def test_object_key_and_receipt_bind_exact_release_and_control_revision(self) -> None:
        request = self.static_request()
        key = transport.source_object_key(self.policy, request)
        self.assertIn("/" + request.release_sha + "/", key)
        self.assertIn("/" + request.control_commit + "/", key)
        self.assertIn("/" + request.control_tree + "/", key)
        self.assertNotEqual(
            key,
            transport.source_object_key(
                self.policy,
                dataclasses.replace(request, release_sha="a" * 40),
            ),
        )
        self.assertNotEqual(
            key,
            transport.source_object_key(
                self.policy,
                dataclasses.replace(request, control_commit="b" * 40),
            ),
        )
        self.assertNotEqual(
            key,
            transport.source_object_key(
                self.policy,
                dataclasses.replace(request, control_tree="c" * 40),
            ),
        )

        controller_request = self.single_request()
        receipt = transport.publish_controller_source_object(
            self.client,
            config=self.policy,
            request=controller_request,
            plaintext_path=self.plaintext,
            encryptor=fake_encrypt,
        )
        self.assertEqual(controller_request.release_sha, receipt["release_sha"])
        self.assertEqual(controller_request.control_commit, receipt["control_commit"])
        self.assertEqual(controller_request.control_tree, receipt["control_tree"])

    def test_rejects_sse_missing_version_and_wrong_readback_version(self) -> None:
        ciphertext = b"FI age ciphertext"
        expectation = self.expectation(ciphertext)
        cases = (
            ("sse", "AES256", "version-1", "provider-side Object Storage encryption"),
            ("missing-version", None, "null", "presigned upload VersionId is invalid"),
            ("wrong-readback", None, "version-1", "read-back returned a different VersionId"),
        )
        for name, readback_sse, version_id, expected_error in cases:
            with self.subTest(case=name):
                client = FakeS3()
                request = self.static_request()
                key = transport.source_object_key(self.policy, request)
                client.add_presigned_upload(
                    key=key,
                    version_id="version-1",
                    data=ciphertext,
                    metadata={
                        "transport-schema": transport.TRANSPORT_SCHEMA,
                        "encryption": transport.OBJECT_ENCRYPTION,
                        "ciphertext-sha256": expectation.ciphertext_sha256,
                        "recipient-mode": transport.STATIC_MODE,
                    },
                )
                client.readback_sse = readback_sse
                if name == "wrong-readback":
                    client.readback_version_override = "another-version"
                with self.assertRaisesRegex(transport.SourceTransportError, expected_error):
                    transport.finalize_presigned_upload(
                        client,
                        policy=self.policy,
                        request=request,
                        expectation=expectation,
                        version_id=version_id,
                    )

    def test_controller_publish_rejects_every_provider_side_encryption_response_field(self) -> None:
        fields = (
            "ServerSideEncryption",
            "SSECustomerAlgorithm",
            "SSECustomerKeyMD5",
            "SSEKMSKeyId",
        )
        for field in fields:
            with self.subTest(response="put", field=field):
                client = FakeS3()
                client.put_response_extra = {field: "fixture"}
                request = dataclasses.replace(self.single_request(), object_id="put-" + field.lower())
                with self.assertRaisesRegex(transport.SourceTransportError, "provider-side Object Storage encryption"):
                    transport.publish_controller_source_object(
                        client,
                        config=self.policy,
                        request=request,
                        plaintext_path=self.plaintext,
                        encryptor=fake_encrypt,
                    )

            with self.subTest(response="readback", field=field):
                client = FakeS3()
                client.readback_response_extra = {field: "fixture"}
                request = dataclasses.replace(self.single_request(), object_id="get-" + field.lower())
                with self.assertRaisesRegex(transport.SourceTransportError, "provider-side Object Storage encryption"):
                    transport.publish_controller_source_object(
                        client,
                        config=self.policy,
                        request=request,
                        plaintext_path=self.plaintext,
                        encryptor=fake_encrypt,
                    )

    def test_controller_can_prepare_transient_presigned_put_and_finalize_exact_readback(self) -> None:
        ciphertext = b"FI age ciphertext already created locally"
        expectation = transport.SourceObjectExpectation(
            plaintext_sha256=transport.sha256_bytes(self.plaintext.read_bytes()),
            plaintext_bytes=len(self.plaintext.read_bytes()),
            ciphertext_sha256=transport.sha256_bytes(ciphertext),
            ciphertext_bytes=len(ciphertext),
        )
        request = self.static_request()
        plan = transport.prepare_presigned_upload(
            self.client,
            controller_config=self.controller_config,
            request=request,
            expectation=expectation,
        )

        self.assertEqual(1, len(self.client.presign_calls))
        self.assertEqual("put_object", self.client.presign_calls[0]["method"])
        self.assertEqual("PUT", self.client.presign_calls[0]["HttpMethod"])
        self.assertEqual("*", self.client.presign_calls[0]["Params"]["IfNoneMatch"])
        self.assertIn("X-Amz-Signature", plan.upload_url)
        self.assertEqual("*", plan.required_headers["if-none-match"])
        self.assertEqual(
            (self.policy.controller_age_recipient, self.policy.webapp_ir_age_recipient), plan.recipients
        )
        self.assertNotIn("credentials", json.dumps(dataclasses_as_json(plan), sort_keys=True).lower())

        self.client.add_presigned_upload(
            key=plan.object_key,
            version_id="version-fixture-1",
            data=ciphertext,
            metadata={
                "transport-schema": transport.TRANSPORT_SCHEMA,
                "encryption": transport.OBJECT_ENCRYPTION,
                "ciphertext-sha256": expectation.ciphertext_sha256,
                "recipient-mode": transport.STATIC_MODE,
            },
        )
        receipt_path = self.root / "finalized-receipt.json"
        receipt = transport.finalize_presigned_upload(
            self.client,
            policy=self.policy,
            request=request,
            expectation=expectation,
            version_id="version-fixture-1",
            receipt_path=receipt_path,
        )
        self.assertEqual("version-fixture-1", receipt["object"]["version_id"])
        self.assertNotIn(b"://", receipt_path.read_bytes().lower())
        download_url = transport.create_version_bound_presigned_get(
            self.client,
            controller_config=self.controller_config,
            request=request,
            version_id="version-fixture-1",
            receipt_payload=transport.canonical_json_bytes(receipt) + b"\n",
        )
        self.assertIn("versionId=version-fixture-1", download_url)
        self.assertEqual("GET", self.client.presign_calls[-1]["HttpMethod"])

    def test_receipt_writer_validates_before_final_create_and_never_replaces_a_race_winner(self) -> None:
        receipt = self.receipt_for()
        invalid_path = self.root / "invalid-receipt.json"
        with self.assertRaisesRegex(transport.SourceTransportError, "receipt is unsupported"):
            transport.write_create_only_receipt(
                invalid_path,
                {**receipt, "unexpected": True},
                config=self.policy,
            )
        self.assertFalse(invalid_path.exists(), "invalid receipt must fail before opening the final path")

        race_path = self.root / "race-receipt.json"
        original_open = transport.os.open

        def open_after_race(path: str, flags: int, mode: int = 0o777) -> int:
            if Path(path) == race_path:
                race_path.write_bytes(b"racer-won")
                race_path.chmod(0o600)
            return original_open(path, flags, mode)

        with (
            mock.patch.object(transport.os, "open", side_effect=open_after_race),
            mock.patch.object(transport.os, "replace") as replace,
        ):
            with self.assertRaisesRegex(transport.SourceTransportError, "refusing to overwrite"):
                transport.write_create_only_receipt(race_path, receipt, config=self.policy)
        replace.assert_not_called()
        self.assertEqual(b"racer-won", race_path.read_bytes())

    def test_get_presign_requires_an_exact_verified_receipt_binding(self) -> None:
        request = self.static_request()
        unrelated = dataclasses.replace(request, object_id="static-other-fixture")
        receipt = self.receipt_for(unrelated)
        before = len(self.client.presign_calls)
        with self.assertRaisesRegex(transport.SourceTransportError, "receipt is not bound"):
            transport.create_version_bound_presigned_get(
                self.client,
                controller_config=self.controller_config,
                request=request,
                version_id="version-fixture-receipt-1",
                receipt_payload=transport.canonical_json_bytes(receipt) + b"\n",
            )
        self.assertEqual(before, len(self.client.presign_calls))

    def test_load_controller_config_exercises_root_controlled_config_path(self) -> None:
        credentials = self.root / "controller-s3-credentials.json"
        credentials.write_text('{"access_key":"fixture","secret_key":"fixture"}', encoding="utf-8")
        credentials.chmod(0o600)
        config_path = self.root / "controller-transport.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema": transport.CONFIG_SCHEMA,
                    "endpoint": self.policy.endpoint,
                    "region": self.policy.region,
                    "bucket": self.policy.bucket,
                    "prefix": self.policy.prefix,
                    "credentials_file": str(credentials),
                    "age_binary": self.policy.age_binary,
                    "workspace": str(self.policy.workspace),
                    "controller_age_recipient": self.policy.controller_age_recipient,
                    "webapp_fi_age_recipient": self.policy.webapp_fi_age_recipient,
                    "webapp_ir_age_recipient": self.policy.webapp_ir_age_recipient,
                    "maximum_plaintext_bytes": self.policy.maximum_plaintext_bytes,
                    "presign_expires_seconds": 300,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        loaded = transport.load_controller_config(config_path)
        self.assertEqual(credentials, loaded.credentials_file)
        self.assertEqual(self.policy, loaded.policy)

    def test_sibling_loader_refuses_writable_file_before_import(self) -> None:
        loader = self.root / "sibling-loader.py"
        loader.write_text("# fixture loader\n", encoding="utf-8")
        loader.chmod(0o644)
        sibling = self.root / "writable-sibling.py"
        sibling.write_text("raise AssertionError('untrusted sibling executed')\n", encoding="utf-8")
        sibling.chmod(0o666)
        module_name = "_source_transport_writable_sibling_fixture"

        with mock.patch.object(transport, "__file__", str(loader)):
            with self.assertRaisesRegex(RuntimeError, "root-owned non-writable regular non-symlink"):
                transport._load_exact_sibling(sibling.name, module_name)
        self.assertNotIn(module_name, sys.modules)

    def test_sibling_loader_refuses_symlink_before_import(self) -> None:
        loader = self.root / "sibling-loader.py"
        loader.write_text("# fixture loader\n", encoding="utf-8")
        loader.chmod(0o644)
        target = self.root / "sibling-target.py"
        target.write_text("raise AssertionError('symlink sibling executed')\n", encoding="utf-8")
        target.chmod(0o644)
        sibling = self.root / "symlink-sibling.py"
        sibling.symlink_to(target.name)
        module_name = "_source_transport_symlink_sibling_fixture"

        with mock.patch.object(transport, "__file__", str(loader)):
            with self.assertRaisesRegex(RuntimeError, "root-owned non-writable regular non-symlink"):
                transport._load_exact_sibling(sibling.name, module_name)
        self.assertNotIn(module_name, sys.modules)

    def test_sibling_loader_refuses_writable_ancestor_before_import(self) -> None:
        parent = self.root / "writable-parent"
        parent.mkdir(mode=0o700)
        loader = parent / "sibling-loader.py"
        loader.write_text("# fixture loader\n", encoding="utf-8")
        loader.chmod(0o644)
        sibling = parent / "otherwise-safe-sibling.py"
        sibling.write_text("raise AssertionError('writable ancestor sibling executed')\n", encoding="utf-8")
        sibling.chmod(0o644)
        parent.chmod(0o777)
        module_name = "_source_transport_writable_parent_fixture"

        with mock.patch.object(transport, "__file__", str(loader)):
            with self.assertRaisesRegex(RuntimeError, "parent is not root-controlled"):
                transport._load_exact_sibling(sibling.name, module_name)
        self.assertNotIn(module_name, sys.modules)

    def test_sibling_loader_accepts_root_owned_sticky_ancestor(self) -> None:
        parent = self.root / "root-sticky-parent"
        parent.mkdir(mode=0o700)
        loader = parent / "sibling-loader.py"
        loader.write_text("# fixture loader\n", encoding="utf-8")
        loader.chmod(0o644)
        sibling = parent / "trusted-sibling.py"
        sibling.write_text("VALUE = 'loaded from a root sticky parent'\n", encoding="utf-8")
        sibling.chmod(0o644)
        parent.chmod(0o1777)
        module_name = "_source_transport_sticky_parent_fixture"

        try:
            with mock.patch.object(transport, "__file__", str(loader)):
                loaded = transport._load_exact_sibling(sibling.name, module_name)
            self.assertEqual("loaded from a root sticky parent", loaded.VALUE)
        finally:
            sys.modules.pop(module_name, None)

    def test_config_and_credentials_reject_unsafe_modes_before_use(self) -> None:
        config_path = self.root / "unsafe-controller-transport.json"
        config_path.write_text("{}", encoding="utf-8")
        config_path.chmod(0o644)
        with self.assertRaisesRegex(transport.SourceTransportError, "root-only regular non-symlink"):
            transport.load_controller_config(config_path)

        credentials = self.root / "unsafe-controller-s3-credentials.json"
        credentials.write_text('{"access_key":"fixture","secret_key":"fixture"}', encoding="utf-8")
        credentials.chmod(0o640)
        unsafe_config = dataclasses.replace(self.controller_config, credentials_file=credentials)
        with (
            mock.patch.object(transport.snapshot, "boto3", object()),
            mock.patch.object(transport.snapshot, "load_credentials") as load_credentials,
        ):
            with self.assertRaisesRegex(transport.SourceTransportError, "root-only regular non-symlink"):
                transport.create_s3_client(unsafe_config)
        load_credentials.assert_not_called()


def dataclasses_as_json(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {name: dataclasses_as_json(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {name: dataclasses_as_json(item) for name, item in value.items()}
    if isinstance(value, tuple):
        return [dataclasses_as_json(item) for item in value]
    return value


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
