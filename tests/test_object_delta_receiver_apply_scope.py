from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import Column, Integer, MetaData, Table, insert

from core import db
from core.object_delta_receiver_apply_scope import (
    OBJECT_DELTA_RECEIVER_APPLY_EXECUTION_OPTION,
    OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY,
    AuthorizedObjectDeltaReceiverDelivery,
    ObjectDeltaReceiverApplyScopeError,
    authorized_object_delta_receiver_apply_scope,
    execution_is_authorized_for_object_delta_receiver_apply,
    session_is_authorized_for_object_delta_receiver_apply,
)


class _Session:
    def __init__(self, *, active_transaction: bool = False) -> None:
        self.info: dict[object, object] = {}
        self.active_transaction = active_transaction
        self.connection = AsyncMock(return_value=SimpleNamespace())

    def in_transaction(self) -> bool:
        return self.active_transaction


def _authority() -> AuthorizedObjectDeltaReceiverDelivery:
    # Scope mechanics do not inspect packet contents; those were proven by the
    # separate pure authorization constructor before the scope is entered.
    return AuthorizedObjectDeltaReceiverDelivery(
        binding=object(),
        verified_packet=object(),
        batch=object(),
        transport_binding=object(),
        source_attestation=object(),
    )


class ObjectDeltaReceiverApplyScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_marks_only_one_fresh_session_and_matching_connection_option(self):
        session = _Session()
        marker = None
        authority = _authority()

        with patch(
            "core.object_delta_receiver_apply_scope.validate_authorized_object_delta_receiver_delivery",
            return_value=authority,
        ):
            async with authorized_object_delta_receiver_apply_scope(
                session,
                authorization=authority,
            ):
                self.assertTrue(session_is_authorized_for_object_delta_receiver_apply(session))
                marker = session.info[OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY]
                self.assertTrue(
                    execution_is_authorized_for_object_delta_receiver_apply(
                        SimpleNamespace(
                            execution_options={
                                OBJECT_DELTA_RECEIVER_APPLY_EXECUTION_OPTION: marker
                            }
                        )
                    )
                )
                self.assertFalse(
                    execution_is_authorized_for_object_delta_receiver_apply(
                        SimpleNamespace(execution_options={})
                    )
                )

        self.assertIsNotNone(marker)
        self.assertNotIn(OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY, session.info)
        self.assertFalse(session_is_authorized_for_object_delta_receiver_apply(session))
        session.connection.assert_awaited_once()

    async def test_scope_refuses_existing_or_nested_transactions(self):
        authority = _authority()
        with patch(
            "core.object_delta_receiver_apply_scope.validate_authorized_object_delta_receiver_delivery",
            return_value=authority,
        ):
            with self.assertRaisesRegex(ObjectDeltaReceiverApplyScopeError, "fresh session"):
                async with authorized_object_delta_receiver_apply_scope(
                    _Session(active_transaction=True), authorization=authority
                ):
                    pass

            session = _Session()
            async with authorized_object_delta_receiver_apply_scope(session, authorization=authority):
                with self.assertRaisesRegex(ObjectDeltaReceiverApplyScopeError, "cannot nest"):
                    async with authorized_object_delta_receiver_apply_scope(
                        _Session(), authorization=authority
                    ):
                        pass

    async def test_scope_allows_only_its_matching_session_and_connection_through_writer_guards(self):
        session = _Session()
        table = Table("object_delta_scope_guard", MetaData(), Column("id", Integer))
        authority = _authority()

        with patch(
            "core.object_delta_receiver_apply_scope.validate_authorized_object_delta_receiver_delivery",
            return_value=authority,
        ):
            async with authorized_object_delta_receiver_apply_scope(session, authorization=authority):
                marker = session.info[OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY]
                context = SimpleNamespace(
                    execution_options={OBJECT_DELTA_RECEIVER_APPLY_EXECUTION_OPTION: marker}
                )
                with patch("core.db.require_application_writer_term") as require_term:
                    db._enforce_application_writer_term_for_core_dml(
                        SimpleNamespace(session=session, statement=insert(table).values(id=1))
                    )
                    with patch.object(db.settings, "application_writer_term_enforced", True):
                        db._enforce_application_writer_term_before_cursor_execute(
                            None,
                            None,
                            "UPDATE object_delta_scope_guard SET id = 2",
                            None,
                            context,
                            False,
                        )
                require_term.assert_not_called()

    async def test_scope_rejects_a_directly_constructed_authority_before_opening_connection(self):
        session = _Session()
        with self.assertRaisesRegex(ObjectDeltaReceiverApplyScopeError, "was not authorized"):
            async with authorized_object_delta_receiver_apply_scope(
                session,
                authorization=_authority(),
            ):
                pass
        session.connection.assert_not_awaited()


class ObjectDeltaReceiverApplyScopeDbGuardTests(unittest.TestCase):
    def test_session_info_without_active_scope_cannot_bypass_writer_term(self):
        session = SimpleNamespace(info={OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY: object()})
        table = Table("object_delta_scope_guard", MetaData(), Column("id", Integer))
        with patch("core.db.require_application_writer_term") as require_term:
            db._enforce_application_writer_term_for_core_dml(
                SimpleNamespace(session=session, statement=insert(table).values(id=1))
            )
        require_term.assert_called_once_with()

    def test_engine_guard_requires_matching_active_context_marker(self):
        context = SimpleNamespace(
            execution_options={OBJECT_DELTA_RECEIVER_APPLY_EXECUTION_OPTION: object()}
        )
        with patch.object(db.settings, "application_writer_term_enforced", True), patch(
            "core.db.require_application_writer_term"
        ) as require_term:
            db._enforce_application_writer_term_before_cursor_execute(
                None,
                None,
                "UPDATE object_delta_scope_guard SET id = 2",
                None,
                context,
                False,
            )
        require_term.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
