import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from api.routers.sync import (
    _apply_item,
    _build_upsert_stmt,
    _localize_commodity_reference_by_name,
    _localize_offer_request_customer_relation_reference,
    _localize_republished_offer_reference,
    _partitioned_sequence_alignment_sql,
    _resolve_local_record_id_by_public_identity,
    _sync_table_has_public_identity,
)
from models.accountant_relation import AccountantRelation
from models.commodity import Commodity, CommodityAlias
from models.customer_relation import CustomerRelation
from models.invitation import Invitation
from models.market_schedule_override import MarketScheduleOverride
from models.market_runtime_state import MarketRuntimeState
from models.notification import Notification
from models.offer import Offer
from models.offer_request import OfferRequest
from models.offer_publication_state import OfferPublicationState
from models.telegram_link_token import TelegramLinkToken
from models.trade import Trade
from models.trade_delivery_receipt import TradeDeliveryReceipt
from models.user import User
from models.user_block import UserBlock
from models.user_notification_preference import UserNotificationPreference


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.execute_calls = []

    def begin_nested(self):
        return AsyncNullContext()

    async def execute(self, stmt, execution_options=None):
        self.execute_calls.append((stmt, execution_options))
        if self.execute_results:
            next_result = self.execute_results.pop(0)
            if isinstance(next_result, Exception):
                raise next_result
            return next_result
        return SimpleNamespace()


class ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeInsertBuilder:
    def __init__(self):
        self.values_payload = None
        self.conflict_payload = None

    def values(self, **kwargs):
        self.values_payload = kwargs
        return self

    def on_conflict_do_update(self, index_elements, set_, **kwargs):
        self.conflict_payload = (index_elements, set_, kwargs)
        return "TRADING_UPSERT"


class UpdateBuilder:
    def __init__(self):
        self.where_clause = None
        self.values_payload = None

    def where(self, clause):
        self.where_clause = clause
        return self

    def values(self, **kwargs):
        self.values_payload = kwargs
        return "MERGE_UPDATE"


class SyncRouterApplyItemSuccessTests(unittest.IsolatedAsyncioTestCase):
    def test_offer_upsert_preserves_competitive_warning_fields(self):
        stmt = _build_upsert_stmt(
            Offer,
            "offers",
            {
                "id": 8,
                "offer_public_id": "ofr_warning_8",
                "version_id": 2,
                "status": "active",
                "exclude_from_competitive_price": True,
                "price_warning_type": "buy_above_highest_active",
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (offer_public_id)", compiled)
        self.assertNotIn("id = excluded.id", compiled)
        self.assertNotIn("offer_public_id = excluded.offer_public_id", compiled)
        self.assertIn("exclude_from_competitive_price =", compiled)
        self.assertIn("price_warning_type =", compiled)

    def test_user_notification_preference_upsert_uses_user_id_and_updated_at_guard(self):
        stmt = _build_upsert_stmt(
            UserNotificationPreference,
            "user_notification_preferences",
            {
                "id": 41,
                "user_id": 7,
                "market_offer_push_enabled": False,
                "updated_at": datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (user_id)", compiled)
        self.assertNotIn("id = excluded.id", compiled)
        self.assertNotIn("user_id = excluded.user_id", compiled)
        self.assertIn("market_offer_push_enabled = excluded.market_offer_push_enabled", compiled)
        self.assertIn("updated_at = excluded.updated_at", compiled)
        self.assertIn("user_notification_preferences.updated_at IS NULL", compiled)
        self.assertIn("excluded.updated_at IS NOT NULL", compiled)
        self.assertNotIn("OR excluded.updated_at IS NULL", compiled)
        self.assertIn("user_notification_preferences.updated_at <= excluded.updated_at", compiled)

    def test_user_notification_preference_null_updated_at_is_not_accepted_as_newer(self):
        stmt = _build_upsert_stmt(
            UserNotificationPreference,
            "user_notification_preferences",
            {
                "id": 41,
                "user_id": 7,
                "market_offer_push_enabled": False,
                "updated_at": None,
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (user_id)", compiled)
        self.assertIn("user_notification_preferences.updated_at IS NULL", compiled)
        self.assertIn("excluded.updated_at IS NOT NULL", compiled)
        self.assertNotIn("OR excluded.updated_at IS NULL", compiled)
        self.assertIn("user_notification_preferences.updated_at <= excluded.updated_at", compiled)

    async def test_user_notification_preference_identity_resolves_by_user_id(self):
        db = FakeDB([ScalarOneOrNoneResult(91)])

        resolved_id = await _resolve_local_record_id_by_public_identity(
            db,
            "user_notification_preferences",
            {"user_id": "7"},
        )

        self.assertEqual(resolved_id, 91)
        self.assertTrue(_sync_table_has_public_identity("user_notification_preferences", {"user_id": "7"}))
        self.assertFalse(_sync_table_has_public_identity("user_notification_preferences", {"user_id": None}))

    async def test_delete_resolves_natural_identity_before_delete(self):
        db = FakeDB([ScalarOneOrNoneResult(14), SimpleNamespace(rowcount=1)])

        result = await _apply_item(
            db,
            "commodities",
            "DELETE",
            999,
            {"id": 999, "name": "gold-main"},
            model=Commodity,
            new_offers=[],
        )

        self.assertEqual(result, "ok")
        self.assertEqual(len(db.execute_calls), 2)
        delete_stmt = db.execute_calls[1][0]
        compiled = str(delete_stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("DELETE FROM commodities", compiled)
        self.assertIn("commodities.id = ", compiled)
        self.assertNotIn("999", compiled)

    async def test_id_only_delete_for_natural_identity_table_is_ignored(self):
        db = FakeDB()

        with patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "commodities",
                "DELETE",
                999,
                {"id": 999},
                model=Commodity,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        self.assertEqual(db.execute_calls, [])
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("missing_natural_identity", rendered_log)
        self.assertIn("sync.unsafe_id_only_delete_ignored", rendered_log)

    async def test_commodity_alias_localizes_commodity_reference_by_name(self):
        db = FakeDB([ScalarOneOrNoneResult(88), SimpleNamespace(rowcount=1)])
        data = {
            "id": 15,
            "alias": "gold-alias",
            "commodity_id": 14,
            "commodity_name": "gold-main",
        }

        with patch("api.routers.sync._build_upsert_stmt", return_value="ALIAS_UPSERT") as builder:
            result = await _apply_item(
                db,
                "commodity_aliases",
                "INSERT",
                15,
                data,
                model=CommodityAlias,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        self.assertEqual(data["commodity_id"], 88)
        builder.assert_called_once()
        persist_data = builder.call_args.args[2]
        self.assertEqual(persist_data["commodity_id"], 88)
        self.assertNotIn("commodity_name", persist_data)

    async def test_commodity_alias_defers_until_named_commodity_exists_locally(self):
        db = FakeDB([ScalarOneOrNoneResult(None)])
        data = {
            "id": 15,
            "alias": "gold-alias",
            "commodity_id": 14,
            "commodity_name": "gold-main",
        }

        with patch("api.routers.sync._build_upsert_stmt") as builder:
            result = await _apply_item(
                db,
                "commodity_aliases",
                "INSERT",
                15,
                data,
                model=CommodityAlias,
                new_offers=[],
            )

        self.assertEqual(result, "deferred")
        builder.assert_not_called()

    async def test_offer_and_trade_commodity_references_localize_by_name(self):
        for table_name in ("offers", "trades"):
            with self.subTest(table_name=table_name):
                db = FakeDB([ScalarOneOrNoneResult(88)])
                data = {"commodity_id": 14, "commodity_name": "gold-main"}

                resolved = await _localize_commodity_reference_by_name(db, table_name, data)

                self.assertTrue(resolved)
                self.assertEqual(data["commodity_id"], 88)

    async def test_republished_offer_reference_localizes_by_public_id(self):
        data = {"republished_offer_id": 31, "republished_offer_public_id": "ofr_source_31"}
        with patch("api.routers.sync._resolve_offer_id_by_public_id", new=AsyncMock(return_value=91)):
            resolved = await _localize_republished_offer_reference(SimpleNamespace(), data)

        self.assertTrue(resolved)
        self.assertEqual(data["republished_offer_id"], 91)
        self.assertNotIn("republished_offer_public_id", data)

    def test_offer_upsert_preserves_child_owned_republish_provenance(self):
        stmt = _build_upsert_stmt(
            Offer,
            "offers",
            {
                "offer_public_id": "ofr_child_31",
                "republished_from_offer_public_id": "ofr_source_31",
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("republished_from_offer_public_id", compiled)
        self.assertIn("ON CONFLICT (offer_public_id)", compiled)

    async def test_offer_request_customer_relation_localizes_by_invitation_token(self):
        db = FakeDB([ScalarOneOrNoneResult(71)])
        data = {
            "customer_relation_id": 17,
            "customer_relation_invitation_token": "customer-token-17",
        }

        resolved = await _localize_offer_request_customer_relation_reference(db, data)

        self.assertTrue(resolved)
        self.assertEqual(data["customer_relation_id"], 71)
        self.assertNotIn("customer_relation_invitation_token", data)

    def test_natural_key_upserts_do_not_conflict_on_remote_id(self):
        relation_stmt = _build_upsert_stmt(
            AccountantRelation,
            "accountant_relations",
            {
                "id": 19,
                "owner_user_id": 2,
                "accountant_user_id": 5,
                "invitation_token": "acct-token-19",
                "global_account_name": "acct",
                "relation_display_name": "دفتر",
                "mobile_number": "09120000000",
                "status": "active",
            },
        )
        relation_compiled = str(relation_stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT (invitation_token)", relation_compiled)
        self.assertNotIn("id = excluded.id", relation_compiled)
        self.assertNotIn("invitation_token = excluded.invitation_token", relation_compiled)

        customer_stmt = _build_upsert_stmt(
            CustomerRelation,
            "customer_relations",
            {
                "id": 23,
                "owner_user_id": 2,
                "customer_user_id": 8,
                "invitation_token": "cust-token-23",
                "management_name": "مشتری",
                "customer_tier": "tier1",
                "status": "active",
            },
        )
        customer_compiled = str(customer_stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT (invitation_token)", customer_compiled)
        self.assertNotIn("id = excluded.id", customer_compiled)

        commodity_stmt = _build_upsert_stmt(Commodity, "commodities", {"id": 14, "name": "gold-main"})
        commodity_compiled = str(commodity_stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT (name) DO NOTHING", commodity_compiled)

        alias_stmt = _build_upsert_stmt(
            CommodityAlias,
            "commodity_aliases",
            {"id": 15, "alias": "gold-alias", "commodity_id": 14},
        )
        alias_compiled = str(alias_stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT (alias)", alias_compiled)
        self.assertNotIn("id = excluded.id", alias_compiled)
        self.assertNotIn("alias = excluded.alias", alias_compiled)

    def test_market_schedule_override_upsert_uses_date_and_updated_at_guard(self):
        stmt = _build_upsert_stmt(
            MarketScheduleOverride,
            "market_schedule_overrides",
            {
                "id": 18,
                "date": date(2026, 6, 27),
                "override_type": "closed_all_day",
                "note": "holiday",
                "updated_at": datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (date)", compiled)
        self.assertNotIn("id = excluded.id", compiled)
        self.assertNotIn("date = excluded.date", compiled)
        self.assertIn("market_schedule_overrides.updated_at IS NULL", compiled)
        self.assertIn("excluded.updated_at IS NULL", compiled)
        self.assertIn("market_schedule_overrides.updated_at <= excluded.updated_at", compiled)

    def test_user_upsert_uses_recency_guard_and_monotonic_fields(self):
        stmt = _build_upsert_stmt(
            User,
            "users",
            {
                "id": 7,
                "telegram_id": None,
                "full_name": "Updated User",
                "is_deleted": False,
                "deleted_at": None,
                "trades_count": 3,
                "last_seen_at": datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 6, 27, 12, 5, tzinfo=timezone.utc),
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (id)", compiled)
        self.assertIn("greatest(coalesce(users.trades_count", compiled)
        self.assertIn("users.updated_at <= excluded.updated_at", compiled)
        self.assertIn("users.updated_at IS NULL", compiled)
        self.assertIn("excluded.updated_at IS NULL", compiled)
        self.assertIn("coalesce(users.is_deleted", compiled)
        self.assertIn("coalesce(excluded.is_deleted", compiled)
        self.assertIn("WHEN (excluded.telegram_id IS NOT NULL) THEN excluded.telegram_id ELSE users.telegram_id", compiled)

    def test_notification_upsert_uses_dedupe_key_and_read_state_monotonic(self):
        stmt = _build_upsert_stmt(
            Notification,
            "notifications",
            {
                "id": 5,
                "user_id": 1,
                "message": "trade completed",
                "is_read": False,
                "level": "INFO",
                "category": "TRADE",
                "dedupe_key": "trade_completed:webapp:10025:1",
                "extra_payload": {"trade_number": 10025},
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL", compiled)
        self.assertNotIn("id = excluded.id", compiled)
        self.assertNotIn("dedupe_key = excluded.dedupe_key", compiled)
        self.assertIn("is_read = (coalesce(notifications.is_read", compiled)
        self.assertIn("OR coalesce(excluded.is_read", compiled)

    def test_invitation_upsert_preserves_used_terminal_and_earliest_expiry(self):
        stmt = _build_upsert_stmt(
            Invitation,
            "invitations",
            {
                "id": 4,
                "account_name": "new-user",
                "mobile_number": "09120000000",
                "token": "invite-token",
                "role": "عادی",
                "created_by_id": 1,
                "is_used": False,
                "expires_at": datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (token)", compiled)
        self.assertNotIn("id = excluded.id", compiled)
        self.assertNotIn("token = excluded.token", compiled)
        self.assertIn("is_used = (coalesce(invitations.is_used", compiled)
        self.assertIn("OR coalesce(excluded.is_used", compiled)
        self.assertIn("least(invitations.expires_at, excluded.expires_at)", compiled)

    def test_telegram_link_token_upsert_keeps_terminal_status_terminal(self):
        stmt = _build_upsert_stmt(
            TelegramLinkToken,
            "telegram_link_tokens",
            {
                "id": 8,
                "user_id": 7,
                "token_hash": "a" * 64,
                "status": "pending",
                "issued_by_server": "iran",
                "expires_at": datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (token_hash)", compiled)
        self.assertNotIn("id = excluded.id", compiled)
        self.assertNotIn("token_hash = excluded.token_hash", compiled)
        self.assertIn("telegram_link_tokens.status NOT IN", compiled)
        self.assertIn("telegram_link_tokens.status = excluded.status", compiled)

    def test_market_runtime_state_upsert_uses_transition_timestamp_guard(self):
        stmt = _build_upsert_stmt(
            MarketRuntimeState,
            "market_runtime_state",
            {
                "id": 1,
                "is_open": True,
                "active_web_notice_visible": False,
                "offers_since_last_open": 3,
                "last_transition_at": datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (id)", compiled)
        self.assertIn("market_runtime_state.last_transition_at IS NULL", compiled)
        self.assertIn("excluded.last_transition_at IS NULL", compiled)
        self.assertIn("market_runtime_state.last_transition_at <= excluded.last_transition_at", compiled)

    async def test_apply_item_ignores_market_runtime_state_when_timestamp_guard_noops(self):
        db = FakeDB([SimpleNamespace(rowcount=0)])

        result = await _apply_item(
            db,
            "market_runtime_state",
            "UPDATE",
            1,
            {
                "is_open": False,
                "active_web_notice_visible": True,
                "offers_since_last_open": 0,
                "last_transition_at": datetime(2026, 6, 27, 15, 30, tzinfo=timezone.utc),
            },
            MarketRuntimeState,
            [],
        )

        self.assertEqual(result, "ignored")
        self.assertEqual(len(db.execute_calls), 1)

    def test_offer_publication_state_upsert_uses_version_and_status_precedence_guard(self):
        stmt = _build_upsert_stmt(
            OfferPublicationState,
            "offer_publication_states",
            {
                "id": 41,
                "offer_public_id": "ofr_41",
                "offer_home_server": "foreign",
                "surface": "telegram_channel",
                "publication_owner_server": "foreign",
                "publisher_bot_identity": "primary",
                "status": "sent",
                "dedupe_key": "offer-publication:telegram_channel:ofr_41",
                "offer_version_id": 3,
                "version_id": 2,
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (dedupe_key)", compiled)
        self.assertIn("offer_publication_states.offer_version_id <= excluded.offer_version_id", compiled)
        self.assertIn("offer_publication_states.offer_version_id < excluded.offer_version_id", compiled)
        self.assertIn("CASE WHEN (excluded.status =", compiled)
        self.assertIn("CASE WHEN (offer_publication_states.status =", compiled)
        self.assertIn("publisher_bot_identity", compiled)

    async def test_user_block_uses_pair_identity_for_upsert_and_delete_resolution(self):
        stmt = _build_upsert_stmt(
            UserBlock,
            "user_blocks",
            {
                "id": 11,
                "blocker_id": 1,
                "blocked_id": 2,
                "created_at": datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT (blocker_id, blocked_id)", compiled)
        self.assertNotIn("id = excluded.id", compiled)
        self.assertTrue(_sync_table_has_public_identity("user_blocks", {"blocker_id": "1", "blocked_id": "2"}))
        self.assertFalse(_sync_table_has_public_identity("user_blocks", {"blocker_id": "1"}))

        db = FakeDB([ScalarOneOrNoneResult(44)])
        resolved_id = await _resolve_local_record_id_by_public_identity(
            db,
            "user_blocks",
            {"blocker_id": "1", "blocked_id": "2"},
        )

        self.assertEqual(resolved_id, 44)

    def test_trade_delivery_receipt_upsert_uses_dedupe_key_and_preserves_identity_fields(self):
        stmt = _build_upsert_stmt(
            TradeDeliveryReceipt,
            "trade_delivery_receipts",
            {
                "id": 41,
                "event_type": "trade_completed",
                "dedupe_key": "trade_completed:webapp:10025:7",
                "trade_number": 10025,
                "recipient_user_id": 7,
                "recipient_role": "offer_owner",
                "channel": "webapp",
                "destination_server": "iran",
                "status": "pending",
                "reason": "webapp_required",
                "worker_id": "foreign-worker-1",
                "lease_until": datetime(2026, 6, 27, 12, 30, tzinfo=timezone.utc),
            },
        )

        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (dedupe_key)", compiled)
        self.assertNotIn("event_type = excluded.event_type", compiled)
        self.assertNotIn("trade_number = excluded.trade_number", compiled)
        self.assertNotIn("recipient_user_id = excluded.recipient_user_id", compiled)
        self.assertNotIn("channel = excluded.channel", compiled)
        self.assertNotIn("destination_server = excluded.destination_server", compiled)
        self.assertNotIn("worker_id = excluded.worker_id", compiled)
        self.assertNotIn("lease_until = excluded.lease_until", compiled)
        self.assertIn("status = excluded.status", compiled)

    async def test_apply_item_handles_trading_settings_and_offer_insert_success(self):
        insert_builder = FakeInsertBuilder()
        db = FakeDB()

        with patch("api.routers.sync.pg_insert", return_value=insert_builder):
            result = await _apply_item(
                db,
                "trading_settings",
                "INSERT",
                1,
                {"key": "offer_expiry_minutes", "value": "15"},
                model=object,
                new_offers=[],
            )
        self.assertEqual(result, "ok")
        self.assertEqual(insert_builder.values_payload, {"key": "offer_expiry_minutes", "value": "15"})
        self.assertEqual(insert_builder.conflict_payload, (["key"], {"value": "15"}, {}))
        self.assertEqual(db.execute_calls[0], ("TRADING_UPSERT", {"is_sync": True}))

        new_offers = []
        offer_data = {
            "price": 12,
            "channel_message_id": 99,
            "home_server": "iran",
            "offer_public_id": "ofr_remote_8",
            "expired_by_user_id": 2,
            "expired_by_actor_user_id": 3,
            "expire_source_surface": "webapp",
            "expire_source_server": "iran",
        }
        db = FakeDB([SimpleNamespace(), ScalarOneOrNoneResult(8)])
        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT") as builder, patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ):
            result = await _apply_item(
                db,
                "offers",
                "INSERT",
                8,
                offer_data,
                model=Offer,
                new_offers=new_offers,
            )
        self.assertEqual(result, "ok")
        self.assertEqual(new_offers, [8])
        self.assertNotIn("channel_message_id", offer_data)
        self.assertNotIn("id", offer_data)
        self.assertEqual(offer_data["home_server"], "iran")
        self.assertEqual(offer_data["offer_public_id"], "ofr_remote_8")
        self.assertEqual(offer_data["expired_by_user_id"], 2)
        self.assertEqual(offer_data["expired_by_actor_user_id"], 3)
        self.assertEqual(offer_data["expire_source_surface"], "webapp")
        self.assertEqual(offer_data["expire_source_server"], "iran")
        builder.assert_called_once_with(Offer, "offers", offer_data)
        self.assertEqual(db.execute_calls[0], ("UPSERT", {"is_sync": True}))

        trade_data = {
            "trade_number": 10008,
            "offer_id": 8,
            "offer_public_id": "ofr_remote_8",
            "offer_user_id": 2,
            "responder_user_id": 5,
            "commodity_id": 1,
            "trade_type": "buy",
            "quantity": 3,
            "price": 120,
            "status": "completed",
        }
        db = FakeDB([ScalarOneOrNoneResult(501), SimpleNamespace()])
        with patch("api.routers.sync._build_upsert_stmt", return_value="TRADE_UPSERT") as builder:
            result = await _apply_item(
                db,
                "trades",
                "INSERT",
                80,
                trade_data,
                model=Trade,
                new_offers=[],
            )
        self.assertEqual(result, "ok")
        self.assertEqual(trade_data["offer_id"], 501)
        self.assertNotIn("id", trade_data)
        builder.assert_called_once()
        self.assertEqual(builder.call_args.args[2]["offer_id"], 501)
        self.assertNotIn("offer_public_id", builder.call_args.args[2])

        db = FakeDB([ScalarOneOrNoneResult(None)])
        with patch("api.routers.sync._build_upsert_stmt", return_value="SHOULD_NOT_RUN") as builder:
            result = await _apply_item(
                db,
                "trades",
                "INSERT",
                81,
                {
                    "trade_number": 10009,
                    "offer_public_id": "ofr_missing",
                    "offer_id": 9,
                    "status": "completed",
                },
                model=Trade,
                new_offers=[],
            )
        self.assertEqual(result, "deferred")
        builder.assert_not_called()

        offer_request_data = {
            "request_home_server": "foreign",
            "local_offer_id": 8,
            "offer_public_id": "ofr_remote_8",
            "requester_user_id": 5,
            "actor_user_id": 5,
            "request_source_surface": "telegram_bot",
            "request_source_server": "foreign",
            "requested_quantity": 12,
            "idempotency_key": "telegram_callback:abc",
            "result_status": "received",
            "internal_failure_context": {"safe_for_admin": True},
        }
        db = FakeDB()
        with patch("api.routers.sync._build_upsert_stmt", return_value="OFFER_REQUEST_UPSERT") as builder:
            result = await _apply_item(
                db,
                "offer_requests",
                "INSERT",
                30,
                offer_request_data,
                model=OfferRequest,
                new_offers=[],
            )
        self.assertEqual(result, "ok")
        self.assertNotIn("id", offer_request_data)
        self.assertIsNone(offer_request_data["local_offer_id"])
        self.assertEqual(offer_request_data["offer_public_id"], "ofr_remote_8")
        self.assertEqual(offer_request_data["request_source_surface"], "telegram_bot")
        self.assertEqual(offer_request_data["internal_failure_context"], {"safe_for_admin": True})
        builder.assert_called_once_with(OfferRequest, "offer_requests", offer_request_data)
        self.assertEqual(db.execute_calls[-1], ("OFFER_REQUEST_UPSERT", {"is_sync": True}))

        publication_data = {
            "offer_id": 8,
            "offer_public_id": "ofr_remote_8",
            "offer_home_server": "iran",
            "surface": "telegram_channel",
            "publication_owner_server": "foreign",
            "status": "pending",
            "dedupe_key": "offer-publication:telegram_channel:ofr_remote_8",
            "offer_version_id": 1,
            "last_known_offer_status": "active",
        }
        db = FakeDB([ScalarOneOrNoneResult(8), SimpleNamespace()])
        with patch("api.routers.sync._build_upsert_stmt", return_value="PUBLICATION_UPSERT") as builder:
            result = await _apply_item(
                db,
                "offer_publication_states",
                "INSERT",
                40,
                publication_data,
                model=OfferPublicationState,
                new_offers=[],
            )
        self.assertEqual(result, "ok")
        self.assertNotIn("id", publication_data)
        self.assertEqual(publication_data["offer_id"], 8)
        self.assertEqual(publication_data["publication_owner_server"], "foreign")
        builder.assert_called_once_with(OfferPublicationState, "offer_publication_states", publication_data)
        self.assertEqual(db.execute_calls[-1], ("PUBLICATION_UPSERT", {"is_sync": True}))

        terminal_offers = []
        terminal_offer_data = {"status": "completed", "channel_message_id": 1001}
        db = FakeDB()
        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ):
            result = await _apply_item(
                db,
                "offers",
                "UPDATE",
                9,
                terminal_offer_data,
                model=object,
                new_offers=[],
                terminal_offers=terminal_offers,
            )
        self.assertEqual(result, "ok")
        self.assertEqual(terminal_offers, [9])
        self.assertNotIn("channel_message_id", terminal_offer_data)

        realtime_offers = []
        iran_offer_update_data = {"status": "active", "remaining_quantity": 6}
        db = FakeDB()
        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await _apply_item(
                db,
                "offers",
                "UPDATE",
                10,
                iran_offer_update_data,
                model=object,
                new_offers=[],
                terminal_offers=realtime_offers,
            )
        self.assertEqual(result, "ok")
        self.assertEqual(realtime_offers, [10])

        relation_data = {
            "owner_user_id": 2,
            "accountant_user_id": 5,
            "created_by_user_id": 2,
            "invitation_token": "invite-token",
            "global_account_name": "acct-user",
            "relation_display_name": "دفتر",
            "duty_description": "books",
            "mobile_number": "09120000000",
            "status": "active",
        }
        db = FakeDB()
        with patch("api.routers.sync._build_upsert_stmt", return_value="RELATION_UPSERT") as builder:
            result = await _apply_item(
                db,
                "accountant_relations",
                "INSERT",
                19,
                relation_data,
                model=object,
                new_offers=[],
            )
        self.assertEqual(result, "ok")
        self.assertNotIn("id", relation_data)
        builder.assert_called_once_with(object, "accountant_relations", relation_data)
        self.assertEqual(db.execute_calls[0], ("RELATION_UPSERT", {"is_sync": True}))

        customer_relation_data = {
            "owner_user_id": 2,
            "customer_user_id": 8,
            "created_by_user_id": 2,
            "invitation_token": "cust-invite-token",
            "management_name": "مشتری مهم",
            "customer_tier": "tier2",
            "commission_rate": "0.7",
            "status": "active",
        }
        db = FakeDB()
        with patch("api.routers.sync._build_upsert_stmt", return_value="CUSTOMER_RELATION_UPSERT") as builder:
            result = await _apply_item(
                db,
                "customer_relations",
                "INSERT",
                23,
                customer_relation_data,
                model=object,
                new_offers=[],
            )
        self.assertEqual(result, "ok")
        self.assertNotIn("id", customer_relation_data)
        builder.assert_called_once_with(object, "customer_relations", customer_relation_data)
        self.assertEqual(db.execute_calls[0], ("CUSTOMER_RELATION_UPSERT", {"is_sync": True}))

    def test_partitioned_sequence_alignment_sql_uses_server_parity(self):
        iran_alter, iran_setval = _partitioned_sequence_alignment_sql("offers_id_seq", "offers", "iran")
        foreign_alter, foreign_setval = _partitioned_sequence_alignment_sql("offers_id_seq", "offers", "foreign")

        self.assertEqual(iran_alter, "ALTER SEQUENCE offers_id_seq INCREMENT BY 2")
        self.assertEqual(foreign_alter, "ALTER SEQUENCE offers_id_seq INCREMENT BY 2")
        self.assertIn("WHEN max_id < 2 THEN 2", iran_setval)
        self.assertIn("MOD(max_id, 2) = 0", iran_setval)
        self.assertIn("WHEN max_id < 1 THEN 1", foreign_setval)
        self.assertIn("MOD(max_id, 2) = 1", foreign_setval)

    async def test_apply_item_merges_unique_violation_by_natural_key(self):
        duplicate_error = Exception("duplicate key value violates unique constraint")
        db = FakeDB([
            __import__("sqlalchemy").exc.IntegrityError("stmt", {}, duplicate_error),
            SimpleNamespace(),
        ])
        model = type("DummyUserModel", (), {"telegram_id": object()})
        update_builder = UpdateBuilder()

        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"), patch(
            "api.routers.sync.update", return_value=update_builder
        ):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                5,
                {"telegram_id": 12345, "full_name": "User Name"},
                model=model,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        self.assertEqual(update_builder.values_payload, {"full_name": "User Name"})
        self.assertEqual(db.execute_calls[1], ("MERGE_UPDATE", {"is_sync": True}))

    async def test_apply_item_merges_market_schedule_override_by_date(self):
        duplicate_error = Exception("duplicate key value violates unique constraint")
        db = FakeDB([
            __import__("sqlalchemy").exc.IntegrityError("stmt", {}, duplicate_error),
            SimpleNamespace(),
        ])
        model = type("MarketScheduleOverrideModel", (), {"date": object()})
        update_builder = UpdateBuilder()
        payload = {
            "date": date(2026, 5, 22),
            "override_type": "closed_all_day",
            "note": "holiday",
        }

        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"), patch(
            "api.routers.sync.update", return_value=update_builder
        ):
            result = await _apply_item(
                db,
                "market_schedule_overrides",
                "INSERT",
                99,
                payload,
                model=model,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        self.assertEqual(update_builder.values_payload, {"override_type": "closed_all_day", "note": "holiday"})
        self.assertEqual(db.execute_calls[1], ("MERGE_UPDATE", {"is_sync": True}))

    async def test_apply_item_merges_mandatory_chat_and_membership_to_local_singletons(self):
        chat_data = {
            "type": "channel",
            "title": "اطلاع‌رسانی",
            "description": "کانال اجباری اطلاع‌رسانی سامانه",
            "is_system": True,
            "is_mandatory": True,
        }
        db = FakeDB([ScalarOneOrNoneResult(44)])
        with patch("api.routers.sync._build_upsert_stmt", return_value="CHAT_UPSERT") as builder:
            result = await _apply_item(
                db,
                "chats",
                "INSERT",
                9,
                chat_data,
                model=object,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        self.assertEqual(chat_data["id"], 44)
        builder.assert_called_once_with(object, "chats", chat_data)
        self.assertEqual(db.execute_calls[-1], ("CHAT_UPSERT", {"is_sync": True}))

        member_data = {
            "chat_id": 9,
            "user_id": 5,
            "role": "member",
            "membership_status": "active",
            "chat_type": "channel",
            "chat_is_system": True,
            "chat_is_mandatory": True,
        }
        db = FakeDB([ScalarOneOrNoneResult(44), ScalarOneOrNoneResult(77)])
        with patch("api.routers.sync._build_upsert_stmt", return_value="MEMBER_UPSERT") as builder:
            result = await _apply_item(
                db,
                "chat_members",
                "INSERT",
                12,
                member_data,
                model=object,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        self.assertEqual(member_data["chat_id"], 44)
        self.assertEqual(member_data["id"], 77)
        self.assertNotIn("chat_type", member_data)
        self.assertNotIn("chat_is_system", member_data)
        self.assertNotIn("chat_is_mandatory", member_data)
        builder.assert_called_once_with(object, "chat_members", member_data)
        self.assertEqual(db.execute_calls[-1], ("MEMBER_UPSERT", {"is_sync": True}))


if __name__ == "__main__":
    unittest.main()
