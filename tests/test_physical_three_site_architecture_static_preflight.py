import ast
import importlib.util
import io
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core import physical_three_site_architecture_static_preflight as static_preflight


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "scripts" / "check_physical_three_site_architecture_static_preflight.py"

spec = importlib.util.spec_from_file_location("three_site_static_preflight_cli", CLI_PATH)
cli = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)


class PhysicalThreeSiteArchitectureStaticPreflightTests(unittest.TestCase):
    def source_texts(self) -> dict[str, str]:
        return {
            path: (REPO_ROOT / path).read_text(encoding="utf-8")
            for path in static_preflight.ARCHITECTURE_STATIC_ARTIFACT_PATHS
        }

    def test_default_off_refuses_before_any_artifact_read(self):
        with patch.object(static_preflight, "_read_artifact_texts", side_effect=AssertionError("read")):
            with self.assertRaises(static_preflight.PhysicalThreeSiteArchitectureStaticPreflightError) as raised:
                static_preflight.inspect_physical_three_site_architecture_static_preflight(
                    config=static_preflight.PhysicalThreeSiteArchitectureStaticPreflightConfig()
                )

        self.assertEqual(raised.exception.code, "THREE_SITE_STATIC_PREFLIGHT_DISABLED")

    def test_only_the_exact_normal_and_promoted_object_storage_routes_are_accepted(self):
        bad_route = replace(
            static_preflight.APPROVED_THREE_SITE_ROUTE_DECLARATIONS[0],
            transport="direct-scp",
        )
        config = static_preflight.PhysicalThreeSiteArchitectureStaticPreflightConfig(
            enabled=True,
            repository_root=REPO_ROOT,
            route_declarations=(
                bad_route,
                static_preflight.APPROVED_THREE_SITE_ROUTE_DECLARATIONS[1],
            ),
        )

        with self.assertRaises(static_preflight.PhysicalThreeSiteArchitectureStaticPreflightError) as raised:
            static_preflight.inspect_physical_three_site_architecture_static_preflight(config=config)

        self.assertEqual(raised.exception.code, "THREE_SITE_STATIC_PREFLIGHT_ROUTE_DECLARATIONS_INVALID")

    def test_current_bounded_artifacts_pass_without_execution_authority(self):
        report = static_preflight.require_physical_three_site_architecture_static_preflight(
            config=static_preflight.PhysicalThreeSiteArchitectureStaticPreflightConfig(
                enabled=True,
                repository_root=REPO_ROOT,
            )
        )

        self.assertEqual(report.status, "passed")
        self.assertEqual(
            report.approved_route_ids,
            ("normal-fi-object-storage-ir", "promoted-ir-object-storage-fi"),
        )
        self.assertTrue(report.static_only)
        self.assertFalse(report.execution_authorized)

    def test_active_direct_command_route_is_rejected(self):
        texts = self.source_texts()
        path = "core/physical_full_matrix_execution_driver_v4.py"
        texts[path] += '\nforbidden = ["scp", "peer:/payload"]\n'

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_DIRECT_COMMAND_ROUTE_FORBIDDEN"
                for item in findings
            )
        )

    def test_nonempty_postgres_streaming_route_is_rejected(self):
        texts = self.source_texts()
        path = "deploy/physical-postgres/standby-postgresql.conf.template"
        texts[path] = texts[path].replace("primary_conninfo = ''", "primary_conninfo = 'host=peer'")

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_POSTGRES_STREAMING_ROUTE_FORBIDDEN"
                for item in findings
            )
        )

    def test_importable_forensic_bypass_is_rejected(self):
        texts = self.source_texts()
        path = "scripts/run_production_full_matrix.py"
        texts[path] = texts[path].replace("@_retire_legacy_forensic_source\ndef _forensic_iran_command", "def _forensic_iran_command", 1)

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_FORENSIC_IMPORT_BYPASS"
                for item in findings
            )
        )

    def test_local_static_delivery_verifier_cannot_regress_to_manifest_peer_probe(self):
        texts = self.source_texts()
        path = "scripts/report_static_delivery.py"
        texts[path] = texts[path].replace(
            'LOCAL_STATIC_DELIVERY_URL = "http://127.0.0.1"',
            'LOCAL_STATIC_DELIVERY_URL = "https://iran.example"',
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_STATIC_DELIVERY_LOCAL_FENCE_MISSING"
                for item in findings
            )
        )

    def test_generic_fi_writer_mode_cannot_be_reintroduced(self):
        texts = self.source_texts()
        path = "scripts/production_writer_lease_agent.py"
        texts[path] = texts[path].replace(
            'if mode == "writer" and site == "webapp_fi":',
            'if False:',
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_FI_WRITER_GENERIC_MODE_FENCE_MISSING"
                for item in findings
            )
        )

    def test_root_compose_direct_sync_disable_cannot_be_removed(self):
        texts = self.source_texts()
        path = "docker-compose.yml"
        texts[path] = texts[path].replace(
            '      TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"',
            '      # TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"',
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_ROOT_COMPOSE_DIRECT_SYNC_FENCE_MISSING"
                for item in findings
            )
        )

    def test_root_compose_cannot_reintroduce_iran_peer_pin(self):
        texts = self.source_texts()
        path = "docker-compose.yml"
        texts[path] = texts[path].replace(
            "    depends_on:\n",
            "    extra_hosts:\n      - 'wa-ir.example:203.0.113.7'\n    depends_on:\n",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_ROOT_COMPOSE_DIRECT_SYNC_FENCE_MISSING"
                for item in findings
            )
        )

    def test_root_compose_cannot_drop_the_default_runtime_retirement_guard(self):
        texts = self.source_texts()
        path = "docker-compose.yml"
        texts[path] = texts[path].replace(
            '    profiles: ["legacy-local-development"]\n',
            "    # profile removed\n",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_ROOT_COMPOSE_RUNTIME_RETIREMENT_GUARD_MISSING"
                for item in findings
            )
        )

    def test_root_compose_cannot_drop_service_level_development_acknowledgement(self):
        texts = self.source_texts()
        path = "docker-compose.iran.yml"
        texts[path] = texts[path].replace(
            "I_UNDERSTAND_THIS_IS_LOCAL_DEVELOPMENT_ONLY",
            "ACK_REMOVED",
            2,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_ROOT_COMPOSE_RUNTIME_RETIREMENT_GUARD_MISSING"
                for item in findings
            )
        )

    def test_every_current_core_peer_url_caller_is_in_the_retirement_inventory(self):
        expected = {
            path
            for path, _callable, _later_markers in static_preflight._PEER_SERVER_URL_FOR_CALLER_FENCES
            if path.startswith("core/")
        }
        expected.update(
            {
                "core/server_routing.py",
                "core/physical_three_site_architecture_static_preflight.py",
            }
        )
        observed = {
            str(path.relative_to(REPO_ROOT))
            for path in (REPO_ROOT / "core").rglob("*.py")
            if "peer_server_url_for(" in path.read_text(encoding="utf-8")
        }

        self.assertEqual(observed, expected)

    def test_script_peer_url_callers_cannot_escape_the_retirement_inventory(self):
        registered = {
            path
            for path, _callable, _later_markers in static_preflight._PEER_SERVER_URL_FOR_CALLER_FENCES
            if path.startswith("scripts/")
        }
        observed = {
            str(path.relative_to(REPO_ROOT))
            for path in (REPO_ROOT / "scripts").rglob("*.py")
            if "peer_server_url_for(" in path.read_text(encoding="utf-8")
        }

        self.assertIn("scripts/dev_admin.py", registered)
        self.assertTrue(observed.issubset(registered))

    def test_every_direct_peer_url_factory_caller_is_in_the_bounded_inventory(self):
        """Make a new direct peer URL factory call an explicit review event.

        ``peer_server_url_for`` and ``default_peer_server_url`` are the two
        remaining compatibility factories which can turn configuration into a
        FI<->IR destination.  The allowed entries below are either the local
        routing helper itself, or a separately checked fail-closed legacy
        boundary.  This walks all production Python sources rather than only
        the checker input list, so a newly added caller cannot evade review by
        simply living in a new file.
        """

        factory_names = {"peer_server_url_for", "default_peer_server_url"}
        observed: set[str] = set()
        for source_root in ("core", "api", "scripts"):
            for path in (REPO_ROOT / source_root).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    callee = node.func
                    name = (
                        callee.id
                        if isinstance(callee, ast.Name)
                        else callee.attr
                        if isinstance(callee, ast.Attribute)
                        else None
                    )
                    if name in factory_names:
                        observed.add(str(path.relative_to(REPO_ROOT)))

        expected = {
            "api/routers/sync.py",  # permanent 410 legacy router fence
            "core/customer_invite.py",
            "core/customer_invite_forwarding.py",
            "core/invitation_creation_forwarding.py",
            "core/offer_expiry_forwarding.py",
            "core/server_routing.py",  # role-local compatibility helper
            "core/session_authority.py",
            "core/sync_push.py",
            "core/sync_worker.py",
            "core/telegram_otp_transport.py",
            "core/telegram_registration_transport.py",
            "core/trade_forwarding.py",
            "scripts/trading_core_probe_worker.py",
        }

        self.assertEqual(observed, expected)

    def test_core_peer_url_caller_cannot_move_its_fence_below_url_construction(self):
        texts = self.source_texts()
        path = "core/trade_forwarding.py"
        texts[path] = texts[path].replace(
            "    try:\n        assert_legacy_direct_fi_ir_transport_retired(\n            component=\"trade-forwarding\",",
            "    target_url = peer_server_url_for(target_server)\n    try:\n        assert_legacy_direct_fi_ir_transport_retired(\n            component=\"trade-forwarding\",",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_CORE_PEER_SERVER_URL_FENCE_MISSING"
                for item in findings
            )
        )

    def test_dev_admin_cannot_reintroduce_peer_session_reset_before_its_fence(self):
        texts = self.source_texts()
        path = "scripts/dev_admin.py"
        texts[path] = texts[path].replace(
            "async def forward_remote_session_reset(user: User, target_server: str) -> tuple[int, dict]:\n    try:\n",
            "async def forward_remote_session_reset(user: User, target_server: str) -> tuple[int, dict]:\n"
            "    target_url = peer_server_url_for(target_server)\n"
            "    try:\n",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_CORE_PEER_SERVER_URL_FENCE_MISSING"
                for item in findings
            )
        )

    def test_staging_direct_sync_profile_cannot_be_reintroduced(self):
        texts = self.source_texts()
        path = "deploy/staging/docker-compose.staging.yml"
        texts[path] = texts[path].replace(
            '      TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"',
            '      TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "0"',
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_STAGING_DIRECT_TRANSPORT_FENCE_MISSING"
                for item in findings
            )
        )

    def test_core_direct_sync_factory_cannot_lose_its_early_fence(self):
        texts = self.source_texts()
        path = "core/sync_push.py"
        texts[path] = texts[path].replace(
            "assert_legacy_direct_fi_ir_transport_retired(",
            "legacy_direct_fi_ir_transport_fence_removed(",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_CORE_DIRECT_TRANSPORT_FENCE_MISSING"
                for item in findings
            )
        )

    def test_legacy_sync_router_permanent_fence_cannot_move_after_peer_work(self):
        texts = self.source_texts()
        path = "api/routers/sync.py"
        texts[path] = texts[path].replace(
            "    _reject_retired_legacy_direct_sync_transport()\n    logger.info(",
            "    logger.info(\n        # permanent fence moved after work\n    _reject_retired_legacy_direct_sync_transport()\n",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_LEGACY_SYNC_ROUTER_PERMANENT_FENCE_MISSING"
                for item in findings
            )
        )

    def test_sync_parity_compare_cannot_regress_to_arbitrary_peer_http(self):
        texts = self.source_texts()
        path = "scripts/compare_sync_parity.py"
        texts[path] = texts[path].replace(
            "    _assert_compare_url_inputs_are_role_local(args)\n",
            "    # URL fence removed\n",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_SYNC_PARITY_HTTP_FENCE_MISSING"
                for item in findings
            )
        )

    def test_worker_http_benchmark_cannot_regress_to_peer_http(self):
        texts = self.source_texts()
        path = "scripts/report_worker_http_benchmark.py"
        texts[path] = texts[path].replace(
            "    base_url = _require_role_local_benchmark_url(str(args.base_url))\n",
            "    base_url = str(args.base_url)\n",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_WORKER_HTTP_BENCHMARK_FENCE_MISSING"
                for item in findings
            )
        )

    def test_legacy_nginx_templates_cannot_reopen_direct_sync_ingress(self):
        texts = self.source_texts()
        path = "deploy/production/nginx-iran-recovery-https.conf.template"
        texts[path] = texts[path].replace("return 410;", "proxy_pass http://peer.example;", 1)

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_NGINX_DIRECT_SYNC_INGRESS_FENCE_MISSING"
                for item in findings
            )
        )

    def test_target_nginx_cannot_reopen_a_broad_legacy_internal_forwarder(self):
        texts = self.source_texts()
        path = "deploy/production/nginx-iran-online-https.conf.template"
        texts[path] = texts[path].replace(
            "location ~ ^/api/(sync|sessions/internal|trades/internal|offers/internal|auth/internal|invitations/internal|customers/internal)(/|$) {\n        access_log off;\n        return 410;",
            "location ~ ^/api/(sync|sessions/internal|trades/internal|offers/internal|auth/internal|invitations/internal|customers/internal)(/|$) {\n        proxy_pass http://peer.example;",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_NGINX_DIRECT_SYNC_INGRESS_FENCE_MISSING"
                for item in findings
            )
        )

    def test_staging_nginx_cannot_reopen_foreign_sync_alias(self):
        texts = self.source_texts()
        path = "deploy/staging/nginx-staging.conf.template"
        prefix, foreign_block = texts[path].split("location ^~ /foreign-sync/", 1)
        texts[path] = prefix + "location ^~ /foreign-sync/" + foreign_block.replace(
            "return 410;",
            "proxy_pass http://peer.example;",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_NGINX_DIRECT_SYNC_INGRESS_FENCE_MISSING"
                for item in findings
            )
        )

    def test_target_nginx_cannot_restore_a_foreign_ip_allowlist(self):
        texts = self.source_texts()
        path = "deploy/production/nginx-iran-online.conf.template"
        texts[path] = texts[path].replace(
            "return 410;",
            "allow __FOREIGN_PUBLIC_IP__;\n        return 410;",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_NGINX_DIRECT_SYNC_INGRESS_FENCE_MISSING"
                for item in findings
            )
        )

    def test_inert_nginx_listener_cannot_gain_an_application_upstream(self):
        texts = self.source_texts()
        path = "deploy/production/nginx-webapp-ir-standby-dark-https.conf.template"
        texts[path] = texts[path].replace("return 503;", "proxy_pass http://peer.example;", 1)

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_NGINX_INERT_LISTENER_FENCE_MISSING"
                for item in findings
            )
        )

    def test_fd_only_recovery_boundary_cannot_drop_its_object_storage_transport_binding(self):
        texts = self.source_texts()
        path = "core/physical_wa_ir_postgres_recovery_fd_boundary.py"
        texts[path] = texts[path].replace(
            'payload.get("destination_object_ingest") != "pull-only"',
            'payload.get("destination_object_ingest") != "peer-http"',
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_REQUIRED_MARKER_MISSING"
                for item in findings
            )
        )

    def test_stage_l_pool_matrix_cannot_lose_its_direct_transport_fence(self):
        texts = self.source_texts()
        path = "scripts/run_stage_l_pool_matrix.py"
        texts[path] = texts[path].replace(
            "assert_legacy_direct_fi_ir_transport_retired(",
            "legacy_direct_fi_ir_transport_fence_removed(",
            1,
        )

        findings = static_preflight.lint_physical_three_site_architecture_artifacts(texts)

        self.assertTrue(
            any(
                item.artifact_path == path
                and item.code == "THREE_SITE_STATIC_PREFLIGHT_LEGACY_DIRECT_TRANSPORT_FENCE_MISSING"
                for item in findings
            )
        )

    def test_cli_is_default_off_and_reports_no_execution_authority(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = cli.main([])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["error"], "THREE_SITE_STATIC_PREFLIGHT_DISABLED")
        self.assertFalse(payload["execution_authorized"])


if __name__ == "__main__":
    unittest.main()
