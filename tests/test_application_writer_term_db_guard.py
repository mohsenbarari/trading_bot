from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event, insert, select, text
from sqlalchemy.orm import Session, declarative_base

from core import db
from core.application_writer_term import ApplicationWriterTermError


Base = declarative_base()


class GuardWidget(Base):
    __tablename__ = "application_writer_term_guard_widgets"

    id = Column(Integer, primary_key=True)


class ApplicationWriterTermDbGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        db.register_application_writer_term_engine_guard(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_session_guards_are_registered(self) -> None:
        self.assertTrue(event.contains(Session, "before_flush", db._enforce_application_writer_term_before_flush))
        self.assertTrue(event.contains(Session, "before_commit", db._enforce_application_writer_term_before_commit))
        self.assertTrue(event.contains(Session, "do_orm_execute", db._enforce_application_writer_term_for_core_dml))

    def test_disabled_policy_preserves_orm_and_raw_sql(self) -> None:
        with patch.object(db.settings, "application_writer_term_enforced", False):
            with Session(self.engine) as session:
                session.add(GuardWidget(id=1))
                session.commit()
            with self.engine.begin() as connection:
                connection.execute(text("INSERT INTO application_writer_term_guard_widgets (id) VALUES (2)"))

        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(select(GuardWidget.id).order_by(GuardWidget.id)).scalars().all(),
                [1, 2],
            )

    def test_invalid_term_blocks_orm_flush_and_bulk_dml_before_write(self) -> None:
        with Session(self.engine) as session:
            session.add(GuardWidget(id=1))
            with patch(
                "core.db.require_application_writer_term",
                side_effect=ApplicationWriterTermError("writer term is missing"),
            ):
                with self.assertRaisesRegex(ApplicationWriterTermError, "missing"):
                    session.flush()
            session.rollback()

        metadata = MetaData()
        table = Table("application_writer_term_bulk", metadata, Column("id", Integer, primary_key=True))
        metadata.create_all(self.engine)
        with Session(self.engine) as session, patch(
            "core.db.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ):
            with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                session.execute(insert(table).values(id=1))
            session.rollback()

        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(GuardWidget.id)).all(), [])
            self.assertEqual(connection.execute(select(table.c.id)).all(), [])

    def test_enabled_invalid_term_blocks_raw_sql_including_select(self) -> None:
        with patch.object(db.settings, "application_writer_term_enforced", True), patch(
            "core.db.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ):
            for statement in (
                "INSERT INTO application_writer_term_guard_widgets (id) VALUES (1)",
                "CREATE TABLE application_writer_term_forbidden (id INTEGER PRIMARY KEY)",
                "SELECT id FROM application_writer_term_guard_widgets",
            ):
                with self.subTest(statement=statement), self.assertRaisesRegex(
                    ApplicationWriterTermError, "expired"
                ):
                    with self.engine.connect() as connection:
                        connection.execute(text(statement))

        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    text("SELECT name FROM sqlite_master WHERE name='application_writer_term_forbidden'")
                ).all(),
                [],
            )


class ApplicationWriterTermSchemaStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_schema_bootstrap_returns_before_any_engine_begin(self) -> None:
        engine = SimpleNamespace(begin=MagicMock())
        with patch(
            "core.db.validate_application_writer_term_runtime_settings"
        ), patch("core.db.require_application_writer_term"), patch.object(
            db.settings,
            "database_schema_bootstrap_enabled",
            False,
        ), patch("core.db.engine", engine):
            await db.init_db()

        engine.begin.assert_not_called()

    async def test_invalid_term_refuses_before_any_engine_begin(self) -> None:
        engine = SimpleNamespace(begin=MagicMock())
        with patch(
            "core.db.validate_application_writer_term_runtime_settings"
        ), patch(
            "core.db.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term missing"),
        ), patch("core.db.engine", engine):
            with self.assertRaisesRegex(ApplicationWriterTermError, "missing"):
                await db.init_db()

        engine.begin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
