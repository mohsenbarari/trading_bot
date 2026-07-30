from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.standby_snapshot_capacity import SnapshotCapacityError, require_capacity


class StandbySnapshotCapacityTests(unittest.TestCase):
    def test_returns_auditable_reservation_when_space_is_sufficient(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch("core.standby_snapshot_capacity.shutil.disk_usage") as disk_usage:
                disk_usage.return_value = mock.Mock(free=100)
                result = require_capacity(
                    root,
                    required_new_bytes=60,
                    minimum_free_bytes=20,
                    label="fixture",
                )

        self.assertEqual(100, result["available_bytes"])
        self.assertEqual(60, result["required_new_bytes"])
        self.assertEqual(20, result["minimum_free_bytes"])
        self.assertEqual(40, result["remaining_bytes"])

    def test_rejects_before_the_caller_can_allocate_when_reserve_would_be_violated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch("core.standby_snapshot_capacity.shutil.disk_usage") as disk_usage:
                disk_usage.return_value = mock.Mock(free=79)
                with self.assertRaisesRegex(SnapshotCapacityError, "insufficient free space"):
                    require_capacity(
                        root,
                        required_new_bytes=60,
                        minimum_free_bytes=20,
                        label="fixture",
                    )

    def test_rejects_negative_and_boolean_capacity_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(SnapshotCapacityError, "required_new_bytes"):
                require_capacity(root, required_new_bytes=True, minimum_free_bytes=0, label="fixture")
            with self.assertRaisesRegex(SnapshotCapacityError, "minimum_free_bytes"):
                require_capacity(root, required_new_bytes=0, minimum_free_bytes=-1, label="fixture")


if __name__ == "__main__":
    unittest.main()
