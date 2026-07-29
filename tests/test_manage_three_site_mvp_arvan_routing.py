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
    ThreeSiteRoutingError,
    _canonical_json_bytes,
    _sha256,
    inspect_or_route,
    load_promotion_proof,
    load_token,
    verify_promotion_proof,
)


def record(ip: str, *, cloud: bool) -> dict:
    return {
        "id": "record-1",
        "type": "a",
        "name": PRODUCTION_RECORD,
        "value": [{"ip": ip, "port": None, "weight": 100, "country": ""}],
        "ttl": 120,
        "cloud": cloud,
        "upstream_https": "https",
        "ip_filter_mode": {"count": "single", "order": "none", "geo_filter": "none"},
    }


class FakeApi:
    def __init__(self, *, current_ip: str, cloud: bool = True) -> None:
        self.current = record(current_ip, cloud=cloud)
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, url: str, token: str, payload: dict | None) -> dict:
        if token != "secret":
            raise AssertionError("unexpected token")
        self.calls.append((method, url, copy.deepcopy(payload)))
        if method == "GET":
            return {"data": [copy.deepcopy(self.current)]}
        if method == "PUT":
            if payload is None:
                raise AssertionError("expected update payload")
            self.current = {**self.current, **payload}
            return {"data": copy.deepcopy(self.current)}
        raise AssertionError(f"unexpected method {method}")


def proof_for(
    *,
    target_site: str = "webapp_ir",
    now: datetime | None = None,
    snapshot_age_seconds: int = 2,
) -> dict:
    reference = now or datetime.now(timezone.utc)
    source_site = "webapp_fi" if target_site == "webapp_ir" else "webapp_ir"
    action = "promote_ir" if target_site == "webapp_ir" else "failback_fi"
    capture_time = reference - timedelta(seconds=max(snapshot_age_seconds - 1, 0))
    proof = {
        "schema": "gold-trade-writer-promotion-proof-v1",
        "action": action,
        "operation_id": "b1d8e9a0-0b0c-4d0e-8f00-000000000001",
        "source_site": source_site,
        "target_site": target_site,
        "snapshot_id": "snapshot-1",
        "source_generation": "generation-1",
        "release_sha": "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        "alembic_revision": "f2c7d8e9a0b1",
        "snapshot_age_seconds": snapshot_age_seconds,
        "snapshot_published_at": capture_time.isoformat(),
        "source_db_snapshot_started_at": (reference - timedelta(seconds=snapshot_age_seconds)).isoformat(),
        "source_capture_completed_at": capture_time.isoformat(),
        "snapshot_ready_at": reference.isoformat(),
        "snapshot_restore_receipt_sha256": "a" * 64,
        "snapshot_stage_receipt_sha256": "b" * 64,
        "lease_id": "lease-1",
        "epoch": 1,
        "issued_at": reference.isoformat(),
        "lease_expires_at": (reference + timedelta(seconds=120)).isoformat(),
        "witness_proof_sha256": "c" * 64,
    }
    proof["proof_sha256"] = _sha256(_canonical_json_bytes(proof))
    return proof


class ThreeSiteMvpArvanRoutingTests(unittest.TestCase):
    def test_verify_proof_accepts_exact_canonical_contract(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        proof = proof_for(now=now)

        summary = verify_promotion_proof(proof, target_site="webapp_ir", now=now)

        self.assertEqual(summary["target_ip"], SITE_ORIGINS["webapp_ir"])
        self.assertEqual(summary["action"], "promote_ir")
        self.assertEqual(summary["proof_sha256"], proof["proof_sha256"])

    def test_verify_proof_rejects_unknown_field_and_bad_hash(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        proof = proof_for(now=now)
        proof["unexpected"] = "nope"
        with self.assertRaisesRegex(ThreeSiteRoutingError, "unexpected field set"):
            verify_promotion_proof(proof, target_site="webapp_ir", now=now)

        proof = proof_for(now=now)
        proof["proof_sha256"] = "0" * 64
        with self.assertRaisesRegex(ThreeSiteRoutingError, "SHA-256"):
            verify_promotion_proof(proof, target_site="webapp_ir", now=now)

    def test_verify_proof_rejects_other_release_before_routing(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        proof = proof_for(now=now)
        proof["release_sha"] = "a" * 40
        unsigned = dict(proof)
        unsigned.pop("proof_sha256")
        proof["proof_sha256"] = _sha256(_canonical_json_bytes(unsigned))

        with self.assertRaisesRegex(ThreeSiteRoutingError, "does not match the deployed MVP release"):
            verify_promotion_proof(proof, target_site="webapp_ir", now=now)

    def test_verify_proof_rejects_stale_database_snapshot_even_if_proof_is_new(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        proof = proof_for(now=now, snapshot_age_seconds=151)
        proof["source_capture_completed_at"] = (now - timedelta(seconds=150)).isoformat()
        proof["snapshot_published_at"] = (now - timedelta(seconds=150)).isoformat()
        proof["snapshot_ready_at"] = (now - timedelta(seconds=121)).isoformat()
        unsigned = dict(proof)
        unsigned.pop("proof_sha256")
        proof["proof_sha256"] = _sha256(_canonical_json_bytes(unsigned))

        with self.assertRaisesRegex(ThreeSiteRoutingError, "older than the allowed recovery point"):
            verify_promotion_proof(proof, target_site="webapp_ir", now=now)

    def test_verify_proof_rejects_a_candidate_that_exceeded_the_stage_bound(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        proof = proof_for(now=now, snapshot_age_seconds=31)
        unsigned = dict(proof)
        unsigned.pop("proof_sha256")
        proof["proof_sha256"] = _sha256(_canonical_json_bytes(unsigned))

        with self.assertRaisesRegex(ThreeSiteRoutingError, "staged within"):
            verify_promotion_proof(proof, target_site="webapp_ir", now=now)

    def test_verify_proof_rejects_a_term_that_cannot_survive_the_route_change(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        proof = proof_for(now=now)
        proof["lease_expires_at"] = (now + timedelta(seconds=5)).isoformat()
        unsigned = dict(proof)
        unsigned.pop("proof_sha256")
        proof["proof_sha256"] = _sha256(_canonical_json_bytes(unsigned))

        with self.assertRaisesRegex(ThreeSiteRoutingError, "too close to expiry"):
            verify_promotion_proof(proof, target_site="webapp_ir", now=now)

    def test_normal_route_requires_witness_proof_before_api_access(self) -> None:
        fake = FakeApi(current_ip=SITE_ORIGINS["webapp_fi"])

        with self.assertRaisesRegex(ThreeSiteRoutingError, "promotion proof is mandatory"):
            inspect_or_route(
                target_site="webapp_ir",
                token="secret",
                expected_current_ip=SITE_ORIGINS["webapp_fi"],
                apply=True,
                bootstrap_proxy=False,
                proof=None,
                request_fn=fake,
            )

        self.assertEqual(fake.calls, [])

    def test_route_switches_only_after_readback_and_proof(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        fake = FakeApi(current_ip=SITE_ORIGINS["webapp_fi"])

        result = inspect_or_route(
            target_site="webapp_ir",
            token="secret",
            expected_current_ip=SITE_ORIGINS["webapp_fi"],
            apply=True,
            bootstrap_proxy=False,
            proof=proof_for(now=now),
            request_fn=fake,
            now=now,
        )

        self.assertEqual(result["status"], "switched")
        self.assertTrue(result["applied"])
        self.assertEqual([method for method, _, _ in fake.calls], ["GET", "PUT", "GET"])
        self.assertEqual(fake.current["value"][0]["ip"], SITE_ORIGINS["webapp_ir"])
        self.assertTrue(fake.current["cloud"])

    def test_proxy_bootstrap_can_only_keep_existing_fi_origin(self) -> None:
        fake = FakeApi(current_ip=SITE_ORIGINS["webapp_fi"], cloud=False)

        result = inspect_or_route(
            target_site="webapp_fi",
            token="secret",
            expected_current_ip=SITE_ORIGINS["webapp_fi"],
            apply=True,
            bootstrap_proxy=True,
            proof=None,
            request_fn=fake,
        )

        self.assertEqual(result["status"], "switched")
        self.assertTrue(fake.current["cloud"])
        self.assertEqual([method for method, _, _ in fake.calls], ["GET", "PUT", "GET"])

    def test_proxy_bootstrap_refuses_ir_before_api_access(self) -> None:
        fake = FakeApi(current_ip=SITE_ORIGINS["webapp_fi"], cloud=False)

        with self.assertRaisesRegex(ThreeSiteRoutingError, "only retain WA-FI"):
            inspect_or_route(
                target_site="webapp_ir",
                token="secret",
                expected_current_ip=SITE_ORIGINS["webapp_fi"],
                apply=True,
                bootstrap_proxy=True,
                proof=None,
                request_fn=fake,
            )

        self.assertEqual(fake.calls, [])

    def test_normal_switch_refuses_unproxied_record(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        fake = FakeApi(current_ip=SITE_ORIGINS["webapp_fi"], cloud=False)

        with self.assertRaisesRegex(ThreeSiteRoutingError, "already proxied"):
            inspect_or_route(
                target_site="webapp_ir",
                token="secret",
                expected_current_ip=SITE_ORIGINS["webapp_fi"],
                apply=True,
                bootstrap_proxy=False,
                proof=proof_for(now=now),
                request_fn=fake,
                now=now,
            )

        self.assertEqual([method for method, _, _ in fake.calls], ["GET"])

    def test_dry_run_never_writes(self) -> None:
        fake = FakeApi(current_ip=SITE_ORIGINS["webapp_fi"])

        result = inspect_or_route(
            target_site="webapp_ir",
            token="secret",
            expected_current_ip=None,
            apply=False,
            bootstrap_proxy=False,
            proof=None,
            request_fn=fake,
        )

        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["applied"])
        self.assertEqual([method for method, _, _ in fake.calls], ["GET"])

    def test_secret_and_proof_files_must_be_private_root_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            token_path = root / "token"
            token_path.write_text("secret\n", encoding="utf-8")
            token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self.assertEqual(load_token(token_path), "secret")

            token_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with self.assertRaisesRegex(ThreeSiteRoutingError, "group/world"):
                load_token(token_path)

            proof_path = root / "proof.json"
            proof_path.write_text(json.dumps(proof_for()), encoding="utf-8")
            proof_path.chmod(0o600)
            self.assertEqual(load_promotion_proof(proof_path)["schema"], "gold-trade-writer-promotion-proof-v1")
            link_path = root / "proof-link"
            link_path.symlink_to(proof_path)
            with self.assertRaisesRegex(ThreeSiteRoutingError, "regular file"):
                load_promotion_proof(link_path)


if __name__ == "__main__":
    unittest.main()
