from pathlib import Path
import unittest

from core.sync_registry import SyncPolicy, get_sync_registry_entry
from models.telegram_delivery_provider_outcome import (
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_delivery_reconciliation_evidence import (
    TelegramDeliveryReconciliationEvidence,
)
from models.telegram_delivery_runtime_gate import TelegramDeliveryRuntimeGate
from models.telegram_delivery_job import TelegramDeliveryJobRecord


class TelegramProviderOutcomeSchemaTests(unittest.TestCase):
    def test_model_has_fenced_identity_pending_index_and_foreign_key(self):
        table = TelegramDeliveryProviderOutcomeRecord.__table__
        unique_names = {constraint.name for constraint in table.constraints}
        index_names = {index.name for index in table.indexes}
        foreign_keys = {str(foreign_key.column) for foreign_key in table.foreign_keys}

        self.assertIn("ux_telegram_delivery_provider_outcomes_fence", unique_names)
        self.assertIn("ix_telegram_delivery_provider_outcomes_pending", index_names)
        self.assertEqual(foreign_keys, {"telegram_delivery_jobs.id"})

    def test_table_is_explicit_foreign_local_no_sync_state(self):
        for table_name in {
            "telegram_delivery_provider_outcomes",
            "telegram_delivery_reconciliation_evidence",
            "telegram_delivery_runtime_gates",
        }:
            with self.subTest(table_name=table_name):
                entry = get_sync_registry_entry(table_name)
                self.assertEqual(entry.policy, SyncPolicy.NO_SYNC)
                self.assertIn("foreign local", entry.authority)

    def test_reconciliation_evidence_is_append_only_identity_audited(self):
        table = TelegramDeliveryReconciliationEvidence.__table__
        constraint_names = {constraint.name for constraint in table.constraints}
        index_names = {index.name for index in table.indexes}
        self.assertIn(
            "ux_telegram_delivery_reconciliation_evidence_identity",
            constraint_names,
        )
        self.assertIn(
            "ix_telegram_delivery_reconciliation_evidence_job",
            index_names,
        )

    def test_migration_is_linear_and_reversible(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/fc18b9d0e2f3_add_telegram_provider_outcome_inbox.py"
        ).read_text(encoding="utf-8")
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "fb07b8c9d0e1"', migration)
        self.assertIn('op.create_table(\n        "telegram_delivery_provider_outcomes"', migration)
        self.assertIn('op.drop_table("telegram_delivery_provider_outcomes")', migration)

        runtime_migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/fd29c0e1f3a4_add_telegram_runtime_gates.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "fc18b9d0e2f3"',
            runtime_migration,
        )
        self.assertIn('op.drop_table("telegram_delivery_runtime_gates")', runtime_migration)

        order_migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/fe30d1e2f4b5_add_offer_edit_global_order.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "fd29c0e1f3a4"',
            order_migration,
        )
        self.assertIn("source_order_at", order_migration)
        self.assertIn('op.drop_column("telegram_delivery_jobs", "source_order_at")', order_migration)

        membership_migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/ff41e2f3a5b6_add_channel_membership_saga.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "fe30d1e2f4b5"',
            membership_migration,
        )
        self.assertIn("telegram_channel_membership_sagas", membership_migration)
        self.assertIn('op.drop_table("telegram_channel_membership_sagas")', membership_migration)

        retention_migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/a052f3a4b6c7_add_telegram_queue_retention_controls.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "ff41e2f3a5b6"',
            retention_migration,
        )
        self.assertIn("retention_legal_hold", retention_migration)
        self.assertIn("payload_redacted_at", retention_migration)
        self.assertIn(
            'op.drop_column("telegram_delivery_jobs", "retention_legal_hold")',
            retention_migration,
        )

    def test_retention_hold_is_atomic_and_indexed(self):
        table = TelegramDeliveryJobRecord.__table__
        constraint_names = {constraint.name for constraint in table.constraints}
        index_names = {index.name for index in table.indexes}
        self.assertIn(
            "ck_telegram_delivery_jobs_retention_hold",
            constraint_names,
        )
        self.assertIn("ix_telegram_delivery_jobs_retention", index_names)
        self.assertIn(
            "payload_redacted_at",
            TelegramDeliveryProviderOutcomeRecord.__table__.columns,
        )

        transport_migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/a163f4a5b7c8_add_telegram_transport_attempt_facts.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "a052f3a4b6c7"',
            transport_migration,
        )
        self.assertIn("provider_attempt_count", transport_migration)
        self.assertIn("transport_phase", transport_migration)

        self.assertIn(
            "ck_telegram_delivery_jobs_provider_attempt_count",
            constraint_names,
        )
        provider_constraints = {
            constraint.name
            for constraint in TelegramDeliveryProviderOutcomeRecord.__table__.constraints
        }
        self.assertIn(
            "ck_telegram_delivery_provider_outcomes_transport_phase",
            provider_constraints,
        )

    def test_runtime_gate_has_strict_scope_and_identity_constraints(self):
        table = TelegramDeliveryRuntimeGate.__table__
        names = {constraint.name for constraint in table.constraints}
        self.assertIn("ck_telegram_delivery_runtime_gates_scope", names)
        self.assertIn("ck_telegram_delivery_runtime_gates_identity", names)
        self.assertIn("ck_telegram_delivery_runtime_gates_state", names)
        self.assertIn("resumed_job_ids", table.columns)


if __name__ == "__main__":
    unittest.main()
