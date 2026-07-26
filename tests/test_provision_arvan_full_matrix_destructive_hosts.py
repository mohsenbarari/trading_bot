from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.provision_arvan_full_matrix_destructive_hosts import (
    ROLE_ORDER,
    ROLE_SPECS,
    _init_script,
    inspect_existing_hosts,
    _validate_init_script,
)


class ProvisionArvanFullMatrixDestructiveHostsTests(unittest.TestCase):
    def test_role_set_and_geography_match_destructive_architecture(self):
        self.assertEqual(
            ROLE_ORDER,
            ("bot_fi", "webapp_fi", "webapp_ir", "witness"),
        )
        self.assertEqual(ROLE_SPECS["webapp_ir"]["region"], "ir-thr-fr1")
        for role in ("bot_fi", "webapp_fi", "witness"):
            self.assertEqual(ROLE_SPECS[role]["region"], "eu-west1-a")
        self.assertEqual(
            len({spec["name"] for spec in ROLE_SPECS.values()}),
            4,
        )

    def test_bootstrap_is_key_only_control_ip_scoped_and_no_delete(self):
        source = __import__(
            "scripts.provision_arvan_full_matrix_destructive_hosts",
            fromlist=["x"],
        )
        module_source = Path(source.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"DELETE"', module_source)
        for role in ROLE_ORDER:
            script = _init_script(
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeMatrixPublicKeyForSyntaxOnly root@test",
                role,
            )
            _validate_init_script(script)
            self.assertIn("PasswordAuthentication no", script)
            self.assertIn("65.109.216.187/32", script)
            self.assertIn("iptables -P INPUT DROP", script)

    def test_inspection_reads_only_action_metadata_without_secret_fields(self):
        state = {
            "status": "active",
            "hosts": {
                role: {
                    "region": spec["region"],
                    "server_id": f"{index + 1:08x}-1234-4234-9234-123456789abc",
                    "public_ip": f"8.8.8.{index + 1}",
                }
                for index, (role, spec) in enumerate(ROLE_SPECS.items())
            },
        }

        def fake_api_request(method, path, token, payload=None, **kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(token, "private-token")
            role = next(
                item
                for item in ROLE_ORDER
                if state["hosts"][item]["server_id"] in path
            )
            if path.endswith("/actions"):
                return {"data": [{"action": "power-off", "message": "private"}]}
            spec = ROLE_SPECS[role]
            return {
                "data": {
                    "name": spec["name"],
                    "flavor": {"id": spec["plan_id"]},
                    "image": {"id": spec["image_id"]},
                    "status": "ACTIVE",
                    "password": "must-not-escape",
                    "addresses": {
                        "public": [
                            {"addr": state["hosts"][role]["public_ip"]}
                        ]
                    },
                }
            }

        with (
            patch(
                "scripts.provision_arvan_full_matrix_destructive_hosts._safe_existing_state",
                return_value=state,
            ),
            patch(
                "scripts.provision_arvan_full_matrix_destructive_hosts.api_request",
                side_effect=fake_api_request,
            ),
        ):
            observed = inspect_existing_hosts("private-token")

        self.assertEqual(observed["status"], "passed")
        self.assertEqual(set(observed["roles"]), set(ROLE_ORDER))
        self.assertEqual(
            observed["roles"]["webapp_ir"]["available_actions"],
            ["power-off"],
        )
        self.assertNotIn("password", repr(observed))
        self.assertNotIn("private", repr(observed))


if __name__ == "__main__":
    unittest.main()
