import ast
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_BIN = sys.executable
TOOL_PATHS = [
    Path('tests/api_load_test.py'),
    Path('tests/load_test.py'),
    Path('tests/live_simulation.py'),
    Path('tests/debug_trade.py'),
]
EXPECTED_PHRASE = 'manual non-regression tool'


class NonRegressionToolSmokeTests(unittest.TestCase):
    def test_manual_tools_compile_cleanly(self):
        result = subprocess.run(
            [PYTHON_BIN, '-m', 'py_compile', *[str(path) for path in TOOL_PATHS]],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_manual_tools_have_explicit_non_regression_docstrings(self):
        for path in TOOL_PATHS:
            with self.subTest(path=str(path)):
                module = ast.parse((REPO_ROOT / path).read_text(encoding='utf-8'))
                docstring = ast.get_docstring(module) or ''
                self.assertIn(EXPECTED_PHRASE, docstring.lower())


if __name__ == '__main__':
    unittest.main()