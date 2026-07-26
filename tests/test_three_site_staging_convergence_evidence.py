from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.sync_parity import build_table_parity_snapshot
from scripts.build_three_site_staging_convergence_evidence import (
    ConvergenceEvidenceError,
    build,
)
from scripts.collect_three_site_staging_migration_observation import (
    ObservationError,
    validate_convergence_artifact,
)
from scripts.export_three_site_staging_convergence_snapshot import (
    ConvergenceExportError,
    _descriptor,
)
from scripts.run_three_site_staging_convergence_observer import _put_descriptor


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
PLAN_SHA = "b" * 64
SITES = ("bot_fi", "webapp_fi", "webapp_ir")


def _parity(channel_message_id: int) -> dict:
    return {
        "status": "ok",
        "schema_version": 1,
        "mode": "deep",
        "tables": {
            "offers": build_table_parity_snapshot(
                "offers",
                [
                    {
                        "id": 1,
                        "offer_public_id": "ofr_convergence",
                        "price": 100,
                        "channel_message_id": channel_message_id,
                    }
                ],
            )
        },
    }


def _snapshot(site: str, *, channel_message_id: int = 1) -> dict:
    return {
        "schema": "three-site-staging-convergence-site-snapshot-v1",
        "campaign_id": CAMPAIGN_ID,
        "release_sha": RELEASE_SHA,
        "plan_sha256": PLAN_SHA,
        "site": site,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "producer_epoch": 1,
        "source_streams": [
            {
                "destination_site": destination,
                "source_sequence": 0,
                "source_transaction_hash": "0" * 64,
            }
            for destination in SITES
            if destination != site
        ],
        "destination_streams": [],
        "unresolved_conflict_count": 0,
        "database_snapshot": _parity(channel_message_id),
        "blob_records": [],
    }


class ThreeSiteConvergenceEvidenceTests(unittest.TestCase):
    def _build(self):
        return build(
            raw_snapshots={
                "bot_fi": _snapshot("bot_fi", channel_message_id=1),
                "webapp_fi": _snapshot("webapp_fi", channel_message_id=2),
                "webapp_ir": _snapshot("webapp_ir", channel_message_id=3),
            },
            campaign_id=CAMPAIGN_ID,
            release_sha=RELEASE_SHA,
            plan_sha256=PLAN_SHA,
        )

    def test_builder_keeps_raw_site_differences_but_requires_business_exactness(self):
        artifacts = self._build()
        database = artifacts["database_parity"]
        self.assertEqual(database["status"], "equivalent")
        self.assertEqual(database["mismatch_count"], 0)
        self.assertTrue(
            any(item["difference_count"] > 0 for item in database["comparisons"])
        )
        for artifact, label in (
            (artifacts["event_checkpoint"], "event checkpoint"),
            (database, "database parity"),
            (artifacts["blob_parity"], "blob parity"),
        ):
            self.assertIs(
                validate_convergence_artifact(
                    artifact,
                    label=label,
                    campaign_id=CAMPAIGN_ID,
                    release_sha=RELEASE_SHA,
                    plan_sha256=PLAN_SHA,
                ),
                artifact,
            )

    def test_builder_rejects_business_drift(self):
        snapshots = {
            "bot_fi": _snapshot("bot_fi"),
            "webapp_fi": _snapshot("webapp_fi"),
            "webapp_ir": _snapshot("webapp_ir"),
        }
        snapshots["webapp_ir"]["database_snapshot"] = {
            **_parity(1),
            "tables": {
                "offers": build_table_parity_snapshot(
                    "offers",
                    [{"id": 1, "offer_public_id": "ofr_convergence", "price": 101}],
                )
            },
        }
        with self.assertRaisesRegex(ConvergenceEvidenceError, "business/critical drift"):
            build(
                raw_snapshots=snapshots,
                campaign_id=CAMPAIGN_ID,
                release_sha=RELEASE_SHA,
                plan_sha256=PLAN_SHA,
            )

    def test_builder_rejects_event_checkpoint_gap(self):
        snapshots = {site: _snapshot(site) for site in SITES}
        snapshots["bot_fi"]["source_streams"][0]["source_sequence"] = 1
        snapshots["bot_fi"]["source_streams"][0]["source_transaction_hash"] = "c" * 64
        with self.assertRaisesRegex(ConvergenceEvidenceError, "not exactly applied"):
            build(
                raw_snapshots=snapshots,
                campaign_id=CAMPAIGN_ID,
                release_sha=RELEASE_SHA,
                plan_sha256=PLAN_SHA,
            )

    def test_builder_rejects_stale_or_skewed_site_observations(self):
        snapshots = {site: _snapshot(site) for site in SITES}
        now = datetime.now(timezone.utc)
        snapshots["webapp_ir"]["observed_at"] = (now - timedelta(minutes=6)).isoformat()
        with self.assertRaisesRegex(ConvergenceEvidenceError, "stale"):
            build(
                raw_snapshots=snapshots,
                campaign_id=CAMPAIGN_ID,
                release_sha=RELEASE_SHA,
                plan_sha256=PLAN_SHA,
                now=now,
            )

        snapshots = {site: _snapshot(site) for site in SITES}
        snapshots["webapp_ir"]["observed_at"] = (now - timedelta(minutes=3)).isoformat()
        with self.assertRaisesRegex(ConvergenceEvidenceError, "capture-skew"):
            build(
                raw_snapshots=snapshots,
                campaign_id=CAMPAIGN_ID,
                release_sha=RELEASE_SHA,
                plan_sha256=PLAN_SHA,
                now=now,
            )

    def test_validator_rejects_mismatched_business_fingerprints(self):
        artifact = self._build()["database_parity"]
        artifact["comparisons"][0]["target_business_fingerprint_sha256"] = "f" * 64
        with self.assertRaisesRegex(ObservationError, "business-exact"):
            validate_convergence_artifact(
                artifact,
                label="database parity",
                campaign_id=CAMPAIGN_ID,
                release_sha=RELEASE_SHA,
                plan_sha256=PLAN_SHA,
            )

    def test_iran_export_descriptor_is_short_lived_and_campaign_bound(self):
        class Client:
            def generate_presigned_url(self, operation, *, Params, ExpiresIn, HttpMethod):
                self.operation = operation
                self.params = Params
                self.ttl = ExpiresIn
                self.method = HttpMethod
                return "https://s3.ir-thr-at1.arvanstorage.ir/private/snapshot?signature=temporary"

        client = Client()
        config = {
            "campaign_id": CAMPAIGN_ID,
            "release_sha": RELEASE_SHA,
            "plan_sha256": PLAN_SHA,
            "limits": {"url_ttl_seconds": 300},
        }
        encoded = _put_descriptor(config, client=client, bucket="staging-private", key="x/y.json")
        upload = _descriptor(
            encoded,
            campaign_id=CAMPAIGN_ID,
            release_sha=RELEASE_SHA,
            plan_sha256=PLAN_SHA,
        )
        self.assertEqual(client.operation, "put_object")
        self.assertEqual(client.method, "PUT")
        self.assertEqual(client.ttl, 300)
        self.assertEqual(upload["method"], "PUT")
        with self.assertRaises(ConvergenceExportError):
            _descriptor(
                encoded,
                campaign_id="22222222-2222-4222-8222-222222222222",
                release_sha=RELEASE_SHA,
                plan_sha256=PLAN_SHA,
            )


if __name__ == "__main__":
    unittest.main()
