import unittest
from unittest.mock import Mock, patch

from core.background_job_authority import (
    JOB_CONNECTIVITY_MONITOR,
    JOB_MARKET_SCHEDULE,
    JOB_OFFER_EXPIRY,
    JOB_OFFER_TELEGRAM_PUBLICATION,
    JOB_SESSION_EXPIRY,
    JOB_SYNC_WORKER,
    JOB_TRADE_TELEGRAM_DELIVERY,
    JOB_TRADE_WEBAPP_DELIVERY,
    JOB_USER_ACCOUNT_STATUS,
    REQUIRED_BACKGROUND_JOBS,
    BackgroundJobAuthorityError,
    assert_background_job_authority,
    background_job_authority_entries,
    check_background_job_authority,
    filter_allowed_background_job_factories,
)
from core.sync_registry import SyncPolicy, get_sync_registry_entry


class BackgroundJobAuthorityTests(unittest.TestCase):
    def test_required_recurring_jobs_are_declared_with_full_policy_metadata(self):
        entries = background_job_authority_entries()
        self.assertTrue(REQUIRED_BACKGROUND_JOBS.issubset(entries))

        for job_name in REQUIRED_BACKGROUND_JOBS | {JOB_SYNC_WORKER}:
            with self.subTest(job_name=job_name):
                entry = entries[job_name]
                self.assertEqual(entry.job_name, job_name)
                self.assertTrue(entry.allowed_servers)
                self.assertTrue(entry.authority_rule)
                self.assertTrue(entry.outage_behavior)
                self.assertTrue(entry.sync_outbox_behavior)

    def test_mutated_db_tables_are_registered_for_sync_policy(self):
        for job_name, entry in background_job_authority_entries().items():
            for table_name in entry.mutated_tables:
                with self.subTest(job_name=job_name, table_name=table_name):
                    self.assertEqual(get_sync_registry_entry(table_name).table_name, table_name)

    def test_jobs_refuse_disallowed_servers(self):
        user_status_decision = check_background_job_authority(
            JOB_USER_ACCOUNT_STATUS,
            server_mode="foreign",
        )
        connectivity_decision = check_background_job_authority(
            JOB_CONNECTIVITY_MONITOR,
            server_mode="foreign",
        )

        self.assertFalse(user_status_decision.ok)
        self.assertEqual(user_status_decision.reason, "background_job_not_allowed_on_server")
        self.assertFalse(connectivity_decision.ok)
        self.assertEqual(connectivity_decision.reason, "background_job_not_allowed_on_server")
        webapp_delivery_decision = check_background_job_authority(JOB_TRADE_WEBAPP_DELIVERY, server_mode="foreign")
        telegram_delivery_decision = check_background_job_authority(JOB_TRADE_TELEGRAM_DELIVERY, server_mode="iran")
        offer_publication_decision = check_background_job_authority(JOB_OFFER_TELEGRAM_PUBLICATION, server_mode="iran")
        self.assertFalse(webapp_delivery_decision.ok)
        self.assertEqual(webapp_delivery_decision.reason, "background_job_not_allowed_on_server")
        self.assertFalse(telegram_delivery_decision.ok)
        self.assertEqual(telegram_delivery_decision.reason, "background_job_not_allowed_on_server")
        self.assertFalse(offer_publication_decision.ok)
        self.assertEqual(offer_publication_decision.reason, "background_job_not_allowed_on_server")

        with self.assertRaises(BackgroundJobAuthorityError):
            assert_background_job_authority(JOB_USER_ACCOUNT_STATUS, server_mode="foreign")

    def test_allowed_jobs_pass_for_their_authoritative_servers(self):
        for job_name in {
            JOB_OFFER_EXPIRY,
            JOB_MARKET_SCHEDULE,
            JOB_SESSION_EXPIRY,
            JOB_SYNC_WORKER,
        }:
            with self.subTest(job_name=job_name):
                self.assertTrue(check_background_job_authority(job_name, server_mode="foreign").ok)
                self.assertTrue(check_background_job_authority(job_name, server_mode="iran").ok)

        self.assertTrue(check_background_job_authority(JOB_USER_ACCOUNT_STATUS, server_mode="iran").ok)
        self.assertTrue(check_background_job_authority(JOB_CONNECTIVITY_MONITOR, server_mode="iran").ok)
        self.assertTrue(check_background_job_authority(JOB_TRADE_WEBAPP_DELIVERY, server_mode="iran").ok)
        self.assertTrue(check_background_job_authority(JOB_TRADE_TELEGRAM_DELIVERY, server_mode="foreign").ok)
        self.assertTrue(check_background_job_authority(JOB_OFFER_TELEGRAM_PUBLICATION, server_mode="foreign").ok)

    def test_unknown_jobs_fail_closed_when_filtering_factories(self):
        rejected = []
        factories = [
            (JOB_OFFER_EXPIRY, Mock(name="offer")),
            ("unknown_runtime_job", Mock(name="unknown")),
        ]

        allowed = filter_allowed_background_job_factories(
            factories,
            server_mode="iran",
            on_rejected=rejected.append,
        )

        self.assertEqual([name for name, _ in allowed], [JOB_OFFER_EXPIRY])
        self.assertEqual(rejected[0].reason, "unknown_background_job")

    def test_offer_impacting_jobs_declare_shared_authoritative_commands(self):
        entries = background_job_authority_entries()

        for job_name in {JOB_OFFER_EXPIRY, JOB_MARKET_SCHEDULE}:
            with self.subTest(job_name=job_name):
                entry = entries[job_name]
                self.assertTrue(entry.offer_impacting)
                self.assertEqual(entry.shared_authoritative_command, "expire_offers_authoritatively")
                self.assertIn("offer_home_server", entry.authority_rule)

    def test_local_runtime_jobs_remain_no_sync_or_internal(self):
        entries = background_job_authority_entries()
        session_expiry = entries[JOB_SESSION_EXPIRY]
        connectivity = entries[JOB_CONNECTIVITY_MONITOR]
        sync_worker = entries[JOB_SYNC_WORKER]

        self.assertTrue(session_expiry.local_runtime)
        self.assertEqual(get_sync_registry_entry("user_sessions").policy, SyncPolicy.NO_SYNC)
        self.assertIn("no-sync", session_expiry.sync_outbox_behavior)

        self.assertTrue(connectivity.local_runtime)
        self.assertEqual(connectivity.mutated_tables, ())
        self.assertIn("redis:connectivity:global", connectivity.external_state)
        self.assertIn("no-sync", connectivity.sync_outbox_behavior)

        self.assertTrue(sync_worker.local_runtime)
        self.assertEqual(get_sync_registry_entry("change_log").policy, SyncPolicy.INTERNAL_BOOKKEEPING)
        self.assertIn("internal bookkeeping", sync_worker.sync_outbox_behavior)

    def test_trade_delivery_jobs_are_server_scoped_by_channel(self):
        entries = background_job_authority_entries()
        webapp = entries[JOB_TRADE_WEBAPP_DELIVERY]
        telegram = entries[JOB_TRADE_TELEGRAM_DELIVERY]

        self.assertEqual(webapp.allowed_servers, ("iran",))
        self.assertIn("notifications", webapp.mutated_tables)
        self.assertIn("webapp", webapp.authority_rule)
        self.assertEqual(telegram.allowed_servers, ("foreign",))
        self.assertNotIn("notifications", telegram.mutated_tables)
        self.assertIn("Telegram Bot API", telegram.external_state)
        offer_publication = entries[JOB_OFFER_TELEGRAM_PUBLICATION]
        self.assertEqual(offer_publication.allowed_servers, ("foreign",))
        self.assertIn("offer_publication_states", offer_publication.mutated_tables)
        self.assertIn("Telegram Bot API", offer_publication.external_state)

    def test_current_server_is_used_when_server_mode_is_not_explicit(self):
        with patch("core.background_job_authority.current_server", return_value="foreign"):
            decision = check_background_job_authority(JOB_USER_ACCOUNT_STATUS)

        self.assertFalse(decision.ok)
        self.assertEqual(decision.current_server, "foreign")


if __name__ == "__main__":
    unittest.main()
