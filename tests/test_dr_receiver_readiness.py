import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.runtime_identity import RuntimeIdentity
from dr_receiver_app import ready


class _MappingResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row


class _FakeDb:
    def __init__(self, row=None, error=None):
        self._row = row
        self._error = error

    async def execute(self, _statement):
        if self._error is not None:
            raise self._error
        return _MappingResult(self._row)


def _pairwise_keys(*pairs):
    return json.dumps(
        [
            {
                "key_id": f"{source}-to-{destination}",
                "source_site": source,
                "destination_site": destination,
                "secret": "s" * 32,
            }
            for source, destination in pairs
        ]
    )


def _settings(keys):
    return SimpleNamespace(
        three_site_dr_enabled=True,
        dr_event_protocol_enabled=True,
        dr_event_protocol_strict=True,
        dr_sync_pairwise_keys_json=keys,
        origin_expected_migration_revision="d542e3f4a6b7",
    )


def _ready_row(**overrides):
    row = {
        "database_user": "webapp_fi_receiver",
        "physical_site": "webapp_fi",
        "receiver_role_bound": True,
        "enforcement_enabled": True,
        "migration_revision": "d542e3f4a6b7",
        "nonce_privilege": True,
        "event_privilege": True,
        "receipt_privilege": True,
    }
    row.update(overrides)
    return row


class DrReceiverReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, *, keys, row=None, error=None):
        identity = RuntimeIdentity("webapp", "webapp_fi", "iran", False)
        with patch("dr_receiver_app.settings", _settings(keys)), patch(
            "dr_receiver_app.resolve_runtime_identity", return_value=identity
        ):
            return await ready(_FakeDb(row=row, error=error))

    async def test_ready_requires_exact_inbound_topology_and_projection_database(self):
        response = await self._call(
            keys=_pairwise_keys(
                ("bot_fi", "webapp_fi"),
                ("webapp_ir", "webapp_fi"),
            ),
            row=_ready_row(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["reasons"], [])

    async def test_missing_inbound_pair_fails_closed(self):
        response = await self._call(
            keys=_pairwise_keys(("bot_fi", "webapp_fi")),
            row=_ready_row(),
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "dr_inbound_key_topology_incomplete",
            json.loads(response.body)["reasons"],
        )

    async def test_wrong_role_or_revision_fails_closed(self):
        response = await self._call(
            keys=_pairwise_keys(
                ("bot_fi", "webapp_fi"),
                ("webapp_ir", "webapp_fi"),
            ),
            row=_ready_row(
                database_user="webapp_fi_app",
                receiver_role_bound=False,
                migration_revision="old",
            ),
        )
        reasons = json.loads(response.body)["reasons"]
        self.assertEqual(response.status_code, 503)
        self.assertIn("dr_receiver_role_mismatch", reasons)
        self.assertIn("migration_revision_mismatch", reasons)

    async def test_database_failure_fails_closed(self):
        response = await self._call(
            keys=_pairwise_keys(
                ("bot_fi", "webapp_fi"),
                ("webapp_ir", "webapp_fi"),
            ),
            error=RuntimeError("database unavailable"),
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "dr_database_readiness_unavailable",
            json.loads(response.body)["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
