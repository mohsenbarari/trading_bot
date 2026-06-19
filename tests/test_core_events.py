from datetime import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from core import events


class _FakeConnection:
    def __init__(self, is_sync=False):
        self._is_sync = is_sync
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
        notification = SimpleNamespace(id=5, user_id=1, message='hi', is_read=False, created_at=None, level='INFO', category='SYSTEM')
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
            ('Trade', 'after_insert'): trade,
            ('Trade', 'after_update'): trade,
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
            ('AdminMarketMessage', 'after_insert'): admin_market_message,
            ('AdminMarketMessage', 'after_update'): admin_market_message,
            ('AdminMarketMessage', 'after_delete'): admin_market_message,
            ('AdminBroadcastMessage', 'after_insert'): admin_broadcast_message,
            ('AdminBroadcastMessage', 'after_update'): admin_broadcast_message,
            ('AdminBroadcastMessage', 'after_delete'): admin_broadcast_message,
        }

    def test_get_sync_redis_reuses_connection_and_log_change_paths(self):
        created_clients = []

        def build_client(**kwargs):
            client = _FakeSyncRedis()
            created_clients.append((kwargs, client))
            return client

        with patch('redis.Redis', side_effect=build_client), patch('core.config.settings', SimpleNamespace(redis_host='host', redis_port=6379)):
            first = events._get_sync_redis()
            second = events._get_sync_redis()

        self.assertIs(first, second)
        self.assertEqual(len(created_clients), 1)
        self.assertEqual(created_clients[0][0]['host'], 'host')

        connection = _FakeConnection()
        sync_redis = _FakeSyncRedis()
        with patch('core.events._get_sync_redis', return_value=sync_redis), patch(
            'core.sync_push.push_sync_direct'
        ) as push_sync_direct:
            connection.execute.return_value = _FakeInsertResult(42)
            events.log_change(connection, 'offers', 5, 'INSERT', {'id': 5})

        connection.execute.assert_called_once()
        self.assertEqual(sync_redis.lpush_calls[0][0], 'sync:outbound')
        queued_payload = json.loads(sync_redis.lpush_calls[0][1])
        self.assertEqual(queued_payload["change_log_id"], 42)
        self.assertEqual(queued_payload["table"], "offers")
        self.assertEqual(queued_payload["id"], 5)
        push_sync_direct.assert_called_once()
        self.assertEqual(push_sync_direct.call_args.args[0]["change_log_id"], 42)

        sync_redis = _FakeSyncRedis(lpush_error=RuntimeError('redis down'))
        with patch('core.events._get_sync_redis', return_value=sync_redis), patch(
            'core.sync_push.push_sync_direct', side_effect=RuntimeError('push down')
        ), patch.object(events, 'logger') as logger:
            events._sync_redis = object()
            events.log_change(connection, 'offers', 6, 'UPDATE', {'id': 6})
        self.assertIsNone(events._sync_redis)
        self.assertGreaterEqual(logger.error.call_count, 1)
        logger.warning.assert_called_once()

        with patch.object(connection, 'execute', side_effect=RuntimeError('sql down')), patch.object(events, 'logger') as logger:
            events.log_change(connection, 'offers', 7, 'DELETE', {'id': 7})
        logger.error.assert_called_once()

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
        offer_payloads = [call.args[4] for call in log_change.call_args_list if call.args[1] == 'offers' and call.args[3] != 'DELETE']
        self.assertTrue(offer_payloads)
        for payload in offer_payloads:
            self.assertEqual(payload['offer_public_id'], 'ofr_event_1')
            self.assertEqual(payload['expired_by_user_id'], 9)
            self.assertEqual(payload['expired_by_actor_user_id'], 7)
            self.assertEqual(payload['expire_source_surface'], 'webapp')
            self.assertEqual(payload['expire_source_server'], 'iran')
        publish_event_sync.assert_any_call('offer:created', unittest.mock.ANY)
        publish_event_sync.assert_any_call('offer:updated', unittest.mock.ANY)
        publish_event_sync.assert_any_call('offer:expired', {'id': 1})
        publish_event_sync.assert_any_call('offer:deleted', {'id': 1})
        logger.info.assert_any_call('✅ Offer event listeners registered')
        logger.info.assert_any_call('✅ Trade event listeners registered')
        logger.info.assert_any_call('✅ User event listeners registered')

        sync_connection = _FakeConnection(is_sync=True)
        with patch('core.events.log_change') as log_change, patch('core.events.publish_event_sync') as publish_event_sync:
            registry[('Offer', 'after_insert')](None, sync_connection, offer)
        log_change.assert_not_called()
        publish_event_sync.assert_not_called()

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

        sync_connection = _FakeConnection(is_sync=True)
        registry[('OfferRequest', 'after_insert')](None, sync_connection, target)
        self.assertEqual(log_change.call_count, 2)
        logger.info.assert_any_call('✅ OfferRequest event listeners registered')

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
            events.setup_notification_events()
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

            notification = SimpleNamespace(id=5, user_id=1, message='hi', is_read=False, created_at=None, level='INFO', category='SYSTEM')
            registry[('Notification', 'after_insert')](None, connection, notification)
            registry[('Notification', 'after_update')](None, connection, notification)
            registry[('Notification', 'after_delete')](None, connection, notification)

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

        self.assertGreaterEqual(log_change.call_count, 28)
        logger.info.assert_any_call('✅ AccountantRelation event listeners registered')
        logger.info.assert_any_call('✅ CustomerRelation event listeners registered')
        logger.info.assert_any_call('✅ Chat event listeners registered')
        logger.info.assert_any_call('✅ ChatMember event listeners registered')
        logger.info.assert_any_call('✅ Commodity event listeners registered')
        logger.info.assert_any_call('✅ CommodityAlias event listeners registered')
        logger.info.assert_any_call('✅ UserBlock event listeners registered')
        logger.info.assert_any_call('✅ TradingSetting event listeners registered')
        logger.info.assert_any_call('✅ Invitation event listeners registered')
        logger.info.assert_any_call('✅ Notification event listeners registered')
        logger.info.assert_any_call('✅ AdminMessage event listeners registered')

        with patch('core.events.setup_user_events') as setup_user_events, patch(
            'core.events.setup_accountant_relation_events'
        ) as setup_accountant_relation_events, patch(
            'core.events.setup_customer_relation_events'
        ) as setup_customer_relation_events, patch(
            'core.events.setup_chat_events'
        ) as setup_chat_events, patch(
            'core.events.setup_chat_member_events'
        ) as setup_chat_member_events, patch(
            'core.events.setup_invitation_events'
        ) as setup_invitation_events, patch('core.events.setup_offer_events') as setup_offer_events, patch(
            'core.events.setup_offer_request_events'
        ) as setup_offer_request_events, patch(
            'core.events.setup_trade_events'
        ) as setup_trade_events, patch('core.events.setup_commodity_events') as setup_commodity_events, patch(
            'core.events.setup_commodity_alias_events'
        ) as setup_commodity_alias_events, patch('core.events.setup_trading_settings_events') as setup_trading_settings_events, patch(
            'core.events.setup_user_block_events'
        ) as setup_user_block_events, patch('core.events.setup_notification_events') as setup_notification_events, patch(
            'core.events.setup_admin_message_events'
        ) as setup_admin_message_events, patch.object(
            events, 'logger'
        ) as logger:
            events.setup_all_events()

        setup_user_events.assert_called_once()
        setup_accountant_relation_events.assert_called_once()
        setup_customer_relation_events.assert_called_once()
        setup_chat_events.assert_called_once()
        setup_chat_member_events.assert_called_once()
        setup_invitation_events.assert_called_once()
        setup_offer_events.assert_called_once()
        setup_offer_request_events.assert_called_once()
        setup_trade_events.assert_called_once()
        setup_commodity_events.assert_called_once()
        setup_commodity_alias_events.assert_called_once()
        setup_trading_settings_events.assert_called_once()
        setup_user_block_events.assert_called_once()
        setup_notification_events.assert_called_once()
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
            events.setup_notification_events()
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


if __name__ == '__main__':
    unittest.main()
