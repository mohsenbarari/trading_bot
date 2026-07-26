from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from scripts.build_three_site_full_matrix_live_plan import (
    BINDING_NAMES,
    LivePlanBuildError,
    build_payload,
)
from scripts.full_matrix_live.common import LiveMatrixError, _validate_roles


class BuildThreeSiteFullMatrixLivePlanTests(unittest.TestCase):
    def _fixtures(self, root: Path):
        campaign_id = str(uuid.uuid4())
        gate_group_id = str(uuid.uuid4())
        release = "a" * 40
        identity = root / "id"
        known_hosts = root / "known_hosts"
        agent_config = root / "webapp-ir-agent.json"
        for path, value in (
            (identity, "not-a-real-key\\n"),
            (known_hosts, "example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA==\\n"),
            (agent_config, "{}\\n"),
        ):
            path.write_text(value, encoding="utf-8")
            path.chmod(0o600)

        def target(role: str, *, transport: str) -> dict:
            common = {
                "host_ip": f"203.0.113.{len(role)}",
                "repo_root": str(root / f"{role}-repo"),
                "compose_file": str(root / f"{role}.compose.yml"),
                "env_file": str(root / f"{role}.env"),
                "project_name": f"full-matrix-{role.replace('_', '-')}",
                "storage_root": str(root / f"{role}-storage"),
                "payload_transport": "object-storage" if role in {"webapp_ir", "witness"} else "local" if role == "bot_fi" else "direct-finland",
                "command_prefix": [],
            }
            if transport == "ssh":
                return {
                    **common,
                    "transport": "ssh",
                    "ssh_port": 22,
                    "ssh_user": "root",
                    "ssh_identity_file": str(identity),
                    "ssh_known_hosts_file": str(known_hosts),
                    "agent_config": "",
                }
            return {
                **common,
                "transport": transport,
                "ssh_port": 0,
                "ssh_user": "",
                "ssh_identity_file": "",
                "ssh_known_hosts_file": "",
                "agent_config": str(agent_config) if transport == "object-storage-agent" else "",
            }

        roles = {
            "bot_fi": target("bot_fi", transport="local"),
            "webapp_fi": target("webapp_fi", transport="ssh"),
            "webapp_ir": target("webapp_ir", transport="object-storage-agent"),
            "witness": target("witness", transport="ssh"),
        }
        mappings = {}
        for name in BINDING_NAMES:
            payload = {"schema": f"fixture-{name}"}
            if name == "inventory":
                payload.update(
                    {
                        "campaign_id": campaign_id,
                        "release_sha": release,
                        "inventory_stage": "provisioned",
                        "host_safety_mode": "shared-host-safe",
                        "production_boundaries": {
                            "host_ips": ["203.0.113.10"],
                            "volume_ids": ["production-volume"],
                            "buckets": ["production-bucket"],
                            "domains": ["production.example"],
                        },
                        "roles": [
                            {
                                "role": role,
                                "host_ip": value["host_ip"],
                                "storage_root": value["storage_root"],
                            }
                            for role, value in roles.items()
                        ],
                    }
                )
            elif name == "failover_control_config":
                payload = {
                    "schema": "three-site-full-matrix-failover-control-v1",
                    "campaign_id": campaign_id,
                    "gate_group_id": gate_group_id,
                    "execution_class": "shared-host-safe",
                    "release_sha": release,
                    "backend_config": str(root / "failover-backend.json"),
                    "relay_credentials": str(root / "relay.env"),
                    "witness_relay_public_key_file": str(root / "witness.pub"),
                    "journal_root": str(root / "failover-journal"),
                }
            elif name == "destructive_control_config":
                payload = {
                    "schema": "three-site-full-matrix-destructive-control-v1",
                    "campaign_id": campaign_id,
                    "gate_group_id": gate_group_id,
                    "execution_class": "shared-host-safe",
                    "release_sha": release,
                    "enabled": False,
                    "provider_state_file": "",
                    "provider_token_file": "",
                    "audit_root": "",
                }
            path = root / f"{name}.json"
            path.write_text(
                json.dumps(payload, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            mappings[name] = path
        return campaign_id, gate_group_id, release, mappings, roles

    def test_builds_every_hash_binding_and_no_secret_values(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            campaign_id, gate_group_id, release, mappings, roles = self._fixtures(root)
            state = root / "state"
            result = build_payload(
                campaign_id=campaign_id,
                gate_group_id=gate_group_id,
                execution_class="shared-host-safe",
                release_sha=release,
                mappings=mappings,
                role_targets=roles,
                scenario_state_root=state,
            )
            self.assertEqual(result["schema"], "three-site-staging-full-matrix-live-plan-v1")
            self.assertTrue(result["production_forbidden"])
            self.assertEqual(result["roles"], roles)
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            for name, path in mappings.items():
                self.assertEqual(result[name]["path"], str(path))
                self.assertEqual(
                    result[name]["sha256"],
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )

    def test_rejects_incomplete_bindings_and_inventory_class_drift(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            campaign_id, gate_group_id, release, mappings, roles = self._fixtures(root)
            with self.assertRaises(LivePlanBuildError):
                build_payload(
                    campaign_id=campaign_id,
                    gate_group_id=gate_group_id,
                    execution_class="shared-host-safe",
                    release_sha=release,
                    mappings={
                        key: value
                        for key, value in mappings.items()
                        if key != "sync_timing_config"
                    },
                    role_targets=roles,
                    scenario_state_root=root / "state-a",
                )
            with self.assertRaises(LivePlanBuildError):
                build_payload(
                    campaign_id=campaign_id,
                    gate_group_id=gate_group_id,
                    execution_class="dedicated-host-destructive",
                    release_sha=release,
                    mappings=mappings,
                    role_targets=roles,
                    scenario_state_root=root / "state-b",
                )

    def test_shared_campaign_requires_object_storage_agent_for_wa_ir(self):
        """No shared-host plan may silently restore a direct Finland-to-Iran path."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            identity = root / "id"
            known_hosts = root / "known_hosts"
            agent_config = root / "webapp-ir-agent.json"
            for path, value in (
                (identity, "not-a-real-key\\n"),
                (known_hosts, "example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA==\\n"),
                (agent_config, "{}\\n"),
            ):
                path.write_text(value, encoding="utf-8")
                path.chmod(0o600)

            def target(role: str, *, transport: str) -> dict:
                common = {
                    "host_ip": f"203.0.113.{len(role)}",
                    "repo_root": str(root / f"{role}-repo"),
                    "compose_file": str(root / f"{role}.compose.yml"),
                    "env_file": str(root / f"{role}.env"),
                    "project_name": f"full-matrix-{role.replace('_', '-')}",
                    "storage_root": str(root / f"{role}-storage"),
                    "payload_transport": "object-storage" if role in {"webapp_ir", "witness"} else "local" if role == "bot_fi" else "direct-finland",
                    "command_prefix": [],
                }
                if transport == "ssh":
                    return {
                        **common,
                        "transport": "ssh",
                        "ssh_port": 22,
                        "ssh_user": "root",
                        "ssh_identity_file": str(identity),
                        "ssh_known_hosts_file": str(known_hosts),
                        "agent_config": "",
                    }
                return {
                    **common,
                    "transport": transport,
                    "ssh_port": 0,
                    "ssh_user": "",
                    "ssh_identity_file": "",
                    "ssh_known_hosts_file": "",
                    "agent_config": str(agent_config) if transport == "object-storage-agent" else "",
                }

            roles = {
                "bot_fi": target("bot_fi", transport="local"),
                "webapp_fi": target("webapp_fi", transport="ssh"),
                "webapp_ir": target("webapp_ir", transport="object-storage-agent"),
                "witness": target("witness", transport="ssh"),
            }
            inventory = {
                "host_safety_mode": "shared-host-safe",
                "roles": [
                    {
                        "role": role,
                        "host_ip": value["host_ip"],
                        "storage_root": value["storage_root"],
                    }
                    for role, value in roles.items()
                ],
            }
            validated = _validate_roles(
                roles,
                inventory=inventory,
                execution_class="shared-host-safe",
            )
            self.assertEqual(validated["webapp_ir"]["transport"], "object-storage-agent")

            drifted = json.loads(json.dumps(roles))
            drifted["webapp_ir"] = target("webapp_ir", transport="ssh")
            with self.assertRaises(LiveMatrixError):
                _validate_roles(
                    drifted,
                    inventory=inventory,
                    execution_class="shared-host-safe",
                )

            mappings = {}
            campaign_id = str(uuid.uuid4())
            release = "a" * 40
            for name in BINDING_NAMES:
                payload = {"schema": f"fixture-{name}"}
                if name == "inventory":
                    payload.update(
                        {
                            "campaign_id": campaign_id,
                            "release_sha": release,
                            "inventory_stage": "provisioned",
                            "host_safety_mode": "shared-host-safe",
                            "production_boundaries": {
                                "host_ips": ["203.0.113.10"],
                                "volume_ids": ["production-volume"],
                                "buckets": ["production-bucket"],
                                "domains": ["production.example"],
                            },
                            "roles": inventory["roles"],
                        }
                    )
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload, sort_keys=True) + "\\n", encoding="utf-8")
                path.chmod(0o600)
                mappings[name] = path
            with self.assertRaises(LivePlanBuildError):
                build_payload(
                    campaign_id=campaign_id,
                    gate_group_id=str(uuid.uuid4()),
                    execution_class="shared-host-safe",
                    release_sha=release,
                    mappings=mappings,
                    role_targets=drifted,
                    scenario_state_root=root / "rejected-state",
                )


if __name__ == "__main__":
    unittest.main()
