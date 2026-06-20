import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.routers.sync import _apply_item, _build_upsert_stmt
from models.offer import Offer
from models.offer_request import OfferRequest


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


class FakeScalarExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class ExpressionProbe:
    def __init__(self, expression):
        self.expression = expression

    def in_(self, values):
        return ExpressionProbe(f"{self.expression} IN {tuple(sorted(values))}")

    def isnot(self, other):
        return ExpressionProbe(f"{self.expression} IS NOT {other}")

    def __le__(self, other):
        return ExpressionProbe(f"{self.expression} <= {other.expression}")

    def __eq__(self, other):
        return ExpressionProbe(f"{self.expression} == {other.expression}")

    def __ne__(self, other):
        return ExpressionProbe(f"{self.expression} != {other.expression}")

    def __and__(self, other):
        return ExpressionProbe(f"({self.expression} AND {other.expression})")

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


def make_offer(status, version_id):
    return SimpleNamespace(id=8, status=status, version_id=version_id)


class SyncRouterStaleOfferEventTests(unittest.IsolatedAsyncioTestCase):
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
