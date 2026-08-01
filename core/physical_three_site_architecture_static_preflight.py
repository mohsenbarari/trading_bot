"""Default-off static architecture fence for the three-site Full-Matrix path.

This checker reads only repository text after an explicit local opt-in.  It
does not load configuration, contact a host, invoke a subprocess, inspect a
provider, open Object Storage, or read credentials.  Its purpose is narrow:
keep the executable surface on exactly the two approved data declarations:

* normal ``WA-FI -> private versioned Object Storage -> WA-IR``; and
* promoted ``WA-IR -> private versioned Object Storage -> WA-FI``.

Direct FI<->IR SSH/SCP/rsync/SFTP, PostgreSQL streaming/DSN routes, and
unfenced two-server Full-Matrix emitters are rejected as static architecture
regressions.  Findings contain a relative path, fixed code, and line number
only; no artifact contents or secret-like material are returned.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Final, Mapping


__all__ = (
    "APPROVED_THREE_SITE_ROUTE_DECLARATIONS",
    "PHYSICAL_THREE_SITE_ARCHITECTURE_STATIC_PREFLIGHT_DEFAULT_ENABLED",
    "PHYSICAL_THREE_SITE_ARCHITECTURE_STATIC_PREFLIGHT_SCHEMA",
    "PhysicalThreeSiteArchitectureStaticFinding",
    "PhysicalThreeSiteArchitectureStaticPreflightConfig",
    "PhysicalThreeSiteArchitectureStaticPreflightError",
    "PhysicalThreeSiteArchitectureStaticPreflightReport",
    "PhysicalThreeSiteRouteDeclaration",
    "inspect_physical_three_site_architecture_static_preflight",
    "lint_physical_three_site_architecture_artifacts",
    "require_physical_three_site_architecture_static_preflight",
)


PHYSICAL_THREE_SITE_ARCHITECTURE_STATIC_PREFLIGHT_SCHEMA: Final = (
    "gold-trade-physical-three-site-architecture-static-preflight-v1"
)
PHYSICAL_THREE_SITE_ARCHITECTURE_STATIC_PREFLIGHT_DEFAULT_ENABLED: Final = False

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
_OBJECT_STORAGE_TRANSPORT: Final = "private-versioned-object-storage"
_DIRECT_CONTROL_FORBIDDEN: Final = "forbidden"
_PULL_ONLY: Final = "pull-only"


@dataclass(frozen=True)
class PhysicalThreeSiteRouteDeclaration:
    """One public route declaration, never an endpoint or credential."""

    route_id: str
    activation: str
    source_site: str
    destination_site: str
    writer_site: str
    transport: str
    direct_site_control: str
    destination_object_ingest: str


APPROVED_THREE_SITE_ROUTE_DECLARATIONS: Final = (
    PhysicalThreeSiteRouteDeclaration(
        route_id="normal-fi-object-storage-ir",
        activation="normal-fi-writer",
        source_site="webapp_fi",
        destination_site="webapp_ir",
        writer_site="webapp_fi",
        transport=_OBJECT_STORAGE_TRANSPORT,
        direct_site_control=_DIRECT_CONTROL_FORBIDDEN,
        destination_object_ingest=_PULL_ONLY,
    ),
    PhysicalThreeSiteRouteDeclaration(
        route_id="promoted-ir-object-storage-fi",
        activation="promoted-ir-writer",
        source_site="webapp_ir",
        destination_site="webapp_fi",
        writer_site="webapp_ir",
        transport=_OBJECT_STORAGE_TRANSPORT,
        direct_site_control=_DIRECT_CONTROL_FORBIDDEN,
        destination_object_ingest=_PULL_ONLY,
    ),
)


@dataclass(frozen=True)
class PhysicalThreeSiteArchitectureStaticPreflightConfig:
    """Default-off local policy for repository-text architecture review."""

    enabled: bool = PHYSICAL_THREE_SITE_ARCHITECTURE_STATIC_PREFLIGHT_DEFAULT_ENABLED
    repository_root: Path = _REPOSITORY_ROOT
    route_declarations: tuple[PhysicalThreeSiteRouteDeclaration, ...] = (
        APPROVED_THREE_SITE_ROUTE_DECLARATIONS
    )


@dataclass(frozen=True)
class PhysicalThreeSiteArchitectureStaticFinding:
    artifact_path: str
    code: str
    line: int | None = None


@dataclass(frozen=True)
class PhysicalThreeSiteArchitectureStaticPreflightReport:
    schema: str
    status: str
    approved_route_ids: tuple[str, ...]
    checked_artifacts: tuple[str, ...]
    findings: tuple[PhysicalThreeSiteArchitectureStaticFinding, ...]
    direct_fi_to_ir_control: str = _DIRECT_CONTROL_FORBIDDEN
    direct_ir_to_fi_control: str = _DIRECT_CONTROL_FORBIDDEN
    static_only: bool = True
    execution_authorized: bool = False


class PhysicalThreeSiteArchitectureStaticPreflightError(ValueError):
    """A fixed-code refusal before static architecture review can be trusted."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _LegacyPythonArtifact:
    path: str
    public_functions: tuple[str, ...]
    forbidden_public_helpers: tuple[str, ...]


@dataclass(frozen=True)
class _LegacyDirectTransportPythonArtifact:
    """A reachable historical peer-route factory that must fail before argv."""

    path: str
    fenced_callables: tuple[str, ...]
    require_cli_payload: bool = False


_ACTIVE_ARTIFACT_REQUIREMENTS: Final[dict[str, tuple[str, ...]]] = {
    "core/physical_wa_ir_bootstrap_bundle_builder.py": (
        "PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_DEFAULT_ENABLED = False",
        '_SOURCE_SITE = "webapp_fi"',
        '_DESTINATION_SITE = "webapp_ir"',
        'direct_fi_to_ir_control: str = "forbidden"',
    ),
    "core/physical_ir_to_fi_object_storage_failback_preflight.py": (
        "PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_DEFAULT_ENABLED = False",
        '_SOURCE_SITE = "webapp_ir"',
        '_DESTINATION_SITE = "webapp_fi"',
        '_DIRECT_CONTROL = "forbidden"',
        '_DESTINATION_INGEST = "pull-only"',
    ),
    "core/physical_full_matrix_execution_driver_v4.py": (
        "PHYSICAL_FULL_MATRIX_V4_EXECUTION_DEFAULT_ENABLED = False",
        '_NORMAL_DIRECTION = ("webapp_fi", "webapp_ir")',
        '_REVERSE_DIRECTION = ("webapp_ir", "webapp_fi")',
        '_DIRECT_CONTROL_FORBIDDEN = "forbidden"',
        "ir-private-versioned-object-storage-pull-v2",
        "fi-private-versioned-object-storage-pull-v2",
    ),
    "core/physical_full_matrix_v4_materialization_preflight.py": (
        "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DEFAULT_ENABLED: Final = False",
        "direct_fi_to_ir_control: str = _FORBIDDEN",
        "direct_ir_to_fi_control: str = _FORBIDDEN",
    ),
    "core/physical_full_matrix_v4_phase_installation_provenance.py": (
        "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_DEFAULT_ENABLED: Final = False",
        '"direct_fi_to_ir_control": _FORBIDDEN',
        '"direct_ir_to_fi_control": _FORBIDDEN',
        '"host_provider_installation_authorized": False',
        '"execution_authorized": False',
    ),
    "core/physical_wa_ir_postgres_recovery_fd_boundary.py": (
        'PHYSICAL_WA_IR_POSTGRES_RECOVERY_FD_BOUNDARY_SCHEMA =',
        '"socket-only-standby-materialization"',
        'payload.get("direct_site_control") != "forbidden"',
        'payload.get("destination_object_ingest") != "pull-only"',
    ),
    "scripts/run_physical_full_matrix_v4.py": (
        "PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_RUNNER_DEFAULT_ENABLED: Final = False",
        '"direct_fi_to_ir_control": "forbidden"',
        '"direct_ir_to_fi_control": "forbidden"',
        "non_operational",
    ),
    "deploy/physical-postgres/primary-postgresql.conf.template": (
        "listen_addresses = ''",
        "primary_conninfo = ''",
        "primary_slot_name = ''",
        "synchronous_standby_names = ''",
        "archive_command = 'exec @@WAL_SPOOL_BINARY@@",
    ),
    "deploy/physical-postgres/standby-postgresql.conf.template": (
        "listen_addresses = ''",
        "primary_conninfo = ''",
        "primary_slot_name = ''",
        "synchronous_standby_names = ''",
        "restore_command = 'exec @@STANDBY_PULL_BINARY@@",
    ),
    "deploy/production/docker-compose.webapp-fi-writer-2c08.yml": (
        "APPLICATION_WRITER_TERM_LOCAL_SITE: webapp_fi",
        'TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"',
        'SINGLE_WRITER_RUNTIME_ENABLED: "true"',
    ),
    "deploy/production/docker-compose.webapp-ir-promoted-2c08.yml": (
        "APPLICATION_WRITER_TERM_LOCAL_SITE: webapp_ir",
        'TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"',
        'SINGLE_WRITER_RUNTIME_ENABLED: "true"',
    ),
}

_LEGACY_PYTHON_ARTIFACTS: Final = (
    _LegacyPythonArtifact(
        path="scripts/run_production_full_matrix.py",
        public_functions=(
            "build_plan",
            "build_preflight_commands",
            "build_execution_plan",
            "execute_scenario_plan",
            "execute_command_plan",
            "main",
        ),
        forbidden_public_helpers=(
            "iran_command",
            "container_python_command",
            "copy_between_servers_command",
        ),
    ),
    _LegacyPythonArtifact(
        path="scripts/plan_production_full_matrix.py",
        public_functions=("build_plan", "main"),
        forbidden_public_helpers=("iran", "iran_compose", "cleanup_command"),
    ),
    _LegacyPythonArtifact(
        path="scripts/build_production_full_matrix_manifest.py",
        public_functions=("build_manifest", "main"),
        forbidden_public_helpers=(),
    ),
    _LegacyPythonArtifact(
        path="scripts/run_staging_two_server_full_matrix.py",
        public_functions=(
            "remote_shell_command",
            "scp_from_iran",
            "scp_to_iran",
            "build_manifest",
            "build_plan",
            "preflight_checks",
            "run_preflight",
            "run_execute",
            "main",
        ),
        forbidden_public_helpers=(
            "iran_ssh_command",
            "remote_load_runner_command",
            "copy_prepare_artifacts_to_peer",
        ),
    ),
    _LegacyPythonArtifact(
        path="scripts/build_staging_two_server_full_matrix_manifest.py",
        public_functions=("build_manifest", "main"),
        forbidden_public_helpers=(),
    ),
)

_LEGACY_BASH_ARTIFACTS: Final = (
    "scripts/production_deploy_online.sh",
    "scripts/recover_cross_server_sync.sh",
)

# These files remain for strictly host-local helpers, artifact parsing, or
# forensic compatibility.  Every listed callable is nevertheless an old
# FI<->IR direct command/control factory and must contain the tiny fail-closed
# fence itself.  Checking the factory, rather than merely the CLI or an
# ``--execute`` flag, prevents import-level or planner-level bypasses.
_LEGACY_DIRECT_TRANSPORT_PYTHON_ARTIFACTS: Final = (
    # Every current core `peer_server_url_for` caller is registered here.  A
    # dedicated source-inventory regression test prevents an unregistered
    # caller from becoming an invisible direct FI<->IR path.
    _LegacyDirectTransportPythonArtifact(
        path="core/trade_forwarding.py",
        fenced_callables=("forward_trade_to_home_server",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/offer_expiry_forwarding.py",
        fenced_callables=("forward_offer_expiry_to_home_server",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/session_authority.py",
        fenced_callables=("fetch_remote_session_authority",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/telegram_registration_transport.py",
        fenced_callables=("_post_signed_iran_command",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/telegram_otp_transport.py",
        fenced_callables=("forward_telegram_otp_delivery",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/customer_invite_forwarding.py",
        fenced_callables=("forward_customer_invite_to_iran",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/customer_invite.py",
        fenced_callables=("_fetch_iran_sync_health", "check_customer_invite_sync_ready"),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/invitation_creation_forwarding.py",
        fenced_callables=("forward_standard_invitation_to_iran",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/sync_push.py",
        fenced_callables=("_get_client", "_do_push", "push_sync_direct"),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/sync_worker.py",
        fenced_callables=("send_sync_item", "main"),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/connectivity.py",
        fenced_callables=("_iran_connectivity_target_url",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="core/notifications.py",
        fenced_callables=("send_telegram_message",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_static_delivery.py",
        fenced_callables=("fetch_url",),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/capture_production_baseline.py",
        fenced_callables=("remote_args", "remote_compose_args", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/run_production_backup.py",
        fenced_callables=(
            "target_for_role",
            "build_backup_shell",
            "backup_role",
            "pull_iran_files",
            "main",
        ),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_production_alerts.py",
        fenced_callables=("run_shell", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/sample_sync_health.py",
        fenced_callables=("build_iran_ssh_command",),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_observability_readiness.py",
        fenced_callables=(
            "Runner.compose_args",
            "Runner.host_python_json",
            "run_benchmark",
            "main",
        ),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/run_worker_pool_matrix.py",
        fenced_callables=("ssh_base", "run_matrix", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_cross_server_sync_benchmark.py",
        fenced_callables=("Runner.compose_args", "run_benchmark", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_trading_core_benchmark.py",
        fenced_callables=("Runner.compose_args", "run_benchmark", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_frontend_ux_benchmark.py",
        fenced_callables=("Runner.compose_args", "run_benchmark", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/run_sync_parity_stage9_production_rollout.py",
        fenced_callables=("ssh_args", "build_plan", "execute_plan", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_deployment_restart_benchmark.py",
        fenced_callables=(
            "Runner.compose_args",
            "Runner.remote_json",
            "run_benchmark",
            "main",
        ),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_production_load_fixtures.py",
        fenced_callables=(
            "Runner.compose_args",
            "sync_worker_compose_args",
            "run_report",
            "main",
        ),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/run_production_benchmark.py",
        fenced_callables=("_iran_compose_args", "build_tasks", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/run_stage_l_pool_matrix.py",
        fenced_callables=("run_matrix", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/trading_core_probe_worker.py",
        fenced_callables=(
            "push_prefix_change_logs_to_peer",
            "sync_prefix_catchup_command",
            "main",
        ),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/seed_shared_sync_tables.py",
        fenced_callables=("send_items", "main_async"),
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/sync_repair_tool.py",
        fenced_callables=("_target_url", "_send_items", "replay_row_command", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/report_final_release_gate.py",
        fenced_callables=("run_live_checks", "run_gate", "main"),
        require_cli_payload=True,
    ),
    _LegacyDirectTransportPythonArtifact(
        path="scripts/dev_admin.py",
        fenced_callables=("forward_remote_session_reset",),
    ),
)
_PEER_SERVER_URL_FOR_CALLER_FENCES: Final = (
    (
        "core/trade_forwarding.py",
        "forward_trade_to_home_server",
        ("peer_server_url_for(", "_json_body(", "sign_internal_payload(", "httpx.AsyncClient("),
    ),
    (
        "core/offer_expiry_forwarding.py",
        "forward_offer_expiry_to_home_server",
        ("peer_server_url_for(", "_json_body(", "sign_internal_payload(", "httpx.AsyncClient("),
    ),
    (
        "core/session_authority.py",
        "fetch_remote_session_authority",
        ("peer_server_url_for(", "_json_body(", "sign_internal_payload(", "httpx.AsyncClient("),
    ),
    (
        "core/telegram_registration_transport.py",
        "_post_signed_iran_command",
        ("peer_server_url_for(", "_json_body(", "sign_internal_payload(", "httpx.AsyncClient("),
    ),
    (
        "core/telegram_otp_transport.py",
        "forward_telegram_otp_delivery",
        ("peer_server_url_for(", "_json_body(", "sign_internal_payload(", "httpx.AsyncClient("),
    ),
    (
        "core/customer_invite_forwarding.py",
        "forward_customer_invite_to_iran",
        ("peer_server_url_for(", "_json_body(", "sign_internal_payload(", "httpx.AsyncClient("),
    ),
    (
        "core/customer_invite.py",
        "_fetch_iran_sync_health",
        ("peer_server_url_for(", "settings.observability_api_key", "httpx.AsyncClient("),
    ),
    (
        "core/customer_invite.py",
        "check_customer_invite_sync_ready",
        ("_foreign_local_sync_queues_clean(", "_fetch_iran_sync_health("),
    ),
    (
        "core/invitation_creation_forwarding.py",
        "forward_standard_invitation_to_iran",
        ("peer_server_url_for(", "_json_body(", "sign_internal_payload(", "httpx.AsyncClient("),
    ),
    (
        "scripts/dev_admin.py",
        "forward_remote_session_reset",
        ("peer_server_url_for(", "_json_body(", "sign_internal_payload(", "httpx.AsyncClient("),
    ),
)
_PEER_SERVER_URL_FENCE_FAILURE_MARKERS: Final[dict[tuple[str, str], tuple[str, ...]]] = {
    ("core/trade_forwarding.py", "forward_trade_to_home_server"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return 503,",
    ),
    ("core/offer_expiry_forwarding.py", "forward_offer_expiry_to_home_server"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return 503,",
    ),
    ("core/session_authority.py", "fetch_remote_session_authority"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return 503,",
    ),
    ("core/telegram_registration_transport.py", "_post_signed_iran_command"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return 503,",
    ),
    ("core/telegram_otp_transport.py", "forward_telegram_otp_delivery"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return 503,",
    ),
    ("core/customer_invite_forwarding.py", "forward_customer_invite_to_iran"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return 503,",
    ),
    ("core/customer_invite.py", "_fetch_iran_sync_health"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        'return None, "legacy_direct_transport_retired"',
    ),
    ("core/customer_invite.py", "check_customer_invite_sync_ready"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return CustomerInviteSyncGateResult(False, reason, _safe_gate_message(reason))",
    ),
    ("core/invitation_creation_forwarding.py", "forward_standard_invitation_to_iran"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return 503,",
    ),
    ("scripts/dev_admin.py", "forward_remote_session_reset"): (
        "except LegacyDirectFiIrTransportRetiredError:",
        "return 503,",
    ),
}
_LEGACY_DIRECT_TRANSPORT_CORE_ROUTER_ARTIFACT: Final = "api/routers/sync.py"

_LEGACY_DIRECT_TRANSPORT_LOCAL_BASH_ARTIFACTS: Final = (
    "deploy.sh",
    "scripts/install_sync_health_monitor.sh",
)

_LEGACY_DIRECT_TRANSPORT_MAKEFILE: Final = "Makefile"
_LEGACY_DIRECT_TRANSPORT_ROOT_COMPOSE_ARTIFACTS: Final = (
    "docker-compose.yml",
    "docker-compose.iran.yml",
)
_LEGACY_DIRECT_TRANSPORT_STAGING_ARTIFACTS: Final = (
    "scripts/deploy_staging.sh",
    "deploy/staging/docker-compose.staging.yml",
)
_LEGACY_DIRECT_TRANSPORT_WRITER_AGENT_ARTIFACTS: Final = (
    "scripts/production_writer_lease_agent.py",
    "deploy/production/production-writer-lease-agent.webapp-fi.json.example",
    "deploy/production/writer-witness-60s-release.json",
)
_LEGACY_DIRECT_TRANSPORT_STATIC_DELIVERY_ARTIFACT: Final = "scripts/report_static_delivery.py"
_LEGACY_DIRECT_TRANSPORT_SYNC_PARITY_COMPARE_ARTIFACT: Final = "scripts/compare_sync_parity.py"
_LEGACY_DIRECT_TRANSPORT_WORKER_HTTP_BENCHMARK_ARTIFACT: Final = "scripts/report_worker_http_benchmark.py"
_LEGACY_DIRECT_TRANSPORT_NGINX_ARTIFACTS: Final = (
    "deploy/production/nginx-iran-online.conf.template",
    "deploy/production/nginx-iran-online-https.conf.template",
    "deploy/production/nginx-iran-recovery-https.conf.template",
    "deploy/production/nginx-webapp-ir-promoted-2c08-https.conf.template",
    "deploy/staging/nginx-staging.conf.template",
    "nginx.conf",
    "scripts/setup_iran_nginx.sh",
    "scripts/setup_foreign_nginx.sh",
)
_LEGACY_DIRECT_TRANSPORT_NGINX_INERT_ARTIFACTS: Final = (
    # These are intentionally dark/recovery listeners, rather than active
    # application ingress.  Keep them in the same bounded static review so a
    # future change cannot silently add a proxy peer route outside the active
    # listener templates above.
    "deploy/production/nginx-iran-recovery-http.conf.template",
    "deploy/production/nginx-webapp-ir-standby-dark-https.conf.template",
)

ARCHITECTURE_STATIC_ARTIFACT_PATHS: Final = tuple(
    [*_ACTIVE_ARTIFACT_REQUIREMENTS.keys()]
    + [item.path for item in _LEGACY_PYTHON_ARTIFACTS]
    + list(_LEGACY_BASH_ARTIFACTS)
    + [item.path for item in _LEGACY_DIRECT_TRANSPORT_PYTHON_ARTIFACTS]
    + [_LEGACY_DIRECT_TRANSPORT_CORE_ROUTER_ARTIFACT]
    + list(_LEGACY_DIRECT_TRANSPORT_LOCAL_BASH_ARTIFACTS)
    + [_LEGACY_DIRECT_TRANSPORT_MAKEFILE]
    + list(_LEGACY_DIRECT_TRANSPORT_ROOT_COMPOSE_ARTIFACTS)
    + list(_LEGACY_DIRECT_TRANSPORT_STAGING_ARTIFACTS)
    + list(_LEGACY_DIRECT_TRANSPORT_WRITER_AGENT_ARTIFACTS)
    + [_LEGACY_DIRECT_TRANSPORT_SYNC_PARITY_COMPARE_ARTIFACT]
    + [_LEGACY_DIRECT_TRANSPORT_WORKER_HTTP_BENCHMARK_ARTIFACT]
    + list(_LEGACY_DIRECT_TRANSPORT_NGINX_ARTIFACTS)
    + list(_LEGACY_DIRECT_TRANSPORT_NGINX_INERT_ARTIFACTS)
)

_DIRECT_COMMAND_PATTERN: Final = re.compile(
    r"(?:\[\s*['\"](?:ssh|scp|rsync|sftp)['\"]|(?:^|[\s;])(?:ssh|scp|rsync|sftp)(?:\s|$))",
    re.MULTILINE,
)
_DIRECT_DSN_PATTERN: Final = re.compile(
    r"postgres(?:ql)?(?:\+[A-Za-z0-9_]+)?://",
    re.IGNORECASE,
)
_NONEMPTY_POSTGRES_STREAMING_PATTERN: Final = re.compile(
    r"^\s*(?:primary_conninfo|primary_slot_name|synchronous_standby_names)\s*=\s*'(?!')",
    re.MULTILINE,
)


def _fail(code: str) -> None:
    raise PhysicalThreeSiteArchitectureStaticPreflightError(code)


def _line_for_match(text: str, match: re.Match[str]) -> int:
    return text.count("\n", 0, match.start()) + 1


def _find_first(text: str, marker: str) -> int | None:
    index = text.find(marker)
    return None if index < 0 else text.count("\n", 0, index) + 1


def _validate_route_declarations(value: object) -> tuple[PhysicalThreeSiteRouteDeclaration, ...]:
    if type(value) is not tuple or len(value) != len(APPROVED_THREE_SITE_ROUTE_DECLARATIONS):
        _fail("THREE_SITE_STATIC_PREFLIGHT_ROUTE_DECLARATIONS_INVALID")
    if any(type(item) is not PhysicalThreeSiteRouteDeclaration for item in value):
        _fail("THREE_SITE_STATIC_PREFLIGHT_ROUTE_DECLARATIONS_INVALID")
    routes = value
    if routes != APPROVED_THREE_SITE_ROUTE_DECLARATIONS:
        _fail("THREE_SITE_STATIC_PREFLIGHT_ROUTE_DECLARATIONS_INVALID")
    return routes


def _validate_config(
    value: object,
) -> tuple[Path, tuple[PhysicalThreeSiteRouteDeclaration, ...]]:
    if type(value) is not PhysicalThreeSiteArchitectureStaticPreflightConfig:
        _fail("THREE_SITE_STATIC_PREFLIGHT_CONFIG_INVALID")
    # Keep this check first: disabled inspection must not read a path at all.
    if value.enabled is not True:
        _fail("THREE_SITE_STATIC_PREFLIGHT_DISABLED")
    routes = _validate_route_declarations(value.route_declarations)
    if not isinstance(value.repository_root, Path):
        _fail("THREE_SITE_STATIC_PREFLIGHT_REPOSITORY_ROOT_INVALID")
    root = value.repository_root.resolve()
    if not root.is_dir():
        _fail("THREE_SITE_STATIC_PREFLIGHT_REPOSITORY_ROOT_INVALID")
    return root, routes


def _read_artifact_texts(root: Path) -> tuple[dict[str, str], list[PhysicalThreeSiteArchitectureStaticFinding]]:
    texts: dict[str, str] = {}
    findings: list[PhysicalThreeSiteArchitectureStaticFinding] = []
    for relative_path in ARCHITECTURE_STATIC_ARTIFACT_PATHS:
        path = root / relative_path
        try:
            texts[relative_path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=relative_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_ARTIFACT_UNREADABLE",
                )
            )
    return texts, findings


def _python_functions(text: str) -> tuple[dict[str, str], tuple[str, ...]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}, ()
    lines = text.splitlines()
    functions: dict[str, str] = {}
    undecorated_forensic: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        functions[node.name] = "\n".join(lines[start:end])
        if node.name.startswith("_forensic_"):
            decorators = {
                item.id
                for item in node.decorator_list
                if isinstance(item, ast.Name)
            }
            if "_retire_legacy_forensic_source" not in decorators:
                undecorated_forensic.append(node.name)
    return functions, tuple(undecorated_forensic)


def _python_callable_bodies(text: str) -> dict[str, str]:
    """Return top-level and one-or-more-class-qualified callable bodies.

    This is deliberately static: no import, decorator evaluation, or module
    execution occurs.  Class qualification lets the audit bind an old remote
    factory such as ``Runner.compose_args`` without treating unrelated local
    ``compose_args`` helpers as the same surface.
    """

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    lines = text.splitlines()
    bodies: dict[str, str] = {}

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.lineno - 1
                end = node.end_lineno or node.lineno
                bodies[f"{prefix}{node.name}"] = "\n".join(lines[start:end])
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")

    visit(tree.body)
    return bodies


def _append_requirement_findings(
    *,
    artifact_path: str,
    text: str,
    requirements: tuple[str, ...],
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    for marker in requirements:
        if marker not in text:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=artifact_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_REQUIRED_MARKER_MISSING",
                )
            )


def _append_active_transport_findings(
    *,
    artifact_path: str,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    for pattern, code in (
        (_DIRECT_COMMAND_PATTERN, "THREE_SITE_STATIC_PREFLIGHT_DIRECT_COMMAND_ROUTE_FORBIDDEN"),
        (_DIRECT_DSN_PATTERN, "THREE_SITE_STATIC_PREFLIGHT_DIRECT_DATABASE_ROUTE_FORBIDDEN"),
        (_NONEMPTY_POSTGRES_STREAMING_PATTERN, "THREE_SITE_STATIC_PREFLIGHT_POSTGRES_STREAMING_ROUTE_FORBIDDEN"),
    ):
        match = pattern.search(text)
        if match is not None:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=artifact_path,
                    code=code,
                    line=_line_for_match(text, match),
                )
            )


def _append_legacy_python_findings(
    *,
    spec: _LegacyPythonArtifact,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    required_markers = (
        "assert_legacy_two_server_full_matrix_retired",
        "blocked_legacy_two_server_full_matrix_payload",
        "_retire_legacy_forensic_source",
        "__all__",
    )
    for marker in required_markers:
        if marker not in text:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=spec.path,
                    code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_FENCE_MISSING",
                )
            )

    if "__wrapped__" in text or "@wraps(" in text:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=spec.path,
                code="THREE_SITE_STATIC_PREFLIGHT_FORENSIC_WRAPPER_BYPASS",
            )
        )

    functions, undecorated_forensic = _python_functions(text)
    if not functions:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=spec.path,
                code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_PYTHON_PARSE_INVALID",
            )
        )
        return
    for name in undecorated_forensic:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=spec.path,
                code="THREE_SITE_STATIC_PREFLIGHT_FORENSIC_IMPORT_BYPASS",
                line=_find_first(text, f"def {name}"),
            )
        )
    for name, body in functions.items():
        if name.startswith("_historical_two_server_") and (
            "assert_legacy_two_server_full_matrix_retired" not in body
        ):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=spec.path,
                    code="THREE_SITE_STATIC_PREFLIGHT_HISTORICAL_SOURCE_UNFENCED",
                    line=_find_first(text, f"def {name}"),
                )
            )
    for name in spec.forbidden_public_helpers:
        if name in functions:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=spec.path,
                    code="THREE_SITE_STATIC_PREFLIGHT_DIRECT_HELPER_PUBLIC",
                    line=_find_first(text, f"def {name}"),
                )
            )
    for name in spec.public_functions:
        body = functions.get(name)
        if body is None:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=spec.path,
                    code="THREE_SITE_STATIC_PREFLIGHT_PUBLIC_FENCE_MISSING",
                )
            )
            continue
        if name == "main":
            if (
                "blocked_legacy_two_server_full_matrix_payload" not in body
                or "return 2" not in body
                or "parse_args" in body
                or "_forensic_" in body
            ):
                findings.append(
                    PhysicalThreeSiteArchitectureStaticFinding(
                        artifact_path=spec.path,
                        code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_CLI_EMISSION_NOT_FENCED",
                        line=_find_first(text, "def main"),
                    )
                )
        elif "_retire_legacy_public" not in body or "_forensic_" in body:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=spec.path,
                    code="THREE_SITE_STATIC_PREFLIGHT_PUBLIC_FENCE_MISSING",
                    line=_find_first(text, f"def {name}"),
                )
            )
        _append_active_transport_findings(
            artifact_path=spec.path,
            text=body,
            findings=findings,
        )


def _append_legacy_bash_findings(
    *,
    artifact_path: str,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    marker = "assert_legacy_cross_site_transport_fenced"
    if text.count(marker) < 2 or "LEGACY_CROSS_SITE_TRANSPORT_REASON" not in text:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_BASH_FENCE_MISSING",
            )
        )
        return
    invocation = text.rfind(marker)
    if invocation <= text.find(f"{marker}()"):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_BASH_FENCE_MISSING",
            )
        )
        return
    if artifact_path.endswith("production_deploy_online.sh"):
        after = text[invocation:]
        if "ensure_manifest_file" not in after or after.find(marker) > after.find("ensure_manifest_file"):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=artifact_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_BASH_FENCE_ORDER_INVALID",
                )
            )
    if artifact_path.endswith("recover_cross_server_sync.sh"):
        after = text[invocation:]
        if "load_shared_deploy_surface" not in after or after.find(marker) > after.find("load_shared_deploy_surface"):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=artifact_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_BASH_FENCE_ORDER_INVALID",
                )
                )


def _append_legacy_direct_transport_python_findings(
    *,
    spec: _LegacyDirectTransportPythonArtifact,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Require a fail-closed fence at each old direct peer-route factory."""

    fence_marker = "assert_legacy_direct_fi_ir_transport_retired"
    if fence_marker not in text:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=spec.path,
                code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_DIRECT_TRANSPORT_FENCE_MISSING",
            )
        )
        return
    bodies = _python_callable_bodies(text)
    if not bodies:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=spec.path,
                code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_DIRECT_TRANSPORT_PARSE_INVALID",
            )
        )
        return
    wrapper_bypass = False
    for name in spec.fenced_callables:
        body = bodies.get(name)
        short_name = name.rsplit(".", 1)[-1]
        if (
            body is not None
            and "__wrapped__" in body
            or re.search(
                rf"(?m)^\s*@[^\n]*wraps[^\n]*\n\s*(?:async\s+)?def\s+{re.escape(short_name)}\s*\(",
                text,
            )
            is not None
        ):
            wrapper_bypass = True
        if body is None or fence_marker not in body:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=spec.path,
                    code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_DIRECT_TRANSPORT_FENCE_MISSING",
                    line=_find_first(text, f"def {name.rsplit('.', 1)[-1]}"),
                )
            )
    if wrapper_bypass:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=spec.path,
                code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_DIRECT_TRANSPORT_WRAPPER_BYPASS",
            )
        )
    if spec.require_cli_payload:
        main_body = bodies.get("main")
        if (
            main_body is None
            or "blocked_legacy_direct_fi_ir_transport_payload" not in main_body
            or "return 2" not in main_body
        ):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=spec.path,
                    code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_DIRECT_TRANSPORT_CLI_FENCE_MISSING",
                    line=_find_first(text, "def main"),
                )
            )


def _append_static_delivery_local_only_findings(
    *,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Keep the compatibility static verifier bound to its own loopback UI."""

    artifact_path = _LEGACY_DIRECT_TRANSPORT_STATIC_DELIVERY_ARTIFACT
    required = (
        'LOCAL_STATIC_DELIVERY_URL = "http://127.0.0.1"',
        'args.base_url or LOCAL_STATIC_DELIVERY_URL',
        'parsed.hostname not in {"127.0.0.1", "::1"}',
        'if args.manifest is not None:',
    )
    fetch_body = _python_callable_bodies(text).get("fetch_url", "")
    if (
        any(marker not in text for marker in required)
        or "resolve_deploy_settings" in text
        or "IRAN_FRONTEND_URL" in text
        or "IRAN_SERVER_URL" in text
        or "assert_legacy_direct_fi_ir_transport_retired" not in fetch_body
    ):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_STATIC_DELIVERY_LOCAL_FENCE_MISSING",
            )
        )


def _append_sync_parity_compare_local_only_findings(
    *,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Reject a regression to arbitrary peer HTTP parity traffic.

    Parity comparison can read sealed artifacts.  Its historical URL flags are
    retained only for a same-role loopback diagnostic, never for a FI<->IR
    request or a remote result publication.
    """

    artifact_path = _LEGACY_DIRECT_TRANSPORT_SYNC_PARITY_COMPARE_ARTIFACT
    required = (
        '"127.0.0.1", "::1"',
        "def _require_role_local_parity_url",
        "assert_legacy_direct_fi_ir_transport_retired(",
        "def _assert_compare_url_inputs_are_role_local",
        "blocked_legacy_direct_fi_ir_transport_payload(component=\"sync-parity-compare\")",
    )
    bodies = _python_callable_bodies(text)
    fetch = bodies.get("_fetch_json", "")
    post = bodies.get("_post_json", "")
    compare = bodies.get("_compare", "")
    main = bodies.get("main", "")
    network_before_fence = any(
        value.find("_require_role_local_parity_url(") < 0
        or value.find("_require_role_local_parity_url(") > value.find("urllib.request.Request(")
        for value in (fetch, post)
    )
    if (
        any(marker not in text for marker in required)
        or network_before_fence
        or "_assert_compare_url_inputs_are_role_local(args)" not in compare
        or compare.find("_assert_compare_url_inputs_are_role_local(args)") > compare.find("_load_snapshot(")
        or "except LegacyDirectFiIrTransportRetiredError:" not in main
        or "return 2" not in main
    ):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_SYNC_PARITY_HTTP_FENCE_MISSING",
            )
        )


def _append_worker_http_benchmark_local_only_findings(
    *,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Keep the JWT-carrying worker probe on its own loopback listener."""

    artifact_path = _LEGACY_DIRECT_TRANSPORT_WORKER_HTTP_BENCHMARK_ARTIFACT
    required = (
        '"127.0.0.1", "::1"',
        "def _require_role_local_benchmark_url",
        "assert_legacy_direct_fi_ir_transport_retired(",
        "blocked_legacy_direct_fi_ir_transport_payload(component=\"worker-http-benchmark\")",
    )
    bodies = _python_callable_bodies(text)
    parse = bodies.get("parse_args", "")
    workload = bodies.get("run_http_workload", "")
    main = bodies.get("main", "")
    if (
        any(marker not in text for marker in required)
        or "args.base_url = _require_role_local_benchmark_url(" not in parse
        or "base_url = _require_role_local_benchmark_url(" not in workload
        or workload.find("base_url = _require_role_local_benchmark_url(") > workload.find("httpx.AsyncClient(")
        or "except LegacyDirectFiIrTransportRetiredError:" not in main
        or "return 2" not in main
    ):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_WORKER_HTTP_BENCHMARK_FENCE_MISSING",
            )
        )


def _nginx_location_bodies(text: str, location: str) -> tuple[str, ...]:
    return tuple(
        match.group("body")
        for match in re.finditer(
        rf"(?m)^\s*location\s+{re.escape(location)}\s*\{{(?P<body>.*?)^\s*\}}",
        text,
        re.DOTALL,
    )
    )


def _nginx_location_body(text: str, location: str) -> str | None:
    bodies = _nginx_location_bodies(text, location)
    return bodies[0] if bodies else None


def _nginx_server_blocks(text: str) -> tuple[str, ...]:
    """Return top-level server text spans; Nginx templates use no nested servers."""

    starts = [match.start() for match in re.finditer(r"(?m)^server\s*\{", text)]
    return tuple(
        text[start : starts[index + 1] if index + 1 < len(starts) else len(text)]
        for index, start in enumerate(starts)
    )


def _append_nginx_direct_sync_ingress_findings(
    *,
    artifact_texts: Mapping[str, str],
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Keep retired FI<->IR HTTP/control ingress closed on every listener."""

    for artifact_path in _LEGACY_DIRECT_TRANSPORT_NGINX_ARTIFACTS:
        text = artifact_texts.get(artifact_path)
        if type(text) is not str:
            continue

        invalid = (
            "THREE_SITE_LEGACY_INTERNAL_INGRESS_FENCED" not in text
            or "__FOREIGN_PUBLIC_IP__" in text
        )
        for server in _nginx_server_blocks(text):
            # The Iran setup script uses an unquoted heredoc and therefore
            # writes `\$` in source for the final Nginx `$` regex anchor.
            normalized_server = server.replace(r"\$", "$")
            direct_bodies = _nginx_location_bodies(normalized_server, "= /api/sync/receive")
            internal_bodies = _nginx_location_bodies(
                normalized_server,
                "~ ^/api/(sync|sessions/internal|trades/internal|offers/internal|auth/internal|invitations/internal|customers/internal)(/|$)",
            )
            has_internal_fence = "location ~ ^/api/(sync|sessions/internal|trades/internal|offers/internal|auth/internal|invitations/internal|customers/internal)" in normalized_server
            direct_index = normalized_server.find("location = /api/sync/receive")
            internal_index = normalized_server.find("location ~ ^/api/(sync|sessions/internal|trades/internal|offers/internal|auth/internal|invitations/internal|customers/internal)")
            generic_api_index = normalized_server.find("location /api/ {")
            if (
                not direct_bodies
                or any(
                    "return 410;" not in body
                    or "proxy_pass" in body
                    or "allow " in body
                    for body in direct_bodies
                )
                or not has_internal_fence
                or not internal_bodies
                or any(
                    "return 410;" not in body
                    or "proxy_pass" in body
                    or "allow " in body
                    for body in internal_bodies
                )
                or direct_index < 0
                or internal_index < 0
                or direct_index > internal_index
                or generic_api_index >= 0 and internal_index > generic_api_index
            ):
                invalid = True

        if artifact_path == "deploy/staging/nginx-staging.conf.template":
            foreign_bodies = _nginx_location_bodies(text, "^~ /foreign-sync/")
            foreign_index = text.find("location ^~ /foreign-sync/")
            generic_api_index = text.find("location /api/ {")
            if (
                len(foreign_bodies) != 1
                or any(
                    "return 410;" not in body
                    or "proxy_pass" in body
                    or "allow " in body
                    or "auth_basic off;" in body
                    for body in foreign_bodies
                )
                or "location = /foreign-sync/" in text
                or "location ~ ^/foreign-sync/" in text
                or "proxy_pass http://127.0.0.1:__FOREIGN_APP_PORT__" in text
                or "rewrite ^/foreign-sync" in text
                or foreign_index < 0
                or generic_api_index >= 0 and foreign_index > generic_api_index
            ):
                invalid = True

        if invalid:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=artifact_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_NGINX_DIRECT_SYNC_INGRESS_FENCE_MISSING",
                )
            )

    # Recovery/dark listeners deliberately do not serve an application at
    # all.  They therefore cannot carry the active-listener 410 marker, but
    # must retain an explicit dark catch-all and must never grow an upstream
    # or peer allowlist.  This is intentionally a narrow check: ACME's local
    # static challenge root and the loopback-only dark health check remain
    # legitimate host-local operations.
    for artifact_path in _LEGACY_DIRECT_TRANSPORT_NGINX_INERT_ARTIFACTS:
        text = artifact_texts.get(artifact_path)
        if type(text) is not str:
            continue
        invalid = "__FOREIGN_PUBLIC_IP__" in text
        for server in _nginx_server_blocks(text):
            normalized_server = server.replace(r"\$", "$")
            catch_all = _nginx_location_bodies(normalized_server, "/")
            if (
                not catch_all
                or any("return 503;" not in body for body in catch_all)
                or any(
                    forbidden in normalized_server
                    for forbidden in ("proxy_pass", "fastcgi_pass", "uwsgi_pass", "scgi_pass")
                )
                or "allow __FOREIGN_PUBLIC_IP__" in normalized_server
            ):
                invalid = True
        if invalid:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=artifact_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_NGINX_INERT_LISTENER_FENCE_MISSING",
                )
            )


def _append_core_direct_transport_findings(
    *,
    artifact_texts: Mapping[str, str],
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Bind legacy core peer paths to their early fail-closed boundaries."""

    marker = "assert_legacy_direct_fi_ir_transport_retired"

    def body(path: str, name: str) -> str:
        return _python_callable_bodies(artifact_texts.get(path, "")).get(name, "")

    for path, name, later_markers in (
        ("core/sync_push.py", "_get_client", ("assert_runtime_sync_transport_allowed()", "httpx.Client(")),
        ("core/sync_push.py", "_do_push", ("time.time()", "client.post(")),
        ("core/sync_push.py", "push_sync_direct", ("default_peer_server_url", "_executor.submit(")),
        ("core/sync_worker.py", "send_sync_item", ("time.time()", "client.post(")),
        ("core/sync_worker.py", "main", ("assert_background_job_authority", "redis.Redis(", "default_peer_server_url")),
        ("core/notifications.py", "send_telegram_message", ("payload =", "push_sync_direct(")),
    ):
        value = body(path, name)
        marker_index = value.find(marker)
        later_indexes = [value.find(item) for item in later_markers if value.find(item) >= 0]
        if marker_index < 0 or (later_indexes and marker_index > min(later_indexes)):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=path,
                    code="THREE_SITE_STATIC_PREFLIGHT_CORE_DIRECT_TRANSPORT_FENCE_MISSING",
                    line=_find_first(artifact_texts.get(path, f"def {name}"), f"def {name}"),
                )
            )

    # These are the current non-router `peer_server_url_for` call paths.  A
    # direct transport retirement assertion must appear before every URL,
    # command payload, signature, or HTTP client primitive in its callable.
    for path, name, later_markers in _PEER_SERVER_URL_FOR_CALLER_FENCES:
        value = body(path, name)
        marker_index = value.find(marker)
        later_indexes = [value.find(item) for item in later_markers if value.find(item) >= 0]
        failure_markers = _PEER_SERVER_URL_FENCE_FAILURE_MARKERS[(path, name)]
        if (
            marker_index < 0
            or (later_indexes and marker_index > min(later_indexes))
            or any(required not in value for required in failure_markers)
        ):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=path,
                    code="THREE_SITE_STATIC_PREFLIGHT_CORE_PEER_SERVER_URL_FENCE_MISSING",
                    line=_find_first(artifact_texts.get(path, f"def {name}"), f"def {name}"),
                )
            )

    registered_peer_paths = {path for path, _name, _later_markers in _PEER_SERVER_URL_FOR_CALLER_FENCES}
    for path, text in artifact_texts.items():
        if (
            path.startswith(("core/", "scripts/"))
            and path not in {"core/server_routing.py"}
            and "peer_server_url_for(" in text
            and path not in registered_peer_paths
        ):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=path,
                    code="THREE_SITE_STATIC_PREFLIGHT_CORE_PEER_SERVER_URL_CALLER_UNREGISTERED",
                    line=_find_first(text, "peer_server_url_for("),
                )
            )

    connectivity_path = "core/connectivity.py"
    target_body = body(connectivity_path, "_iran_connectivity_target_url")
    check_body = body(connectivity_path, "check_connectivity")
    if (
        marker not in target_body
        or "_iran_connectivity_target_url()" not in check_body
        or "except LegacyDirectFiIrTransportRetiredError:" not in check_body
        or "return False" not in check_body
        or check_body.find("_iran_connectivity_target_url()") > check_body.find("httpx.AsyncClient")
    ):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=connectivity_path,
                code="THREE_SITE_STATIC_PREFLIGHT_CORE_CONNECTIVITY_PEER_FENCE_MISSING",
            )
        )

    router_path = _LEGACY_DIRECT_TRANSPORT_CORE_ROUTER_ARTIFACT
    router_text = artifact_texts.get(router_path, "")
    receive_body = body(router_path, "receive_sync_data")
    resync_body = body(router_path, "resync_from_changelog")
    permanent_guard_body = body(router_path, "_reject_retired_legacy_direct_sync_transport")
    reject = "_reject_retired_legacy_direct_sync_transport()"
    receive_index = receive_body.find(reject)
    resync_index = resync_body.find(reject)
    resync_later = [
        resync_body.find(item)
        for item in ("peer_server_url_for", "default_peer_server_url", "select(ChangeLog)", "httpx_mod")
        if resync_body.find(item) >= 0
    ]
    if (
        router_text.count("Depends(_reject_retired_legacy_direct_sync_transport)") != 2
        or "single_writer_runtime_enabled" in permanent_guard_body
        or "status_code=410" not in permanent_guard_body
        or "RETIRED_LEGACY_DIRECT_SYNC_HTTP_DETAIL" not in permanent_guard_body
        or receive_index < 0
        or (receive_body.find("logger.info") >= 0 and receive_index > receive_body.find("logger.info"))
        or resync_index < 0
        or (resync_later and resync_index > min(resync_later))
        or (
            resync_body.find("_require_dev_key(request)") >= 0
            and resync_index > resync_body.find("_require_dev_key(request)")
        )
    ):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=router_path,
                code="THREE_SITE_STATIC_PREFLIGHT_LEGACY_SYNC_ROUTER_PERMANENT_FENCE_MISSING",
            )
        )


def _append_deploy_sh_direct_transport_findings(
    *,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Prove root legacy deploy rejects peer targets before config/SSH."""

    artifact_path = "deploy.sh"
    required = (
        "assert_legacy_direct_fi_ir_transport_fenced",
        'case "$TARGET" in',
        "all|frontend|iran)",
        "load_shared_deploy_surface",
    )
    if any(marker not in text for marker in required):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_ROOT_DEPLOY_FENCE_MISSING",
            )
        )
        return
    gate_start = text.find('case "$TARGET" in')
    fence_invocation = text.find("assert_legacy_direct_fi_ir_transport_fenced", gate_start)
    load_invocation = text.find("load_shared_deploy_surface", gate_start)
    if (
        fence_invocation < 0
        or load_invocation < 0
        or fence_invocation > load_invocation
        or "all|frontend|iran)\n        assert_legacy_direct_fi_ir_transport_fenced" not in text
    ):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_ROOT_DEPLOY_FENCE_ORDER_INVALID",
            )
        )


def _append_sync_health_monitor_findings(
    *,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Ensure the recurring timer cannot reinstall direct Iran SSH telemetry."""

    artifact_path = "scripts/install_sync_health_monitor.sh"
    required = (
        'SKIP_IRAN_ARG=" --skip-iran"',
        "${SYNC_HEALTH_MONITOR_SKIP_IRAN:-1}",
        "retired direct FI-to-IR SSH sampling",
    )
    if any(marker not in text for marker in required) or re.search(r"\n\s*0\)\s*;;", text):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_SYNC_HEALTH_TIMER_FENCE_MISSING",
            )
        )


def _append_makefile_direct_transport_findings(
    *,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Keep old Make SSH entrypoints as dependency-only denial targets."""

    artifact_path = _LEGACY_DIRECT_TRANSPORT_MAKEFILE
    required_targets = (
        "sync-health-iran",
        "logs-iran",
        "restart-iran",
        "production-data-hygiene-iran",
    )
    missing = [
        target
        for target in required_targets
        if f"{target}: legacy-direct-fi-ir-transport-blocked" not in text
    ]
    if (
        missing
        or "legacy-direct-fi-ir-transport-blocked:" not in text
        or "WA-IR status is intentionally not queried from this host" not in text
    ):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_MAKE_DIRECT_TRANSPORT_FENCE_MISSING",
            )
        )
    for target in required_targets:
        direct_recipe = re.search(
            rf"(?ms)^{re.escape(target)}:\s*\n\t[^\n]*(?:\bssh\b|\bscp\b|\brsync\b)",
            text,
        )
        if direct_recipe is not None:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=artifact_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_MAKE_DIRECT_TRANSPORT_REACHABLE",
                    line=_line_for_match(text, direct_recipe),
                )
            )


def _compose_service_block(text: str, service_name: str) -> str | None:
    """Extract one YAML service block without loading YAML or interpolating it."""

    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line == f"  {service_name}:":
            start = index
            continue
        if start is None or index <= start:
            continue
        if line and not line.startswith(" "):
            return "\n".join(lines[start:index])
        if line.startswith("  ") and not line.startswith("    ") and line.rstrip().endswith(":"):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:]) if start is not None else None


def _compose_service_names(text: str) -> tuple[str, ...]:
    """List service names without parsing/interpolating a Compose document."""

    in_services = False
    names: list[str] = []
    for line in text.splitlines():
        if line == "services:":
            in_services = True
            continue
        if not in_services:
            continue
        if line and not line.startswith(" "):
            break
        match = re.fullmatch(r"  ([A-Za-z0-9_-]+):", line)
        if match is not None:
            names.append(match.group(1))
    return tuple(names)


def _append_root_compose_direct_transport_findings(
    *,
    artifact_path: str,
    text: str,
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Keep legacy root Compose inert outside an acknowledged local profile."""

    services = ("app", "bot", "sync_worker") if artifact_path == "docker-compose.yml" else ("app", "sync_worker")
    for service in services:
        block = _compose_service_block(text, service)
        if (
            block is None
            or re.search(r'^      SINGLE_WRITER_RUNTIME_ENABLED: "true"\s*$', block, re.MULTILINE) is None
            or re.search(r'^      TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"\s*$', block, re.MULTILINE) is None
            or "extra_hosts:" in block
        ):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=artifact_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_ROOT_COMPOSE_DIRECT_SYNC_FENCE_MISSING",
                    line=_find_first(text, f"  {service}:"),
                )
            )

    guard = _compose_service_block(text, "legacy_root_runtime_guard")
    guard_required = (
        'pull_policy: never',
        'restart: "no"',
        'ENVIRONMENT:-',
        'I_UNDERSTAND_THIS_IS_LOCAL_DEVELOPMENT_ONLY',
        "exit 2",
    )
    service_names = _compose_service_names(text)
    non_guard_services = tuple(name for name in service_names if name != "legacy_root_runtime_guard")
    invalid_guard = (
        guard is None
        or any(marker not in guard for marker in guard_required)
        or 'profiles: ["legacy-local-development"]' in guard
        or not non_guard_services
    )
    for service in non_guard_services:
        block = _compose_service_block(text, service)
        if (
            block is None
            or 'profiles: ["legacy-local-development"]' not in block
            or "legacy_root_runtime_guard:\n        condition: service_completed_successfully" not in block
        ):
            invalid_guard = True
    # Commands that can execute application code or DML independently repeat
    # the acknowledgement: a direct `compose run app` cannot bypass the
    # profile/default guard merely by naming its service explicitly.
    for service in ("app", "bot", "migration", "sync_worker"):
        block = _compose_service_block(text, service)
        if block is None:
            continue
        command_line = next((line for line in block.splitlines() if line.startswith("    command:")), "")
        if "ENVIRONMENT:-" not in command_line or "I_UNDERSTAND_THIS_IS_LOCAL_DEVELOPMENT_ONLY" not in command_line:
            invalid_guard = True
    if invalid_guard:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=artifact_path,
                code="THREE_SITE_STATIC_PREFLIGHT_ROOT_COMPOSE_RUNTIME_RETIREMENT_GUARD_MISSING",
                line=_find_first(text, "  legacy_root_runtime_guard:"),
            )
        )


def _append_staging_direct_transport_findings(
    *,
    artifact_texts: Mapping[str, str],
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Retire the old staging profile before it can target production WA-IR."""

    script_path, compose_path = _LEGACY_DIRECT_TRANSPORT_STAGING_ARTIFACTS
    script = artifact_texts.get(script_path)
    if type(script) is str:
        required = (
            "assert_legacy_direct_staging_transport_fenced",
            "legacy_direct_staging_transport_blocked",
            'STAGING_INTERNAL_IRAN_SERVER_URL="http://app:8000"',
            'STAGING_FOREIGN_IRAN_SERVER_URL="http://app:8000"',
            "assert_legacy_direct_staging_transport_fenced \"$@\"",
            "assert_legacy_direct_staging_transport_fenced\n        deploy",
        "direct_peer_transport=retired",
        "for service in migration app foreign_app bot sync_worker foreign_sync_worker",
        '"$previous" == "--profile" && "$argument" == "staging-sync"',
        "sync_worker|foreign_sync_worker|--profile=*staging-sync*",
    )
        forbidden = (
            "staging.gold-trade.ir",
            "65.109.220.59",
            "STAGING_PUBLIC_FOREIGN_SYNC_URL",
            "STAGING_INTERNAL_FOREIGN_SERVER_URL",
            "start_sync_worker()",
        )
        gate_index = script.find("assert_legacy_direct_staging_transport_fenced")
        compose_index = script.find("compose up -d --build")
        if (
            any(marker not in script for marker in required)
            or any(marker in script for marker in forbidden)
            or gate_index < 0
            or compose_index < 0
            or gate_index > compose_index
        ):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=script_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_STAGING_DIRECT_TRANSPORT_FENCE_MISSING",
                )
            )

    compose = artifact_texts.get(compose_path)
    if type(compose) is str:
        services = (
            "app",
            "foreign_app",
            "sync_worker",
            "foreign_sync_worker",
            "migration",
            "bot",
            "load_telegram_foreign",
            "load_webapp_iran",
        )
        invalid = "extra_hosts:" in compose or "staging.gold-trade.ir" in compose or "65.109.220.59" in compose
        for service in services:
            block = _compose_service_block(compose, service)
            if (
                block is None
                or re.search(r'^      SINGLE_WRITER_RUNTIME_ENABLED: "true"\s*$', block, re.MULTILINE) is None
                or re.search(r'^      TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"\s*$', block, re.MULTILINE) is None
            ):
                invalid = True
        for service in ("foreign_app", "foreign_sync_worker", "bot", "load_telegram_foreign"):
            block = _compose_service_block(compose, service)
            if block is None or "IRAN_SERVER_URL: ${STAGING_FOREIGN_IRAN_SERVER_URL:-http://app:8000}" not in block:
                invalid = True
        if invalid:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=compose_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_STAGING_DIRECT_TRANSPORT_FENCE_MISSING",
                )
            )


def _append_writer_agent_direct_transport_findings(
    *,
    artifact_texts: Mapping[str, str],
    findings: list[PhysicalThreeSiteArchitectureStaticFinding],
) -> None:
    """Prevent a lease-agent config from selecting root Compose + sync_worker."""

    agent_path = "scripts/production_writer_lease_agent.py"
    agent_text = artifact_texts.get(agent_path)
    if type(agent_text) is str:
        gate = 'if mode == "writer" and site == "webapp_fi":'
        reason = "generic WebApp-FI writer mode is retired"
        runtime_parse = "runtime_raw = raw.get(\"runtime\")"
        if (
            gate not in agent_text
            or reason not in agent_text
            or runtime_parse not in agent_text
            or agent_text.find(gate) > agent_text.find(runtime_parse)
        ):
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=agent_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_FI_WRITER_GENERIC_MODE_FENCE_MISSING",
                )
            )

    example_path = "deploy/production/production-writer-lease-agent.webapp-fi.json.example"
    example_text = artifact_texts.get(example_path)
    if (
        type(example_text) is str
        and (
            '"mode": "fenced_fi_writer"' not in example_text
            or '"services": ["app", "bot"]' not in example_text
            or "docker-compose.webapp-fi-writer-2c08.yml" not in example_text
            or "sync_worker" in example_text
        )
    ):
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=example_path,
                code="THREE_SITE_STATIC_PREFLIGHT_FI_WRITER_GENERIC_EXAMPLE_RETIRED",
            )
        )

    profile_path = "deploy/production/writer-witness-60s-release.json"
    profile_text = artifact_texts.get(profile_path)
    if type(profile_text) is str and '"mode": "fenced_fi_writer"' not in profile_text:
        findings.append(
            PhysicalThreeSiteArchitectureStaticFinding(
                artifact_path=profile_path,
                code="THREE_SITE_STATIC_PREFLIGHT_FI_WRITER_GENERIC_PROFILE_RETIRED",
            )
        )


def lint_physical_three_site_architecture_artifacts(
    artifact_texts: Mapping[str, str],
) -> tuple[PhysicalThreeSiteArchitectureStaticFinding, ...]:
    """Lint supplied static text; this function performs no file or network I/O."""

    findings: list[PhysicalThreeSiteArchitectureStaticFinding] = []
    for relative_path in ARCHITECTURE_STATIC_ARTIFACT_PATHS:
        text = artifact_texts.get(relative_path)
        if type(text) is not str:
            findings.append(
                PhysicalThreeSiteArchitectureStaticFinding(
                    artifact_path=relative_path,
                    code="THREE_SITE_STATIC_PREFLIGHT_ARTIFACT_MISSING",
                )
            )
    for artifact_path, requirements in _ACTIVE_ARTIFACT_REQUIREMENTS.items():
        text = artifact_texts.get(artifact_path)
        if type(text) is not str:
            continue
        _append_requirement_findings(
            artifact_path=artifact_path,
            text=text,
            requirements=requirements,
            findings=findings,
        )
        _append_active_transport_findings(
            artifact_path=artifact_path,
            text=text,
            findings=findings,
        )
    for spec in _LEGACY_PYTHON_ARTIFACTS:
        text = artifact_texts.get(spec.path)
        if type(text) is str:
            _append_legacy_python_findings(spec=spec, text=text, findings=findings)
    for artifact_path in _LEGACY_BASH_ARTIFACTS:
        text = artifact_texts.get(artifact_path)
        if type(text) is str:
            _append_legacy_bash_findings(
                artifact_path=artifact_path,
                text=text,
                findings=findings,
            )
    for spec in _LEGACY_DIRECT_TRANSPORT_PYTHON_ARTIFACTS:
        text = artifact_texts.get(spec.path)
        if type(text) is str:
            _append_legacy_direct_transport_python_findings(
                spec=spec,
                text=text,
                findings=findings,
            )
    static_delivery = artifact_texts.get(_LEGACY_DIRECT_TRANSPORT_STATIC_DELIVERY_ARTIFACT)
    if type(static_delivery) is str:
        _append_static_delivery_local_only_findings(
            text=static_delivery,
            findings=findings,
        )
    sync_parity_compare = artifact_texts.get(_LEGACY_DIRECT_TRANSPORT_SYNC_PARITY_COMPARE_ARTIFACT)
    if type(sync_parity_compare) is str:
        _append_sync_parity_compare_local_only_findings(
            text=sync_parity_compare,
            findings=findings,
        )
    worker_http_benchmark = artifact_texts.get(_LEGACY_DIRECT_TRANSPORT_WORKER_HTTP_BENCHMARK_ARTIFACT)
    if type(worker_http_benchmark) is str:
        _append_worker_http_benchmark_local_only_findings(
            text=worker_http_benchmark,
            findings=findings,
        )
    _append_nginx_direct_sync_ingress_findings(
        artifact_texts=artifact_texts,
        findings=findings,
    )
    _append_core_direct_transport_findings(
        artifact_texts=artifact_texts,
        findings=findings,
    )
    deploy_sh = artifact_texts.get("deploy.sh")
    if type(deploy_sh) is str:
        _append_deploy_sh_direct_transport_findings(text=deploy_sh, findings=findings)
    monitor = artifact_texts.get("scripts/install_sync_health_monitor.sh")
    if type(monitor) is str:
        _append_sync_health_monitor_findings(text=monitor, findings=findings)
    makefile = artifact_texts.get(_LEGACY_DIRECT_TRANSPORT_MAKEFILE)
    if type(makefile) is str:
        _append_makefile_direct_transport_findings(text=makefile, findings=findings)
    for artifact_path in _LEGACY_DIRECT_TRANSPORT_ROOT_COMPOSE_ARTIFACTS:
        text = artifact_texts.get(artifact_path)
        if type(text) is str:
            _append_root_compose_direct_transport_findings(
                artifact_path=artifact_path,
                text=text,
                findings=findings,
            )
    _append_staging_direct_transport_findings(
        artifact_texts=artifact_texts,
        findings=findings,
    )
    _append_writer_agent_direct_transport_findings(
        artifact_texts=artifact_texts,
        findings=findings,
    )
    return tuple(findings)


def inspect_physical_three_site_architecture_static_preflight(
    *,
    config: PhysicalThreeSiteArchitectureStaticPreflightConfig,
) -> PhysicalThreeSiteArchitectureStaticPreflightReport:
    """Read the bounded repository inventory after explicit opt-in only."""

    root, routes = _validate_config(config)
    texts, read_findings = _read_artifact_texts(root)
    findings = [*read_findings, *lint_physical_three_site_architecture_artifacts(texts)]
    return PhysicalThreeSiteArchitectureStaticPreflightReport(
        schema=PHYSICAL_THREE_SITE_ARCHITECTURE_STATIC_PREFLIGHT_SCHEMA,
        status="passed" if not findings else "failed",
        approved_route_ids=tuple(item.route_id for item in routes),
        checked_artifacts=ARCHITECTURE_STATIC_ARTIFACT_PATHS,
        findings=tuple(findings),
    )


def require_physical_three_site_architecture_static_preflight(
    *,
    config: PhysicalThreeSiteArchitectureStaticPreflightConfig,
) -> PhysicalThreeSiteArchitectureStaticPreflightReport:
    """Require a clean static audit; findings are never an execution permit."""

    report = inspect_physical_three_site_architecture_static_preflight(config=config)
    if report.findings:
        _fail("THREE_SITE_STATIC_PREFLIGHT_ARCHITECTURE_REGRESSION")
    return report
