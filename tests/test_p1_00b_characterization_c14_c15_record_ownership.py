"""Hermetic stdlib characterization of C14 and C15 JSON inventory record ownership.

Stage: P1-00b
Assignment: WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161
Canonical gap: docs/refactor/two-server/section-1/00-stage-ledger.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Shared seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407

Observed inventory record-ownership scope:
- Genuine C14 inventory continuation-14.json owning HTTP 500 list execute failure characterization
- Genuine C15 inventory continuation-15.json owning HTTP 200 empty list characterization
- Shared ownership assertion oracle verifying frozen literal dictionaries, schema int type, predecessor byte SHA256, inventory byte SHA256, behavior source byte SHA256, and AST-parsed synchronous test method selectors
- Scenario 1 (test_c14_record_substitution_into_c15_rejected): Proves genuine C15 inventory rejects substitution of genuine C14 record in place of C15 record despite sharing seed API-IDENTITY-GET-USERS-READ-ALL-USERS-L407
- Scenario 2 (test_c14_record_append_to_c15_rejected): Proves genuine C15 inventory rejects mixed-owner append of [C15 record, C14 record] despite unchanged C15 predecessor and shared seed API-IDENTITY-GET-USERS-READ-ALL-USERS-L407

Exclusions:
- Excludes reciprocal C15-to-C14 scenario.
- Excludes generic schema numeric-domain scenarios.
- Excludes empty/duplicate record cases.
- Excludes HTTP runtime and authorization-policy execution.
- Excludes persistence, retry, reload, and origin matrix.
- Excludes ledger, inventory, count edits, new behavior acceptance, or Stage closure.
"""

import ast
import hashlib
import json
import unittest

C13_INVENTORY_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json"
)
C14_INVENTORY_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json"
)
C15_INVENTORY_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
)

FROZEN_C14_INVENTORY = {
    "schema_version": 1,
    "stage": "P1-00b",
    "predecessor_inventory": {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json",
        "sha256": "ebe8e3c3189f8961a61d256ed5cc690864c2e4d7f5c6d1cc91e3554060769071",
        "last_commit": "e0ed45e87a45f32d19de19e2227f3af1eb969193",
    },
    "records": [
        {
            "path": "tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py",
            "sha256": "1fd0fe0e19768903c6fd2e921b63238c2af0ceba5b6331210ce6e1e1494e0623",
            "last_commit": "d175d215f83631a98a1a9fefea7c27cef6d88936",
            "seed_ids": [
                "API-IDENTITY-GET-USERS-READ-ALL-USERS-L407",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py::UsersReadAllExecuteFailureHttpCharacterizationTests::test_list_query_failure_returns_http_500_before_relation_context",
            ],
        },
    ],
}

FROZEN_C15_INVENTORY = {
    "schema_version": 1,
    "stage": "P1-00b",
    "predecessor_inventory": {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json",
        "sha256": "782407b237098640fffa0ce1c011d80406ce0b1707b9b06428a5e911e0487554",
        "last_commit": "540dc1cba9e2efed477cd81b71b946a69b47fcde",
    },
    "records": [
        {
            "path": "tests/test_p1_00b_characterization_users_read_all_empty_http.py",
            "sha256": "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa",
            "last_commit": "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8",
            "seed_ids": [
                "API-IDENTITY-GET-USERS-READ-ALL-USERS-L407",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_users_read_all_empty_http.py::UsersReadAllEmptyHttpCharacterizationTests::test_empty_list_returns_http_200_without_relation_queries",
            ],
        },
    ],
}

FROZEN_INVENTORY_SHA256 = {
    "C14": "782407b237098640fffa0ce1c011d80406ce0b1707b9b06428a5e911e0487554",
    "C15": "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5",
}

FROZEN_PREDECESSOR_SHA256 = {
    "C14": "ebe8e3c3189f8961a61d256ed5cc690864c2e4d7f5c6d1cc91e3554060769071",
    "C15": "782407b237098640fffa0ce1c011d80406ce0b1707b9b06428a5e911e0487554",
}

FROZEN_INVENTORY_PATHS = {
    "C14": C14_INVENTORY_PATH,
    "C15": C15_INVENTORY_PATH,
}

FROZEN_SPECS = {
    "C14": FROZEN_C14_INVENTORY,
    "C15": FROZEN_C15_INVENTORY,
}


class C14C15RecordOwnershipTests(unittest.TestCase):
    """Characterization tests verifying C14/C15 JSON inventory record ownership."""

    def assert_inventory_record_ownership(self, inventory_data: dict, target: str) -> None:
        """Shared assertion oracle for inventory record ownership against frozen literals."""
        self.assertIsInstance(inventory_data, dict)
        self.assertIn(target, FROZEN_SPECS, f"Unknown target key {target}")
        expected = FROZEN_SPECS[target]

        # Exact schema key shape
        self.assertEqual(
            set(inventory_data.keys()),
            {"schema_version", "stage", "predecessor_inventory", "records"},
        )
        self.assertEqual(
            set(inventory_data["predecessor_inventory"].keys()),
            {"path", "sha256", "last_commit"},
        )

        # Exact schema int type (strictly int, not bool)
        self.assertIs(type(inventory_data["schema_version"]), int)
        self.assertEqual(inventory_data["schema_version"], expected["schema_version"])
        self.assertEqual(inventory_data["stage"], expected["stage"])
        self.assertEqual(
            inventory_data["predecessor_inventory"],
            expected["predecessor_inventory"],
        )

        # Record count and structure shape
        self.assertIsInstance(inventory_data["records"], list)
        self.assertEqual(len(inventory_data["records"]), len(expected["records"]))
        for record in inventory_data["records"]:
            self.assertEqual(
                set(record.keys()),
                {"path", "sha256", "last_commit", "seed_ids", "test_selectors"},
            )
            self.assertIsInstance(record["seed_ids"], list)
            self.assertIsInstance(record["test_selectors"], list)

        # Full deep dictionary equality against independently frozen spec
        self.assertEqual(inventory_data, expected)

        # Verify actual inventory file raw bytes SHA256 against frozen hash
        inv_path = FROZEN_INVENTORY_PATHS[target]
        with open(inv_path, "rb") as f:
            inv_bytes = f.read()
        self.assertEqual(
            hashlib.sha256(inv_bytes).hexdigest(),
            FROZEN_INVENTORY_SHA256[target],
        )

        # Verify predecessor file raw bytes SHA256 against frozen hash
        pred_path = expected["predecessor_inventory"]["path"]
        with open(pred_path, "rb") as f:
            pred_bytes = f.read()
        pred_sha = hashlib.sha256(pred_bytes).hexdigest()
        self.assertEqual(pred_sha, FROZEN_PREDECESSOR_SHA256[target])
        self.assertEqual(pred_sha, expected["predecessor_inventory"]["sha256"])

        # Verify each owned behavior source SHA256 and AST test selector resolution
        for idx, record in enumerate(inventory_data["records"]):
            exp_record = expected["records"][idx]
            self.assertEqual(record["path"], exp_record["path"])
            self.assertEqual(record["sha256"], exp_record["sha256"])
            self.assertEqual(record["last_commit"], exp_record["last_commit"])
            self.assertEqual(record["seed_ids"], exp_record["seed_ids"])
            self.assertEqual(record["test_selectors"], exp_record["test_selectors"])

            source_path = record["path"]
            with open(source_path, "rb") as f:
                source_bytes = f.read()
            self.assertEqual(
                hashlib.sha256(source_bytes).hexdigest(),
                record["sha256"],
            )

            tree = ast.parse(source_bytes.decode("utf-8"), filename=source_path)
            for selector in record["test_selectors"]:
                parts = selector.split("::")
                self.assertEqual(
                    len(parts),
                    3,
                    f"Selector {selector} must follow path::Class::method format",
                )
                sel_path, sel_class, sel_method = parts
                self.assertEqual(sel_path, source_path)

                # Resolve top-level ClassDef in AST
                class_node = None
                for node in tree.body:
                    if isinstance(node, ast.ClassDef) and node.name == sel_class:
                        class_node = node
                        break
                self.assertIsNotNone(
                    class_node,
                    f"Top-level class {sel_class} not found in {source_path}",
                )

                # Resolve synchronous method in ClassDef (FunctionDef, not AsyncFunctionDef)
                method_node = None
                for node in class_node.body:
                    if isinstance(node, ast.FunctionDef) and node.name == sel_method:
                        method_node = node
                        break
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == sel_method:
                        self.fail(
                            f"Selector method {sel_method} in {sel_class} must be synchronous, found async",
                        )
                self.assertIsNotNone(
                    method_node,
                    f"Synchronous method {sel_method} not found in class {sel_class}",
                )

    def test_exact_inventory_record_ownership(self) -> None:
        """Verify genuine canonical committed C14 and C15 inventories via shared oracle."""
        with open(C14_INVENTORY_PATH, "rb") as f:
            c14_bytes = f.read()
        c14_data = json.loads(c14_bytes.decode("utf-8"))

        with open(C15_INVENTORY_PATH, "rb") as f:
            c15_bytes = f.read()
        c15_data = json.loads(c15_bytes.decode("utf-8"))

        # Baseline validation of genuine C14 and C15 inventories
        self.assert_inventory_record_ownership(c14_data, "C14")
        self.assert_inventory_record_ownership(c15_data, "C15")

        # Freeze C14 and C15 canonical source commit and selector contracts
        c14_record = c14_data["records"][0]
        self.assertEqual(
            c14_record["last_commit"],
            "d175d215f83631a98a1a9fefea7c27cef6d88936",
        )
        self.assertEqual(
            c14_record["test_selectors"],
            [
                "tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py::UsersReadAllExecuteFailureHttpCharacterizationTests::test_list_query_failure_returns_http_500_before_relation_context",
            ],
        )

        c15_record = c15_data["records"][0]
        self.assertEqual(
            c15_record["last_commit"],
            "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8",
        )
        self.assertEqual(
            c15_record["test_selectors"],
            [
                "tests/test_p1_00b_characterization_users_read_all_empty_http.py::UsersReadAllEmptyHttpCharacterizationTests::test_empty_list_returns_http_200_without_relation_queries",
            ],
        )

        # Prove that shared seed is not ownership evidence
        self.assertEqual(c14_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"])
        self.assertEqual(c15_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"])
        self.assertEqual(c14_record["seed_ids"], c15_record["seed_ids"])
        self.assertNotEqual(c14_record["path"], c15_record["path"])
        self.assertNotEqual(c14_record["sha256"], c15_record["sha256"])
        self.assertNotEqual(c14_record["last_commit"], c15_record["last_commit"])
        self.assertNotEqual(c14_record["test_selectors"], c15_record["test_selectors"])

    def test_c14_record_substitution_into_c15_rejected(self) -> None:
        """Prove C15 inventory rejects substituted genuine C14 record despite shared seed."""
        # Read and validate fresh genuine C14 and C15 immediately before mutation
        with open(C14_INVENTORY_PATH, "rb") as f:
            c14_disk_bytes_before = f.read()
        with open(C15_INVENTORY_PATH, "rb") as f:
            c15_disk_bytes_before = f.read()

        c14_fresh = json.loads(c14_disk_bytes_before.decode("utf-8"))
        c15_fresh = json.loads(c15_disk_bytes_before.decode("utf-8"))

        self.assert_inventory_record_ownership(c14_fresh, "C14")
        self.assert_inventory_record_ownership(c15_fresh, "C15")

        c14_record = c14_fresh["records"][0]
        c15_record = c15_fresh["records"][0]

        # Build separate JSON text document with C15 fields preserved and C14 record substituted
        mutant_doc = {
            "schema_version": c15_fresh["schema_version"],
            "stage": c15_fresh["stage"],
            "predecessor_inventory": c15_fresh["predecessor_inventory"],
            "records": [c14_record],
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        decoded_mutant = json.loads(mutant_json_text)

        # Explicit assertions on mutant construction
        self.assertEqual(
            decoded_mutant["predecessor_inventory"],
            c15_fresh["predecessor_inventory"],
        )
        self.assertEqual(decoded_mutant["records"], [c14_record])
        self.assertNotEqual(decoded_mutant["records"], [c15_record])
        self.assertEqual(c14_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"])
        self.assertEqual(c15_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"])
        self.assertEqual(c14_record["seed_ids"], c15_record["seed_ids"])

        # Call the SAME C15 oracle on this decoded mutant under assertRaises(AssertionError)
        with self.assertRaises(AssertionError):
            self.assert_inventory_record_ownership(decoded_mutant, "C15")

        # Reread/decode and validate both genuine C14 and C15 after rejection
        with open(C14_INVENTORY_PATH, "rb") as f:
            c14_disk_bytes_after = f.read()
        with open(C15_INVENTORY_PATH, "rb") as f:
            c15_disk_bytes_after = f.read()

        self.assertEqual(
            c14_disk_bytes_after,
            c14_disk_bytes_before,
            "Canonical C14 inventory bytes modified during substitution rejection test",
        )
        self.assertEqual(
            c15_disk_bytes_after,
            c15_disk_bytes_before,
            "Canonical C15 inventory bytes modified during substitution rejection test",
        )

        c14_reloaded = json.loads(c14_disk_bytes_after.decode("utf-8"))
        c15_reloaded = json.loads(c15_disk_bytes_after.decode("utf-8"))
        self.assert_inventory_record_ownership(c14_reloaded, "C14")
        self.assert_inventory_record_ownership(c15_reloaded, "C15")

    def test_c14_record_append_to_c15_rejected(self) -> None:
        """Prove C15 inventory rejects mixed-owner append [C15, C14] despite shared seed."""
        # Read and validate fresh genuine C14 and C15 immediately before mutation
        with open(C14_INVENTORY_PATH, "rb") as f:
            c14_disk_bytes_before = f.read()
        with open(C15_INVENTORY_PATH, "rb") as f:
            c15_disk_bytes_before = f.read()

        c14_fresh = json.loads(c14_disk_bytes_before.decode("utf-8"))
        c15_fresh = json.loads(c15_disk_bytes_before.decode("utf-8"))

        self.assert_inventory_record_ownership(c14_fresh, "C14")
        self.assert_inventory_record_ownership(c15_fresh, "C15")

        c14_record = c14_fresh["records"][0]
        c15_record = c15_fresh["records"][0]

        # Build separate JSON text document with genuine C15 fields and ordered [C15, C14] records
        mutant_append_doc = {
            "schema_version": c15_fresh["schema_version"],
            "stage": c15_fresh["stage"],
            "predecessor_inventory": c15_fresh["predecessor_inventory"],
            "records": [c15_record, c14_record],
        }
        mutant_append_json = json.dumps(mutant_append_doc, indent=2)
        decoded_append_mutant = json.loads(mutant_append_json)

        # Prove it is a mixed-owner append, not an empty or duplicate record substitute
        self.assertEqual(len(decoded_append_mutant["records"]), 2)
        self.assertEqual(
            decoded_append_mutant["records"],
            [c15_record, c14_record],
        )
        self.assertEqual(decoded_append_mutant["records"][0], c15_record)
        self.assertEqual(decoded_append_mutant["records"][1], c14_record)
        self.assertNotEqual(len(decoded_append_mutant["records"]), 0)
        self.assertTrue(all(bool(r) for r in decoded_append_mutant["records"]))
        self.assertNotEqual(c14_record, c15_record)
        self.assertNotEqual(c14_record["path"], c15_record["path"])
        self.assertNotEqual(c14_record["last_commit"], c15_record["last_commit"])
        self.assertEqual(
            decoded_append_mutant["predecessor_inventory"],
            c15_fresh["predecessor_inventory"],
        )
        self.assertEqual(c14_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"])
        self.assertEqual(c15_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"])
        self.assertEqual(c14_record["seed_ids"], c15_record["seed_ids"])

        # Call the SAME C15 oracle on this decoded mutant under assertRaises(AssertionError)
        with self.assertRaises(AssertionError):
            self.assert_inventory_record_ownership(decoded_append_mutant, "C15")

        # Reread/decode and validate both genuine C14 and C15 after rejection
        with open(C14_INVENTORY_PATH, "rb") as f:
            c14_disk_bytes_after = f.read()
        with open(C15_INVENTORY_PATH, "rb") as f:
            c15_disk_bytes_after = f.read()

        self.assertEqual(
            c14_disk_bytes_after,
            c14_disk_bytes_before,
            "Canonical C14 inventory bytes modified during append rejection test",
        )
        self.assertEqual(
            c15_disk_bytes_after,
            c15_disk_bytes_before,
            "Canonical C15 inventory bytes modified during append rejection test",
        )

        c14_reloaded = json.loads(c14_disk_bytes_after.decode("utf-8"))
        c15_reloaded = json.loads(c15_disk_bytes_after.decode("utf-8"))
        self.assert_inventory_record_ownership(c14_reloaded, "C14")
        self.assert_inventory_record_ownership(c15_reloaded, "C15")


if __name__ == "__main__":
    unittest.main()
