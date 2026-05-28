import unittest
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from api.routers.trading_settings import (
    MarketScheduleOverrideUpsert,
    _normalize_closed_weekdays,
    _parse_local_time,
    _prepare_override_payload,
    create_market_override,
    delete_market_override,
    list_market_overrides,
    update_market_override,
)
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType


class FakeScalarResult:
    def __init__(self, first_value=None, all_values=None):
        self._first_value = first_value
        self._all_values = list(all_values or ([] if first_value is None else [first_value]))

    def scalars(self):
        return self

    def first(self):
        return self._first_value

    def all(self):
        return list(self._all_values)


class FakeDB:
    def __init__(self, *, execute_results=None, get_results=None):
        self.execute_results = list(execute_results or [])
        self.get_results = list(get_results or [])
        self.added = []
        self.deleted = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock(side_effect=self._refresh)

    async def execute(self, _statement):
        return self.execute_results.pop(0)

    async def get(self, _model, _primary_key):
        return self.get_results.pop(0)

    def add(self, instance):
        self.added.append(instance)

    async def delete(self, instance):
        self.deleted.append(instance)

    async def _refresh(self, instance):
        if getattr(instance, "id", None) is None:
            instance.id = len(self.added) + 100


def make_override(**overrides):
    data = {
        "id": 1,
        "date": date(2026, 5, 23),
        "override_type": MarketScheduleOverrideType.CLOSED_ALL_DAY,
        "open_time_local": None,
        "close_time_local": None,
        "note": "تعطیلی",
        "created_by_user_id": 7,
    }
    data.update(overrides)
    return MarketScheduleOverride(**data)


class TradingSettingsRouterOverrideTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_create_update_and_delete_market_overrides(self):
        override_one = make_override(id=1, date=date(2026, 5, 23), note="تعطیلی کامل")
        override_two = make_override(
            id=2,
            date=date(2026, 5, 24),
            override_type=MarketScheduleOverrideType.CUSTOM_HOURS,
            open_time_local=time(10, 0),
            close_time_local=time(13, 30),
            note="نیمه‌وقت",
        )

        db = FakeDB(execute_results=[FakeScalarResult(all_values=[override_one, override_two])])
        listed = await list_market_overrides(db=db)
        self.assertEqual([item.id for item in listed], [1, 2])
        self.assertEqual(listed[1].open_time_local, "10:00")

        create_db = FakeDB(execute_results=[FakeScalarResult(first_value=None)])
        created = await create_market_override(
            MarketScheduleOverrideUpsert(
                date="2026-05-25",
                override_type=MarketScheduleOverrideType.CUSTOM_HOURS,
                open_time_local="11:00",
                close_time_local="14:00",
                note="شیفت کوتاه",
            ),
            db=create_db,
            current_user=SimpleNamespace(id=55),
        )
        self.assertEqual(created.date, "2026-05-25")
        self.assertEqual(created.open_time_local, "11:00")
        self.assertEqual(create_db.added[0].created_by_user_id, 55)
        create_db.commit.assert_awaited_once()
        create_db.refresh.assert_awaited_once()

        existing = make_override(id=9, note="قدیمی")
        update_db = FakeDB(
            execute_results=[FakeScalarResult(first_value=None)],
            get_results=[existing],
        )
        updated = await update_market_override(
            9,
            MarketScheduleOverrideUpsert(
                date="2026-05-26",
                override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
                note="باز ویژه",
            ),
            db=update_db,
            _=SimpleNamespace(id=1),
        )
        self.assertEqual(updated.date, "2026-05-26")
        self.assertEqual(updated.override_type, MarketScheduleOverrideType.OPEN_ALL_DAY)
        self.assertIsNone(updated.open_time_local)

        delete_db = FakeDB(get_results=[existing])
        deleted = await delete_market_override(9, db=delete_db, _=SimpleNamespace(id=1))
        self.assertEqual(deleted, {"success": True})
        self.assertEqual(delete_db.deleted, [existing])
        delete_db.commit.assert_awaited_once()

    async def test_market_override_validation_and_missing_rows(self):
        duplicate_db = FakeDB(execute_results=[FakeScalarResult(first_value=make_override())])
        with self.assertRaises(HTTPException) as exc_info:
            await create_market_override(
                MarketScheduleOverrideUpsert(
                    date="2026-05-23",
                    override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                ),
                db=duplicate_db,
                current_user=SimpleNamespace(id=1),
            )
        self.assertEqual(exc_info.exception.status_code, 400)

        with self.assertRaises(HTTPException) as exc_info:
            await create_market_override(
                MarketScheduleOverrideUpsert(
                    date="2026-05-27",
                    override_type=MarketScheduleOverrideType.CUSTOM_HOURS,
                    open_time_local="15:00",
                ),
                db=FakeDB(execute_results=[FakeScalarResult(first_value=None)]),
                current_user=SimpleNamespace(id=1),
            )
        self.assertEqual(exc_info.exception.status_code, 400)

        with self.assertRaises(HTTPException) as exc_info:
            await update_market_override(
                999,
                MarketScheduleOverrideUpsert(
                    date="2026-05-27",
                    override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                ),
                db=FakeDB(get_results=[None]),
                _=SimpleNamespace(id=1),
            )
        self.assertEqual(exc_info.exception.status_code, 404)

        with self.assertRaises(HTTPException) as exc_info:
            await delete_market_override(999, db=FakeDB(get_results=[None]), _=SimpleNamespace(id=1))
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_override_helper_validation_branches_and_duplicate_update(self):
        with self.assertRaises(HTTPException) as exc_info:
            _parse_local_time("nope", "ساعت شروع بازار")
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("ساعت شروع بازار نامعتبر است", exc_info.exception.detail)

        with self.assertRaises(HTTPException) as exc_info:
            _normalize_closed_weekdays(["x"])
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "روزهای بسته بازار نامعتبر هستند")

        with self.assertRaises(HTTPException) as exc_info:
            _normalize_closed_weekdays([7])
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "روزهای بسته بازار باید بین 0 تا 6 باشند")

        with self.assertRaises(HTTPException) as exc_info:
            _prepare_override_payload(
                MarketScheduleOverrideUpsert(
                    date="bad-date",
                    override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                )
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "تاریخ استثنا نامعتبر است")

        with self.assertRaises(HTTPException) as exc_info:
            _prepare_override_payload(
                MarketScheduleOverrideUpsert(
                    date="2026-05-28",
                    override_type=MarketScheduleOverrideType.CUSTOM_HOURS,
                    open_time_local="14:00",
                    close_time_local="13:00",
                )
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "ساعت شروع استثنا باید قبل از ساعت پایان باشد")

        existing = make_override(id=9, date=date(2026, 5, 28))
        duplicate_db = FakeDB(
            execute_results=[FakeScalarResult(first_value=make_override(id=10, date=date(2026, 5, 29)))],
            get_results=[existing],
        )
        with self.assertRaises(HTTPException) as exc_info:
            await update_market_override(
                9,
                MarketScheduleOverrideUpsert(
                    date="2026-05-29",
                    override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
                ),
                db=duplicate_db,
                _=SimpleNamespace(id=1),
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "برای این تاریخ قبلاً استثنا ثبت شده است")


if __name__ == "__main__":
    unittest.main()