import unittest
from types import SimpleNamespace

from core.runtime_identity import RuntimeIdentity
from core.webapp_writer_control import WriterStateSnapshot
from core.writer_fencing import (
    WriterFenceError,
    _enforce_writer_fence_before_commit,
    current_writer_fence_context,
    writer_fence_scope,
)


def identity(site="webapp_fi"):
    return RuntimeIdentity("webapp", site, "iran", False)


def snapshot(site="webapp_fi", epoch=2, transition_id="transition-current"):
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

    def test_unscoped_projection_transaction_is_not_intercepted(self):
        session = SimpleNamespace(connection=lambda: self.fail("must not query writer state"))

        _enforce_writer_fence_before_commit(session)


if __name__ == "__main__":
    unittest.main()
