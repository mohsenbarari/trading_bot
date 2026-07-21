import configparser
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_BIN = sys.executable


class MigrationSmokeTests(unittest.TestCase):
    def test_alembic_ini_points_to_migrations_script_location(self):
        parser = configparser.ConfigParser()
        parser.read(REPO_ROOT / 'alembic.ini')

        self.assertEqual(parser.get('alembic', 'script_location'), 'migrations')

    def test_alembic_and_migration_python_files_compile(self):
        result = subprocess.run(
            [PYTHON_BIN, '-m', 'compileall', '-q', 'alembic', 'migrations'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_alembic_heads_command_loads_revision_graph(self):
        result = subprocess.run(
            [PYTHON_BIN, '-m', 'alembic', '-c', 'alembic.ini', 'heads'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        heads = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(heads, ['f764a5b6c8d9 (head)'])

    def test_executed_e653_revision_is_immutable_and_remediation_is_forward_only(self):
        versions = REPO_ROOT / 'migrations' / 'versions'
        executed = versions / 'e653f4a5b7c8_close_three_site_database_trust_gaps.py'
        child = versions / 'f764a5b6c8d9_seal_dr_event_stream_contract.py'

        self.assertEqual(
            hashlib.sha256(executed.read_bytes()).hexdigest(),
            '4507368ecb8b35034d90e3eed8d99fa20a3c8c0abff27c89cb1799b8f9359eaf',
        )
        child_source = child.read_text(encoding='utf-8')
        self.assertIn('down_revision = "e653f4a5b7c8"', child_source)
        self.assertIn("jsonb_typeof(value) <> 'number'", child_source)
        self.assertIn('HISTORY_PREFLIGHT_SQL', child_source)
        self.assertIn('trg_dr_event_destination_binding', child_source)
        self.assertIn('op.execute(f"REVOKE ALL ON FUNCTION {function_identity} FROM PUBLIC")', child_source)

    def test_writer_trigger_uses_database_boottime_not_wall_clock(self):
        migration = (
            REPO_ROOT
            / 'migrations/versions/e4f9a0b1c2d3_use_database_boottime_writer_fence.py'
        ).read_text(encoding='utf-8')

        self.assertIn('CREATE EXTENSION IF NOT EXISTS trading_bot_boottime', migration)
        self.assertIn('trading_bot_boottime_seconds()', migration)
        self.assertIn('trading_bot_boot_id()', migration)
        upgrade_body = migration.split('def upgrade()', 1)[1].split('def downgrade()', 1)[0]
        self.assertNotIn('clock_timestamp()', upgrade_body)

    def test_three_site_trigger_functions_never_inherit_public_execute(self):
        expected = {
            'e4f9a0b1c2d3_use_database_boottime_writer_fence.py': (
                'trading_bot_enforce_writer_term()',
            ),
            'e5a0b1c2d3e4_add_durable_effect_fanouts.py': (
                'trading_bot_dr_effect_fanout_intent_immutable()',
            ),
            'e6b1c2d3e4f5_add_dr_transaction_envelopes.py': (
                'trading_bot_dr_event_immutable()',
                'trading_bot_dr_event_finalized()',
            ),
            'e8d3e4f5a6b7_enforce_database_event_coverage.py': (
                'trading_bot_require_same_transaction_dr_event()',
                'trading_bot_dr_event_immutable()',
            ),
            'e9e4f5a6b7c8_enable_bot_database_event_fence.py': (
                'trading_bot_enforce_writer_term()',
                'trading_bot_require_same_transaction_dr_event()',
            ),
            'c431d2e3f5a6_reconcile_integrated_database_policy.py': (
                'trading_bot_dr_event_finalized()',
                'trading_bot_enforce_writer_term()',
                'trading_bot_require_same_transaction_dr_event()',
            ),
            'd542e3f4a6b7_harden_dr_event_integrity_and_role_boundaries.py': (
                'trading_bot_cleanup_expired_replay_nonces(timestamptz, integer)',
                'trading_bot_dr_event_integrity_valid(text)',
                'trading_bot_require_same_transaction_dr_event()',
                'trading_bot_reject_receiver_source_xid()',
            ),
        }
        for filename, functions in expected.items():
            source = (REPO_ROOT / 'migrations/versions' / filename).read_text(encoding='utf-8')
            for function in functions:
                self.assertIn(
                    f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC',
                    source,
                    msg=f'{filename}:{function}',
                )

    def test_runtime_role_scripts_revoke_global_function_defaults(self):
        for filename in (
            'activate_three_site_database_fencing.py',
            'provision_bot_database_roles.py',
        ):
            source = (REPO_ROOT / 'scripts' / filename).read_text(encoding='utf-8')
            self.assertIn('REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC', source)
            self.assertNotIn(
                'IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC',
                source,
            )

    def test_webapp_writer_migration_bootstraps_current_fi_writer(self):
        migration = (
            REPO_ROOT
            / 'migrations/versions/d1c6e7f8a9b0_add_webapp_writer_fencing_foundation.py'
        ).read_text(encoding='utf-8')

        self.assertIn('webapp_writer_state', migration)
        self.assertIn('webapp_writer_transitions', migration)
        self.assertIn("'webapp_fi', 1, 'active'", migration)
        self.assertIn('ck_webapp_writer_state_active_consistency', migration)

    def test_webapp_writer_witness_migration_is_additive_and_bootstraps_vacant(self):
        migration = (
            REPO_ROOT
            / 'migrations/versions/d2e7f8a9b0c1_add_webapp_writer_witness_lease.py'
        ).read_text(encoding='utf-8')

        self.assertIn('down_revision: Union[str, Sequence[str], None] = "d1c6e7f8a9b0"', migration)
        self.assertIn('webapp_writer_witness_state', migration)
        self.assertIn('webapp_writer_witness_receipts', migration)
        self.assertIn("'webapp', NULL, 0, NULL, 'vacant'", migration)
        self.assertIn('lease_refresh', migration)
        self.assertIn('witness_proof_hash', migration)

    def test_offer_request_ledger_enum_migration_is_idempotent(self):
        migration = (REPO_ROOT / 'migrations/versions/c8d9e0f1a2b3_add_offer_request_ledger.py').read_text(
            encoding='utf-8'
        )

        self.assertIn('from sqlalchemy.dialects import postgresql', migration)
        self.assertGreaterEqual(migration.count('postgresql.ENUM('), 2)
        self.assertGreaterEqual(migration.count('create_type=False'), 2)
        self.assertIn('offer_request_status.create(bind, checkfirst=True)', migration)
        self.assertIn('offer_request_source_surface.create(bind, checkfirst=True)', migration)

    def test_offer_publication_state_enum_migration_is_idempotent(self):
        migration = (REPO_ROOT / 'migrations/versions/d0e1f2a3b4c6_add_offer_publication_states.py').read_text(
            encoding='utf-8'
        )

        self.assertIn('from sqlalchemy.dialects import postgresql', migration)
        self.assertGreaterEqual(migration.count('postgresql.ENUM('), 2)
        self.assertGreaterEqual(migration.count('create_type=False'), 2)
        self.assertIn('offer_publication_surface.create(bind, checkfirst=True)', migration)
        self.assertIn('offer_publication_status.create(bind, checkfirst=True)', migration)

    def test_offer_trade_settlement_migration_is_additive_and_backfills_cash(self):
        migration = (
            REPO_ROOT / 'migrations/versions/b9e0f1a2c3d4_add_offer_trade_settlement_type.py'
        ).read_text(encoding='utf-8')

        self.assertIn('postgresql.ENUM(', migration)
        self.assertIn('create_type=False', migration)
        self.assertIn('settlement_type.create(bind, checkfirst=True)', migration)
        self.assertIn('for table_name in ("offers", "trades")', migration)
        self.assertIn('nullable=False', migration)
        self.assertIn("server_default=sa.text(\"'CASH'::settlementtype\")", migration)

    def test_offer_republish_provenance_migration_merges_heads_and_backfills_lineage(self):
        migration = (
            REPO_ROOT / "migrations/versions/d0b5e6f7a8c9_add_offer_republish_provenance.py"
        ).read_text(encoding="utf-8")

        self.assertIn('(\"c7d8e9f0a1b4\", \"c9a4e7b2d615\")', migration)
        self.assertIn("republished_from_offer_public_id", migration)
        self.assertIn("source.republished_offer_public_id = replacement.offer_public_id", migration)
        self.assertIn("unique=True", migration)

    def test_offer_republish_per_home_migration_replaces_global_unique_index(self):
        migration = (
            REPO_ROOT / "migrations/versions/f2c7d8e9a0b1_allow_offer_republish_per_home.py"
        ).read_text(encoding="utf-8")

        self.assertIn('down_revision = "f1b6e7f8a9dc"', migration)
        self.assertIn('op.drop_index(OLD_INDEX, table_name="offers")', migration)
        self.assertIn(
            '["republished_from_offer_public_id", "home_server"]',
            migration,
        )
        self.assertIn("unique=True", migration)
        self.assertIn("must not discard either independent offer", migration)

    def test_new_offer_public_id_backfills_cannot_use_independent_random_values(self):
        allowed_legacy_random_backfills = {
            'a6b7c8d9e0f1_add_offer_public_id.py',
        }
        offenders = []

        for migration_path in (REPO_ROOT / 'migrations' / 'versions').glob('*.py'):
            source = migration_path.read_text(encoding='utf-8')
            if 'offer_public_id' not in source:
                continue
            uses_independent_randomness = 'random()' in source or 'clock_timestamp()' in source
            if uses_independent_randomness and migration_path.name not in allowed_legacy_random_backfills:
                offenders.append(migration_path.name)

        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()
