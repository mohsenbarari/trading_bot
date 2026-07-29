from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from scripts import provision_wa_ir_staging_volume as volume


class ProvisionWaIrStagingVolumeTests(unittest.TestCase):
    def test_programmatic_execution_stops_before_server_or_volume_access(self):
        with patch.object(volume, "_server") as server, patch.object(volume, "_find") as find:
            with self.assertRaises(volume.LegacyThreeSiteStagingRuntimeRetiredError):
                volume.execute(token="untrusted", apply=True, confirm="untrusted")
        server.assert_not_called()
        find.assert_not_called()

    def test_cli_stops_before_parser_or_private_token_read(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            volume, "read_private_text"
        ) as read_token:
            self.assertEqual(volume.main(["--token-file", "/untrusted", "--apply"]), 2)
        read_token.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "blocked_legacy_three_site_staging_runtime_retired")
        self.assertEqual(payload["component"], volume.RETIREMENT_COMPONENT)

    def test_pure_confirmation_helper_remains_available(self):
        self.assertEqual(
            volume.confirmation_phrase(),
            f"create-wa-ir-staging-volume:{volume.REGION}:{volume.SERVER_ID}:{volume.VOLUME_SIZE_GB}",
        )


if __name__ == "__main__":
    unittest.main()
