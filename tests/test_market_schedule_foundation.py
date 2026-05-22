import unittest
from datetime import date, datetime, time

from api.routers import sync
from models import MarketRuntimeState, MarketScheduleOverride, MarketScheduleOverrideType


class MarketScheduleFoundationTests(unittest.TestCase):
    def test_sync_model_mapping_and_order_include_market_tables(self):
        self.assertIs(sync.get_model_class("market_schedule_overrides"), MarketScheduleOverride)
        self.assertIs(sync.get_model_class("market_runtime_state"), MarketRuntimeState)
        self.assertIn("market_schedule_overrides", sync.TABLE_ORDER)
        self.assertIn("market_runtime_state", sync.TABLE_ORDER)
        self.assertLess(sync.TABLE_ORDER["market_schedule_overrides"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["market_runtime_state"], sync.TABLE_ORDER["offers"])

    def test_parse_item_coerces_market_override_date_and_times(self):
        item = {
            "table": "market_schedule_overrides",
            "operation": "INSERT",
            "id": 7,
            "data": {
                "id": 7,
                "date": "2026-05-22",
                "override_type": MarketScheduleOverrideType.CUSTOM_HOURS.value,
                "open_time_local": "09:00:00",
                "close_time_local": "13:30:00",
                "created_at": "2026-05-22T08:15:00+00:00",
            },
        }

        table, operation, model, data, record_id = sync._parse_item(item)

        self.assertEqual(table, "market_schedule_overrides")
        self.assertEqual(operation, "INSERT")
        self.assertIs(model, MarketScheduleOverride)
        self.assertEqual(record_id, 7)
        self.assertEqual(data["date"], date(2026, 5, 22))
        self.assertEqual(data["open_time_local"], time(9, 0))
        self.assertEqual(data["close_time_local"], time(13, 30))
        self.assertEqual(data["created_at"], datetime.fromisoformat("2026-05-22T08:15:00+00:00"))

    def test_market_models_expose_expected_table_contracts(self):
        self.assertEqual(MarketScheduleOverride.__tablename__, "market_schedule_overrides")
        self.assertEqual(MarketRuntimeState.__tablename__, "market_runtime_state")
        self.assertEqual(MarketScheduleOverrideType.CLOSED_ALL_DAY.value, "closed_all_day")
        self.assertEqual(str(MarketRuntimeState.__table__.c.offers_since_last_open.server_default.arg), "0")


if __name__ == "__main__":
    unittest.main()