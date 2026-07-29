import copy
from datetime import datetime, timedelta, timezone
import json
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.manage_three_site_mvp_arvan_routing import (
    PRODUCTION_RECORD,
    SITE_ORIGINS,
    _canonical_json_bytes,
    _sha256,
)
from scripts.route_webapp_ir_from_promotion_proof import (
    PromotionRouteError,
    route_from_latest_proof,
    select_latest_fresh_promotion_proof,
)


class FakeApi:
    def __init__(self) -> None:
        self.current = {
            "id": "record-1",
            "type": "a",
            "name": PRODUCTION_RECORD,
            "value": [{"ip": SITE_ORIGINS["webapp_fi"], "port": None, "weight": 100, "country": ""}],
            "ttl": 120,
            "cloud": True,
            "upstream_https": "https",
        }
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, url: str, token: str, payload: dict | None) -> dict:
        if token != "secret":
            raise AssertionError("unexpected token")
        self.calls.append((method, url, copy.deepcopy(payload)))
        if method == "GET":
            return {"data": [copy.deepcopy(self.current)]}
        if method == "PUT":
            if payload is None:
                raise AssertionError("missing update payload")
            self.current = {**self.current, **payload}
            return {"data": copy.deepcopy(self.current)}
        raise AssertionError(method)


def proof_for(now: datetime, *, age_seconds: int = 2) -> dict:
    capture_at = now - timedelta(seconds=max(age_seconds - 1, 0))
    proof = {
        "schema": "gold-trade-writer-promotion-proof-v1",
        "action": "promote_ir",
        "operation_id": "b1d8e9a0-0b0c-4d0e-8f00-000000000001",
        "source_site": "webapp_fi",
        "target_site": "webapp_ir",
        "snapshot_id": "snapshot-1",
        "source_generation": "generation-1",
        "release_sha": "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        "alembic_revision": "f2c7d8e9a0b1",
        "snapshot_age_seconds": age_seconds,
        "source_db_snapshot_started_at": (now - timedelta(seconds=age_seconds)).isoformat(),
        "source_capture_completed_at": capture_at.isoformat(),
        "snapshot_published_at": capture_at.isoformat(),
        "snapshot_ready_at": now.isoformat(),
        "snapshot_restore_receipt_sha256": "a" * 64,
        "snapshot_stage_receipt_sha256": "b" * 64,
        "lease_id": "lease-1",
        "epoch": 1,
        "issued_at": now.isoformat(),
        "lease_expires_at": (now + timedelta(seconds=120)).isoformat(),
        "witness_proof_sha256": "c" * 64,
    }
    proof["proof_sha256"] = _sha256(_canonical_json_bytes(proof))
    return proof


def write_proof(directory: Path, proof: dict, *, suffix: str = "d" * 64) -> Path:
    directory.mkdir(mode=0o700)
    path = directory / f"promote_ir-{proof['snapshot_id']}-{suffix}.json"
    path.write_text(json.dumps(proof), encoding="utf-8")
    path.chmod(0o600)
    return path


def write_listener_receipt(directory: Path, proof: dict, *, activated_at: datetime) -> Path:
    path = directory / "webapp-ir-promoted-listener.json"
    payload = {
        "schema": "gold-trade-wa-ir-promoted-listener-activation-v1",
        "status": "reloaded",
        "release_sha": proof["release_sha"],
        "server_name": "coin.gold-trade.ir",
        "loopback_upstream": "http://127.0.0.1:18000",
        "site_config_sha256": "e" * 64,
        "certificate_sha256": "f" * 64,
        "activated_at": activated_at.isoformat(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


class RouteWebappIrFromPromotionProofTests(unittest.TestCase):
    def test_selects_latest_fresh_proof_and_routes_after_readback(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir) / "proofs"
            proof = proof_for(now - timedelta(seconds=32))
            write_proof(directory, proof)
            listener_receipt = write_listener_receipt(directory, proof, activated_at=now)

            result = route_from_latest_proof(
                proof_directory=directory,
                listener_receipt=listener_receipt,
                token="secret",
                apply=True,
                request_fn=fake,
                now=now,
            )

        self.assertEqual(result["status"], "switched")
        self.assertTrue(result["applied"])
        self.assertEqual([call[0] for call in fake.calls], ["GET", "PUT", "GET"])
        self.assertEqual(fake.current["value"][0]["ip"], SITE_ORIGINS["webapp_ir"])

    def test_ignores_stale_proof_without_contacting_arvan(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir) / "proofs"
            write_proof(directory, proof_for(now - timedelta(seconds=121), age_seconds=2))

            result = route_from_latest_proof(
                proof_directory=directory,
                listener_receipt=directory / "webapp-ir-promoted-listener.json",
                token="secret",
                apply=True,
                request_fn=fake,
                now=now,
            )

        self.assertEqual(result, {"status": "no_fresh_promotion_proof", "applied": False})
        self.assertEqual(fake.calls, [])

    def test_refuses_to_route_without_a_fresh_listener_receipt(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        fake = FakeApi()
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir) / "proofs"
            proof = proof_for(now - timedelta(seconds=32))
            write_proof(directory, proof)
            listener_receipt = write_listener_receipt(
                directory,
                proof,
                activated_at=now - timedelta(seconds=31),
            )

            with self.assertRaisesRegex(PromotionRouteError, "listener receipt is stale"):
                route_from_latest_proof(
                    proof_directory=directory,
                    listener_receipt=listener_receipt,
                    token="secret",
                    apply=True,
                    request_fn=fake,
                    now=now,
                )

        self.assertEqual(fake.calls, [])

    def test_rejects_group_readable_proof_file(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir) / "proofs"
            path = write_proof(directory, proof_for(now))
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with self.assertRaisesRegex(PromotionRouteError, "not root-owned and private"):
                select_latest_fresh_promotion_proof(directory, now=now)

    def test_ignores_nonmatching_files(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir) / "proofs"
            directory.mkdir(mode=0o700)
            (directory / "notes.txt").write_text("ignore", encoding="utf-8")
            self.assertIsNone(select_latest_fresh_promotion_proof(directory, now=now))


if __name__ == "__main__":
    unittest.main()
