from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import unittest
import types
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
import schemas
from schemas import CommodityAliasCreate
from models.commodity import Commodity, CommodityAlias
from api.routers.commodities import add_alias_to_commodity

COMMODITY_NAME_SQL = (
    "SELECT commodities.id, commodities.name "
    "FROM commodities "
    "WHERE commodities.name = %(name_1)s"
)
COMMODITY_ALIAS_SQL = (
    "SELECT commodity_aliases.id, commodity_aliases.alias, commodity_aliases.commodity_id "
    "FROM commodity_aliases "
    "WHERE commodity_aliases.alias = %(alias_1)s"
)


def _make_scalar_one_or_none_result(
    row: Commodity | CommodityAlias | None,
) -> types.SimpleNamespace:
    result_box = types.SimpleNamespace(calls=0)

    def scalar_one_or_none() -> Commodity | CommodityAlias | None:
        result_box.calls += 1
        return row

    result_box.scalar_one_or_none = scalar_one_or_none
    return result_box


def _make_fake_db(
    *,
    events: list[str],
    commodity_by_name: dict[str, Commodity | None],
    alias_by_text: dict[str, CommodityAlias | None],
    commit_error: Exception | None = None,
) -> types.SimpleNamespace:
    commodity_routes = dict(commodity_by_name)
    alias_routes = dict(alias_by_text)
    commit_raised = False
    db = types.SimpleNamespace(
        events=events,
        added=[],
        deleted=[],
        commits=0,
        refreshes=[],
        rollbacks=0,
        executed=[],
        results=[],
    )

    async def execute(stmt: object) -> types.SimpleNamespace:
        if getattr(stmt, "__visit_name__", None) != "select" or not hasattr(stmt, "compile"):
            raise AssertionError(f"Expected real SQLAlchemy Select statement, got {stmt!r}")
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = " ".join(str(compiled).split())
        params = dict(compiled.params)
        db.executed.append((sql, params))

        if sql == COMMODITY_NAME_SQL:
            if set(params.keys()) != {"name_1"}:
                raise AssertionError(f"Unexpected commodity-name params: {params!r}")
            requested_name = params["name_1"]
            if not isinstance(requested_name, str) or requested_name not in commodity_routes:
                raise AssertionError(f"Unconfigured commodity-name lookup: {requested_name!r}")
            db.events.append("execute:commodities.name")
            result = _make_scalar_one_or_none_result(commodity_routes[requested_name])
            db.results.append(result)
            return result

        if sql == COMMODITY_ALIAS_SQL:
            if set(params.keys()) != {"alias_1"}:
                raise AssertionError(f"Unexpected commodity-alias params: {params!r}")
            requested_alias = params["alias_1"]
            if not isinstance(requested_alias, str) or requested_alias not in alias_routes:
                raise AssertionError(f"Unconfigured commodity-alias lookup: {requested_alias!r}")
            db.events.append("execute:commodity_aliases.alias")
            result = _make_scalar_one_or_none_result(alias_routes[requested_alias])
            db.results.append(result)
            return result

        raise AssertionError(f"Unexpected SQL shape: {sql!r} with params {params!r}")

    def add(obj: object) -> None:
        db.added.append(obj)
        db.events.append("add")

    async def delete(obj: object) -> None:
        db.deleted.append(obj)
        db.events.append("delete")

    async def commit() -> None:
        nonlocal commit_raised
        db.commits += 1
        db.events.append("commit")
        if commit_error is not None and not commit_raised:
            commit_raised = True
            raise commit_error

    async def refresh(obj: object) -> None:
        db.refreshes.append(obj)
        db.events.append("refresh")

    async def rollback() -> None:
        db.rollbacks += 1
        db.events.append("rollback")

    db.execute = execute
    db.add = add
    db.delete = delete
    db.commit = commit
    db.refresh = refresh
    db.rollback = rollback
    return db


def _make_logger_recorder(
    events: list[str],
) -> tuple[types.SimpleNamespace, list[tuple[tuple[object, ...], dict[str, object]]]]:
    info_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _info(*args: object, **kwargs: object) -> None:
        info_calls.append((args, dict(kwargs)))
        events.append("log")

    def _unexpected(level: str):
        def _fail(*args: object, **kwargs: object) -> None:
            raise AssertionError(f"Unexpected logger.{level} call: args={args!r}, kwargs={kwargs!r}")

        return _fail

    recorder = types.SimpleNamespace(
        info=_info,
        debug=_unexpected("debug"),
        warning=_unexpected("warning"),
        error=_unexpected("error"),
        exception=_unexpected("exception"),
        critical=_unexpected("critical"),
    )
    return recorder, info_calls


def _make_scenario_harness(
    *,
    commodity_by_name: dict[str, Commodity | None],
    alias_by_text: dict[str, CommodityAlias | None],
    commit_error: Exception | None = None,
    cache_error: Exception | None = None,
) -> tuple[
    list[str],
    types.SimpleNamespace,
    types.SimpleNamespace,
    list[tuple[tuple[object, ...], dict[str, object]]],
    AsyncMock,
    list[Exception],
]:
    events: list[str] = []
    db = _make_fake_db(
        events=events,
        commodity_by_name=commodity_by_name,
        alias_by_text=alias_by_text,
        commit_error=commit_error,
    )
    logger_recorder, info_calls = _make_logger_recorder(events)
    raised_cache_errors: list[Exception] = []

    async def _invalidate_side_effect(*args: object, **kwargs: object) -> None:
        events.append("invalidate")
        if cache_error is not None and not raised_cache_errors:
            raised_cache_errors.append(cache_error)
            raise cache_error

    invalidate_mock = AsyncMock(side_effect=_invalidate_side_effect)
    return events, db, logger_recorder, info_calls, invalidate_mock, raised_cache_errors


class CommodityAliasAddCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_alias_validates_availability_and_persists_once(self) -> None:
        fence = install_p1_00_network_fence()
        self.assertTrue(fence["installed"])
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        handler_globals = add_alias_to_commodity.__globals__
        original_logger = handler_globals["logger"]
        original_no_digits = handler_globals["ensure_commodity_text_has_no_digits"]
        original_is_available = handler_globals["ensure_commodity_text_is_available"]
        original_select = handler_globals["select"]

        self.assertIs(CommodityAliasCreate, schemas.CommodityAliasCreate)
        self.assertIs(handler_globals["Commodity"], Commodity)
        self.assertIs(handler_globals["CommodityAlias"], CommodityAlias)
        self.assertIsInstance(original_no_digits, types.FunctionType)
        self.assertIsInstance(original_is_available, types.FunctionType)

        # 1. digits rejected before any DB execute, mutation or cache call
        events, db, logger_rec, info_calls, invalidate_mock, _ = _make_scenario_harness(
            commodity_by_name={},
            alias_by_text={},
        )
        self.assertIsInstance(invalidate_mock, AsyncMock)
        with (
            patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=invalidate_mock),
            patch("api.routers.commodities.logger", new=logger_rec),
        ):
            with self.assertRaises(HTTPException) as exc_ctx:
                await add_alias_to_commodity(
                    commodity_id=1,
                    alias=CommodityAliasCreate(alias="طلای18"),
                    db=db,
                    source="bot",
                )
        self.assertIs(handler_globals["logger"], original_logger)
        self.assertEqual(exc_ctx.exception.status_code, 400)
        self.assertEqual(
            exc_ctx.exception.detail,
            "شما نمیتوانید در نام کالا از اعداد استفاده کنید",
        )
        self.assertEqual(
            info_calls,
            [(("Adding alias 'طلای18' to commodity ID 1 via source: bot",), {})],
        )
        self.assertEqual(events, ["log"])
        self.assertEqual(db.executed, [])
        self.assertEqual(db.results, [])
        self.assertEqual(db.added, [])
        self.assertEqual(db.deleted, [])
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.refreshes, [])
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(invalidate_mock.call_count, 0)
        self.assertEqual(invalidate_mock.await_count, 0)
        invalidate_mock.assert_not_awaited()

        # 2. existing commodity name owned by another commodity id -> 409 after first execute
        other_commodity = Commodity(id=99, name="gold")
        events, db, logger_rec, info_calls, invalidate_mock, _ = _make_scenario_harness(
            commodity_by_name={"gold": other_commodity},
            alias_by_text={},
        )
        with (
            patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=invalidate_mock),
            patch("api.routers.commodities.logger", new=logger_rec),
        ):
            with self.assertRaises(HTTPException) as exc_ctx:
                await add_alias_to_commodity(
                    commodity_id=1,
                    alias=CommodityAliasCreate(alias="gold"),
                    db=db,
                    source="bot",
                )
        self.assertIs(handler_globals["logger"], original_logger)
        self.assertEqual(exc_ctx.exception.status_code, 409)
        self.assertEqual(
            exc_ctx.exception.detail,
            "نام «gold» از قبل به عنوان نام اصلی یک کالا ثبت شده است",
        )
        self.assertEqual(
            info_calls,
            [(("Adding alias 'gold' to commodity ID 1 via source: bot",), {})],
        )
        self.assertEqual(events, ["log", "execute:commodities.name"])
        self.assertEqual(db.executed, [(COMMODITY_NAME_SQL, {"name_1": "gold"})])
        self.assertEqual(len(db.results), 1)
        self.assertIsInstance(db.results[0].scalar_one_or_none, types.FunctionType)
        self.assertNotIsInstance(db.results[0].scalar_one_or_none, AsyncMock)
        self.assertEqual(db.results[0].calls, 1)
        self.assertEqual(other_commodity.id, 99)
        self.assertEqual(other_commodity.name, "gold")
        self.assertEqual(db.added, [])
        self.assertEqual(db.deleted, [])
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.refreshes, [])
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(invalidate_mock.call_count, 0)
        self.assertEqual(invalidate_mock.await_count, 0)
        invalidate_mock.assert_not_awaited()

        # 3. own commodity name (existing Commodity.id == current commodity_id) is allowed
        own_commodity = Commodity(id=1, name="gold")
        events, db, logger_rec, info_calls, invalidate_mock, _ = _make_scenario_harness(
            commodity_by_name={"gold": own_commodity},
            alias_by_text={"gold": None},
        )
        with (
            patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=invalidate_mock),
            patch("api.routers.commodities.logger", new=logger_rec),
        ):
            own_name_result = await add_alias_to_commodity(
                commodity_id=1,
                alias=CommodityAliasCreate(alias="gold"),
                db=db,
                source="bot",
            )
        self.assertIs(handler_globals["logger"], original_logger)
        self.assertIsInstance(own_name_result, CommodityAlias)
        self.assertIsNone(own_name_result.id)
        self.assertEqual(own_name_result.commodity_id, 1)
        self.assertEqual(own_name_result.alias, "gold")
        self.assertEqual(
            info_calls,
            [(("Adding alias 'gold' to commodity ID 1 via source: bot",), {})],
        )
        self.assertEqual(
            db.executed,
            [
                (COMMODITY_NAME_SQL, {"name_1": "gold"}),
                (COMMODITY_ALIAS_SQL, {"alias_1": "gold"}),
            ],
        )
        self.assertEqual([r.calls for r in db.results], [1, 1])
        self.assertEqual(len(db.added), 1)
        self.assertIs(db.added[0], own_name_result)
        self.assertEqual(db.deleted, [])
        self.assertEqual(db.commits, 1)
        self.assertEqual(len(db.refreshes), 1)
        self.assertIs(db.refreshes[0], own_name_result)
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(invalidate_mock.call_count, 1)
        self.assertEqual(invalidate_mock.await_count, 1)
        invalidate_mock.assert_awaited_once_with()
        self.assertEqual(
            events,
            [
                "log",
                "execute:commodities.name",
                "execute:commodity_aliases.alias",
                "add",
                "commit",
                "refresh",
                "invalidate",
            ],
        )

        # 4. existing alias on another commodity -> 409 after both availability queries
        other_alias = CommodityAlias(id=5, commodity_id=2, alias="gold")
        events, db, logger_rec, info_calls, invalidate_mock, _ = _make_scenario_harness(
            commodity_by_name={"gold": None},
            alias_by_text={"gold": other_alias},
        )
        with (
            patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=invalidate_mock),
            patch("api.routers.commodities.logger", new=logger_rec),
        ):
            with self.assertRaises(HTTPException) as exc_ctx:
                await add_alias_to_commodity(
                    commodity_id=1,
                    alias=CommodityAliasCreate(alias="gold"),
                    db=db,
                    source="bot",
                )
        self.assertIs(handler_globals["logger"], original_logger)
        self.assertEqual(exc_ctx.exception.status_code, 409)
        self.assertEqual(
            exc_ctx.exception.detail,
            "نام «gold» از قبل به عنوان نام مستعار یک کالا ثبت شده است",
        )
        self.assertEqual(
            info_calls,
            [(("Adding alias 'gold' to commodity ID 1 via source: bot",), {})],
        )
        self.assertEqual(
            db.executed,
            [
                (COMMODITY_NAME_SQL, {"name_1": "gold"}),
                (COMMODITY_ALIAS_SQL, {"alias_1": "gold"}),
            ],
        )
        self.assertEqual([r.calls for r in db.results], [1, 1])
        self.assertEqual(other_alias.id, 5)
        self.assertEqual(other_alias.commodity_id, 2)
        self.assertEqual(other_alias.alias, "gold")
        self.assertEqual(
            events,
            ["log", "execute:commodities.name", "execute:commodity_aliases.alias"],
        )
        self.assertEqual(db.added, [])
        self.assertEqual(db.deleted, [])
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.refreshes, [])
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(invalidate_mock.call_count, 0)
        self.assertEqual(invalidate_mock.await_count, 0)
        invalidate_mock.assert_not_awaited()

        # 5. clean success: both availability queries None -> persists, refreshes, invalidates
        events, db, logger_rec, info_calls, invalidate_mock, _ = _make_scenario_harness(
            commodity_by_name={"gold": None},
            alias_by_text={"gold": None},
        )
        with (
            patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=invalidate_mock),
            patch("api.routers.commodities.logger", new=logger_rec),
        ):
            result = await add_alias_to_commodity(
                commodity_id=1,
                alias=CommodityAliasCreate(alias="gold"),
                db=db,
                source="bot",
            )
        self.assertIs(handler_globals["logger"], original_logger)
        self.assertIsInstance(result, CommodityAlias)
        self.assertIsNone(result.id)
        self.assertEqual(result.commodity_id, 1)
        self.assertEqual(result.alias, "gold")
        self.assertEqual(
            info_calls,
            [(("Adding alias 'gold' to commodity ID 1 via source: bot",), {})],
        )
        self.assertEqual(
            db.executed,
            [
                (COMMODITY_NAME_SQL, {"name_1": "gold"}),
                (COMMODITY_ALIAS_SQL, {"alias_1": "gold"}),
            ],
        )
        self.assertEqual([r.calls for r in db.results], [1, 1])
        for query_result in db.results:
            self.assertIsInstance(query_result.scalar_one_or_none, types.FunctionType)
            self.assertNotIsInstance(query_result.scalar_one_or_none, AsyncMock)
        self.assertEqual(db.added, [result])
        self.assertIs(db.added[0], result)
        self.assertEqual(db.deleted, [])
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.refreshes, [result])
        self.assertIs(db.refreshes[0], result)
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(invalidate_mock.call_count, 1)
        self.assertEqual(invalidate_mock.await_count, 1)
        invalidate_mock.assert_awaited_once_with()
        self.assertEqual(invalidate_mock.await_args_list[0].args, ())
        self.assertEqual(invalidate_mock.await_args_list[0].kwargs, {})
        self.assertEqual(
            events,
            [
                "log",
                "execute:commodities.name",
                "execute:commodity_aliases.alias",
                "add",
                "commit",
                "refresh",
                "invalidate",
            ],
        )

        # 6. commit failure -> rollback once, HTTPException 404 with sentinel as __context__
        commit_sentinel = RuntimeError("alias-commit-failure")
        events, db, logger_rec, info_calls, invalidate_mock, _ = _make_scenario_harness(
            commodity_by_name={"gold": None},
            alias_by_text={"gold": None},
            commit_error=commit_sentinel,
        )
        with (
            patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=invalidate_mock),
            patch("api.routers.commodities.logger", new=logger_rec),
        ):
            with self.assertRaises(HTTPException) as exc_ctx:
                await add_alias_to_commodity(
                    commodity_id=1,
                    alias=CommodityAliasCreate(alias="gold"),
                    db=db,
                    source="bot",
                )
        self.assertIs(handler_globals["logger"], original_logger)
        self.assertEqual(exc_ctx.exception.status_code, 404)
        self.assertEqual(
            exc_ctx.exception.detail,
            "کالایی با این ID برای افزودن نام مستعار یافت نشد",
        )
        self.assertIs(exc_ctx.exception.__context__, commit_sentinel)
        self.assertIsNone(exc_ctx.exception.__cause__)
        self.assertEqual(
            info_calls,
            [(("Adding alias 'gold' to commodity ID 1 via source: bot",), {})],
        )
        self.assertEqual(
            db.executed,
            [
                (COMMODITY_NAME_SQL, {"name_1": "gold"}),
                (COMMODITY_ALIAS_SQL, {"alias_1": "gold"}),
            ],
        )
        self.assertEqual([r.calls for r in db.results], [1, 1])
        self.assertEqual(len(db.added), 1)
        self.assertIsInstance(db.added[0], CommodityAlias)
        self.assertIsNone(db.added[0].id)
        self.assertEqual(db.added[0].commodity_id, 1)
        self.assertEqual(db.added[0].alias, "gold")
        self.assertEqual(db.deleted, [])
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.refreshes, [])
        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(invalidate_mock.call_count, 0)
        self.assertEqual(invalidate_mock.await_count, 0)
        invalidate_mock.assert_not_awaited()
        self.assertEqual(
            events,
            [
                "log",
                "execute:commodities.name",
                "execute:commodity_aliases.alias",
                "add",
                "commit",
                "rollback",
            ],
        )

        # 7. cache invalidation failure -> swallowed after commit and refresh, no rollback
        cache_sentinel = RuntimeError("alias-cache-down")
        (
            events,
            db,
            logger_rec,
            info_calls,
            invalidate_mock,
            raised_cache_errors,
        ) = _make_scenario_harness(
            commodity_by_name={"gold": None},
            alias_by_text={"gold": None},
            cache_error=cache_sentinel,
        )
        with (
            patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=invalidate_mock),
            patch("api.routers.commodities.logger", new=logger_rec),
        ):
            cache_fail_result = await add_alias_to_commodity(
                commodity_id=1,
                alias=CommodityAliasCreate(alias="gold"),
                db=db,
                source="bot",
            )
        self.assertIs(handler_globals["logger"], original_logger)
        self.assertIsInstance(cache_fail_result, CommodityAlias)
        self.assertIsNone(cache_fail_result.id)
        self.assertEqual(cache_fail_result.commodity_id, 1)
        self.assertEqual(cache_fail_result.alias, "gold")
        self.assertEqual(
            info_calls,
            [(("Adding alias 'gold' to commodity ID 1 via source: bot",), {})],
        )
        self.assertEqual(
            db.executed,
            [
                (COMMODITY_NAME_SQL, {"name_1": "gold"}),
                (COMMODITY_ALIAS_SQL, {"alias_1": "gold"}),
            ],
        )
        self.assertEqual([r.calls for r in db.results], [1, 1])
        self.assertEqual(db.added, [cache_fail_result])
        self.assertIs(db.added[0], cache_fail_result)
        self.assertEqual(db.deleted, [])
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.refreshes, [cache_fail_result])
        self.assertIs(db.refreshes[0], cache_fail_result)
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(invalidate_mock.call_count, 1)
        self.assertEqual(invalidate_mock.await_count, 1)
        invalidate_mock.assert_awaited_once_with()
        self.assertEqual(len(raised_cache_errors), 1)
        self.assertIs(raised_cache_errors[0], cache_sentinel)
        self.assertEqual(
            events,
            [
                "log",
                "execute:commodities.name",
                "execute:commodity_aliases.alias",
                "add",
                "commit",
                "refresh",
                "invalidate",
            ],
        )

        # Final restoration, zero-patching, and network-fence checks
        self.assertIs(handler_globals["logger"], original_logger)
        self.assertNotIsInstance(handler_globals["logger"], AsyncMock)
        self.assertIs(handler_globals["ensure_commodity_text_has_no_digits"], original_no_digits)
        self.assertIs(handler_globals["ensure_commodity_text_is_available"], original_is_available)
        self.assertIs(handler_globals["select"], original_select)
        self.assertIs(handler_globals["Commodity"], Commodity)
        self.assertIs(handler_globals["CommodityAlias"], CommodityAlias)
        self.assertEqual(len(fence["denied_connects"]) - denied_connects_before, 0)
        self.assertEqual(len(fence["live_redis_constructs"]) - live_redis_before, 0)
        self.assertEqual(fence["denied_connects"][denied_connects_before:], [])
        self.assertEqual(fence["live_redis_constructs"][live_redis_before:], [])


if __name__ == "__main__":
    unittest.main()
