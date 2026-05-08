import unittest
from unittest.mock import patch

from jose import JWTError

from api.routers.realtime import verify_ws_token


class RealtimeRouterVerifyTokenTests(unittest.TestCase):
    def test_verify_ws_token_returns_user_and_session_on_valid_token(self):
        with patch("api.routers.realtime.jwt.decode", return_value={"sub": "5", "sid": "session-id"}):
            self.assertEqual(verify_ws_token("token"), (5, "session-id"))

    def test_verify_ws_token_returns_none_for_missing_sub_or_invalid_token(self):
        with patch("api.routers.realtime.jwt.decode", return_value={}):
            self.assertIsNone(verify_ws_token("token"))

        with patch("api.routers.realtime.jwt.decode", side_effect=JWTError("bad")):
            self.assertIsNone(verify_ws_token("token"))

        with patch("api.routers.realtime.jwt.decode", return_value={"sub": "not-an-int"}):
            self.assertIsNone(verify_ws_token("token"))


if __name__ == "__main__":
    unittest.main()