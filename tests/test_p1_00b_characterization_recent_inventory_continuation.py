import hashlib
import json
from pathlib import Path
import unittest


class RecentCharacterizationContinuationContractTests(unittest.TestCase):
    def test_empty_manifest_is_sha_bound_to_base_inventory(self):
        repo_root = Path(__file__).resolve().parent.parent

        base_inventory_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        base_path = repo_root / base_inventory_rel_path
        self.assertTrue(base_path.is_file(), f"Base inventory not found at {base_path}")

        continuation_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json"
        continuation_path = repo_root / continuation_rel_path
        self.assertTrue(continuation_path.is_file(), f"Continuation manifest not found at {continuation_path}")

        base_bytes = base_path.read_bytes()
        calculated_base_sha256 = hashlib.sha256(base_bytes).hexdigest()
        base_data = json.loads(base_bytes.decode("utf-8"))

        continuation_bytes = continuation_path.read_bytes()
        continuation_data = json.loads(continuation_bytes.decode("utf-8"))

        # Validate exact top-level keys
        expected_top_level_keys = {"schema_version", "stage", "base_inventory", "records"}
        self.assertEqual(set(continuation_data.keys()), expected_top_level_keys)

        # Validate top-level schema_version, stage, records
        self.assertIs(type(continuation_data["schema_version"]), int)
        self.assertEqual(continuation_data["schema_version"], 1)
        self.assertEqual(continuation_data["stage"], "P1-00b")
        self.assertEqual(continuation_data["stage"], base_data["stage"])
        self.assertIsInstance(continuation_data["records"], list)
        self.assertEqual(continuation_data["records"], [])
        self.assertEqual(len(continuation_data["records"]), 0)

        # Validate exact base_inventory keys
        base_inventory_binding = continuation_data["base_inventory"]
        self.assertIsInstance(base_inventory_binding, dict)
        expected_base_inventory_keys = {"path", "sha256", "inspected_head"}
        self.assertEqual(set(base_inventory_binding.keys()), expected_base_inventory_keys)

        # Validate exact binding values
        authoritative_path = "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        authoritative_sha256 = "5a235244378a63ad9da935e5e70c98593d4664f5f36cfcca3a610743aeef33eb"
        authoritative_head = "5c2b95722ece7b0d22c13ee3c861dfb96a930c92"

        self.assertEqual(base_inventory_binding["path"], authoritative_path)
        self.assertEqual(base_inventory_binding["sha256"], authoritative_sha256)
        self.assertEqual(base_inventory_binding["inspected_head"], authoritative_head)

        # Compare binding values to actual base inventory
        self.assertEqual(base_inventory_binding["sha256"], calculated_base_sha256)
        self.assertEqual(base_inventory_binding["inspected_head"], base_data["inspected_head"])


if __name__ == "__main__":
    unittest.main()
