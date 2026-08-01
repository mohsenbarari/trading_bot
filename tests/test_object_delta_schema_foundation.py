import importlib.util
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory

from models.database import Base
import models.object_delta as object_delta
import models.object_delta_receiver_delivery as object_delta_receiver_delivery
import models.object_delta_source_publication_attempt as object_delta_source_publication_attempt


class ObjectDeltaSchemaFoundationTests(unittest.TestCase):
    def test_durable_tables_are_registered_without_runtime_adapters(self):
        self.assertEqual(
            {
                ObjectDeltaStream.__tablename__
                for ObjectDeltaStream in (
                    object_delta.ObjectDeltaStream,
                    object_delta.ObjectDeltaOutboxEntry,
                    object_delta.ObjectDeltaReceiverCursor,
                    object_delta.ObjectDeltaImportReceipt,
                    object_delta.ObjectDeltaSourceCutover,
                    object_delta_receiver_delivery.ObjectDeltaReceiverDeliveryNonceReceipt,
                    object_delta_source_publication_attempt.ObjectDeltaSourcePublicationAttempt,
                    object_delta_source_publication_attempt.ObjectDeltaSourcePublicationSeal,
                    object_delta_source_publication_attempt.ObjectDeltaSourcePublicationReceipt,
                    object_delta_source_publication_attempt.ObjectDeltaSourcePublicationAttestation,
                    object_delta_source_publication_attempt.ObjectDeltaSourcePublicationLedgerBinding,
                )
            },
            {
                "object_delta_streams",
                "object_delta_outbox",
                "object_delta_receiver_cursors",
                "object_delta_import_receipts",
                "object_delta_source_cutovers",
                "object_delta_receiver_delivery_nonce_receipts",
                "object_delta_source_publication_attempts",
                "object_delta_source_publication_seals",
                "object_delta_source_publication_receipts",
                "object_delta_source_publication_attestations",
                "object_delta_source_publication_ledger_bindings",
            },
        )
        self.assertIs(Base.metadata.tables["object_delta_outbox"].c.canonical_sync_item.type.__class__, object_delta.JSON)

    def test_migration_is_a_single_downstream_revision_and_has_no_runtime_imports(self):
        path = Path(__file__).parents[1] / "migrations/versions/a1b2c3d4e5f6_add_object_delta_schema.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn('revision: str = "0deltadelta01"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "f2c7d8e9a0b1"', text)
        self.assertNotIn("boto3", text)
        self.assertNotIn("subprocess", text)
        self.assertEqual(text.count("op.create_table("), 4)
        self.assertEqual(text.count("op.drop_table("), 4)

    def test_source_publication_attempt_revision_is_the_only_head_and_change_log_is_an_ancestor(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["0deltaattempt01"])

        ancestors: set[str] = set()

        def visit(revision_id: str) -> None:
            if revision_id in ancestors:
                return
            ancestors.add(revision_id)
            revision = script.get_revision(revision_id)
            for parent_id in revision._normalized_down_revisions:
                visit(parent_id)

        visit("0deltaattempt01")
        self.assertIn("0deltaattempt01", ancestors)
        self.assertIn("0deltaguard01", ancestors)
        self.assertIn("0deltanoncebind01", ancestors)
        self.assertIn("0deltacutover01", ancestors)
        self.assertIn("0deltanonce01", ancestors)
        self.assertIn("0deltasource01", ancestors)
        self.assertIn("0deltadelta01", ancestors)
        self.assertIn("f2c7d8e9a0b1", ancestors)
        self.assertIn("5061c56d11e7", ancestors)

    def test_downgrade_fails_closed_when_durable_delta_evidence_exists(self):
        path = Path(__file__).parents[1] / "migrations/versions/a1b2c3d4e5f6_add_object_delta_schema.py"
        text = path.read_text(encoding="utf-8")
        guard = "refusing destructive object-delta schema downgrade: durable rows exist"
        self.assertIn(guard, text)
        self.assertLess(text.index(guard), text.index('op.drop_table("object_delta_import_receipts")'))
        for table_name in (
            "object_delta_streams",
            "object_delta_outbox",
            "object_delta_receiver_cursors",
            "object_delta_import_receipts",
        ):
            self.assertIn(f"EXISTS (SELECT 1 FROM {table_name})", text)

    def test_nonce_downgrade_preserves_anti_replay_evidence(self):
        path = (
            Path(__file__).parents[1]
            / "migrations/versions/c3d4e5f6a7b8_add_object_delta_receiver_delivery_nonce_receipts.py"
        )
        text = path.read_text(encoding="utf-8")
        guard = "refusing destructive object-delta receiver delivery nonce downgrade: durable rows exist"
        self.assertIn(guard, text)
        self.assertIn(
            "EXISTS (SELECT 1 FROM object_delta_receiver_delivery_nonce_receipts)",
            text,
        )
        self.assertLess(
            text.index(guard),
            text.index('op.drop_table("object_delta_receiver_delivery_nonce_receipts")'),
        )

    def test_source_cutover_migration_is_schema_only_and_fails_closed_on_downgrade(self):
        path = (
            Path(__file__).parents[1]
            / "migrations/versions/d4e5f6a7b8c9_add_object_delta_source_cutover.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('revision: str = "0deltacutover01"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "0deltanonce01"', text)
        self.assertEqual(1, text.count("op.create_table("))
        self.assertNotIn("boto3", text)
        self.assertNotIn("subprocess", text)
        guard = "refusing destructive object-delta source cutover downgrade: durable rows exist"
        self.assertIn(guard, text)
        self.assertIn("EXISTS (SELECT 1 FROM object_delta_source_cutovers)", text)
        for column_name in (
            "snapshot_manifest_object_key",
            "snapshot_manifest_object_version_id",
            "snapshot_manifest_ciphertext_sha256",
            "snapshot_manifest_ciphertext_bytes",
            "baseline_manifest_object_key",
            "baseline_manifest_object_version_id",
            "baseline_manifest_ciphertext_sha256",
            "baseline_manifest_ciphertext_bytes",
        ):
            self.assertIn(f'sa.Column("{column_name}"', text)
        self.assertIn("ck_object_delta_source_cutovers_published_object_evidence", text)
        self.assertLess(
            text.index(guard),
            text.index('op.drop_table("object_delta_source_cutovers")'),
        )

    def test_nonce_import_binding_migration_is_schema_only_and_preserves_consumed_nonce_evidence(self):
        path = (
            Path(__file__).parents[1]
            / "migrations/versions/e5f6a7b8c9d0_add_object_delta_nonce_import_binding.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('revision: str = "0deltanoncebind01"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "0deltacutover01"', text)
        self.assertNotIn("boto3", text)
        self.assertNotIn("subprocess", text)
        self.assertIn("ux_object_delta_import_receipts_nonce_binding", text)
        self.assertIn("fk_od_rdnr_import_binding", text)
        guard = "refusing destructive object-delta nonce import-binding downgrade: durable nonce rows exist"
        self.assertIn(guard, text)
        self.assertIn(
            "EXISTS (SELECT 1 FROM object_delta_receiver_delivery_nonce_receipts)",
            text,
        )

    def test_append_only_guard_migration_hardens_source_rows_without_role_assumptions(self):
        path = (
            Path(__file__).parents[1]
            / "migrations/versions/f6a7b8c9d0e2_add_object_delta_source_append_only_guards.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('revision: str = "0deltaguard01"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "0deltanoncebind01"', text)
        self.assertNotIn("boto3", text)
        self.assertNotIn("subprocess", text)
        self.assertNotIn("CREATE ROLE", text)
        self.assertNotIn("\n        GRANT ", text)
        self.assertNotIn("\n        REVOKE ", text)
        self.assertNotIn("SECURITY DEFINER", text)

        self.assertIn("LOCK TABLE", text)
        self.assertIn("IN SHARE ROW EXCLUSIVE MODE", text)
        self.assertIn(
            "refusing Object-delta append-only guard upgrade: published source cutover evidence is incomplete",
            text,
        )
        self.assertIn(
            "refusing Object-delta append-only guard upgrade: outbox evidence lacks matching baseline_published source cutover",
            text,
        )
        self.assertIn("object_delta_guard_source_cutover_insert", text)
        self.assertIn("object_delta_guard_append_only_source_evidence", text)
        self.assertIn("object_delta_guard_outbox_published_cutover", text)
        self.assertEqual(6, text.count("CREATE TRIGGER"))
        self.assertEqual(6, text.count("DROP TRIGGER"))
        self.assertIn("BEFORE UPDATE OR DELETE ON object_delta_source_cutovers", text)
        self.assertIn("BEFORE TRUNCATE ON object_delta_source_cutovers", text)
        self.assertIn("BEFORE UPDATE OR DELETE ON object_delta_outbox", text)
        self.assertIn("BEFORE TRUNCATE ON object_delta_outbox", text)
        self.assertIn("BEFORE INSERT ON object_delta_source_cutovers", text)
        self.assertIn("BEFORE INSERT ON object_delta_outbox", text)
        self.assertIn("cutover_row.state = '{_PUBLISHED_STATE}'", text)
        self.assertIn("cutover_row.writer_epoch = NEW.writer_epoch", text)
        self.assertIn("cutover_row.writer_lease_id = NEW.writer_lease_id", text)
        for identity_column in (
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
        ):
            self.assertIn(
                f"cutover_row.{identity_column} = stream_row.{identity_column}",
                text,
            )
        for evidence_column in (
            "snapshot_manifest_object_key",
            "snapshot_manifest_object_version_id",
            "snapshot_manifest_ciphertext_sha256",
            "snapshot_manifest_ciphertext_bytes",
            "baseline_manifest_object_key",
            "baseline_manifest_object_version_id",
            "baseline_manifest_ciphertext_sha256",
            "baseline_manifest_ciphertext_bytes",
        ):
            self.assertIn(f"cutover_row.{evidence_column} IS NOT NULL", text)

        guard = "refusing destructive Object-delta append-only guard downgrade: durable source rows exist"
        self.assertIn(guard, text)
        self.assertIn("EXISTS (SELECT 1 FROM object_delta_streams)", text)
        self.assertIn("EXISTS (SELECT 1 FROM object_delta_source_cutovers)", text)
        self.assertIn("EXISTS (SELECT 1 FROM object_delta_outbox)", text)
        self.assertLess(text.index(guard), text.index("DROP TRIGGER"))

    def test_append_only_guard_migration_emits_closed_upgrade_and_fail_closed_downgrade(self):
        path = (
            Path(__file__).parents[1]
            / "migrations/versions/f6a7b8c9d0e2_add_object_delta_source_append_only_guards.py"
        )
        spec = importlib.util.spec_from_file_location("object_delta_append_only_guard", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        upgrade_sql: list[str] = []
        with patch.object(module.op, "execute", side_effect=upgrade_sql.append):
            module.upgrade()
        rendered_upgrade = "\n".join(upgrade_sql)
        self.assertTrue(upgrade_sql[0].lstrip().startswith("LOCK TABLE"))
        self.assertLess(
            rendered_upgrade.index("LOCK TABLE"),
            rendered_upgrade.index("CREATE FUNCTION object_delta_guard_source_cutover_insert"),
        )
        self.assertLess(
            rendered_upgrade.index("refusing Object-delta append-only guard upgrade"),
            rendered_upgrade.index("CREATE TRIGGER"),
        )
        self.assertIn("trg_object_delta_outbox_published_cutover", rendered_upgrade)
        self.assertIn("trg_object_delta_outbox_append_only_truncate", rendered_upgrade)

        downgrade_sql: list[str] = []
        with patch.object(module.op, "execute", side_effect=downgrade_sql.append):
            module.downgrade()
        rendered_downgrade = "\n".join(downgrade_sql)
        self.assertIn("refusing destructive Object-delta append-only guard downgrade", downgrade_sql[0])
        self.assertLess(
            rendered_downgrade.index("refusing destructive Object-delta append-only guard downgrade"),
            rendered_downgrade.index("DROP TRIGGER"),
        )
        self.assertIn("DROP FUNCTION object_delta_guard_outbox_published_cutover()", rendered_downgrade)

    def test_source_cutover_binds_the_full_stream_identity_and_state_gated_object_evidence(self):
        cutover = Base.metadata.tables["object_delta_source_cutovers"]
        stream = Base.metadata.tables["object_delta_streams"]

        self.assertEqual(
            {
                "object_delta_streams.id",
                "object_delta_streams.source_site",
                "object_delta_streams.destination_site",
                "object_delta_streams.campaign_id",
                "object_delta_streams.release_sha",
                "object_delta_streams.stream_generation_id",
            },
            {str(fk.target_fullname) for fk in cutover.foreign_keys},
        )
        unique_names = {constraint.name for constraint in cutover.constraints if constraint.name}
        self.assertIn("ux_object_delta_source_cutovers_stream", unique_names)
        self.assertIn("ux_object_delta_source_cutovers_identity", unique_names)
        self.assertIn("ux_object_delta_source_cutovers_write_gate", unique_names)
        self.assertIn(
            "ux_object_delta_streams_id_identity",
            {constraint.name for constraint in stream.constraints if constraint.name},
        )
        self.assertIsNone(cutover.c.write_gate_id.default)
        self.assertIsNone(cutover.c.write_gate_id.server_default)

        required_columns = {
            "registry_fingerprint",
            "writer_epoch",
            "writer_lease_id",
            "source_generation",
            "snapshot_id",
            "alembic_revision",
            "database_sha256",
            "uploads_sha256",
            "state",
            "created_at",
            "updated_at",
        }
        self.assertTrue(required_columns.issubset(cutover.c.keys()))
        for column_name in (
            "snapshot_manifest_object_key",
            "snapshot_manifest_object_version_id",
            "snapshot_manifest_ciphertext_sha256",
            "snapshot_manifest_ciphertext_bytes",
            "baseline_manifest_object_key",
            "baseline_manifest_object_version_id",
            "baseline_manifest_ciphertext_sha256",
            "baseline_manifest_ciphertext_bytes",
        ):
            self.assertTrue(cutover.c[column_name].nullable)
        for column_name in (
            "source_generation",
            "snapshot_id",
            "alembic_revision",
            "database_sha256",
            "uploads_sha256",
        ):
            self.assertFalse(cutover.c[column_name].nullable)

        checks = {
            constraint.name: str(constraint.sqltext)
            for constraint in cutover.constraints
            if getattr(constraint, "sqltext", None) is not None and constraint.name
        }
        self.assertIn("outbox_active_baseline_pending", checks["ck_object_delta_source_cutovers_state"])
        self.assertIn("baseline_published", checks["ck_object_delta_source_cutovers_state"])
        published_evidence = checks["ck_object_delta_source_cutovers_published_object_evidence"]
        for column_name in (
            "snapshot_manifest_object_key",
            "snapshot_manifest_object_version_id",
            "snapshot_manifest_ciphertext_sha256",
            "snapshot_manifest_ciphertext_bytes",
            "baseline_manifest_object_key",
            "baseline_manifest_object_version_id",
            "baseline_manifest_ciphertext_sha256",
            "baseline_manifest_ciphertext_bytes",
        ):
            self.assertIn(f"{column_name} IS NOT NULL", published_evidence)

    def test_critical_foreign_keys_and_idempotency_constraints_exist(self):
        outbox = Base.metadata.tables["object_delta_outbox"]
        self.assertEqual(
            {str(fk.target_fullname) for fk in outbox.foreign_keys},
            {"object_delta_streams.id", "change_log.id"},
        )
        receipt = Base.metadata.tables["object_delta_import_receipts"]
        unique_names = {constraint.name for constraint in receipt.constraints if constraint.name}
        self.assertIn("ux_object_delta_import_receipts_object_version", unique_names)
        self.assertIn("ux_object_delta_import_receipts_stream_first_sequence", unique_names)
        self.assertIn("ux_object_delta_import_receipts_nonce_binding", unique_names)
        nonce_receipt = Base.metadata.tables["object_delta_receiver_delivery_nonce_receipts"]
        nonce_unique_names = {
            constraint.name for constraint in nonce_receipt.constraints if constraint.name
        }
        self.assertIn(
            "ux_od_rdnr_controller_nonce",
            nonce_unique_names,
        )
        expected_nonce_import_binding = (
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            "writer_epoch",
            "writer_lease_id",
            "first_sequence",
            "last_sequence",
            "batch_sha256",
            "object_key",
            "object_version_id",
        )
        self.assertEqual(
            {
                f"object_delta_import_receipts.{column_name}"
                for column_name in expected_nonce_import_binding
            },
            {str(fk.target_fullname) for fk in nonce_receipt.foreign_keys},
        )
        foreign_keys = [
            constraint
            for constraint in nonce_receipt.constraints
            if constraint.name == "fk_od_rdnr_import_binding"
        ]
        self.assertEqual(1, len(foreign_keys))
        self.assertEqual(
            expected_nonce_import_binding,
            tuple(element.parent.name for element in foreign_keys[0].elements),
        )
        self.assertEqual(
            expected_nonce_import_binding,
            tuple(element.column.name for element in foreign_keys[0].elements),
        )

    def test_object_delta_named_schema_objects_fit_postgresql_identifier_limit(self):
        """Keep explicit model names usable by PostgreSQL's 63-byte limit."""

        names: set[str] = set()
        for table_name, table in Base.metadata.tables.items():
            if not table_name.startswith("object_delta_"):
                continue
            names.update(
                item.name
                for item in (*table.constraints, *table.indexes)
                if isinstance(item.name, str)
            )
        self.assertTrue(names)
        self.assertEqual(
            set(),
            {name for name in names if len(name.encode("utf-8")) > 63},
        )

    def test_object_delta_migration_identifiers_fit_postgresql_identifier_limit(self):
        """Migrations use explicit names too, so metadata coverage alone is insufficient."""

        versions = Path(__file__).parents[1] / "migrations/versions"
        names: set[str] = set()
        for path in versions.glob("*_object_delta_*.py"):
            names.update(
                re.findall(
                    r'[\"\']((?:ck|fk|ix|ux)_[A-Za-z0-9_]+)[\"\']',
                    path.read_text(encoding="utf-8"),
                )
            )
        self.assertTrue(names)
        self.assertEqual(
            set(),
            {name for name in names if len(name.encode("utf-8")) > 63},
        )


if __name__ == "__main__":
    unittest.main()
