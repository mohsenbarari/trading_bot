from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock

from scripts import collect_production_shadow_compose_runtime_snapshot as MODULE
from scripts import collect_three_site_staging_convergence_snapshot as LEGACY


CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
PLAN_SHA256 = "b" * 64


class ContainerCollectorEntrypointTests(unittest.TestCase):
    def _source_manifest(self, root: Path, *, files: dict[str, bytes] | None = None) -> Path:
        entries = files or {
            "scripts/collect_production_shadow_compose_runtime_snapshot.py": b"# wrapper\n",
            "scripts/collect_three_site_staging_convergence_snapshot.py": b"# delegate\n",
            "core/__init__.py": b"\n",
            "core/safe.py": b"VALUE = 7\n",
            "models/__init__.py": b"\n",
        }
        for relative, payload in entries.items():
            path = root / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o644)
        document: dict[str, object] = {
            "schema": LEGACY.CONTAINER_SOURCE_MANIFEST_SCHEMA,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": "c" * 40,
            "files": {
                relative: hashlib.sha256(payload).hexdigest()
                for relative, payload in entries.items()
            },
        }
        document["source_manifest_sha256"] = hashlib.sha256(
            json.dumps(
                document,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
        path = root.parent / "collector-source-manifest.json"
        path.write_bytes(
            json.dumps(
                document,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        )
        path.chmod(0o600)
        return path

    @contextlib.contextmanager
    def _clean_project_modules(self):
        saved = {
            name: module
            for name, module in sys.modules.items()
            if name.split(".", maxsplit=1)[0] in {"core", "models"}
        }
        for name in list(saved):
            sys.modules.pop(name, None)
        try:
            yield
        finally:
            for name in list(sys.modules):
                if name.split(".", maxsplit=1)[0] in {"core", "models"}:
                    sys.modules.pop(name, None)
            sys.modules.update(saved)

    def test_rejects_missing_or_invalid_required_arguments(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                MODULE._arguments([])
        with self.assertRaisesRegex(RuntimeError, "arguments are invalid"):
            MODULE._arguments([
                "--campaign-id", CAMPAIGN_ID,
                "--release-sha", RELEASE_SHA,
                "--plan-sha256", PLAN_SHA256,
                "--max-rows-per-table", "0",
                "--source-manifest-path", "/run/collector-source-manifest.json",
            ])

    def test_full_fixed_collector_arguments_bind_dynamic_plan_values(self) -> None:
        source_manifest = "/root/secure-envs/campaign/collector-source-manifest.json"
        arguments = MODULE._arguments([
            "--campaign-id", CAMPAIGN_ID,
            "--release-sha", RELEASE_SHA,
            "--source-manifest-path", source_manifest,
            "--plan-sha256", PLAN_SHA256,
            "--max-rows-per-table", "10",
        ])
        self.assertEqual(arguments.campaign_id, CAMPAIGN_ID)
        self.assertEqual(arguments.release_sha, RELEASE_SHA)
        self.assertEqual(arguments.source_manifest_path, source_manifest)
        self.assertEqual(arguments.plan_sha256, PLAN_SHA256)
        self.assertEqual(arguments.max_rows_per_table, 10)

    def test_uses_release_local_container_safe_collector_without_host_fds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / RELEASE_SHA
            script = root / "scripts" / "collect_production_shadow_compose_runtime_snapshot.py"
            script.parent.mkdir(mode=0o700, parents=True)
            script.write_text("# path anchor only\n", encoding="ascii")
            calls: list[dict[str, object]] = []
            delegate = ModuleType("scripts.collect_three_site_staging_convergence_snapshot")

            async def collect_container_safe(**kwargs):  # noqa: ANN003
                calls.append(kwargs)
                return {"status": "ok"}

            delegate.collect_container_safe = collect_container_safe  # type: ignore[attr-defined]
            with (
                mock.patch.object(MODULE, "__file__", str(script)),
                mock.patch.dict(
                    "sys.modules",
                    {"scripts.collect_three_site_staging_convergence_snapshot": delegate},
                ),
                mock.patch("sys.stdout"),
            ):
                status = MODULE.main([
                    "--campaign-id", CAMPAIGN_ID,
                    "--release-sha", RELEASE_SHA,
                    "--plan-sha256", PLAN_SHA256,
                    "--max-rows-per-table", "10",
                    "--source-manifest-path", "/run/collector-source-manifest.json",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["release_root"], root)
            self.assertEqual(calls[0]["release_sha"], RELEASE_SHA)

    def test_does_not_reference_host_descriptor_environment(self) -> None:
        source = Path(MODULE.__file__).read_text(encoding="ascii")
        self.assertNotIn("HELD_RELEASE", source)
        self.assertNotIn("HELD_CONVERGENCE", source)

    def test_container_loader_rejects_preloaded_project_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / RELEASE_SHA
            legacy_path = root / "scripts" / "collect_three_site_staging_convergence_snapshot.py"
            legacy_path.parent.mkdir(mode=0o700, parents=True)
            legacy_path.write_text("# path anchor only\n", encoding="ascii")
            with (
                mock.patch.object(LEGACY, "__file__", str(legacy_path)),
                mock.patch.object(LEGACY, "_require_isolated_collector_interpreter"),
                mock.patch.dict("sys.modules", {"core": ModuleType("core")}),
            ):
                with self.assertRaisesRegex(
                    LEGACY.ConvergenceSnapshotError,
                    "preloaded project modules",
                ):
                    LEGACY.load_container_runtime_dependencies(
                        release_sha=RELEASE_SHA,
                        release_root=root,
                    )

    def test_container_loader_installs_trusted_roots_before_runtime_imports(self) -> None:
        source = Path(LEGACY.__file__).read_text(encoding="ascii")
        loader = source[source.index("def load_container_runtime_dependencies"):]
        self.assertLess(
            loader.index("_require_isolated_collector_interpreter()"),
            loader.index("from sqlalchemy import"),
        )
        self.assertLess(
            loader.index("_install_trusted_system_package_roots()"),
            loader.index("from sqlalchemy import"),
        )

    def test_manifest_finder_loads_only_verified_project_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._clean_project_modules():
            root = Path(temporary) / RELEASE_SHA
            root.mkdir(mode=0o700)
            manifest_path = self._source_manifest(root)
            finder = LEGACY._install_container_manifest_source_finder(
                source_manifest_path=manifest_path,
                release_root=root,
                release_sha=RELEASE_SHA,
            )
            try:
                module = importlib.import_module("core.safe")
                self.assertEqual(module.VALUE, 7)
                LEGACY._validate_container_manifest_imports(finder)
            finally:
                sys.meta_path.remove(finder)

    def test_manifest_finder_allows_zero_byte_regular_package_inits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._clean_project_modules():
            root = Path(temporary) / RELEASE_SHA
            root.mkdir(mode=0o700)
            manifest_path = self._source_manifest(
                root,
                files={
                    "scripts/collect_production_shadow_compose_runtime_snapshot.py": b"# wrapper\n",
                    "scripts/collect_three_site_staging_convergence_snapshot.py": b"# delegate\n",
                    "core/__init__.py": b"",
                    "core/safe.py": b"VALUE = 7\n",
                    "models/__init__.py": b"",
                },
            )
            finder = LEGACY._install_container_manifest_source_finder(
                source_manifest_path=manifest_path,
                release_root=root,
                release_sha=RELEASE_SHA,
            )
            try:
                self.assertEqual(importlib.import_module("core.safe").VALUE, 7)
                self.assertIsNotNone(importlib.import_module("models"))
            finally:
                sys.meta_path.remove(finder)

    def test_manifest_finder_rejects_untracked_and_extension_project_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._clean_project_modules():
            root = Path(temporary) / RELEASE_SHA
            root.mkdir(mode=0o700)
            manifest_path = self._source_manifest(root)
            (root / "core" / "evil.py").write_text("VALUE = 99\n", encoding="ascii")
            (root / "core" / "native.so").write_bytes(b"not an extension")
            finder = LEGACY._install_container_manifest_source_finder(
                source_manifest_path=manifest_path,
                release_root=root,
                release_sha=RELEASE_SHA,
            )
            try:
                with self.assertRaisesRegex(ModuleNotFoundError, "absent from the exact source manifest"):
                    importlib.import_module("core.evil")
                with self.assertRaisesRegex(ModuleNotFoundError, "absent from the exact source manifest"):
                    importlib.import_module("core.native")
            finally:
                sys.meta_path.remove(finder)

    def test_manifest_parser_rejects_preload_namespace_and_noncanonical_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._clean_project_modules():
            root = Path(temporary) / RELEASE_SHA
            root.mkdir(mode=0o700)
            manifest_path = self._source_manifest(root)
            with mock.patch.dict("sys.modules", {"core": ModuleType("core")}):
                with self.assertRaisesRegex(LEGACY.ConvergenceSnapshotError, "preloaded project modules"):
                    LEGACY._install_container_manifest_source_finder(
                        source_manifest_path=manifest_path,
                        release_root=root,
                        release_sha=RELEASE_SHA,
                    )
            raw = manifest_path.read_bytes()
            manifest_path.write_bytes(b"\n" + raw)
            with self.assertRaisesRegex(LEGACY.ConvergenceSnapshotError, "not canonical"):
                LEGACY._load_container_manifest_sources(
                    source_manifest_path=manifest_path,
                    release_root=root,
                    release_sha=RELEASE_SHA,
                )

    def test_manifest_parser_rejects_namespace_package_without_init(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._clean_project_modules():
            root = Path(temporary) / RELEASE_SHA
            root.mkdir(mode=0o700)
            entries = {
                "scripts/collect_production_shadow_compose_runtime_snapshot.py": b"# wrapper\n",
                "scripts/collect_three_site_staging_convergence_snapshot.py": b"# delegate\n",
                "core/__init__.py": b"\n",
                "models/record.py": b"VALUE = 1\n",
            }
            manifest_path = self._source_manifest(root, files=entries)
            with self.assertRaisesRegex(LEGACY.ConvergenceSnapshotError, "incomplete"):
                LEGACY._load_container_manifest_sources(
                    source_manifest_path=manifest_path,
                    release_root=root,
                    release_sha=RELEASE_SHA,
                )


if __name__ == "__main__":
    unittest.main()
