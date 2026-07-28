from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

import yaml

from scripts import production_shadow_cutover_controller as CONTROLLER
from scripts import production_shadow_host_agent as HOST
from scripts import production_shadow_precommit_worker as WORKER
from scripts import wa_ir_production_operation as WA
from scripts.render_three_site_production_shadow_role_compose import (
    render_role_compose,
)
from tests.test_production_shadow_cutover_controller import manifest_payload


REPO_ROOT = Path(__file__).resolve().parents[1]


class ProductionShadowOperationIdentityContractTests(unittest.TestCase):
    def test_operation_uuid_defines_every_project_and_root(self):
        source = manifest_payload()
        manifest = CONTROLLER.validate_manifest(source)
        operation_id = manifest["operation_id"]
        release_sha = manifest["release_sha"]
        compact = operation_id.replace("-", "")
        project_base = f"tb3p-{compact}"
        project_root = (
            Path("/srv/trading-bot-three-site-production-shadow")
            / operation_id
        )
        data_root = (
            Path("/srv/trading-bot-three-site-production-shadow-data")
            / operation_id
        )
        secret_root = (
            Path(
                "/root/secure-envs/trading-bot/"
                "three-site-production-shadow"
            )
            / operation_id
        )
        self.assertEqual(
            manifest["deployment"]["shadow_compose_project"],
            project_base,
        )
        self.assertEqual(
            manifest["deployment"]["shadow_root"],
            str(project_root),
        )

        plan = CONTROLLER.render_plan(
            manifest,
            manifest_sha256="4" * 64,
        )
        for phase in plan["reversible_preparation"]["phases"]:
            for command in phase["commands"]:
                argv = command["argv"]
                agent_path = str(
                    CONTROLLER._remote_agent_path(
                        operation_id,
                        release_sha,
                    )
                )
                self.assertIn(agent_path, argv)
                agent_index = argv.index(agent_path)
                self.assertEqual(
                    argv[agent_index - 2 : agent_index],
                    ["/usr/bin/python3", "-I"],
                )
                request, execute = HOST.parse_request_argv(
                    argv[
                        agent_index
                        + 1 :
                    ],
                    contract=CONTROLLER.host_agent_contract_document(),
                    observed_agent_sha256="c" * 64,
                )
                self.assertTrue(execute)
                self.assertEqual(request["operation_id"], operation_id)
                self.assertEqual(request["shadow_project"], project_base)
                self.assertEqual(request["shadow_root"], str(project_root))
                self.assertEqual(
                    request["host_agent_contract"],
                    str(secret_root / "host-agent-contract.json"),
                )

        for role in ("bot_fi", "webapp_fi"):
            paths = WORKER.operation_paths(
                operation_id,
                release_sha,
                role,
            )
            role_path = role.replace("_", "-")
            self.assertEqual(paths.project_base, project_base)
            self.assertEqual(
                paths.project_name,
                f"{project_base}-{role_path}",
            )
            self.assertEqual(paths.project_root, project_root)
            self.assertEqual(paths.data_root, data_root)
            self.assertEqual(paths.secret_root, secret_root)
            self.assertEqual(
                paths.release_root,
                project_root / "releases" / release_sha,
            )

        self.assertEqual(WA._project_base(operation_id), project_base)
        wa_paths = WA._canonical_operation_paths(
            SimpleNamespace(
                operation_id=operation_id,
                release_sha=release_sha,
            )
        )
        self.assertEqual(wa_paths.project_root, project_root)
        self.assertEqual(wa_paths.data_root, data_root)
        self.assertEqual(wa_paths.secret_root, secret_root)
        self.assertEqual(
            wa_paths.release_root,
            project_root / "releases" / release_sha,
        )

        canonical = yaml.safe_load(
            (
                REPO_ROOT
                / "deploy"
                / "production"
                / "docker-compose.three-site-shadow.yml"
            ).read_text(encoding="utf-8")
        )
        for role in ("bot-fi", "webapp-fi"):
            rendered = render_role_compose(
                canonical,
                role=role,
                scope="precommit",
            )
            self.assertEqual(
                rendered["name"],
                (
                    "${PRODUCTION_SHADOW_PROJECT:"
                    "?operation-bound project is required}-"
                    f"{role}"
                ),
            )
            operation = rendered["x-production-shadow-operation"]
            self.assertEqual(
                operation["operation_id"],
                (
                    "${PRODUCTION_SHADOW_OPERATION_ID:"
                    "?operation UUID is required}"
                ),
            )
            self.assertEqual(
                operation["project_root"],
                (
                    "${PRODUCTION_SHADOW_PROJECT_ROOT:"
                    "?operation-bound project root is required}"
                ),
            )


if __name__ == "__main__":
    unittest.main()
