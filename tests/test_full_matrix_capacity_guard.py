import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.full_matrix_capacity_guard import SCHEMA, capacity_guard_reasons


class _StatVfs:
    f_bavail = 3
    f_frsize = 1024


class FullMatrixCapacityGuardTests(unittest.TestCase):
    def _marker(self, root: Path, **changes):
        value = {
            "schema": SCHEMA,
            "state": "armed",
            "campaign_id": "fm-capacity-test",
            "release_sha": "a" * 40,
            "operation_id": "a" * 36,
            "role": "webapp_fi",
            "storage_total_bytes": 10_000,
            "available_bytes": 3_072,
            "hard_limit_bytes": 4_096,
        }
        value.update(changes)
        path = root / "guard.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(path, 0o444)
        return path

    def test_absent_marker_leaves_normal_writers_unaffected(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                capacity_guard_reasons(
                    marker_file=f"{temporary}/guard.json",
                    release_sha="a" * 40,
                    physical_site="webapp_fi",
                    three_site_enabled=True,
                ),
                (),
            )

    def test_armed_marker_fences_before_any_data_plane_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._marker(Path(temporary))
            with patch("core.full_matrix_capacity_guard.os.statvfs", return_value=_StatVfs()):
                self.assertEqual(
                    capacity_guard_reasons(
                        marker_file=str(path),
                        release_sha="a" * 40,
                        physical_site="webapp_fi",
                        three_site_enabled=True,
                    ),
                    ("full_matrix_capacity_hard_limit",),
                )

    def test_stale_or_malformed_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._marker(Path(temporary), release_sha="b" * 40)
            self.assertEqual(
                capacity_guard_reasons(
                    marker_file=str(path),
                    release_sha="a" * 40,
                    physical_site="webapp_fi",
                    three_site_enabled=True,
                ),
                ("full_matrix_capacity_guard_invalid",),
            )
            os.chmod(path, 0o644)
            self.assertEqual(
                capacity_guard_reasons(
                    marker_file=str(path),
                    release_sha="a" * 40,
                    physical_site="webapp_fi",
                    three_site_enabled=True,
                ),
                ("full_matrix_capacity_guard_invalid",),
            )

    def test_marker_that_no_longer_matches_filesystem_stays_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._marker(Path(temporary))
            class Recovered:
                f_bavail = 9
                f_frsize = 1024
            with patch("core.full_matrix_capacity_guard.os.statvfs", return_value=Recovered()):
                self.assertEqual(
                    capacity_guard_reasons(
                        marker_file=str(path),
                        release_sha="a" * 40,
                        physical_site="webapp_fi",
                        three_site_enabled=True,
                    ),
                    ("full_matrix_capacity_guard_inconsistent",),
                )
