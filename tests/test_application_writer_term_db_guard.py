from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Table,
    create_engine,
    delete,
    event,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.orm import Session, declarative_base

from core import db
from core.application_writer_term import ApplicationWriterTermError


Base = declarative_base()


class WriterTermGuardWidget(Base):
    __tablename__ = "writer_term_guard_widgets"

    id = Column(Integer, primary_key=True)


class ApplicationWriterTermSessionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_global_session_guards_are_registered(self) -> None:
        self.assertTrue(
            event.contains(
                Session,
                "before_flush",
                db._enforce_application_writer_term_before_flush,
            )
        )
        self.assertTrue(
            event.contains(
                Session,
                "before_commit",
                db._enforce_application_writer_term_before_commit,
            )
        )
        self.assertTrue(
            event.contains(
                Session,
                "do_orm_execute",
                db._enforce_application_writer_term_for_core_dml,
            )
        )

    def test_disabled_policy_preserves_orm_flush_and_commit(self) -> None:
        session = Session(self.engine)
        try:
            with patch.object(db.settings, "application_writer_term_enforced", False):
                session.add(WriterTermGuardWidget(id=1))
                session.commit()
        finally:
            session.close()

        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(WriterTermGuardWidget.id)).scalar_one(), 1)

    def test_invalid_term_blocks_orm_flush_before_database_write(self) -> None:
        session = Session(self.engine)
        try:
            session.add(WriterTermGuardWidget(id=1))
            with patch(
                "core.db.require_application_writer_term",
                side_effect=ApplicationWriterTermError("writer term is missing"),
            ) as require_term:
                with self.assertRaisesRegex(ApplicationWriterTermError, "missing"):
                    session.flush()

            require_term.assert_called_once_with()
        finally:
            session.rollback()
            session.close()

        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(WriterTermGuardWidget.id)).all(), [])

    def test_invalid_term_blocks_each_session_core_dml_statement(self) -> None:
        table = Table(
            "writer_term_core_dml",
            MetaData(),
            Column("id", Integer, primary_key=True),
        )
        statements = (
            insert(table).values(id=1),
            update(table).values(id=2),
            delete(table),
        )

        for statement in statements:
            with self.subTest(statement_type=type(statement).__name__), patch(
                "core.db.require_application_writer_term",
                side_effect=ApplicationWriterTermError("writer term is expired"),
            ) as require_term:
                with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                    db._enforce_application_writer_term_for_core_dml(
                        SimpleNamespace(statement=statement)
                    )

                require_term.assert_called_once_with()

    def test_invalid_term_blocks_session_execute_core_dml_before_execution(self) -> None:
        metadata = MetaData()
        table = Table(
            "writer_term_execute_dml",
            metadata,
            Column("id", Integer, primary_key=True),
        )
        metadata.create_all(self.engine)
        session = Session(self.engine)
        try:
            with patch(
                "core.db.require_application_writer_term",
                side_effect=ApplicationWriterTermError("writer term is expired"),
            ) as require_term:
                with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                    session.execute(insert(table).values(id=1))

            require_term.assert_called_once_with()
        finally:
            session.rollback()
            session.close()

        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(table.c.id)).all(), [])

    def test_invalid_term_is_rechecked_before_commit(self) -> None:
        session = Session(self.engine)
        try:
            with patch(
                "core.db.require_application_writer_term",
                side_effect=ApplicationWriterTermError("writer term is expired"),
            ) as require_term:
                with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                    session.commit()

            require_term.assert_called_once_with()
        finally:
            session.rollback()
            session.close()

    def test_core_select_does_not_call_the_write_guard(self) -> None:
        session = Session(self.engine)
        try:
            with patch("core.db.require_application_writer_term") as require_term:
                session.execute(select(WriterTermGuardWidget.id))

            require_term.assert_not_called()
        finally:
            session.close()


class ApplicationWriterTermEngineGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE writer_term_direct_sql "
                    "(id INTEGER PRIMARY KEY, value INTEGER NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO writer_term_direct_sql (id, value) VALUES (1, 1)")
            )
        db.register_application_writer_term_engine_guard(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_application_engine_cursor_guard_is_registered(self) -> None:
        self.assertTrue(
            event.contains(
                db.engine.sync_engine,
                "before_cursor_execute",
                db._enforce_application_writer_term_before_cursor_execute,
            )
        )

    def test_disabled_policy_preserves_direct_text_dml_and_ddl(self) -> None:
        with patch.object(db.settings, "application_writer_term_enforced", False), patch(
            "core.db.require_application_writer_term"
        ) as require_term:
            with self.engine.begin() as connection:
                connection.execute(
                    text("CREATE TABLE writer_term_disabled_ddl (id INTEGER PRIMARY KEY)")
                )
                connection.execute(
                    text("INSERT INTO writer_term_direct_sql (id, value) VALUES (2, 2)")
                )
                connection.execute(
                    text("UPDATE writer_term_direct_sql SET value = 3 WHERE id = 2")
                )
                connection.execute(text("DELETE FROM writer_term_direct_sql WHERE id = 2"))

        require_term.assert_not_called()
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    text("SELECT value FROM writer_term_direct_sql WHERE id = 1")
                ).scalar_one(),
                1,
            )
            self.assertEqual(
                connection.execute(
                    text("SELECT name FROM sqlite_master WHERE name = 'writer_term_disabled_ddl'")
                ).scalar_one(),
                "writer_term_disabled_ddl",
            )

    def test_enabled_invalid_term_blocks_direct_text_dml_and_ddl_before_execution(self) -> None:
        statements = (
            "INSERT INTO writer_term_direct_sql (id, value) VALUES (2, 2)",
            "UPDATE writer_term_direct_sql SET value = 2 WHERE id = 1",
            "DELETE FROM writer_term_direct_sql WHERE id = 1",
            "CREATE TABLE writer_term_forbidden_ddl (id INTEGER PRIMARY KEY)",
        )

        for statement in statements:
            with self.subTest(statement=statement), patch.object(
                db.settings,
                "application_writer_term_enforced",
                True,
            ), patch(
                "core.db.require_application_writer_term",
                side_effect=ApplicationWriterTermError("writer term is expired"),
            ) as require_term:
                with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                    with self.engine.connect() as connection:
                        connection.execute(text(statement))

                require_term.assert_called_once_with()

        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    text("SELECT id, value FROM writer_term_direct_sql ORDER BY id")
                ).all(),
                [(1, 1)],
            )
            self.assertEqual(
                connection.execute(
                    text("SELECT name FROM sqlite_master WHERE name = 'writer_term_forbidden_ddl'")
                ).all(),
                [],
            )

    def test_enabled_invalid_term_blocks_unknown_leading_sql_form(self) -> None:
        with patch.object(db.settings, "application_writer_term_enforced", True), patch(
            "core.db.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ) as require_term:
            with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                with self.engine.connect() as connection:
                    connection.execute(text("WITH candidate AS (SELECT 1) SELECT * FROM candidate"))

        require_term.assert_called_once_with()

    def test_sql_classifier_treats_select_into_and_locking_select_as_unsafe(self) -> None:
        self.assertTrue(
            db._sql_statement_requires_writer_term(
                "SELECT 1 INTO writer_term_created_relation"
            )
        )
        self.assertTrue(
            db._sql_statement_requires_writer_term(
                "SELECT value FROM writer_term_direct_sql FOR UPDATE"
            )
        )
        self.assertFalse(
            db._sql_statement_requires_writer_term(
                "-- known read-only form\nSELECT value FROM writer_term_direct_sql"
            )
        )

    def test_enabled_invalid_term_allows_explicit_read_only_select(self) -> None:
        with patch.object(db.settings, "application_writer_term_enforced", True), patch(
            "core.db.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ) as require_term:
            with self.engine.connect() as connection:
                result = connection.execute(
                    text("/* read-only query */ SELECT value FROM writer_term_direct_sql WHERE id = 1")
                )

        self.assertEqual(result.scalar_one(), 1)
        require_term.assert_not_called()


if __name__ == "__main__":
    unittest.main()
