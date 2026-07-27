from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from unittest import mock
import unittest

from scripts.production_shadow_cutover_controller import (
    POSTCOMMIT_JOURNAL_STATUS,
    PRECOMMIT_JOURNAL_STATUS,
    REMOTE_AGENT_PATH,
    HOST_AGENT_CONTRACT_SHA256,
    host_agent_contract_document,
    render_plan,
    validate_manifest,
)
from scripts.production_shadow_host_agent import (
    BUSINESS_WRITE_FORWARD_ONLY,
    BUSINESS_WRITE_FORBIDDEN,
    FIXED_CONTRACT_PATH,
    HostAgentError,
    contract_sha256,
    main,
    parse_request_argv,
    request_sha256,
    validate_contract,
    validate_request,
)
from tests.test_production_shadow_cutover_controller import manifest_payload


CONTRACT = host_agent_contract_document()
AGENT_SHA256 = "c" * 64


def rendered_phases() -> list[dict]:
    plan = render_plan(
        validate_manifest(manifest_payload()),
        manifest_sha256="4" * 64,
    )
    return [
        *plan["phases"],
        *plan["postcommit_forward_recovery"]["commands"],
        *plan["rollback"]["commands"],
    ]


def agent_argv(command: dict) -> list[str]:
    argv = command["argv"]
    return argv[argv.index(REMOTE_AGENT_PATH) + 1 :]


class ProductionShadowHostAgentTests(unittest.TestCase):
    def test_every_controller_rendered_request_matches_agent_contract(self):
        seen_operations: set[str] = set()
        for phase in rendered_phases():
            for command in phase["commands"]:
                request, execute = parse_request_argv(
                    agent_argv(command),
                    contract=CONTRACT,
                    observed_agent_sha256=AGENT_SHA256,
                )
                self.assertFalse(execute)
                self.assertEqual(request["operation"], command["argv"][
                    command["argv"].index("--operation") + 1
                ])
                self.assertEqual(request["role"], command["role"])
                self.assertEqual(
                    request["required_journal_status"],
                    phase["required_journal_status"],
                )
                self.assertEqual(
                    request["business_write_policy"],
                    (
                        BUSINESS_WRITE_FORWARD_ONLY
                        if phase["business_write_allowed"]
                        else BUSINESS_WRITE_FORBIDDEN
                    ),
                )
                self.assertEqual(
                    len(
                        request_sha256(
                            request,
                            contract=CONTRACT,
                            observed_agent_sha256=AGENT_SHA256,
                        )
                    ),
                    64,
                )
                seen_operations.add(request["operation"])
        self.assertEqual(len(seen_operations), 35)

    def test_postcommit_and_precommit_journal_bindings_are_distinct(self):
        phases = rendered_phases()
        postcommit = [phase for phase in phases if phase["forward_only"]]
        precommit = [phase for phase in phases if not phase["forward_only"]]
        self.assertTrue(postcommit)
        self.assertTrue(precommit)
        self.assertTrue(
            all(
                phase["required_journal_status"] == POSTCOMMIT_JOURNAL_STATUS
                and phase["business_write_allowed"]
                for phase in postcommit
            )
        )
        self.assertTrue(
            all(
                phase["required_journal_status"] == PRECOMMIT_JOURNAL_STATUS
                and not phase["business_write_allowed"]
                for phase in precommit
            )
        )

    def test_role_host_operation_transport_and_path_tampering_fail_closed(self):
        phase = rendered_phases()[0]
        command = phase["commands"][0]
        request, _ = parse_request_argv(
            agent_argv(command),
            contract=CONTRACT,
            observed_agent_sha256=AGENT_SHA256,
        )
        mutations = {
            "expected_host": "127.0.0.1",
            "role": "webapp_ir",
            "operation": "unknown-operation",
            "payload_transport": "scp",
            "shadow_root": "/srv/trading-bot/current",
            "shadow_project": "trading_bot",
            "business_write_policy": BUSINESS_WRITE_FORWARD_ONLY,
            "required_journal_status": POSTCOMMIT_JOURNAL_STATUS,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                candidate = dict(request)
                candidate[field] = value
                with self.assertRaises(HostAgentError):
                    validate_request(
                        candidate,
                        contract=CONTRACT,
                        observed_agent_sha256=AGENT_SHA256,
                    )

    def test_artifact_vhost_and_request_shape_tampering_fail_closed(self):
        command = rendered_phases()[0]["commands"][0]
        request, _ = parse_request_argv(
            agent_argv(command),
            contract=CONTRACT,
            observed_agent_sha256=AGENT_SHA256,
        )

        zero_hash = dict(request)
        zero_hash["approval_sha256"] = "0" * 64
        with self.assertRaisesRegex(HostAgentError, "nonzero"):
            validate_request(
                zero_hash,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        wrong_image = dict(request)
        wrong_image["app_image_id"] = "latest"
        with self.assertRaisesRegex(HostAgentError, "immutable"):
            validate_request(
                wrong_image,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        wrong_vhost = dict(request)
        wrong_vhost["production_vhosts"] = {
            **request["production_vhosts"],
            "bot_fi": ["unexpected.example"],
        }
        with self.assertRaisesRegex(HostAgentError, "vhost"):
            validate_request(
                wrong_vhost,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        extra = dict(request)
        extra["unexpected"] = True
        with self.assertRaisesRegex(HostAgentError, "fields"):
            validate_request(
                extra,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

    def test_standalone_contract_and_agent_artifact_are_manifest_bound(self):
        self.assertEqual(
            contract_sha256(validate_contract(CONTRACT)),
            HOST_AGENT_CONTRACT_SHA256,
        )
        command = rendered_phases()[0]["commands"][0]
        request, _ = parse_request_argv(
            agent_argv(command),
            contract=CONTRACT,
            observed_agent_sha256=AGENT_SHA256,
        )
        self.assertEqual(
            request["host_agent_contract"],
            str(FIXED_CONTRACT_PATH),
        )
        with self.assertRaisesRegex(HostAgentError, "executable differs"):
            validate_request(
                request,
                contract=CONTRACT,
                observed_agent_sha256="9" * 64,
            )
        tampered = json.loads(json.dumps(CONTRACT))
        tampered["operations"][0]["roles"] = ["witness"]
        with self.assertRaisesRegex(HostAgentError, "contract digest"):
            validate_request(
                request,
                contract=tampered,
                observed_agent_sha256=AGENT_SHA256,
            )

    def test_validate_only_cli_is_non_mutating_and_execute_is_hard_blocked(self):
        command = rendered_phases()[0]["commands"][0]
        argv = agent_argv(command)
        request, _ = parse_request_argv(
            argv,
            contract=CONTRACT,
            observed_agent_sha256=AGENT_SHA256,
        )

        output = io.StringIO()
        with (
            mock.patch(
                "scripts.production_shadow_host_agent.hash_agent_artifact",
                return_value=AGENT_SHA256,
            ),
            mock.patch(
                "scripts.production_shadow_host_agent.read_contract",
                return_value=CONTRACT,
            ),
            mock.patch(
                "scripts.production_shadow_host_agent.observe_local_ipv4_addresses",
                return_value={request["expected_host"]},
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main(argv), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "validated-request")
        self.assertTrue(payload["host_identity_observed"])
        self.assertEqual(payload["agent_artifact_sha256"], AGENT_SHA256)
        self.assertEqual(
            payload["host_agent_contract_sha256"],
            HOST_AGENT_CONTRACT_SHA256,
        )
        self.assertFalse(payload["execution_supported"])
        self.assertFalse(payload["production_contacted"])

        output = io.StringIO()
        with (
            mock.patch(
                "scripts.production_shadow_host_agent.hash_agent_artifact",
                return_value=AGENT_SHA256,
            ),
            mock.patch(
                "scripts.production_shadow_host_agent.read_contract",
                return_value=CONTRACT,
            ),
            mock.patch(
                "scripts.production_shadow_host_agent.observe_local_ipv4_addresses",
                return_value={request["expected_host"]},
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main([*argv, "--execute"]), 2)
        blocked = json.loads(output.getvalue())
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("hard-disabled", blocked["error"])
        self.assertFalse(blocked["production_contacted"])

    def test_cli_requires_root_without_parsing_or_contacting_production(self):
        command = rendered_phases()[0]["commands"][0]
        output = io.StringIO()
        with mock.patch("os.geteuid", return_value=1000), redirect_stdout(output):
            self.assertEqual(main(agent_argv(command)), 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "blocked")
        self.assertIn("must run as root", payload["error"])
        self.assertFalse(payload["production_contacted"])


if __name__ == "__main__":
    unittest.main()
