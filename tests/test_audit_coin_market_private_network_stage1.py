import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path

from scripts import audit_coin_market_private_network_stage1 as stage1


class CoinMarketPrivateNetworkStage1Tests(unittest.TestCase):
    def setUp(self):
        self.current = b"c" * 32
        self.next = b"n" * 32
        self.authenticator = stage1.RequestAuthenticator(
            {"current": self.current, "next": self.next},
            max_clock_skew_seconds=30,
            replay_window_seconds=120,
        )
        self.body = b"opaque-market-fact"

    def headers(self, key_id="current", key=None, timestamp=1_000, nonce=None):
        return stage1.make_headers(
            key_id,
            key or self.current,
            self.body,
            timestamp=timestamp,
            nonce=nonce,
        )

    def test_canonical_request_binds_method_path_identity_time_nonce_and_body(self):
        material = stage1.canonical_request(
            "POST", "/v1/probe", "current", "1000", "a" * 32, self.body
        )
        expected = (
            "POST\n/v1/probe\ncurrent\n1000\n"
            + "a" * 32
            + "\n"
            + hashlib.sha256(self.body).hexdigest()
        ).encode("ascii")
        self.assertEqual(material, expected)
        self.assertEqual(
            stage1.sign_request(
                self.current,
                "POST",
                "/v1/probe",
                "current",
                "1000",
                "a" * 32,
                self.body,
            ),
            hmac.new(self.current, expected, hashlib.sha256).hexdigest(),
        )

    def test_current_and_next_rotation_keys_are_accepted(self):
        current = self.authenticator.authenticate(
            "POST", stage1.PROBE_PATH, self.headers(nonce="a" * 32), self.body, now=1_000
        )
        following = self.authenticator.authenticate(
            "POST",
            stage1.PROBE_PATH,
            self.headers(key_id="next", key=self.next, nonce="b" * 32),
            self.body,
            now=1_000,
        )
        self.assertEqual(current.key_id, "current")
        self.assertEqual(following.key_id, "next")

    def test_replay_bad_signature_unknown_key_and_clock_skew_fail_closed(self):
        replay = self.headers(nonce="c" * 32)
        self.authenticator.authenticate(
            "POST", stage1.PROBE_PATH, replay, self.body, now=1_000
        )
        with self.assertRaisesRegex(stage1.AuthenticationError, "replay_detected"):
            self.authenticator.authenticate(
                "POST", stage1.PROBE_PATH, replay, self.body, now=1_000
            )

        bad = self.headers(nonce="d" * 32)
        bad["X-Market-Signature"] = "0" * 64
        with self.assertRaisesRegex(stage1.AuthenticationError, "invalid_signature"):
            self.authenticator.authenticate(
                "POST", stage1.PROBE_PATH, bad, self.body, now=1_000
            )

        with self.assertRaisesRegex(stage1.AuthenticationError, "unknown_key"):
            self.authenticator.authenticate(
                "POST",
                stage1.PROBE_PATH,
                self.headers(key_id="retired", nonce="e" * 32),
                self.body,
                now=1_000,
            )

        with self.assertRaisesRegex(stage1.AuthenticationError, "clock_skew"):
            self.authenticator.authenticate(
                "POST",
                stage1.PROBE_PATH,
                self.headers(timestamp=969, nonce="f" * 32),
                self.body,
                now=1_000,
            )

    def test_private_endpoint_validation_rejects_public_loopback_and_same_peer(self):
        stage1.validate_private_endpoint("10.240.1.10", "10.240.1.20", 18443)
        for bind, peer in (
            ("65.109.216.187", "10.240.1.20"),
            ("127.0.0.1", "10.240.1.20"),
            ("10.240.1.10", "10.240.1.10"),
        ):
            with self.assertRaises(stage1.Stage1Error):
                stage1.validate_private_endpoint(bind, peer, 18443)

    def test_short_key_is_rejected_without_rendering_key_material(self):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "key"
            key_path.write_bytes(b"private-but-too-short")
            with self.assertRaisesRegex(stage1.Stage1Error, "hmac_key_too_short") as caught:
                stage1.load_key(key_path)
        self.assertNotIn("private-but-too-short", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
