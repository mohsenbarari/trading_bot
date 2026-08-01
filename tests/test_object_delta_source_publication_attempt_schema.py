from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from models.database import Base
import models.object_delta  # register composite stream identity target
import models.object_delta_source_batch  # register source ledger target
import models.object_delta_source_publication_attempt as publication_models


class ObjectDeltaSourcePublicationAttemptSchemaTests(unittest.TestCase):
    def test_models_register_immutable_reservation_and_ordered_evidence(self) -> None:
        expected = {
            "object_delta_source_publication_attempts",
            "object_delta_source_publication_seals",
            "object_delta_source_publication_receipts",
            "object_delta_source_publication_attestations",
            "object_delta_source_publication_ledger_bindings",
        }
        self.assertEqual(
            expected,
            {
                model.__tablename__
                for model in (
                    publication_models.ObjectDeltaSourcePublicationAttempt,
                    publication_models.ObjectDeltaSourcePublicationSeal,
                    publication_models.ObjectDeltaSourcePublicationReceipt,
                    publication_models.ObjectDeltaSourcePublicationAttestation,
                    publication_models.ObjectDeltaSourcePublicationLedgerBinding,
                )
            },
        )
        self.assertTrue(expected.issubset(Base.metadata.tables))

        attempt = Base.metadata.tables["object_delta_source_publication_attempts"]
        self.assertNotIn("state", attempt.c)
        self.assertNotIn("updated_at", attempt.c)
        self.assertTrue(
            {
                "attempt_id",
                "stream_id",
                "source_site",
                "destination_site",
                "campaign_id",
                "release_sha",
                "stream_generation_id",
                "writer_epoch",
                "writer_lease_id",
                "first_sequence",
                "last_sequence",
                "prior_chain_sha256",
                "payload_sha256",
                "payload_bytes",
                "object_key",
                "destination_age_recipient",
                "transport_policy_sha256",
                "source_cutover_artifact_sha256",
                "source_cutover_artifact_bytes",
                "created_at",
            }.issubset(attempt.c.keys())
        )

    def test_reservation_has_dual_unique_keys_and_composite_stream_identity(self) -> None:
        attempt = Base.metadata.tables["object_delta_source_publication_attempts"]
        unique_names = {
            constraint.name
            for constraint in attempt.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertTrue(
            {
                "ux_od_spa_attempt_id",
                "ux_od_spa_object_key",
                "ux_od_spa_attempt_object_key",
                "ux_od_spa_stream_first_sequence",
            }.issubset(unique_names)
        )
        self.assertIn("ix_od_spa_stream_first", {index.name for index in attempt.indexes})
        foreign_keys = {
            constraint.name: constraint
            for constraint in attempt.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        stream_fk = foreign_keys["fk_od_spa_stream_identity"]
        self.assertEqual(
            (
                "stream_id",
                "source_site",
                "destination_site",
                "campaign_id",
                "release_sha",
                "stream_generation_id",
            ),
            tuple(element.parent.name for element in stream_fk.elements),
        )

    def test_stage_foreign_keys_and_terminal_binding_are_one_way(self) -> None:
        expected_foreign_keys = {
            "object_delta_source_publication_seals": {"fk_od_sps_attempt"},
            "object_delta_source_publication_receipts": {
                "fk_od_spr_attempt_key",
                "fk_od_spr_seal_ciphertext",
            },
            "object_delta_source_publication_attestations": {"fk_od_spat_receipt"},
            "object_delta_source_publication_ledger_bindings": {
                "fk_od_splb_attestation",
                "fk_od_splb_source_ledger",
            },
        }
        for table_name, names in expected_foreign_keys.items():
            with self.subTest(table_name=table_name):
                table = Base.metadata.tables[table_name]
                self.assertEqual(
                    names,
                    {
                        constraint.name
                        for constraint in table.constraints
                        if isinstance(constraint, ForeignKeyConstraint)
                    },
                )
        bindings = Base.metadata.tables["object_delta_source_publication_ledger_bindings"]
        self.assertEqual(
            {"ux_od_splb_attempt", "ux_od_splb_source_ledger"},
            {
                constraint.name
                for constraint in bindings.constraints
                if isinstance(constraint, UniqueConstraint)
            },
        )
        checks = {
            constraint.name: str(constraint.sqltext)
            for table_name in (
                "object_delta_source_publication_attempts",
                "object_delta_source_publication_seals",
                "object_delta_source_publication_receipts",
                "object_delta_source_publication_attestations",
            )
            for constraint in Base.metadata.tables[table_name].constraints
            if getattr(constraint, "sqltext", None) is not None and constraint.name
        }
        self.assertIn("source_cutover_artifact_sha256", checks["ck_od_spa_hashes"])
        self.assertIn("ciphertext_sha256 = spool_sha256", checks["ck_od_sps_exact_spool"])
        self.assertIn("transport_receipt_artifact_sha256", checks["ck_od_spr_hashes"])
        self.assertIn("source_attestation_artifact_sha256", checks["ck_od_spat_hashes"])

    def test_model_and_migration_identifiers_fit_postgresql_limit(self) -> None:
        names: set[str] = set()
        for table_name, table in Base.metadata.tables.items():
            if table_name.startswith("object_delta_source_publication_"):
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

        path = (
            Path(__file__).parents[1]
            / "migrations/versions/g7a8b9c0d1e2_add_object_delta_source_publication_attempts.py"
        )
        text = path.read_text(encoding="utf-8")
        migration_names = set(
            re.findall(r'[\"\']((?:ck|fk|ix|ux)_[A-Za-z0-9_]+)[\"\']', text)
        )
        self.assertTrue(migration_names)
        self.assertEqual(
            set(),
            {name for name in migration_names if len(name.encode("utf-8")) > 63},
        )

    def test_migration_is_schema_only_and_enforces_exact_terminal_binding(self) -> None:
        path = (
            Path(__file__).parents[1]
            / "migrations/versions/g7a8b9c0d1e2_add_object_delta_source_publication_attempts.py"
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn('revision: str = "0deltaattempt01"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "0deltaguard01"', text)
        for forbidden in ("boto3", "requests", "subprocess", "socket", "httpx"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(5, text.count("op.create_table("))
        self.assertIn("object_delta_guard_append_only_source_pub_attempt", text)
        self.assertIn("object_delta_guard_source_pub_ledger_binding", text)
        self.assertIn("trg_od_splb_validate_ledger", text)
        for ledger_match in (
            "ledger_row.stream_id = attempt_row.stream_id",
            "ledger_row.writer_epoch = attempt_row.writer_epoch",
            "ledger_row.writer_lease_id = attempt_row.writer_lease_id",
            "ledger_row.first_sequence = attempt_row.first_sequence",
            "ledger_row.last_sequence = attempt_row.last_sequence",
            "ledger_row.payload_sha256 = attempt_row.payload_sha256",
            "ledger_row.object_version_id = receipt_row.object_version_id",
            "ledger_row.batch_sha256 = attestation_row.batch_sha256",
        ):
            self.assertIn(ledger_match, text)
        self.assertIn("char_length(object_key) BETWEEN 3 AND 1024", text)
        self.assertIn("char_length(object_version_id) BETWEEN 1 AND 1024", text)
        repetition_bounds = re.findall(r"\{(?:\d+,)?(\d+)\}", text)
        self.assertTrue(repetition_bounds)
        self.assertTrue(all(int(bound) <= 255 for bound in repetition_bounds))
        guard = (
            "refusing destructive object-delta source publication attempt downgrade: "
            "durable rows exist"
        )
        self.assertIn(guard, text)
        self.assertLess(text.index(guard), text.index("DROP TRIGGER"))

        spec = importlib.util.spec_from_file_location("object_delta_source_publication_attempts", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        created_tables: list[str] = []
        upgrade_sql: list[str] = []
        with (
            patch.object(
                module.op,
                "create_table",
                side_effect=lambda name, *args: created_tables.append(name),
            ),
            patch.object(module.op, "create_index"),
            patch.object(module.op, "execute", side_effect=upgrade_sql.append),
        ):
            module.upgrade()
        self.assertEqual(
            [
                "object_delta_source_publication_attempts",
                "object_delta_source_publication_seals",
                "object_delta_source_publication_receipts",
                "object_delta_source_publication_attestations",
                "object_delta_source_publication_ledger_bindings",
            ],
            created_tables,
        )
        self.assertEqual(11, "\n".join(upgrade_sql).count("CREATE TRIGGER"))


if __name__ == "__main__":
    unittest.main()
