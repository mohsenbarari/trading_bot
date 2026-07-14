from datetime import datetime, timedelta, timezone
from contextlib import ExitStack
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from core import events


class _FakeConnection:
    def __init__(self, is_sync=False):
        self._is_sync = is_sync
        self.info = {}
        self.execute = MagicMock()

    def get_execution_options(self):
        return {"is_sync": self._is_sync}


class _FakeSyncRedis:
    def __init__(self, *, lpush_error=None, publish_error=None):
        self._lpush_error = lpush_error
        self._publish_error = publish_error
        self.lpush_calls = []
        self.publish_calls = []

    def lpush(self, key, payload):
        self.lpush_calls.append((key, payload))
        if self._lpush_error:
            raise self._lpush_error

    def publish(self, channel, payload):
        self.publish_calls.append((channel, payload))
        if self._publish_error:
            raise self._publish_error


class _FakeInsertResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _MappingResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def mappings(self):
        return self

    def one_or_none(self):
        return self.rows.pop(0) if self.rows else None


def _capture_listeners(registry):
    def fake_listens_for(model, event_name):
        def decorator(func):
            registry[(model.__name__, event_name)] = func
            return func

        return decorator

    return fake_listens_for


class CoreEventsTests(unittest.TestCase):
    def setUp(self):
        events._sync_redis = None
        events._event_listeners_initialized = False

    def test_setup_all_events_is_idempotent(self):
        setup_names = (
            "register_sync_outbox_guards",
            "setup_user_events",
            "setup_accountant_relation_events",
            "setup_customer_relation_events",
            "setup_chat_events",
            "setup_chat_member_events",
            "setup_invitation_events",
            "setup_offer_events",
            "setup_offer_request_events",
            "setup_offer_publication_state_events",
            "setup_trade_events",
            "setup_trade_delivery_receipt_events",
            "setup_telegram_admin_broadcast_events",
            "setup_telegram_notification_outbox_events",
            "setup_commodity_events",
            "setup_commodity_alias_events",
            "setup_trading_settings_events",
            "setup_market_schedule_override_events",
            "setup_market_runtime_state_events",
            "setup_user_block_events",
            "setup_telegram_link_token_events",
            "setup_notification_events",
            "setup_user_notification_preference_events",
            "setup_admin_message_events",
        )
        with ExitStack() as stack:
            patched = {
                name: stack.enter_context(patch(f"core.events.{name}"))
                for name in setup_names
            }
            logger = stack.enter_context(patch.object(events, "logger"))
            events.setup_all_events()
            events.setup_all_events()

        for mocked in patched.values():
            mocked.assert_called_once()
        logger.debug.assert_called_once_with("SQLAlchemy event listeners already initialized")

    def test_registration_user_reference_metadata_uses_natural_identity(self):
        connection = _FakeConnection()
        connection.execute.side_effect = [
            _MappingResult(
                [
                    {
                        "account_name": "owner-account",
                        "mobile_number": "09121112233",
                        "telegram_id": 7001,
                    }
                ]
            ),
            _MappingResult(
                [
                    {
                        "account_name": "child-account",
                        "mobile_number": "09121112234",
                        "telegram_id": None,
                    }
                ]
            ),
        ]
        with patch("core.events.settings.registration_sync_v2_enabled", True):
            result = events._registration_user_references(
                connection,
                {"owner_user_id": 100, "customer_user_id": 101},
            )

        self.assertEqual(
            result["owner_user_id"]["current"]["account_name"],
            "owner-account",
        )
        self.assertEqual(
            result["customer_user_id"]["current"]["mobile_number"],
            "09121112234",
        )
        self.assertNotIn("telegram_id", result["customer_user_id"]["current"])

        with patch("core.events.settings.registration_sync_v2_enabled", False):
            self.assertEqual(
                events._registration_user_references(
                    _FakeConnection(),
                    {"owner_user_id": 100},
                ),
                {},
            )

    def _build_listener_targets(self, now):
        chat = SimpleNamespace(
            id=6,
            type=SimpleNamespace(value='channel'),
            title='اطلاع‌رسانی',
            description='کانال اجباری اطلاع‌رسانی سامانه',
            created_by_id=None,
            is_system=True,
            is_mandatory=True,
            is_deleted=False,
            deleted_at=None,
            max_members=None,
            last_message_at=now,
            created_at=now,
            updated_at=now,
        )
        chat_member = SimpleNamespace(
            id=7,
            chat_id=6,
            user_id=3,
            role=SimpleNamespace(value='admin'),
            membership_status=SimpleNamespace(value='ACTIVE'),
            chat_type='channel',
            chat_is_system=True,
            chat_is_mandatory=True,
            joined_at=now,
            left_at=None,
            last_read_at=now,
            is_marked_unread=False,
            is_muted=False,
            is_pinned=False,
            pinned_at=None,
            pin_order=None,
            is_hidden=False,
            hidden_at=None,
            created_at=now,
            updated_at=now,
        )
        offer = SimpleNamespace(
            id=1,
            offer_public_id='ofr_event_1',
            version_id=2,
            user_id=9,
            actor_user_id=7,
            home_server='foreign',
            offer_type=SimpleNamespace(value='buy'),
            commodity_id=11,
            quantity=20,
            remaining_quantity=15,
            price=100,
            is_wholesale=False,
            lot_sizes=[10, 5],
            original_lot_sizes=[10, 5],
            notes='note',
            status=SimpleNamespace(value='active'),
            channel_message_id=44,
            republished_offer_id=12,
            created_at=now,
            updated_at=now,
            expired_by_user_id=9,
            expired_by_actor_user_id=7,
            expire_source_surface='webapp',
            expire_source_server='iran',
            idempotency_key='idem',
            archived=False,
        )
        trade = SimpleNamespace(
            id=2,
            version_id=3,
            trade_number=10001,
            offer_id=1,
            offer_user_id=3,
            offer_user_mobile='0912',
            responder_user_id=4,
            responder_user_mobile='0935',
            actor_user_id=5,
            commodity_id=11,
            trade_type=SimpleNamespace(value='buy'),
            quantity=7,
            price=90,
            status=SimpleNamespace(value='confirmed'),
            note='n',
            created_at=now,
            confirmed_at=now,
            completed_at=None,
            updated_at=now,
            idempotency_key='trade-1',
            archived=False,
        )
        user = SimpleNamespace(
            id=3,
            telegram_id=99,
            username='user',
            full_name='User Name',
            mobile_number='0912',
            account_name='acct',
            address='addr',
            avatar_file_id='chat-file-user',
            role=SimpleNamespace(value='admin'),
            account_status=SimpleNamespace(value='inactive'),
            deactivated_at=now,
            messenger_grace_expires_at=now,
            messenger_blocked_at=now,
            has_bot_access=True,
            admin_password_hash='hash',
            must_change_password=False,
            home_server='foreign',
            is_deleted=False,
            deleted_at=None,
            can_block_users=True,
            max_blocked_users=5,
            max_daily_trades=2,
            max_active_commodities=3,
            max_daily_requests=4,
            trading_restricted_until=None,
            limitations_expire_at=None,
            trades_count=1,
            commodities_traded_count=2,
            channel_messages_count=3,
            max_sessions=1,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        accountant_relation = SimpleNamespace(
            id=8,
            owner_user_id=3,
            accountant_user_id=4,
            created_by_user_id=3,
            invitation_token='invite-token',
            global_account_name='acct-user',
            relation_display_name='دفتر',
            duty_description='books',
            mobile_number='09120000000',
            status=SimpleNamespace(value='active'),
            expires_at=now,
            activated_at=now,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        commodity = SimpleNamespace(id=1, name='Gold')
        alias = SimpleNamespace(id=2, alias='gold', commodity_id=1)
        block = SimpleNamespace(id=3, blocker_id=8, blocked_id=9, created_at=now)
        setting = SimpleNamespace(key='offer_min_quantity', value='5', updated_at=now)
        invitation = SimpleNamespace(
            id=4,
            account_name='acct',
            mobile_number='0912',
            token='tok',
            short_code='abc',
            role=SimpleNamespace(value='STANDARD'),
            created_by_id=1,
            is_used=False,
            expires_at=now,
            created_at=now,
        )
        notification = SimpleNamespace(
            id=5,
            user_id=1,
            message='hi',
            is_read=False,
            created_at=None,
            level='INFO',
            category='SYSTEM',
            dedupe_key='trade_completed:webapp:10001:1',
            extra_payload={'route': '/users/1'},
        )
        notification_preference = SimpleNamespace(
            id=6,
            user_id=1,
            market_offer_push_enabled=True,
            created_at=now,
            updated_at=now,
        )
        admin_market_message = SimpleNamespace(
            id=10,
            content='market notice',
            created_by_id=3,
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=7,
            published_at=now,
            created_at=now,
            updated_at=None,
        )
        admin_broadcast_message = SimpleNamespace(
            id=11,
            content='broadcast notice',
            created_by_id=3,
            target_groups=['users', 'customers'],
            recipient_count=9,
            published_at=now,
            created_at=now,
        )
        customer_relation = SimpleNamespace(
            id=9,
            owner_user_id=3,
            customer_user_id=5,
            created_by_user_id=3,
            invitation_token='cust-invite-token',
            management_name='مشتری ویژه',
            customer_tier=SimpleNamespace(value='tier2'),
            commission_rate='0.50',
            min_trade_quantity=1,
            max_trade_quantity=10,
            max_daily_trades=3,
            max_daily_commodity_volume=50,
            trading_restricted_until=now,
            status=SimpleNamespace(value='active'),
            expires_at=now,
            activated_at=now,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        offer_request = SimpleNamespace(
            id=30,
            version_id=1,
            request_home_server='foreign',
            local_offer_id=1,
            offer_public_id='ofr_event_1',
            requester_user_id=4,
            actor_user_id=4,
            request_source_surface=SimpleNamespace(value='telegram_bot'),
            request_source_server='foreign',
            requested_quantity=7,
            idempotency_key='telegram_callback:sync',
            received_at=now,
            decided_at=None,
            result_status=SimpleNamespace(value='received'),
            public_failure_code=None,
            public_failure_message=None,
            internal_failure_code=None,
            internal_failure_context=None,
            resulting_trade_id=None,
            customer_relation_id=None,
            customer_owner_user_id=None,
            customer_tier_snapshot=None,
            customer_management_name_snapshot=None,
            customer_commission_rate_snapshot=None,
            customer_commission_context=None,
            archived=False,
            created_at=now,
            updated_at=now,
        )
        offer_publication_state = SimpleNamespace(
            id=31,
            version_id=1,
            offer_id=1,
            offer_public_id='ofr_event_1',
            offer_home_server='foreign',
            surface=SimpleNamespace(value='telegram_channel'),
            publication_owner_server='foreign',
            status=SimpleNamespace(value='pending'),
            dedupe_key='offer-publication:telegram_channel:ofr_event_1',
            surface_resource_id=None,
            telegram_chat_id=None,
            telegram_message_id=None,
            offer_version_id=2,
            last_known_offer_status='active',
            last_attempt_at=now,
            last_success_at=None,
            next_retry_at=None,
            disabled_at=None,
            lagged_at=None,
            error_code=None,
            error_message=None,
            state_metadata={'safe': True},
            archived=False,
            created_at=now,
            updated_at=now,
        )
        trade_delivery_receipt = SimpleNamespace(
            id=41,
            event_type='trade_completed',
            dedupe_key='trade_completed:webapp:10001:1',
            trade_id=2,
            trade_number=10001,
            offer_id=1,
            recipient_user_id=1,
            recipient_role='offer_owner',
            channel=SimpleNamespace(value='webapp'),
            destination_server='iran',
            status=SimpleNamespace(value='pending'),
            reason='webapp_required',
            notification_id=None,
            telegram_message_id=None,
            worker_id='worker-1',
            lease_until=now,
            attempt_count=0,
            next_retry_at=None,
            last_error=None,
            last_error_class=None,
            audit_payload={'receipt': True},
            event_created_at=now,
            sent_at=None,
            terminal_at=None,
            created_at=now,
            updated_at=now,
        )

        return {
            ('Chat', 'after_insert'): chat,
            ('Chat', 'after_update'): chat,
            ('Chat', 'after_delete'): chat,
            ('ChatMember', 'after_insert'): chat_member,
            ('ChatMember', 'after_update'): chat_member,
            ('ChatMember', 'after_delete'): chat_member,
            ('Offer', 'after_insert'): offer,
            ('Offer', 'after_update'): offer,
            ('Offer', 'after_delete'): offer,
            ('OfferRequest', 'after_insert'): offer_request,
            ('OfferRequest', 'after_update'): offer_request,
            ('OfferPublicationState', 'after_insert'): offer_publication_state,
            ('OfferPublicationState', 'after_update'): offer_publication_state,
            ('OfferPublicationState', 'after_delete'): offer_publication_state,
            ('Trade', 'after_insert'): trade,
            ('Trade', 'after_update'): trade,
            ('TradeDeliveryReceipt', 'after_insert'): trade_delivery_receipt,
            ('TradeDeliveryReceipt', 'after_update'): trade_delivery_receipt,
            ('TradeDeliveryReceipt', 'after_delete'): trade_delivery_receipt,
            ('User', 'after_insert'): user,
            ('User', 'after_update'): user,
            ('AccountantRelation', 'after_insert'): accountant_relation,
            ('AccountantRelation', 'after_update'): accountant_relation,
            ('AccountantRelation', 'after_delete'): accountant_relation,
            ('CustomerRelation', 'after_insert'): customer_relation,
            ('CustomerRelation', 'after_update'): customer_relation,
            ('CustomerRelation', 'after_delete'): customer_relation,
            ('Commodity', 'after_insert'): commodity,
            ('Commodity', 'after_update'): commodity,
            ('Commodity', 'after_delete'): commodity,
            ('CommodityAlias', 'after_insert'): alias,
            ('CommodityAlias', 'after_update'): alias,
            ('CommodityAlias', 'after_delete'): alias,
            ('UserBlock', 'after_insert'): block,
            ('UserBlock', 'after_delete'): block,
            ('TradingSetting', 'after_insert'): setting,
            ('TradingSetting', 'after_update'): setting,
            ('Invitation', 'after_insert'): invitation,
            ('Invitation', 'after_update'): invitation,
            ('Invitation', 'after_delete'): invitation,
            ('Notification', 'after_insert'): notification,
            ('Notification', 'after_update'): notification,
            ('Notification', 'after_delete'): notification,
            ('UserNotificationPreference', 'after_insert'): notification_preference,
            ('UserNotificationPreference', 'after_update'): notification_preference,
            ('UserNotificationPreference', 'after_delete'): notification_preference,
            ('AdminMarketMessage', 'after_insert'): admin_market_message,
            ('AdminMarketMessage', 'after_update'): admin_market_message,
            ('AdminMarketMessage', 'after_delete'): admin_market_message,
            ('AdminBroadcastMessage', 'after_insert'): admin_broadcast_message,
            ('AdminBroadcastMessage', 'after_update'): admin_broadcast_message,
            ('AdminBroadcastMessage', 'after_delete'): admin_broadcast_message,
        }

    def test_get_sync_redis_reuses_connection_and_log_change_records_outbox_only(self):
        created_clients = []

        def build_client(**kwargs):
            client = _FakeSyncRedis()
            created_clients.append((kwargs, client))
            return client

        with patch('redis.Redis', side_effect=build_client), patch(
            'core.config.settings',
            SimpleNamespace(redis_host='host', redis_port=6379, sync_signal_redis_timeout_seconds=0.3),
        ):
            first = events._get_sync_redis()
            second = events._get_sync_redis()

        self.assertIs(first, second)
        self.assertEqual(len(created_clients), 1)
        self.assertEqual(created_clients[0][0]['host'], 'host')
        self.assertEqual(created_clients[0][0]['socket_connect_timeout'], 0.3)
        self.assertEqual(created_clients[0][0]['socket_timeout'], 0.3)

        connection = _FakeConnection()
        with patch('core.events._get_sync_redis') as get_sync_redis, patch(
            'core.sync_push.push_sync_direct'
        ) as push_sync_direct:
            connection.execute.return_value = _FakeInsertResult(42)
            events.log_change(connection, 'offers', 5, 'INSERT', {'id': 5})

        connection.execute.assert_called_once()
        get_sync_redis.assert_not_called()
        push_sync_direct.assert_not_called()
        inserted_change_log_data = json.loads(connection.execute.call_args.args[1]['data'])
        self.assertEqual(inserted_change_log_data, {'id': 5})

        with patch('core.events._get_sync_redis') as get_sync_redis, patch(
            'core.sync_push.push_sync_direct'
        ) as push_sync_direct:
            connection.execute.return_value = _FakeInsertResult(43)
            events.log_change(
                connection,
                'offers',
                5,
                'UPDATE',
                {'id': 5, 'offer_public_id': 'ofr_5', 'status': 'active'},
            )

        get_sync_redis.assert_not_called()
        push_sync_direct.assert_not_called()
        inserted_change_log_data = json.loads(connection.execute.call_args.args[1]['data'])
        self.assertEqual(inserted_change_log_data["offer_public_id"], "ofr_5")

        with patch.object(connection, 'execute', side_effect=RuntimeError('sql down')), patch.object(events, 'logger') as logger:
            with self.assertRaises(RuntimeError):
                events.log_change(connection, 'offers', 7, 'DELETE', {'id': 7})
        logger.error.assert_not_called()

    def test_log_change_applies_field_policy_before_outbox_insert(self):
        connection = _FakeConnection()
        dirty_payload = {
            'id': 3,
            'mobile_number': '09120000000',
            'full_name': 'User Name',
            'admin_password_hash': 'bcrypt-secret',
            'must_change_password': True,
            'avatar_file_id': 'chat-file-user',
        }

        with patch('core.events._get_sync_redis') as get_sync_redis, patch(
            'core.sync_push.push_sync_direct'
        ) as push_sync_direct:
            connection.execute.return_value = _FakeInsertResult(44)
            events.log_change(connection, 'users', 3, 'UPDATE', dirty_payload)

        inserted_change_log_data = json.loads(connection.execute.call_args.args[1]['data'])
        get_sync_redis.assert_not_called()
        push_sync_direct.assert_not_called()

        self.assertEqual(inserted_change_log_data['mobile_number'], '09120000000')
        self.assertNotIn('admin_password_hash', inserted_change_log_data)
        self.assertNotIn('must_change_password', inserted_change_log_data)
        self.assertNotIn('avatar_file_id', inserted_change_log_data)
        self.assertNotIn('bcrypt-secret', json.dumps(inserted_change_log_data))
        self.assertNotIn('chat-file-user', json.dumps(inserted_change_log_data))

    def test_publish_event_sync_success_and_failure(self):
        sync_redis = _FakeSyncRedis()
        with patch('core.events._get_sync_redis', return_value=sync_redis), patch.object(events, 'logger') as logger:
            events.publish_event_sync('offer:created', {'id': 1})
        self.assertEqual(sync_redis.publish_calls, [('events:offer:created', '{"id": 1}')])
        logger.info.assert_called_once()

        sync_redis = _FakeSyncRedis(publish_error=RuntimeError('publish down'))
        with patch('core.events._get_sync_redis', return_value=sync_redis), patch.object(events, 'logger') as logger:
            events.publish_event_sync('offer:updated', {'id': 2})
        logger.error.assert_called_once()

    def test_offer_trade_and_user_event_listeners(self):
        registry = {}
        now = datetime(2025, 1, 1, 12, 0, 0)
        with patch('core.events.event.listens_for', side_effect=_capture_listeners(registry)), patch.object(
            events, 'logger'
        ) as logger:
            events.setup_offer_events()
            events.setup_trade_events()
            events.setup_user_events()

        connection = _FakeConnection()
        with patch('core.events.log_change') as log_change, patch('core.events.publish_event_sync') as publish_event_sync:
            offer = SimpleNamespace(
                id=1,
                offer_public_id='ofr_event_1',
                version_id=2,
                user_id=9,
                home_server='foreign',
                offer_type=SimpleNamespace(value='buy'),
                settlement_type=SimpleNamespace(value='tomorrow'),
                commodity_id=11,
                quantity=20,
                remaining_quantity=15,
                price=100,
                is_wholesale=False,
                lot_sizes=[10, 5],
                original_lot_sizes=[10, 5],
                notes='note',
                status=SimpleNamespace(value='active'),
                channel_message_id=44,
                republished_offer_id=12,
                created_at=now,
                updated_at=now,
                expired_by_user_id=9,
                expired_by_actor_user_id=7,
                expire_source_surface='webapp',
                expire_source_server='iran',
                idempotency_key='idem',
                archived=False,
            )
            registry[('Offer', 'after_insert')](None, connection, offer)
            registry[('Offer', 'after_update')](None, connection, offer)
            expired_offer = SimpleNamespace(**{**offer.__dict__, 'status': SimpleNamespace(value='expired')})
            registry[('Offer', 'after_update')](None, connection, expired_offer)
            registry[('Offer', 'after_delete')](None, connection, offer)

            trade_insert = SimpleNamespace(
                id=2,
                version_id=3,
                trade_number=10001,
                offer_id=1,
                offer_user_id=3,
                offer_user_mobile='0912',
                responder_user_id=4,
                responder_user_mobile='0935',
                commodity_id=11,
                trade_type='sell',
                settlement_type='tomorrow',
                quantity=7,
                price=90,
                status='pending',
                note='n',
                created_at=now,
                confirmed_at=now,
                completed_at=None,
                updated_at=now,
                idempotency_key='trade-1',
                archived=False,
            )
            registry[('Trade', 'after_insert')](None, connection, trade_insert)
            trade_update = SimpleNamespace(**{**trade_insert.__dict__, 'trade_type': SimpleNamespace(value='buy'), 'status': SimpleNamespace(value='confirmed')})
            registry[('Trade', 'after_update')](None, connection, trade_update)

            user = SimpleNamespace(
                id=3,
                telegram_id=99,
                username='user',
                full_name='User Name',
                mobile_number='0912',
                account_name='acct',
                address='addr',
                avatar_file_id='chat-file-user',
                role=SimpleNamespace(value='admin'),
                account_status=SimpleNamespace(value='inactive'),
                deactivated_at=now,
                messenger_grace_expires_at=now,
                messenger_blocked_at=now,
                has_bot_access=True,
                admin_password_hash='hash',
                must_change_password=False,
                home_server='foreign',
                is_deleted=False,
                deleted_at=None,
                can_block_users=True,
                max_blocked_users=5,
                max_daily_trades=2,
                max_active_commodities=3,
                max_daily_requests=4,
                trading_restricted_until=None,
                limitations_expire_at=None,
                trades_count=1,
                commodities_traded_count=2,
                channel_messages_count=3,
                max_sessions=1,
                max_customers=5,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            registry[('User', 'after_insert')](None, connection, user)
            registry[('User', 'after_update')](None, connection, user)

        self.assertGreaterEqual(log_change.call_count, 8)
        user_payloads = [call.args[4] for call in log_change.call_args_list if call.args[1] == 'users']
        self.assertTrue(user_payloads)
        for payload in user_payloads:
            self.assertEqual(payload['global_lock_grace_expires_at'], payload['messenger_grace_expires_at'])
            self.assertEqual(payload['global_web_locked_at'], payload['messenger_blocked_at'])
            self.assertEqual(payload['max_customers'], 5)
            self.assertNotIn('admin_password_hash', payload)
            self.assertNotIn('must_change_password', payload)
            self.assertNotIn('avatar_file_id', payload)
        offer_payloads = [call.args[4] for call in log_change.call_args_list if call.args[1] == 'offers' and call.args[3] != 'DELETE']
        self.assertTrue(offer_payloads)
        for payload in offer_payloads:
            self.assertEqual(payload['offer_public_id'], 'ofr_event_1')
            self.assertEqual(payload['settlement_type'], 'tomorrow')
            self.assertIn('commodity_name', payload)
            self.assertIn('republished_offer_public_id', payload)
            self.assertEqual(payload['expired_by_user_id'], 9)
            self.assertEqual(payload['expired_by_actor_user_id'], 7)
            self.assertEqual(payload['expire_source_surface'], 'webapp')
            self.assertEqual(payload['expire_source_server'], 'iran')
        trade_payloads = [call.args[4] for call in log_change.call_args_list if call.args[1] == 'trades']
        self.assertTrue(trade_payloads)
        for payload in trade_payloads:
            self.assertEqual(payload['settlement_type'], 'tomorrow')
        publish_event_sync.assert_any_call('offer:created', unittest.mock.ANY)
        publish_event_sync.assert_any_call('offer:updated', unittest.mock.ANY)
        publish_event_sync.assert_any_call('offer:expired', {'id': 1})
        publish_event_sync.assert_any_call('offer:deleted', {'id': 1, 'offer_public_id': 'ofr_event_1'})
        logger.info.assert_any_call('✅ Offer event listeners registered')
        logger.info.assert_any_call('✅ Trade event listeners registered')
        logger.info.assert_any_call('✅ User event listeners registered')

        sync_connection = _FakeConnection(is_sync=True)
        with patch('core.events.log_change') as log_change, patch('core.events.publish_event_sync') as publish_event_sync:
            registry[('Offer', 'after_insert')](None, sync_connection, offer)
        log_change.assert_not_called()
        publish_event_sync.assert_not_called()

    def test_registration_v2_user_insert_emits_only_iran_owned_fields(self):
        registry = {}
        now = datetime(2026, 7, 11, 10, 0, 0)
        with patch(
            "core.events.event.listens_for",
            side_effect=_capture_listeners(registry),
        ):
            events.setup_user_events()

        user = SimpleNamespace(
            id=3,
            telegram_id=99,
            username="user",
            full_name="User Name",
            mobile_number="09120000000",
            account_name="acct",
            address="registered address",
            role=SimpleNamespace(value="عادی"),
            account_status=SimpleNamespace(value="active"),
            deactivated_at=None,
            messenger_grace_expires_at=None,
            messenger_blocked_at=None,
            has_bot_access=True,
            bot_onboarding_required_step=2,
            bot_onboarding_completed_step=1,
            bot_onboarding_completed_at=now,
            home_server="iran",
            is_deleted=False,
            deleted_at=None,
            can_block_users=True,
            max_blocked_users=5,
            max_daily_trades=2,
            max_active_commodities=3,
            max_daily_requests=4,
            trading_restricted_until=None,
            limitations_expire_at=None,
            trades_count=11,
            commodities_traded_count=12,
            channel_messages_count=13,
            max_sessions=1,
            max_accountants=3,
            max_customers=5,
            last_seen_at=now,
            sync_version=1,
            created_at=now,
            updated_at=now,
        )
        connection = _FakeConnection()

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode",
            "iran",
        ), patch("core.events.log_change") as log_change:
            registry[("User", "after_insert")](None, connection, user)

        payload = log_change.call_args.args[4]
        self.assertEqual(payload["home_server"], "iran")
        self.assertEqual(payload["sync_version"], 1)
        self.assertNotIn("bot_onboarding_required_step", payload)
        self.assertNotIn("bot_onboarding_completed_step", payload)
        self.assertNotIn("bot_onboarding_completed_at", payload)
        self.assertNotIn("trades_count", payload)
        self.assertNotIn("commodities_traded_count", payload)
        self.assertNotIn("channel_messages_count", payload)
        self.assertNotIn("global_lock_grace_expires_at", payload)
        self.assertNotIn("global_web_locked_at", payload)

        with patch("core.events.settings.registration_sync_v2_enabled", False), patch(
            "core.events.settings.server_mode",
            "iran",
        ), patch("core.events.log_change") as legacy_log_change:
            registry[("User", "after_insert")](None, connection, user)

        legacy_payload = legacy_log_change.call_args.args[4]
        self.assertNotIn("sync_version", legacy_payload)
        self.assertNotIn("_sync_identity", legacy_payload)

    def test_offer_request_event_listener_syncs_ledger_payload(self):
        registry = {}
        now = datetime(2025, 1, 1, 12, 0, 0)
        with patch('core.events.event.listens_for', side_effect=_capture_listeners(registry)), patch.object(
            events, 'logger'
        ) as logger:
            events.setup_offer_request_events()

        target = SimpleNamespace(
            id=30,
            version_id=2,
            request_home_server='foreign',
            local_offer_id=7,
            offer_public_id='ofr_request_30',
            requester_user_id=5,
            actor_user_id=6,
            request_source_surface=SimpleNamespace(value='telegram_bot'),
            request_source_server='foreign',
            requested_quantity=12,
            idempotency_key='telegram_callback:abc',
            received_at=now,
            decided_at=now,
            result_status=SimpleNamespace(value='completed_trade'),
            public_failure_code=None,
            public_failure_message=None,
            internal_failure_code='internal',
            internal_failure_context={'redacted': True},
            resulting_trade_id=88,
            resulting_trade=SimpleNamespace(trade_number=10088),
            customer_relation_id=17,
            customer_owner_user_id=4,
            customer_tier_snapshot='tier2',
            customer_management_name_snapshot='VIP',
            customer_commission_rate_snapshot='0.70',
            customer_commission_context={'commission': 'snapshot'},
            archived=False,
            created_at=now,
            updated_at=now,
        )
        connection = _FakeConnection()

        with patch('core.events.log_change') as log_change:
            registry[('OfferRequest', 'after_insert')](None, connection, target)
            registry[('OfferRequest', 'after_update')](None, connection, target)

        self.assertEqual(log_change.call_count, 2)
        for call in log_change.call_args_list:
            self.assertEqual(call.args[1], 'offer_requests')
            payload = call.args[4]
            self.assertEqual(payload['offer_public_id'], 'ofr_request_30')
            self.assertEqual(payload['request_source_surface'], 'telegram_bot')
            self.assertEqual(payload['request_source_server'], 'foreign')
            self.assertEqual(payload['result_status'], 'completed_trade')
            self.assertEqual(payload['internal_failure_context'], {'redacted': True})
            self.assertEqual(payload['customer_management_name_snapshot'], 'VIP')
            self.assertEqual(payload['resulting_trade_number'], 10088)
            self.assertIn('customer_relation_invitation_token', payload)
            self.assertNotIn('resulting_trade_id', payload)

        sync_connection = _FakeConnection(is_sync=True)
        registry[('OfferRequest', 'after_insert')](None, sync_connection, target)
        self.assertEqual(log_change.call_count, 2)
        logger.info.assert_any_call('✅ OfferRequest event listeners registered')

    def test_offer_publication_state_event_listener_syncs_surface_payload(self):
        registry = {}
        now = datetime(2026, 5, 21, 10, 0, 0)
        with patch('core.events.event.listens_for', side_effect=_capture_listeners(registry)), patch.object(
            events, 'logger'
        ) as logger:
            events.setup_offer_publication_state_events()

        target = SimpleNamespace(
            id=31,
            version_id=2,
            offer_id=1,
            offer_public_id='ofr_event_1',
            offer_home_server='foreign',
            surface=SimpleNamespace(value='telegram_channel'),
            publication_owner_server='foreign',
            status=SimpleNamespace(value='failed'),
            dedupe_key='offer-publication:telegram_channel:ofr_event_1',
            surface_resource_id=None,
            telegram_chat_id=-100123,
            telegram_message_id=700,
            offer_version_id=2,
            last_known_offer_status='active',
            last_attempt_at=now,
            last_success_at=None,
            next_retry_at=None,
            disabled_at=None,
            lagged_at=None,
            error_code='telegram_send_failed',
            error_message='temporary failure',
            state_metadata={'retryable': True},
            archived=False,
            created_at=now,
            updated_at=None,
        )
        connection = _FakeConnection()

        with patch('core.events.log_change') as log_change:
            registry[('OfferPublicationState', 'after_insert')](None, connection, target)
            registry[('OfferPublicationState', 'after_update')](None, connection, target)

        self.assertEqual(log_change.call_count, 2)
        payload = log_change.call_args_list[0].args[4]
        self.assertEqual(payload['surface'], 'telegram_channel')
        self.assertEqual(payload['publication_owner_server'], 'foreign')
        self.assertEqual(payload['status'], 'failed')
        self.assertEqual(payload['dedupe_key'], 'offer-publication:telegram_channel:ofr_event_1')
        self.assertEqual(payload['telegram_message_id'], 700)
        self.assertEqual(payload['error_code'], 'telegram_send_failed')
        self.assertEqual(payload['state_metadata'], {'retryable': True})

        sync_connection = _FakeConnection(is_sync=True)
        registry[('OfferPublicationState', 'after_insert')](None, sync_connection, target)
        self.assertEqual(log_change.call_count, 2)
        logger.info.assert_any_call('✅ OfferPublicationState event listeners registered')

    def test_remaining_event_listener_groups_and_setup_all_events(self):
        registry = {}
        now = datetime(2025, 1, 1, 12, 0, 0)
        with patch('core.events.event.listens_for', side_effect=_capture_listeners(registry)), patch.object(
            events, 'logger'
        ) as logger:
            events.setup_accountant_relation_events()
            events.setup_customer_relation_events()
            events.setup_chat_events()
            events.setup_chat_member_events()
            events.setup_commodity_events()
            events.setup_commodity_alias_events()
            events.setup_user_block_events()
            events.setup_trading_settings_events()
            events.setup_invitation_events()
            events.setup_offer_request_events()
            events.setup_offer_publication_state_events()
            events.setup_trade_delivery_receipt_events()
            events.setup_notification_events()
            events.setup_user_notification_preference_events()
            events.setup_telegram_link_token_events()
            events.setup_admin_message_events()

        connection = _FakeConnection()
        with patch('core.events.log_change') as log_change:
            chat = SimpleNamespace(
                id=6,
                type=SimpleNamespace(value='channel'),
                title='اطلاع‌رسانی',
                description='کانال اجباری اطلاع‌رسانی سامانه',
                created_by_id=None,
                is_system=True,
                is_mandatory=True,
                is_deleted=False,
                deleted_at=None,
                max_members=None,
                last_message_at=now,
                created_at=now,
                updated_at=now,
            )
            registry[('Chat', 'after_insert')](None, connection, chat)
            registry[('Chat', 'after_update')](None, connection, chat)
            registry[('Chat', 'after_delete')](None, connection, chat)

            chat_member = SimpleNamespace(
                id=7,
                chat_id=6,
                user_id=3,
                role=SimpleNamespace(value='admin'),
                membership_status=SimpleNamespace(value='ACTIVE'),
                chat_type='channel',
                chat_is_system=True,
                chat_is_mandatory=True,
                joined_at=now,
                left_at=None,
                last_read_at=now,
                is_marked_unread=False,
                is_muted=False,
                is_pinned=False,
                pinned_at=None,
                pin_order=None,
                is_hidden=False,
                hidden_at=None,
                created_at=now,
                updated_at=now,
            )
            registry[('ChatMember', 'after_insert')](None, connection, chat_member)
            registry[('ChatMember', 'after_update')](None, connection, chat_member)
            registry[('ChatMember', 'after_delete')](None, connection, chat_member)

            commodity = SimpleNamespace(id=1, name='Gold')
            registry[('Commodity', 'after_insert')](None, connection, commodity)
            registry[('Commodity', 'after_update')](None, connection, commodity)
            registry[('Commodity', 'after_delete')](None, connection, commodity)

            alias = SimpleNamespace(id=2, alias='gold', commodity_id=1)
            registry[('CommodityAlias', 'after_insert')](None, connection, alias)
            registry[('CommodityAlias', 'after_update')](None, connection, alias)
            registry[('CommodityAlias', 'after_delete')](None, connection, alias)

            block = SimpleNamespace(id=3, blocker_id=8, blocked_id=9, created_at=now)
            registry[('UserBlock', 'after_insert')](None, connection, block)
            registry[('UserBlock', 'after_delete')](None, connection, block)

            setting = SimpleNamespace(key='offer_min_quantity', value='5', updated_at=now)
            registry[('TradingSetting', 'after_insert')](None, connection, setting)
            registry[('TradingSetting', 'after_update')](None, connection, setting)

            invitation = SimpleNamespace(
                id=4,
                account_name='acct',
                mobile_number='0912',
                token='tok',
                short_code='abc',
                role=SimpleNamespace(value='STANDARD'),
                created_by_id=1,
                is_used=False,
                expires_at=now,
                created_at=now,
            )
            registry[('Invitation', 'after_insert')](None, connection, invitation)
            registry[('Invitation', 'after_update')](None, connection, invitation)
            registry[('Invitation', 'after_delete')](None, connection, invitation)

            publication_state = SimpleNamespace(
                id=31,
                version_id=1,
                offer_id=1,
                offer_public_id='ofr_event_1',
                offer_home_server='foreign',
                surface=SimpleNamespace(value='telegram_channel'),
                publication_owner_server='foreign',
                status=SimpleNamespace(value='pending'),
                dedupe_key='offer-publication:telegram_channel:ofr_event_1',
                surface_resource_id=None,
                telegram_chat_id=None,
                telegram_message_id=None,
                offer_version_id=2,
                last_known_offer_status='active',
                last_attempt_at=now,
                last_success_at=None,
                next_retry_at=None,
                disabled_at=None,
                lagged_at=None,
                error_code=None,
                error_message=None,
                state_metadata=None,
                archived=False,
                created_at=now,
                updated_at=None,
            )
            registry[('OfferPublicationState', 'after_insert')](None, connection, publication_state)
            registry[('OfferPublicationState', 'after_update')](None, connection, publication_state)
            registry[('OfferPublicationState', 'after_delete')](None, connection, publication_state)

            notification = SimpleNamespace(
                id=5,
                user_id=1,
                message='hi',
                is_read=False,
                created_at=None,
                level='INFO',
                category='SYSTEM',
                dedupe_key='trade_completed:webapp:10001:1',
                extra_payload={'route': '/users/1'},
            )
            registry[('Notification', 'after_insert')](None, connection, notification)
            registry[('Notification', 'after_update')](None, connection, notification)
            registry[('Notification', 'after_delete')](None, connection, notification)

            trade_delivery_receipt = SimpleNamespace(
                id=41,
                event_type='trade_completed',
                dedupe_key='trade_completed:webapp:10001:1',
                trade_id=2,
                trade_number=10001,
                offer_id=1,
                recipient_user_id=1,
                recipient_role='offer_owner',
                channel=SimpleNamespace(value='webapp'),
                destination_server='iran',
                status=SimpleNamespace(value='pending'),
                reason='webapp_required',
                notification_id=None,
                telegram_message_id=None,
                worker_id='worker-1',
                lease_until=now,
                attempt_count=0,
                next_retry_at=None,
                last_error=None,
                last_error_class=None,
                audit_payload={'receipt': True},
                event_created_at=now,
                sent_at=None,
                terminal_at=None,
                created_at=now,
                updated_at=now,
            )
            registry[('TradeDeliveryReceipt', 'after_insert')](None, connection, trade_delivery_receipt)
            registry[('TradeDeliveryReceipt', 'after_update')](None, connection, trade_delivery_receipt)
            registry[('TradeDeliveryReceipt', 'after_delete')](None, connection, trade_delivery_receipt)

            notification_preference = SimpleNamespace(
                id=6,
                user_id=1,
                market_offer_push_enabled=True,
                created_at=now,
                updated_at=now,
            )
            registry[('UserNotificationPreference', 'after_insert')](None, connection, notification_preference)
            registry[('UserNotificationPreference', 'after_update')](None, connection, notification_preference)
            registry[('UserNotificationPreference', 'after_delete')](None, connection, notification_preference)

            telegram_link_token = SimpleNamespace(
                id=10,
                user_id=1,
                token_hash='hashed-token-value',
                status=SimpleNamespace(value='pending'),
                issued_by_server='iran',
                expires_at=now,
                used_at=None,
                used_telegram_id=None,
                revoked_at=None,
                created_at=now,
                updated_at=now,
            )
            registry[('TelegramLinkToken', 'after_insert')](None, connection, telegram_link_token)
            registry[('TelegramLinkToken', 'after_update')](None, connection, telegram_link_token)
            registry[('TelegramLinkToken', 'after_delete')](None, connection, telegram_link_token)

            accountant_relation = SimpleNamespace(
                id=8,
                owner_user_id=3,
                accountant_user_id=4,
                created_by_user_id=3,
                invitation_token='invite-token',
                global_account_name='acct-user',
                relation_display_name='دفتر',
                duty_description='books',
                mobile_number='09120000000',
                status=SimpleNamespace(value='active'),
                expires_at=now,
                activated_at=now,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            )
            registry[('AccountantRelation', 'after_insert')](None, connection, accountant_relation)
            registry[('AccountantRelation', 'after_update')](None, connection, accountant_relation)
            registry[('AccountantRelation', 'after_delete')](None, connection, accountant_relation)

            customer_relation = SimpleNamespace(
                id=9,
                owner_user_id=3,
                customer_user_id=5,
                created_by_user_id=3,
                invitation_token='cust-invite-token',
                management_name='مشتری ویژه',
                customer_tier=SimpleNamespace(value='tier2'),
                commission_rate='0.50',
                min_trade_quantity=1,
                max_trade_quantity=10,
                max_daily_trades=3,
                max_daily_commodity_volume=50,
                trading_restricted_until=now,
                status=SimpleNamespace(value='active'),
                expires_at=now,
                activated_at=now,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            )
            registry[('CustomerRelation', 'after_insert')](None, connection, customer_relation)
            registry[('CustomerRelation', 'after_update')](None, connection, customer_relation)
            registry[('CustomerRelation', 'after_delete')](None, connection, customer_relation)

        self.assertGreaterEqual(log_change.call_count, 37)
        telegram_link_payloads = [
            call.args[4]
            for call in log_change.call_args_list
            if call.args[1] == 'telegram_link_tokens' and call.args[3] != 'DELETE'
        ]
        self.assertEqual(telegram_link_payloads[0]['token_hash'], 'hashed-token-value')
        self.assertEqual(telegram_link_payloads[0]['status'], 'pending')
        self.assertNotIn('raw_token', telegram_link_payloads[0])
        user_block_delete_payloads = [
            call.args[4]
            for call in log_change.call_args_list
            if call.args[1] == 'user_blocks' and call.args[3] == 'DELETE'
        ]
        self.assertEqual(user_block_delete_payloads[0]['blocker_id'], 8)
        self.assertEqual(user_block_delete_payloads[0]['blocked_id'], 9)
        self.assertIn('created_at', user_block_delete_payloads[0])
        receipt_payloads = [
            call.args[4]
            for call in log_change.call_args_list
            if call.args[1] == 'trade_delivery_receipts' and call.args[3] != 'DELETE'
        ]
        self.assertTrue(receipt_payloads)
        for payload in receipt_payloads:
            self.assertEqual(payload['trade_number'], 10001)
            self.assertNotIn('trade_id', payload)
            self.assertNotIn('offer_id', payload)
            self.assertNotIn('notification_id', payload)
        logger.info.assert_any_call('✅ AccountantRelation event listeners registered')
        logger.info.assert_any_call('✅ CustomerRelation event listeners registered')
        logger.info.assert_any_call('✅ Chat event listeners registered')
        logger.info.assert_any_call('✅ ChatMember event listeners registered')
        logger.info.assert_any_call('✅ Commodity event listeners registered')
        logger.info.assert_any_call('✅ CommodityAlias event listeners registered')
        logger.info.assert_any_call('✅ UserBlock event listeners registered')
        logger.info.assert_any_call('✅ TradingSetting event listeners registered')
        logger.info.assert_any_call('✅ Invitation event listeners registered')
        logger.info.assert_any_call('✅ OfferPublicationState event listeners registered')
        logger.info.assert_any_call('✅ TradeDeliveryReceipt event listeners registered')
        logger.info.assert_any_call('✅ Notification event listeners registered')
        logger.info.assert_any_call('✅ UserNotificationPreference event listeners registered')
        logger.info.assert_any_call('✅ TelegramLinkToken event listeners registered')
        logger.info.assert_any_call('✅ AdminMessage event listeners registered')

        with ExitStack() as stack:
            setup_user_events = stack.enter_context(patch('core.events.setup_user_events'))
            setup_accountant_relation_events = stack.enter_context(patch('core.events.setup_accountant_relation_events'))
            setup_customer_relation_events = stack.enter_context(patch('core.events.setup_customer_relation_events'))
            setup_chat_events = stack.enter_context(patch('core.events.setup_chat_events'))
            setup_chat_member_events = stack.enter_context(patch('core.events.setup_chat_member_events'))
            setup_invitation_events = stack.enter_context(patch('core.events.setup_invitation_events'))
            setup_offer_events = stack.enter_context(patch('core.events.setup_offer_events'))
            setup_offer_request_events = stack.enter_context(patch('core.events.setup_offer_request_events'))
            setup_offer_publication_state_events = stack.enter_context(patch('core.events.setup_offer_publication_state_events'))
            setup_trade_events = stack.enter_context(patch('core.events.setup_trade_events'))
            setup_trade_delivery_receipt_events = stack.enter_context(patch('core.events.setup_trade_delivery_receipt_events'))
            setup_commodity_events = stack.enter_context(patch('core.events.setup_commodity_events'))
            setup_commodity_alias_events = stack.enter_context(patch('core.events.setup_commodity_alias_events'))
            setup_trading_settings_events = stack.enter_context(patch('core.events.setup_trading_settings_events'))
            setup_user_block_events = stack.enter_context(patch('core.events.setup_user_block_events'))
            setup_notification_events = stack.enter_context(patch('core.events.setup_notification_events'))
            setup_user_notification_preference_events = stack.enter_context(patch('core.events.setup_user_notification_preference_events'))
            setup_telegram_link_token_events = stack.enter_context(patch('core.events.setup_telegram_link_token_events'))
            setup_admin_message_events = stack.enter_context(patch('core.events.setup_admin_message_events'))
            register_sync_outbox_guards = stack.enter_context(patch('core.events.register_sync_outbox_guards'))
            logger = stack.enter_context(patch.object(events, 'logger'))
            events.setup_all_events()

        register_sync_outbox_guards.assert_called_once()
        setup_user_events.assert_called_once()
        setup_accountant_relation_events.assert_called_once()
        setup_customer_relation_events.assert_called_once()
        setup_chat_events.assert_called_once()
        setup_chat_member_events.assert_called_once()
        setup_invitation_events.assert_called_once()
        setup_offer_events.assert_called_once()
        setup_offer_request_events.assert_called_once()
        setup_offer_publication_state_events.assert_called_once()
        setup_trade_events.assert_called_once()
        setup_trade_delivery_receipt_events.assert_called_once()
        setup_commodity_events.assert_called_once()
        setup_commodity_alias_events.assert_called_once()
        setup_trading_settings_events.assert_called_once()
        setup_user_block_events.assert_called_once()
        setup_notification_events.assert_called_once()
        setup_user_notification_preference_events.assert_called_once()
        setup_telegram_link_token_events.assert_called_once()
        setup_admin_message_events.assert_called_once()
        logger.info.assert_called_with('🎯 All event listeners initialized')
        self.assertIs(events.setup_event_listeners, events.setup_all_events)

    def test_listener_sync_short_circuit_and_error_paths(self):
        registry = {}
        now = datetime(2025, 1, 1, 12, 0, 0)
        with patch('core.events.event.listens_for', side_effect=_capture_listeners(registry)):
            events.setup_offer_events()
            events.setup_trade_events()
            events.setup_user_events()
            events.setup_accountant_relation_events()
            events.setup_customer_relation_events()
            events.setup_chat_events()
            events.setup_chat_member_events()
            events.setup_commodity_events()
            events.setup_commodity_alias_events()
            events.setup_user_block_events()
            events.setup_trading_settings_events()
            events.setup_invitation_events()
            events.setup_offer_request_events()
            events.setup_offer_publication_state_events()
            events.setup_trade_delivery_receipt_events()
            events.setup_notification_events()
            events.setup_user_notification_preference_events()
            events.setup_admin_message_events()

        targets = self._build_listener_targets(now)

        sync_connection = _FakeConnection(is_sync=True)
        with patch('core.events.log_change') as log_change, patch('core.events.publish_event_sync') as publish_event_sync:
            for key, target in targets.items():
                registry[key](None, sync_connection, target)

        log_change.assert_not_called()
        publish_event_sync.assert_not_called()

        connection = _FakeConnection()
        with patch('core.events.log_change', side_effect=RuntimeError('boom')), patch.object(events, 'logger') as logger:
            for key, target in targets.items():
                registry[key](None, connection, target)

        self.assertEqual(logger.error.call_count, len(targets))

    def test_commodity_insert_ignores_cache_import_failures(self):
        registry = {}
        with patch('core.events.event.listens_for', side_effect=_capture_listeners(registry)):
            events.setup_commodity_events()

        real_import = __import__

        def failing_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'core.cache':
                raise ImportError('cache unavailable')
            return real_import(name, globals, locals, fromlist, level)

        with patch('core.events.log_change') as log_change, patch('builtins.__import__', side_effect=failing_import):
            registry[('Commodity', 'after_insert')](None, _FakeConnection(), SimpleNamespace(id=7, name='Silver'))

        log_change.assert_called_once_with(unittest.mock.ANY, 'commodities', 7, 'INSERT', {'id': 7, 'name': 'Silver'})

    def test_lookup_chat_sync_flags_and_chat_member_payload_fallback_paths(self):
        mapping_result = SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: {'type': 'group', 'is_system': False, 'is_mandatory': False}))
        connection = SimpleNamespace(execute=MagicMock(return_value=mapping_result))
        self.assertEqual(events._lookup_chat_sync_flags(connection, 42), ('group', False, False))

        none_result = SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))
        none_connection = SimpleNamespace(execute=MagicMock(return_value=none_result))
        self.assertEqual(events._lookup_chat_sync_flags(none_connection, 42), (None, None, None))

        failing_connection = SimpleNamespace(execute=MagicMock(side_effect=RuntimeError('db failed')))
        self.assertEqual(events._lookup_chat_sync_flags(failing_connection, 42), (None, None, None))

        target = SimpleNamespace(
            id=1,
            chat_id=42,
            user_id=8,
            role=None,
            membership_status=None,
            joined_at=None,
            left_at=None,
            last_read_at=None,
            is_muted=False,
            is_marked_unread=False,
            is_pinned=False,
            pinned_at=None,
            pin_order=None,
            is_hidden=False,
            hidden_at=None,
            created_at=None,
            updated_at=None,
            chat_type=None,
            chat_is_system=None,
            chat_is_mandatory=None,
        )
        payload = events._chat_member_sync_payload(none_connection, target)
        self.assertIsNone(payload['chat_type'])
        self.assertIsNone(payload['chat_is_system'])
        self.assertIsNone(payload['chat_is_mandatory'])

    def test_market_schedule_override_and_runtime_state_event_groups(self):
        registry = {}
        with patch('core.events.event.listens_for', side_effect=_capture_listeners(registry)), patch.object(events, 'logger') as logger:
            events.setup_market_schedule_override_events()
            events.setup_market_runtime_state_events()

        now = datetime(2025, 1, 1, 12, 0, 0)
        connection = _FakeConnection()

        override_target = SimpleNamespace(
            id=11,
            date=None,
            override_type=None,
            open_time_local=None,
            close_time_local=None,
            note='manual close',
            created_by_user_id=1,
            created_at=now,
            updated_at=now,
        )
        runtime_target = SimpleNamespace(
            id=1,
            is_open=False,
            active_web_notice_visible=True,
            offers_since_last_open=2,
            last_transition_at=None,
            created_at=now,
            updated_at=now,
        )

        with patch('core.events.log_change') as log_change:
            registry[('MarketScheduleOverride', 'after_insert')](None, connection, override_target)
            registry[('MarketScheduleOverride', 'after_update')](None, connection, override_target)
            registry[('MarketScheduleOverride', 'after_delete')](None, connection, override_target)

            registry[('MarketRuntimeState', 'after_insert')](None, connection, runtime_target)
            registry[('MarketRuntimeState', 'after_update')](None, connection, runtime_target)
            registry[('MarketRuntimeState', 'after_delete')](None, connection, runtime_target)

        self.assertEqual(log_change.call_count, 6)
        logger.info.assert_any_call('✅ MarketScheduleOverride event listeners registered')
        logger.info.assert_any_call('✅ MarketRuntimeState event listeners registered')

    def test_registration_helpers_fail_closed_on_inspection_reference_and_counter_history(self):
        with patch("core.events.sa_inspect", side_effect=RuntimeError("detached")):
            self.assertEqual(events._changed_column_fields(object()), set())
        target = SimpleNamespace(sync_version="invalid")
        events._bump_sync_version(target)
        self.assertEqual(target.sync_version, 2)

        connection = _FakeConnection()
        with patch("core.events.settings.registration_sync_v2_enabled", True):
            self.assertEqual(
                events._registration_user_references(
                    connection, {"owner_user_id": None}
                ),
                {},
            )
        connection.execute.assert_not_called()

        for row, error in (
            (None, "reference_missing"),
            ({"account_name": "", "mobile_number": None, "telegram_id": None}, "identity_missing"),
        ):
            failing = _FakeConnection()
            failing.execute.return_value = _MappingResult([] if row is None else [row])
            with patch(
                "core.events.settings.registration_sync_v2_enabled", True
            ), self.assertRaisesRegex(RuntimeError, error):
                events._registration_user_references(
                    failing, {"owner_user_id": 8}
                )

        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        target = SimpleNamespace(id=8)

        def counter(epoch, occurred_at):
            return SimpleNamespace(
                event_id="evt-stage9",
                kind="reset",
                epoch=epoch,
                deltas={},
                occurred_at=occurred_at,
            )

        first_conflict = _FakeConnection()
        first_conflict.execute.return_value = SimpleNamespace(first=lambda: (1, now))
        with self.assertRaises(events.InvalidUserCounterMutation):
            events._record_local_user_counter_event(
                first_conflict, target, counter(2, now + timedelta(seconds=1)), "iran"
            )

        missing_history = _FakeConnection()
        missing_history.execute.return_value = SimpleNamespace(first=lambda: None)
        with self.assertRaises(events.InvalidUserCounterMutation):
            events._record_local_user_counter_event(
                missing_history, target, counter(3, now + timedelta(seconds=1)), "iran"
            )

        stale_boundary = _FakeConnection()
        stale_boundary.execute.return_value = SimpleNamespace(first=lambda: (2, now))
        with self.assertRaises(events.InvalidUserCounterMutation):
            events._record_local_user_counter_event(
                stale_boundary, target, counter(3, now), "iran"
            )

        valid_boundary = _FakeConnection()
        valid_boundary.execute.side_effect = [
            SimpleNamespace(first=lambda: (2, now)),
            SimpleNamespace(),
        ]
        events._record_local_user_counter_event(
            valid_boundary,
            target,
            counter(3, now + timedelta(seconds=1)),
            "iran",
        )
        self.assertEqual(valid_boundary.execute.call_count, 2)

        first_valid = _FakeConnection()
        first_valid.execute.side_effect = [
            SimpleNamespace(first=lambda: None),
            SimpleNamespace(),
        ]
        events._record_local_user_counter_event(
            first_valid,
            target,
            counter(2, now + timedelta(seconds=1)),
            "iran",
        )
        self.assertEqual(first_valid.execute.call_count, 2)

    def test_registration_listener_payload_references_versions_and_v2_errors(self):
        registry = {}
        with patch(
            "core.events.event.listens_for",
            side_effect=_capture_listeners(registry),
        ):
            events.setup_accountant_relation_events()
            events.setup_customer_relation_events()
            events.setup_telegram_link_token_events()
            events.setup_invitation_events()

        now = datetime(2026, 7, 12, 12, 0)
        targets = self._build_listener_targets(now)
        link_token = SimpleNamespace(
            id=7,
            user_id=8,
            token_hash="hash",
            status=SimpleNamespace(value="pending"),
            issued_by_server="iran",
            expires_at=now,
            used_at=None,
            used_telegram_id=None,
            revoked_at=None,
            created_at=now,
            updated_at=now,
            sync_version=1,
        )
        target_by_model = {
            "AccountantRelation": targets[("AccountantRelation", "after_insert")],
            "CustomerRelation": targets[("CustomerRelation", "after_insert")],
            "TelegramLinkToken": link_token,
            "Invitation": targets[("Invitation", "after_insert")],
        }
        connection = _FakeConnection()
        references = {"owner_user_id": {"current": {"account_name": "owner"}, "previous": {}}}
        with patch(
            "core.events.settings.registration_sync_v2_enabled", True
        ), patch("core.events.settings.server_mode", "iran"), patch(
            "core.events._registration_user_references", return_value=references
        ), patch("core.events._changed_column_fields", return_value={"status"}), patch(
            "core.events.log_change"
        ) as log_change:
            for model, target in target_by_model.items():
                registry[(model, "before_update")](None, connection, target) if (model, "before_update") in registry else None
                registry[(model, "after_insert")](None, connection, target)

        self.assertEqual(log_change.call_count, 4)
        for call in log_change.call_args_list:
            self.assertIn(events.REGISTRATION_USER_REFERENCES_FIELD, call.args[4])
        self.assertEqual(target_by_model["AccountantRelation"].sync_version, 2)
        self.assertEqual(target_by_model["CustomerRelation"].sync_version, 2)
        self.assertEqual(target_by_model["Invitation"].sync_version, 2)

        with patch(
            "core.events.settings.registration_sync_v2_enabled", False
        ), patch("core.events._changed_column_fields", return_value={"status"}):
            registry[("AccountantRelation", "before_update")](
                None, connection, target_by_model["AccountantRelation"]
            )
            registry[("CustomerRelation", "before_update")](
                None, connection, target_by_model["CustomerRelation"]
            )
            registry[("Invitation", "before_update")](
                None, connection, target_by_model["Invitation"]
            )

        with patch(
            "core.events.settings.registration_sync_v2_enabled", True
        ), patch("core.events.log_change", side_effect=RuntimeError("outbox failed")):
            for model, target in target_by_model.items():
                for event_name in ("after_insert", "after_update"):
                    with self.subTest(model=model, event=event_name), self.assertRaisesRegex(
                        RuntimeError, "outbox failed"
                    ):
                        registry[(model, event_name)](None, connection, target)

        with patch(
            "core.events.settings.registration_sync_v2_enabled", False
        ), patch("core.events.log_change", side_effect=RuntimeError("legacy failure")):
            registry[("TelegramLinkToken", "after_insert")](
                None, connection, link_token
            )
            registry[("TelegramLinkToken", "after_update")](
                None, connection, link_token
            )

    def test_user_listener_authority_and_error_boundaries(self):
        registry = {}
        with patch(
            "core.events.event.listens_for", side_effect=_capture_listeners(registry)
        ):
            events.setup_user_events()
        user = self._build_listener_targets(datetime(2026, 7, 12, 12, 0))[
            ("User", "after_insert")
        ]
        user.sync_version = getattr(user, "sync_version", 1)
        connection = _FakeConnection()

        with patch("core.events.settings.registration_sync_v2_enabled", False), patch(
            "core.events._changed_column_fields", return_value={"address"}
        ):
            registry[("User", "before_update")](None, connection, user)

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode", "foreign"
        ), patch("core.events._changed_column_fields", return_value={"updated_at"}):
            registry[("User", "before_update")](None, connection, user)

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode", "other"
        ), patch("core.events._changed_column_fields", return_value={"address"}):
            registry[("User", "before_update")](None, connection, user)

        old_version = user.sync_version
        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode", "iran"
        ), patch("core.events._changed_column_fields", return_value={"address"}):
            registry[("User", "before_update")](None, connection, user)
        self.assertEqual(user.sync_version, old_version + 1)
        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode", "iran"
        ), patch(
            "core.events._changed_column_fields",
            return_value={"sync_version", "updated_at"},
        ):
            registry[("User", "before_update")](None, connection, user)

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode", "foreign"
        ), patch("core.events.log_change") as log_change:
            registry[("User", "after_insert")](None, connection, user)
        log_change.assert_not_called()

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode", "iran"
        ), patch("core.events._changed_column_fields", return_value={"future_field"}), patch(
            "core.events.build_user_counter_event", return_value=None
        ), patch.object(events.logger, "warning") as warning, patch(
            "core.events.log_change"
        ):
            registry[("User", "after_update")](None, connection, user)
        warning.assert_called_once()

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode", "iran"
        ), patch("core.events._changed_column_fields", return_value={"trades_count"}), patch(
            "core.events.build_user_counter_event",
            side_effect=events.InvalidUserCounterMutation("invalid counter mutation"),
        ), self.assertRaises(events.InvalidUserCounterMutation):
            registry[("User", "after_update")](None, connection, user)


if __name__ == '__main__':
    unittest.main()
