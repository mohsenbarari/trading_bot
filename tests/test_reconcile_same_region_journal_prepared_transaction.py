from __future__ import annotations

import unittest

from scripts.reconcile_same_region_journal_prepared_transaction import (
    JournalRecoveryError,
    action_for_remote_state,
)


class SameRegionJournalReconciliationTests(unittest.TestCase):
    def test_committed_decision_requires_local_commit(self):
        self.assertEqual(action_for_remote_state("committed"), "commit")

    def test_prepared_or_rolled_back_requires_local_rollback(self):
        self.assertEqual(action_for_remote_state("prepared"), "rollback")
        self.assertEqual(action_for_remote_state("rolled_back"), "rollback")

    def test_unknown_state_is_refused(self):
        with self.assertRaises(JournalRecoveryError):
            action_for_remote_state("unknown")
