import unittest

from scripts import sync_probe_worker as worker


def make_snapshot(prefix="p11b-", status="completed", counts=None):
    return {
        "schema_version": worker.OFFER_SYNC_SNAPSHOT_SCHEMA_VERSION,
        "captured_at": "2026-06-20T00:00:00+00:00",
        "server_mode": "foreign",
        "prefix": prefix,
        "evidence_tables": list(worker.OFFER_SYNC_TABLES),
        "messenger_tables_included": [],
        "table_counts": counts
        or {
            "offers": 1,
            "trades": 1,
            "offer_requests": 1,
            "offer_publication_states": 2,
        },
        "offer_status_counts": {status: 1},
        "trade_status_counts": {"completed": 1},
        "offer_request_status_counts": {"completed_trade": 1},
        "offer_request_surface_counts": {"webapp": 1},
        "publication_status_counts": {"visible": 1, "sent": 1},
        "publication_surface_counts": {"webapp_market": 1, "telegram_channel": 1},
        "completed_trade_quantity": 5,
        "offers": [
            {
                "id": 11,
                "offer_public_id": "ofr_step11b",
                "status": status,
                "home_server": "foreign",
                "version_id": 3,
                "remaining_quantity": 0,
            }
        ],
    }


def clean_health(server_mode):
    return {
        "status": "ok",
        "server_mode": server_mode,
        "redis_ok": True,
        "unsynced_change_log_count": 0,
        "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
    }


def make_artifact(duration=1.0, foreign_snapshot=None, iran_snapshot=None):
    return {
        "schema_version": worker.BOT_WEBAPP_SYNC_EVIDENCE_SCHEMA_VERSION,
        "checks": {
            name: {"ok": True, "duration_seconds": duration}
            for name in worker.REQUIRED_BOT_WEBAPP_SYNC_CHECKS
        },
        "server_snapshots": {
            "foreign": foreign_snapshot or make_snapshot(),
            "iran": iran_snapshot or make_snapshot(),
        },
        "sync_health": {
            "foreign": clean_health("foreign"),
            "iran": clean_health("iran"),
        },
    }


class BotWebAppSyncEvidenceTests(unittest.TestCase):
    def test_offer_sync_evidence_tables_exclude_messenger_tables(self):
        tables = worker.offer_sync_evidence_tables()

        self.assertEqual(tables, worker.OFFER_SYNC_TABLES)
        self.assertNotIn("messages", tables)
        self.assertNotIn("chat_members", tables)

        with self.assertRaises(worker.SyncProbeError):
            worker.assert_no_messenger_tables_in_evidence(["offers", "messages"])

    def test_offer_sync_snapshots_match_counts_and_terminal_status(self):
        worker.assert_offer_sync_snapshots_match(make_snapshot(), make_snapshot())

        mismatch = make_snapshot(
            counts={
                "offers": 1,
                "trades": 0,
                "offer_requests": 1,
                "offer_publication_states": 2,
            }
        )
        with self.assertRaises(worker.SyncProbeError):
            worker.assert_offer_sync_snapshots_match(make_snapshot(), mismatch)

        reactivated = make_snapshot(status="active")
        with self.assertRaises(worker.SyncProbeError):
            worker.assert_offer_sync_snapshots_match(make_snapshot(status="completed"), reactivated)

    def test_cross_server_sync_evidence_artifact_validates_lag_and_health(self):
        worker.validate_cross_server_sync_evidence_artifact(make_artifact(duration=1.5), accepted_lag_seconds=2.0)

        with self.assertRaises(worker.SyncProbeError):
            worker.validate_cross_server_sync_evidence_artifact(make_artifact(duration=2.5), accepted_lag_seconds=2.0)

        dirty = make_artifact()
        dirty["sync_health"]["iran"]["redis_queues"]["sync:retry"] = 1
        with self.assertRaises(worker.SyncProbeError):
            worker.validate_cross_server_sync_evidence_artifact(dirty, accepted_lag_seconds=2.0)

    def test_cross_server_sync_evidence_artifact_requires_all_checks(self):
        artifact = make_artifact()
        del artifact["checks"]["stale_replay_terminal_guard"]

        with self.assertRaises(worker.SyncProbeError):
            worker.validate_cross_server_sync_evidence_artifact(artifact, accepted_lag_seconds=2.0)


if __name__ == "__main__":
    unittest.main()
