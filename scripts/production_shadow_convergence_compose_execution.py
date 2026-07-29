#!/usr/bin/env python3
"""Pure contracts for a future Compose-network convergence observer executor.

This module deliberately does not import Docker helpers, open files, execute a
subprocess, or call a worker.  It only validates the static inputs a privileged
executor must prove before it can later run one exact role observer container
and validates the redacted receipt that executor would return.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping
from uuid import UUID

PLAN_SCHEMA = "production-shadow-convergence-compose-observer-plan-v1"
RECEIPT_SCHEMA = "production-shadow-convergence-compose-observer-receipt-v1"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
RUNTIME_IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
PROJECT_ROOT_PREFIX = "/srv/trading-bot-three-site-production-shadow"
SECRET_ROOT_PREFIX = "/root/secure-envs/trading-bot/three-site-production-shadow"
DOCKER = "/usr/bin/docker"
MAX_STDOUT_BYTES = 64 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
TIMEOUT_SECONDS = 120
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64
OBSERVER_DUMMY_COMMAND = (
    "python",
    "-c",
    "raise SystemExit('invoke with docker compose run')",
)
CONTAINER_COLLECTOR_RELATIVE = (
    "scripts/collect_production_shadow_compose_runtime_snapshot.py"
)
CONTAINER_COLLECTOR_DELEGATE_RELATIVE = (
    "scripts/collect_three_site_staging_convergence_snapshot.py"
)
CONTAINER_COLLECTOR_INTERPRETER = "python"
OBSERVER_OPERATION_NETWORK_LABELS = {
    "trading-bot.production.operation-id": (
        "${PRODUCTION_SHADOW_OPERATION_ID:?operation UUID is required}"
    )
}

PLAN_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "canonical_compose_sha256",
        "role",
        "service",
        "profile",
        "project_name",
        "role_compose_path",
        "role_compose_sha256",
        "role_environment_path",
        "role_environment_sha256",
        "collector_path",
        "collector_sha256",
        "collector_delegate_sha256",
        "collector_closure_sha256",
        "collector_source_manifest_path",
        "collector_source_manifest_sha256",
        "collector_argv",
        "config_probe_argv",
        "resolved_observer_service_sha256",
        "role_material_sha256",
        "role_material_inspection_sha256",
        "runtime_target_binding_sha256",
        "runtime_image_ids",
        "internal_network",
        "network_name",
        "release_mount",
        "runtime_input_mount",
        "container_id_file",
        "compose_argv",
        "cleanup_probe_argv",
        "timeout_seconds",
        "max_stdout_bytes",
        "max_stderr_bytes",
        "production_mutation_forbidden",
        "object_storage_contact_forbidden",
        "plan_sha256",
    }
)
RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "role",
        "service",
        "plan_sha256",
        "runtime_target_binding_sha256",
        "image_id",
        "stdout_sha256",
        "stdout_bytes",
        "stderr_bytes",
        "exit_code",
        "started_at",
        "finished_at",
        "duration_ms",
        "container_removed",
        "cleanup_verified",
        "network_inspection",
        "container_inspection",
        "production_mutated",
        "object_storage_contacted",
        "receipt_sha256",
    }
)


class ComposeObserverExecutionContractError(ValueError):
    """The proposed one-shot Compose observer contract is not exact."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ComposeObserverExecutionContractError("JSON document has duplicate fields")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ComposeObserverExecutionContractError("contract value is not canonical JSON") from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise ComposeObserverExecutionContractError(f"{label} is not a nonzero SHA-256")
    return value


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ComposeObserverExecutionContractError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ComposeObserverExecutionContractError(f"{label} is invalid") from exc
    if str(parsed) != value:
        raise ComposeObserverExecutionContractError(f"{label} is invalid")
    return value


def _release_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise ComposeObserverExecutionContractError(f"{label} is invalid")
    return value


def _absolute_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ComposeObserverExecutionContractError(f"{label} is invalid")
    path = PurePosixPath(value)
    if not path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ComposeObserverExecutionContractError(f"{label} is invalid")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ComposeObserverExecutionContractError(f"{label} is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ComposeObserverExecutionContractError(f"{label} is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ComposeObserverExecutionContractError(f"{label} is invalid")
    return result.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_release_root(*, operation_id: str, release_sha: str) -> str:
    return f"{PROJECT_ROOT_PREFIX}/{operation_id}/releases/{release_sha}"


def canonical_runtime_input_root(*, operation_id: str, role: str) -> str:
    if role not in ROLES:
        raise ComposeObserverExecutionContractError("runtime input role is invalid")
    return f"{SECRET_ROOT_PREFIX}/{operation_id}/convergence-observer-runtime/{role}"


def canonical_container_collector_path(*, operation_id: str, release_sha: str) -> str:
    return f"{canonical_release_root(operation_id=operation_id, release_sha=release_sha)}/{CONTAINER_COLLECTOR_RELATIVE}"


def canonical_collector_source_manifest_path(*, operation_id: str, role: str) -> str:
    return f"{canonical_runtime_input_root(operation_id=operation_id, role=role)}/collector-source-manifest.json"


def collector_closure_digest(
    *,
    collector_sha256: str,
    delegate_sha256: str,
    source_manifest_sha256: str,
) -> str:
    return _sha256(
        {
            "collector_sha256": _nonzero_sha256(collector_sha256, label="collector_sha256"),
            "delegate_sha256": _nonzero_sha256(delegate_sha256, label="collector_delegate_sha256"),
            "source_manifest_sha256": _nonzero_sha256(
                source_manifest_sha256,
                label="collector_source_manifest_sha256",
            ),
        }
    )


def canonical_role_compose_path(*, operation_id: str, role: str) -> str:
    if role not in ROLES:
        raise ComposeObserverExecutionContractError("role Compose role is invalid")
    return (
        f"{SECRET_ROOT_PREFIX}/{operation_id}/convergence-observer-runtime/"
        f"{role}/compose-observer-execution.yml"
    )


def canonical_role_environment_path(*, operation_id: str, role: str) -> str:
    if role not in ROLES:
        raise ComposeObserverExecutionContractError("role environment role is invalid")
    return (
        f"{SECRET_ROOT_PREFIX}/{operation_id}/convergence-observer-runtime/"
        f"{role}/compose-observer-execution.env"
    )


def canonical_network_name(*, operation_id: str, role: str) -> str:
    if role not in ROLES:
        raise ComposeObserverExecutionContractError("network role is invalid")
    project = f"tb3p-{operation_id.replace('-', '')}-{role.replace('_', '-')}"
    return f"{project}_{role}"


def canonical_container_id_file(*, operation_id: str, role: str) -> str:
    return f"{canonical_runtime_input_root(operation_id=operation_id, role=role)}/compose-observer.cid"


def role_material_inspection_sha256(
    *,
    operation_id: str,
    role: str,
    role_material_sha256: str,
    role_compose_sha256: str,
    role_environment_sha256: str,
) -> str:
    """Bind the two inspected role files to the immutable role material."""

    return _sha256(
        {
            "operation_id": _uuid(operation_id, label="operation_id"),
            "role": role,
            "role_material_sha256": _nonzero_sha256(
                role_material_sha256, label="role_material_sha256"
            ),
            "role_compose_sha256": _nonzero_sha256(
                role_compose_sha256, label="role_compose_sha256"
            ),
            "role_environment_sha256": _nonzero_sha256(
                role_environment_sha256, label="role_environment_sha256"
            ),
        }
    )


def observer_service_shape(*, role: str) -> dict[str, Any]:
    """Return the fixed, data-only observer service shape for one role."""

    if role not in ROLES:
        raise ComposeObserverExecutionContractError("observer service role is invalid")
    return {
        "role": role,
        "service": f"{role}_sync_observer",
        "profiles": [f"{role.replace('_', '-')}-observe"],
        "restart": "no",
        "command": list(OBSERVER_DUMMY_COMMAND),
        "depends_on": {f"{role}_db": "service_healthy"},
        "networks": [role],
    }


def _validated_runtime_image_ids(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(RUNTIME_IMAGE_KINDS):
        raise ComposeObserverExecutionContractError(f"{label} fields differ")
    result = {kind: value[kind] for kind in RUNTIME_IMAGE_KINDS}
    if (
        any(not isinstance(item, str) or IMAGE_ID_RE.fullmatch(item) is None for item in result.values())
        or len(set(result.values())) != len(result)
    ):
        raise ComposeObserverExecutionContractError(f"{label} is invalid")
    return result


def validate_canonical_observer_service(
    canonical_compose: Any,
    *,
    role: str,
    label: str,
) -> dict[str, Any]:
    """Validate only the fixed data shape committed by the runtime binding."""

    expected = observer_service_shape(role=role)
    if not isinstance(canonical_compose, Mapping):
        raise ComposeObserverExecutionContractError(f"{label} Compose is invalid")
    services = canonical_compose.get("services")
    networks = canonical_compose.get("networks")
    if not isinstance(services, Mapping) or not isinstance(networks, Mapping):
        raise ComposeObserverExecutionContractError(f"{label} Compose members are invalid")
    service = services.get(expected["service"])
    network = networks.get(role)
    expected_depends_on = {f"{role}_db": {"condition": "service_healthy"}}
    expected_network = {
        "labels": dict(OBSERVER_OPERATION_NETWORK_LABELS),
        "internal": True,
    }
    if (
        not isinstance(service, Mapping)
        or service.get("profiles") != expected["profiles"]
        or service.get("restart") != expected["restart"]
        or service.get("command") != expected["command"]
        or service.get("depends_on") != expected_depends_on
        or service.get("networks") != expected["networks"]
        or network != expected_network
        or "ports" in service
        or "env_file" in service
        or service.get("network_mode") == "host"
        or "container_name" in service
    ):
        raise ComposeObserverExecutionContractError(f"{label} observer service differs")
    return expected


def _validated_runtime_binding_projection(
    value: Any,
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
    role: str,
) -> dict[str, Any]:
    """Accept a narrow projection after worker-owned data-only validation.

    The worker validates the complete local target binding and target-set with
    fixed data-only code.  This module deliberately does not load or execute a
    Git module; it binds this redacted projection to the requested operation.
    """

    fields = {
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "canonical_compose_sha256",
        "role",
        "role_material_sha256",
        "role_runtime_image_ids",
        "binding_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ComposeObserverExecutionContractError(
            "runtime target binding projection fields differ"
        )
    if (
        value.get("campaign_id") != campaign_id
        or value.get("operation_id") != operation_id
        or value.get("release_sha") != release_sha
        or value.get("manifest_sha256") != manifest_sha256
        or value.get("role") != role
    ):
        raise ComposeObserverExecutionContractError(
            "runtime target binding projection identity differs"
        )
    return {
        "canonical_compose_sha256": _nonzero_sha256(
            value.get("canonical_compose_sha256"),
            label="binding canonical_compose_sha256",
        ),
        "role_material_sha256": _nonzero_sha256(
            value.get("role_material_sha256"), label="binding role_material_sha256"
        ),
        "role_runtime_image_ids": _validated_runtime_image_ids(
            value.get("role_runtime_image_ids"), label="binding runtime image IDs"
        ),
        "binding_sha256": _nonzero_sha256(
            value.get("binding_sha256"), label="binding_sha256"
        ),
    }


def _mount(source: str, target: str) -> dict[str, Any]:
    return {
        "type": "bind",
        "source": source,
        "target": target,
        "read_only": True,
        "bind": {"create_host_path": False},
    }


def _validate_readonly_mount(value: Any, *, expected: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or dict(value) != dict(expected):
        raise ComposeObserverExecutionContractError(f"{label} differs")
    return dict(expected)


def _validate_execution_service(
    service: Any,
    *,
    role: str,
    runtime_image_ids: Mapping[str, str],
    release_mount: Mapping[str, Any],
    runtime_input_mount: Mapping[str, Any],
    role_environment_path: str,
) -> None:
    if not isinstance(service, Mapping):
        raise ComposeObserverExecutionContractError("rendered observer service is invalid")
    if set(service) != {
        "image",
        "pull_policy",
        "profiles",
        "restart",
        "command",
        "depends_on",
        "networks",
        "volumes",
        "env_file",
        "read_only",
        "cap_drop",
        "security_opt",
    }:
        raise ComposeObserverExecutionContractError("rendered observer service keys differ")
    expected_shape = observer_service_shape(role=role)
    expected_depends_on = {f"{role}_db": {"condition": "service_healthy"}}
    if (
        service.get("image") != runtime_image_ids["app"]
        or service.get("pull_policy") != "never"
        or service.get("profiles") != expected_shape["profiles"]
        or service.get("restart") != expected_shape["restart"]
        or service.get("command") != expected_shape["command"]
        or service.get("depends_on") != expected_depends_on
        or service.get("networks") != expected_shape["networks"]
        or service.get("volumes") != [dict(release_mount), dict(runtime_input_mount)]
        or service.get("env_file") != [role_environment_path]
        or service.get("read_only") is not True
        or service.get("cap_drop") != ["ALL"]
        or service.get("security_opt") != ["no-new-privileges:true"]
        or "ports" in service
        or service.get("network_mode") == "host"
        or "container_name" in service
    ):
        raise ComposeObserverExecutionContractError("rendered observer service differs")


def _compose_argv(
    *,
    project_name: str,
    role_compose_path: str,
    role_environment_path: str,
    profile: str,
    service: str,
    container_id_file: str,
) -> list[str]:
    return [
        DOCKER,
        "compose",
        "--project-name",
        project_name,
        "--env-file",
        role_environment_path,
        "--file",
        role_compose_path,
        "--profile",
        profile,
        "run",
        "--cidfile",
        container_id_file,
        "--rm",
        "--no-deps",
        service,
    ]


def _collector_argv(
    *,
    campaign_id: str,
    release_sha: str,
    collector_path: str,
    source_manifest_path: str,
) -> list[str]:
    """The exact command prefix appended after the Compose service name.

    The observer request supplies only its bound plan hash and row limit at
    execution time.  No caller-provided executable, path, or option can enter
    this prefix.
    """

    return [
        CONTAINER_COLLECTOR_INTERPRETER,
        "-B",
        "-I",
        "-S",
        collector_path,
        "--campaign-id",
        campaign_id,
        "--release-sha",
        release_sha,
        "--source-manifest-path",
        source_manifest_path,
        "--plan-sha256",
    ]


def compose_runtime_argv(
    plan: Mapping[str, Any], *, request_plan_sha256: str, max_rows_per_table: int
) -> list[str]:
    """Return the sole executable argv from a validated plan and request facts."""

    checked = validate_execution_plan(plan)
    request_digest = _nonzero_sha256(request_plan_sha256, label="request plan_sha256")
    if type(max_rows_per_table) is not int or not 1 <= max_rows_per_table <= 100_000:
        raise ComposeObserverExecutionContractError("request max_rows_per_table is invalid")
    return [
        *checked["compose_argv"],
        *checked["collector_argv"],
        request_digest,
        "--max-rows-per-table",
        str(max_rows_per_table),
    ]


def _cleanup_probe_argv(*, project_name: str) -> list[str]:
    """Return the zero-oneoff residue probe an executor must run pre/post."""

    return [
        DOCKER,
        "ps",
        "--all",
        "--quiet",
        "--filter",
        f"label=com.docker.compose.project={project_name}",
        "--filter",
        "label=com.docker.compose.oneoff=True",
    ]


def _config_probe_argv(
    *, project_name: str, role_compose_path: str, role_environment_path: str, profile: str
) -> list[str]:
    return [
        DOCKER,
        "compose",
        "--project-name",
        project_name,
        "--env-file",
        role_environment_path,
        "--file",
        role_compose_path,
        "--profile",
        profile,
        "config",
        "--format",
        "json",
    ]


def _validate_network_inspection(value: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Require a normalized result of ``docker network inspect``.

    The executor derives this small object from the inspect response before it
    removes the one-shot container.  Raw inspect output is intentionally never
    persisted because it can contain unrelated container metadata.
    """

    fields = {
        "source",
        "name",
        "id_sha256",
        "operation_id",
        "project_name",
        "internal",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ComposeObserverExecutionContractError("network inspection fields differ")
    expected = {
        "source": "docker-network-inspect-v1",
        "name": plan["network_name"],
        "operation_id": plan["operation_id"],
        "project_name": plan["project_name"],
        "internal": True,
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ComposeObserverExecutionContractError("network inspection identity differs")
    return {**expected, "id_sha256": _nonzero_sha256(value.get("id_sha256"), label="network id_sha256")}


def _validate_container_inspection(value: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Require a normalized result of the one-shot ``docker inspect`` call."""

    fields = {
        "source",
        "id_sha256",
        "operation_id",
        "project_name",
        "service",
        "oneoff",
        "network_name",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ComposeObserverExecutionContractError("container inspection fields differ")
    expected = {
        "source": "docker-container-inspect-v1",
        "operation_id": plan["operation_id"],
        "project_name": plan["project_name"],
        "service": plan["service"],
        "oneoff": True,
        "network_name": plan["network_name"],
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ComposeObserverExecutionContractError("container inspection identity differs")
    return {**expected, "id_sha256": _nonzero_sha256(value.get("id_sha256"), label="container id_sha256")}


def _plan_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "plan_sha256"})


def _receipt_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "receipt_sha256"})


def build_execution_plan(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
    canonical_compose_sha256: str,
    canonical_compose: Mapping[str, Any],
    rendered_observer_service: Mapping[str, Any],
    role: str,
    project_name: str,
    role_compose_path: str,
    role_compose_sha256: str,
    role_environment_path: str,
    role_environment_sha256: str,
    collector_sha256: str,
    collector_delegate_sha256: str,
    collector_source_manifest_sha256: str,
    role_material_sha256: str,
    runtime_image_ids: Mapping[str, Any],
    runtime_target_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one non-executing, exact one-shot observer plan.

    ``rendered_observer_service`` represents the future role-scoped Compose
    execution overlay after interpolation.  It is intentionally data, not a
    Docker invocation; callers must separately prove the rendered config came
    from the exact files named in the returned plan.
    """

    campaign = _uuid(campaign_id, label="campaign_id")
    operation = _uuid(operation_id, label="operation_id")
    if campaign == operation:
        raise ComposeObserverExecutionContractError("campaign and operation IDs must differ")
    release = _release_sha(release_sha, label="release_sha")
    manifest = _nonzero_sha256(manifest_sha256, label="manifest_sha256")
    compose_sha = _nonzero_sha256(
        canonical_compose_sha256,
        label="canonical_compose_sha256",
    )
    if role not in ROLES:
        raise ComposeObserverExecutionContractError("observer role is invalid")
    expected_project = f"tb3p-{operation.replace('-', '')}-{role.replace('_', '-')}"
    if project_name != expected_project:
        raise ComposeObserverExecutionContractError("role Compose project name differs")
    compose_path = _absolute_path(role_compose_path, label="role_compose_path")
    environment_path = _absolute_path(role_environment_path, label="role_environment_path")
    if (
        compose_path != canonical_role_compose_path(operation_id=operation, role=role)
        or environment_path
        != canonical_role_environment_path(operation_id=operation, role=role)
    ):
        raise ComposeObserverExecutionContractError("role Compose or environment path differs")
    image_ids = _validated_runtime_image_ids(runtime_image_ids, label="runtime image IDs")
    # The immutable execution overlay is deliberately stricter than the
    # source role Compose: its service has image/mount/env_file fields that
    # the source shape does not carry.  Validate its network here; the exact
    # observer service is validated below after all release-local paths are
    # known.
    if (
        not isinstance(canonical_compose, Mapping)
        or not isinstance(canonical_compose.get("services"), Mapping)
        or not isinstance(canonical_compose.get("networks"), Mapping)
        or canonical_compose["networks"].get(role)
        != {
            "labels": dict(OBSERVER_OPERATION_NETWORK_LABELS),
            "internal": True,
        }
    ):
        raise ComposeObserverExecutionContractError("canonical observer Compose differs")
    binding = _validated_runtime_binding_projection(
        runtime_target_binding,
        campaign_id=campaign,
        operation_id=operation,
        release_sha=release,
        manifest_sha256=manifest,
        role=role,
    )
    if (
        binding["role_runtime_image_ids"] != image_ids
        or binding["role_material_sha256"] != _nonzero_sha256(
            role_material_sha256, label="role_material_sha256"
        )
        or binding["canonical_compose_sha256"] != compose_sha
    ):
        raise ComposeObserverExecutionContractError("runtime target binding material differs")
    release_root = canonical_release_root(operation_id=operation, release_sha=release)
    runtime_input_root = canonical_runtime_input_root(operation_id=operation, role=role)
    release_mount = _mount(release_root, release_root)
    runtime_input_mount = _mount(runtime_input_root, runtime_input_root)
    _validate_execution_service(
        rendered_observer_service,
        role=role,
        runtime_image_ids=image_ids,
        release_mount=release_mount,
        runtime_input_mount=runtime_input_mount,
        role_environment_path=environment_path,
    )
    shape = observer_service_shape(role=role)
    compose_file_sha = _nonzero_sha256(
        role_compose_sha256, label="role_compose_sha256"
    )
    environment_file_sha = _nonzero_sha256(
        role_environment_sha256, label="role_environment_sha256"
    )
    collector_path = canonical_container_collector_path(
        operation_id=operation,
        release_sha=release,
    )
    collector_digest = _nonzero_sha256(collector_sha256, label="collector_sha256")
    collector_delegate_digest = _nonzero_sha256(
        collector_delegate_sha256,
        label="collector_delegate_sha256",
    )
    source_manifest_digest = _nonzero_sha256(
        collector_source_manifest_sha256,
        label="collector_source_manifest_sha256",
    )
    document: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "status": "planned-not-executed",
        "campaign_id": campaign,
        "operation_id": operation,
        "release_sha": release,
        "manifest_sha256": manifest,
        "canonical_compose_sha256": compose_sha,
        "role": role,
        "service": shape["service"],
        "profile": shape["profiles"][0],
        "project_name": project_name,
        "role_compose_path": compose_path,
        "role_compose_sha256": compose_file_sha,
        "role_environment_path": environment_path,
        "role_environment_sha256": environment_file_sha,
        "collector_path": collector_path,
        "collector_sha256": collector_digest,
        "collector_delegate_sha256": collector_delegate_digest,
        "collector_closure_sha256": collector_closure_digest(
            collector_sha256=collector_digest,
            delegate_sha256=collector_delegate_digest,
            source_manifest_sha256=source_manifest_digest,
        ),
        "collector_source_manifest_path": canonical_collector_source_manifest_path(
            operation_id=operation,
            role=role,
        ),
        "collector_source_manifest_sha256": source_manifest_digest,
        "collector_argv": _collector_argv(
            campaign_id=campaign,
            release_sha=release,
            collector_path=collector_path,
            source_manifest_path=canonical_collector_source_manifest_path(
                operation_id=operation,
                role=role,
            ),
        ),
        "config_probe_argv": _config_probe_argv(
            project_name=project_name,
            role_compose_path=compose_path,
            role_environment_path=environment_path,
            profile=shape["profiles"][0],
        ),
        "resolved_observer_service_sha256": _sha256(rendered_observer_service),
        "role_material_sha256": binding["role_material_sha256"],
        "role_material_inspection_sha256": role_material_inspection_sha256(
            operation_id=operation,
            role=role,
            role_material_sha256=binding["role_material_sha256"],
            role_compose_sha256=compose_file_sha,
            role_environment_sha256=environment_file_sha,
        ),
        "runtime_target_binding_sha256": binding["binding_sha256"],
        "runtime_image_ids": image_ids,
        "internal_network": role,
        "network_name": canonical_network_name(operation_id=operation, role=role),
        "release_mount": release_mount,
        "runtime_input_mount": runtime_input_mount,
        "container_id_file": canonical_container_id_file(
            operation_id=operation,
            role=role,
        ),
        "compose_argv": _compose_argv(
            project_name=project_name,
            role_compose_path=compose_path,
            role_environment_path=environment_path,
            profile=shape["profiles"][0],
            service=shape["service"],
            container_id_file=canonical_container_id_file(
                operation_id=operation,
                role=role,
            ),
        ),
        "cleanup_probe_argv": _cleanup_probe_argv(project_name=project_name),
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_stdout_bytes": MAX_STDOUT_BYTES,
        "max_stderr_bytes": MAX_STDERR_BYTES,
        "production_mutation_forbidden": True,
        "object_storage_contact_forbidden": True,
        "plan_sha256": ZERO_SHA256,
    }
    document["plan_sha256"] = _plan_digest(document)
    return validate_execution_plan(document)


def validate_execution_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PLAN_FIELDS:
        raise ComposeObserverExecutionContractError("execution plan fields differ")
    document = dict(value)
    if document.get("schema") != PLAN_SCHEMA or document.get("status") != "planned-not-executed":
        raise ComposeObserverExecutionContractError("execution plan identity differs")
    campaign = _uuid(document.get("campaign_id"), label="campaign_id")
    operation = _uuid(document.get("operation_id"), label="operation_id")
    release = _release_sha(document.get("release_sha"), label="release_sha")
    manifest = _nonzero_sha256(document.get("manifest_sha256"), label="manifest_sha256")
    _nonzero_sha256(
        document.get("canonical_compose_sha256"),
        label="canonical_compose_sha256",
    )
    role = document.get("role")
    if role not in ROLES:
        raise ComposeObserverExecutionContractError("execution plan role is invalid")
    shape = observer_service_shape(role=role)
    expected_project = f"tb3p-{operation.replace('-', '')}-{role.replace('_', '-')}"
    if (
        campaign == operation
        or document.get("service") != shape["service"]
        or document.get("profile") != shape["profiles"][0]
        or document.get("project_name") != expected_project
        or document.get("internal_network") != role
        or document.get("network_name")
        != canonical_network_name(operation_id=operation, role=role)
        or document.get("container_id_file")
        != canonical_container_id_file(operation_id=operation, role=role)
        or document.get("production_mutation_forbidden") is not True
        or document.get("object_storage_contact_forbidden") is not True
        or document.get("timeout_seconds") != TIMEOUT_SECONDS
        or document.get("max_stdout_bytes") != MAX_STDOUT_BYTES
        or document.get("max_stderr_bytes") != MAX_STDERR_BYTES
    ):
        raise ComposeObserverExecutionContractError("execution plan contract differs")
    compose_path = _absolute_path(document.get("role_compose_path"), label="role_compose_path")
    environment_path = _absolute_path(document.get("role_environment_path"), label="role_environment_path")
    if (
        compose_path != canonical_role_compose_path(operation_id=operation, role=role)
        or environment_path
        != canonical_role_environment_path(operation_id=operation, role=role)
    ):
        raise ComposeObserverExecutionContractError("execution plan role file paths differ")
    image_ids = _validated_runtime_image_ids(
        document.get("runtime_image_ids"), label="runtime image IDs"
    )
    expected_release_mount = _mount(
        canonical_release_root(operation_id=operation, release_sha=release),
        canonical_release_root(operation_id=operation, release_sha=release),
    )
    expected_input_mount = _mount(
        canonical_runtime_input_root(operation_id=operation, role=role),
        canonical_runtime_input_root(operation_id=operation, role=role),
    )
    _validate_readonly_mount(document.get("release_mount"), expected=expected_release_mount, label="release mount")
    _validate_readonly_mount(document.get("runtime_input_mount"), expected=expected_input_mount, label="runtime input mount")
    for field in (
        "role_compose_sha256",
        "role_material_sha256",
        "role_environment_sha256",
        "role_material_inspection_sha256",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "collector_sha256",
        "collector_delegate_sha256",
        "collector_closure_sha256",
        "collector_source_manifest_sha256",
    ):
        _nonzero_sha256(document.get(field), label=field)
    if document.get("role_material_inspection_sha256") != role_material_inspection_sha256(
        operation_id=operation,
        role=role,
        role_material_sha256=document["role_material_sha256"],
        role_compose_sha256=document["role_compose_sha256"],
        role_environment_sha256=document["role_environment_sha256"],
    ):
        raise ComposeObserverExecutionContractError("execution plan role material inspection differs")
    collector_path = canonical_container_collector_path(
        operation_id=operation,
        release_sha=release,
    )
    if (
        document.get("collector_path") != collector_path
        or document.get("collector_argv")
        != _collector_argv(
            campaign_id=campaign,
            release_sha=release,
            collector_path=collector_path,
            source_manifest_path=canonical_collector_source_manifest_path(
                operation_id=operation,
                role=role,
            ),
        )
    ):
        raise ComposeObserverExecutionContractError("execution plan collector command differs")
    if (
        document.get("config_probe_argv")
        != _config_probe_argv(
            project_name=expected_project,
            role_compose_path=compose_path,
            role_environment_path=environment_path,
            profile=shape["profiles"][0],
        )
    ):
        raise ComposeObserverExecutionContractError("execution plan config probe differs")
    _nonzero_sha256(
        document.get("resolved_observer_service_sha256"),
        label="resolved_observer_service_sha256",
    )
    if document.get("collector_closure_sha256") != collector_closure_digest(
        collector_sha256=document["collector_sha256"],
        delegate_sha256=document["collector_delegate_sha256"],
        source_manifest_sha256=document["collector_source_manifest_sha256"],
    ):
        raise ComposeObserverExecutionContractError("execution plan collector closure differs")
    if document.get("collector_source_manifest_path") != canonical_collector_source_manifest_path(
        operation_id=operation,
        role=role,
    ):
        raise ComposeObserverExecutionContractError("execution plan collector source manifest path differs")
    expected_argv = _compose_argv(
        project_name=expected_project,
        role_compose_path=compose_path,
        role_environment_path=environment_path,
        profile=shape["profiles"][0],
        service=shape["service"],
        container_id_file=canonical_container_id_file(
            operation_id=operation,
            role=role,
        ),
    )
    if document.get("compose_argv") != expected_argv:
        raise ComposeObserverExecutionContractError("execution plan Compose argv differs")
    if document.get("cleanup_probe_argv") != _cleanup_probe_argv(
        project_name=expected_project
    ):
        raise ComposeObserverExecutionContractError("execution plan cleanup probe differs")
    if document.get("plan_sha256") != _plan_digest(document):
        raise ComposeObserverExecutionContractError("execution plan digest differs")
    return document


def build_execution_receipt(
    *,
    plan: Mapping[str, Any],
    image_id: str,
    stdout: bytes,
    stderr_bytes: int,
    exit_code: int,
    started_at: datetime,
    finished_at: datetime,
    container_removed: bool,
    cleanup_verified: bool,
    network_inspection: Mapping[str, Any],
    container_inspection: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the redacted receipt shape an executor must return after success."""

    checked = validate_execution_plan(plan)
    if image_id != checked["runtime_image_ids"]["app"]:
        raise ComposeObserverExecutionContractError("receipt image differs from the plan")
    if not isinstance(stdout, bytes) or len(stdout) > MAX_STDOUT_BYTES:
        raise ComposeObserverExecutionContractError("receipt stdout is invalid")
    if (
        type(stderr_bytes) is not int
        or not 0 <= stderr_bytes <= MAX_STDERR_BYTES
        or type(exit_code) is not int
        or exit_code != 0
        or container_removed is not True
        or cleanup_verified is not True
    ):
        raise ComposeObserverExecutionContractError("receipt process outcome is invalid")
    if started_at.tzinfo is None or finished_at.tzinfo is None:
        raise ComposeObserverExecutionContractError("receipt timestamps are invalid")
    network = _validate_network_inspection(network_inspection, plan=checked)
    container = _validate_container_inspection(container_inspection, plan=checked)
    started = started_at.astimezone(timezone.utc)
    finished = finished_at.astimezone(timezone.utc)
    duration_ms = int((finished - started).total_seconds() * 1000)
    if not 0 <= duration_ms <= TIMEOUT_SECONDS * 1000:
        raise ComposeObserverExecutionContractError("receipt duration exceeds the plan")
    document: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "completed-read-only",
        "campaign_id": checked["campaign_id"],
        "operation_id": checked["operation_id"],
        "release_sha": checked["release_sha"],
        "manifest_sha256": checked["manifest_sha256"],
        "role": checked["role"],
        "service": checked["service"],
        "plan_sha256": checked["plan_sha256"],
        "runtime_target_binding_sha256": checked["runtime_target_binding_sha256"],
        "image_id": image_id,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stdout_bytes": len(stdout),
        "stderr_bytes": stderr_bytes,
        "exit_code": exit_code,
        "started_at": _timestamp_text(started),
        "finished_at": _timestamp_text(finished),
        "duration_ms": duration_ms,
        "container_removed": True,
        "cleanup_verified": True,
        "network_inspection": network,
        "container_inspection": container,
        "production_mutated": False,
        "object_storage_contacted": False,
        "receipt_sha256": ZERO_SHA256,
    }
    document["receipt_sha256"] = _receipt_digest(document)
    return validate_execution_receipt(document, plan=checked)


def validate_execution_receipt(value: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    checked_plan = validate_execution_plan(plan)
    if not isinstance(value, Mapping) or set(value) != RECEIPT_FIELDS:
        raise ComposeObserverExecutionContractError("execution receipt fields differ")
    document = dict(value)
    expected = {
        "schema": RECEIPT_SCHEMA,
        "status": "completed-read-only",
        "campaign_id": checked_plan["campaign_id"],
        "operation_id": checked_plan["operation_id"],
        "release_sha": checked_plan["release_sha"],
        "manifest_sha256": checked_plan["manifest_sha256"],
        "role": checked_plan["role"],
        "service": checked_plan["service"],
        "plan_sha256": checked_plan["plan_sha256"],
        "runtime_target_binding_sha256": checked_plan["runtime_target_binding_sha256"],
        "image_id": checked_plan["runtime_image_ids"]["app"],
        "container_removed": True,
        "cleanup_verified": True,
        "production_mutated": False,
        "object_storage_contacted": False,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise ComposeObserverExecutionContractError("execution receipt binding differs")
    _validate_network_inspection(document.get("network_inspection"), plan=checked_plan)
    _validate_container_inspection(document.get("container_inspection"), plan=checked_plan)
    for field in (
        "stdout_sha256",
        "receipt_sha256",
    ):
        _nonzero_sha256(document.get(field), label=field)
    if (
        type(document.get("stdout_bytes")) is not int
        or not 0 <= document["stdout_bytes"] <= checked_plan["max_stdout_bytes"]
        or type(document.get("stderr_bytes")) is not int
        or not 0 <= document["stderr_bytes"] <= checked_plan["max_stderr_bytes"]
        or type(document.get("exit_code")) is not int
        or document["exit_code"] != 0
        or type(document.get("duration_ms")) is not int
        or not 0 <= document["duration_ms"] <= checked_plan["timeout_seconds"] * 1000
    ):
        raise ComposeObserverExecutionContractError("execution receipt outcome differs")
    started = _timestamp(document.get("started_at"), label="receipt started_at")
    finished = _timestamp(document.get("finished_at"), label="receipt finished_at")
    if finished < started or int((finished - started).total_seconds() * 1000) != document["duration_ms"]:
        raise ComposeObserverExecutionContractError("execution receipt timing differs")
    if document.get("receipt_sha256") != _receipt_digest(document):
        raise ComposeObserverExecutionContractError("execution receipt digest differs")
    return document
