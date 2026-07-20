import unittest

import models  # noqa: F401
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from models.telegram_interaction_anchor_state import TelegramInteractionAnchorState


class TelegramInteractionAnchorSchemaTests(unittest.TestCase):
    def test_table_has_generation_and_active_tuple_constraints(self):
        names = {
            constraint.name
            for constraint in TelegramInteractionAnchorState.__table__.constraints
            if constraint.name
        }

        self.assertIn(
            "ck_telegram_interaction_anchor_states_generation_order",
            names,
        )
        self.assertIn(
            "ck_telegram_interaction_anchor_states_active_tuple",
            names,
        )
        self.assertIn(
            "ck_telegram_interaction_anchor_active_outbox",
            names,
        )

    def test_outbox_links_are_unique_when_present(self):
        indexes = {
            index.name: index
            for index in TelegramInteractionAnchorState.__table__.indexes
        }

        self.assertTrue(
            indexes[
                "ux_telegram_interaction_anchor_states_desired_outbox"
            ].unique
        )
        self.assertTrue(
            indexes[
                "ux_telegram_interaction_anchor_states_active_outbox"
            ].unique
        )

    def test_anchor_state_is_foreign_local_no_sync(self):
        entry = get_sync_registry_entry("telegram_interaction_anchor_states")

        self.assertEqual(entry.policy, SyncPolicy.NO_SYNC)
        self.assertIn("foreign local", entry.authority)


if __name__ == "__main__":
    unittest.main()
