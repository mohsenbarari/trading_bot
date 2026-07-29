from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from core.production_writer_lease import (
    LEASE_SCHEMA,
    ProductionWriterLeaseError,
    load_production_writer_lease,
)


NOW = datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)


def lease_payload(*, holder_site: str = "webapp_fi", expires_at: datetime | None = None) -> dict:
    if expires_at is None:
        expires_at = NOW + timedelta(seconds=60)
    return {
        "schema": LEASE_SCHEMA,
        "holder_site": holder_site,
        "writer_epoch": 3,
        "lease_id": "lease-3",
        "issued_at": NOW.isoformat(),
        "expires_at": expires_at.isoformat(),
        "witness_transition_id": "transition-3",
        "proof_sha256": "a" * 64,
    }


def write_lease(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)


class ProductionWriterLeaseTests(unittest.TestCase):
    def test_root_only_lease_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())

            lease = load_production_writer_lease(path)

        self.assertEqual(lease.writer_epoch, 3)

    def test_insecure_file_permissions_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())
            os.chmod(path, 0o644)

            with self.assertRaisesRegex(ProductionWriterLeaseError, "permissions"):
                load_production_writer_lease(path)

if __name__ == "__main__":
    unittest.main()
