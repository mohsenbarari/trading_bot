import copy
import unittest
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from api.routers import sync
from api.routers.sync import _build_upsert_stmt, receive_sync_data
from core.sync_parity import (
    IDENTITY_FIELDS_BY_TABLE,
    LOCAL_ONLY_FIELDS_BY_TABLE,
    VOLATILE_FIELDS_BY_TABLE,
    build_record_parity,
    build_table_parity_snapshot,
    compare_parity_snapshots,
    synced_parity_table_names,
)
from core.sync_registry import SyncPolicy, sync_registry_entries
from core.sync_repair import (
    REPLAY_IDENTITY_FIELDS_BY_TABLE,
    build_current_state_replay_item,
    build_repair_plan,
    row_to_sync_data,
    validate_replay_identity,
)


NOW = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)


SYNC_TABLE_FIXTURES = {
    "accountant_relations": {
        "id": 11,
        "owner_user_id": 1,
        "accountant_user_id": 2,
        "created_by_user_id": 1,
        "invitation_token": "acct-rel-token-11",
        "global_account_name": "acct-rel-11",
        "relation_display_name": "حسابدار",
        "duty_description": "books",
        "mobile_number": "09120000011",
        "status": "active",
        "expires_at": NOW,
        "activated_at": NOW,
        "deleted_at": None,
    },
    "admin_broadcast_messages": {
        "id": 12,
        "content": "broadcast",
        "created_by_id": 1,
        "target_groups": ["users"],
        "recipient_count": 5,
        "published_at": NOW,
    },
    "admin_market_messages": {
        "id": 13,
        "content": "market",
        "created_by_id": 1,
        "reused_from_id": None,
        "is_active": True,
        "notified_recipients_count": 5,
        "published_at": NOW,
        "updated_at": NOW,
    },
    "commodities": {"id": 14, "name": "gold-main"},
    "commodity_aliases": {"id": 15, "alias": "gold-alias", "commodity_id": 14},
    "customer_relations": {
        "id": 16,
        "owner_user_id": 1,
        "customer_user_id": 3,
        "created_by_user_id": 1,
        "invitation_token": "cust-rel-token-16",
        "management_name": "مشتری",
        "customer_tier": "tier1",
        "commission_rate": "0.50",
        "min_trade_quantity": 1,
        "max_trade_quantity": 10,
        "max_daily_trades": 5,
        "max_daily_commodity_volume": 100,
        "trading_restricted_until": None,
        "status": "active",
        "expires_at": NOW,
        "activated_at": NOW,
        "deleted_at": None,
        "updated_at": NOW,
    },
    "invitations": {
        "id": 17,
        "account_name": "invite-17",
        "mobile_number": "09120000017",
        "token": "invite-token-17",
        "short_code": "s17",
        "role": "STANDARD",
        "created_by_id": 1,
        "is_used": False,
        "expires_at": NOW,
    },
    "market_runtime_state": {
        "id": 1,
        "is_open": True,
        "active_web_notice_visible": True,
        "offers_since_last_open": 3,
        "last_transition_at": NOW,
        "updated_at": NOW,
    },
    "market_schedule_overrides": {
        "id": 18,
        "date": date(2026, 6, 27),
        "override_type": "closed",
        "open_time_local": time(9, 0),
        "close_time_local": time(17, 0),
        "note": "holiday",
        "created_by_user_id": 1,
        "updated_at": NOW,
    },
    "notifications": {
        "id": 19,
        "user_id": 1,
        "message": "trade completed",
        "is_read": False,
        "level": "INFO",
        "category": "TRADE",
        "dedupe_key": "trade_completed:webapp:10019:1",
        "extra_payload": {"trade_number": 10019},
    },
    "offer_publication_states": {
        "id": 20,
        "version_id": 1,
        "offer_id": 30,
        "offer_public_id": "ofr_matrix_20",
        "offer_home_server": "foreign",
        "surface": "telegram_channel",
        "publication_owner_server": "foreign",
        "status": "sent",
        "dedupe_key": "offer-publication:telegram_channel:ofr_matrix_20",
        "surface_resource_id": "telegram:-100:777",
        "telegram_chat_id": -1001,
        "telegram_message_id": 777,
        "offer_version_id": 2,
        "last_known_offer_status": "active",
        "state_metadata": {"source": "matrix"},
        "archived": False,
        "updated_at": NOW,
    },
    "offer_requests": {
        "id": 21,
        "version_id": 2,
        "request_home_server": "foreign",
        "local_offer_id": 30,
        "offer_public_id": "ofr_matrix_20",
        "requester_user_id": 4,
        "actor_user_id": 4,
        "request_source_surface": "telegram_bot",
        "request_source_server": "foreign",
        "requested_quantity": 3,
        "idempotency_key": "telegram_callback:matrix:21",
        "received_at": NOW,
        "decided_at": None,
        "result_status": "received",
        "archived": False,
        "updated_at": NOW,
    },
    "offers": {
        "id": 30,
        "offer_public_id": "ofr_matrix_20",
        "version_id": 2,
        "user_id": 1,
        "actor_user_id": 1,
        "home_server": "foreign",
        "offer_type": "buy",
        "commodity_id": 14,
        "quantity": 40,
        "remaining_quantity": 40,
        "price": 120000,
        "exclude_from_competitive_price": False,
        "price_warning_type": None,
        "status": "active",
        "is_wholesale": False,
        "lot_sizes": [20, 20],
        "original_lot_sizes": [20, 20],
        "notes": "matrix",
        "channel_message_id": 777,
        "idempotency_key": "offer-create-matrix-30",
        "archived": False,
        "updated_at": NOW,
    },
    "telegram_link_tokens": {
        "id": 31,
        "user_id": 1,
        "token_hash": "a" * 64,
        "status": "pending",
        "issued_by_server": "iran",
        "expires_at": NOW,
        "used_at": None,
        "used_telegram_id": None,
        "revoked_at": None,
    },
    "telegram_admin_broadcasts": {
        "id": 36,
        "content": "پیام تست همگانی",
        "created_by_id": 1,
        "audience_type": "selected",
        "target_groups": [],
        "recipient_count": 1,
        "status": "queued",
        "queued_at": NOW,
        "completed_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    },
    "telegram_admin_broadcast_receipts": {
        "id": 37,
        "broadcast_id": 36,
        "recipient_user_id": 4,
        "telegram_id_at_enqueue": 1000004,
        "telegram_id_at_send": None,
        "dedupe_key": "telegram-admin-broadcast:36:4",
        "status": "pending",
        "reason": None,
        "telegram_message_id": None,
        "attempt_count": 0,
        "next_retry_at": None,
        "last_error_class": None,
        "last_error_message": None,
        "worker_id": "telegram-admin-broadcast-local",
        "lease_until": NOW,
        "sent_at": None,
        "terminal_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    },
    "trade_delivery_receipts": {
        "id": 32,
        "event_type": "trade_completed",
        "dedupe_key": "trade_completed:telegram:10032:1",
        "trade_id": 33,
        "trade_number": 10032,
        "offer_id": 30,
        "recipient_user_id": 1,
        "recipient_role": "owner",
        "channel": "telegram",
        "destination_server": "foreign",
        "status": "pending",
        "reason": None,
        "worker_id": "worker-local",
        "lease_until": NOW,
        "attempt_count": 0,
        "audit_payload": {"trade_number": 10032},
        "event_created_at": NOW,
        "updated_at": NOW,
    },
    "trades": {
        "id": 33,
        "version_id": 1,
        "trade_number": 10032,
        "offer_id": 30,
        "offer_user_id": 1,
        "offer_user_mobile": "09120000001",
        "responder_user_id": 4,
        "responder_user_mobile": "09120000004",
        "actor_user_id": 4,
        "commodity_id": 14,
        "trade_type": "buy",
        "quantity": 3,
        "price": 120000,
        "status": "pending",
        "note": "matrix",
        "idempotency_key": "trade-matrix-33",
        "archived": False,
        "updated_at": NOW,
    },
    "trading_settings": {
        "key": "offer_expiry_minutes",
        "value": "20",
        "updated_at": NOW,
    },
    "user_blocks": {
        "id": 34,
        "blocker_id": 1,
        "blocked_id": 4,
        "created_at": NOW,
    },
    "user_notification_preferences": {
        "id": 35,
        "user_id": 1,
        "market_offer_push_enabled": True,
        "updated_at": NOW,
    },
    "users": {
        "id": 1,
        "account_name": "matrix-user-1",
        "mobile_number": "09120000001",
        "telegram_id": 1000001,
        "username": "matrix_user",
        "full_name": "Matrix User",
        "address": "address",
        "avatar_file_id": "chat-file-local",
        "role": "STANDARD",
        "account_status": "active",
        "has_bot_access": True,
        "is_deleted": False,
        "trades_count": 2,
        "commodities_traded_count": 1,
        "channel_messages_count": 1,
        "max_sessions": 1,
        "max_accountants": 1,
        "max_customers": 5,
        "home_server": "foreign",
        "can_block_users": True,
        "max_blocked_users": 20,
        "updated_at": NOW,
    },
}


RULE_FAMILY_BY_TABLE = {
    "accountant_relations": "linked_relation_guard",
    "admin_broadcast_messages": "idempotent_id_upsert",
    "admin_market_messages": "idempotent_id_upsert",
    "commodities": "id_upsert_with_natural_key_fallback",
    "commodity_aliases": "id_upsert_with_natural_key_fallback",
    "customer_relations": "linked_relation_guard",
    "invitations": "token_terminal_merge",
    "market_runtime_state": "transition_timestamp_guard",
    "market_schedule_overrides": "id_upsert_with_natural_key_fallback",
    "notifications": "dedupe_key_read_monotonic",
    "offer_publication_states": "dedupe_key_version_status_precedence",
    "offer_requests": "request_home_idempotency_version_guard",
    "offers": "offer_public_id_version_terminal_guard",
    "telegram_link_tokens": "token_hash_terminal_guard",
    "telegram_admin_broadcasts": "idempotent_id_upsert",
    "telegram_admin_broadcast_receipts": "dedupe_key_terminal_receipt_guard",
    "trade_delivery_receipts": "dedupe_key_terminal_receipt_guard",
    "trades": "trade_number_completed_trade_guard",
    "trading_settings": "special_key_update_handler",
    "user_blocks": "block_pair_identity",
    "user_notification_preferences": "user_id_updated_at_guard",
    "users": "id_upsert_field_level_monotonic",
}


EXPECTED_ORDER_PAIRS = (
    ("users", "accountant_relations"),
    ("users", "customer_relations"),
    ("users", "telegram_link_tokens"),
    ("users", "invitations"),
    ("users", "notifications"),
    ("users", "telegram_admin_broadcasts"),
    ("users", "user_notification_preferences"),
    ("notifications", "offers"),
    ("accountant_relations", "offers"),
    ("customer_relations", "offers"),
    ("commodities", "commodity_aliases"),
    ("market_schedule_overrides", "market_runtime_state"),
    ("offers", "offer_publication_states"),
    ("offers", "offer_requests"),
    ("offers", "trades"),
    ("offer_requests", "trades"),
    ("trades", "trade_delivery_receipts"),
    ("telegram_admin_broadcasts", "telegram_admin_broadcast_receipts"),
)

NATURAL_IDENTITY_ONLY_TABLES_WITHOUT_SEPARATE_BUSINESS_FIELDS = {"commodities"}


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MatrixReceiveDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, *args, **kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None), first=lambda: None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def begin_nested(self):
        return AsyncNullContext()


def synced_tables() -> set[str]:
    return {
        table_name
        for table_name, entry in sync_registry_entries().items()
        if entry.policy == SyncPolicy.SYNC
    }


def fake_row(table_name: str, payload: dict):
    columns = [SimpleNamespace(name=field) for field in payload]
    return SimpleNamespace(__table__=SimpleNamespace(columns=columns), **payload)


def sample_identity(table_name: str, payload: dict) -> dict:
    allowed_fields = REPLAY_IDENTITY_FIELDS_BY_TABLE[table_name]
    return {field: payload[field] for field in sorted(allowed_fields) if field in payload and payload[field] not in (None, "")}


def business_mutation(table_name: str, payload: dict) -> dict:
    mutated = copy.deepcopy(payload)
    identity_fields = set(IDENTITY_FIELDS_BY_TABLE.get(table_name, ("id",)))
    local_only_fields = set(LOCAL_ONLY_FIELDS_BY_TABLE.get(table_name, set()))
    if identity_fields and identity_fields != {"id"}:
        local_only_fields.add("id")
    volatile_fields = set(VOLATILE_FIELDS_BY_TABLE.get("*", set())) | set(VOLATILE_FIELDS_BY_TABLE.get(table_name, set()))
    candidates = [
        field
        for field, value in mutated.items()
        if field not in identity_fields
        and field not in local_only_fields
        and field not in volatile_fields
        and field not in {"last_seen_at", "worker_id", "lease_until"}
        and value is not None
    ]
    if not candidates:
        candidates = [field for field in mutated if field not in identity_fields]
    field = candidates[0]
    value = mutated[field]
    if isinstance(value, bool):
        mutated[field] = not value
    elif isinstance(value, int):
        mutated[field] = value + 1
    elif isinstance(value, str):
        mutated[field] = f"{value}-changed"
    elif isinstance(value, list):
        mutated[field] = list(value) + ["changed"]
    elif isinstance(value, dict):
        mutated[field] = {**value, "changed": True}
    else:
        mutated[field] = "changed"
    return mutated


class SyncGuaranteeMatrixTests(unittest.IsolatedAsyncioTestCase):
    def test_every_synced_table_has_receiver_payload_parity_repair_and_rule_coverage(self):
        tables = synced_tables()

        self.assertEqual(set(SYNC_TABLE_FIXTURES), tables)
        self.assertEqual(set(RULE_FAMILY_BY_TABLE), tables)
        self.assertEqual(set(synced_parity_table_names("deep")), tables)

        for table_name in sorted(tables):
            with self.subTest(table_name=table_name):
                model = sync.get_model_class(table_name)
                self.assertIsNotNone(model)
                self.assertIn(table_name, sync.TABLE_ORDER)
                self.assertIn(table_name, REPLAY_IDENTITY_FIELDS_BY_TABLE)

                payload = SYNC_TABLE_FIXTURES[table_name]
                identity = sample_identity(table_name, payload)
                self.assertTrue(identity, f"{table_name} fixture must expose at least one replay identity")
                validate_replay_identity(table_name, identity)

                parity = build_record_parity(table_name, payload)
                self.assertIn("identity_hash", parity)
                self.assertIn("business_hash", parity)

    def test_dependency_order_matrix_is_stable_for_reordered_batches(self):
        for before, after in EXPECTED_ORDER_PAIRS:
            with self.subTest(before=before, after=after):
                self.assertLess(sync.TABLE_ORDER[before], sync.TABLE_ORDER[after])

    async def test_receiver_reorders_full_sync_batch_before_apply(self):
        items = [
            {
                "table": table_name,
                "operation": "UPDATE" if table_name == "trading_settings" else "INSERT",
                "id": payload.get("id", payload.get("key")),
                "data": copy.deepcopy(payload),
            }
            for table_name, payload in sorted(
                SYNC_TABLE_FIXTURES.items(),
                key=lambda item: sync.TABLE_ORDER[item[0]],
                reverse=True,
            )
        ]
        seen_tables: list[str] = []

        async def fake_apply_item(db, table, operation, record_id, data, model, new_offers, terminal_offers=None):
            seen_tables.append(table)
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()
        ), patch("core.cache.invalidate_commodities_cache", new=AsyncMock()), patch(
            "core.cache.invalidate_admin_market_current_cache", new=AsyncMock()
        ), patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()), patch(
            "api.routers.sync._refresh_notification_unread_counts", new=AsyncMock()
        ), patch("core.trading_settings.refresh_settings_cache_async", new=AsyncMock()), patch(
            "core.trading_settings.get_trading_settings_async", new=AsyncMock(return_value=None)
        ), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=MatrixReceiveDB(), _=None)

        self.assertEqual(result, {"status": "success", "processed": len(items)})
        self.assertEqual(seen_tables, sorted(seen_tables, key=lambda table: sync.TABLE_ORDER[table]))

    async def test_deferred_failed_and_duplicate_delivery_matrix_is_safe(self):
        items = [
            {"table": "offers", "operation": "UPDATE", "id": 30, "data": copy.deepcopy(SYNC_TABLE_FIXTURES["offers"])},
            {"table": "trades", "operation": "INSERT", "id": 33, "data": copy.deepcopy(SYNC_TABLE_FIXTURES["trades"])},
        ]

        with patch(
            "api.routers.sync._apply_item",
            new=AsyncMock(side_effect=["deferred", "ok", "error"]),
        ) as apply_mock, patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=MatrixReceiveDB(), _=None)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(apply_mock.await_count, 3)

    def test_payload_replay_matrix_drops_local_only_fields_and_keeps_sync_metadata(self):
        for table_name, payload in sorted(SYNC_TABLE_FIXTURES.items()):
            with self.subTest(table_name=table_name):
                item = build_current_state_replay_item(
                    table_name=table_name,
                    row=fake_row(table_name, payload),
                    source_server="foreign",
                    source_sequence=1000 + sync.TABLE_ORDER[table_name],
                )

                self.assertEqual(item["table"], table_name)
                self.assertEqual(item["sync_meta"]["source_server"], "foreign")
                self.assertEqual(item["sync_meta"]["source_sequence"], 1000 + sync.TABLE_ORDER[table_name])
                self.assertIn("sync_protocol", item)
                self.assertEqual(row_to_sync_data(table_name, fake_row(table_name, payload)), item["data"])
                self.assertNotIn("avatar_file_id", item["data"])
                self.assertNotIn("channel_message_id", item["data"])
                self.assertNotIn("worker_id", item["data"])
                self.assertNotIn("lease_until", item["data"])

    def test_parity_and_repair_matrix_detects_business_drift_for_every_synced_table(self):
        for table_name, payload in sorted(SYNC_TABLE_FIXTURES.items()):
            with self.subTest(table_name=table_name):
                if table_name in NATURAL_IDENTITY_ONLY_TABLES_WITHOUT_SEPARATE_BUSINESS_FIELDS:
                    continue
                local = {
                    "status": "ok",
                    "schema_version": 1,
                    "mode": "deep",
                    "tables": {table_name: build_table_parity_snapshot(table_name, [payload])},
                }
                peer = {
                    "status": "ok",
                    "schema_version": 1,
                    "mode": "deep",
                    "tables": {table_name: build_table_parity_snapshot(table_name, [business_mutation(table_name, payload)])},
                }

                comparison = compare_parity_snapshots(local, peer)
                plan = build_repair_plan(local, peer, direction="local-to-peer")

                self.assertEqual(comparison["status"], "business_drift")
                self.assertEqual(plan["status"], "dry_run")
                self.assertGreaterEqual(plan["action_count"], 1)
                self.assertTrue(
                    any(action["table"] == table_name for action in plan["actions"]),
                    f"{table_name} drift should produce a repair action",
                )

    def test_merge_rule_matrix_compiles_for_every_receiver_rule_family(self):
        for table_name, payload in sorted(SYNC_TABLE_FIXTURES.items()):
            with self.subTest(table_name=table_name, rule_family=RULE_FAMILY_BY_TABLE[table_name]):
                model = sync.get_model_class(table_name)
                if table_name == "trading_settings":
                    self.assertEqual(RULE_FAMILY_BY_TABLE[table_name], "special_key_update_handler")
                    continue
                stmt = _build_upsert_stmt(model, table_name, copy.deepcopy(payload))
                compiled = str(stmt.compile(dialect=postgresql.dialect()))
                self.assertIn("ON CONFLICT", compiled)

                if table_name in {
                    "offers",
                    "trades",
                    "offer_publication_states",
                    "trade_delivery_receipts",
                    "telegram_admin_broadcast_receipts",
                }:
                    identity_token = {
                        "offers": "offer_public_id",
                        "trades": "trade_number",
                        "offer_publication_states": "dedupe_key",
                        "trade_delivery_receipts": "dedupe_key",
                        "telegram_admin_broadcast_receipts": "dedupe_key",
                    }[table_name]
                    self.assertIn(identity_token, compiled)


if __name__ == "__main__":
    unittest.main()
