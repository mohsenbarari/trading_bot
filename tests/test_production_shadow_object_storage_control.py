from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from unittest import mock

from scripts import production_shadow_object_storage_control as CONTROL
from scripts import production_shadow_object_storage_control_receiver as RECEIVER
from scripts.wa_ir_production_object_storage_transport import EphemeralPresignedGet, PublishedObject


OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
TREE = "2ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
DIGEST = "a" * 64


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")


def envelope() -> dict[str, object]:
    worker = {"hello": "world"}
    value: dict[str, object] = {
        "schema": CONTROL.REQUEST_ENVELOPE_SCHEMA,
        "request_type": RECEIVER.REQUEST_TYPE,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE,
        "release_tree_sha": TREE,
        "role": "webapp_ir",
        "worker_request": worker,
        "worker_request_sha256": hashlib.sha256(canonical(worker)).hexdigest(),
        "request_sha256": "0" * 64,
    }
    value["request_sha256"] = hashlib.sha256(canonical({key: item for key, item in value.items() if key != "request_sha256"})).hexdigest()
    return value


def signed_url(path: str, *, put: bool = False) -> str:
    headers = ["host"]
    if put:
        headers = [
            "content-type",
            "host",
            "if-none-match",
            "x-amz-meta-artifact-kind",
            "x-amz-meta-operation-id",
            "x-amz-meta-request-sha256",
            "x-amz-meta-role",
            "x-amz-meta-transport-schema",
            "x-amz-meta-upload-id",
        ]
    query = urlencode(
        {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"example/{datetime.now(timezone.utc).strftime('%Y%m%d')}/ir-thr-at1/s3/aws4_request",
            "X-Amz-Date": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "X-Amz-Expires": "300",
            "X-Amz-Signature": "b" * 64,
            "X-Amz-SignedHeaders": ";".join(headers),
            **({"versionId": "version-1"} if not put else {}),
        }
    )
    return f"https://s3.ir-thr-at1.arvanstorage.ir{path}?{query}"


class FakeClient:
    def __init__(self) -> None:
        self.params: dict[str, object] | None = None

    def generate_presigned_url(self, operation: str, *, Params: dict[str, object], ExpiresIn: int) -> str:  # noqa: N803
        self.params = {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn}
        return signed_url(f"/{Params['Bucket']}/{Params['Key']}", put=True)


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def read(self, _maximum: int) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True


class FakeReadbackClient:
    def __init__(self, *, payload: bytes, version_id: str, metadata: dict[str, str]) -> None:
        self.payload = payload
        self.version_id = version_id
        self.metadata = metadata
        self.calls: list[dict[str, object]] = []
        self.body: FakeBody | None = None

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {
            "VersionId": self.version_id,
            "Metadata": self.metadata,
            "ContentLength": len(self.payload),
        }

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        self.body = FakeBody(self.payload)
        return {
            "VersionId": self.version_id,
            "Metadata": self.metadata,
            "ContentLength": len(self.payload),
            "Body": self.body,
        }


class FakeProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeLostPutRecoveryClient:
    """Models an accepted PUT whose response was lost before the receiver saw it."""

    def __init__(
        self,
        *,
        payload: bytes,
        version_id: str = "version-lost-reply",
        metadata: dict[str, str],
        missing: bool = False,
        head_metadata: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.version_id = version_id
        self.metadata = metadata
        self.missing = missing
        self.head_metadata = metadata if head_metadata is None else head_metadata
        self.calls: list[dict[str, object]] = []

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if self.missing:
            raise FakeProviderError("NoSuchKey")
        expected_version = kwargs.get("VersionId")
        if expected_version is not None and expected_version != self.version_id:
            raise FakeProviderError("NoSuchKey")
        metadata = self.head_metadata if expected_version is None else self.metadata
        return {
            "VersionId": self.version_id,
            "Metadata": metadata,
            "ContentLength": len(self.payload),
        }

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if kwargs.get("VersionId") != self.version_id:
            raise FakeProviderError("NoSuchKey")
        return {
            "VersionId": self.version_id,
            "Metadata": self.metadata,
            "ContentLength": len(self.payload),
            "Body": io.BytesIO(self.payload),
        }


class ControlTransportTests(unittest.TestCase):
    def test_request_envelope_is_canonical_and_digest_bound(self) -> None:
        value = envelope()
        self.assertEqual(CONTROL.validate_request_envelope(value), value)
        self.assertTrue(CONTROL.request_envelope_payload(value).endswith(b"\n"))

    def test_request_envelope_rejects_mutated_worker_payload(self) -> None:
        value = envelope()
        value["worker_request"] = {"hello": "changed"}
        with self.assertRaises(CONTROL.ControlTransportError):
            CONTROL.validate_request_envelope(value)

    def test_result_grant_is_key_bound_to_operation_role_and_request(self) -> None:
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        self.assertEqual(
            CONTROL.validate_result_upload_grant(
                grant.document(), prefix="dark-standby/production-shadow-control"
            ),
            grant,
        )
        bad = grant.document()
        bad["object_key"] = str(bad["object_key"]).replace("webapp_ir", "witness")
        with self.assertRaises(CONTROL.ControlTransportError):
            CONTROL.validate_result_upload_grant(bad, prefix="dark-standby/production-shadow-control")

    def test_presigned_result_put_requires_create_only_headers(self) -> None:
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        client = FakeClient()
        url = CONTROL.presign_result_upload(client, grant)
        self.assertIn("X-Amz-Signature=", url)
        self.assertEqual(client.params["operation"], "put_object")
        params = client.params["Params"]
        self.assertEqual(params["IfNoneMatch"], "*")
        self.assertEqual(params["Metadata"], grant.metadata())

    def test_same_key_result_url_without_signed_create_only_header_is_rejected(self) -> None:
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        url = signed_url(f"/{grant.bucket}/{grant.object_key}", put=True)
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        signed = query["X-Amz-SignedHeaders"][0].split(";")
        query["X-Amz-SignedHeaders"] = [
            ";".join(item for item in signed if item != "if-none-match")
        ]
        bad = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), "")
        )
        with self.assertRaises(CONTROL.ControlTransportError):
            CONTROL.validate_result_upload_url(bad, grant=grant)

    def test_request_publication_delegates_to_create_only_versioned_transport(self) -> None:
        request = envelope()
        payload = CONTROL.request_envelope_payload(request)
        published = PublishedObject(
            bucket=CONTROL.PRODUCTION_BUCKET,
            object_key=(
                "dark-standby/production-shadow-control/"
                f"{OPERATION_ID}/control-request/0123456789abcdef0123456789abcdef-{'c' * 64}.age"
            ),
            version_id="version-1",
            plaintext_sha256=hashlib.sha256(payload).hexdigest(),
            plaintext_bytes=len(payload),
            ciphertext_sha256="c" * 64,
            ciphertext_bytes=100,
            metadata={
                "operation-id": OPERATION_ID,
                "artifact-kind": CONTROL.REQUEST_ARTIFACT_KIND,
                "request-schema": CONTROL.REQUEST_ENVELOPE_SCHEMA,
                "request-type": RECEIVER.REQUEST_TYPE,
                "role": "webapp_ir",
                "request-sha256": str(request["request_sha256"]),
            },
        )
        presigned = EphemeralPresignedGet(
            signed_url(f"/{published.bucket}/{published.object_key}"),
            expires_in_seconds=300,
            object_key=published.object_key,
            version_id=published.version_id,
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "request.json"
            source.write_bytes(payload)
            os.chmod(source, 0o600)
            with mock.patch.object(
                CONTROL, "publish_age_encrypted", return_value=published
            ) as publish, mock.patch.object(
                CONTROL, "presign_exact_get", return_value=presigned
            ):
                result = CONTROL.publish_request(
                    source,
                    recipient_file=Path("/etc/trading-bot/control-recipient.txt"),
                    prefix="dark-standby/production-shadow-control",
                    client=object(),
                    journal_path=Path(directory) / "publication.json",
                    ttl_seconds=300,
                )
        self.assertEqual(result.published.version_id, "version-1")
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["artifact_kind"], CONTROL.REQUEST_ARTIFACT_KIND)
        self.assertEqual(kwargs["operation_id"], OPERATION_ID)
        self.assertEqual(kwargs["metadata"]["request-sha256"], request["request_sha256"])

    def test_request_descriptor_has_no_durable_url_field(self) -> None:
        request = envelope()
        payload = CONTROL.request_envelope_payload(request)
        published = PublishedObject(
            bucket=CONTROL.PRODUCTION_BUCKET,
            object_key=(
                "dark-standby/production-shadow-control/"
                f"{OPERATION_ID}/control-request/0123456789abcdef0123456789abcdef-{'c' * 64}.age"
            ),
            version_id="version-1",
            plaintext_sha256=hashlib.sha256(payload).hexdigest(),
            plaintext_bytes=len(payload),
            ciphertext_sha256="c" * 64,
            ciphertext_bytes=100,
            metadata={
                "operation-id": OPERATION_ID,
                "artifact-kind": CONTROL.REQUEST_ARTIFACT_KIND,
                "request-schema": CONTROL.REQUEST_ENVELOPE_SCHEMA,
                "request-type": RECEIVER.REQUEST_TYPE,
                "role": "webapp_ir",
                "request-sha256": request["request_sha256"],
            },
        )
        presigned = EphemeralPresignedGet(
            signed_url(f"/{published.bucket}/{published.object_key}"),
            expires_in_seconds=300,
            object_key=published.object_key,
            version_id=published.version_id,
        )
        publication = CONTROL.RequestPublication(
            published=published,
            presigned=presigned,
            request_sha256=str(request["request_sha256"]),
            request_bytes=len(payload),
            request_type=RECEIVER.REQUEST_TYPE,
            role="webapp_ir",
        )
        evidence = publication.evidence()
        self.assertNotIn("url", evidence)
        descriptor = CONTROL.build_request_descriptor(publication)
        self.assertEqual(descriptor["destination_name"], CONTROL.request_destination_name(str(request["request_sha256"])))
        self.assertIn("url", descriptor)

    def test_receiver_command_uses_base64url_and_one_safe_remote_command(self) -> None:
        request = envelope()
        payload = CONTROL.request_envelope_payload(request)
        published = PublishedObject(
            bucket=CONTROL.PRODUCTION_BUCKET,
            object_key=(
                "dark-standby/production-shadow-control/"
                f"{OPERATION_ID}/control-request/0123456789abcdef0123456789abcdef-{'c' * 64}.age"
            ),
            version_id="version-1",
            plaintext_sha256=hashlib.sha256(payload).hexdigest(),
            plaintext_bytes=len(payload),
            ciphertext_sha256="c" * 64,
            ciphertext_bytes=100,
            metadata={
                "operation-id": OPERATION_ID,
                "artifact-kind": CONTROL.REQUEST_ARTIFACT_KIND,
                "request-schema": CONTROL.REQUEST_ENVELOPE_SCHEMA,
                "request-type": RECEIVER.REQUEST_TYPE,
                "role": "webapp_ir",
                "request-sha256": request["request_sha256"],
            },
        )
        url = signed_url(f"/{published.bucket}/{published.object_key}")
        publication = CONTROL.RequestPublication(
            published=published,
            presigned=EphemeralPresignedGet(
                url,
                expires_in_seconds=300,
                object_key=published.object_key,
                version_id=published.version_id,
            ),
            request_sha256=str(request["request_sha256"]),
            request_bytes=len(payload),
            request_type=RECEIVER.REQUEST_TYPE,
            role="webapp_ir",
        )
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=publication.request_sha256,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        result_url = signed_url(f"/{grant.bucket}/{grant.object_key}", put=True)
        command = CONTROL.build_receiver_control_command(
            receiver_path=Path(f"/srv/trading-bot-three-site-production-shadow/{OPERATION_ID}/releases/{RELEASE}/scripts/production_shadow_object_storage_control_receiver.py"),
            policy_path=Path(f"/etc/trading-bot-three-site/{OPERATION_ID}/control-receiver.json"),
            publication=publication,
            result_grant=grant,
            result_url=result_url,
            confirmation="production-shadow-object-storage-control:test",
        )
        joined = "\x00".join(command.argv)
        request_url_b64 = command.argv[command.argv.index("--request-url-b64") + 1]
        result_url_b64 = command.argv[command.argv.index("--result-url-b64") + 1]
        self.assertEqual(
            CONTROL.decode_control_url_argument(request_url_b64, label="control request"),
            url,
        )
        self.assertEqual(
            CONTROL.decode_control_url_argument(result_url_b64, label="control result"),
            result_url,
        )
        self.assertNotIn(url, command.argv)
        self.assertNotIn(result_url, command.argv)
        self.assertNotIn("https://", joined)
        self.assertEqual(
            command.argv[command.argv.index("--request-sha256") + 1],
            publication.request_sha256,
        )
        self.assertNotIn(canonical(request).decode("ascii"), joined)
        self.assertNotIn("/bin/sh", command.argv)
        self.assertEqual(shlex.split(command.remote_command()), list(command.argv))
        self.assertNotIn("https://", command.remote_command())
        self.assertEqual(command.application_payload_bytes_over_ssh, 0)

    def test_result_url_base64url_is_decoded_then_exactly_validated(self) -> None:
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        valid = signed_url(f"/{grant.bucket}/{grant.object_key}", put=True)
        args = argparse.Namespace(
            result_url_b64=CONTROL.encode_control_url_argument(
                valid, label="control result"
            )
        )
        self.assertEqual(
            RECEIVER._result_url_from_arguments(args, grant=grant),  # noqa: SLF001
            valid,
        )
        forged = valid.replace("s3.ir-thr-at1.arvanstorage.ir", "attacker.invalid")
        args.result_url_b64 = CONTROL.encode_control_url_argument(
            forged, label="control result"
        )
        with self.assertRaises(RECEIVER.ControlReceiverError):
            RECEIVER._result_url_from_arguments(args, grant=grant)  # noqa: SLF001
        args.result_url_b64 += "="
        with self.assertRaises(RECEIVER.ControlReceiverError):
            RECEIVER._result_url_from_arguments(args, grant=grant)  # noqa: SLF001

    def test_request_url_base64url_is_decoded_then_exactly_validated(self) -> None:
        object_key = (
            "dark-standby/production-shadow-control/"
            f"{OPERATION_ID}/control-request/"
            f"0123456789abcdef0123456789abcdef-{'c' * 64}.age"
        )
        valid = signed_url(f"/{CONTROL.PRODUCTION_BUCKET}/{object_key}")
        args = argparse.Namespace(
            operation_id=OPERATION_ID,
            request_destination_name=CONTROL.request_destination_name(DIGEST),
            request_object_key=object_key,
            request_version_id="version-1",
            request_url_b64=CONTROL.encode_control_url_argument(
                valid, label="control request"
            ),
            request_ciphertext_sha256="c" * 64,
            request_ciphertext_bytes=100,
            request_plaintext_sha256="a" * 64,
            request_plaintext_bytes=99,
        )
        self.assertEqual(
            RECEIVER._descriptor_from_arguments(args).url,  # noqa: SLF001
            valid,
        )
        forged = valid.replace("s3.ir-thr-at1.arvanstorage.ir", "attacker.invalid")
        args.request_url_b64 = CONTROL.encode_control_url_argument(
            forged, label="control request"
        )
        with self.assertRaises(RECEIVER.ControlReceiverError):
            RECEIVER._descriptor_from_arguments(args)  # noqa: SLF001
        args.request_url_b64 += "="
        with self.assertRaises(RECEIVER.ControlReceiverError):
            RECEIVER._descriptor_from_arguments(args)  # noqa: SLF001

    def test_result_readback_requires_returned_exact_version_and_hash(self) -> None:
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        ciphertext = b"age-encrypted-result"
        result = CONTROL.ResultObject(
            bucket=grant.bucket,
            object_key=grant.object_key,
            version_id="version-2",
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_bytes=len(ciphertext),
            metadata=grant.metadata(),
        )
        client = FakeReadbackClient(payload=ciphertext, version_id="version-2", metadata=grant.metadata())
        self.assertEqual(CONTROL.readback_result_exact(client, result), ciphertext)
        self.assertEqual([call["VersionId"] for call in client.calls], ["version-2", "version-2"])
        self.assertIsNotNone(client.body)
        self.assertTrue(client.body.closed)  # type: ignore[union-attr]
        wrong = replace(result, version_id="other-version")
        with self.assertRaises(CONTROL.ControlTransportError):
            CONTROL.readback_result_exact(client, wrong)

    def test_lost_put_recovery_uses_immutable_journal_and_exact_version(self) -> None:
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        ciphertext = b"accepted-before-reply-lost"
        intent = CONTROL.build_result_upload_recovery_intent(
            prefix="dark-standby/production-shadow-control",
            grant=grant,
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_bytes=len(ciphertext),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "result-recovery-intent.json"
            receipt_path = root / "result-recovery-receipt.json"
            persisted = CONTROL.persist_result_upload_recovery_intent(intent_path, intent)
            self.assertEqual(persisted, intent)
            conflicting = replace(intent, ciphertext_sha256="f" * 64)
            with self.assertRaisesRegex(CONTROL.ControlTransportError, "different binding"):
                CONTROL.persist_result_upload_recovery_intent(intent_path, conflicting)
            raw_intent = intent_path.read_text("ascii")
            self.assertNotIn("https://", raw_intent)
            self.assertNotIn("X-Amz-", raw_intent)
            self.assertNotIn(ciphertext.decode("ascii"), raw_intent)

            client = FakeLostPutRecoveryClient(
                payload=ciphertext,
                metadata=grant.metadata(),
            )
            recovered = CONTROL.recover_result_upload_from_journal(
                client,
                intent_path=intent_path,
                receipt_path=receipt_path,
            )
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.version_id, "version-lost-reply")  # type: ignore[union-attr]
            self.assertEqual(
                client.calls,
                [
                    {"Bucket": grant.bucket, "Key": grant.object_key},
                    {
                        "Bucket": grant.bucket,
                        "Key": grant.object_key,
                        "VersionId": "version-lost-reply",
                    },
                    {
                        "Bucket": grant.bucket,
                        "Key": grant.object_key,
                        "VersionId": "version-lost-reply",
                    },
                ],
            )
            raw_receipt = receipt_path.read_text("ascii")
            self.assertNotIn("https://", raw_receipt)
            self.assertNotIn("X-Amz-", raw_receipt)
            self.assertNotIn(ciphertext.decode("ascii"), raw_receipt)

            no_network = mock.Mock()
            self.assertEqual(
                CONTROL.recover_result_upload_from_journal(
                    no_network,
                    intent_path=intent_path,
                    receipt_path=receipt_path,
                ),
                recovered,
            )
            no_network.head_object.assert_not_called()

    def test_lost_put_recovery_rejects_collision_and_keeps_no_receipt(self) -> None:
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        ciphertext = b"accepted-before-reply-lost"
        intent = CONTROL.build_result_upload_recovery_intent(
            prefix="dark-standby/production-shadow-control",
            grant=grant,
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_bytes=len(ciphertext),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "result-recovery-intent.json"
            receipt_path = root / "result-recovery-receipt.json"
            CONTROL.persist_result_upload_recovery_intent(intent_path, intent)
            client = FakeLostPutRecoveryClient(
                payload=ciphertext,
                metadata=grant.metadata(),
                head_metadata={**grant.metadata(), "upload-id": "other"},
            )
            with self.assertRaisesRegex(CONTROL.ControlTransportError, "failed closed"):
                CONTROL.recover_result_upload_from_journal(
                    client,
                    intent_path=intent_path,
                    receipt_path=receipt_path,
                )
            self.assertEqual(client.calls, [{"Bucket": grant.bucket, "Key": grant.object_key}])
            self.assertFalse(receipt_path.exists())

    def test_lost_put_recovery_returns_none_when_no_object_was_accepted(self) -> None:
        grant = CONTROL.build_result_upload_grant(
            prefix="dark-standby/production-shadow-control",
            operation_id=OPERATION_ID,
            role="webapp_ir",
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        ciphertext = b"not-accepted"
        intent = CONTROL.build_result_upload_recovery_intent(
            prefix="dark-standby/production-shadow-control",
            grant=grant,
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_bytes=len(ciphertext),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "result-recovery-intent.json"
            receipt_path = root / "result-recovery-receipt.json"
            CONTROL.persist_result_upload_recovery_intent(intent_path, intent)
            client = FakeLostPutRecoveryClient(
                payload=ciphertext,
                metadata=grant.metadata(),
                missing=True,
            )
            self.assertIsNone(
                CONTROL.recover_result_upload_from_journal(
                    client,
                    intent_path=intent_path,
                    receipt_path=receipt_path,
                )
            )
            self.assertEqual(client.calls, [{"Bucket": grant.bucket, "Key": grant.object_key}])
            self.assertFalse(receipt_path.exists())


class ReceiverContractTests(unittest.TestCase):
    def policy(self) -> RECEIVER.ReceiverPolicy:
        return RECEIVER.ReceiverPolicy(
            role="webapp_ir",
            campaign_id=CAMPAIGN_ID,
            operation_id=OPERATION_ID,
            release_sha=RELEASE,
            release_tree_sha=TREE,
            release_root=Path(f"/srv/trading-bot-three-site-production-shadow/{OPERATION_ID}/releases/{RELEASE}"),
            receiver_relative_path=RECEIVER.RECEIVER_RELATIVE_PATH,
            receiver_sha256="d" * 64,
            operations_root=Path("/srv/trading-bot/dark-standby/operations"),
            age_identity_path=Path("/root/secure-envs/trading-bot/age-identity.txt"),
            controller_age_recipient="age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            object_storage_prefix="dark-standby/production-shadow-control",
            allowed_request_types={
                RECEIVER.REQUEST_TYPE: RECEIVER.RequestTypePolicy(
                    request_type=RECEIVER.REQUEST_TYPE,
                    worker_relative_path=RECEIVER.WORKER_RELATIVE_PATH,
                    worker_sha256="e" * 64,
                    worker_request_schema=RECEIVER.WORKER_REQUEST_SCHEMA,
                    worker_result_schema=RECEIVER.WORKER_RESULT_SCHEMA,
                    required_role="webapp_ir",
                    required_action="readback",
                    max_result_bytes=1024 * 1024,
                )
            },
        )

    def bound_envelope(
        self,
        policy: RECEIVER.ReceiverPolicy,
        *,
        release_sha: str = RELEASE,
        role: str = "webapp_ir",
        request_type: str = RECEIVER.REQUEST_TYPE,
    ) -> dict[str, object]:
        worker = {
            "schema": RECEIVER.WORKER_REQUEST_SCHEMA,
            "role": "webapp_ir",
            "action": "readback",
            "worker_path": str(policy.release_root / RECEIVER.WORKER_RELATIVE_PATH),
            "worker_sha256": policy.allowed_request_types[RECEIVER.REQUEST_TYPE].worker_sha256,
            "request_sha256": "f" * 64,
        }
        value: dict[str, object] = {
            "schema": CONTROL.REQUEST_ENVELOPE_SCHEMA,
            "request_type": request_type,
            "campaign_id": policy.campaign_id,
            "operation_id": policy.operation_id,
            "release_sha": release_sha,
            "release_tree_sha": policy.release_tree_sha,
            "role": role,
            "worker_request": worker,
            "worker_request_sha256": hashlib.sha256(canonical(worker)).hexdigest(),
            "request_sha256": "0" * 64,
        }
        value["request_sha256"] = hashlib.sha256(
            canonical({key: item for key, item in value.items() if key != "request_sha256"})
        ).hexdigest()
        return value

    def descriptor_for(self, request: dict[str, object]) -> argparse.Namespace:
        return argparse.Namespace(
            operation_id=OPERATION_ID,
            artifact_kind=CONTROL.REQUEST_ARTIFACT_KIND,
            destination_name=CONTROL.request_destination_name(str(request["request_sha256"])),
            object_key=(
                "dark-standby/production-shadow-control/"
                f"{OPERATION_ID}/control-request/0123456789abcdef0123456789abcdef-{'c' * 64}.age"
            ),
        )

    def test_receiver_confirmation_is_request_bound(self) -> None:
        policy = self.policy()
        phrase = RECEIVER.receiver_confirmation(policy, request_sha256=DIGEST)
        self.assertIn(OPERATION_ID, phrase)
        self.assertTrue(phrase.endswith(DIGEST))

    def test_receiver_rejects_wrong_role_release_or_request_type_before_worker(self) -> None:
        policy = self.policy()
        request = self.bound_envelope(policy)
        payload = CONTROL.request_envelope_payload(request)
        descriptor = self.descriptor_for(request)
        with mock.patch.object(RECEIVER.LEASE, "validate_request", return_value=request["worker_request"]):
            received = RECEIVER._validate_received_request(  # noqa: SLF001
                policy,
                descriptor=descriptor,
                request_bytes=payload,
            )
        self.assertEqual(received.envelope["release_sha"], RELEASE)
        for malformed in (
            self.bound_envelope(policy, release_sha="f" * 40),
            self.bound_envelope(policy, role="witness"),
            self.bound_envelope(policy, request_type="untrusted-control-v1"),
        ):
            with self.assertRaises(RECEIVER.ControlReceiverError):
                RECEIVER._validate_received_request(  # noqa: SLF001
                    policy,
                    descriptor=self.descriptor_for(malformed),
                    request_bytes=CONTROL.request_envelope_payload(malformed),
                )

    def test_receiver_policy_has_no_arbitrary_worker_or_action_surface(self) -> None:
        policy = self.policy()
        raw = {
            "request_type": RECEIVER.REQUEST_TYPE,
            "worker_relative_path": RECEIVER.WORKER_RELATIVE_PATH,
            "worker_sha256": "e" * 64,
            "worker_request_schema": RECEIVER.WORKER_REQUEST_SCHEMA,
            "worker_result_schema": RECEIVER.WORKER_RESULT_SCHEMA,
            "required_role": "webapp_ir",
            "required_action": "readback",
            "max_result_bytes": 1024,
        }
        self.assertEqual(
            RECEIVER._policy_request_type(raw, role=policy.role).worker_relative_path,  # noqa: SLF001
            RECEIVER.WORKER_RELATIVE_PATH,
        )
        for field, value in (
            ("worker_relative_path", "scripts/anything.py"),
            ("required_action", "acquire"),
            ("request_type", "arbitrary-command-v1"),
        ):
            altered = dict(raw)
            altered[field] = value
            with self.assertRaises(RECEIVER.ControlReceiverError):
                RECEIVER._policy_request_type(altered, role=policy.role)  # noqa: SLF001

    def test_bad_confirmation_and_bad_result_url_stop_before_object_storage_receive(self) -> None:
        policy = self.policy()
        with mock.patch.object(RECEIVER, "receive_request") as receive:
            with self.assertRaises(RECEIVER.ControlReceiverError):
                RECEIVER.apply(
                    policy,
                    argparse.Namespace(request_sha256=DIGEST, confirm="wrong"),
                )
        receive.assert_not_called()

        grant = CONTROL.build_result_upload_grant(
            prefix=policy.object_storage_prefix,
            operation_id=policy.operation_id,
            role=policy.role,
            request_sha256=DIGEST,
            ttl_seconds=300,
            upload_id="5d50cb39-7747-4ccd-a00f-1c2f7edecab5",
        )
        parsed = urlsplit(signed_url(f"/{grant.bucket}/{grant.object_key}", put=True))
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["X-Amz-SignedHeaders"] = ["content-type;host"]
        bad_url = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), "")
        )
        args = argparse.Namespace(
            request_sha256=DIGEST,
            confirm=RECEIVER.receiver_confirmation(policy, request_sha256=DIGEST),
            result_grant_schema=CONTROL.RESULT_UPLOAD_SCHEMA,
            result_object_key=grant.object_key,
            result_upload_id=grant.upload_id,
            result_ttl_seconds=grant.ttl_seconds,
            result_url_b64=CONTROL.encode_control_url_argument(
                bad_url, label="control result"
            ),
        )
        with mock.patch.object(RECEIVER, "receive_request") as receive:
            with self.assertRaises(RECEIVER.ControlReceiverError):
                RECEIVER.apply(policy, args)
        receive.assert_not_called()

    def test_result_policy_bound_is_enforced_before_persistence(self) -> None:
        request_policy = self.policy().allowed_request_types[RECEIVER.REQUEST_TYPE]
        RECEIVER._require_result_within_policy(b"x" * request_policy.max_result_bytes, request_policy)  # noqa: SLF001
        with self.assertRaises(RECEIVER.ControlReceiverError):
            RECEIVER._require_result_within_policy(  # noqa: SLF001
                b"x" * (request_policy.max_result_bytes + 1), request_policy
            )

    def test_result_ciphertext_replay_requires_create_only_binding(self) -> None:
        recipient = "age1" + "q" * 58
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            result_path.write_bytes(b'{"result":"verified"}\n')
            os.chmod(result_path, 0o600)

            def fake_age(argv: list[str], **_kwargs: object) -> SimpleNamespace:
                output = Path(argv[argv.index("--output") + 1])
                output.write_bytes(b"age-ciphertext")
                os.chmod(output, 0o600)
                return SimpleNamespace(returncode=0, stderr=b"")

            with mock.patch.object(RECEIVER.subprocess, "run", side_effect=fake_age) as run:
                ciphertext, digest, size = RECEIVER._encrypt_result(  # noqa: SLF001
                    result_path,
                    request_sha256=DIGEST,
                    recipient=recipient,
                )
            self.assertEqual(ciphertext.suffix, ".age")
            self.assertEqual(digest, hashlib.sha256(b"age-ciphertext").hexdigest())
            self.assertEqual(size, len(b"age-ciphertext"))
            self.assertEqual(run.call_count, 1)
            with mock.patch.object(RECEIVER.subprocess, "run") as retry:
                self.assertEqual(
                    RECEIVER._encrypt_result(  # noqa: SLF001
                        result_path,
                        request_sha256=DIGEST,
                        recipient=recipient,
                    ),
                    (ciphertext, digest, size),
                )
            retry.assert_not_called()

        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            result_path.write_bytes(b'{"result":"verified"}\n')
            os.chmod(result_path, 0o600)
            result_sha = hashlib.sha256(result_path.read_bytes()).hexdigest()
            orphan = RECEIVER._ciphertext_path(result_path, result_sha256=result_sha)  # noqa: SLF001
            orphan.write_bytes(b"age-ciphertext")
            os.chmod(orphan, 0o600)
            with self.assertRaises(RECEIVER.ControlReceiverError):
                RECEIVER._encrypt_result(  # noqa: SLF001
                    result_path,
                    request_sha256=DIGEST,
                    recipient=recipient,
                )

    def test_root_only_paths_and_plan_default_cannot_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "policy.json"
            source.write_bytes(b"{}")
            os.chmod(source, 0o644)
            with self.assertRaises(RECEIVER.ControlReceiverError):
                RECEIVER._require_root_only_file(source, label="test policy", max_bytes=1024)  # noqa: SLF001
            os.chmod(source, 0o600)
            self.assertEqual(
                RECEIVER._require_root_only_file(source, label="test policy", max_bytes=1024),  # noqa: SLF001
                b"{}",
            )

        policy = self.policy()
        stream = io.StringIO()
        with mock.patch.object(RECEIVER, "load_policy", return_value=policy), mock.patch.object(
            RECEIVER, "apply"
        ) as apply, redirect_stdout(stream):
            self.assertEqual(RECEIVER.main(["--policy", "/etc/trading-bot/control.json"]), 0)
        apply.assert_not_called()
        self.assertIn('"status":"planned"', stream.getvalue())

    def test_worker_argv_is_fixed_and_never_uses_request_command(self) -> None:
        policy = self.policy()
        request = envelope()
        worker = {
            "operation_id": OPERATION_ID,
            "request_sha256": "f" * 64,
            "role": "webapp_ir",
            "action": "readback",
            "renewal_sequence": 0,
        }
        request["worker_request"] = worker
        request["worker_request_sha256"] = hashlib.sha256(canonical(worker)).hexdigest()
        request["request_sha256"] = hashlib.sha256(canonical({key: item for key, item in request.items() if key != "request_sha256"})).hexdigest()
        received = RECEIVER.ReceivedRequest(
            descriptor=None,  # type: ignore[arg-type]
            installation_result="created",
            payload=b"x\n",
            envelope=request,
            policy_request=policy.allowed_request_types[RECEIVER.REQUEST_TYPE],
        )
        argv = RECEIVER._worker_argv(policy, received)  # noqa: SLF001
        self.assertEqual(argv[0:2], ("/usr/bin/env", "-i"))
        self.assertIn("--host-stdio", argv)
        self.assertNotIn("/bin/sh", argv)
        self.assertNotIn("hello", argv)
        self.assertEqual(argv[10], str(policy.release_root / RECEIVER.WORKER_RELATIVE_PATH))

    def test_unsolicited_authority_response_is_rejected(self) -> None:
        expected = {"sequence": 1, "checkpoint": "before", "challenge": "abc"}
        raw = canonical(
            {
                "schema": RECEIVER.CONTROL.AUTHORITY_RESPONSE_SCHEMA,
                "status": "authorized",
                "sequence": 2,
                "checkpoint": "before",
                "challenge": "abc",
                "request_binding_sha256": DIGEST,
            }
        ) + b"\n"
        with self.assertRaises(RECEIVER.ControlReceiverError):
            RECEIVER._validate_authority_response(raw, request_sha256=DIGEST, expected=expected)  # noqa: SLF001

    def test_result_attestation_excludes_urls_and_declares_no_direct_payload(self) -> None:
        policy = self.policy()
        request = envelope()
        descriptor = argparse.Namespace(
            bucket=CONTROL.PRODUCTION_BUCKET,
            object_key="dark-standby/production-shadow-control/object",
            version_id="version-1",
            ciphertext_sha256="a" * 64,
            ciphertext_bytes=99,
            plaintext_sha256="b" * 64,
            plaintext_bytes=88,
        )
        received = RECEIVER.ReceivedRequest(
            descriptor=descriptor,  # type: ignore[arg-type]
            installation_result="created",
            payload=b"x\n",
            envelope=request,
            policy_request=policy.allowed_request_types[RECEIVER.REQUEST_TYPE],
        )
        result = {
            "schema": RECEIVER.RESULT_SCHEMA,
            "status": "verified",
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE,
            "release_tree_sha": TREE,
            "role": "webapp_ir",
            "request_type": RECEIVER.REQUEST_TYPE,
            "request_sha256": request["request_sha256"],
            "worker_request_sha256": request["worker_request_sha256"],
            "worker_result": {},
            "worker_result_sha256": hashlib.sha256(canonical({})).hexdigest(),
            "receiver_sha256": policy.receiver_sha256,
            "result_sha256": "0" * 64,
        }
        result["result_sha256"] = hashlib.sha256(canonical({key: item for key, item in result.items() if key != "result_sha256"})).hexdigest()
        result_object = CONTROL.ResultObject(
            bucket=CONTROL.PRODUCTION_BUCKET,
            object_key="dark-standby/production-shadow-control/result",
            version_id="version-2",
            ciphertext_sha256="c" * 64,
            ciphertext_bytes=123,
            metadata={},
        )
        attestation = RECEIVER._attestation(  # noqa: SLF001
            policy,
            received,
            result_path=Path(f"/srv/trading-bot/dark-standby/operations/{OPERATION_ID}/control-results/{request['request_sha256']}.json"),
            result_document=result,
            result_object=result_object,
        )
        self.assertNotIn("https://", json.dumps(attestation))
        self.assertEqual(attestation["payload_bytes_over_ssh"], 0)
        self.assertFalse(attestation["generic_shell_execution_used"])


if __name__ == "__main__":
    unittest.main()
