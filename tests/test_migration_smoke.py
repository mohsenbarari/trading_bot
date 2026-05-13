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


if __name__ == '__main__':
    unittest.main()