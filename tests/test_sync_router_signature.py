import hashlib
import hmac
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from api.routers.sync import verify_signature


class FakeRequest:
    def __init__(self, headers=None, body="[]"):
        self.headers = dict(headers or {})
        self._body = body.encode()
        self.url = SimpleNamespace(path="/api/sync/receive")

    async def body(self):
        return self._body


class SyncRouterSignatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_signature_rejects_missing_invalid_expired_and_bad_timestamp(self):
        with patch("api.routers.sync.settings.sync_api_key", "secret"):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_signature(FakeRequest())
            self.assertEqual(exc_info.exception.status_code, 401)
            self.assertEqual(exc_info.exception.detail, "Missing authentication headers")

            with self.assertRaises(HTTPException) as exc_info:
                await verify_signature(
                    FakeRequest(
                        headers={
                            "X-API-Key": "wrong",
                            "X-Timestamp": "100",
                            "X-Signature": "sig",
                        }
                    )
                )
            self.assertEqual(exc_info.exception.detail, "Invalid API Key")

            with patch("api.routers.sync.time.time", return_value=1_000):
                with self.assertRaises(HTTPException) as exc_info:
                    await verify_signature(
                        FakeRequest(
                            headers={
                                "X-API-Key": "secret",
                                "X-Timestamp": "10",
                                "X-Signature": "sig",
                            }
                        )
                    )
                self.assertEqual(exc_info.exception.detail, "Request expired")

            with self.assertRaises(HTTPException) as exc_info:
                await verify_signature(
                    FakeRequest(
                        headers={
                            "X-API-Key": "secret",
                            "X-Timestamp": "not-an-int",
                            "X-Signature": "sig",
                        }
                    )
                )
            self.assertEqual(exc_info.exception.detail, "Invalid timestamp")

    async def test_verify_signature_accepts_valid_signature_and_wraps_bad_signature(self):
        body = "[{\"id\": 1}]"
        timestamp = "1000"
        secret = "secret"
        signature = hmac.new(
            secret.encode(),
            f"{timestamp}:{body}".encode(),
            hashlib.sha256,
        ).hexdigest()

        with patch("api.routers.sync.settings.sync_api_key", secret), patch(
            "api.routers.sync.time.time", return_value=1_000
        ):
            result = await verify_signature(
                FakeRequest(
                    headers={
                        "X-API-Key": secret,
                        "X-Timestamp": timestamp,
                        "X-Signature": signature,
                    },
                    body=body,
                )
            )
        self.assertIsNone(result)

        with patch("api.routers.sync.settings.sync_api_key", secret), patch(
            "api.routers.sync.time.time", return_value=1_000
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_signature(
                    FakeRequest(
                        headers={
                            "X-API-Key": secret,
                            "X-Timestamp": timestamp,
                            "X-Signature": "bad-signature",
                        },
                        body=body,
                    )
                )
        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "Invalid signature")


if __name__ == "__main__":
    unittest.main()
