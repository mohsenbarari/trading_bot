from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import contextlib
import inspect
import unittest
import unittest.mock
from typing import Any, Iterator

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.selectable import Select
from schemas import CommodityCreate
from models.commodity import Commodity, CommodityAlias
from api.routers.commodities import create_commodity
import api.routers.commodities as commodities_router
import bot.utils.redis_helpers as redis_helpers

_COMMODITY_BY_NAME_SQL = (
    "SELECT commodities.id, commodities.name "
    "FROM commodities "
    "WHERE commodities.name = %(name_1)s"
)
_ALIAS_BY_TEXT_SQL = (
    "SELECT commodity_aliases.id, commodity_aliases.alias, commodity_aliases.commodity_id "
    "FROM commodity_aliases "
    "WHERE commodity_aliases.alias = %(alias_1)s"
)
_DIGIT_ERROR_DETAIL = "شما نمیتوانید در نام کالا از اعداد استفاده کنید"


class _ScalarResult:
    def __init__(self, value: Commodity | CommodityAlias | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Commodity | CommodityAlias | None:
        return self._value


class _RecordingLogger:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.info_messages: list[str] = []
        self.unexpected_calls: list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]] = []

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if args or kwargs:
            self.unexpected_calls.append(("info_args", msg, args, kwargs))
        self.info_messages.append(msg)
        self._events.append("log")

    def debug(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self.unexpected_calls.append(("debug", msg, args, kwargs))
        self._events.append("log:debug")

    def warning(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self.unexpected_calls.append(("warning", msg, args, kwargs))
        self._events.append("log:warning")

    def error(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self.unexpected_calls.append(("error", msg, args, kwargs))
        self._events.append("log:error")

    def exception(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self.unexpected_calls.append(("exception", msg, args, kwargs))
        self._events.append("log:exception")


class _QueryAwareFakeDB:
    def __init__(
        self,
        *,
        events: list[str],
        commodities_by_name: dict[str, Commodity | None],
        aliases_by_text: dict[str, CommodityAlias | None],
        commit_error: RuntimeError | None = None,
    ) -> None:
        self._events = events
        self._commodities_by_name = dict(commodities_by_name)
        self._aliases_by_text = dict(aliases_by_text)
        self._commit_error = commit_error
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.result_doubles: list[_ScalarResult] = []
        self.adds: list[Any] = []
        self.commits: int = 0
        self.commits_failed: list[RuntimeError] = []
        self.commit_failure_snapshots: list[tuple[tuple[str, ...], RuntimeError]] = []
        self.refreshes: list[tuple[Any, Any]] = []

    async def execute(self, stmt: Any) -> _ScalarResult:
        if not isinstance(stmt, Select):
            raise AssertionError(f"Expected SQLAlchemy Select, got {type(stmt)!r}")
        compiled = stmt.compile(dialect=postgresql.dialect())
        normalized_sql = " ".join(str(compiled).split())
        params = dict(compiled.params)

        if normalized_sql == _COMMODITY_BY_NAME_SQL:
            if set(params.keys()) != {"name_1"} or not isinstance(params["name_1"], str):
                raise AssertionError(f"Unexpected commodities.name params: {params!r}")
            requested_name = params["name_1"]
            if requested_name not in self._commodities_by_name:
                raise AssertionError(
                    f"Unconfigured commodities.name lookup: {requested_name!r}"
                )
            self.executed.append((normalized_sql, params))
            self._events.append("execute:commodities.name")
            result = _ScalarResult(self._commodities_by_name[requested_name])
            self.result_doubles.append(result)
            return result

        if normalized_sql == _ALIAS_BY_TEXT_SQL:
            if set(params.keys()) != {"alias_1"} or not isinstance(params["alias_1"], str):
                raise AssertionError(
                    f"Unexpected commodity_aliases.alias params: {params!r}"
                )
            requested_alias = params["alias_1"]
            if requested_alias not in self._aliases_by_text:
                raise AssertionError(
                    f"Unconfigured commodity_aliases.alias lookup: {requested_alias!r}"
                )
            self.executed.append((normalized_sql, params))
            self._events.append("execute:commodity_aliases.alias")
            result = _ScalarResult(self._aliases_by_text[requested_alias])
            self.result_doubles.append(result)
            return result

        raise AssertionError(
            f"Unexpected SQL shape: {normalized_sql!r} (params={params!r})"
        )

    def add(self, obj: Any) -> None:
        self.adds.append(obj)
        self._events.append("add")

    async def commit(self) -> None:
        self._events.append("commit")
        if self._commit_error is not None:
            err = self._commit_error
            self._commit_error = None
            self.commits_failed.append(err)
            self.commit_failure_snapshots.append((tuple(self._events), err))
            raise err
        self.commits += 1

    async def refresh(self, obj: Any, attrs: Any = None) -> None:
        self.refreshes.append((obj, attrs))
        if attrs == ["aliases"]:
            self._events.append("refresh:aliases")
        else:
            self._events.append(f"refresh:{attrs!r}")


class _ScenarioHarness:
    def __init__(
        self,
        *,
        events: list[str],
        db: _QueryAwareFakeDB,
        logger_double: _RecordingLogger,
        invalidate_mock: unittest.mock.AsyncMock,
        invalidate_failures: list[tuple[tuple[str, ...], RuntimeError]],
    ) -> None:
        self.events = events
        self.db = db
        self.logger_double = logger_double
        self.invalidate_mock = invalidate_mock
        self.invalidate_failures = invalidate_failures


@contextlib.contextmanager
def _scenario_harness(
    *,
    commodities_by_name: dict[str, Commodity | None],
    aliases_by_text: dict[str, CommodityAlias | None],
    commit_error: RuntimeError | None = None,
    invalidate_error: RuntimeError | None = None,
) -> Iterator[_ScenarioHarness]:
    events: list[str] = []
    db = _QueryAwareFakeDB(
        events=events,
        commodities_by_name=commodities_by_name,
        aliases_by_text=aliases_by_text,
        commit_error=commit_error,
    )
    logger_double = _RecordingLogger(events)
    invalidate_failures: list[tuple[tuple[str, ...], RuntimeError]] = []
    pending_invalidate_error: list[RuntimeError | None] = [invalidate_error]

    async def _invalidate_side_effect() -> None:
        events.append("invalidate")
        err = pending_invalidate_error[0]
        if err is not None:
            pending_invalidate_error[0] = None
            invalidate_failures.append((tuple(events), err))
            raise err

    invalidate_mock = unittest.mock.AsyncMock(side_effect=_invalidate_side_effect)

    with (
        unittest.mock.patch("api.routers.commodities.logger", new=logger_double),
        unittest.mock.patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache",
            new=invalidate_mock,
        ),
    ):
        yield _ScenarioHarness(
            events=events,
            db=db,
            logger_double=logger_double,
            invalidate_mock=invalidate_mock,
            invalidate_failures=invalidate_failures,
        )


class CommodityCreateMutationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_commodity_validation_conflict_persistence_and_cache_paths(
        self,
    ) -> None:
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        orig_create_commodity = commodities_router.create_commodity
        orig_no_digits = commodities_router.ensure_commodity_text_has_no_digits
        orig_is_available = commodities_router.ensure_commodity_text_is_available
        orig_digit_pattern = commodities_router.COMMODITY_DIGIT_PATTERN
        orig_get_request_source = commodities_router.get_request_source
        orig_select = commodities_router.select
        orig_logger = commodities_router.logger
        orig_invalidate = redis_helpers.invalidate_commodity_cache

        def _assert_unpatched_outside_harness() -> None:
            self.assertIs(create_commodity, orig_create_commodity)
            self.assertIs(commodities_router.create_commodity, orig_create_commodity)
            self.assertIs(
                commodities_router.ensure_commodity_text_has_no_digits,
                orig_no_digits,
            )
            self.assertIs(
                commodities_router.ensure_commodity_text_is_available,
                orig_is_available,
            )
            self.assertIs(commodities_router.COMMODITY_DIGIT_PATTERN, orig_digit_pattern)
            self.assertIs(commodities_router.get_request_source, orig_get_request_source)
            self.assertIs(commodities_router.select, orig_select)
            self.assertIs(commodities_router.Commodity, Commodity)
            self.assertIs(commodities_router.CommodityAlias, CommodityAlias)
            self.assertIs(commodities_router.logger, orig_logger)
            self.assertIs(redis_helpers.invalidate_commodity_cache, orig_invalidate)
            for prod_obj in (
                create_commodity,
                orig_no_digits,
                orig_is_available,
                Commodity,
                CommodityAlias,
                orig_logger,
                orig_invalidate,
            ):
                self.assertFalse(
                    isinstance(prod_obj, unittest.mock.NonCallableMock),
                    f"Unexpected mock bound outside harness: {prod_obj!r}",
                )
            self.assertTrue(inspect.iscoroutinefunction(create_commodity))
            self.assertTrue(inspect.iscoroutinefunction(orig_is_available))
            self.assertTrue(inspect.iscoroutinefunction(orig_invalidate))
            self.assertFalse(inspect.iscoroutinefunction(orig_no_digits))

        self.assertTrue(inspect.iscoroutinefunction(_QueryAwareFakeDB.execute))
        self.assertFalse(inspect.iscoroutinefunction(_QueryAwareFakeDB.add))
        self.assertTrue(inspect.iscoroutinefunction(_QueryAwareFakeDB.commit))
        self.assertTrue(inspect.iscoroutinefunction(_QueryAwareFakeDB.refresh))
        self.assertFalse(inspect.iscoroutinefunction(_ScalarResult.scalar_one_or_none))
        _assert_unpatched_outside_harness()

        # Scenario 1: ASCII digit in commodity name rejected before any DB or cache effect.
        with _scenario_harness(
            commodities_by_name={},
            aliases_by_text={},
        ) as h1:
            self.assertIsInstance(h1.invalidate_mock, unittest.mock.AsyncMock)
            with self.assertRaises(HTTPException) as exc1:
                await create_commodity(
                    commodity_data=CommodityCreate(name="نیم86"),
                    aliases=["ربع"],
                    db=h1.db,
                    source="bot",
                )
            self.assertEqual(exc1.exception.status_code, 400)
            self.assertEqual(exc1.exception.detail, _DIGIT_ERROR_DETAIL)
            self.assertEqual(h1.events, ["log"])
            self.assertEqual(
                h1.logger_double.info_messages,
                ["Creating commodity 'نیم86' via source: bot"],
            )
            self.assertEqual(h1.logger_double.unexpected_calls, [])
            self.assertEqual(h1.db.executed, [])
            self.assertEqual(h1.db.adds, [])
            self.assertEqual(h1.db.commits, 0)
            self.assertEqual(h1.db.commits_failed, [])
            self.assertEqual(h1.db.refreshes, [])
            self.assertEqual(h1.invalidate_mock.await_count, 0)
            self.assertEqual(h1.invalidate_mock.call_count, 0)
        _assert_unpatched_outside_harness()

        # Scenario 2: Arabic-Indic digits in alias rejected before any DB or cache effect.
        with _scenario_harness(
            commodities_by_name={},
            aliases_by_text={},
        ) as h2:
            with self.assertRaises(HTTPException) as exc2:
                await create_commodity(
                    commodity_data=CommodityCreate(name="نیم"),
                    aliases=["ربع٤٠٣"],
                    db=h2.db,
                    source="bot",
                )
            self.assertEqual(exc2.exception.status_code, 400)
            self.assertEqual(exc2.exception.detail, _DIGIT_ERROR_DETAIL)
            self.assertEqual(h2.events, ["log"])
            self.assertEqual(
                h2.logger_double.info_messages,
                ["Creating commodity 'نیم' via source: bot"],
            )
            self.assertEqual(h2.logger_double.unexpected_calls, [])
            self.assertEqual(h2.db.executed, [])
            self.assertEqual(h2.db.adds, [])
            self.assertEqual(h2.db.commits, 0)
            self.assertEqual(h2.db.commits_failed, [])
            self.assertEqual(h2.db.refreshes, [])
            self.assertEqual(h2.invalidate_mock.await_count, 0)
            self.assertEqual(h2.invalidate_mock.call_count, 0)
        _assert_unpatched_outside_harness()

        # Scenario 3: Canonical commodity name conflict short-circuits before alias query.
        existing_gold = Commodity(id=1, name="Gold")
        self.assertEqual(existing_gold.id, 1)
        self.assertEqual(existing_gold.name, "Gold")
        with _scenario_harness(
            commodities_by_name={"Gold": existing_gold},
            aliases_by_text={},
        ) as h3:
            with self.assertRaises(HTTPException) as exc3:
                await create_commodity(
                    commodity_data=CommodityCreate(name="Gold"),
                    aliases=[],
                    db=h3.db,
                    source="bot",
                )
            self.assertEqual(exc3.exception.status_code, 409)
            self.assertEqual(
                exc3.exception.detail,
                "نام «Gold» از قبل به عنوان نام اصلی یک کالا ثبت شده است",
            )
            self.assertEqual(h3.events, ["log", "execute:commodities.name"])
            self.assertEqual(
                h3.logger_double.info_messages,
                ["Creating commodity 'Gold' via source: bot"],
            )
            self.assertEqual(h3.logger_double.unexpected_calls, [])
            self.assertEqual(
                h3.db.executed,
                [(_COMMODITY_BY_NAME_SQL, {"name_1": "Gold"})],
            )
            self.assertEqual(len(h3.db.result_doubles), 1)
            self.assertFalse(
                isinstance(
                    h3.db.result_doubles[0].scalar_one_or_none,
                    unittest.mock.AsyncMock,
                )
            )
            self.assertEqual(h3.db.adds, [])
            self.assertEqual(h3.db.commits, 0)
            self.assertEqual(h3.db.commits_failed, [])
            self.assertEqual(h3.db.refreshes, [])
            self.assertEqual(h3.invalidate_mock.await_count, 0)
            self.assertEqual(h3.invalidate_mock.call_count, 0)
        _assert_unpatched_outside_harness()

        # Scenario 4: Existing alias-text conflict during alias availability check.
        existing_alias = CommodityAlias(id=9, alias="gold", commodity_id=1)
        self.assertEqual(existing_alias.id, 9)
        self.assertEqual(existing_alias.alias, "gold")
        self.assertEqual(existing_alias.commodity_id, 1)
        with _scenario_harness(
            commodities_by_name={"Gold": None, "gold": None},
            aliases_by_text={"Gold": None, "gold": existing_alias},
        ) as h4:
            with self.assertRaises(HTTPException) as exc4:
                await create_commodity(
                    commodity_data=CommodityCreate(name="Gold"),
                    aliases=["gold"],
                    db=h4.db,
                    source="bot",
                )
            self.assertEqual(exc4.exception.status_code, 409)
            self.assertEqual(
                exc4.exception.detail,
                "نام «gold» از قبل به عنوان نام مستعار یک کالا ثبت شده است",
            )
            self.assertEqual(
                h4.events,
                [
                    "log",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                ],
            )
            self.assertEqual(
                h4.logger_double.info_messages,
                ["Creating commodity 'Gold' via source: bot"],
            )
            self.assertEqual(h4.logger_double.unexpected_calls, [])
            self.assertEqual(
                h4.db.executed,
                [
                    (_COMMODITY_BY_NAME_SQL, {"name_1": "Gold"}),
                    (_ALIAS_BY_TEXT_SQL, {"alias_1": "Gold"}),
                    (_COMMODITY_BY_NAME_SQL, {"name_1": "gold"}),
                    (_ALIAS_BY_TEXT_SQL, {"alias_1": "gold"}),
                ],
            )
            self.assertEqual(h4.db.adds, [])
            self.assertEqual(h4.db.commits, 0)
            self.assertEqual(h4.db.commits_failed, [])
            self.assertEqual(h4.db.refreshes, [])
            self.assertEqual(h4.invalidate_mock.await_count, 0)
            self.assertEqual(h4.invalidate_mock.call_count, 0)
        _assert_unpatched_outside_harness()

        # Scenario 5: Success with duplicate alias deduplication, persistence, refresh, and cache invalidation.
        duplicate_aliases_input = ["gold", "gold"]
        with _scenario_harness(
            commodities_by_name={"Gold": None, "gold": None},
            aliases_by_text={"Gold": None, "gold": None},
        ) as h5:
            created5 = await create_commodity(
                commodity_data=CommodityCreate(name="Gold"),
                aliases=duplicate_aliases_input,
                db=h5.db,
                source="bot",
            )
            self.assertEqual(duplicate_aliases_input, ["gold", "gold"])
            self.assertEqual(len(h5.db.adds), 1)
            self.assertIsInstance(created5, Commodity)
            self.assertIs(created5, h5.db.adds[0])
            self.assertEqual(created5.name, "Gold")
            self.assertEqual(len(created5.aliases), 1)
            self.assertIsInstance(created5.aliases[0], CommodityAlias)
            self.assertEqual(created5.aliases[0].alias, "gold")
            self.assertIs(created5.aliases[0].commodity, created5)
            self.assertEqual(
                h5.logger_double.info_messages,
                ["Creating commodity 'Gold' via source: bot"],
            )
            self.assertEqual(h5.logger_double.unexpected_calls, [])
            self.assertEqual(
                h5.db.executed,
                [
                    (_COMMODITY_BY_NAME_SQL, {"name_1": "Gold"}),
                    (_ALIAS_BY_TEXT_SQL, {"alias_1": "Gold"}),
                    (_COMMODITY_BY_NAME_SQL, {"name_1": "gold"}),
                    (_ALIAS_BY_TEXT_SQL, {"alias_1": "gold"}),
                ],
            )
            self.assertEqual(
                h5.events,
                [
                    "log",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                    "add",
                    "commit",
                    "refresh:aliases",
                    "invalidate",
                ],
            )
            self.assertEqual(h5.db.commits, 1)
            self.assertEqual(h5.db.commits_failed, [])
            self.assertEqual(len(h5.db.refreshes), 1)
            self.assertIs(h5.db.refreshes[0][0], created5)
            self.assertEqual(h5.db.refreshes[0][1], ["aliases"])
            h5.invalidate_mock.assert_awaited_once_with()
            self.assertEqual(h5.invalidate_failures, [])
        _assert_unpatched_outside_harness()

        # Scenario 6: Cache invalidation failure is swallowed after commit and refresh.
        cache_sentinel = RuntimeError("create-cache-failure")
        with _scenario_harness(
            commodities_by_name={"Gold": None, "gold": None},
            aliases_by_text={"Gold": None, "gold": None},
            invalidate_error=cache_sentinel,
        ) as h6:
            created6 = await create_commodity(
                commodity_data=CommodityCreate(name="Gold"),
                aliases=["gold"],
                db=h6.db,
                source="bot",
            )
            self.assertEqual(len(h6.db.adds), 1)
            self.assertIsInstance(created6, Commodity)
            self.assertIs(created6, h6.db.adds[0])
            self.assertEqual(created6.name, "Gold")
            self.assertEqual([alias.alias for alias in created6.aliases], ["gold"])
            self.assertEqual(
                h6.logger_double.info_messages,
                ["Creating commodity 'Gold' via source: bot"],
            )
            self.assertEqual(h6.logger_double.unexpected_calls, [])
            expected_s6_events = [
                "log",
                "execute:commodities.name",
                "execute:commodity_aliases.alias",
                "execute:commodities.name",
                "execute:commodity_aliases.alias",
                "add",
                "commit",
                "refresh:aliases",
                "invalidate",
            ]
            self.assertEqual(h6.events, expected_s6_events)
            self.assertEqual(h6.db.commits, 1)
            self.assertEqual(h6.db.commits_failed, [])
            self.assertEqual(len(h6.db.refreshes), 1)
            self.assertIs(h6.db.refreshes[0][0], created6)
            self.assertEqual(h6.db.refreshes[0][1], ["aliases"])
            h6.invalidate_mock.assert_awaited_once_with()
            self.assertEqual(len(h6.invalidate_failures), 1)
            s6_snapshot, s6_raised = h6.invalidate_failures[0]
            self.assertIs(s6_raised, cache_sentinel)
            self.assertEqual(list(s6_snapshot), expected_s6_events)
        _assert_unpatched_outside_harness()

        # Scenario 7: Commit failure propagates identical sentinel; refresh and invalidate suppressed.
        commit_sentinel = RuntimeError("create-commit-failure")
        with _scenario_harness(
            commodities_by_name={"Gold": None, "gold": None},
            aliases_by_text={"Gold": None, "gold": None},
            commit_error=commit_sentinel,
        ) as h7:
            with self.assertRaises(RuntimeError) as exc7:
                await create_commodity(
                    commodity_data=CommodityCreate(name="Gold"),
                    aliases=["gold"],
                    db=h7.db,
                    source="bot",
                )
            self.assertIs(exc7.exception, commit_sentinel)
            self.assertEqual(len(h7.db.adds), 1)
            self.assertIsInstance(h7.db.adds[0], Commodity)
            self.assertEqual(h7.db.adds[0].name, "Gold")
            self.assertEqual(
                [alias.alias for alias in h7.db.adds[0].aliases],
                ["gold"],
            )
            self.assertEqual(
                h7.logger_double.info_messages,
                ["Creating commodity 'Gold' via source: bot"],
            )
            self.assertEqual(h7.logger_double.unexpected_calls, [])
            expected_s7_events = [
                "log",
                "execute:commodities.name",
                "execute:commodity_aliases.alias",
                "execute:commodities.name",
                "execute:commodity_aliases.alias",
                "add",
                "commit",
            ]
            self.assertEqual(h7.events, expected_s7_events)
            self.assertEqual(h7.db.commits, 0)
            self.assertEqual(len(h7.db.commits_failed), 1)
            self.assertIs(h7.db.commits_failed[0], commit_sentinel)
            self.assertEqual(len(h7.db.commit_failure_snapshots), 1)
            s7_snapshot, s7_raised = h7.db.commit_failure_snapshots[0]
            self.assertIs(s7_raised, commit_sentinel)
            self.assertEqual(list(s7_snapshot), expected_s7_events)
            self.assertEqual(h7.db.refreshes, [])
            self.assertEqual(h7.invalidate_mock.await_count, 0)
            self.assertEqual(h7.invalidate_mock.call_count, 0)
        _assert_unpatched_outside_harness()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
