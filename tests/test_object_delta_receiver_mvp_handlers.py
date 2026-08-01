from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from core.object_delta_mvp_canonical import INSERT, UPDATE, validate_canonical_mvp_object_delta
from core.object_delta_receiver_mvp_handlers import (
    COMMODITIES_NATURAL_KEY,
    COMMODITIES_TABLE,
    COMMODITY_ENSURE_CONFLICT_POLICY,
    OBJECT_DELTA_RECEIVER_MVP_HANDLERS_DEFAULT_ENABLED,
    OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY,
    SUPPORTED_OBJECT_DELTA_MVP_RECEIVER_TABLES,
    CommodityEnsureIntent,
    ObjectDeltaReceiverMvpPlannedChange,
    ObjectDeltaReceiverMvpHandlerError,
    compile_object_delta_mvp_receiver_planned_change,
    compile_object_delta_mvp_receiver_intent,
    object_delta_receiver_mvp_execution_registry_fingerprint,
    require_object_delta_mvp_receiver_planned_change,
)


def commodity_descriptor(*, operation: str = INSERT):
    return validate_canonical_mvp_object_delta(
        {
            "table": COMMODITIES_TABLE,
            "operation": operation,
            "identity": {COMMODITIES_NATURAL_KEY: "Imami coin"},
            "fields": {},
            "references": {},
        }
    )


class ObjectDeltaReceiverMvpHandlerTests(unittest.TestCase):
    def test_commodities_insert_compiles_to_an_id_free_ensure_intent(self):
        self.assertFalse(OBJECT_DELTA_RECEIVER_MVP_HANDLERS_DEFAULT_ENABLED)
        intent = compile_object_delta_mvp_receiver_intent(commodity_descriptor())
        self.assertIsInstance(intent, CommodityEnsureIntent)
        self.assertEqual(COMMODITIES_TABLE, intent.table)
        self.assertEqual(INSERT, intent.operation)
        self.assertEqual("Imami coin", intent.name)
        self.assertEqual(COMMODITY_ENSURE_CONFLICT_POLICY, intent.conflict_policy)
        self.assertFalse(intent.default_enabled)
        self.assertFalse(intent.enables_receiver)
        self.assertNotIn("id", intent.__dict__)

        with self.assertRaisesRegex(ObjectDeltaReceiverMvpHandlerError, "operation"):
            compile_object_delta_mvp_receiver_intent(commodity_descriptor(operation=UPDATE))

    def test_only_commodities_has_a_handler_and_every_other_canonical_table_fails_closed(self):
        self.assertEqual({COMMODITIES_TABLE}, SUPPORTED_OBJECT_DELTA_MVP_RECEIVER_TABLES)
        for table, identity, fields, references in (
            ("trading_settings", {"key": "minimum"}, {"value": "1"}, {}),
            ("market_schedule_overrides", {"date": "2026-08-01"}, {
                "override_type": "closed_all_day",
                "open_time_local": None,
                "close_time_local": None,
                "note": None,
            }, {}),
            ("market_runtime_state", {"market_runtime_singleton": "market_runtime_state"}, {
                "is_open": True,
                "active_web_notice_visible": False,
                "offers_since_last_open": 0,
                "last_transition_at": None,
            }, {}),
            ("commodity_aliases", {"alias": "Imami"}, {}, {"commodity_name": "Imami coin"}),
        ):
            with self.subTest(table=table):
                descriptor = validate_canonical_mvp_object_delta(
                    {
                        "table": table,
                        "operation": INSERT,
                        "identity": identity,
                        "fields": fields,
                        "references": references,
                    }
                )
                with self.assertRaisesRegex(ObjectDeltaReceiverMvpHandlerError, "no explicit handler"):
                    compile_object_delta_mvp_receiver_intent(descriptor)

    def test_noncanonical_or_tampered_descriptors_fail_closed(self):
        with self.assertRaisesRegex(ObjectDeltaReceiverMvpHandlerError, "invalid"):
            compile_object_delta_mvp_receiver_intent({"table": COMMODITIES_TABLE})
        forged = replace(commodity_descriptor(), fields={"id": 1})
        with self.assertRaisesRegex(ObjectDeltaReceiverMvpHandlerError, "invalid"):
            compile_object_delta_mvp_receiver_intent(forged)
        with self.assertRaisesRegex(ObjectDeltaReceiverMvpHandlerError, "cannot enable"):
            CommodityEnsureIntent(
                table=COMMODITIES_TABLE,
                operation=INSERT,
                name="Imami coin",
                enables_receiver=True,
            )

    def test_execution_registry_is_immutable_and_planned_changes_are_opaque_handler_capabilities(self):
        self.assertEqual(
            {("commodities", "INSERT")},
            set(OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY),
        )
        with self.assertRaises(TypeError):
            OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY[("users", "UPDATE")] = "generic"
        first_fingerprint = object_delta_receiver_mvp_execution_registry_fingerprint()
        self.assertEqual(first_fingerprint, object_delta_receiver_mvp_execution_registry_fingerprint())
        self.assertRegex(first_fingerprint, r"^[0-9a-f]{64}$")

        change = compile_object_delta_mvp_receiver_planned_change(
            logical_sequence=7,
            change_log_id=41,
            descriptor=commodity_descriptor(),
        )
        self.assertIs(change, require_object_delta_mvp_receiver_planned_change(change))
        self.assertEqual("Imami coin", change.intent.name)
        self.assertEqual(first_fingerprint, change.execution_registry_fingerprint)
        self.assertFalse(hasattr(change, "sync_item"))

        direct = ObjectDeltaReceiverMvpPlannedChange(
            logical_sequence=7,
            change_log_id=41,
            execution_registry_fingerprint=first_fingerprint,
            intent=change.intent,
        )
        for forged in (direct, replace(change)):
            with self.subTest(forged=forged):
                with self.assertRaisesRegex(ObjectDeltaReceiverMvpHandlerError, "not authorized"):
                    require_object_delta_mvp_receiver_planned_change(forged)


class ObjectDeltaReceiverMvpHandlerStaticTests(unittest.TestCase):
    def test_module_is_pure_and_does_not_import_database_or_legacy_sync_paths(self):
        source = (
            Path(__file__).parents[1] / "core/object_delta_receiver_mvp_handlers.py"
        ).read_text(encoding="utf-8")
        for prohibited_import in (
            "import sqlalchemy",
            "from sqlalchemy",
            "from models",
            "api.routers.sync",
            "core.sync_worker",
            "import requests",
            "import httpx",
            "import redis",
        ):
            self.assertNotIn(prohibited_import, source)


if __name__ == "__main__":
    unittest.main()
