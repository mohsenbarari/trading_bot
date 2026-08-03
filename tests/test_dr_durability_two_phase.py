from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine

from core.dr_durability_two_phase import (
    DurabilityCoordinatedSession,
    DurabilityJournalInDoubtError,
    _IN_DOUBT_KEY,
    _JOURNAL_TRANSACTION_KEY,
)
from core.dr_durability_journal_client import (
    DurabilityJournalClientError,
    PreparedJournalTransaction,
)


class _Prepare:
    local_transaction_gid = "_sa_1234567890abcdef1234567890abcdef"


def _prepared() -> PreparedJournalTransaction:
    return PreparedJournalTransaction(prepare=_Prepare(), key=object())


class _Prepared:
    class _Prepare:
        local_transaction_gid = "_sa_1234567890abcdef1234567890abcdef"

    prepare = _Prepare()


class DurabilityTwoPhaseSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.session = DurabilityCoordinatedSession(bind=self.engine)
        self.session.twophase = True

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_remote_commit_is_between_local_prepare_and_commit_prepared(self):
        journal = _prepared()
        self.session.info[_JOURNAL_TRANSACTION_KEY] = journal
        calls: list[str] = []

        def prepared():
            calls.append("local_prepare")

        def remote_commit(_journal, *, prepared_transaction_gid):
            self.assertEqual(prepared_transaction_gid, journal.prepare.local_transaction_gid)
            calls.append("remote_commit")

        def local_commit(*_args):
            calls.append("local_commit_prepared")

        with patch.object(self.session, "prepare", side_effect=prepared), patch(
            "core.dr_durability_two_phase.commit_prepared_journal", side_effect=remote_commit
        ), patch("sqlalchemy.orm.Session.commit", side_effect=local_commit):
            self.session.commit()

        self.assertEqual(calls, ["local_prepare", "remote_commit", "local_commit_prepared"])

    def test_unverified_remote_commit_preserves_local_prepared_transaction(self):
        journal = _prepared()
        self.session.info[_JOURNAL_TRANSACTION_KEY] = journal
        self.session.begin()

        with patch.object(self.session, "prepare"), patch(
            "core.dr_durability_two_phase.commit_prepared_journal",
            side_effect=DurabilityJournalClientError("transport interrupted"),
        ):
            with self.assertRaises(DurabilityJournalInDoubtError):
                self.session.commit()

        self.assertIs(self.session.info[_IN_DOUBT_KEY], journal)

    def test_in_doubt_rollback_closes_without_issuing_local_rollback(self):
        self.session.info[_IN_DOUBT_KEY] = _Prepared()
        with patch("sqlalchemy.orm.Session.close") as close, patch(
            "sqlalchemy.orm.Session.rollback"
        ) as rollback:
            self.session.rollback()
        close.assert_called_once_with()
        rollback.assert_not_called()

    def test_preservation_deassociates_the_connection_before_cleanup(self):
        self.session.twophase = False
        connection = self.session.connection()
        journal = _prepared()
        self.session._preserve_prepared_transaction(journal)

        self.assertIsNone(connection.get_transaction())
        self.assertFalse(connection.closed)
