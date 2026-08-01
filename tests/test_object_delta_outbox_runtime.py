from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.object_delta_outbox_runtime import (
    ObjectDeltaOutboxRuntimeError,
    allocate_verified_object_delta_outbox_entries,
)


class _Connection:
    pass


def _binding():
    return SimpleNamespace(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id="wa-ir-standby-97265988-4b12-444e",
        release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        expected_registry_fingerprint="0123456789abcdef",
        stream_generation_id="fi-ir-delta-97265988-a",
    )


def _term(*, epoch=9, lease_id="lease-9", transition="transition-9"):
    return SimpleNamespace(
        holder_site="webapp_fi",
        writer_epoch=epoch,
        lease_id=lease_id,
        witness_transition_id=transition,
    )


def _entry(change_log_id=41):
    return SimpleNamespace(
        id=change_log_id,
        operation="UPDATE",
        table_name="users",
        record_id=101,
        data={"id": 101},
        timestamp=SimpleNamespace(timestamp=lambda: 1785412800.0),
        hash="a" * 64,
    )


def _item(change_log_id=41):
    return {
        "type": "db_change",
        "change_log_id": change_log_id,
        "sync_protocol": {"registry_fingerprint": "0123456789abcdef"},
    }


class ObjectDeltaOutboxRuntimeTests(unittest.TestCase):
    def test_disabled_runtime_returns_before_validating_or_touching_the_connection(self):
        connection = _Connection()
        with patch("core.object_delta_outbox_runtime.binding_from_settings", return_value=None), patch(
            "core.object_delta_outbox_runtime._locked_change_log_entry"
        ) as locked:
            result = allocate_verified_object_delta_outbox_entries(
                connection,
                change_log_ids=(None,),
                expected_count=1,
            )

        self.assertEqual((), result.allocations)
        locked.assert_not_called()

    def test_enabled_runtime_allocates_each_exact_verified_id_with_one_term(self):
        connection = _Connection()
        allocation = SimpleNamespace(action="allocated", logical_sequence=1)
        with patch("core.object_delta_outbox_runtime.binding_from_settings", return_value=_binding()), patch(
            "core.object_delta_outbox_runtime._locked_change_log_entry",
            side_effect=[_entry(41), _entry(42)],
        ), patch(
            "core.db.require_application_writer_term",
            side_effect=[_term(), _term()],
        ), patch(
            "core.sync_worker.change_log_entry_to_sync_item",
            side_effect=[_item(41), _item(42)],
        ), patch(
            "core.object_delta_outbox_runtime.allocate_object_delta_outbox_entry_sync",
            return_value=allocation,
        ) as allocator:
            result = allocate_verified_object_delta_outbox_entries(
                connection,
                change_log_ids=(41, 42),
                expected_count=2,
            )

        self.assertEqual((allocation, allocation), result.allocations)
        self.assertEqual(2, allocator.call_count)
        first_request = allocator.call_args_list[0].args[1]
        second_request = allocator.call_args_list[1].args[1]
        self.assertEqual((41, 42), (first_request.change_log_id, second_request.change_log_id))
        self.assertEqual("webapp_fi", first_request.source_site)
        self.assertEqual("webapp_ir", first_request.destination_site)
        self.assertEqual((9, "lease-9"), (first_request.writer_epoch, first_request.writer_lease_id))

    def test_enabled_runtime_rejects_an_incomplete_or_duplicate_handoff_before_lookup(self):
        connection = _Connection()
        with patch("core.object_delta_outbox_runtime.binding_from_settings", return_value=_binding()), patch(
            "core.object_delta_outbox_runtime._locked_change_log_entry"
        ) as locked:
            with self.assertRaisesRegex(ObjectDeltaOutboxRuntimeError, "count"):
                allocate_verified_object_delta_outbox_entries(
                    connection,
                    change_log_ids=(41,),
                    expected_count=2,
                )
            with self.assertRaisesRegex(ObjectDeltaOutboxRuntimeError, "repeats"):
                allocate_verified_object_delta_outbox_entries(
                    connection,
                    change_log_ids=(41, 41),
                    expected_count=2,
                )

        locked.assert_not_called()

    def test_enabled_runtime_refuses_a_writer_term_change_before_commit(self):
        connection = _Connection()
        with patch("core.object_delta_outbox_runtime.binding_from_settings", return_value=_binding()), patch(
            "core.object_delta_outbox_runtime._locked_change_log_entry", return_value=_entry(41)
        ), patch(
            "core.db.require_application_writer_term",
            side_effect=[_term(epoch=9), _term(epoch=10, lease_id="lease-10", transition="transition-10")],
        ), patch("core.sync_worker.change_log_entry_to_sync_item", return_value=_item(41)), patch(
            "core.object_delta_outbox_runtime.allocate_object_delta_outbox_entry_sync",
            return_value=SimpleNamespace(action="allocated"),
        ):
            with self.assertRaisesRegex(ObjectDeltaOutboxRuntimeError, "term changed"):
                allocate_verified_object_delta_outbox_entries(
                    connection,
                    change_log_ids=(41,),
                    expected_count=1,
                )


if __name__ == "__main__":
    unittest.main()
