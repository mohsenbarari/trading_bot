import stat
import tempfile
import unittest
from pathlib import Path

from scripts.arvan_origin_switch import (
    ArvanOriginSwitchError,
    append_audit_event,
    confirmation_phrase,
    inspect_or_switch,
    load_token,
)


def record(ip: str) -> dict:
    return {
        "id": "record-1",
        "type": "a",
        "name": "switch-test",
        "value": [{"ip": ip, "port": None, "weight": 100, "country": ""}],
        "ttl": 120,
        "cloud": True,
        "upstream_https": "https",
        "ip_filter_mode": {"count": "single", "order": "none", "geo_filter": "none"},
    }


class FakeApi:
    def __init__(self, current_ip: str = "10.0.0.1") -> None:
        self.current = record(current_ip)
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, url: str, token: str, payload: dict | None) -> dict:
        self.calls.append((method, url, payload))
        self.assert_token_not_recorded(token)
        if method == "GET":
            return {"data": [dict(self.current)]}
        if method == "PUT":
            assert payload is not None
            self.current = {**self.current, **payload}
            return {"data": dict(self.current)}
        raise AssertionError(method)

    def assert_token_not_recorded(self, token: str) -> None:
        if token != "secret":
            raise AssertionError("unexpected token")


class ArvanOriginSwitchTests(unittest.TestCase):
    def test_dry_run_never_writes(self) -> None:
        fake = FakeApi()
        result = inspect_or_switch(
            domain="gold-trading.ir",
            record_name="switch-test",
            target_ip="10.0.0.2",
            token="secret",
            expected_current_ip=None,
            apply=False,
            confirmation=None,
            request_fn=fake,
        )
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["applied"])
        self.assertEqual([call[0] for call in fake.calls], ["GET"])

    def test_apply_requires_expected_current_ip(self) -> None:
        with self.assertRaisesRegex(ArvanOriginSwitchError, "expected-current-ip"):
            inspect_or_switch(
                domain="gold-trading.ir",
                record_name="switch-test",
                target_ip="10.0.0.2",
                token="secret",
                expected_current_ip=None,
                apply=True,
                confirmation=confirmation_phrase("gold-trading.ir", "switch-test", "10.0.0.2"),
                request_fn=FakeApi(),
            )

    def test_apply_aborts_if_current_origin_changed(self) -> None:
        fake = FakeApi("10.0.0.9")
        with self.assertRaisesRegex(ArvanOriginSwitchError, "explicitly expected"):
            inspect_or_switch(
                domain="gold-trading.ir",
                record_name="switch-test",
                target_ip="10.0.0.2",
                token="secret",
                expected_current_ip="10.0.0.1",
                apply=True,
                confirmation=confirmation_phrase("gold-trading.ir", "switch-test", "10.0.0.2"),
                request_fn=fake,
            )
        self.assertEqual([call[0] for call in fake.calls], ["GET"])

    def test_apply_requires_exact_confirmation(self) -> None:
        fake = FakeApi()
        with self.assertRaisesRegex(ArvanOriginSwitchError, "Confirmation mismatch"):
            inspect_or_switch(
                domain="gold-trading.ir",
                record_name="switch-test",
                target_ip="10.0.0.2",
                token="secret",
                expected_current_ip="10.0.0.1",
                apply=True,
                confirmation="wrong",
                request_fn=fake,
            )
        self.assertEqual([call[0] for call in fake.calls], ["GET"])

    def test_apply_preserves_proxy_safety_and_verifies_readback(self) -> None:
        fake = FakeApi()
        result = inspect_or_switch(
            domain="gold-trading.ir",
            record_name="switch-test",
            target_ip="10.0.0.2",
            token="secret",
            expected_current_ip="10.0.0.1",
            apply=True,
            confirmation=confirmation_phrase("gold-trading.ir", "switch-test", "10.0.0.2"),
            request_fn=fake,
        )
        self.assertEqual(result["status"], "switched")
        self.assertTrue(result["applied"])
        self.assertEqual([call[0] for call in fake.calls], ["GET", "PUT", "GET"])
        put_payload = fake.calls[1][2]
        self.assertEqual(put_payload["value"][0]["ip"], "10.0.0.2")
        self.assertTrue(put_payload["cloud"])
        self.assertEqual(put_payload["upstream_https"], "https")
        self.assertEqual(put_payload["ttl"], 120)

    def test_already_at_target_is_idempotent(self) -> None:
        fake = FakeApi("10.0.0.2")
        result = inspect_or_switch(
            domain="gold-trading.ir",
            record_name="switch-test",
            target_ip="10.0.0.2",
            token="secret",
            expected_current_ip="10.0.0.1",
            apply=True,
            confirmation="unused",
            request_fn=fake,
        )
        self.assertEqual(result["status"], "already_at_target")
        self.assertEqual([call[0] for call in fake.calls], ["GET"])

    def test_token_file_must_be_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = Path(tmpdir) / "token"
            token_path.write_text("secret\n", encoding="utf-8")
            token_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with self.assertRaisesRegex(ArvanOriginSwitchError, "permissions are too broad"):
                load_token(token_path)
            token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self.assertEqual(load_token(token_path), "secret")

    def test_audit_log_is_owner_only_and_contains_no_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit" / "origin.jsonl"
            append_audit_event(
                audit_path,
                {"event": "arvan.origin_switch.applied", "target_ip": "10.0.0.2"},
            )
            self.assertEqual(stat.S_IMODE(audit_path.stat().st_mode), 0o600)
            contents = audit_path.read_text(encoding="utf-8")
            self.assertIn("arvan.origin_switch.applied", contents)
            self.assertNotIn("secret", contents)


if __name__ == "__main__":
    unittest.main()
