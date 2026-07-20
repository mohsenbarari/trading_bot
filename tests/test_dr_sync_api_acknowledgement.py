from __future__ import annotations

import json
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from api.routers.dr_sync import receive_blob_receipt, receive_dr_events
from core.dr_event_protocol import canonical_json_bytes
from core.dr_sync_auth import (
    ValidatedDrRequest,
    acknowledgement_signature_is_valid,
)


SECRET = "destination-response-signing-secret-32-bytes"


def _request(body: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/dr-sync/events",
            "raw_path": b"/api/dr-sync/events",
            "query_string": b"",
            "headers": [],
            "client": ("192.0.2.10", 12345),
            "server": ("sync.internal", 443),
        },
        receive,
    )


class _FakeDb:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeBlobDb(_FakeDb):
    def __init__(self, manifest, delivery) -> None:  # noqa: ANN001
        super().__init__()
        self.manifest = manifest
        self.delivery = delivery

    async def get(self, model, _key, **_kwargs):  # noqa: ANN001
        return self.manifest if model.__name__ == "DrBlobManifest" else self.delivery


class DrSyncApiAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    async def test_receiver_signs_destination_acknowledgement_after_commit(self):
        body = json.dumps({"events": []}, separators=(",", ":")).encode()
        auth = ValidatedDrRequest(
            key_id="fi-to-ir-v1",
            source_site="webapp_fi",
            destination_site="webapp_ir",
            nonce="n" * 32,
            timestamp=1_800_000_000,
            request_hash="a" * 64,
        )
        unsigned = {
            "destination_site": "webapp_ir",
            "source_site": "webapp_fi",
            "key_id": "fi-to-ir-v1",
            "request_hash": "a" * 64,
            "results": [],
            "acknowledgement_hash": "b" * 64,
        }
        db = _FakeDb()
        keys_json = json.dumps(
            [
                {
                    "key_id": "fi-to-ir-v1",
                    "source_site": "webapp_fi",
                    "destination_site": "webapp_ir",
                    "secret": SECRET,
                }
            ]
        )
        with patch("api.routers.dr_sync._authenticate", return_value=auth), patch(
            "api.routers.dr_sync.receive_batch",
            new=AsyncMock(return_value=dict(unsigned)),
        ), patch(
            "api.routers.dr_sync.resolve_runtime_identity",
            return_value=SimpleNamespace(physical_site="webapp_ir"),
        ), patch.multiple(
            "api.routers.dr_sync.settings",
            three_site_dr_enabled=True,
            dr_event_protocol_enabled=True,
            dr_sync_pairwise_keys_json=keys_json,
            dr_sync_request_max_age_seconds=30,
        ):
            response = await receive_dr_events(_request(body), db)

        self.assertTrue(db.committed)
        self.assertFalse(db.rolled_back)
        signature = response.pop("acknowledgement_mac")
        self.assertEqual(response, unsigned)
        self.assertTrue(
            acknowledgement_signature_is_valid(
                payload=response,
                signature=signature,
                secret=SECRET,
            )
        )

    async def test_blob_receipt_response_is_also_pairwise_signed(self):
        receipt_unsigned = {
            "content_hash": "b" * 64,
            "size_bytes": 10,
            "object_version_id": "version-1",
            "object_ciphertext_hash": "c" * 64,
            "object_ciphertext_size": 42,
            "encryption_key_id": "blob-key-v1",
            "encryption_algorithm": "AES-256-GCM-v1",
        }
        payload = {
            **receipt_unsigned,
            "receipt_hash": hashlib.sha256(
                canonical_json_bytes(receipt_unsigned)
            ).hexdigest(),
        }
        body = canonical_json_bytes(payload)
        auth = ValidatedDrRequest(
            key_id="ir-to-fi-v1",
            source_site="webapp_ir",
            destination_site="webapp_fi",
            nonce="n" * 32,
            timestamp=1_800_000_000,
            request_hash="a" * 64,
        )
        manifest = SimpleNamespace(
            size_bytes=10,
            object_ciphertext_hash="c" * 64,
            object_ciphertext_size=42,
            encryption_key_id="blob-key-v1",
            encryption_algorithm="AES-256-GCM-v1",
            object_version_id="version-1",
            state="uploaded",
        )
        delivery = SimpleNamespace(
            status="available",
            acknowledged_at=None,
            last_error_code="old",
            next_attempt_at=object(),
            acknowledgement_hash=None,
        )
        db = _FakeBlobDb(manifest, delivery)
        keys_json = json.dumps(
            [
                {
                    "key_id": "ir-to-fi-v1",
                    "source_site": "webapp_ir",
                    "destination_site": "webapp_fi",
                    "secret": SECRET,
                }
            ]
        )
        with patch("api.routers.dr_sync._authenticate", return_value=auth), patch(
            "api.routers.dr_sync.reserve_replay_nonce", new=AsyncMock()
        ), patch(
            "api.routers.dr_sync.resolve_runtime_identity",
            return_value=SimpleNamespace(physical_site="webapp_fi"),
        ), patch.multiple(
            "api.routers.dr_sync.settings",
            three_site_dr_enabled=True,
            dr_event_protocol_enabled=True,
            dr_sync_pairwise_keys_json=keys_json,
            dr_sync_request_max_age_seconds=30,
            dr_blob_require_versioning=True,
        ):
            response = await receive_blob_receipt(_request(body), db)

        self.assertTrue(db.committed)
        signature = response.pop("acknowledgement_mac")
        self.assertEqual(response["destination_site"], "webapp_fi")
        self.assertEqual(response["source_site"], "webapp_ir")
        self.assertEqual(response["key_id"], "ir-to-fi-v1")
        self.assertTrue(
            acknowledgement_signature_is_valid(
                payload=response,
                signature=signature,
                secret=SECRET,
            )
        )


if __name__ == "__main__":
    unittest.main()
