from __future__ import annotations

from io import BytesIO
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from botocore.exceptions import ClientError

from core.dr_blob_crypto import DrBlobKeyring
from core.dr_event_protocol import canonical_json_bytes
from core.dr_delivery_worker import ClaimedDeliveryBatch, deliver_batch
from core.dr_object_storage import S3Config
from core.dr_object_transport import (
    DrObjectTransportError,
    build_event_record,
    load_blob_receipt_ack,
    load_blob_receipt_record,
    list_control_record_keys,
    load_event_receipt,
    load_event_record,
    parse_event_record,
    publish_event_receipt,
    publish_event_record,
    publish_blob_receipt_ack,
    publish_blob_receipt_record,
)
from core.dr_sync_auth import PairwiseDrKey, sign_acknowledgement


class _FakeVersionedS3:
    def __init__(self) -> None:
        self.objects: dict[str, list[dict]] = {}
        self.put_count = 0

    @staticmethod
    def _missing() -> ClientError:
        return ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    def put_object(self, **kwargs):
        self.put_count += 1
        key = kwargs["Key"]
        version = f"version-{self.put_count}"
        self.objects.setdefault(key, []).append(
            {
                "version": version,
                "body": bytes(kwargs["Body"].read()),
                "metadata": dict(kwargs["Metadata"]),
            }
        )
        return {"VersionId": version}

    def _item(self, key: str, version: str | None = None) -> dict:
        versions = self.objects.get(key)
        if not versions:
            raise self._missing()
        if version is None:
            return versions[-1]
        for item in versions:
            if item["version"] == version:
                return item
        raise self._missing()

    def head_object(self, **kwargs):
        item = self._item(kwargs["Key"], kwargs.get("VersionId"))
        return {
            "ContentLength": len(item["body"]),
            "Metadata": dict(item["metadata"]),
            "VersionId": item["version"],
            "ETag": "fake-etag",
        }

    def get_object(self, **kwargs):
        item = self._item(kwargs["Key"], kwargs.get("VersionId"))
        return {
            "Body": BytesIO(item["body"]),
            "ContentLength": len(item["body"]),
            "Metadata": dict(item["metadata"]),
            "VersionId": item["version"],
        }

    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        return {
            "Contents": [
                {"Key": key}
                for key in sorted(self.objects)
                if key.startswith(prefix)
            ],
            "IsTruncated": False,
        }


class DrObjectTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s3 = _FakeVersionedS3()
        self.config = S3Config("https://example.invalid", "test", "bucket", "access-key", "s" * 32)
        self.key = PairwiseDrKey("fi-to-ir", "webapp_fi", "webapp_ir", "k" * 32)
        self.keyring = DrBlobKeyring("control-1", {"control-1": b"x" * 32})
        self.settings = patch.multiple(
            "core.dr_object_transport.settings",
            dr_object_transport_prefix="staging/three-site/object-transport",
            dr_blob_require_versioning=True,
        )
        self.settings.start()
        self.client = patch("core.dr_object_transport.object_storage_client", return_value=self.s3)
        self.client.start()

    def tearDown(self) -> None:
        self.client.stop()
        self.settings.stop()

    def _body(self) -> bytes:
        return canonical_json_bytes(
            {
                "events": [
                    {
                        "event_id": "00000000-0000-4000-8000-000000000001",
                        "sequence": 1,
                    }
                ]
            }
        )

    def _acknowledgement(self, request_hash: str) -> dict:
        unsigned = {
            "destination_site": "webapp_ir",
            "source_site": "webapp_fi",
            "key_id": self.key.key_id,
            "request_hash": request_hash,
            "results": [],
        }
        acknowledgement_hash = __import__("hashlib").sha256(
            canonical_json_bytes(unsigned)
        ).hexdigest()
        signed = {**unsigned, "acknowledgement_hash": acknowledgement_hash}
        return {
            **signed,
            "acknowledgement_mac": sign_acknowledgement(payload=signed, secret=self.key.secret),
        }

    def test_event_record_is_encrypted_idempotent_and_has_exact_version(self):
        event, stored = publish_event_record(
            self.config,
            body=self._body(),
            source_site="webapp_fi",
            destination_site="webapp_ir",
            key=self.key,
            keyring=self.keyring,
        )
        second_event, second_stored = publish_event_record(
            self.config,
            body=self._body(),
            source_site="webapp_fi",
            destination_site="webapp_ir",
            key=self.key,
            keyring=self.keyring,
        )

        self.assertEqual(self.s3.put_count, 1)
        self.assertEqual(event, second_event)
        self.assertEqual(stored.version_id, second_stored.version_id)
        self.assertEqual(stored.identity.ciphertext_hash, second_stored.identity.ciphertext_hash)
        self.assertNotIn(self._body()[:24], self.s3.objects[stored.object_key][-1]["body"])
        loaded, loaded_stored = load_event_record(
            self.config,
            object_key=stored.object_key,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            key=self.key,
            keyring=self.keyring,
        )
        self.assertEqual(loaded, event)
        self.assertEqual(loaded_stored.version_id, stored.version_id)
        self.assertEqual(
            list_control_record_keys(
                self.config,
                source_site="webapp_fi",
                destination_site="webapp_ir",
                key=self.key,
            ),
            (stored.object_key,),
        )

    def test_event_record_signature_and_hop_are_fail_closed(self):
        record, plaintext = build_event_record(
            body=self._body(),
            source_site="webapp_fi",
            destination_site="webapp_ir",
            key=self.key,
        )
        record["record_mac"] = "0" * 64
        with self.assertRaisesRegex(DrObjectTransportError, "signature"):
            parse_event_record(
                canonical_json_bytes(record),
                expected_source_site="webapp_fi",
                expected_destination_site="webapp_ir",
                key=self.key,
            )
        with self.assertRaisesRegex(DrObjectTransportError, "not authorized"):
            build_event_record(
                body=plaintext,
                source_site="bot_fi",
                destination_site="webapp_ir",
                key=PairwiseDrKey("bot-to-ir", "bot_fi", "webapp_ir", "k" * 32),
            )

    def test_receipt_is_bound_to_event_version_and_advances_by_new_version(self):
        event, stored = publish_event_record(
            self.config,
            body=self._body(),
            source_site="webapp_fi",
            destination_site="webapp_ir",
            key=self.key,
            keyring=self.keyring,
        )
        received = self._acknowledgement(event.request_hash)
        receipt = publish_event_receipt(
            self.config,
            event=event,
            stored=stored,
            acknowledgement=received,
            key=self.key,
            keyring=self.keyring,
        )
        self.assertEqual(receipt.event_object_version_id, stored.version_id)
        self.assertNotEqual(receipt.receipt_object_key, stored.object_key)
        self.assertTrue(receipt.receipt_object_version_id)
        self.assertEqual(self.s3.put_count, 2)

        applied = {**received, "results": [{"status": "applied"}]}
        unsigned = {name: applied[name] for name in applied if name not in {"acknowledgement_hash", "acknowledgement_mac"}}
        applied["acknowledgement_hash"] = __import__("hashlib").sha256(
            canonical_json_bytes(unsigned)
        ).hexdigest()
        applied["acknowledgement_mac"] = sign_acknowledgement(
            payload={name: applied[name] for name in applied if name != "acknowledgement_mac"},
            secret=self.key.secret,
        )
        publish_event_receipt(
            self.config,
            event=event,
            stored=stored,
            acknowledgement=applied,
            key=self.key,
            keyring=self.keyring,
        )
        self.assertEqual(self.s3.put_count, 3)
        current = load_event_receipt(
            self.config,
            event=event,
            stored=stored,
            key=self.key,
            keyring=self.keyring,
        )
        self.assertEqual(current.acknowledgement["results"], [{"status": "applied"}])

    def test_blob_receipt_request_and_ack_are_encrypted_and_version_bound(self):
        key = PairwiseDrKey("ir-to-fi", "webapp_ir", "webapp_fi", "r" * 32)
        body = canonical_json_bytes(
            {
                "content_hash": "a" * 64,
                "size_bytes": 4,
                "object_version_id": "blob-version-1",
                "object_ciphertext_hash": "b" * 64,
                "object_ciphertext_size": 32,
                "encryption_key_id": "control-1",
                "encryption_algorithm": "AES-256-GCM-v1",
                "receipt_hash": "c" * 64,
            }
        )
        record, stored = publish_blob_receipt_record(
            self.config,
            body=body,
            source_site="webapp_ir",
            destination_site="webapp_fi",
            key=key,
            keyring=self.keyring,
        )
        loaded, exact = load_blob_receipt_record(
            self.config,
            object_key=stored.object_key,
            source_site="webapp_ir",
            destination_site="webapp_fi",
            key=key,
            keyring=self.keyring,
        )
        self.assertEqual((loaded, exact.version_id), (record, stored.version_id))
        unsigned = {
            "destination_site": "webapp_fi",
            "source_site": "webapp_ir",
            "key_id": key.key_id,
            "request_hash": record.request_hash,
            "content_hash": "a" * 64,
            "receipt_hash": "c" * 64,
            "delivery_hash": "d" * 64,
        }
        acknowledgement = {
            **unsigned,
            "acknowledgement_hash": __import__("hashlib").sha256(
                canonical_json_bytes(unsigned)
            ).hexdigest(),
        }
        acknowledgement["acknowledgement_mac"] = sign_acknowledgement(
            payload=acknowledgement, secret=key.secret
        )
        published_ack = publish_blob_receipt_ack(
            self.config,
            record=record,
            stored=stored,
            acknowledgement=acknowledgement,
            key=key,
            keyring=self.keyring,
        )
        loaded_ack = load_blob_receipt_ack(
            self.config,
            record=record,
            stored=stored,
            key=key,
            keyring=self.keyring,
        )
        self.assertEqual(loaded_ack, published_ack)
        self.assertEqual(loaded_ack.object_version_id, stored.version_id)


class ObjectStorageDeliveryRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_fi_to_ir_delivery_never_calls_peer_http(self):
        envelope = {
            "event_id": "00000000-0000-4000-8000-000000000001",
        }
        batch = ClaimedDeliveryBatch(
            "claim-1", "webapp_ir", (envelope["event_id"],), (envelope,)
        )
        key = PairwiseDrKey("fi-to-ir", "webapp_fi", "webapp_ir", "k" * 32)
        client = MagicMock()
        routed = AsyncMock(return_value="acknowledged")
        with patch("core.dr_delivery_worker._deliver_object_storage_batch", routed):
            result = await deliver_batch(
                batch,
                local_site="webapp_fi",
                client=client,
                peer_urls={"webapp_ir": "https://webapp-ir-dr.staging.internal:8443"},
                keys={key.key_id: key},
                object_transport=object(),
            )
        self.assertEqual(result, "acknowledged")
        routed.assert_awaited_once()
        client.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
