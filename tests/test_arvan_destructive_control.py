from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.arvan_destructive_control import (
    ArvanDestructiveControlError,
    build_bound_power_intent,
    build_power_intent,
    execute_bound_power_intent,
    execute_power_intent,
)
from scripts.provision_arvan_full_matrix_destructive_hosts import ROLE_SPECS


class ArvanDestructiveControlTests(unittest.TestCase):
    def _state(self) -> dict:
        return {
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

    def _request(self, state: dict, statuses: dict[str, str], calls: list[tuple]):
        def request(method, path, token, payload=None, **kwargs):
            self.assertEqual(token, "private-token")
            role = next(
                item
                for item in ROLE_SPECS
                if state["hosts"][item]["server_id"] in path
            )
            calls.append((method, path, payload))
            if method == "POST":
                if path.endswith("/power-off"):
                    statuses[role] = "SHUTOFF"
                elif path.endswith("/power-on"):
                    statuses[role] = "ACTIVE"
                else:
                    raise AssertionError(path)
                return {"data": {"message": "accepted"}}
            spec = ROLE_SPECS[role]
            return {
                "data": {
                    "name": spec["name"],
                    "flavor": {"id": spec["plan_id"]},
                    "image": {"id": spec["image_id"]},
                    "status": statuses[role],
                    "password": "must-not-escape",
                    "addresses": {
                        "public": [{"addr": state["hosts"][role]["public_ip"]}]
                    },
                }
            }

        return request

    def test_intent_is_bound_to_exact_active_disposable_host(self):
        state = self._state()
        statuses = {role: "ACTIVE" for role in ROLE_SPECS}
        calls: list[tuple] = []
        with patch(
            "scripts.full_matrix_live.arvan_destructive_control._safe_existing_state",
            return_value=state,
        ):
            intent = build_power_intent(
                campaign_id="12345678-1234-4234-9234-123456789abc",
                release_sha="a" * 40,
                operation_id="22345678-1234-4234-9234-123456789abc",
                scenario_id="witness_partition_and_vm_pause",
                role="witness",
                action="power-off",
                request=self._request(state, statuses, calls),
                token="private-token",
            )
        self.assertEqual(intent["expected_before_status"], "ACTIVE")
        self.assertEqual(intent["recovery_action"], "power-on")
        self.assertIn("{campaign-host}", intent["provider_endpoint"])
        self.assertEqual([call[0] for call in calls], ["GET"])
        self.assertNotIn("password", repr(intent))

    def test_execution_posts_only_reviewed_endpoint_then_requires_observed_state(self):
        state = self._state()
        statuses = {role: "ACTIVE" for role in ROLE_SPECS}
        calls: list[tuple] = []
        request = self._request(state, statuses, calls)
        with patch(
            "scripts.full_matrix_live.arvan_destructive_control._safe_existing_state",
            return_value=state,
        ):
            intent = build_power_intent(
                campaign_id="12345678-1234-4234-9234-123456789abc",
                release_sha="a" * 40,
                operation_id="22345678-1234-4234-9234-123456789abc",
                scenario_id="witness_partition_and_vm_pause",
                role="witness",
                action="power-off",
                request=request,
                token="private-token",
            )
            with tempfile.TemporaryDirectory() as root:
                result = execute_power_intent(
                    intent,
                    audit_path=Path(root) / "audit.jsonl",
                    request=request,
                    token="private-token",
                    timeout_seconds=2,
                    poll_seconds=0.1,
                )
        self.assertEqual(result["after_status"], "SHUTOFF")
        self.assertEqual(result["action"], "power-off")
        self.assertEqual([call[0] for call in calls].count("POST"), 1)
        self.assertTrue(next(path for method, path, _ in calls if method == "POST").endswith("/power-off"))

    def test_precondition_or_intent_tamper_fails_before_provider_mutation(self):
        state = self._state()
        statuses = {role: "ACTIVE" for role in ROLE_SPECS}
        statuses["witness"] = "SHUTOFF"
        calls: list[tuple] = []
        with patch(
            "scripts.full_matrix_live.arvan_destructive_control._safe_existing_state",
            return_value=state,
        ), self.assertRaises(ArvanDestructiveControlError):
            build_power_intent(
                campaign_id="12345678-1234-4234-9234-123456789abc",
                release_sha="a" * 40,
                operation_id="22345678-1234-4234-9234-123456789abc",
                scenario_id="witness_partition_and_vm_pause",
                role="witness",
                action="power-off",
                request=self._request(state, statuses, calls),
                token="private-token",
            )
        self.assertEqual([call[0] for call in calls], ["GET"])

        statuses["witness"] = "ACTIVE"
        with patch(
            "scripts.full_matrix_live.arvan_destructive_control._safe_existing_state",
            return_value=state,
        ):
            intent = build_power_intent(
                campaign_id="12345678-1234-4234-9234-123456789abc",
                release_sha="a" * 40,
                operation_id="22345678-1234-4234-9234-123456789abc",
                scenario_id="witness_partition_and_vm_pause",
                role="witness",
                action="power-off",
                request=self._request(state, statuses, calls),
                token="private-token",
            )
            forged = copy.deepcopy(intent)
            forged["role"] = "webapp_fi"
            with tempfile.TemporaryDirectory() as root, self.assertRaises(ArvanDestructiveControlError):
                execute_power_intent(
                    forged,
                    audit_path=Path(root) / "audit.jsonl",
                    request=self._request(state, statuses, calls),
                    token="private-token",
                )
        self.assertEqual([call[0] for call in calls].count("POST"), 0)

    def test_bound_control_rejects_global_provider_fallback_and_uses_attested_paths(self):
        state = self._state()
        statuses = {role: "ACTIVE" for role in ROLE_SPECS}
        calls: list[tuple] = []
        campaign = "12345678-1234-4234-9234-123456789abc"
        group = "32345678-1234-4234-9234-123456789abc"
        operation = "22345678-1234-4234-9234-123456789abc"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            state_path = root / "provider-state.json"
            token_path = root / "provider-token"
            audit_root = root / "audit"
            state_path.write_text(__import__("json").dumps(state) + "\n", encoding="utf-8")
            token_path.write_text("private-token\n", encoding="utf-8")
            state_path.chmod(0o600)
            token_path.chmod(0o600)
            audit_root.mkdir(mode=0o700)
            control = {
                "schema": "three-site-full-matrix-destructive-control-v1",
                "campaign_id": campaign,
                "gate_group_id": group,
                "execution_class": "dedicated-host-destructive",
                "release_sha": "a" * 40,
                "enabled": True,
                "provider_state_file": str(state_path),
                "provider_token_file": str(token_path),
                "audit_root": str(audit_root),
            }
            request = self._request(state, statuses, calls)
            intent = build_bound_power_intent(
                control=control,
                campaign_id=campaign,
                gate_group_id=group,
                release_sha="a" * 40,
                operation_id=operation,
                scenario_id="witness_partition_and_vm_pause",
                role="witness",
                action="power-off",
                request=request,
                token="private-token",
            )
            result = execute_bound_power_intent(
                intent,
                control=control,
                campaign_id=campaign,
                gate_group_id=group,
                release_sha="a" * 40,
                request=request,
                token="private-token",
                timeout_seconds=2,
                poll_seconds=0.1,
            )
        self.assertEqual(result["after_status"], "SHUTOFF")
        self.assertEqual([call[0] for call in calls].count("POST"), 1)


if __name__ == "__main__":
    unittest.main()
