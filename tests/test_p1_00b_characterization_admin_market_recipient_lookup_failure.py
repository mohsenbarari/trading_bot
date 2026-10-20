"""Hermetic characterization test for market management message recipient-lookup failure.

Scope and limitations:
- Characterizes direct handler execution of
  `api.routers.admin_messages.create_market_message` for the single early-failure
  boundary where `list_market_management_recipient_user_ids` raises `RuntimeError`.
- Exercises the router function directly with explicit `AdminMarketMessageCreate`,
  `current_user`, and `db` stand-ins; it does not exercise FastAPI routing,
  `Depends(verify_super_admin)`, `Depends(get_db)`, or
  `require_shared_admin_write_authority`.
- Replaces router-level collaborator bindings via a single `ExitStack` and does not
  execute SQL queries, ORM model constructors, persistence transactions, cache
  backends, event publication, or notification delivery.
- Canonical localhost Redis/network isolation is installed before importing
  application modules.
"""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

import api.routers.admin_messages as admin_messages


class AdminMarketRecipientLookupFailureTests(unittest.IsolatedAsyncioTestCase):
    """Characterize recipient-lookup failure ordering in `create_market_message`."""

    async def test_recipient_lookup_exception_precedes_creation_and_all_downstream(self) -> None:
        """A recipient-lookup RuntimeError propagates identically and suppresses creation and all downstreams."""
        original_handler = admin_messages.create_market_message
        patched_names = (
            "list_market_management_recipient_user_ids",
            "create_market_management_message",
            "set_cached_admin_market_current",
            "_notify_users",
            "create_user_notification",
            "_serialize_market_message",
            "publish_event_sync",
        )
        originals = {name: getattr(admin_messages, name) for name in patched_names}

        lookup_error = RuntimeError("market recipient lookup failed")
        lookup_mock = AsyncMock(side_effect=lookup_error)
        create_mock = AsyncMock(
            side_effect=AssertionError("create_market_management_message must not be called")
        )
        cache_mock = AsyncMock(
            side_effect=AssertionError("set_cached_admin_market_current must not be called")
        )
        notify_mock = AsyncMock(
            side_effect=AssertionError("_notify_users must not be called")
        )
        create_notification_mock = AsyncMock(
            side_effect=AssertionError("create_user_notification must not be called")
        )
        serialize_mock = Mock(
            side_effect=AssertionError("_serialize_market_message must not be called")
        )
        publish_mock = Mock(
            side_effect=AssertionError("publish_event_sync must not be called")
        )
        replacements = {
            "list_market_management_recipient_user_ids": lookup_mock,
            "create_market_management_message": create_mock,
            "set_cached_admin_market_current": cache_mock,
            "_notify_users": notify_mock,
            "create_user_notification": create_notification_mock,
            "_serialize_market_message": serialize_mock,
            "publish_event_sync": publish_mock,
        }

        db_async_methods = {
            "execute": AsyncMock(side_effect=AssertionError("db.execute must not be called")),
            "get": AsyncMock(side_effect=AssertionError("db.get must not be called")),
            "commit": AsyncMock(side_effect=AssertionError("db.commit must not be called")),
            "refresh": AsyncMock(side_effect=AssertionError("db.refresh must not be called")),
            "rollback": AsyncMock(side_effect=AssertionError("db.rollback must not be called")),
        }
        db_add_mock = Mock(side_effect=AssertionError("db.add must not be called"))
        db = SimpleNamespace(add=db_add_mock, **db_async_methods)
        actor = SimpleNamespace(id=701)
        data = admin_messages.AdminMarketMessageCreate(
            content="Market announcement paused on lookup error",
            reused_from_id=19,
        )

        with ExitStack() as stack:
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(admin_messages, name, replacement))

            self.assertIs(admin_messages.create_market_message, original_handler)
            for name, replacement in replacements.items():
                current_attr = getattr(admin_messages, name)
                self.assertIs(current_attr, replacement)
                self.assertIsNot(current_attr, originals[name])

            with self.assertRaises(RuntimeError) as raised_ctx:
                await admin_messages.create_market_message(
                    data=data,
                    current_user=actor,
                    db=db,
                )

            self.assertIs(raised_ctx.exception, lookup_error)
            self.assertEqual(lookup_mock.call_count, 1)
            self.assertEqual(lookup_mock.await_count, 1)
            lookup_mock.assert_called_once_with(db, exclude_user_ids=[actor.id])
            lookup_mock.assert_awaited_once_with(db, exclude_user_ids=[actor.id])
            self.assertEqual(
                lookup_mock.call_args_list,
                [call(db, exclude_user_ids=[701])],
            )
            self.assertIs(lookup_mock.call_args.args[0], db)
            self.assertEqual(set(lookup_mock.call_args.kwargs.keys()), {"exclude_user_ids"})

            for async_boundary in (
                create_mock,
                cache_mock,
                notify_mock,
                create_notification_mock,
            ):
                async_boundary.assert_not_called()
                async_boundary.assert_not_awaited()
                self.assertEqual(async_boundary.call_count, 0)
                self.assertEqual(async_boundary.await_count, 0)

            for sync_boundary in (serialize_mock, publish_mock):
                sync_boundary.assert_not_called()
                self.assertEqual(sync_boundary.call_count, 0)

            db_add_mock.assert_not_called()
            self.assertEqual(db_add_mock.call_count, 0)
            for db_async_mock in db_async_methods.values():
                db_async_mock.assert_not_called()
                db_async_mock.assert_not_awaited()
                self.assertEqual(db_async_mock.call_count, 0)
                self.assertEqual(db_async_mock.await_count, 0)

        self.assertIs(admin_messages.create_market_message, original_handler)
        for name, original_attr in originals.items():
            self.assertIs(getattr(admin_messages, name), original_attr)


if __name__ == "__main__":
    unittest.main()
