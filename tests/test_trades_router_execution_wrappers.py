import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from api.routers.trades import (
    InternalTradeExecuteRequest,
    TradeCreate,
    create_trade,
    execute_trade_internal,
    _forward_trade_if_remote_home,
)


class FakeExecuteResult:
    def __init__(self, *, single_or_none=None):
        self._single_or_none = single_or_none

    def scalar_one_or_none(self):
        return self._single_or_none


class FakeDB:
    def __init__(self, *, get_results=None, execute_results=None):
        self.get_results = list(get_results or [])
        self.execute_results = list(execute_results or [])

    async def get(self, _model, _id):
        if not self.get_results:
            raise AssertionError("Unexpected get() call")
        return self.get_results.pop(0)

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


def make_context(owner_user, actor_user=None):
    actor = actor_user or owner_user
    return SimpleNamespace(owner_user=owner_user, actor_user=actor, relation=None, is_accountant_context=owner_user.id != actor.id)


def make_request(body=b"{}", headers=None):
    async def body_reader():
        return body

    return SimpleNamespace(body=body_reader, headers=headers or {})


class TradesRouterExecutionWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_trade_returns_forwarded_response_when_remote_home(self):
        trade_data = TradeCreate(offer_id=7, quantity=3, idempotency_key="idem-1")
        background_tasks = BackgroundTasks()
        current_user = SimpleNamespace(id=5)
        context = make_context(current_user)
        forwarded = JSONResponse(status_code=202, content={"forwarded": True})
        lease = SimpleNamespace(acquired=True, token="slot-1", release=AsyncMock())

        with patch(
            "api.routers.trades._forward_trade_if_remote_home",
            new=AsyncMock(return_value=forwarded),
        ) as forward_mock, patch(
            "api.routers.trades._execute_trade_authoritatively",
            new=AsyncMock(),
        ) as execute_mock:
            result = await create_trade(
                trade_data=trade_data,
                background_tasks=background_tasks,
                raw_request=SimpleNamespace(),
                trade_contention_lease=lease,
                db=FakeDB(),
                context=context,
            )

        self.assertIs(result, forwarded)
        lease.release.assert_awaited_once()
        forward_mock.assert_awaited_once()
        self.assertTrue(forward_mock.await_args.kwargs["request_pre_gated"])
        execute_mock.assert_not_awaited()

    async def test_create_trade_returns_remote_failure_without_local_partial_execution(self):
        trade_data = TradeCreate(offer_id=7, quantity=3, idempotency_key="idem-1")
        background_tasks = BackgroundTasks()
        current_user = SimpleNamespace(id=5)
        context = make_context(current_user)
        forwarded = JSONResponse(status_code=504, content={"detail": "remote timeout"})

        with patch(
            "api.routers.trades._forward_trade_if_remote_home",
            new=AsyncMock(return_value=forwarded),
        ), patch(
            "api.routers.trades._execute_trade_authoritatively",
            new=AsyncMock(),
        ) as execute_mock:
            result = await create_trade(
                trade_data=trade_data,
                background_tasks=background_tasks,
                raw_request=SimpleNamespace(),
                db=FakeDB(),
                context=context,
            )

        self.assertIs(result, forwarded)
        execute_mock.assert_not_awaited()

    async def test_create_trade_delegates_to_authoritative_execution_when_local(self):
        trade_data = TradeCreate(offer_id=7, quantity=3, idempotency_key="idem-1")
        background_tasks = BackgroundTasks()
        current_user = SimpleNamespace(id=5)
        context = make_context(current_user)
        lease = SimpleNamespace(acquired=True, token="slot-1", release=AsyncMock())

        with patch(
            "api.routers.trades._forward_trade_if_remote_home",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.trades._execute_trade_authoritatively",
            new=AsyncMock(return_value={"id": 77}),
        ) as execute_mock:
            result = await create_trade(
                trade_data=trade_data,
                background_tasks=background_tasks,
                raw_request=SimpleNamespace(),
                trade_contention_lease=lease,
                db=FakeDB(),
                context=context,
            )

        self.assertEqual(result, {"id": 77})
        lease.release.assert_awaited_once()
        execute_mock.assert_awaited_once()
        self.assertEqual(execute_mock.await_args.kwargs["trade_data"], trade_data)
        self.assertIs(execute_mock.await_args.kwargs["background_tasks"], background_tasks)
        self.assertEqual(execute_mock.await_args.kwargs["context"], context)
        self.assertIsInstance(execute_mock.await_args.kwargs["edge_received_at"], datetime)
        self.assertTrue(execute_mock.await_args.kwargs["request_pre_gated"])

    async def test_execute_trade_internal_rejects_invalid_signature(self):
        internal_data = InternalTradeExecuteRequest(
            offer_id=7,
            offer_public_id="ofr_remote_7",
            quantity=3,
            responder_user_id=5,
            edge_received_at=datetime.utcnow(),
            source_surface="webapp",
            source_server="iran",
            idempotency_key="idem-1",
        )

        with patch("api.routers.trades.verify_internal_signature", return_value=False):
            with self.assertRaises(HTTPException) as exc_info:
                await execute_trade_internal(
                    internal_data=internal_data,
                    background_tasks=BackgroundTasks(),
                    raw_request=make_request(headers={}),
                    db=FakeDB(),
                )
        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "Invalid internal trade signature")

    async def test_execute_trade_internal_rejects_wrong_offer_server_or_missing_responder(self):
        internal_data = InternalTradeExecuteRequest(
            offer_id=7,
            offer_public_id="ofr_remote_7",
            quantity=3,
            responder_user_id=5,
            edge_received_at=datetime.utcnow(),
            source_surface="webapp",
            source_server="iran",
            idempotency_key="idem-1",
        )
        headers = {"x-timestamp": "1", "x-signature": "sig", "x-api-key": "key", "x-source-server": "iran"}

        with patch("api.routers.trades.verify_internal_signature", return_value=True), patch(
            "api.routers.trades.normalize_server",
            side_effect=lambda value, default="foreign": str(value or default).lower(),
        ), patch("api.routers.trades.current_server", return_value="foreign"):
            with self.assertRaises(HTTPException) as exc_info:
                await execute_trade_internal(
                    internal_data=internal_data,
                    background_tasks=BackgroundTasks(),
                    raw_request=make_request(headers=headers),
                    db=FakeDB(execute_results=[FakeExecuteResult(single_or_none=SimpleNamespace(id=44, home_server="iran"))]),
                )
        self.assertEqual(exc_info.exception.status_code, 409)

        with patch("api.routers.trades.verify_internal_signature", return_value=True), patch(
            "api.routers.trades.normalize_server",
            side_effect=lambda value, default="foreign": str(value or default).lower(),
        ), patch("api.routers.trades.current_server", return_value="foreign"):
            with self.assertRaises(HTTPException) as exc_info:
                await execute_trade_internal(
                    internal_data=internal_data,
                    background_tasks=BackgroundTasks(),
                    raw_request=make_request(headers=headers),
                    db=FakeDB(
                        execute_results=[FakeExecuteResult(single_or_none=SimpleNamespace(id=44, home_server="foreign"))],
                        get_results=[None],
                    ),
                )
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "کاربر درخواست‌دهنده یافت نشد")

    async def test_execute_trade_internal_rejects_missing_mismatched_or_local_source_server(self):
        internal_data = InternalTradeExecuteRequest(
            offer_id=7,
            offer_public_id="ofr_remote_7",
            quantity=3,
            responder_user_id=5,
            edge_received_at=datetime.utcnow(),
            source_surface="webapp",
            source_server="iran",
            idempotency_key="idem-1",
        )
        base_headers = {"x-timestamp": "1", "x-signature": "sig", "x-api-key": "key"}

        for headers, source_server, current_server_value in (
            (base_headers, "iran", "foreign"),
            ({**base_headers, "x-source-server": "foreign"}, "iran", "foreign"),
            ({**base_headers, "x-source-server": "foreign"}, "foreign", "foreign"),
        ):
            with self.subTest(headers=headers, source_server=source_server, current=current_server_value):
                internal_data.source_server = source_server
                with patch("api.routers.trades.verify_internal_signature", return_value=True), patch(
                    "api.routers.trades.current_server",
                    return_value=current_server_value,
                ):
                    with self.assertRaises(HTTPException) as exc_info:
                        await execute_trade_internal(
                            internal_data=internal_data,
                            background_tasks=BackgroundTasks(),
                            raw_request=make_request(headers=headers),
                            db=FakeDB(),
                        )
                self.assertEqual(exc_info.exception.status_code, 401)
                self.assertEqual(exc_info.exception.detail, "Invalid internal trade source")

    async def test_execute_trade_internal_delegates_to_authoritative_execution(self):
        internal_data = InternalTradeExecuteRequest(
            offer_id=7,
            offer_public_id="ofr_foreign_999",
            quantity=3,
            responder_user_id=5,
            actor_user_id=44,
            edge_received_at=datetime(2026, 1, 1, 12, 0, 0),
            source_surface="telegram_bot",
            source_server="iran",
            idempotency_key="idem-1",
            request_pre_gated=True,
        )
        headers = {"x-timestamp": "1", "x-signature": "sig", "x-api-key": "key", "x-source-server": "iran"}
        responder = SimpleNamespace(id=5, is_deleted=False)
        actor = SimpleNamespace(id=44, is_deleted=False)
        resolved_offer = SimpleNamespace(id=999, home_server="foreign")

        with patch("api.routers.trades.verify_internal_signature", return_value=True), patch(
            "api.routers.trades.normalize_server",
            side_effect=lambda value, default="foreign": str(value or default).lower(),
        ), patch("api.routers.trades.current_server", return_value="foreign"), patch(
            "api.routers.trades._execute_trade_authoritatively",
            new=AsyncMock(return_value={"id": 99}),
        ) as execute_mock:
            result = await execute_trade_internal(
                internal_data=internal_data,
                background_tasks=BackgroundTasks(),
                raw_request=make_request(body=b"payload", headers=headers),
                db=FakeDB(
                    execute_results=[FakeExecuteResult(single_or_none=resolved_offer)],
                    get_results=[responder, actor],
                ),
            )

        self.assertEqual(result, {"id": 99})
        execute_mock.assert_awaited_once()
        delegated_trade_data = execute_mock.await_args.kwargs["trade_data"]
        self.assertEqual(delegated_trade_data.offer_id, 999)
        self.assertEqual(delegated_trade_data.offer_public_id, "ofr_foreign_999")
        self.assertEqual(delegated_trade_data.quantity, 3)
        self.assertEqual(delegated_trade_data.idempotency_key, "idem-1")
        delegated_context = execute_mock.await_args.kwargs["context"]
        self.assertEqual(delegated_context.owner_user, responder)
        self.assertEqual(delegated_context.actor_user, actor)
        self.assertTrue(delegated_context.is_accountant_context)
        self.assertEqual(execute_mock.await_args.kwargs["edge_received_at"], internal_data.edge_received_at)
        self.assertEqual(execute_mock.await_args.kwargs["request_source_surface"].value, "telegram_bot")
        self.assertEqual(execute_mock.await_args.kwargs["request_source_server"], "iran")
        self.assertTrue(execute_mock.await_args.kwargs["request_pre_gated"])

    async def test_forward_trade_if_remote_home_covers_both_cross_server_directions_and_idempotency(self):
        scenarios = (
            ("foreign", "iran"),
            ("iran", "foreign"),
        )
        for source_server, target_server in scenarios:
            with self.subTest(source_server=source_server, target_server=target_server):
                db = FakeDB(get_results=[SimpleNamespace(home_server=target_server, offer_public_id="ofr_forward_7")])
                owner_user = SimpleNamespace(id=5)
                edge_received_at = datetime(2026, 6, 16, 12, 0, 0)

                with patch("api.routers.trades.is_remote_home", return_value=True), patch(
                    "api.routers.trades.current_server",
                    return_value=source_server,
                ), patch(
                    "api.routers.trades.forward_trade_to_home_server",
                    new=AsyncMock(return_value=(200, {"id": 88, "replayed": True})),
                ) as forward_mock:
                    response = await _forward_trade_if_remote_home(
                        db=db,
                        trade_data=TradeCreate(offer_id=7, quantity=3, idempotency_key="idem-retry"),
                        context=make_context(owner_user),
                        edge_received_at=edge_received_at,
                    )

                self.assertIsNotNone(response)
                self.assertEqual(response.status_code, 200)
                forward_mock.assert_awaited_once()
                target_home_server, payload = forward_mock.await_args.args
                self.assertEqual(target_home_server, target_server)
                self.assertEqual(payload["source_server"], source_server)
                self.assertEqual(payload["source_surface"], "webapp")
                self.assertEqual(payload["offer_public_id"], "ofr_forward_7")
                self.assertEqual(payload["idempotency_key"], "idem-retry")
                self.assertEqual(payload["offer_id"], 7)
                self.assertEqual(payload["responder_user_id"], 5)


if __name__ == "__main__":
    unittest.main()
