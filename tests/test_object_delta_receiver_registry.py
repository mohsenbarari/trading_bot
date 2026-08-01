from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

from core.append_only_sync_delta_payload import OBJECT_DELTA_SYNC_TABLES
from core.object_delta_receiver_registry import (
    GLOBAL_RECEIVER_PRECONDITIONS,
    ObjectDeltaReceiverRegistryError,
    ReceiverApplyStatus,
    ReceiverIdentityKind,
    ReceiverPrerequisite,
    receiver_registry_fingerprint,
    receiver_table_spec,
    receiver_table_specs,
    validate_receiver_table_operation,
    validate_receiver_target_columns,
)


ROOT = Path(__file__).parents[1]
MODEL_BY_TABLE = {
    "accountant_relations": ("models/accountant_relation.py", "AccountantRelation"),
    "admin_broadcast_messages": ("models/admin_message.py", "AdminBroadcastMessage"),
    "admin_market_messages": ("models/admin_message.py", "AdminMarketMessage"),
    "commodities": ("models/commodity.py", "Commodity"),
    "commodity_aliases": ("models/commodity.py", "CommodityAlias"),
    "customer_relations": ("models/customer_relation.py", "CustomerRelation"),
    "invitations": ("models/invitation.py", "Invitation"),
    "market_runtime_state": ("models/market_runtime_state.py", "MarketRuntimeState"),
    "market_schedule_overrides": ("models/market_schedule_override.py", "MarketScheduleOverride"),
    "notifications": ("models/notification.py", "Notification"),
    "offer_publication_states": ("models/offer_publication_state.py", "OfferPublicationState"),
    "offer_requests": ("models/offer_request.py", "OfferRequest"),
    "offers": ("models/offer.py", "Offer"),
    "trades": ("models/trade.py", "Trade"),
    "trade_delivery_receipts": ("models/trade_delivery_receipt.py", "TradeDeliveryReceipt"),
    "telegram_link_tokens": ("models/telegram_link_token.py", "TelegramLinkToken"),
    "telegram_admin_broadcasts": ("models/telegram_admin_broadcast.py", "TelegramAdminBroadcast"),
    "telegram_admin_broadcast_receipts": (
        "models/telegram_admin_broadcast.py",
        "TelegramAdminBroadcastReceipt",
    ),
    "telegram_notification_outbox": (
        "models/telegram_notification_outbox.py",
        "TelegramNotificationOutbox",
    ),
    "trading_settings": ("models/trading_setting.py", "TradingSetting"),
    "user_blocks": ("models/user_block.py", "UserBlock"),
    "user_notification_preferences": (
        "models/user_notification_preference.py",
        "UserNotificationPreference",
    ),
    "users": ("models/user.py", "User"),
}


def _column_names(model_path: str, class_name: str) -> frozenset[str]:
    """Read ORM source structurally without importing models or a DB library."""

    tree = ast.parse((ROOT / model_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        columns: set[str] = set()
        for statement in node.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target = statement.targets[0]
            value = statement.value
            if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
                continue
            function_name = (
                value.func.id
                if isinstance(value.func, ast.Name)
                else value.func.attr
                if isinstance(value.func, ast.Attribute)
                else None
            )
            if function_name == "Column":
                columns.add(target.id)
        return frozenset(columns)
    raise AssertionError(f"model class {class_name} was not found in {model_path}")


class ObjectDeltaReceiverRegistryTests(unittest.TestCase):
    def test_registry_covers_exact_current_object_delta_tables(self) -> None:
        self.assertEqual(set(OBJECT_DELTA_SYNC_TABLES), set(receiver_table_specs()))
        self.assertEqual(set(OBJECT_DELTA_SYNC_TABLES), set(MODEL_BY_TABLE))

    def test_each_spec_matches_its_current_physical_model_columns(self) -> None:
        for table, spec in receiver_table_specs().items():
            with self.subTest(table=table):
                model_path, class_name = MODEL_BY_TABLE[table]
                self.assertEqual(_column_names(model_path, class_name), spec.allowed_columns)
                self.assertFalse(spec.wire_only_fields & spec.allowed_columns)

    def test_every_table_is_explicitly_unavailable_pending_baseline_cutpoint(self) -> None:
        for table, spec in receiver_table_specs().items():
            with self.subTest(table=table):
                self.assertEqual(ReceiverApplyStatus.UNAVAILABLE, spec.apply_status)
                self.assertFalse(spec.currently_applicable)
                self.assertTrue(GLOBAL_RECEIVER_PRECONDITIONS <= spec.required_preconditions)
                self.assertIn(
                    ReceiverPrerequisite.ATTESTED_BASELINE_CUTPOINT,
                    spec.required_preconditions,
                )
                self.assertTrue(
                    {
                        ReceiverPrerequisite.PORTABLE_IDENTITY_PROOF,
                        ReceiverPrerequisite.PRIMARY_KEY_ALIGNMENT_ATTESTATION,
                    }
                    & spec.required_preconditions
                )

    def test_source_primary_key_tables_require_explicit_alignment_attestation(self) -> None:
        source_key_tables = {
            table
            for table, spec in receiver_table_specs().items()
            if spec.identity_kind
            in {
                ReceiverIdentityKind.SOURCE_PRIMARY_KEY,
                ReceiverIdentityKind.SINGLETON_SOURCE_PRIMARY_KEY,
            }
        }
        self.assertEqual(
            {
                "admin_broadcast_messages",
                "admin_market_messages",
                "market_runtime_state",
                "telegram_admin_broadcasts",
            },
            source_key_tables,
        )
        for table in source_key_tables:
            self.assertIn(
                ReceiverPrerequisite.PRIMARY_KEY_ALIGNMENT_ATTESTATION,
                receiver_table_spec(table).required_preconditions,
            )

    def test_missing_and_unknown_tables_fail_closed(self) -> None:
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ObjectDeltaReceiverRegistryError, "missing"):
                    receiver_table_spec(value)
        for value in ("messages", "offers "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ObjectDeltaReceiverRegistryError, "unknown"):
                    receiver_table_spec(value)

    def test_operation_and_target_column_declarations_reject_unknown_values(self) -> None:
        self.assertEqual(
            "trading_settings",
            validate_receiver_table_operation("trading_settings", "UPDATE").table,
        )
        with self.assertRaisesRegex(ObjectDeltaReceiverRegistryError, "forbidden"):
            validate_receiver_table_operation("trading_settings", "DELETE")
        with self.assertRaisesRegex(ObjectDeltaReceiverRegistryError, "missing"):
            validate_receiver_table_operation("offers", None)

        projected = validate_receiver_target_columns("offers", {"id", "price", "status"})
        self.assertEqual(frozenset({"id", "price", "status"}), projected)
        with self.assertRaisesRegex(ObjectDeltaReceiverRegistryError, "unknown"):
            validate_receiver_target_columns("offers", {"id", "not_a_model_column"})
        with self.assertRaisesRegex(ObjectDeltaReceiverRegistryError, "duplicated"):
            validate_receiver_target_columns("offers", ["id", "id"])

    def test_reference_evidence_is_declared_without_claiming_resolution(self) -> None:
        commodity_alias = receiver_table_spec("commodity_aliases")
        commodity_reference = commodity_alias.references[0]
        self.assertEqual("commodity_id", commodity_reference.local_column)
        self.assertEqual("commodity_name", commodity_reference.canonical_wire_field)
        self.assertIn("commodity_name", commodity_alias.wire_only_fields)

        offer = receiver_table_spec("offers")
        self.assertIn("republished_offer_public_id", offer.wire_only_fields)
        self.assertFalse(offer.currently_applicable)

    def test_registry_mapping_is_immutable_and_fingerprint_is_stable(self) -> None:
        registry = receiver_table_specs()
        with self.assertRaises(TypeError):
            registry["unexpected"] = receiver_table_spec("users")
        first = receiver_registry_fingerprint()
        second = receiver_registry_fingerprint()
        self.assertEqual(first, second)
        self.assertRegex(first, re.compile(r"^[0-9a-f]{64}$"))

    def test_module_has_only_standard_library_imports(self) -> None:
        path = ROOT / "core/object_delta_receiver_registry.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed_modules = {
            "__future__",
            "collections.abc",
            "dataclasses",
            "enum",
            "hashlib",
            "json",
            "types",
            "typing",
        }
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.add(node.module or "")
        self.assertTrue(imported_modules <= allowed_modules, imported_modules - allowed_modules)


if __name__ == "__main__":
    unittest.main()
