from __future__ import annotations

import unittest

from core.object_delta_mvp_scope import (
    INSERT,
    UPDATE,
    OBJECT_DELTA_MVP_DEFAULT_ENABLED,
    OBJECT_DELTA_MVP_ENABLES_RECEIVERS,
    OBJECT_DELTA_MVP_TABLE_SCOPES,
    OBJECT_DELTA_MVP_VALIDATES_RAW_CHANGELOG_PAYLOADS,
    ObjectDeltaMvpScopeError,
    SELECTION_REASON_DEFAULT_OFF,
    SELECTION_REASON_OPERATION_OUTSIDE_V1_SCOPE,
    SELECTION_REASON_SELECTED,
    USER_CANONICAL_IDENTITY_FIELDS,
    USER_CANONICAL_IDENTITY_KIND,
    object_delta_mvp_scope_for_table,
    select_object_delta_mvp_scope,
)


class ObjectDeltaMvpScopeTests(unittest.TestCase):
    def test_scope_contains_only_the_explicit_v1_table_operation_matrix(self):
        expected_operations = {
            "trading_settings": (INSERT, UPDATE),
            "market_schedule_overrides": (INSERT, UPDATE),
            "market_runtime_state": (INSERT, UPDATE),
            "users": (INSERT, UPDATE),
            "invitations": (INSERT, UPDATE),
            "accountant_relations": (INSERT, UPDATE),
            "customer_relations": (INSERT, UPDATE),
            "user_blocks": (INSERT,),
            "commodities": (INSERT, UPDATE),
            "commodity_aliases": (INSERT, UPDATE),
            "offers": (INSERT, UPDATE),
            "offer_requests": (INSERT, UPDATE),
            "trades": (INSERT, UPDATE),
        }

        self.assertEqual(set(expected_operations), set(OBJECT_DELTA_MVP_TABLE_SCOPES))
        for table, operations in expected_operations.items():
            self.assertEqual(
                operations,
                object_delta_mvp_scope_for_table(table).allowed_operations,
            )
            for operation in operations:
                decision = select_object_delta_mvp_scope(table, operation, enabled=True)
                self.assertTrue(decision.table_operation_in_scope)
                self.assertTrue(decision.selected)
                self.assertEqual(SELECTION_REASON_SELECTED, decision.reason)

    def test_allowed_change_is_default_off_until_the_caller_explicitly_opts_in(self):
        decision = select_object_delta_mvp_scope("commodities", INSERT)

        self.assertFalse(OBJECT_DELTA_MVP_DEFAULT_ENABLED)
        self.assertTrue(decision.table_operation_in_scope)
        self.assertFalse(decision.enabled)
        self.assertFalse(decision.selected)
        self.assertEqual(SELECTION_REASON_DEFAULT_OFF, decision.reason)
        self.assertEqual(("name",), decision.scope.canonical_identity_fields)

    def test_excluded_delete_and_user_block_update_have_explicit_negative_decisions(self):
        delete = select_object_delta_mvp_scope("trades", "DELETE", enabled=True)
        user_block_update = select_object_delta_mvp_scope("user_blocks", UPDATE, enabled=True)

        for decision in (delete, user_block_update):
            self.assertFalse(decision.table_operation_in_scope)
            self.assertFalse(decision.selected)
            self.assertEqual(SELECTION_REASON_OPERATION_OUTSIDE_V1_SCOPE, decision.reason)

    def test_user_blocks_requires_canonical_paired_user_identities(self):
        scope = object_delta_mvp_scope_for_table("user_blocks")

        self.assertTrue(scope.requires_canonical_paired_user_identities)
        self.assertEqual(
            ("blocker_user_identity", "blocked_user_identity"),
            scope.canonical_identity_fields,
        )
        self.assertEqual(
            ("blocker_user", "blocked_user"),
            tuple(reference.name for reference in scope.required_canonical_reference_prerequisites),
        )
        for reference in scope.required_canonical_reference_prerequisites:
            self.assertEqual("users", reference.target_table)
            self.assertEqual(USER_CANONICAL_IDENTITY_KIND, reference.target_identity_kind)
            self.assertEqual(USER_CANONICAL_IDENTITY_FIELDS, reference.target_identity_fields)

    def test_scopes_expose_canonical_identities_and_nonlocal_references(self):
        expected_identities = {
            "trading_settings": ("key",),
            "market_schedule_overrides": ("date",),
            "market_runtime_state": ("market_runtime_singleton",),
            "users": USER_CANONICAL_IDENTITY_FIELDS,
            "invitations": ("token",),
            "accountant_relations": ("invitation_token",),
            "customer_relations": ("invitation_token",),
            "user_blocks": ("blocker_user_identity", "blocked_user_identity"),
            "commodities": ("name",),
            "commodity_aliases": ("alias",),
            "offers": ("offer_public_id",),
            "offer_requests": ("request_home_server", "idempotency_key"),
            "trades": ("trade_number",),
        }
        self.assertEqual(set(expected_identities), set(OBJECT_DELTA_MVP_TABLE_SCOPES))
        for table, identity in expected_identities.items():
            scope = object_delta_mvp_scope_for_table(table)
            self.assertEqual(identity, scope.canonical_identity_fields)
            self.assertTrue(scope.canonical_identity_kind)

        alias_reference = object_delta_mvp_scope_for_table(
            "commodity_aliases"
        ).required_canonical_reference_prerequisites
        self.assertEqual(1, len(alias_reference))
        self.assertEqual("commodities", alias_reference[0].target_table)
        self.assertEqual(("name",), alias_reference[0].target_identity_fields)

        request_references = {
            reference.name: reference
            for reference in object_delta_mvp_scope_for_table(
                "offer_requests"
            ).canonical_reference_prerequisites
        }
        self.assertEqual("offers", request_references["offer"].target_table)
        self.assertEqual(("offer_public_id",), request_references["offer"].target_identity_fields)
        self.assertEqual("trades", request_references["resulting_trade"].target_table)
        self.assertEqual(("trade_number",), request_references["resulting_trade"].target_identity_fields)
        self.assertEqual(
            "customer_relations",
            request_references["customer_relation"].target_table,
        )
        self.assertEqual(
            ("invitation_token",),
            request_references["customer_relation"].target_identity_fields,
        )

    def test_malformed_or_unknown_table_operation_and_enablement_are_rejected(self):
        for table, operation in (
            (None, INSERT),
            (" users", INSERT),
            ("admin_market_messages", INSERT),
            ("chats", INSERT),
            ("telegram_notification_outbox", INSERT),
            ("notifications", INSERT),
            ("users", None),
            ("users", "update"),
            ("users", "UPSERT"),
        ):
            with self.subTest(table=table, operation=operation):
                with self.assertRaises(ObjectDeltaMvpScopeError):
                    select_object_delta_mvp_scope(table, operation)

        with self.assertRaises(ObjectDeltaMvpScopeError):
            select_object_delta_mvp_scope("users", INSERT, enabled=1)

    def test_selection_does_not_claim_raw_payload_safety_or_receiver_enablement(self):
        decision = select_object_delta_mvp_scope("offers", UPDATE, enabled=True)

        self.assertFalse(OBJECT_DELTA_MVP_VALIDATES_RAW_CHANGELOG_PAYLOADS)
        self.assertFalse(OBJECT_DELTA_MVP_ENABLES_RECEIVERS)
        self.assertFalse(decision.validates_raw_changelog_payloads)
        self.assertFalse(decision.enables_receiver)


if __name__ == "__main__":
    unittest.main()
