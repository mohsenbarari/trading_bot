import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.sync import receive_sync_data


class FakeOfferExecuteResult:
    def __init__(self, offer):
        self._offer = offer

    def scalars(self):
        return SimpleNamespace(first=lambda: self._offer)


class FakeDB:
    def __init__(self, offer_results=None):
        self.offer_results = list(offer_results or [])
        self.commits = 0

    async def execute(self, stmt, *args, **kwargs):
        text = str(stmt)
        if "setval(" in text:
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


class SyncRouterReceiveOfferPublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_sync_data_publishes_new_foreign_offer(self):
        offer = make_offer()
        db = FakeDB([FakeOfferExecuteResult(offer)])
        items = [{"table": "offers", "operation": "INSERT", "id": 7, "data": {"price": 11}}]

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None):
            new_offers.append(record_id)
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch("api.routers.offers.send_offer_to_channel", new=AsyncMock(return_value=555)) as send_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        send_mock.assert_awaited_once_with(offer, offer.user)
        self.assertEqual(offer.channel_message_id, 555)
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_skips_already_published_or_none_message_id(self):
        items = [{"table": "offers", "operation": "INSERT", "id": 7, "data": {"price": 11}}]

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None):
            new_offers.append(record_id)
            return "ok"

        db = FakeDB([FakeOfferExecuteResult(None)])
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch("api.routers.offers.send_offer_to_channel", new=AsyncMock()) as send_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        send_mock.assert_not_awaited()
        self.assertEqual(result, {"status": "success", "processed": 1})

        offer = make_offer()
        db = FakeDB([FakeOfferExecuteResult(offer)])
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch("api.routers.offers.send_offer_to_channel", new=AsyncMock(return_value=None)) as send_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        send_mock.assert_awaited_once_with(offer, offer.user)
        self.assertIsNone(offer.channel_message_id)
        self.assertEqual(result, {"status": "success", "processed": 1})


if __name__ == "__main__":
    unittest.main()
