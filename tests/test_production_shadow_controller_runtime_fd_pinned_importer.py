from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/production_shadow_controller_runtime_fd_pinned_importer.py"
SPEC = importlib.util.spec_from_file_location(
    "controller_runtime_fd_pinned_importer_under_test",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FdPinnedScriptsImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fd-pinned-scripts-importer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "release"
        self.release.mkdir(mode=0o700)
        self.uid = os.geteuid()
        self._write_pre_runtime_release()
        self._clean_scripts_modules()
        self.addCleanup(self._clean_scripts_modules)

    def _clean_scripts_modules(self) -> None:
        for name in tuple(sys.modules):
            if name == "scripts" or name.startswith("scripts."):
                sys.modules.pop(name, None)

    def _write(self, relative: str, payload: bytes) -> None:
        path = self.release / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        cursor = path.parent
        while cursor != self.release.parent:
            cursor.chmod(0o700)
            if cursor == self.release:
                break
            cursor = cursor.parent
        path.write_bytes(payload)
        path.chmod(0o600)

    def _write_pre_runtime_release(self) -> None:
        for relative in sorted(MODULE.PRE_RUNTIME_SOURCE_PATHS):
            self._write(relative, (ROOT / relative).read_bytes())

    def _source_path(self, relative: str) -> Path:
        return self.release / relative

    def _append_source(self, relative: str, payload: str) -> None:
        path = self._source_path(relative)
        self._write(relative, path.read_bytes() + payload.encode("utf-8"))

    def _sources(
        self,
        *,
        override: dict[str, str] | None = None,
        additional: tuple[str, ...] = (),
    ) -> list[object]:
        override = override or {}
        result: list[object] = []
        for relative in sorted((*MODULE.PRE_RUNTIME_SOURCE_PATHS, *additional)):
            path = self.release / relative
            digest = override.get(relative, hashlib.sha256(path.read_bytes()).hexdigest())
            result.append(MODULE.FdPinnedScriptsSource(relative, digest))
        return result

    def _map(
        self,
        *,
        override: dict[str, str] | None = None,
        additional: tuple[str, ...] = (),
    ) -> object:
        descriptor = os.open(
            self.release,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            return MODULE.FdPinnedScriptsModuleMap(
                release_descriptor=descriptor,
                sources=self._sources(override=override, additional=additional),
                expected_uid=self.uid,
            )
        finally:
            os.close(descriptor)

    def _assert_session_torn_down(self, source_map: object) -> None:
        self.assertFalse(source_map.installed)
        self.assertNotIn(source_map, sys.meta_path)
        self.assertFalse(any(name == "scripts" or name.startswith("scripts.") for name in sys.modules))

    def _assert_constructor_rejects_injected_bootstrap(self, payload: str, expression: str) -> None:
        self._append_source(
            "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py",
            payload,
        )
        with self.assertRaisesRegex(MODULE.FdPinnedScriptsImportError, expression):
            self._map()

    def test_admits_exact_real_pre_runtime_source_set_before_execution(self) -> None:
        source_map = self._map()
        before_path = tuple(sys.path)
        self.addCleanup(source_map.close)
        source_map.install()

        bootstrap = source_map.import_module(
            "scripts.production_shadow_convergence_source_set_runtime_bootstrap"
        )
        builder = source_map.import_module(
            "scripts.build_production_shadow_controller_runtime_closure"
        )

        package = sys.modules["scripts"]
        verifier = sys.modules[
            "scripts.verify_production_shadow_controller_runtime_closure"
        ]
        self.assertEqual(bootstrap.__package__, "scripts")
        self.assertEqual(builder.__package__, "scripts")
        self.assertIs(builder.VERIFY, verifier)
        self.assertEqual(package.__path__, [])
        self.assertEqual(tuple(sys.path), before_path)
        self.assertTrue(str(builder.__file__).startswith("/proc/self/fd/"))
        source_map.assert_loaded_provenance()

    def test_unknown_scripts_module_cannot_fall_through_to_pathfinder_and_cleans_up(self) -> None:
        source_map = self._map()
        fallback = self.root / "fallback"
        self._write_fallback_module(fallback)
        with mock.patch.object(sys, "path", [str(fallback), *sys.path]):
            source_map.install()
            source_map.import_module(
                "scripts.production_shadow_convergence_source_set_runtime_bootstrap"
            )
            with self.assertRaisesRegex(ModuleNotFoundError, "absent from the exact FD-pinned source map"):
                importlib.import_module("scripts.fallback")
        self._assert_session_torn_down(source_map)

    def test_every_source_digest_is_checked_before_any_source_can_execute(self) -> None:
        bootstrap = "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py"
        with self.assertRaisesRegex(MODULE.FdPinnedScriptsImportError, "digest differs"):
            self._map(override={bootstrap: "f" * 64})

    def test_replaced_mapped_source_fails_before_execution_and_cleans_up(self) -> None:
        source_map = self._map()
        marker = self.root / "replacement-executed"
        replacement = self.root / "replacement.py"
        replacement.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed', encoding='ascii')\n",
            encoding="ascii",
        )
        replacement.chmod(0o600)
        os.replace(
            replacement,
            self.release / "scripts/build_production_shadow_controller_runtime_closure.py",
        )
        source_map.install()

        with self.assertRaisesRegex(MODULE.FdPinnedScriptsImportError, "digest differs"):
            source_map.import_module("scripts.build_production_shadow_controller_runtime_closure")

        self.assertFalse(marker.exists())
        self._assert_session_torn_down(source_map)

    def test_invalid_source_is_rejected_before_finder_installation(self) -> None:
        self._write(
            "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py",
            b"def not_valid(:\n",
        )

        with self.assertRaisesRegex(MODULE.FdPinnedScriptsImportError, "not valid UTF-8 Python"):
            self._map()

    def test_preloaded_scripts_namespace_is_rejected_before_finder_install(self) -> None:
        source_map = self._map()
        sys.modules["scripts"] = ModuleType("scripts")
        try:
            with self.assertRaisesRegex(MODULE.FdPinnedScriptsImportError, "already preloaded"):
                source_map.install()
        finally:
            sys.modules.pop("scripts", None)
            source_map.close()

    def test_map_requires_the_exact_pre_runtime_source_set(self) -> None:
        descriptor = os.open(
            self.release,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        self.addCleanup(os.close, descriptor)
        package = self.release / "scripts/__init__.py"
        with self.assertRaisesRegex(MODULE.FdPinnedScriptsImportError, "exact pre-runtime-only source set"):
            MODULE.FdPinnedScriptsModuleMap(
                release_descriptor=descriptor,
                sources=[
                    MODULE.FdPinnedScriptsSource(
                        "scripts/__init__.py",
                        hashlib.sha256(package.read_bytes()).hexdigest(),
                    )
                ],
                expected_uid=self.uid,
            )

    def test_post_runtime_sources_are_explicitly_unavailable(self) -> None:
        producer = "scripts/produce_production_shadow_convergence_source_set.py"
        self._write(producer, b"VALUE = 'post-runtime'\n")

        with self.assertRaisesRegex(MODULE.FdPinnedScriptsImportError, "post-runtime scripts are unavailable"):
            self._map(additional=(producer,))

    def test_policy_rejects_import_outside_stdlib_or_exact_scripts_map_before_execution(self) -> None:
        self._assert_constructor_rejects_injected_bootstrap(
            "\nfrom core import escape\n",
            "outside the stdlib/scripts allowlist",
        )

    def test_policy_rejects_unmapped_scripts_import_before_execution(self) -> None:
        self._assert_constructor_rejects_injected_bootstrap(
            "\nfrom scripts import produce_production_shadow_convergence_source_set\n",
            "scripts package import is absent",
        )

    def test_policy_rejects_dynamic_loaders_before_execution(self) -> None:
        cases = {
            "builtin-eval": "\neval('1 + 1')\n",
            "builtin-exec": "\nexec('VALUE = 1')\n",
            "builtin-compile": "\ncompile('VALUE = 1', '<test>', 'exec')\n",
            "builtin-import": "\n__import__('core.escape')\n",
            "runpy": "\nimport runpy\nrunpy.run_path('/tmp/blocked.py')\n",
            "importlib-spec": (
                "\nimport importlib.util\n"
                "importlib.util.spec_from_file_location('blocked', '/tmp/blocked.py')\n"
            ),
            "source-loader-import": "\nfrom importlib.machinery import SourceFileLoader\n",
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                self._write_pre_runtime_release()
                self._assert_constructor_rejects_injected_bootstrap(
                    payload,
                    "dynamic (loader|import)",
                )

    def test_policy_rejects_sys_import_state_mutations_before_execution(self) -> None:
        cases = {
            "path-method": "\nimport sys\nsys.path.insert(0, '/unsafe')\n",
            "meta-path-method": "\nimport sys\nsys.meta_path.pop(0)\n",
            "modules-assignment": "\nimport sys\nsys.modules['unsafe'] = None\n",
            "modules-delete": "\nimport sys\ndel sys.modules['unsafe']\n",
            "modules-method": "\nimport sys\nsys.modules.clear()\n",
            "path-alias": "\nimport sys\nunsafe_path = sys.path\nunsafe_path.append('/unsafe')\n",
            "sys-import-alias": "\nfrom sys import path as unsafe_path\n",
            "dynamic-sys-access": "\nimport sys\ngetattr(sys, 'path').append('/unsafe')\n",
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                self._write_pre_runtime_release()
                self._assert_constructor_rejects_injected_bootstrap(
                    payload,
                    "protected sys import state",
                )

    def test_policy_rejects_direct_release_path_escapes_before_execution(self) -> None:
        cases = {
            "bare-file": "\nfrom pathlib import Path\nPath(__file__).resolve()\n",
            "scripts-module-file": "\nimport scripts as current\ncurrent.__file__\n",
            "modules-module-file": "\nimport sys\nsys.modules[__name__].__file__\n",
            "fd-literal": "\nunsafe = '/proc/self/fd/999'\n",
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                self._write_pre_runtime_release()
                self._assert_constructor_rejects_injected_bootstrap(
                    payload,
                    "direct release (module|descriptor) path",
                )

    def test_package_cannot_replace_its_empty_path_before_execution(self) -> None:
        self._append_source("scripts/__init__.py", "\n__path__.append('/unsafe')\n")

        with self.assertRaisesRegex(MODULE.FdPinnedScriptsImportError, "direct release module path"):
            self._map()

    def test_primitive_has_no_project_import_or_sys_path_mutation(self) -> None:
        source = MODULE_PATH.read_text(encoding="ascii")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse(
            any(name == "scripts" or name.startswith("scripts.") for name in imported)
        )
        self.assertFalse(any(name == "core" or name.startswith("core.") for name in imported))
        sys_path_mutations = {
            "append",
            "clear",
            "extend",
            "insert",
            "pop",
            "remove",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = node.func.value
                self.assertFalse(
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "sys"
                    and target.attr == "path"
                    and node.func.attr in sys_path_mutations
                )
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                self.assertFalse(
                    any(
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "sys"
                        and target.attr == "path"
                        for target in targets
                    )
                )
        self.assertIn("pre-runtime", source)
        dynamic_calls = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                dynamic_calls.add(node.func.id)
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
            ):
                dynamic_calls.add(f"{node.func.value.id}.{node.func.attr}")
        self.assertNotIn("runpy.run_path", dynamic_calls)
        self.assertNotIn("importlib.util.spec_from_file_location", dynamic_calls)

    @staticmethod
    def _write_fallback_module(root: Path) -> None:
        module = root / "scripts/fallback.py"
        module.parent.mkdir(parents=True)
        (module.parent / "__init__.py").write_text("\n", encoding="ascii")
        module.write_text("VALUE = 'fallback'\n", encoding="ascii")


if __name__ == "__main__":
    unittest.main()
