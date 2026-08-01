from __future__ import annotations

import ast
from pathlib import Path
import unittest

from core.object_delta_mvp_canonical import (
    DELETE,
    INSERT,
    UPDATE,
    MARKET_RUNTIME_SINGLETON_FIELD,
    MARKET_RUNTIME_SINGLETON_VALUE,
    OBJECT_DELTA_MVP_CANONICAL_DEFAULT_ENABLED,
    OBJECT_DELTA_MVP_CANONICAL_ENABLES_RECEIVER,
    OBJECT_DELTA_MVP_CANONICAL_TABLES,
    OBJECT_DELTA_MVP_CANONICAL_VALIDATES_RAW_CHANGELOG_PAYLOADS,
    ObjectDeltaMvpCanonicalError,
    canonical_mvp_table_descriptor,
    validate_canonical_mvp_object_delta,
)


ROOT = Path(__file__).parents[1]


def delta(
    *,
    table: str,
    operation: str = UPDATE,
    identity: dict[str, object],
    fields: dict[str, object],
    references: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "table": table,
        "operation": operation,
        "identity": identity,
        "fields": fields,
        "references": {} if references is None else references,
    }


class ObjectDeltaMvpCanonicalTests(unittest.TestCase):
    def test_exact_low_complexity_table_contracts_are_immutable_and_default_off(self) -> None:
        expected = {
            "trading_settings": ("key",),
            "market_schedule_overrides": ("date",),
            "market_runtime_state": (MARKET_RUNTIME_SINGLETON_FIELD,),
            "commodities": ("name",),
            "commodity_aliases": ("alias",),
        }

        self.assertFalse(OBJECT_DELTA_MVP_CANONICAL_DEFAULT_ENABLED)
        self.assertFalse(OBJECT_DELTA_MVP_CANONICAL_VALIDATES_RAW_CHANGELOG_PAYLOADS)
        self.assertFalse(OBJECT_DELTA_MVP_CANONICAL_ENABLES_RECEIVER)
        self.assertEqual(set(expected), set(OBJECT_DELTA_MVP_CANONICAL_TABLES))
        for table, identity_fields in expected.items():
            with self.subTest(table=table):
                contract = canonical_mvp_table_descriptor(table)
                self.assertEqual(identity_fields, contract.identity_fields)
                self.assertEqual((INSERT, UPDATE), contract.allowed_operations)
                self.assertFalse(contract.default_enabled)
                self.assertFalse(contract.validates_raw_changelog_payloads)
                self.assertFalse(contract.enables_receiver)
        self.assertEqual(
            ("commodity_name",),
            canonical_mvp_table_descriptor("commodity_aliases").reference_names,
        )
        with self.assertRaises(TypeError):
            OBJECT_DELTA_MVP_CANONICAL_TABLES["unexpected"] = object()

    def test_valid_mappings_produce_immutable_canonical_descriptors(self) -> None:
        cases = (
            delta(
                table="trading_settings",
                operation=INSERT,
                identity={"key": "offer_min_quantity"},
                fields={"value": "5"},
            ),
            delta(
                table="market_schedule_overrides",
                identity={"date": "2026-08-01"},
                fields={
                    "override_type": "custom_hours",
                    "open_time_local": "09:00:00",
                    "close_time_local": "18:00:00",
                    "note": "Holiday hours",
                },
            ),
            delta(
                table="market_runtime_state",
                identity={MARKET_RUNTIME_SINGLETON_FIELD: MARKET_RUNTIME_SINGLETON_VALUE},
                fields={
                    "is_open": True,
                    "active_web_notice_visible": False,
                    "offers_since_last_open": 0,
                    "last_transition_at": "2026-08-01T09:00:00Z",
                },
            ),
            delta(
                table="commodities",
                identity={"name": "Imami coin"},
                fields={},
            ),
            delta(
                table="commodity_aliases",
                identity={"alias": "Imami"},
                fields={},
                references={"commodity_name": "Imami coin"},
            ),
        )

        for value in cases:
            with self.subTest(table=value["table"]):
                descriptor = validate_canonical_mvp_object_delta(value)
                self.assertFalse(descriptor.validates_raw_changelog_payloads)
                self.assertFalse(descriptor.enables_receiver)
                self.assertEqual(value, descriptor.as_mapping())
                with self.assertRaises(TypeError):
                    descriptor.identity["unexpected"] = "value"
                with self.assertRaises(TypeError):
                    descriptor.fields["unexpected"] = "value"
                with self.assertRaises(TypeError):
                    descriptor.references["unexpected"] = "value"

    def test_unknown_or_missing_envelope_sections_fail_closed(self) -> None:
        valid = delta(
            table="commodities",
            identity={"name": "Imami coin"},
            fields={},
        )
        for mutation, error in (
            (lambda value: value.update({"change_log_id": 41}), "unknown"),
            (lambda value: value.pop("references"), "missing"),
            (lambda value: value.update({"identity": []}), "mapping"),
        ):
            with self.subTest(error=error):
                candidate = dict(valid)
                mutation(candidate)
                with self.assertRaisesRegex(ObjectDeltaMvpCanonicalError, error):
                    validate_canonical_mvp_object_delta(candidate)

    def test_identity_reference_and_local_raw_ids_are_rejected(self) -> None:
        no_identity = delta(
            table="commodities",
            identity={},
            fields={},
        )
        local_identity = delta(
            table="commodities",
            identity={"id": 41},
            fields={},
        )
        local_alias_field = delta(
            table="commodity_aliases",
            identity={"alias": "Imami"},
            fields={"commodity_id": 41},
            references={"commodity_name": "Imami coin"},
        )
        missing_reference = delta(
            table="commodity_aliases",
            identity={"alias": "Imami"},
            fields={},
            references={},
        )
        raw_reference = delta(
            table="commodity_aliases",
            identity={"alias": "Imami"},
            fields={},
            references={"commodity_id": 41},
        )

        for value, error in (
            (no_identity, "missing"),
            (local_identity, "local raw id"),
            (local_alias_field, "local raw id"),
            (missing_reference, "missing"),
            (raw_reference, "local raw id"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ObjectDeltaMvpCanonicalError, error):
                    validate_canonical_mvp_object_delta(value)

    def test_delete_and_noncanonical_types_are_rejected(self) -> None:
        valid = delta(
            table="market_runtime_state",
            identity={MARKET_RUNTIME_SINGLETON_FIELD: MARKET_RUNTIME_SINGLETON_VALUE},
            fields={
                "is_open": True,
                "active_web_notice_visible": False,
                "offers_since_last_open": 0,
                "last_transition_at": None,
            },
        )
        invalid_cases = (
            (delta(
                table="commodities",
                operation=DELETE,
                identity={"name": "Imami coin"},
                fields={},
            ), "delete"),
            (delta(
                table="commodities",
                operation="update",
                identity={"name": "Imami coin"},
                fields={},
            ), "operation"),
            (delta(
                table="market_schedule_overrides",
                identity={"date": "2026/08/01"},
                fields={
                    "override_type": "closed_all_day",
                    "open_time_local": None,
                    "close_time_local": None,
                    "note": None,
                },
            ), "ISO date"),
            (delta(
                table="market_runtime_state",
                identity={MARKET_RUNTIME_SINGLETON_FIELD: MARKET_RUNTIME_SINGLETON_VALUE},
                fields={
                    "is_open": 1,
                    "active_web_notice_visible": False,
                    "offers_since_last_open": 0,
                    "last_transition_at": None,
                },
            ), "boolean"),
            (delta(
                table="market_runtime_state",
                identity={MARKET_RUNTIME_SINGLETON_FIELD: MARKET_RUNTIME_SINGLETON_VALUE},
                fields={
                    "is_open": True,
                    "active_web_notice_visible": False,
                    "offers_since_last_open": True,
                    "last_transition_at": None,
                },
            ), "offer count"),
            (delta(
                table="market_runtime_state",
                identity={MARKET_RUNTIME_SINGLETON_FIELD: "1"},
                fields=valid["fields"],
            ), "singleton"),
            (delta(
                table="commodity_aliases",
                identity={"alias": "Imami"},
                fields={},
                references={"commodity_name": 1},
            ), "canonical text"),
        )
        for value, error in invalid_cases:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ObjectDeltaMvpCanonicalError, error):
                    validate_canonical_mvp_object_delta(value)

    def test_market_schedule_semantics_and_timestamp_canonicalization_are_strict(self) -> None:
        all_day_with_hours = delta(
            table="market_schedule_overrides",
            identity={"date": "2026-08-01"},
            fields={
                "override_type": "closed_all_day",
                "open_time_local": "09:00:00",
                "close_time_local": "18:00:00",
                "note": None,
            },
        )
        unordered_custom_hours = delta(
            table="market_schedule_overrides",
            identity={"date": "2026-08-01"},
            fields={
                "override_type": "custom_hours",
                "open_time_local": "18:00:00",
                "close_time_local": "09:00:00",
                "note": None,
            },
        )
        non_utc_timestamp = delta(
            table="market_runtime_state",
            identity={MARKET_RUNTIME_SINGLETON_FIELD: MARKET_RUNTIME_SINGLETON_VALUE},
            fields={
                "is_open": True,
                "active_web_notice_visible": False,
                "offers_since_last_open": 0,
                "last_transition_at": "2026-08-01T09:00:00+03:30",
            },
        )

        for value, error in (
            (all_day_with_hours, "all-day"),
            (unordered_custom_hours, "ordered"),
            (non_utc_timestamp, "canonical UTC"),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(ObjectDeltaMvpCanonicalError, error):
                    validate_canonical_mvp_object_delta(value)

        legacy_utc = delta(
            table="market_runtime_state",
            identity={MARKET_RUNTIME_SINGLETON_FIELD: MARKET_RUNTIME_SINGLETON_VALUE},
            fields={
                "is_open": True,
                "active_web_notice_visible": False,
                "offers_since_last_open": 0,
                "last_transition_at": "2026-08-01T09:00:00+00:00",
            },
        )
        normalized = validate_canonical_mvp_object_delta(legacy_utc)
        self.assertEqual("2026-08-01T09:00:00Z", normalized.fields["last_transition_at"])

    def test_module_is_pure_and_does_not_import_scope_or_runtime_modules(self) -> None:
        path = ROOT / "core/object_delta_mvp_canonical.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.add(node.module or "")

        self.assertEqual(
            {
                "__future__",
                "collections.abc",
                "dataclasses",
                "datetime",
                "types",
                "typing",
            },
            imported_modules,
        )


if __name__ == "__main__":
    unittest.main()
