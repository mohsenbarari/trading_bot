import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.manage_three_site_mvp_arvan_acme_dns import (
    CERTIFICATE_DOMAIN,
    CHALLENGE_RECORD,
    AcmeDnsError,
    cleanup,
    present,
)


VALIDATION = "a" * 43


class FakeApi:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, url: str, token: str, payload: dict | None) -> dict:
        if token != "secret":
            raise AssertionError("unexpected token")
        self.calls.append((method, url, copy.deepcopy(payload)))
        if method == "POST":
            if payload is None:
                raise AssertionError("expected payload")
            self.records.append(
                {
                    "id": f"record-{len(self.records) + 1}",
                    "type": payload["type"].lower(),
                    "name": payload["name"],
                    "value": copy.deepcopy(payload["value"]),
                    "ttl": payload["ttl"],
                }
            )
            return {"data": copy.deepcopy(self.records[-1])}
        if method == "GET":
            return {"data": copy.deepcopy(self.records)}
        if method == "DELETE":
            record_id = url.rsplit("/", 1)[-1]
            self.records = [record for record in self.records if record["id"] != record_id]
            return {"data": {"id": record_id}}
        raise AssertionError(f"unexpected method {method}")


class ArvanAcmeDnsTests(unittest.TestCase):
    def test_present_creates_only_expected_txt_and_writes_private_state(self) -> None:
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            result = present(
                domain=CERTIFICATE_DOMAIN,
                validation=VALIDATION,
                token="secret",
                state_dir=state_dir,
                request_fn=fake,
                propagation_seconds=0,
            )

            self.assertEqual(result["record_name"], CHALLENGE_RECORD)
            self.assertEqual([call[0] for call in fake.calls], ["POST", "GET"])
            self.assertEqual(fake.calls[0][2], {
                "type": "TXT",
                "name": CHALLENGE_RECORD,
                "value": {"text": VALIDATION},
                "ttl": 120,
            })
            state_path = state_dir / "coin.gold-trade.ir.json"
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["record_id"], "record-1")

    def test_present_is_idempotent_for_the_same_validation(self) -> None:
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            present(
                domain=CERTIFICATE_DOMAIN,
                validation=VALIDATION,
                token="secret",
                state_dir=state_dir,
                request_fn=fake,
                propagation_seconds=0,
            )
            result = present(
                domain=CERTIFICATE_DOMAIN,
                validation=VALIDATION,
                token="secret",
                state_dir=state_dir,
                request_fn=fake,
                propagation_seconds=0,
            )

            self.assertEqual(result["record_id"], "record-1")
            self.assertEqual([call[0] for call in fake.calls], ["POST", "GET"])

    def test_cleanup_verifies_exact_record_before_delete(self) -> None:
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            present(
                domain=CERTIFICATE_DOMAIN,
                validation=VALIDATION,
                token="secret",
                state_dir=state_dir,
                request_fn=fake,
                propagation_seconds=0,
            )
            result = cleanup(
                domain=CERTIFICATE_DOMAIN,
                validation=VALIDATION,
                token="secret",
                state_dir=state_dir,
                request_fn=fake,
            )

            self.assertEqual(result["record_id"], "record-1")
            self.assertEqual([call[0] for call in fake.calls], ["POST", "GET", "GET", "DELETE", "GET"])
            self.assertEqual(fake.records, [])
            self.assertFalse((state_dir / "coin.gold-trade.ir.json").exists())

    def test_cleanup_refuses_to_delete_replaced_record(self) -> None:
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            present(
                domain=CERTIFICATE_DOMAIN,
                validation=VALIDATION,
                token="secret",
                state_dir=state_dir,
                request_fn=fake,
                propagation_seconds=0,
            )
            fake.records[0]["value"] = {"text": "b" * 43}
            with self.assertRaisesRegex(AcmeDnsError, "exactly one matching"):
                cleanup(
                    domain=CERTIFICATE_DOMAIN,
                    validation=VALIDATION,
                    token="secret",
                    state_dir=state_dir,
                    request_fn=fake,
                )
            self.assertEqual([call[0] for call in fake.calls], ["POST", "GET", "GET"])

    def test_other_domain_is_rejected_before_api_access(self) -> None:
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(AcmeDnsError, "restricted"):
                present(
                    domain="example.invalid",
                    validation=VALIDATION,
                    token="secret",
                    state_dir=Path(tmpdir) / "state",
                    request_fn=fake,
                    propagation_seconds=0,
                )
        self.assertEqual(fake.calls, [])

    def test_state_file_must_remain_private(self) -> None:
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            present(
                domain=CERTIFICATE_DOMAIN,
                validation=VALIDATION,
                token="secret",
                state_dir=state_dir,
                request_fn=fake,
                propagation_seconds=0,
            )
            state_path = state_dir / "coin.gold-trade.ir.json"
            state_path.chmod(0o640)
            with self.assertRaisesRegex(AcmeDnsError, "private regular"):
                cleanup(
                    domain=CERTIFICATE_DOMAIN,
                    validation=VALIDATION,
                    token="secret",
                    state_dir=state_dir,
                    request_fn=fake,
                )


if __name__ == "__main__":
    unittest.main()
