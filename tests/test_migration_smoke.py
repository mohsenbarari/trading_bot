import configparser
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
        self.assertTrue(result.stdout.strip(), msg='Expected at least one Alembic head revision')

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
