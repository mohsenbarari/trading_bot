import copy
import hashlib
import json
from pathlib import Path
import unittest


EXPECTED_RECONCILIATION_SHA256 = (
    "80e23877c849688cddb567b88898ee1440adee0a65a07237b5325d36dcab0a6d"
)

EXPECTED_REMAINING_INVENTORY_PATHS = [
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json",
]


def get_reconciliation_json_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "refactor"
        / "two-server"
        / "section-1"
        / "inventory"
        / "recent-characterization-reconciliation-13-14.json"
    )


def load_reconciliation_data() -> dict:
    raw_bytes = get_reconciliation_json_path().read_bytes()
    return json.loads(raw_bytes.decode("utf-8"))


def validate_reconciliation_accounting_domain(data: dict) -> None:
    if type(data) is not dict:
        raise ValueError("Top-level reconciliation payload must be a dictionary")

    # 1. Validate schema_version
    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError(
            f"Field 'schema_version' must be exact int with value 1, "
            f"got {schema_version!r} (type {type(schema_version).__name__})"
        )

    # 2. Validate root accounting counters
    root_counter_specs = [
        ("units_since_reconciliation", 5),
        ("total_behavior_units", 5),
        ("reconciled_behavior_units", 2),
        ("remaining_behavior_units", 3),
    ]
    for field_name, expected_val in root_counter_specs:
        val = data.get(field_name)
        if type(val) is not int or val != expected_val:
            raise ValueError(
                f"Field '{field_name}' must be exact int with value {expected_val}, "
                f"got {val!r} (type {type(val).__name__})"
            )

    # 3. Validate nested counts counters
    counts = data.get("counts")
    if type(counts) is not dict:
        raise ValueError("Field 'counts' must be a dictionary")

    nested_counter_specs = [
        ("counts.baseline_evidenced_seed_count", "baseline_evidenced_seed_count", 135),
        ("counts.reconciled_new_seed_count", "reconciled_new_seed_count", 252),
        ("counts.evidenced_seed_count", "evidenced_seed_count", 387),
        ("counts.remaining_seed_count", "remaining_seed_count", 78),
        ("counts.expected_seed_count", "expected_seed_count", 465),
    ]
    for full_field_path, key, expected_val in nested_counter_specs:
        val = counts.get(key)
        if type(val) is not int or val != expected_val:
            raise ValueError(
                f"Field '{full_field_path}' must be exact int with value {expected_val}, "
                f"got {val!r} (type {type(val).__name__})"
            )

    # 4. Validate remaining_inventory_paths
    remaining_paths = data.get("remaining_inventory_paths")
    if type(remaining_paths) is not list:
        raise ValueError(
            f"Field 'remaining_inventory_paths' must be a list, "
            f"got {remaining_paths!r} (type {type(remaining_paths).__name__})"
        )
    if len(remaining_paths) != len(EXPECTED_REMAINING_INVENTORY_PATHS):
        raise ValueError(
            f"Field 'remaining_inventory_paths' must have length "
            f"{len(EXPECTED_REMAINING_INVENTORY_PATHS)}, got {len(remaining_paths)}"
        )
    for idx, (actual_item, expected_item) in enumerate(
        zip(remaining_paths, EXPECTED_REMAINING_INVENTORY_PATHS)
    ):
        if type(actual_item) is not str:
            raise ValueError(
                f"Field 'remaining_inventory_paths' entry at index {idx} must be str, "
                f"got {actual_item!r}"
            )
        if actual_item != expected_item:
            raise ValueError(
                f"Field 'remaining_inventory_paths' mismatch at index {idx}: "
                f"expected {expected_item!r}, got {actual_item!r}"
            )


class ReconciliationAccountingDomainTests(unittest.TestCase):
    def test_immutable_accounting_baseline(self) -> None:
        target_path = get_reconciliation_json_path()
        self.assertTrue(target_path.is_file(), f"File not found: {target_path}")

        raw_bytes = target_path.read_bytes()
        computed_sha = hashlib.sha256(raw_bytes).hexdigest()
        self.assertEqual(computed_sha, EXPECTED_RECONCILIATION_SHA256)

        data = json.loads(raw_bytes.decode("utf-8"))

        # Exact schema_version
        self.assertEqual(data["schema_version"], 1)
        self.assertIs(type(data["schema_version"]), int)

        # Exact root accounting counters
        self.assertEqual(data["units_since_reconciliation"], 5)
        self.assertIs(type(data["units_since_reconciliation"]), int)
        self.assertEqual(data["total_behavior_units"], 5)
        self.assertIs(type(data["total_behavior_units"]), int)
        self.assertEqual(data["reconciled_behavior_units"], 2)
        self.assertIs(type(data["reconciled_behavior_units"]), int)
        self.assertEqual(data["remaining_behavior_units"], 3)
        self.assertIs(type(data["remaining_behavior_units"]), int)

        # Exact nested counts counters
        counts = data["counts"]
        self.assertIs(type(counts), dict)
        self.assertEqual(counts["baseline_evidenced_seed_count"], 135)
        self.assertIs(type(counts["baseline_evidenced_seed_count"]), int)
        self.assertEqual(counts["reconciled_new_seed_count"], 252)
        self.assertIs(type(counts["reconciled_new_seed_count"]), int)
        self.assertEqual(counts["evidenced_seed_count"], 387)
        self.assertIs(type(counts["evidenced_seed_count"]), int)
        self.assertEqual(counts["remaining_seed_count"], 78)
        self.assertIs(type(counts["remaining_seed_count"]), int)
        self.assertEqual(counts["expected_seed_count"], 465)
        self.assertIs(type(counts["expected_seed_count"]), int)

        # Exact remaining_inventory_paths
        self.assertIs(type(data["remaining_inventory_paths"]), list)
        self.assertEqual(
            data["remaining_inventory_paths"],
            EXPECTED_REMAINING_INVENTORY_PATHS,
        )
        for p in data["remaining_inventory_paths"]:
            self.assertIs(type(p), str)

        # Canonical stage/status/coverage/seed-acceptance/blocker/closure flags
        self.assertEqual(data["stage"], "P1-00b")
        self.assertEqual(data["status"], "PARTIAL_RECONCILIATION")
        self.assertEqual(data["coverage_status"], "PARTIAL")
        self.assertEqual(
            data["seed_acceptance_status"],
            "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
        )
        self.assertEqual(
            data["canonical_blockers"],
            [
                "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
            ],
        )
        self.assertIs(data["closures_authorized"], False)
        self.assertIs(type(data["closures_authorized"]), bool)
        self.assertEqual(
            data["stages"],
            {
                "P1-00": "COMPLETE",
                "P1-00b": "IN_PROGRESS",
            },
        )
        self.assertEqual(
            data["reconciliation_policy"],
            {
                "no_sixth_behavior_accepted_before_complete_reconciliation": True,
                "deferred_continuations": ["continuation-12"],
                "global_reconciliation_complete": False,
            },
        )

        # Validate genuine baseline with helper and verify non-mutation
        data_copy = copy.deepcopy(data)
        validate_reconciliation_accounting_domain(data)
        self.assertEqual(data, data_copy)

    def test_schema_version_wrong_numeric_types_rejected(self) -> None:
        baseline = load_reconciliation_data()
        test_cases = [True, False, 1.0, "1", 2]
        for bad_val in test_cases:
            with self.subTest(bad_val=bad_val):
                mutant = copy.deepcopy(baseline)
                mutant["schema_version"] = bad_val
                with self.assertRaises(ValueError) as ctx:
                    validate_reconciliation_accounting_domain(mutant)
                self.assertIn("schema_version", str(ctx.exception))

    def test_every_counter_wrong_numeric_types_rejected(self) -> None:
        baseline = load_reconciliation_data()
        counter_definitions = [
            ("units_since_reconciliation", None, 5),
            ("total_behavior_units", None, 5),
            ("reconciled_behavior_units", None, 2),
            ("remaining_behavior_units", None, 3),
            ("counts.baseline_evidenced_seed_count", "baseline_evidenced_seed_count", 135),
            ("counts.reconciled_new_seed_count", "reconciled_new_seed_count", 252),
            ("counts.evidenced_seed_count", "evidenced_seed_count", 387),
            ("counts.remaining_seed_count", "remaining_seed_count", 78),
            ("counts.expected_seed_count", "expected_seed_count", 465),
        ]

        for full_path, nested_key, expected_val in counter_definitions:
            mutant_cases = [
                True,
                False,
                float(expected_val),
                str(expected_val),
                expected_val + 1,
            ]
            for bad_val in mutant_cases:
                with self.subTest(field=full_path, bad_val=bad_val):
                    mutant = copy.deepcopy(baseline)
                    if nested_key is None:
                        mutant[full_path] = bad_val
                    else:
                        mutant["counts"][nested_key] = bad_val
                    with self.assertRaises(ValueError) as ctx:
                        validate_reconciliation_accounting_domain(mutant)
                    self.assertIn(full_path, str(ctx.exception))

    def test_wrong_pending_inventory_paths_rejected(self) -> None:
        baseline = load_reconciliation_data()
        wrong_c12 = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-12.json"
        )
        p15, p16, p17 = EXPECTED_REMAINING_INVENTORY_PATHS

        drift_scenarios = [
            # Replacement of each slot independently
            ("slot_0_replaced", [wrong_c12, p16, p17]),
            ("slot_1_replaced", [p15, wrong_c12, p17]),
            ("slot_2_replaced", [p15, p16, wrong_c12]),
            # Duplicate entry
            ("duplicate_entry", [p15, p16, p16]),
            # Reordered entries
            ("reordered_entries", [p16, p15, p17]),
            # Missing entry
            ("missing_entry", [p15, p16]),
            # Wrong container types
            ("container_string", p15),
            ("container_dict", {p15: 1, p16: 2, p17: 3}),
            ("container_none", None),
        ]

        for scenario_name, bad_paths in drift_scenarios:
            with self.subTest(scenario=scenario_name):
                mutant = copy.deepcopy(baseline)
                mutant["remaining_inventory_paths"] = bad_paths
                with self.assertRaises(ValueError) as ctx:
                    validate_reconciliation_accounting_domain(mutant)
                self.assertIn("remaining_inventory_paths", str(ctx.exception))
