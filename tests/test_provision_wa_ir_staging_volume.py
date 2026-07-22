from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import provision_wa_ir_staging_volume as volume


class ProvisionWaIrStagingVolumeTests(unittest.TestCase):
    @patch.object(volume, "_find", return_value=None)
    @patch.object(volume, "_server", return_value={"id": volume.SERVER_ID})
    def test_plan_is_non_mutating_and_confirmation_is_exact(self, _server, _find):
        result = volume.execute(token="token", apply=False, confirm=None)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["billable_resource_created"])
        self.assertEqual(result["required_confirmation"], volume.confirmation_phrase())

    @patch.object(volume, "_find", return_value=None)
    @patch.object(volume, "_server", return_value={"id": volume.SERVER_ID})
    def test_apply_rejects_wrong_confirmation_before_create(self, _server, _find):
        with self.assertRaisesRegex(volume.VolumeError, "confirmation"):
            volume.execute(token="token", apply=True, confirm="wrong")

    @patch.object(volume, "_wait")
    @patch.object(volume, "api_request")
    @patch.object(volume, "_find", return_value=None)
    @patch.object(volume, "_server", return_value={"id": volume.SERVER_ID})
    def test_apply_creates_then_attaches_exact_pinned_volume(
        self, _server, _find, request, wait
    ):
        request.side_effect = [
            {"data": {"id": "volume-id"}},
            {"data": {"message": "attached"}},
        ]
        wait.side_effect = [
            {"id": "volume-id", "size": 50, "status": "available", "attachments": []},
            {
                "id": "volume-id",
                "size": 50,
                "status": "in-use",
                "attachments": [{"server_id": volume.SERVER_ID}],
            },
        ]
        result = volume.execute(
            token="token", apply=True, confirm=volume.confirmation_phrase()
        )
        self.assertEqual(result["status"], "attached")
        self.assertTrue(result["delete_after_full_matrix"])
        create = request.call_args_list[0]
        self.assertEqual(create.args[0], "POST")
        self.assertEqual(create.args[3]["size"], 50)
        attach = request.call_args_list[1]
        self.assertEqual(attach.args[0], "PATCH")
        self.assertEqual(
            attach.args[3],
            {"server_id": volume.SERVER_ID, "volume_id": "volume-id"},
        )


if __name__ == "__main__":
    unittest.main()
