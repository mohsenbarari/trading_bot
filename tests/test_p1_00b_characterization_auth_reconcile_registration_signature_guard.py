"""Characterization test for Stage P1-00b Assignment WA-P1-00B-FREEZE-20260904-001 seed API-AUTH-POST-AUTH-RECONCILE-TELEGRAM-REGISTRATION-INTERNAL-L193."""

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

from fastapi import HTTPException, Response

from api.routers.auth import reconcile_telegram_registration_internal


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


class AuthReconcileRegistrationSignatureGuardCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_invalid_signature_rejects_before_authority_parsing_db_or_network_work(
        self,
    ) -> None:
        raw_body = b'{"command_id": "00000000-0000-0000-0000-000000000001", "mobile_number": "+989123456789"}'
        headers_map = {
            "x-timestamp": "1726531200",
            "x-signature": "invalid-test-signature",
            "x-api-key": "test-internal-api-key",
            "x-source-server": "foreign",
        }
        observable_headers = ObservableHeaders(headers_map)
        request = StrictRequestDouble(raw_body, observable_headers)
        response = Response()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cache-Control", response.headers)

        fence = install_p1_00_network_fence()

        with patch(
            "api.routers.auth.verify_internal_signature",
            return_value=False,
        ) as mock_verify_signature, patch(
            "api.routers.auth.current_server",
        ) as spy_current_server, patch(
            "api.routers.auth.normalize_server",
        ) as spy_normalize_server, patch(
            "api.routers.auth.json.loads",
        ) as spy_json_loads, patch(
            "api.routers.auth.TelegramRegistrationCommand.model_validate",
        ) as spy_model_validate, patch(
            "api.routers.auth.direct_registration_runtime_ready",
        ) as spy_direct_ready, patch(
            "api.routers.auth.complete_invitation_registration",
            new_callable=AsyncMock,
        ) as spy_complete_invitation, patch(
            "api.routers.auth.record_registration_completion",
        ) as spy_record_completion, patch(
            "api.routers.auth.audit_log",
        ) as spy_audit_log:
            denied_connects_before = len(fence["denied_connects"])
            live_redis_before = len(fence["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_context:
                await reconcile_telegram_registration_internal(
                    raw_request=request,
                    response=response,
                    db=None,
                )

            denied_connects_after = len(fence["denied_connects"])
            live_redis_after = len(fence["live_redis_constructs"])

            self.assertEqual(denied_connects_before, denied_connects_after)
            self.assertEqual(live_redis_before, live_redis_after)

        self.assertEqual(exc_context.exception.status_code, 401)
        self.assertEqual(
            exc_context.exception.detail,
            "Invalid internal registration signature",
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
            ["x-timestamp", "x-signature", "x-api-key"],
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cache-Control", response.headers)

        spy_current_server.assert_not_called()
        spy_normalize_server.assert_not_called()
        spy_json_loads.assert_not_called()
        spy_model_validate.assert_not_called()
        spy_direct_ready.assert_not_called()
        spy_complete_invitation.assert_not_called()
        spy_complete_invitation.assert_not_awaited()
        spy_record_completion.assert_not_called()
        spy_audit_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
