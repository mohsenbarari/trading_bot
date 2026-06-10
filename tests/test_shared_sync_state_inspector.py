import unittest

from scripts.inspect_shared_sync_state import SIGNAL_QUERIES, classify_counts


def empty_signal_counts() -> dict[str, int]:
    return {key: 0 for key in SIGNAL_QUERIES}


class SharedSyncStateInspectorTests(unittest.TestCase):
    def test_empty_signal_tables_are_fresh(self):
        payload = classify_counts(empty_signal_counts())

        self.assertEqual(payload["classification"], "fresh")
        self.assertTrue(payload["is_fresh"])
        self.assertEqual(payload["signal_total"], 0)
        self.assertEqual(payload["blocking_signals"], {})

    def test_users_block_fresh_classification(self):
        counts = empty_signal_counts()
        counts["users"] = 1

        payload = classify_counts(counts)

        self.assertEqual(payload["classification"], "existing")
        self.assertFalse(payload["is_fresh"])
        self.assertEqual(payload["signal_total"], 1)
        self.assertEqual(payload["blocking_signals"], {"users": 1})

    def test_non_system_chats_block_fresh_classification(self):
        counts = empty_signal_counts()
        counts["non_system_non_mandatory_chats"] = 2
        counts["non_system_non_mandatory_chat_members"] = 5

        payload = classify_counts(counts)

        self.assertEqual(payload["classification"], "existing")
        self.assertEqual(payload["signal_total"], 7)
        self.assertEqual(
            payload["blocking_signals"],
            {
                "non_system_non_mandatory_chats": 2,
                "non_system_non_mandatory_chat_members": 5,
            },
        )


if __name__ == "__main__":
    unittest.main()
