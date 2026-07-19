import unittest

from core.dr_data_matrix import build_three_site_data_matrix
from core.dr_data_policy import WEBAPP_DR_REPLICA_TABLES
from models.database import Base


class DrDataMatrixTests(unittest.TestCase):
    def test_every_mapped_table_and_field_appears_exactly_once(self):
        matrix = build_three_site_data_matrix()
        rows = matrix["tables"]

        self.assertEqual([row["name"] for row in rows], sorted(Base.metadata.tables))
        observed = [
            (row["name"], field["name"])
            for row in rows
            for field in row["fields"]
            if field["mapped_column"]
        ]
        expected = [
            (table.name, column.name)
            for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name)
            for column in table.columns
        ]
        self.assertEqual(observed, expected)
        self.assertEqual(
            sum(len(row["fields"]) for row in rows), matrix["field_count"]
        )

    def test_private_webapp_tables_never_target_bot(self):
        rows = {row["name"]: row for row in build_three_site_data_matrix()["tables"]}
        for table_name in WEBAPP_DR_REPLICA_TABLES:
            self.assertEqual(rows[table_name]["replication_class"], "webapp_private_dr")
            self.assertEqual(rows[table_name]["destinations"], ["webapp_fi", "webapp_ir"])

    def test_push_credentials_are_private_dr_but_browser_fingerprint_is_dropped(self):
        rows = {row["name"]: row for row in build_three_site_data_matrix()["tables"]}
        fields = {item["name"]: item for item in rows["push_subscriptions"]["fields"]}

        for field_name in ("endpoint", "endpoint_hash", "p256dh", "auth"):
            self.assertTrue(fields[field_name]["transported"])
            self.assertTrue(fields[field_name]["sensitive"])
        for field_name in ("last_error", "platform", "user_agent"):
            self.assertFalse(fields[field_name]["transported"])


if __name__ == "__main__":
    unittest.main()
