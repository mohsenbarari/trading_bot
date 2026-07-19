import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.runtime_identity import RuntimeIdentity
from core.webapp_writer_control import WriterStateSnapshot
from core.writer_fencing import (
    WriterFenceError,
    _enforce_writer_fence_before_commit,
    current_writer_fence_context,
    projection_fence_scope,
    register_writer_fence_listener,
    settings as writer_fence_settings,
    writer_fence_scope,
)
from core.writer_lease_clock import boottime_seconds, current_boot_id
from models.user import User


def identity(site="webapp_fi"):
    return RuntimeIdentity("webapp", site, "iran", False)


def snapshot(site="webapp_fi", epoch=2, transition_id="transition-current", *, witness=False):
    now = datetime.now(timezone.utc)
    return WriterStateSnapshot(
        active_site=site,
        writer_epoch=epoch,
        control_state="active",
        transition_id=transition_id,
        readiness_evidence_hash=None,
        readiness_evidence_id=None,
        readiness_approved_by=None,
        readiness_approved_at=None,
        readiness_expires_at=None,
        witness_lease_id="lease-current" if witness else None,
        witness_lease_expires_at=now + timedelta(seconds=180) if witness else None,
        witness_proof_hash="a" * 64 if witness else None,
        witness_transition_id="witness-current" if witness else None,
        witness_lease_issued_at=now if witness else None,
        witness_local_boot_id=current_boot_id() if witness else None,
        witness_local_boottime_deadline=boottime_seconds() + 160 if witness else None,
        witness_observed_wall_at=now if witness else None,
        witness_observed_boottime=boottime_seconds() if witness else None,
        witness_clock_offset_ms=0 if witness else None,
    )


class FakeMappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class FakeSession:
    def __init__(self, row):
        self.row = row

    def connection(self):
        return self

    def execute(self, _statement):
        return FakeMappingResult(self.row)


class WriterFenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_writer_fence_listener()

    def _strict_settings(self):
        return patch.multiple(
            writer_fence_settings,
            three_site_dr_enabled=True,
            logical_authority="webapp",
            physical_site="webapp_fi",
            server_mode="iran",
        )
    def test_scope_sets_and_restores_commit_context(self):
        self.assertIsNone(current_writer_fence_context())

        with writer_fence_scope(identity(), snapshot(), source="test") as context:
            self.assertEqual(current_writer_fence_context(), context)
            self.assertEqual(context.writer_epoch, 2)

        self.assertIsNone(current_writer_fence_context())

    def test_scope_rejects_non_active_local_site(self):
        with self.assertRaises(WriterFenceError):
            with writer_fence_scope(
                identity("webapp_ir"),
                snapshot("webapp_fi"),
                source="test",
            ):
                self.fail("standby scope must not open")

    def test_commit_boundary_accepts_only_the_same_writer_term(self):
        valid_row = {
            "active_site": "webapp_fi",
            "writer_epoch": 2,
            "control_state": "active",
            "transition_id": "transition-current",
        }
        with writer_fence_scope(identity(), snapshot(), source="test"):
            _enforce_writer_fence_before_commit(FakeSession(valid_row))

            for changed in (
                {**valid_row, "active_site": "webapp_ir"},
                {**valid_row, "writer_epoch": 3},
                {**valid_row, "control_state": "fenced"},
                {**valid_row, "transition_id": "transition-new"},
                None,
            ):
                with self.subTest(row=changed):
                    with self.assertRaises(WriterFenceError):
                        _enforce_writer_fence_before_commit(FakeSession(changed))

    def test_unscoped_authoritative_orm_commit_is_rejected(self):
        engine = create_engine("sqlite:///:memory:")
        with Session(engine) as session, self._strict_settings():
            session.add(User(id=1001, full_name="unsafe"))
            with self.assertRaisesRegex(WriterFenceError, "lacks an explicit"):
                session.commit()

    def test_unscoped_bulk_or_raw_write_is_rejected(self):
        engine = create_engine("sqlite:///:memory:")
        with Session(engine) as session, self._strict_settings():
            with self.assertRaisesRegex(WriterFenceError, "bulk/raw"):
                session.execute(text("UPDATE users SET full_name = 'unsafe' WHERE id = 1"))

    def test_scoped_writable_cte_and_function_are_still_rejected(self):
        engine = create_engine("sqlite:///:memory:")
        unsafe = (
            "WITH changed AS (UPDATE users SET full_name = 'unsafe' RETURNING id) SELECT id FROM changed",
            "CALL mutate_user(1)",
            "SELECT mutate_user(1)",
            "SELECT id FROM users; DELETE FROM users",
        )
        with Session(engine) as session, self._strict_settings(), writer_fence_scope(
            identity(), snapshot(), source="test"
        ):
            for sql in unsafe:
                with self.subTest(sql=sql), self.assertRaisesRegex(
                    WriterFenceError, "Unclassified raw SQL"
                ):
                    session.execute(text(sql))

    def test_projection_capability_has_closed_field_allowlist(self):
        engine = create_engine("sqlite:///:memory:")
        with Session(engine) as session, self._strict_settings(), projection_fence_scope(source="test"):
            session.add(User(id=1002, full_name="safe", admin_password_hash="forbidden"))
            with self.assertRaisesRegex(WriterFenceError, "admin_password_hash"):
                session.commit()

    def test_read_only_unscoped_commit_does_not_query_writer_state(self):
        session = SimpleNamespace(
            info={},
            new=(),
            dirty=(),
            deleted=(),
            connection=lambda: self.fail("must not query writer state"),
        )
        with self._strict_settings():
            _enforce_writer_fence_before_commit(session)

    def test_commit_boundary_fails_closed_when_required_witness_is_near_expiry(self):
        now = datetime.now(timezone.utc)
        valid_row = {
            "active_site": "webapp_fi",
            "writer_epoch": 2,
            "control_state": "active",
            "transition_id": "transition-current",
            "witness_lease_id": "lease-current",
            "witness_lease_expires_at": now + timedelta(seconds=180),
            "witness_local_boot_id": current_boot_id(),
            "witness_local_boottime_deadline": boottime_seconds() + 160,
        }
        with writer_fence_scope(
            identity(),
            snapshot(witness=True),
            source="test",
            require_witness_lease=True,
        ):
            _enforce_writer_fence_before_commit(FakeSession(valid_row))
            with self.assertRaises(WriterFenceError):
                _enforce_writer_fence_before_commit(
                    FakeSession(
                        {
                            **valid_row,
                            "witness_local_boottime_deadline": boottime_seconds() - 1,
                        }
                    )
                )


if __name__ == "__main__":
    unittest.main()
