import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.sync import (
    _apply_item,
    _build_upsert_stmt,
    _localize_trade_delivery_receipt_references,
    _localize_offer_request_resulting_trade_reference,
)
from models.offer import Offer
from models.offer_request import OfferRequest
from models.trade import Trade
from sqlalchemy.exc import IntegrityError


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class ApplyDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.execute_calls = []

    def begin_nested(self):
        return AsyncNullContext()

    async def execute(self, stmt, execution_options=None):
        self.execute_calls.append((stmt, execution_options))
        if self.execute_results:
            result = self.execute_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return SimpleNamespace()


class FakeOfferExecuteResult:
    def __init__(self, offer):
        self._offer = offer

    def scalars(self):
        return SimpleNamespace(first=lambda: self._offer)


class FakeTradeExecuteResult:
    def __init__(self, trade):
        self._trade = trade

    def scalars(self):
        return SimpleNamespace(first=lambda: self._trade)


class FakeScalarExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeFirstExecuteResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class ExpressionProbe:
    def __init__(self, expression):
        self.expression = expression

    @staticmethod
    def _render(other):
        return getattr(other, "expression", repr(other))

    def in_(self, values):
        return ExpressionProbe(f"{self.expression} IN {tuple(sorted(values))}")

    def isnot(self, other):
        return ExpressionProbe(f"{self.expression} IS NOT {other}")

    def is_(self, other):
        return ExpressionProbe(f"{self.expression} IS {other}")

    def is_not_distinct_from(self, other):
        return ExpressionProbe(f"{self.expression} IS NOT DISTINCT FROM {self._render(other)}")

    def __le__(self, other):
        return ExpressionProbe(f"{self.expression} <= {self._render(other)}")

    def __eq__(self, other):
        return ExpressionProbe(f"{self.expression} == {self._render(other)}")

    def __ne__(self, other):
        return ExpressionProbe(f"{self.expression} != {self._render(other)}")

    def __and__(self, other):
        return ExpressionProbe(f"({self.expression} AND {self._render(other)})")

    def __or__(self, other):
        return ExpressionProbe(f"({self.expression} OR {self._render(other)})")

    def __invert__(self):
        return ExpressionProbe(f"NOT ({self.expression})")

    def __repr__(self):
        return self.expression


class FakeOfferModel:
    status = ExpressionProbe("current_status")
    version_id = ExpressionProbe("current_version")


class FakeOfferRequestModel:
    idempotency_key = ExpressionProbe("current_idempotency_key")
    result_status = ExpressionProbe("current_result_status")
    version_id = ExpressionProbe("current_version")


class FakeTradeModel:
    offer_id = ExpressionProbe("current_offer_id")
    offer_user_id = ExpressionProbe("current_offer_user_id")
    responder_user_id = ExpressionProbe("current_responder_user_id")
    actor_user_id = ExpressionProbe("current_actor_user_id")
    commodity_id = ExpressionProbe("current_commodity_id")
    trade_type = ExpressionProbe("current_trade_type")
    settlement_type = ExpressionProbe("current_settlement_type")
    quantity = ExpressionProbe("current_quantity")
    price = ExpressionProbe("current_price")
    status = ExpressionProbe("current_status")
    trade_number = ExpressionProbe("current_trade_number")
    archived = ExpressionProbe("current_archived")


class FakeCustomerRelationModel:
    customer_user_id = ExpressionProbe("current_customer_user_id")
    status = ExpressionProbe("current_status")
    deleted_at = ExpressionProbe("current_deleted_at")


class FakeOfferInsertBuilder:
    def __init__(self):
        self.excluded = {
            "status": ExpressionProbe("incoming_status"),
            "version_id": ExpressionProbe("incoming_version"),
        }
        self.where_clause = None

    def values(self, **kwargs):
        self.values_payload = kwargs
        return self

    def on_conflict_do_update(self, index_elements, set_, where=None):
        self.conflict_payload = (index_elements, set_)
        self.where_clause = where
        return self


class FakeTradeInsertBuilder:
    def __init__(self):
        self.excluded = {
            "offer_id": ExpressionProbe("incoming_offer_id"),
            "offer_user_id": ExpressionProbe("incoming_offer_user_id"),
            "responder_user_id": ExpressionProbe("incoming_responder_user_id"),
            "actor_user_id": ExpressionProbe("incoming_actor_user_id"),
            "commodity_id": ExpressionProbe("incoming_commodity_id"),
            "trade_type": ExpressionProbe("incoming_trade_type"),
            "settlement_type": ExpressionProbe("incoming_settlement_type"),
            "quantity": ExpressionProbe("incoming_quantity"),
            "price": ExpressionProbe("incoming_price"),
            "status": ExpressionProbe("incoming_status"),
            "trade_number": ExpressionProbe("incoming_trade_number"),
            "archived": ExpressionProbe("incoming_archived"),
        }
        self.where_clause = None
        self.conflict_payload = None

    def values(self, **kwargs):
        self.values_payload = kwargs
        return self

    def on_conflict_do_update(self, index_elements, set_, where=None):
        self.conflict_payload = (index_elements, set_)
        self.where_clause = where
        return self


class FakeOfferRequestInsertBuilder:
    def __init__(self):
        self.excluded = {
            "result_status": ExpressionProbe("incoming_result_status"),
            "version_id": ExpressionProbe("incoming_version"),
        }
        self.where_clause = None
        self.index_where = None

    def values(self, **kwargs):
        self.values_payload = kwargs
        return self

    def on_conflict_do_update(self, index_elements, set_, index_where=None, where=None):
        self.conflict_payload = (index_elements, set_)
        self.index_where = index_where
        self.where_clause = where
        return self


class FakeCustomerRelationInsertBuilder:
    def __init__(self):
        self.excluded = {
            "customer_user_id": ExpressionProbe("incoming_customer_user_id"),
            "status": ExpressionProbe("incoming_status"),
            "deleted_at": ExpressionProbe("incoming_deleted_at"),
        }
        self.where_clause = None

    def values(self, **kwargs):
        self.values_payload = kwargs
        return self

    def on_conflict_do_update(self, index_elements, set_, where=None):
        self.conflict_payload = (index_elements, set_)
        self.where_clause = where
        return self


def make_offer(status, version_id):
    return SimpleNamespace(id=8, status=status, version_id=version_id)


def make_completed_trade(**overrides):
    values = {
        "id": 80,
        "trade_number": 10008,
        "offer_id": 8,
        "offer_user_id": 2,
        "responder_user_id": 5,
        "actor_user_id": 5,
        "commodity_id": 1,
        "trade_type": "buy",
        "settlement_type": "tomorrow",
        "quantity": 3,
        "price": 120,
        "status": "completed",
        "archived": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def completed_trade_payload(**overrides):
    values = {
        "trade_number": 10008,
        "offer_id": 8,
        "offer_user_id": 2,
        "responder_user_id": 5,
        "actor_user_id": 5,
        "commodity_id": 1,
        "trade_type": "buy",
        "settlement_type": "tomorrow",
        "quantity": 3,
        "price": 120,
        "status": "completed",
        "archived": False,
    }
    values.update(overrides)
    return values


class SyncRouterStaleOfferEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_trade_delivery_receipt_trade_number_resolves_local_trade_and_offer_ids(self):
        data = {
            "dedupe_key": "trade_completed:webapp:10088:7",
            "trade_number": 10088,
            "trade_id": 88,
            "offer_id": 12,
        }
        db = ApplyDB([FakeFirstExecuteResult((501, 601))])

        resolved = await _localize_trade_delivery_receipt_references(db, data)

        self.assertTrue(resolved)
        self.assertEqual(data["trade_id"], 501)
        self.assertEqual(data["offer_id"], 601)

    async def test_trade_delivery_receipt_trade_number_defers_until_trade_arrives(self):
        data = {
            "dedupe_key": "trade_completed:webapp:10088:7",
            "trade_number": 10088,
            "trade_id": 88,
            "offer_id": 12,
        }
        db = ApplyDB([FakeFirstExecuteResult(None)])

        resolved = await _localize_trade_delivery_receipt_references(db, data)

        self.assertFalse(resolved)
        self.assertEqual(data["trade_id"], 88)
        self.assertEqual(data["offer_id"], 12)

    async def test_offer_request_resulting_trade_number_resolves_to_local_trade_id(self):
        data = {
            "request_home_server": "foreign",
            "idempotency_key": "request-1",
            "resulting_trade_number": 10088,
            "resulting_trade_id": 88,
        }

        with patch("api.routers.sync._resolve_trade_id_by_trade_number", new=AsyncMock(return_value=501)):
            resolved = await _localize_offer_request_resulting_trade_reference(SimpleNamespace(), data)

        self.assertTrue(resolved)
        self.assertEqual(data["resulting_trade_id"], 501)
        self.assertNotIn("resulting_trade_number", data)

    async def test_offer_request_resulting_trade_number_defers_until_trade_arrives(self):
        data = {
            "request_home_server": "foreign",
            "idempotency_key": "request-1",
            "resulting_trade_number": 10088,
            "resulting_trade_id": 88,
        }

        with patch("api.routers.sync._resolve_trade_id_by_trade_number", new=AsyncMock(return_value=None)):
            resolved = await _localize_offer_request_resulting_trade_reference(SimpleNamespace(), data)

        self.assertFalse(resolved)
        self.assertIsNone(data["resulting_trade_id"])
        self.assertNotIn("resulting_trade_number", data)

    async def test_offer_upsert_uses_atomic_ordering_where_clause(self):
        insert_builder = FakeOfferInsertBuilder()

        with patch("api.routers.sync.pg_insert", return_value=insert_builder):
            result = _build_upsert_stmt(
                FakeOfferModel,
                "offers",
                {"id": 8, "status": "active", "version_id": 3},
            )

        self.assertIs(result, insert_builder)
        self.assertIsNotNone(insert_builder.where_clause)
        rendered_where = repr(insert_builder.where_clause)
        self.assertIn("current_version <= incoming_version", rendered_where)
        self.assertIn("current_status IN", rendered_where)

    async def test_offer_request_upsert_uses_atomic_ordering_where_clause(self):
        insert_builder = FakeOfferRequestInsertBuilder()

        with patch("api.routers.sync.pg_insert", return_value=insert_builder):
            result = _build_upsert_stmt(
                FakeOfferRequestModel,
                "offer_requests",
                {
                    "id": 2,
                    "request_home_server": "foreign",
                    "idempotency_key": "stage-forward:2",
                    "result_status": "completed_trade",
                    "resulting_trade_id": 61,
                    "version_id": 2,
                },
            )

        self.assertIs(result, insert_builder)
        self.assertEqual(insert_builder.conflict_payload[0], ["request_home_server", "idempotency_key"])
        self.assertNotIn("request_home_server", insert_builder.conflict_payload[1])
        self.assertNotIn("idempotency_key", insert_builder.conflict_payload[1])
        self.assertIsNotNone(insert_builder.index_where)
        self.assertIsNotNone(insert_builder.where_clause)
        rendered_where = repr(insert_builder.where_clause)
        self.assertIn("current_version <= incoming_version", rendered_where)
        self.assertIn("current_result_status IN", rendered_where)

    async def test_customer_relation_upsert_protects_resolved_link_from_stale_null_link(self):
        insert_builder = FakeCustomerRelationInsertBuilder()

        with patch("api.routers.sync.pg_insert", return_value=insert_builder):
            result = _build_upsert_stmt(
                FakeCustomerRelationModel,
                "customer_relations",
                {
                    "id": 1,
                    "customer_user_id": None,
                    "status": "active",
                    "deleted_at": None,
                },
            )

        self.assertIs(result, insert_builder)
        self.assertIsNotNone(insert_builder.where_clause)
        rendered_where = repr(insert_builder.where_clause)
        self.assertIn("current_customer_user_id IS None", rendered_where)
        self.assertIn("incoming_customer_user_id IS NOT None", rendered_where)
        self.assertIn("incoming_deleted_at IS NOT None", rendered_where)
        self.assertIn("incoming_status IN", rendered_where)

    async def test_stale_null_link_relation_upsert_noop_is_ignored_with_audit_metadata(self):
        db = ApplyDB([SimpleNamespace(rowcount=0)])
        data = {"id": 1, "customer_user_id": None, "status": "active", "deleted_at": None}

        with patch("api.routers.sync._build_upsert_stmt", return_value="CUSTOMER_RELATION_UPSERT"), patch(
            "api.routers.sync.logger"
        ) as logger_mock:
            result = await _apply_item(
                db,
                "customer_relations",
                "UPDATE",
                1,
                data,
                model=SimpleNamespace(),
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("stale_null_relation_link", rendered_log)
        self.assertIn("sync.stale_linked_relation_ignored", rendered_log)

    async def test_trade_upsert_uses_atomic_completed_trade_guard(self):
        insert_builder = FakeTradeInsertBuilder()

        with patch("api.routers.sync.pg_insert", return_value=insert_builder):
            result = _build_upsert_stmt(
                FakeTradeModel,
                "trades",
                completed_trade_payload(id=80),
            )

        self.assertIs(result, insert_builder)
        self.assertEqual(insert_builder.conflict_payload[0], ["trade_number"])
        self.assertNotIn("id", insert_builder.conflict_payload[1])
        self.assertNotIn("trade_number", insert_builder.conflict_payload[1])
        self.assertIsNotNone(insert_builder.where_clause)
        rendered_where = repr(insert_builder.where_clause)
        self.assertIn("current_status !=", rendered_where)
        self.assertIn("OR", rendered_where)
        self.assertIn("current_price IS NOT DISTINCT FROM incoming_price", rendered_where)
        self.assertIn("current_settlement_type IS NOT DISTINCT FROM incoming_settlement_type", rendered_where)
        self.assertIn("current_trade_number IS NOT DISTINCT FROM incoming_trade_number", rendered_where)

    async def test_completed_trade_delete_is_ignored(self):
        existing_trade = make_completed_trade()
        db = ApplyDB([FakeScalarExecuteResult(80), FakeTradeExecuteResult(existing_trade)])

        with patch("api.routers.sync._build_upsert_stmt") as builder, patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "DELETE",
                80,
                {"trade_number": 10008},
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        builder.assert_not_called()
        self.assertEqual(len(db.execute_calls), 2)
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("completed_trade_delete", rendered_log)
        self.assertIn("sync.trade_guard_ignored", rendered_log)

    async def test_missing_trade_delete_is_ignored_without_id_fallback(self):
        db = ApplyDB([
            FakeScalarExecuteResult(None),
        ])

        with patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "DELETE",
                80,
                {"trade_number": 10008},
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("natural_identity_not_found", rendered_log)
        self.assertIn("sync.unsafe_id_only_delete_ignored", rendered_log)

    async def test_completed_trade_cannot_be_reopened_by_sync(self):
        existing_trade = make_completed_trade()
        db = ApplyDB([FakeTradeExecuteResult(existing_trade)])
        data = completed_trade_payload(status="pending")

        with patch("api.routers.sync._build_upsert_stmt") as builder, patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "UPDATE",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        builder.assert_not_called()
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("completed_to_non_completed_update", rendered_log)

    async def test_completed_trade_business_field_mismatch_is_ignored(self):
        existing_trade = make_completed_trade()
        db = ApplyDB([FakeTradeExecuteResult(existing_trade)])
        data = completed_trade_payload(price=121)

        with patch("api.routers.sync._build_upsert_stmt") as builder, patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "UPDATE",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        builder.assert_not_called()
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("protected_business_field_mismatch", rendered_log)

    async def test_completed_trade_settlement_mismatch_is_ignored(self):
        existing_trade = make_completed_trade()
        db = ApplyDB([FakeTradeExecuteResult(existing_trade)])
        data = completed_trade_payload(settlement_type="cash")

        with patch("api.routers.sync._build_upsert_stmt") as builder, patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "UPDATE",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        builder.assert_not_called()
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("protected_business_field_mismatch", rendered_log)

    async def test_completed_trade_incomplete_destructive_payload_is_ignored(self):
        existing_trade = make_completed_trade()
        db = ApplyDB([FakeTradeExecuteResult(existing_trade)])

        with patch("api.routers.sync._build_upsert_stmt") as builder, patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "UPDATE",
                80,
                {"trade_number": 10008, "status": "completed"},
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        builder.assert_not_called()
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("incomplete_destructive_payload", rendered_log)

    async def test_duplicate_completed_trade_sync_is_idempotent(self):
        existing_trade = make_completed_trade()
        db = ApplyDB([FakeTradeExecuteResult(existing_trade), SimpleNamespace(rowcount=1)])
        data = completed_trade_payload()

        with patch("api.routers.sync._build_upsert_stmt", return_value="TRADE_UPSERT") as builder:
            result = await _apply_item(
                db,
                "trades",
                "UPDATE",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        builder.assert_called_once()
        self.assertEqual(db.execute_calls[-1], ("TRADE_UPSERT", {"is_sync": True}))

    async def test_duplicate_completed_trade_sync_with_nullable_actor_is_idempotent(self):
        existing_trade = make_completed_trade(actor_user_id=None)
        db = ApplyDB([FakeTradeExecuteResult(existing_trade), SimpleNamespace(rowcount=1)])
        data = completed_trade_payload(actor_user_id=None)

        with patch("api.routers.sync._build_upsert_stmt", return_value="TRADE_UPSERT") as builder:
            result = await _apply_item(
                db,
                "trades",
                "UPDATE",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        builder.assert_called_once()

    async def test_missing_completed_trade_sync_is_inserted(self):
        db = ApplyDB([FakeTradeExecuteResult(None), SimpleNamespace(rowcount=1)])
        data = completed_trade_payload()

        with patch("api.routers.sync._build_upsert_stmt", return_value="TRADE_UPSERT") as builder:
            result = await _apply_item(
                db,
                "trades",
                "INSERT",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        builder.assert_called_once()
        self.assertEqual(db.execute_calls[-1], ("TRADE_UPSERT", {"is_sync": True}))

    async def test_non_completed_trade_can_be_completed_by_valid_sync(self):
        existing_trade = make_completed_trade(status="pending")
        db = ApplyDB([FakeTradeExecuteResult(existing_trade), SimpleNamespace(rowcount=1)])
        data = completed_trade_payload()

        with patch("api.routers.sync._build_upsert_stmt", return_value="TRADE_UPSERT") as builder:
            result = await _apply_item(
                db,
                "trades",
                "UPDATE",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        builder.assert_called_once()

    async def test_trade_atomic_upsert_guard_noop_is_ignored_with_audit_metadata(self):
        db = ApplyDB([FakeTradeExecuteResult(None), FakeTradeExecuteResult(None), SimpleNamespace(rowcount=0)])
        data = completed_trade_payload()

        with patch("api.routers.sync._build_upsert_stmt", return_value="TRADE_UPSERT"), patch(
            "api.routers.sync.logger"
        ) as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "UPDATE",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("atomic_upsert_guard_noop", rendered_log)
        self.assertIn("sync.trade_guard_ignored", rendered_log)

    async def test_trade_natural_key_fallback_calls_guard_before_update(self):
        duplicate_error = IntegrityError("stmt", {}, Exception("duplicate key value violates unique constraint"))
        existing_trade = make_completed_trade(price=120)
        db = ApplyDB([
            FakeTradeExecuteResult(None),
            FakeTradeExecuteResult(None),
            duplicate_error,
            FakeTradeExecuteResult(existing_trade),
        ])
        data = completed_trade_payload(price=121)

        with patch("api.routers.sync._build_upsert_stmt", return_value="TRADE_UPSERT"), patch(
            "api.routers.sync.update"
        ) as update_mock, patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "INSERT",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        update_mock.assert_not_called()
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("protected_business_field_mismatch", rendered_log)

    async def test_trade_natural_key_fallback_applies_atomic_guard_and_ignores_noop(self):
        duplicate_error = IntegrityError(
            "stmt", {}, Exception("duplicate key value violates unique constraint")
        )
        data = completed_trade_payload(price=121)
        existing_trade = make_completed_trade(price=121)
        db = ApplyDB(
            [
                FakeTradeExecuteResult(None),
                FakeTradeExecuteResult(None),
                duplicate_error,
                FakeTradeExecuteResult(existing_trade),
                SimpleNamespace(rowcount=0),
            ]
        )

        with patch(
            "api.routers.sync._build_upsert_stmt", return_value="TRADE_UPSERT"
        ), patch("api.routers.sync.update") as update_mock, patch(
            "api.routers.sync.logger"
        ) as logger_mock:
            result = await _apply_item(
                db,
                "trades",
                "INSERT",
                80,
                data,
                model=Trade,
                new_offers=[],
            )

        self.assertEqual(result, "ignored")
        update_mock.assert_called_once_with(Trade)
        self.assertIn("atomic_upsert_guard_noop", repr(logger_mock.warning.call_args))

    async def test_out_of_order_offer_update_after_expiry_does_not_reactivate(self):
        existing_offer = make_offer("expired", 3)
        db = ApplyDB([FakeOfferExecuteResult(existing_offer)])
        data = {
            "offer_public_id": "ofr_8",
            "home_server": "iran",
            "status": "active",
            "version_id": 2,
        }

        with patch("api.routers.sync._build_upsert_stmt") as builder, patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "offers",
                "UPDATE",
                8,
                data,
                model=Offer,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        builder.assert_not_called()
        self.assertEqual(len(db.execute_calls), 1)
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("sync.stale_offer_ignored", rendered_log)
        self.assertIn("older_authoritative_version", rendered_log)
        self.assertIn("ofr_8", rendered_log)

    async def test_duplicate_terminal_offer_replay_is_idempotent(self):
        existing_offer = make_offer("expired", 3)
        db = ApplyDB([FakeOfferExecuteResult(existing_offer), SimpleNamespace(), FakeScalarExecuteResult(8)])
        data = {
            "offer_public_id": "ofr_8",
            "home_server": "iran",
            "status": "expired",
            "version_id": 3,
            "channel_message_id": 700,
        }
        terminal_offers = []

        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT") as builder, patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ):
            result = await _apply_item(
                db,
                "offers",
                "UPDATE",
                8,
                data,
                model=Offer,
                new_offers=[],
                terminal_offers=terminal_offers,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(terminal_offers, [8])
        self.assertNotIn("channel_message_id", data)
        builder.assert_called_once()
        self.assertEqual(db.execute_calls[1], ("UPSERT", {"is_sync": True}))

    async def test_same_version_non_terminal_peer_state_is_ignored_with_audit_metadata(self):
        existing_offer = make_offer("expired", 3)
        db = ApplyDB([FakeOfferExecuteResult(existing_offer)])
        data = {
            "offer_public_id": "ofr_8",
            "home_server": "iran",
            "status": "active",
            "version_id": 3,
            "idempotency_key": "offer-reactivate:8",
        }

        with patch("api.routers.sync._build_upsert_stmt") as builder, patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "offers",
                "UPDATE",
                8,
                data,
                model=Offer,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        builder.assert_not_called()
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("terminal_state_protected", rendered_log)
        self.assertIn("command_idempotency_id", rendered_log)
        self.assertIn("offer-reactivate:8", rendered_log)

    async def test_atomic_upsert_guard_noop_is_logged_with_audit_metadata(self):
        existing_offer = make_offer("active", 2)
        db = ApplyDB([FakeOfferExecuteResult(existing_offer), SimpleNamespace(rowcount=0)])
        data = {
            "offer_public_id": "ofr_8",
            "home_server": "iran",
            "status": "active",
            "version_id": 3,
        }

        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"), patch(
            "api.routers.sync.logger"
        ) as logger_mock:
            result = await _apply_item(
                db,
                "offers",
                "UPDATE",
                8,
                data,
                model=Offer,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("atomic_upsert_guard_noop", rendered_log)
        self.assertIn("sync.stale_offer_ignored", rendered_log)

    async def test_newer_terminal_offer_update_wins(self):
        existing_offer = make_offer("active", 2)
        db = ApplyDB([FakeOfferExecuteResult(existing_offer), SimpleNamespace(), FakeScalarExecuteResult(8)])
        data = {
            "offer_public_id": "ofr_8",
            "home_server": "iran",
            "status": "completed",
            "version_id": 3,
        }
        terminal_offers = []

        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT") as builder, patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ):
            result = await _apply_item(
                db,
                "offers",
                "UPDATE",
                8,
                data,
                model=Offer,
                new_offers=[],
                terminal_offers=terminal_offers,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(terminal_offers, [8])
        builder.assert_called_once()

    async def test_stale_offer_request_upsert_noop_is_logged(self):
        db = ApplyDB([SimpleNamespace(rowcount=0)])
        data = {
            "request_home_server": "foreign",
            "idempotency_key": "stage-forward:2",
            "request_source_surface": "webapp",
            "request_source_server": "iran",
            "requested_quantity": 5,
            "result_status": "received",
            "version_id": 1,
        }

        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"), patch(
            "api.routers.sync.logger"
        ) as logger_mock:
            result = await _apply_item(
                db,
                "offer_requests",
                "INSERT",
                2,
                data,
                model=OfferRequest,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        rendered_log = repr(logger_mock.warning.call_args)
        self.assertIn("atomic_upsert_guard_noop", rendered_log)
        self.assertIn("sync.stale_offer_request_ignored", rendered_log)
