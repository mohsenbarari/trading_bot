"""Hermetic stdlib characterization of C16 and C17 JSON inventory record ownership.

Stage: P1-00b
Assignment: WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161
Canonical gap: docs/refactor/two-server/section-1/00-stage-ledger.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Shared seed: API-IDENTITY-GET-USERS-READ-USER-L440

Observed inventory record-ownership scope:
- Genuine C16 inventory continuation-16.json owning manager-peer read HTTP 403 characterization
- Genuine C17 inventory continuation-17.json owning authorized missing-target HTTP 404 characterization
- Shared ownership assertion oracle verifying frozen literal dictionaries, schema int type, predecessor byte SHA256, inventory byte SHA256, behavior source byte SHA256, and AST-parsed synchronous test method selectors
- Scenario 1 (test_c17_record_substitution_into_c16_rejected): Proves genuine C16 inventory rejects substitution of genuine C17 record in place of C16 record despite sharing seed API-IDENTITY-GET-USERS-READ-USER-L440
- Scenario 2 (test_c17_record_append_to_c16_rejected): Proves genuine C16 inventory rejects mixed-owner append of [C16 record, C17 record] despite unchanged C16 predecessor and shared seed API-IDENTITY-GET-USERS-READ-USER-L440

Exclusions:
- Excludes reciprocal C16-to-C17 scenario already covered in accepted C17 checkpoint.
- Excludes schema numeric-type cases already covered.
- Excludes empty/duplicate record cases.
- Excludes HTTP runtime and authorization-policy execution.
- Excludes persistence, retry, reload, and origin matrix.
- Excludes ledger, inventory, count edits, new behavior acceptance, or Stage closure.
"""

import ast
import hashlib
import json
import unittest

C15_INVENTORY_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
)
C16_INVENTORY_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
)
C17_INVENTORY_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
)

FROZEN_C16_INVENTORY = {
    "schema_version": 1,
    "stage": "P1-00b",
    "predecessor_inventory": {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json",
        "sha256": "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5",
        "last_commit": "8cabfffc4622d88bf006ed9ec050e0dc0b8e211c",
    },
    "records": [
        {
            "path": "tests/test_p1_00b_characterization_users_read_manager_peer_http.py",
            "sha256": "f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd",
            "last_commit": "05fe0d5c2a5dae727aa7219549843b1ce6235945",
            "seed_ids": [
                "API-IDENTITY-GET-USERS-READ-USER-L440",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_users_read_manager_peer_http.py::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading",
            ],
        },
    ],
}

FROZEN_C17_INVENTORY = {
    "schema_version": 1,
    "stage": "P1-00b",
    "predecessor_inventory": {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
        "sha256": "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
        "last_commit": "0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f",
    },
    "records": [
        {
            "path": "tests/test_p1_00b_characterization_users_read_missing_http.py",
            "sha256": "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb",
            "last_commit": "0f3deb613251c7c5f39822b2a1f0e5532e5e140f",
            "seed_ids": [
                "API-IDENTITY-GET-USERS-READ-USER-L440",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get",
            ],
        },
    ],
}

FROZEN_INVENTORY_SHA256 = {
    "C16": "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
    "C17": "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15",
}

FROZEN_PREDECESSOR_SHA256 = {
    "C16": "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5",
    "C17": "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
}

FROZEN_INVENTORY_PATHS = {
    "C16": C16_INVENTORY_PATH,
    "C17": C17_INVENTORY_PATH,
}

FROZEN_SPECS = {
    "C16": FROZEN_C16_INVENTORY,
    "C17": FROZEN_C17_INVENTORY,
}


class C16C17RecordOwnershipTests(unittest.TestCase):
    """Characterization tests verifying C16/C17 JSON inventory record ownership."""

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
        """Verify genuine canonical committed C16 and C17 inventories via shared oracle."""
        with open(C16_INVENTORY_PATH, "rb") as f:
            c16_bytes = f.read()
        c16_data = json.loads(c16_bytes.decode("utf-8"))

        with open(C17_INVENTORY_PATH, "rb") as f:
            c17_bytes = f.read()
        c17_data = json.loads(c17_bytes.decode("utf-8"))

        # Baseline validation of genuine C16 and C17 inventories
        self.assert_inventory_record_ownership(c16_data, "C16")
        self.assert_inventory_record_ownership(c17_data, "C17")

        # Freeze C16 and C17 canonical source commit and selector contracts
        c16_record = c16_data["records"][0]
        self.assertEqual(
            c16_record["last_commit"],
            "05fe0d5c2a5dae727aa7219549843b1ce6235945",
        )
        self.assertEqual(
            c16_record["test_selectors"],
            [
                "tests/test_p1_00b_characterization_users_read_manager_peer_http.py::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading",
            ],
        )

        c17_record = c17_data["records"][0]
        self.assertEqual(
            c17_record["last_commit"],
            "0f3deb613251c7c5f39822b2a1f0e5532e5e140f",
        )
        self.assertEqual(
            c17_record["test_selectors"],
            [
                "tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get",
            ],
        )

        # Prove that shared seed is not ownership evidence
        self.assertEqual(c16_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-USER-L440"])
        self.assertEqual(c17_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-USER-L440"])
        self.assertEqual(c16_record["seed_ids"], c17_record["seed_ids"])
        self.assertNotEqual(c16_record["path"], c17_record["path"])
        self.assertNotEqual(c16_record["sha256"], c17_record["sha256"])
        self.assertNotEqual(c16_record["last_commit"], c17_record["last_commit"])
        self.assertNotEqual(c16_record["test_selectors"], c17_record["test_selectors"])

    def test_c17_record_substitution_into_c16_rejected(self) -> None:
        """Prove C16 inventory rejects substituted genuine C17 record despite shared seed."""
        # Read and validate fresh genuine C16 and C17 immediately before mutation
        with open(C16_INVENTORY_PATH, "rb") as f:
            c16_disk_bytes_before = f.read()
        with open(C17_INVENTORY_PATH, "rb") as f:
            c17_disk_bytes_before = f.read()

        c16_fresh = json.loads(c16_disk_bytes_before.decode("utf-8"))
        c17_fresh = json.loads(c17_disk_bytes_before.decode("utf-8"))

        self.assert_inventory_record_ownership(c16_fresh, "C16")
        self.assert_inventory_record_ownership(c17_fresh, "C17")

        c16_record = c16_fresh["records"][0]
        c17_record = c17_fresh["records"][0]

        # Build separate JSON text document with C16 fields preserved and C17 record substituted
        mutant_doc = {
            "schema_version": c16_fresh["schema_version"],
            "stage": c16_fresh["stage"],
            "predecessor_inventory": c16_fresh["predecessor_inventory"],
            "records": [c17_record],
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        decoded_mutant = json.loads(mutant_json_text)

        # Explicit assertions on mutant construction
        self.assertEqual(
            decoded_mutant["predecessor_inventory"],
            c16_fresh["predecessor_inventory"],
        )
        self.assertEqual(decoded_mutant["records"], [c17_record])
        self.assertNotEqual(decoded_mutant["records"], [c16_record])
        self.assertEqual(c16_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-USER-L440"])
        self.assertEqual(c17_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-USER-L440"])
        self.assertEqual(c16_record["seed_ids"], c17_record["seed_ids"])

        # Call the SAME C16 oracle on this decoded mutant under assertRaises(AssertionError)
        with self.assertRaises(AssertionError):
            self.assert_inventory_record_ownership(decoded_mutant, "C16")

        # Reread/decode and validate both genuine C16 and C17 after rejection
        with open(C16_INVENTORY_PATH, "rb") as f:
            c16_disk_bytes_after = f.read()
        with open(C17_INVENTORY_PATH, "rb") as f:
            c17_disk_bytes_after = f.read()

        self.assertEqual(
            c16_disk_bytes_after,
            c16_disk_bytes_before,
            "Canonical C16 inventory bytes modified during substitution rejection test",
        )
        self.assertEqual(
            c17_disk_bytes_after,
            c17_disk_bytes_before,
            "Canonical C17 inventory bytes modified during substitution rejection test",
        )

        c16_reloaded = json.loads(c16_disk_bytes_after.decode("utf-8"))
        c17_reloaded = json.loads(c17_disk_bytes_after.decode("utf-8"))
        self.assert_inventory_record_ownership(c16_reloaded, "C16")
        self.assert_inventory_record_ownership(c17_reloaded, "C17")

    def test_c17_record_append_to_c16_rejected(self) -> None:
        """Prove C16 inventory rejects mixed-owner append [C16, C17] despite shared seed."""
        # Read and validate fresh genuine C16 and C17 immediately before mutation
        with open(C16_INVENTORY_PATH, "rb") as f:
            c16_disk_bytes_before = f.read()
        with open(C17_INVENTORY_PATH, "rb") as f:
            c17_disk_bytes_before = f.read()

        c16_fresh = json.loads(c16_disk_bytes_before.decode("utf-8"))
        c17_fresh = json.loads(c17_disk_bytes_before.decode("utf-8"))

        self.assert_inventory_record_ownership(c16_fresh, "C16")
        self.assert_inventory_record_ownership(c17_fresh, "C17")

        c16_record = c16_fresh["records"][0]
        c17_record = c17_fresh["records"][0]

        # Build separate JSON text document with genuine C16 fields and ordered [C16, C17] records
        mutant_append_doc = {
            "schema_version": c16_fresh["schema_version"],
            "stage": c16_fresh["stage"],
            "predecessor_inventory": c16_fresh["predecessor_inventory"],
            "records": [c16_record, c17_record],
        }
        mutant_append_json = json.dumps(mutant_append_doc, indent=2)
        decoded_append_mutant = json.loads(mutant_append_json)

        # Prove it is a mixed-owner append, not an empty or duplicate record substitute
        self.assertEqual(len(decoded_append_mutant["records"]), 2)
        self.assertEqual(
            decoded_append_mutant["records"],
            [c16_record, c17_record],
        )
        self.assertEqual(decoded_append_mutant["records"][0], c16_record)
        self.assertEqual(decoded_append_mutant["records"][1], c17_record)
        self.assertNotEqual(len(decoded_append_mutant["records"]), 0)
        self.assertTrue(all(bool(r) for r in decoded_append_mutant["records"]))
        self.assertNotEqual(c16_record, c17_record)
        self.assertNotEqual(c16_record["path"], c17_record["path"])
        self.assertNotEqual(c16_record["last_commit"], c17_record["last_commit"])
        self.assertEqual(
            decoded_append_mutant["predecessor_inventory"],
            c16_fresh["predecessor_inventory"],
        )
        self.assertEqual(c16_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-USER-L440"])
        self.assertEqual(c17_record["seed_ids"], ["API-IDENTITY-GET-USERS-READ-USER-L440"])
        self.assertEqual(c16_record["seed_ids"], c17_record["seed_ids"])

        # Call the SAME C16 oracle on this decoded mutant under assertRaises(AssertionError)
        with self.assertRaises(AssertionError):
            self.assert_inventory_record_ownership(decoded_append_mutant, "C16")

        # Reread/decode and validate both genuine C16 and C17 after rejection
        with open(C16_INVENTORY_PATH, "rb") as f:
            c16_disk_bytes_after = f.read()
        with open(C17_INVENTORY_PATH, "rb") as f:
            c17_disk_bytes_after = f.read()

        self.assertEqual(
            c16_disk_bytes_after,
            c16_disk_bytes_before,
            "Canonical C16 inventory bytes modified during append rejection test",
        )
        self.assertEqual(
            c17_disk_bytes_after,
            c17_disk_bytes_before,
            "Canonical C17 inventory bytes modified during append rejection test",
        )

        c16_reloaded = json.loads(c16_disk_bytes_after.decode("utf-8"))
        c17_reloaded = json.loads(c17_disk_bytes_after.decode("utf-8"))
        self.assert_inventory_record_ownership(c16_reloaded, "C16")
        self.assert_inventory_record_ownership(c17_reloaded, "C17")


if __name__ == "__main__":
    unittest.main()
