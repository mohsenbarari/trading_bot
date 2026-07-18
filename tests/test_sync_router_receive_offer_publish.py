import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from api.routers.sync import _publish_synced_offer_created_realtime_after_sync, receive_sync_data
from core.enums import SettlementType


class FakeOfferExecuteResult:
    def __init__(self, offer):
        self._offer = offer

    def scalars(self):
        offers = [] if self._offer is None else [self._offer]
        return SimpleNamespace(first=lambda: self._offer, all=lambda: list(offers))


class FakeDB:
    def __init__(self, offer_results=None):
        self.offer_results = list(offer_results or [])
        self.commits = 0

    async def execute(self, stmt, *args, **kwargs):
        text = str(stmt)
        if "setval(" in text or text.strip().upper().startswith("ALTER SEQUENCE"):
            return SimpleNamespace()
        if self.offer_results:
            return self.offer_results.pop(0)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        raise AssertionError("rollback should not be called")

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


class FakeSelect:
    def options(self, *args, **kwargs):
        return self

    def where(self, *args, **kwargs):
        return self

    def with_for_update(self, **kwargs):
        return self

    def __str__(self):
        return "SELECT offer"


def make_offer():
    return SimpleNamespace(id=7, channel_message_id=None, user=SimpleNamespace(id=1), commodity=SimpleNamespace(id=2))


def make_terminal_offer():
    return SimpleNamespace(
        id=8,
        status="completed",
        remaining_quantity=0,
        lot_sizes=None,
        channel_message_id=777,
        user=SimpleNamespace(id=1),
        commodity=SimpleNamespace(id=2),
    )


class SyncRouterReceiveOfferPublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_synced_offer_realtime_payload_preserves_tomorrow_settlement(self):
        offer = SimpleNamespace(
            id=7,
            offer_public_id="ofr_sync_7",
            status="active",
            offer_type="sell",
            settlement_type=SettlementType.TOMORROW,
            commodity_id=2,
            commodity=SimpleNamespace(name="ربع بهار"),
            quantity=40,
            remaining_quantity=40,
            price=178000,
            created_at=None,
            notes=None,
            is_wholesale=True,
            lot_sizes=None,
            original_lot_sizes=None,
        )
        db = FakeDB([FakeOfferExecuteResult(offer)])

        with patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "core.trading_settings.get_trading_settings_async", new=AsyncMock(return_value=None)
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock:
            await _publish_synced_offer_created_realtime_after_sync(db, [7, 7])

        publish_mock.assert_awaited_once()
        event_name, payload = publish_mock.await_args.args
        self.assertEqual(event_name, "offer:created")
        self.assertEqual(payload["settlement_type"], "tomorrow")

    async def test_synced_offer_realtime_payload_preserves_zero_remaining_quantity(self):
        offer = SimpleNamespace(
            id=9,
            offer_public_id="ofr_sync_zero",
            status="active",
            offer_type="sell",
            settlement_type=SettlementType.CASH,
            commodity_id=2,
            commodity=SimpleNamespace(name="ربع بهار"),
            quantity=40,
            remaining_quantity=0,
            price=178000,
            created_at=None,
            notes=None,
            is_wholesale=True,
            lot_sizes=None,
            original_lot_sizes=None,
        )
        db = FakeDB([FakeOfferExecuteResult(offer)])

        with patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "core.trading_settings.get_trading_settings_async", new=AsyncMock(return_value=None)
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock:
            await _publish_synced_offer_created_realtime_after_sync(db, [9])

        publish_mock.assert_awaited_once()
        self.assertEqual(publish_mock.await_args.args[1]["remaining_quantity"], 0)

    async def test_receive_sync_data_publishes_new_foreign_offer(self):
        offer = make_offer()
        db = FakeDB([FakeOfferExecuteResult(offer)])
        items = [{"table": "offers", "operation": "INSERT", "id": 7, "data": {"price": 11}}]

        async def fake_apply_item(
            db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None, **_kwargs
        ):
            new_offers.append(record_id)
            return "ok"

        async def fake_publish(db_arg, offer_arg, user_arg, **_kwargs):
            offer_arg.channel_message_id = 555
            return SimpleNamespace(message_id=555, status="sent", error_code=None)

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "core.services.telegram_offer_publication_service.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ) as publish_mock, patch("api.routers.sync.active_publication_is_gated", new=AsyncMock(return_value=False)):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        publish_mock.assert_awaited_once_with(
            db,
            offer,
            offer.user,
            send_offer_to_channel=ANY,
        )
        self.assertEqual(offer.channel_message_id, 555)
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_skips_already_published_or_none_message_id(self):
        items = [{"table": "offers", "operation": "INSERT", "id": 7, "data": {"price": 11}}]

        async def fake_apply_item(
            db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None, **_kwargs
        ):
            new_offers.append(record_id)
            return "ok"

        db = FakeDB([FakeOfferExecuteResult(None)])
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "core.services.telegram_offer_publication_service.publish_offer_to_telegram_channel_once",
            new=AsyncMock(),
        ) as publish_mock, patch("api.routers.sync.active_publication_is_gated", new=AsyncMock(return_value=False)):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        publish_mock.assert_not_awaited()
        self.assertEqual(result, {"status": "success", "processed": 1})

        offer = make_offer()
        db = FakeDB([FakeOfferExecuteResult(offer)])
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "core.services.telegram_offer_publication_service.publish_offer_to_telegram_channel_once",
            new=AsyncMock(return_value=SimpleNamespace(message_id=None, status="failed", error_code="telegram_send_empty_result")),
        ) as publish_mock, patch("api.routers.sync.active_publication_is_gated", new=AsyncMock(return_value=False)):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        publish_mock.assert_awaited_once_with(
            db,
            offer,
            offer.user,
            send_offer_to_channel=ANY,
        )
        self.assertIsNone(offer.channel_message_id)
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_skips_foreign_active_publish_when_recovery_gate_enabled(self):
        offer = make_offer()
        db = FakeDB([FakeOfferExecuteResult(offer)])
        items = [{"table": "offers", "operation": "INSERT", "id": 7, "data": {"price": 11}}]

        async def fake_apply_item(
            db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None, **_kwargs
        ):
            new_offers.append(record_id)
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch(
            "core.services.telegram_offer_publication_service.publish_offer_to_telegram_channel_once",
            new=AsyncMock(),
        ) as publish_mock, patch("api.routers.sync.active_publication_is_gated", new=AsyncMock(return_value=True)):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        publish_mock.assert_not_awaited()
        self.assertIsNone(offer.channel_message_id)
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_skips_iran_realtime_created_publish_when_recovery_gate_enabled(self):
        db = FakeDB()
        items = [{"table": "offers", "operation": "INSERT", "id": 7, "data": {"price": 11}}]

        async def fake_apply_item(
            db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None, **_kwargs
        ):
            new_offers.append(record_id)
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch(
            "api.routers.sync._publish_synced_offer_created_realtime_after_sync",
            new=AsyncMock(),
        ) as realtime_publish_mock, patch("api.routers.sync.active_publication_is_gated", new=AsyncMock(return_value=True)):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        realtime_publish_mock.assert_not_awaited()
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_replays_terminal_foreign_offer_once(self):
        terminal_offer = make_terminal_offer()
        db = FakeDB([FakeOfferExecuteResult(terminal_offer), FakeOfferExecuteResult(terminal_offer)])
        items = [
            {"table": "offers", "operation": "UPDATE", "id": 8, "data": {"status": "completed"}},
            {"table": "offers", "operation": "UPDATE", "id": 8, "data": {"status": "completed"}},
        ]

        async def fake_apply_item(
            db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None, **_kwargs
        ):
            terminal_offers.append(record_id)
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "core.services.telegram_offer_publication_service.load_telegram_publication_state_for_update",
            new=AsyncMock(return_value=None),
        ) as load_publication_state_mock, patch(
            "core.services.telegram_offer_channel_service.apply_offer_channel_state", new=AsyncMock(return_value=True)
        ) as apply_state_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 2})
        publish_mock.assert_not_awaited()
        load_publication_state_mock.assert_awaited_once_with(db, terminal_offer)
        apply_state_mock.assert_awaited_once_with(
            terminal_offer,
            publication_state=None,
            reason="sync_terminal_offer",
        )


if __name__ == "__main__":
    unittest.main()
