import json
import unittest

from core.sync_field_policy import (
    SyncFieldAction,
    SyncFieldClassification,
    get_sync_field_policy_entry,
    sanitize_sync_payload,
    sync_field_policy_entries,
    sync_field_policy_fingerprint,
    sync_log_payload_context,
)


class SyncFieldPolicyTests(unittest.TestCase):
    def test_user_sensitive_and_no_sync_reference_fields_are_sanitized(self):
        payload = {
            "id": 7,
            "mobile_number": "09120000000",
            "full_name": "User Seven",
            "admin_password_hash": "bcrypt-secret",
            "must_change_password": True,
            "avatar_file_id": "chat-file-1",
        }

        sanitized = sanitize_sync_payload("users", payload)

        self.assertEqual(sanitized["mobile_number"], "09120000000")
        self.assertEqual(sanitized["full_name"], "User Seven")
        self.assertNotIn("admin_password_hash", sanitized)
        self.assertNotIn("must_change_password", sanitized)
        self.assertNotIn("avatar_file_id", sanitized)

    def test_push_subscription_runtime_secrets_never_leave_as_raw_fields(self):
        payload = {
            "id": 3,
            "user_id": 7,
            "endpoint": "https://push.example/subscription/raw",
            "p256dh": "raw-p256dh",
            "auth": "raw-auth",
            "user_agent": "raw-browser",
            "platform": "android",
            "last_error": "raw failure",
        }

        sanitized = sanitize_sync_payload("push_subscriptions", payload)

        self.assertEqual(sanitized["id"], 3)
        self.assertEqual(sanitized["user_id"], 7)
        self.assertEqual(len(sanitized["endpoint_hash"]), 64)
        self.assertNotIn("endpoint", sanitized)
        self.assertNotIn("p256dh", sanitized)
        self.assertNotIn("auth", sanitized)
        self.assertNotIn("user_agent", sanitized)
        self.assertNotIn("platform", sanitized)
        self.assertNotIn("last_error", sanitized)
        self.assertNotIn("raw", json.dumps(sanitized))

    def test_offer_publication_runtime_fields_are_dropped_from_sync_payload(self):
        payload = {
            "id": 3,
            "offer_id": 33,
            "offer_public_id": "ofr_1",
            "offer_home_server": "foreign",
            "surface": "telegram_channel",
            "publication_owner_server": "foreign",
            "publisher_bot_identity": "primary",
            "status": "sent",
            "dedupe_key": "offer-publication:telegram_channel:ofr_1",
            "surface_resource_id": "telegram:-1001:777",
            "telegram_chat_id": -1001,
            "telegram_message_id": 777,
            "last_attempt_at": "2026-06-28T10:00:00Z",
            "last_success_at": "2026-06-28T10:00:01Z",
            "next_retry_at": "2026-06-28T10:01:00Z",
            "error_code": "BadRequest",
            "error_message": "provider detail",
            "state_metadata": {"provider": "telegram"},
            "offer_version_id": 4,
        }

        sanitized = sanitize_sync_payload("offer_publication_states", payload)

        self.assertEqual(sanitized["offer_public_id"], "ofr_1")
        self.assertEqual(sanitized["offer_home_server"], "foreign")
        self.assertEqual(sanitized["surface"], "telegram_channel")
        self.assertEqual(sanitized["publication_owner_server"], "foreign")
        self.assertEqual(sanitized["publisher_bot_identity"], "primary")
        self.assertEqual(sanitized["status"], "sent")
        self.assertEqual(sanitized["dedupe_key"], "offer-publication:telegram_channel:ofr_1")
        self.assertEqual(sanitized["offer_version_id"], 4)
        for field in {
            "offer_id",
            "surface_resource_id",
            "telegram_chat_id",
            "telegram_message_id",
            "last_attempt_at",
            "last_success_at",
            "next_retry_at",
            "error_code",
            "error_message",
            "state_metadata",
        }:
            self.assertNotIn(field, sanitized)

    def test_trade_delivery_receipt_local_foreign_keys_are_dropped_from_sync_payload(self):
        payload = {
            "id": 41,
            "dedupe_key": "trade_completed:webapp:10001:7",
            "event_type": "trade_completed",
            "trade_id": 2,
            "trade_number": 10001,
            "offer_id": 8,
            "notification_id": 13,
            "recipient_user_id": 7,
            "channel": "webapp",
            "destination_server": "iran",
            "worker_id": "local-worker",
            "lease_until": "2026-06-29T12:00:00Z",
        }

        sanitized = sanitize_sync_payload("trade_delivery_receipts", payload)

        self.assertEqual(sanitized["dedupe_key"], "trade_completed:webapp:10001:7")
        self.assertEqual(sanitized["trade_number"], 10001)
        self.assertEqual(sanitized["recipient_user_id"], 7)
        for field in {"trade_id", "offer_id", "notification_id", "worker_id", "lease_until"}:
            self.assertNotIn(field, sanitized)

    def test_telegram_admin_broadcast_local_worker_fields_are_dropped_from_sync_payload(self):
        payload = {
            "id": 51,
            "broadcast_id": 5,
            "recipient_user_id": 7,
            "telegram_id_at_enqueue": 9001,
            "telegram_id_at_send": 9002,
            "dedupe_key": "telegram-admin-broadcast:5:7",
            "status": "retryable_failed",
            "reason": "telegram_rate_limited",
            "last_error_message": "provider detail",
            "worker_id": "foreign-local-worker",
            "lease_until": "2026-06-30T12:00:00Z",
            "queue_job_id": 81,
            "queue_handed_off_at": "2026-06-30T11:59:00Z",
        }

        sanitized = sanitize_sync_payload("telegram_admin_broadcast_receipts", payload)

        self.assertEqual(sanitized["dedupe_key"], "telegram-admin-broadcast:5:7")
        self.assertEqual(sanitized["telegram_id_at_enqueue"], 9001)
        self.assertEqual(sanitized["telegram_id_at_send"], 9002)
        self.assertEqual(sanitized["last_error_message"], "provider detail")
        self.assertNotIn("worker_id", sanitized)
        self.assertNotIn("lease_until", sanitized)
        self.assertNotIn("queue_job_id", sanitized)
        self.assertNotIn("queue_handed_off_at", sanitized)

        broadcast = sanitize_sync_payload(
            "telegram_admin_broadcasts",
            {
                "id": 7,
                "content": "پیام",
                "queue_last_handed_off_at": "2026-06-30T12:00:00Z",
            },
        )
        self.assertEqual(broadcast, {"id": 7, "content": "پیام"})

    def test_notification_outbox_queue_binding_is_dropped_from_sync_payload(self):
        payload = {
            "id": 91,
            "dedupe_key": "telegram-notification:project_user_joined:9:7",
            "source_type": "project_user_joined",
            "recipient_user_id": 7,
            "text": "پیام عضویت",
            "worker_id": "foreign-local-worker",
            "lease_until": "2026-07-18T12:00:00Z",
            "queue_job_id": 811,
            "queue_handed_off_at": "2026-07-18T11:59:00Z",
        }

        sanitized = sanitize_sync_payload("telegram_notification_outbox", payload)

        self.assertEqual(
            sanitized["dedupe_key"],
            "telegram-notification:project_user_joined:9:7",
        )
        self.assertEqual(sanitized["text"], "پیام عضویت")
        for field in {
            "worker_id",
            "lease_until",
            "queue_job_id",
            "queue_handed_off_at",
        }:
            self.assertNotIn(field, sanitized)

    def test_required_sensitive_fields_have_explicit_classification(self):
        expectations = {
            ("users", "admin_password_hash"): SyncFieldClassification.NO_SYNC,
            ("users", "avatar_file_id"): SyncFieldClassification.NO_SYNC,
            ("users", "mobile_number"): SyncFieldClassification.SYNC,
            ("users", "telegram_id"): SyncFieldClassification.SYNC,
            ("users", "username"): SyncFieldClassification.SYNC,
            ("users", "full_name"): SyncFieldClassification.SYNC,
            ("invitations", "short_code"): SyncFieldClassification.SYNC,
            ("invitation_identity_reservations", "normalized_mobile"): SyncFieldClassification.NO_SYNC,
            ("invitation_identity_reservations", "normalized_account_name"): SyncFieldClassification.NO_SYNC,
            ("telegram_registration_intents", "invitation_token"): SyncFieldClassification.NO_SYNC,
            ("telegram_registration_intents", "normalized_mobile"): SyncFieldClassification.NO_SYNC,
            ("telegram_registration_intents", "telegram_id"): SyncFieldClassification.NO_SYNC,
            ("telegram_registration_intents", "address"): SyncFieldClassification.NO_SYNC,
            ("telegram_registration_command_receipts", "request_hash"): SyncFieldClassification.NO_SYNC,
            ("telegram_registration_command_receipts", "invitation_token_hash"): SyncFieldClassification.NO_SYNC,
            ("offer_expiry_command_receipts", "request_hash"): SyncFieldClassification.NO_SYNC,
            ("trades", "offer_user_mobile"): SyncFieldClassification.SYNC,
            ("notifications", "message"): SyncFieldClassification.SYNC,
            ("notifications", "extra_payload"): SyncFieldClassification.SYNC,
            ("offer_requests", "customer_relation_invitation_token"): SyncFieldClassification.SYNC,
            ("telegram_admin_broadcasts", "content"): SyncFieldClassification.SYNC,
            ("telegram_admin_broadcast_receipts", "telegram_id_at_enqueue"): SyncFieldClassification.SYNC,
            ("telegram_admin_broadcast_receipts", "telegram_id_at_send"): SyncFieldClassification.SYNC,
            ("telegram_admin_broadcast_receipts", "last_error_message"): SyncFieldClassification.SYNC,
            ("telegram_admin_broadcast_receipts", "worker_id"): SyncFieldClassification.NO_SYNC,
            ("telegram_admin_broadcast_receipts", "lease_until"): SyncFieldClassification.NO_SYNC,
            ("telegram_admin_broadcast_receipts", "queue_job_id"): SyncFieldClassification.NO_SYNC,
            ("telegram_admin_broadcast_receipts", "queue_handed_off_at"): SyncFieldClassification.NO_SYNC,
            ("telegram_admin_broadcasts", "queue_last_handed_off_at"): SyncFieldClassification.NO_SYNC,
            ("telegram_notification_outbox", "queue_job_id"): SyncFieldClassification.NO_SYNC,
            ("telegram_notification_outbox", "queue_handed_off_at"): SyncFieldClassification.NO_SYNC,
            ("trade_delivery_receipts", "last_error"): SyncFieldClassification.SYNC,
            ("trade_delivery_receipts", "audit_payload"): SyncFieldClassification.SYNC,
            ("trade_delivery_receipts", "trade_id"): SyncFieldClassification.NO_SYNC,
            ("trade_delivery_receipts", "offer_id"): SyncFieldClassification.NO_SYNC,
            ("trade_delivery_receipts", "notification_id"): SyncFieldClassification.NO_SYNC,
            ("trade_delivery_receipts", "worker_id"): SyncFieldClassification.NO_SYNC,
            ("trade_delivery_receipts", "lease_until"): SyncFieldClassification.NO_SYNC,
            ("offer_publication_states", "telegram_message_id"): SyncFieldClassification.NO_SYNC,
            ("offer_publication_states", "error_message"): SyncFieldClassification.NO_SYNC,
            ("offer_publication_states", "state_metadata"): SyncFieldClassification.NO_SYNC,
            ("push_subscriptions", "endpoint"): SyncFieldClassification.HASH_ONLY,
            ("push_subscriptions", "auth"): SyncFieldClassification.NO_SYNC,
        }

        for key, classification in expectations.items():
            with self.subTest(key=key):
                entry = get_sync_field_policy_entry(*key)
                self.assertIsNotNone(entry)
                self.assertEqual(entry.classification, classification)

        self.assertEqual(
            get_sync_field_policy_entry("trade_delivery_receipts", "worker_id").action,
            SyncFieldAction.DROP,
        )
        self.assertEqual(
            get_sync_field_policy_entry("trade_delivery_receipts", "lease_until").action,
            SyncFieldAction.DROP,
        )
        self.assertEqual(
            get_sync_field_policy_entry("telegram_admin_broadcast_receipts", "worker_id").action,
            SyncFieldAction.DROP,
        )
        self.assertEqual(
            get_sync_field_policy_entry("telegram_admin_broadcast_receipts", "lease_until").action,
            SyncFieldAction.DROP,
        )

    def test_no_sync_reference_fields_drop_raw_foreign_keys(self):
        for table_name, field_name, reference_table in {
            ("users", "avatar_file_id", "chat_files"),
            ("chats", "avatar_file_id", "chat_files"),
        }:
            with self.subTest(table_name=table_name, field_name=field_name):
                entry = get_sync_field_policy_entry(table_name, field_name)
                self.assertIsNotNone(entry)
                self.assertEqual(entry.action, SyncFieldAction.DROP)
                self.assertEqual(entry.references_no_sync_table, reference_table)

    def test_log_payload_context_omits_values_and_reports_policy_shape(self):
        payload = {
            "mobile_number": "09120000000",
            "admin_password_hash": "bcrypt-secret",
            "avatar_file_id": "chat-file-1",
        }

        context = sync_log_payload_context("users", payload)
        rendered = json.dumps(context, ensure_ascii=False)

        self.assertEqual(context["data_kind"], "dict")
        self.assertEqual(context["sensitive_field_count"], 2)
        self.assertIn("admin_password_hash", context["dropped_fields"])
        self.assertIn("avatar_file_id", context["no_sync_reference_fields"])
        self.assertNotIn("09120000000", rendered)
        self.assertNotIn("bcrypt-secret", rendered)
        self.assertNotIn("chat-file-1", rendered)

    def test_field_policy_fingerprint_is_stable_and_policy_backed(self):
        self.assertTrue(sync_field_policy_entries())
        self.assertEqual(len(sync_field_policy_fingerprint()), 16)


if __name__ == "__main__":
    unittest.main()
