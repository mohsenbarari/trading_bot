"""Characterization test for internal offer expiry signature rejection guard."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)
import tests.test_p1_00_characterization_network_isolation

install_p1_00_network_fence()
tests.test_p1_00_characterization_network_isolation.install_p1_00_network_fence()

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import InternalOfferExpireRequest, expire_offer_internal


class ObservableHeaders:
    """Observable headers double capturing get lookups in request order."""

    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = dict(headers)
        self.requested_keys: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.requested_keys.append(key)
        return self._headers.get(key, default)


class StrictRequestDouble:
    """Strict request double tracking body read count and header lookups."""

    def __init__(self, raw_body: bytes, headers: ObservableHeaders) -> None:
        self._raw_body = raw_body
        self.headers = headers
        self.body_reads = 0

    async def body(self) -> bytes:
        self.body_reads += 1
        return self._raw_body


class OpaqueDBSentinel:
    """Opaque database sentinel exposing no AsyncSession or query/lifecycle API."""

    pass


class ExpireOfferInternalSignatureGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_signature_rejects_before_db_receipt_or_expiry_side_effects(self) -> None:
        raw_body = b'{"offer_id": 42, "source_server": "foreign", "expire_reason": "manual"}'
        headers_map = {
            "x-source-server": "foreign",
            "x-timestamp": "1726531200",
            "x-signature": "invalid-test-signature",
            "x-api-key": "test-internal-api-key",
        }
        observable_headers = ObservableHeaders(headers_map)
        request = StrictRequestDouble(raw_body, observable_headers)

        payload = InternalOfferExpireRequest(
            offer_id=42,
            offer_public_id=None,
            owner_user_id=1001,
            actor_user_id=1002,
            source_surface="webapp",
            source_server="foreign",
            expire_reason="manual",
            command_id=None,
            idempotency_key=None,
        )

        db_sentinel = OpaqueDBSentinel()
        forbidden_session_methods = (
            "execute",
            "get",
            "commit",
            "rollback",
            "flush",
            "refresh",
            "add",
            "delete",
            "close",
        )
        for method_name in forbidden_session_methods:
            self.assertFalse(hasattr(db_sentinel, method_name))

        fence = install_p1_00_network_fence()

        with patch(
            "api.routers.offers.verify_internal_signature",
            return_value=False,
        ) as mock_verify_signature, patch(
            "api.routers.offers._expire_offer_internal_with_receipt",
            new_callable=AsyncMock,
        ) as spy_receipt_flow, patch(
            "api.routers.offers.expire_offer_authoritatively",
            new_callable=AsyncMock,
        ) as spy_authoritative_expire, patch(
            "api.routers.offers.enforce_manual_offer_expire_limits",
            new_callable=AsyncMock,
        ) as spy_manual_limits, patch(
            "api.routers.offers._expire_offer_side_effects",
            new_callable=AsyncMock,
        ) as spy_side_effects:
            denied_connects_before = len(fence["denied_connects"])
            live_redis_before = len(fence["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_context:
                await expire_offer_internal(
                    internal_data=payload,
                    raw_request=request,
                    db=db_sentinel,
                )

            denied_connects_after = len(fence["denied_connects"])
            live_redis_after = len(fence["live_redis_constructs"])

            self.assertEqual(denied_connects_before, denied_connects_after)
            self.assertEqual(live_redis_before, live_redis_after)

        self.assertEqual(exc_context.exception.status_code, 401)
        self.assertEqual(
            exc_context.exception.detail,
            "Invalid internal offer expiry signature",
        )

        self.assertEqual(request.body_reads, 1)
        mock_verify_signature.assert_called_once_with(
            raw_body,
            "1726531200",
            "invalid-test-signature",
            "test-internal-api-key",
        )

        self.assertEqual(
            observable_headers.requested_keys,
            ["x-source-server", "x-timestamp", "x-signature", "x-api-key"],
        )

        spy_receipt_flow.assert_not_called()
        spy_receipt_flow.assert_not_awaited()
        spy_authoritative_expire.assert_not_called()
        spy_authoritative_expire.assert_not_awaited()
        spy_manual_limits.assert_not_called()
        spy_manual_limits.assert_not_awaited()
        spy_side_effects.assert_not_called()
        spy_side_effects.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
