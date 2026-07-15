import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.offer_expiry_forwarding import (
    OFFER_EXPIRY_RECEIPT_OUTCOMES,
    forward_offer_expiry_to_home_server,
)
from core.services.offer_expiry_command_receipt_service import OfferExpiryReceiptOutcome


class FakeClientContext:
    def __init__(self, response):
        self.post = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def payload():
    return {
        "offer_id": 11,
        "offer_public_id": "ofr_stage11_forward_123456",
        "owner_user_id": 7,
        "actor_user_id": 7,
        "source_surface": "webapp",
        "source_server": "iran",
        "expire_reason": "manual",
        "command_id": "85f737c9-477b-47ca-a2b2-d069fdc6d094",
        "idempotency_key": "offer-expiry:v1:85f737c9477b47caa2b2d069fdc6d094",
    }


class OfferExpiryForwardingTests(unittest.IsolatedAsyncioTestCase):
    def test_forwarding_outcomes_follow_receipt_enum(self):
        self.assertEqual(
            OFFER_EXPIRY_RECEIPT_OUTCOMES,
            frozenset(outcome.value for outcome in OfferExpiryReceiptOutcome),
        )

    async def _forward(
        self,
        response_body,
        *,
        request_payload=None,
        status_code=200,
        json_error=False,
    ):
        def response_json():
            if json_error:
                raise ValueError("invalid JSON")
            return response_body

        response = SimpleNamespace(
            status_code=status_code,
            json=response_json,
            text="",
        )
        client = FakeClientContext(response)
        with patch(
            "core.offer_expiry_forwarding.peer_server_url_for",
            return_value="https://peer.invalid",
        ), patch(
            "core.offer_expiry_forwarding.current_server",
            return_value="iran",
        ), patch(
            "core.offer_expiry_forwarding.sign_internal_payload",
            return_value="signature",
        ), patch(
            "core.offer_expiry_forwarding.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "core.offer_expiry_forwarding.log_trading_event",
        ) as log_event:
            result = await forward_offer_expiry_to_home_server(
                "foreign",
                request_payload or payload(),
            )
        return result, client, log_event

    async def test_matching_receipt_ack_is_returned_without_legacy_warning(self):
        request_payload = payload()
        result, client, log_event = await self._forward(
            {
                "expired": True,
                "offer_public_id": request_payload["offer_public_id"],
                "command_id": request_payload["command_id"],
                "outcome": "expired",
                "replayed": False,
            }
        )

        self.assertEqual(result[0], 200)
        client.post.assert_awaited_once()
        events = [call.args[1] for call in log_event.call_args_list]
        self.assertNotIn("offer_expiry_forward.legacy_success_without_receipt_ack", events)

    async def test_command_sender_fails_closed_when_success_has_no_matching_receipt(self):
        result, _client, log_event = await self._forward(
            {"expired": True, "offer_id": 11}
        )

        self.assertEqual(result[0], 503)
        events = [call.args[1] for call in log_event.call_args_list]
        self.assertIn("offer_expiry_forward.receipt_ack_invalid", events)
        failure = next(
            call
            for call in log_event.call_args_list
            if call.args[1] == "offer_expiry_forward.receipt_ack_invalid"
        )
        self.assertEqual(failure.kwargs["result"], "failure")
        self.assertFalse(failure.kwargs["receipt_ack_present"])
        self.assertNotIn("owner_user_id", failure.kwargs)
        self.assertNotIn("actor_user_id", failure.kwargs)

    async def test_command_sender_fails_closed_when_receipt_ack_mismatches(self):
        result, _client, log_event = await self._forward(
            {"expired": True, "command_id": "different-command"}
        )

        self.assertEqual(result[0], 503)
        failure = next(
            call
            for call in log_event.call_args_list
            if call.args[1] == "offer_expiry_forward.receipt_ack_invalid"
        )
        self.assertTrue(failure.kwargs["receipt_ack_present"])

    async def test_command_sender_fails_closed_on_non_json_success(self):
        result, _client, log_event = await self._forward(
            None,
            json_error=True,
        )

        self.assertEqual(result[0], 503)
        events = [call.args[1] for call in log_event.call_args_list]
        self.assertIn("offer_expiry_forward.invalid_json_response", events)

    async def test_command_sender_fails_closed_on_empty_204(self):
        result, _client, _log_event = await self._forward(
            None,
            status_code=204,
            json_error=True,
        )

        self.assertEqual(result[0], 503)

    async def test_command_sender_requires_full_terminal_receipt_contract(self):
        request_payload = payload()
        base = {
            "expired": True,
            "offer_public_id": request_payload["offer_public_id"],
            "command_id": request_payload["command_id"],
            "outcome": "expired",
            "replayed": False,
        }
        invalid_receipts = (
            {key: value for key, value in base.items() if key != "outcome"},
            {**base, "outcome": "unknown"},
            {**base, "replayed": 0},
            {**base, "expired": False},
            {**base, "offer_public_id": "ofr_different_123456"},
        )

        for response_body in invalid_receipts:
            with self.subTest(response_body=response_body):
                result, _client, _log_event = await self._forward(response_body)
                self.assertEqual(result[0], 503)

    async def test_command_sender_rejects_redirect_with_valid_receipt_shape(self):
        request_payload = payload()
        result, _client, log_event = await self._forward(
            {
                "expired": True,
                "offer_public_id": request_payload["offer_public_id"],
                "command_id": request_payload["command_id"],
                "outcome": "expired",
                "replayed": False,
            },
            status_code=302,
        )

        self.assertEqual(result[0], 503)
        events = [call.args[1] for call in log_event.call_args_list]
        self.assertIn("offer_expiry_forward.unexpected_success_status", events)

    async def test_legacy_sender_without_command_identity_remains_compatible(self):
        request_payload = payload()
        request_payload.pop("command_id")
        request_payload.pop("idempotency_key")
        response_body = {"expired": True, "offer_id": 11}

        result, _client, log_event = await self._forward(
            response_body,
            request_payload=request_payload,
        )

        self.assertEqual(result, (200, response_body))
        events = [call.args[1] for call in log_event.call_args_list]
        self.assertNotIn("offer_expiry_forward.receipt_ack_invalid", events)


if __name__ == "__main__":
    unittest.main()
