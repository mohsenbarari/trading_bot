from __future__ import annotations

import io
import ipaddress
import json
import unittest
from unittest.mock import patch

from scripts import provision_arvan_witness_recovery_vps as recovery


class ProvisionArvanWitnessRecoveryVpsTests(unittest.TestCase):
    def test_cli_stops_before_parser_token_or_provider_access(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            recovery, "parse_args"
        ) as parse_args, patch.object(recovery, "read_private_text") as read_token, patch.object(
            recovery, "api_request"
        ) as request:
            self.assertEqual(recovery.main(), 2)
        parse_args.assert_not_called()
        read_token.assert_not_called()
        request.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "blocked_legacy_three_site_staging_runtime_retired")
        self.assertEqual(payload["component"], recovery.RETIREMENT_COMPONENT)

    def test_programmatic_provider_and_private_file_boundaries_are_retired(self):
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(recovery.LegacyThreeSiteStagingRuntimeRetiredError):
                recovery.api_request("GET", "/untrusted", "untrusted")
        urlopen.assert_not_called()
        with self.assertRaises(recovery.LegacyThreeSiteStagingRuntimeRetiredError):
            recovery.read_private_text(recovery.TOKEN_FILE)

    def test_direct_parser_helper_stops_before_constructing_a_parser(self):
        with patch.object(recovery.argparse, "ArgumentParser") as parser:
            with self.assertRaises(recovery.LegacyThreeSiteStagingRuntimeRetiredError):
                recovery.parse_args()
        parser.assert_not_called()

    def test_pure_network_free_helpers_remain_available(self):
        self.assertEqual(recovery.expected_rules()[0]["port_to"], "22")
        self.assertEqual(
            recovery.server_public_ipv4(
                {"addresses": {"public": [{"addr": str(ipaddress.ip_address("8.8.8.8"))}]}}
            ),
            "8.8.8.8",
        )
        self.assertIn("PasswordAuthentication no", recovery.init_script("ssh-ed25519 AAAA test"))


if __name__ == "__main__":
    unittest.main()
