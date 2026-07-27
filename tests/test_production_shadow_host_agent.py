from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest import mock
import unittest

from scripts import production_shadow_host_agent as HOST_MODULE
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
    execute_precommit_request,
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


def rendered_preparation() -> list[dict]:
    plan = render_plan(
        validate_manifest(manifest_payload()),
        manifest_sha256="4" * 64,
    )
    return list(plan["reversible_preparation"]["phases"])


def agent_argv(command: dict) -> list[str]:
    argv = command["argv"]
    return argv[argv.index(REMOTE_AGENT_PATH) + 1 :]


class ProductionShadowHostAgentTests(unittest.TestCase):
    def installed_precommit_manifest(
        self,
        root: Path,
        request: dict,
    ) -> tuple[dict, Path, Path]:
        role_path = request["role"].replace("_", "-")
        worker = (
            root
            / "project"
            / request["operation_id"]
            / "releases"
            / request["release_sha"]
            / "scripts"
            / "production_shadow_precommit_worker.py"
        )
        producer = worker.with_name(
            "produce_production_shadow_readonly_acceptance.py"
        )
        worker.parent.mkdir(parents=True, mode=0o700)
        worker.write_bytes(b"fixed-worker\n")
        producer.write_bytes(b"fixed-producer\n")
        worker.chmod(0o644)
        producer.chmod(0o644)

        manifest_path = (
            root
            / "secret"
            / request["operation_id"]
            / role_path
            / "precommit-operation.json"
        )
        manifest_path.parent.mkdir(parents=True, mode=0o700)
        document = {
            "schema": HOST_MODULE.PRECOMMIT_MANIFEST_SCHEMA,
            "operation_id": request["operation_id"],
            "role": request["role"],
            "release_sha": request["release_sha"],
            "release_tree_sha": "a" * 40,
            "controller_manifest_sha256": request["manifest_sha256"],
            "approval_sha256": request["approval_sha256"],
            "role_material_sha256": request["role_material_sha256"],
            "canonical_compose_sha256": request[
                "shadow_compose_sha256"
            ],
            "role_compose_sha256": "b" * 64,
            "environment_sha256": "c" * 64,
            "worker_sha256": hashlib.sha256(
                worker.read_bytes()
            ).hexdigest(),
            "acceptance_producer_sha256": hashlib.sha256(
                producer.read_bytes()
            ).hexdigest(),
            "image_artifacts": request["image_artifacts"],
            "runtime_image_ids": request["runtime_image_ids"],
            "artifacts": {
                "release-bundle": {
                    "sha256": request["release_bundle_sha256"],
                    "bytes": request["release_bundle_bytes"],
                    "restored_tree_sha256": None,
                },
                "role-material": {
                    "sha256": request["role_material_sha256"],
                    "bytes": request["role_material_bytes"],
                    "restored_tree_sha256": None,
                },
                "app-image-archive": {
                    "sha256": request["image_artifacts"]["app"][
                        "archive_sha256"
                    ],
                    "bytes": request["image_artifacts"]["app"][
                        "archive_bytes"
                    ],
                    "restored_tree_sha256": None,
                },
                "postgres-image-archive": {
                    "sha256": request[
                        "image_artifacts"
                    ]["postgres"]["archive_sha256"],
                    "bytes": request[
                        "image_artifacts"
                    ]["postgres"]["archive_bytes"],
                    "restored_tree_sha256": None,
                },
                "redis-image-archive": {
                    "sha256": request["image_artifacts"]["redis"][
                        "archive_sha256"
                    ],
                    "bytes": request["image_artifacts"]["redis"][
                        "archive_bytes"
                    ],
                    "restored_tree_sha256": None,
                },
                "nginx-image-archive": {
                    "sha256": request["image_artifacts"]["nginx"][
                        "archive_sha256"
                    ],
                    "bytes": request["image_artifacts"]["nginx"][
                        "archive_bytes"
                    ],
                    "restored_tree_sha256": None,
                },
                "database-backup": {
                    "sha256": "d" * 64,
                    "bytes": 10,
                    "restored_tree_sha256": None,
                },
                "uploads-archive": {
                    "sha256": "e" * 64,
                    "bytes": 11,
                    "restored_tree_sha256": "f" * 64,
                },
                "audit-archive": {
                    "sha256": "1" * 64,
                    "bytes": 12,
                    "restored_tree_sha256": "2" * 64,
                },
            },
            "source_database": {
                "alembic_revision": "source_1",
                "fingerprint_algorithm": (
                    "pg-copy-jsonl-sha256-canonical-session-v1"
                ),
                "database_fingerprint_sha256": "3" * 64,
                "row_count": 100,
                "table_count": 10,
            },
            "target_migration_revision": "source_1",
            "postgres_runtime_uid": 70,
            "postgres_runtime_gid": 70,
        }
        manifest_path.write_bytes(
            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        manifest_path.chmod(0o600)
        return document, manifest_path, worker

    def test_every_controller_rendered_request_matches_agent_contract(self):
        seen_operations: set[str] = set()
        for phase in [*rendered_preparation(), *rendered_phases()]:
            for command in phase["commands"]:
                request, execute = parse_request_argv(
                    agent_argv(command),
                    contract=CONTRACT,
                    observed_agent_sha256=AGENT_SHA256,
                )
                self.assertEqual(execute, phase["execution_supported"])
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
                for field in (
                    HOST_MODULE.NGINX_GENERATION_ARTIFACT_FIELDS.values()
                ):
                    self.assertEqual(
                        request[field],
                        manifest_payload()["artifacts"][field],
                    )
                operation = next(
                    row
                    for row in CONTRACT["operations"]
                    if row["operation"] == request["operation"]
                )
                self.assertEqual(
                    command["nginx_generation_bindings"],
                    {
                        state: request[
                            HOST_MODULE.NGINX_GENERATION_ARTIFACT_FIELDS[state]
                        ]
                        for state in operation["nginx_generations"]
                    },
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
        self.assertEqual(len(seen_operations), 40)

    def test_only_reversible_precommit_operation_invokes_fixed_worker_argv(self):
        command = rendered_preparation()[0]["commands"][0]
        request, execute = parse_request_argv(
            agent_argv(command),
            contract=CONTRACT,
            observed_agent_sha256=AGENT_SHA256,
        )
        self.assertTrue(execute)
        manifest = {
            "operation_id": request["operation_id"],
            "role": request["role"],
            "release_sha": request["release_sha"],
        }
        worker_result = {
            "status": "completed",
            "action": request["operation"],
            "operation_id": request["operation_id"],
            "role": request["role"],
        }
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(worker_result).encode("utf-8"),
            stderr=b"",
        )
        with (
            mock.patch(
                "scripts.production_shadow_host_agent._load_precommit_manifest",
                return_value=manifest,
            ),
            mock.patch(
                "scripts.production_shadow_host_agent.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            result = execute_precommit_request(request)
        self.assertEqual(result["status"], "executed-precommit")
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "/usr/bin/python3")
        self.assertNotIn("sh", argv)
        self.assertNotIn("bash", argv)
        self.assertEqual(
            argv[argv.index("--action") + 1],
            "verify-installation",
        )
        self.assertIn(
            (
                f"prepare-precommit:{request['operation_id']}:{request['role']}:"
                f"verify-installation:{request['release_sha']}"
            ),
            argv,
        )

    def test_fixed_precommit_manifest_and_release_worker_are_hash_bound(self):
        command = rendered_preparation()[0]["commands"][0]
        request, _execute = parse_request_argv(
            agent_argv(command),
            contract=CONTRACT,
            observed_agent_sha256=AGENT_SHA256,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document, manifest_path, worker = (
                self.installed_precommit_manifest(root, request)
            )
            with mock.patch.multiple(
                HOST_MODULE,
                PRECOMMIT_SECRET_ROOT=root / "secret",
                PRECOMMIT_PROJECT_ROOT=root / "project",
            ):
                observed = HOST_MODULE._load_precommit_manifest(request)
                self.assertEqual(observed, document)

                changed = dict(document)
                changed["controller_manifest_sha256"] = "9" * 64
                manifest_path.write_bytes(
                    json.dumps(
                        changed,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                with self.assertRaisesRegex(
                    HostAgentError,
                    "differs from the request",
                ):
                    HOST_MODULE._load_precommit_manifest(request)

                manifest_path.write_bytes(
                    json.dumps(
                        document,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                worker.write_bytes(b"changed-worker\n")
                with self.assertRaisesRegex(
                    HostAgentError,
                    "differs from the fixed manifest",
                ):
                    HOST_MODULE._load_precommit_manifest(request)

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

        campaign_project = dict(request)
        campaign_project["shadow_project"] = (
            f"tb3p-{request['campaign_id'].replace('-', '')}"
        )
        with self.assertRaisesRegex(HostAgentError, "operation-derived"):
            validate_request(
                campaign_project,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        campaign_root = dict(request)
        campaign_root["shadow_root"] = (
            "/srv/trading-bot-three-site-production-shadow/"
            f"{request['campaign_id']}"
        )
        with self.assertRaisesRegex(HostAgentError, "operation-derived"):
            validate_request(
                campaign_root,
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
        request_digest = request_sha256(
            request,
            contract=CONTRACT,
            observed_agent_sha256=AGENT_SHA256,
        )
        for field, replacement in (
            ("nginx_shadow_readonly_generation_sha256", "e" * 64),
            ("nginx_shadow_writable_generation_sha256", "d" * 64),
        ):
            with self.subTest(field=field):
                changed = dict(request)
                changed[field] = replacement
                self.assertNotEqual(
                    request_sha256(
                        changed,
                        contract=CONTRACT,
                        observed_agent_sha256=AGENT_SHA256,
                    ),
                    request_digest,
                )

        zero_hash = dict(request)
        zero_hash["approval_sha256"] = "0" * 64
        with self.assertRaisesRegex(HostAgentError, "nonzero"):
            validate_request(
                zero_hash,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        zero_readonly_generation = dict(request)
        zero_readonly_generation[
            "nginx_shadow_readonly_generation_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(HostAgentError, "nonzero"):
            validate_request(
                zero_readonly_generation,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        duplicate_generation = dict(request)
        duplicate_generation[
            "nginx_shadow_writable_generation_sha256"
        ] = request["nginx_shadow_readonly_generation_sha256"]
        with self.assertRaisesRegex(
            HostAgentError,
            "generation digests must be distinct",
        ):
            validate_request(
                duplicate_generation,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        wrong_image = json.loads(json.dumps(request))
        wrong_image["runtime_image_ids"]["app"] = "latest"
        with self.assertRaisesRegex(HostAgentError, "runtime image"):
            validate_request(
                wrong_image,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        duplicate_image = json.loads(json.dumps(request))
        duplicate_image["runtime_image_ids"]["nginx"] = (
            duplicate_image["runtime_image_ids"]["redis"]
        )
        with self.assertRaisesRegex(HostAgentError, "runtime image"):
            validate_request(
                duplicate_image,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        duplicate_archive = json.loads(json.dumps(request))
        duplicate_archive["image_artifacts"]["nginx"]["archive_sha256"] = (
            duplicate_archive["image_artifacts"]["redis"]["archive_sha256"]
        )
        with self.assertRaisesRegex(HostAgentError, "archive_sha256"):
            validate_request(
                duplicate_archive,
                contract=CONTRACT,
                observed_agent_sha256=AGENT_SHA256,
            )

        wrong_material_format = dict(request)
        wrong_material_format["role_material_format"] = (
            "production-shadow-witness-material-tar"
        )
        with self.assertRaisesRegex(HostAgentError, "material format"):
            validate_request(
                wrong_material_format,
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

        invalid_generation_contract = json.loads(json.dumps(CONTRACT))
        invalid_generation_contract["operations"][0][
            "nginx_generations"
        ] = ["shadow-writable"]
        with self.assertRaisesRegex(
            HostAgentError,
            "writable Nginx before the commit boundary",
        ):
            validate_contract(invalid_generation_contract)

        operations = {
            row["operation"]: row["nginx_generations"]
            for row in CONTRACT["operations"]
        }
        self.assertEqual(
            operations["switch-three-vhost-upstreams-shadow-readonly"],
            ["legacy-frozen", "shadow-readonly"],
        )
        self.assertEqual(
            operations["verify-pre-first-write-acceptance"],
            ["shadow-readonly", "shadow-writable"],
        )
        self.assertEqual(
            operations["activate-forward-only-three-vhost-generations"],
            ["shadow-writable"],
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
