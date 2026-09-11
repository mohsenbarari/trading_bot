from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from api.routers.realtime import sse_stream


class TestP100bCharacterizationRealtimeSseResults(unittest.IsolatedAsyncioTestCase):
    async def test_sse_stream_invalid_token_raises_http_401_and_skips_generator(self):
        token = "invalid-token-sample"
        request = SimpleNamespace()
        current_user = SimpleNamespace(id=42)

        with patch("api.routers.realtime.verify_ws_token", return_value=None) as mock_verify, \
             patch("api.routers.realtime.event_generator") as mock_event_generator:
            with self.assertRaises(HTTPException) as exc_info:
                await sse_stream(
                    request=request,
                    current_user=current_user,
                    token=token,
                )

            exc = exc_info.exception
            self.assertEqual(exc.status_code, 401)
            self.assertEqual(exc.detail, "Could not validate credentials")
            self.assertEqual(exc.headers, {"WWW-Authenticate": "Bearer"})

            mock_verify.assert_called_once_with(token)
            mock_event_generator.assert_not_called()

    async def test_sse_stream_identity_mismatch_uses_current_user_and_freezes_response_headers(self):
        current_user_id = 42
        token_subject = 999
        session_id = "85f737c9-477b-47ca-a2b2-d069fdc6d094"
        token = "token-with-mismatched-subject"

        self.assertNotEqual(token_subject, current_user_id)
        self.assertTrue(bool(session_id))

        request = SimpleNamespace()
        current_user = SimpleNamespace(id=current_user_id)

        async def stream():
            if False:
                yield ""

        sentinel_stream = stream()

        with patch(
            "api.routers.realtime.verify_ws_token",
            return_value=(token_subject, session_id),
        ) as mock_verify, patch(
            "api.routers.realtime.event_generator",
            return_value=sentinel_stream,
        ) as mock_event_generator:
            response = await sse_stream(
                request=request,
                current_user=current_user,
                token=token,
            )

            mock_verify.assert_called_once_with(token)
            self.assertIsInstance(response, StreamingResponse)
            mock_event_generator.assert_called_once_with(
                current_user_id,
                session_id=session_id,
            )
            self.assertNotEqual(mock_event_generator.call_args[0][0], token_subject)

            self.assertEqual(response.media_type, "text/event-stream")
            self.assertEqual(response.headers["Cache-Control"], "no-cache")
            self.assertEqual(response.headers["Connection"], "keep-alive")
            self.assertEqual(response.headers["X-Accel-Buffering"], "no")


if __name__ == "__main__":
    unittest.main()
