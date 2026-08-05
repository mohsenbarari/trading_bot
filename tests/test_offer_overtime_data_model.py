"""Stage 1 of the offer overtime feature: storage exists and stays inert.

These checks run without a database. They cover the two things that can silently
go wrong at this stage: the migration drifting away from the models, and the new
storage changing behavior before the feature is built. Actually executing the
migration needs Postgres and is covered by the database-backed suites.
"""

import ast
import os
import unittest

from core.registration_sync_policy import (
    USER_SYNC_FOREIGN_FIELDS,
    USER_SYNC_IDENTITY_FIELDS,
)
from core.offer_request_identity import (
    generate_offer_request_public_id,
    is_offer_request_public_id_shape,
)
from core.sync_parity import LOCAL_ONLY_FIELDS_BY_TABLE
from models.offer import Offer
from models.offer_request import (
    OVERTIME_NONTERMINAL_STATUSES,
    OVERTIME_OWNER_OCCUPYING_STATUSES,
    OfferRequest,
    OfferRequestStatus,
    OfferRequestWorkflow,
)
from models.user import User


MIGRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "migrations",
    "versions",
    "b5d1c7e93f04_add_offer_overtime_data_model.py",
)

NEW_REQUEST_COLUMNS = (
    "request_public_id",
    "workflow_kind",
    "offer_owner_user_id",
    "queue_sequence",
    "presented_at",
    "decision_deadline_at",
    "decided_by_user_id",
    "terminal_reason",
    "telegram_delivery_job_id",
    "telegram_message_id",
)

NEW_OVERTIME_INDEXES = (
    "ux_offer_requests_overtime_active_per_offer",
    "ux_offer_requests_overtime_owner_occupied",
    "ix_offer_requests_overtime_queue_order",
    "ix_offer_requests_overtime_open_by_requester",
)


def _migration_source() -> str:
    with open(MIGRATION_PATH, encoding="utf-8") as handle:
        return handle.read()


class OvertimeStorageIsInertTests(unittest.TestCase):
    """Nothing added in this stage may change behavior for an existing user."""

    def test_user_preference_defaults_to_disabled(self):
        column = User.__table__.columns["offer_overtime_minutes"]
        self.assertFalse(column.nullable)
        self.assertEqual(column.default.arg, 0)
        self.assertIn("0", str(column.server_default.arg))

    def test_offer_snapshot_and_marker_default_to_disabled(self):
        snapshot = Offer.__table__.columns["overtime_minutes_snapshot"]
        marker = Offer.__table__.columns["overtime_trade_committed"]
        self.assertFalse(snapshot.nullable)
        self.assertEqual(snapshot.default.arg, 0)
        self.assertFalse(marker.nullable)
        self.assertIs(marker.default.arg, False)

    def test_request_workflow_defaults_to_the_existing_direct_path(self):
        column = OfferRequest.__table__.columns["workflow_kind"]
        self.assertFalse(column.nullable)
        self.assertIs(column.default.arg, OfferRequestWorkflow.DIRECT)
        self.assertEqual(column.server_default.arg, "direct")

    def test_every_new_request_column_is_optional(self):
        """A row written by code that predates this stage must still be valid."""
        for name in NEW_REQUEST_COLUMNS:
            if name == "workflow_kind":
                continue  # covered above: not nullable, but server-defaulted
            with self.subTest(column=name):
                self.assertTrue(OfferRequest.__table__.columns[name].nullable)


class OvertimeStateGroupTests(unittest.TestCase):
    def test_nonterminal_states_are_the_ones_that_hold_an_offer(self):
        self.assertEqual(
            {status.value for status in OVERTIME_NONTERMINAL_STATUSES},
            {"overtime_queued", "overtime_delivering", "overtime_presented"},
        )

    def test_owner_occupying_states_exclude_the_queue(self):
        """A queued request holds its offer but is not in front of the owner."""
        self.assertEqual(
            {status.value for status in OVERTIME_OWNER_OCCUPYING_STATUSES},
            {"overtime_delivering", "overtime_presented"},
        )
        self.assertNotIn(
            OfferRequestStatus.OVERTIME_QUEUED, OVERTIME_OWNER_OCCUPYING_STATUSES
        )

    def test_owner_occupying_states_are_a_subset_of_nonterminal(self):
        self.assertTrue(
            set(OVERTIME_OWNER_OCCUPYING_STATUSES).issubset(OVERTIME_NONTERMINAL_STATUSES)
        )

    def test_success_reuses_the_existing_completed_trade_status(self):
        overtime_values = {
            status.value
            for status in OfferRequestStatus
            if status.value.startswith("overtime_")
        }
        self.assertNotIn("overtime_completed_trade", overtime_values)
        self.assertIn("completed_trade", {s.value for s in OfferRequestStatus})


class OvertimeIndexTests(unittest.TestCase):
    def setUp(self):
        self.indexes = {index.name: index for index in OfferRequest.__table__.indexes}

    def test_all_overtime_indexes_exist(self):
        for name in NEW_OVERTIME_INDEXES:
            self.assertIn(name, self.indexes)

    def test_one_live_request_per_offer_and_one_prompt_per_owner_are_unique(self):
        self.assertTrue(self.indexes["ux_offer_requests_overtime_active_per_offer"].unique)
        self.assertTrue(self.indexes["ux_offer_requests_overtime_owner_occupied"].unique)

    def test_uniqueness_is_scoped_to_the_offer_home_server(self):
        for name in (
            "ux_offer_requests_overtime_active_per_offer",
            "ux_offer_requests_overtime_owner_occupied",
        ):
            with self.subTest(index=name):
                columns = [column.name for column in self.indexes[name].columns]
                self.assertEqual(columns[0], "request_home_server")

    def test_predicates_compare_the_enum_as_text(self):
        """Binding to the enum OID would break on a later type rebuild."""
        for name in NEW_OVERTIME_INDEXES:
            with self.subTest(index=name):
                where = str(self.indexes[name].dialect_options["postgresql"]["where"])
                self.assertIn("result_status::text", where)

    def test_request_public_id_is_unique(self):
        self.assertTrue(OfferRequest.__table__.columns["request_public_id"].unique)


class OvertimeSyncWiringTests(unittest.TestCase):
    def test_preference_is_iran_authoritative_and_never_foreign_writable(self):
        self.assertIn("offer_overtime_minutes", USER_SYNC_IDENTITY_FIELDS)
        self.assertNotIn("offer_overtime_minutes", USER_SYNC_FOREIGN_FIELDS)

    def test_delivery_job_reference_is_excluded_from_parity(self):
        """The two peers hold different local delivery rows by design."""
        self.assertIn(
            "telegram_delivery_job_id", LOCAL_ONLY_FIELDS_BY_TABLE["offer_requests"]
        )


class OfferRequestPublicIdentityTests(unittest.TestCase):
    def test_generated_identifier_is_opaque_and_recognizable(self):
        value = generate_offer_request_public_id()
        self.assertTrue(value.startswith("req_"))
        self.assertTrue(is_offer_request_public_id_shape(value))
        self.assertNotEqual(value, generate_offer_request_public_id())

    def test_shape_check_rejects_bare_numbers_and_empty_values(self):
        for value in (None, "", "42", "req_", "ofr_abcdefghij"):
            with self.subTest(value=value):
                self.assertFalse(is_offer_request_public_id_shape(value))


class MigrationMatchesModelsTests(unittest.TestCase):
    """Guard against the migration and the models drifting apart."""

    def setUp(self):
        self.source = _migration_source()
        self.tree = ast.parse(self.source)

    def _function_source(self, name: str) -> str:
        node = next(
            item
            for item in self.tree.body
            if isinstance(item, ast.FunctionDef) and item.name == name
        )
        return ast.get_source_segment(self.source, node) or ""

    def test_revision_follows_the_recorded_baseline_head(self):
        self.assertIn('revision: str = "b5d1c7e93f04"', self.source)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "a274f5a6b8c9"', self.source)

    def test_upgrade_adds_every_new_model_column(self):
        upgrade = self._function_source("upgrade")
        for name in NEW_REQUEST_COLUMNS:
            with self.subTest(column=name):
                self.assertIn(f'"{name}"', upgrade)
        for name in ("offer_overtime_minutes", "overtime_minutes_snapshot", "overtime_trade_committed"):
            with self.subTest(column=name):
                self.assertIn(f'"{name}"', upgrade)

    def test_downgrade_removes_every_column_upgrade_added(self):
        downgrade = self._function_source("downgrade")
        for name in NEW_REQUEST_COLUMNS + (
            "offer_overtime_minutes",
            "overtime_minutes_snapshot",
            "overtime_trade_committed",
        ):
            with self.subTest(column=name):
                self.assertIn(f'"{name}"', downgrade)

    def test_downgrade_removes_every_index_upgrade_added(self):
        downgrade = self._function_source("downgrade")
        for name in NEW_OVERTIME_INDEXES + ("ix_offer_requests_request_public_id",):
            with self.subTest(index=name):
                self.assertIn(name, downgrade)

    def _module_constant(self, name: str):
        node = next(
            item
            for item in self.tree.body
            if isinstance(item, ast.Assign)
            and any(getattr(target, "id", None) == name for target in item.targets)
        )
        return ast.literal_eval(node.value)

    def test_migration_adds_exactly_the_model_overtime_statuses(self):
        self.assertEqual(
            set(self._module_constant("_NEW_REQUEST_STATUSES")),
            {
                status.value
                for status in OfferRequestStatus
                if status.value.startswith("overtime_")
            },
        )

    def test_migration_index_predicates_match_the_model_state_groups(self):
        self.assertEqual(
            set(self._module_constant("_NONTERMINAL")),
            {status.value for status in OVERTIME_NONTERMINAL_STATUSES},
        )
        self.assertEqual(
            set(self._module_constant("_OWNER_OCCUPYING")),
            {status.value for status in OVERTIME_OWNER_OCCUPYING_STATUSES},
        )

    def test_enum_values_are_added_with_the_safe_repository_pattern(self):
        upgrade = self._function_source("upgrade")
        self.assertIn("autocommit_block", upgrade)
        self.assertIn("ADD VALUE IF NOT EXISTS", upgrade)
        self.assertIn("_NEW_REQUEST_STATUSES", upgrade)

    def test_downgrade_refuses_to_discard_live_overtime_evidence(self):
        downgrade = self._function_source("downgrade")
        self.assertIn("RAISE EXCEPTION", downgrade)
        self.assertIn("offer_requests", downgrade)

    def test_downgrade_keeps_enum_labels_for_a_reversible_code_rollback(self):
        downgrade = self._function_source("downgrade")
        self.assertNotIn("DROP TYPE IF EXISTS offerrequeststatus", downgrade)
        self.assertIn("DROP TYPE IF EXISTS offerrequestworkflow", downgrade)


if __name__ == "__main__":
    unittest.main()
