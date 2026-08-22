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
            "publisher_bot_identity IN ('primary', 'publisher_1'",
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
        self.assertLess(
            source.index(
                'op.create_check_constraint(\n'
                '        "ck_offer_publication_states_publisher_bot_identity"'
            ),
            source.index("UPDATE offer_publication_states"),
            "the check constraint must be installed before the trigger-producing "
            "backfill so PostgreSQL does not reject a later ALTER TABLE",
        )

    def test_sync_promotion_migration_keeps_owner_immutable_except_for_bound_placeholder(self):
        source = Path(
            "migrations/versions/"
            "fe4f5a6b7c8d_guard_sync_publication_publisher_promotion.py"
        ).read_text(encoding="utf-8")

        self.assertIn('revision: str = "fe4f5a6b7c8d"', source)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "fd3e4f5a6b7c"',
            source,
        )
        self.assertIn("CREATE OR REPLACE FUNCTION enforce_offer_publication_telegram_owner_immutable", source)
        self.assertIn("OLD.publisher_bot_identity = 'primary'", source)
        self.assertIn("OLD.publication_owner_server = 'foreign'", source)
        self.assertIn("OLD.status = 'pending'", source)
        self.assertIn("OLD.version_id = 1", source)
        self.assertIn("OLD.telegram_message_id IS NULL", source)
        self.assertGreaterEqual(source.count("NEW.telegram_message_id IS NULL"), 2)
        self.assertIn("IF TG_OP = 'INSERT'", source)
        self.assertIn("NEW.dedupe_key IS NOT DISTINCT FROM OLD.dedupe_key", source)
        self.assertIn("NEW.offer_public_id IS NOT DISTINCT FROM OLD.offer_public_id", source)
        self.assertIn("'offer-publication:telegram_channel:' || NEW.offer_public_id", source)
        self.assertIn("NEW.version_id > OLD.version_id", source)
        self.assertIn("trading_bot.sync_publication_publisher_promotion", source)
        self.assertIn("COALESCE(", source)
        self.assertIn("= NEW.dedupe_key", source)
        self.assertIn("PERFORM set_config(", source)
        self.assertGreaterEqual(
            source.count("RAISE EXCEPTION 'Telegram publication owner is immutable'"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
