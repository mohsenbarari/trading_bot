import importlib
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_BIN = sys.executable


class EntrypointSmokeTests(unittest.TestCase):
    def test_entrypoint_files_compile(self):
        result = subprocess.run(
            [PYTHON_BIN, '-m', 'py_compile', 'main.py', 'run_bot.py', 'manage.py'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_main_module_import_exposes_fastapi_app(self):
        main_module = importlib.import_module('main')

        self.assertTrue(hasattr(main_module, 'app'))
        self.assertEqual(main_module.app.title, 'Trading Bot API')

    def test_run_bot_and_manage_import_expose_main_functions(self):
        run_bot_module = importlib.import_module('run_bot')
        manage_module = importlib.import_module('manage')

        self.assertTrue(callable(run_bot_module.main))
        self.assertTrue(callable(manage_module.main))
        self.assertTrue(callable(manage_module.run_migrations))


if __name__ == '__main__':
    unittest.main()