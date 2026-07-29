from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class ProductionShadowParityImportTests(unittest.TestCase):
    def _isolated_python(self, code: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", code],
            cwd=cwd,
            env={
                "PATH": os.defpath,
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )

    def test_pure_parity_module_imports_without_site_packages(self) -> None:
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(REPO_ROOT)!r}); "
            "import core.production_shadow_parity as parity; "
            "assert parity.SYNC_PARITY_SCHEMA_VERSION == 1; "
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as directory:
            completed = self._isolated_python(code, cwd=Path(directory))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "ok\n")

    def test_source_set_imports_do_not_require_sqlalchemy_or_pyyaml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stubs = root / "stubs"
            self._write_cryptography_stubs(stubs)
            code = "\n".join(
                (
                    "import builtins, sys",
                    f"sys.path[:0] = [{str(stubs)!r}, {str(REPO_ROOT)!r}]",
                    "real_import = builtins.__import__",
                    "forbidden = ('sqlalchemy', 'yaml', 'models', "
                    "'scripts.orchestrate_production_shadow_prepared_clone_inventory')",
                    "def guarded(name, globals=None, locals=None, fromlist=(), level=0):",
                    "    if any(name == item or name.startswith(item + '.') for item in forbidden):",
                    "        raise AssertionError('forbidden import: ' + name)",
                    "    return real_import(name, globals, locals, fromlist, level)",
                    "builtins.__import__ = guarded",
                    "import scripts.produce_production_shadow_convergence_source_set",
                    "import scripts.orchestrate_production_shadow_convergence_gate",
                    "assert 'scripts.orchestrate_production_shadow_prepared_clone_inventory' not in sys.modules",
                    "print('ok')",
                )
            )
            completed = self._isolated_python(code, cwd=root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "ok\n")

    def test_sync_parity_reexports_pure_snapshot_functions(self) -> None:
        from core import production_shadow_parity
        from core import sync_parity

        self.assertIs(
            sync_parity.business_snapshot_fingerprint,
            production_shadow_parity.business_snapshot_fingerprint,
        )
        self.assertIs(
            sync_parity.compare_parity_snapshots,
            production_shadow_parity.compare_parity_snapshots,
        )

    @staticmethod
    def _write_cryptography_stubs(root: Path) -> None:
        files = {
            "cryptography/__init__.py": "",
            "cryptography/exceptions.py": "class InvalidSignature(Exception):\n    pass\n",
            "cryptography/hazmat/__init__.py": "",
            "cryptography/hazmat/primitives/__init__.py": "",
            "cryptography/hazmat/primitives/asymmetric/__init__.py": "",
            "cryptography/hazmat/primitives/asymmetric/ed25519.py": (
                "class Ed25519PrivateKey:\n    pass\n\n"
                "class Ed25519PublicKey:\n    pass\n"
            ),
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
