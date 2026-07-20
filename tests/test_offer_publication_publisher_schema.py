from pathlib import Path
import unittest

from models.offer_publication_state import OfferPublicationState


class OfferPublicationPublisherSchemaTests(unittest.TestCase):
    def test_model_contains_canonical_publisher_identity_constraint(self):
        columns = OfferPublicationState.__table__.columns
        self.assertIn("publisher_bot_identity", columns)
        self.assertEqual(columns.publisher_bot_identity.type.length, 32)
        self.assertTrue(columns.publisher_bot_identity.nullable)

        check_constraints = {
            constraint.name: str(constraint.sqltext)
            for constraint in OfferPublicationState.__table__.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        self.assertIn(
            "ck_offer_publication_states_publisher_bot_identity",
            check_constraints,
        )
        self.assertIn(
            "publisher_bot_identity = 'primary'",
            check_constraints[
                "ck_offer_publication_states_publisher_bot_identity"
            ],
        )
        self.assertIn(
            "surface = 'telegram_channel'",
            check_constraints[
                "ck_offer_publication_states_publisher_bot_identity"
            ],
        )

    def test_migration_is_additive_backfills_telegram_and_supports_downgrade(self):
        source = Path(
            "migrations/versions/"
            "f3d8e9a0b1ce_add_offer_publication_publisher.py"
        ).read_text(encoding="utf-8")

        self.assertIn('revision: str = "f3d8e9a0b1ce"', source)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "f2c7d8e9a0bd"',
            source,
        )
        self.assertIn('SET publisher_bot_identity = \'primary\'', source)
        self.assertIn("WHERE surface = 'telegram_channel'", source)
        self.assertIn("(surface = 'telegram_channel'", source)
        self.assertIn(
            '"ck_offer_publication_states_publisher_bot_identity"',
            source,
        )
        self.assertIn(
            'op.drop_column("offer_publication_states", "publisher_bot_identity")',
            source,
        )
        self.assertNotIn('drop_table("offer_publication_states")', source)


if __name__ == "__main__":
    unittest.main()
