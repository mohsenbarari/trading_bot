"""Regression tests for the default-off database-boundary source audit."""

from __future__ import annotations

import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from core.application_database_boundary_inventory import (
    APPLICATION_DATABASE_BOUNDARY_INVENTORY_SCHEMA,
    ApplicationDatabaseBoundaryInventoryError,
    REGISTERED_APPLICATION_DATABASE_BOUNDARIES,
    assert_application_database_boundary_inventory,
    assert_legacy_connection_module_is_unwired,
    discover_application_database_boundaries,
    run_application_database_boundary_audit,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ApplicationDatabaseBoundaryInventoryTests(unittest.TestCase):
    def test_checked_in_source_matches_the_literal_database_boundary_registry(self) -> None:
        discovered = run_application_database_boundary_audit(PROJECT_ROOT)

        self.assertEqual("application_database_boundary_inventory/v1", APPLICATION_DATABASE_BOUNDARY_INVENTORY_SCHEMA)
        self.assertEqual(15, len(discovered))
        self.assertEqual(15, len(REGISTERED_APPLICATION_DATABASE_BOUNDARIES))
        self.assertIn(
            (
                "core/db.py",
                "<module>",
                "sqlalchemy.create_async_engine",
                1,
            ),
            {boundary.identity for boundary in discovered},
        )
        self.assertIn(
            (
                "core/db.py",
                "<module>",
                "sqlalchemy.async_sessionmaker",
                1,
            ),
            {boundary.identity for boundary in discovered},
        )

    def test_new_independent_engine_is_rejected_until_explicitly_registered(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "api" / "independent_engine.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "from sqlalchemy.ext.asyncio import create_async_engine\n"
                "independent = create_async_engine('postgresql+asyncpg://example')\n",
                encoding="utf-8",
            )

            discovered = discover_application_database_boundaries(root)
            self.assertEqual(1, len(discovered))
            with self.assertRaisesRegex(
                ApplicationDatabaseBoundaryInventoryError,
                "application_database_boundary_inventory_mismatch:unregistered=",
            ):
                assert_application_database_boundary_inventory(discovered)

    def test_import_aliases_and_session_factory_aliases_are_not_an_audit_escape(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "worker.py"
            source.write_text(
                "import sqlalchemy.ext.asyncio as sa_async\n"
                "factory_constructor = sa_async.async_sessionmaker\n"
                "engine = sa_async.create_async_engine('postgresql+asyncpg://example')\n"
                "sessions = factory_constructor(engine)\n",
                encoding="utf-8",
            )

            discovered = discover_application_database_boundaries(root)
            self.assertEqual(
                {
                    ("worker.py", "<module>", "sqlalchemy.create_async_engine", 1),
                    ("worker.py", "<module>", "sqlalchemy.async_sessionmaker", 1),
                },
                {boundary.identity for boundary in discovered},
            )

    def test_dynamic_factory_lookup_is_refused_instead_of_becoming_an_audit_blind_spot(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "worker.py"
            source.write_text(
                "import sqlalchemy.ext.asyncio as sa_async\n"
                "factory = getattr(sa_async, 'create_async_engine')\n"
                "engine = factory('postgresql+asyncpg://example')\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ApplicationDatabaseBoundaryInventoryError,
                "application_database_boundary_dynamic_access:worker.py:sqlalchemy.ext.asyncio.create_async_engine@2",
            ):
                discover_application_database_boundaries(root)

    def test_direct_legacy_factory_import_or_call_is_a_release_audit_failure(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "api" / "legacy_database_use.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "from src.infrastructure.database.connection import init_database\n"
                "init_database('postgresql+asyncpg://example')\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ApplicationDatabaseBoundaryInventoryError,
                "application_database_boundary_legacy_connection_imported:api/legacy_database_use.py",
            ):
                assert_legacy_connection_module_is_unwired(root)

    def test_relative_legacy_factory_import_is_a_release_audit_failure(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "interfaces" / "http_api" / "legacy_database_use.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "from ...infrastructure.database.connection import init_database\n"
                "init_database('postgresql+asyncpg://example')\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ApplicationDatabaseBoundaryInventoryError,
                "application_database_boundary_legacy_connection_imported:src/interfaces/http_api/legacy_database_use.py",
            ):
                assert_legacy_connection_module_is_unwired(root)

    def test_importable_legacy_factory_is_unconditionally_retired_before_engine_creation(self) -> None:
        legacy = importlib.import_module("src.infrastructure.database.connection")

        self.assertIsNone(legacy.engine)
        self.assertIsNone(legacy.AsyncSessionLocal)
        with self.assertRaisesRegex(
            legacy.RetiredLegacyDatabaseConnectionError,
            "retired; use core.db",
        ):
            legacy.init_database("postgresql+asyncpg://example")
        self.assertIsNone(legacy.engine)
        self.assertIsNone(legacy.AsyncSessionLocal)


if __name__ == "__main__":
    unittest.main()
