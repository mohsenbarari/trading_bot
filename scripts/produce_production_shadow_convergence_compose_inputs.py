#!/usr/bin/env python3
"""Create root-only Compose-observer plan/material pairs without execution.

This producer has no Docker, SSH, Object Storage, or network client.  It
reopens only the local operation artifacts already staged on a runtime host,
derives the narrow observer plan, and publishes each canonical output once.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import keyword
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import SecureFileError, read_secure_bytes, sha256_secure_file, write_secure_new_bytes
from scripts import produce_production_shadow_prepare_material as prepare_material
from scripts import production_shadow_convergence_compose_execution as execution
from scripts import production_shadow_convergence_runtime_targets as runtime_targets
from scripts import production_shadow_cutover_controller as cutover
from scripts.render_three_site_production_shadow_role_compose import (
    ProductionShadowRoleError,
    canonical_role_compose_bytes,
    parse_env_values,
)


RUNTIME_ROLES = execution.ROLES
IMAGE_KINDS = execution.RUNTIME_IMAGE_KINDS
IMAGE_ENV_BY_KIND = {
    "app": "PRODUCTION_SHADOW_APP_IMAGE_ID",
    "postgres": "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID",
    "redis": "PRODUCTION_SHADOW_REDIS_IMAGE_ID",
    "nginx": "PRODUCTION_SHADOW_NGINX_IMAGE_ID",
}
MAX_INPUT_BYTES = 64 * 1024 * 1024
OUTPUT_MODE = 0o600
GIT = "/usr/bin/git"
MAX_COLLECTOR_SOURCE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_COLLECTOR_SOURCE_FILES = 5_000
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_PAGER": "cat",
}
GIT_STRICT_OPTIONS = (
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.pager=cat",
    "-c", "protocol.file.allow=never",
)


class ComposeInputProducerError(RuntimeError):
    """The local Compose input closure cannot be proven exact."""


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ComposeInputProducerError("document is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ComposeInputProducerError("JSON has duplicate fields")
        document[key] = value
    return document


def _read_canonical_json(path: Path, *, label: str, owner_uid: int) -> tuple[dict[str, Any], bytes]:
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=owner_uid,
            max_size=MAX_INPUT_BYTES,
        )
    except SecureFileError as exc:
        raise ComposeInputProducerError(f"{label} is unavailable") from exc
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComposeInputProducerError(f"{label} is not strict canonical JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document):
        raise ComposeInputProducerError(f"{label} is not canonical JSON")
    return document, payload


def _role_paths(*, operation_id: str, release_sha: str, role: str) -> dict[str, Path]:
    if role not in RUNTIME_ROLES:
        raise ComposeInputProducerError("Witness is excluded from Compose observer inputs")
    project_root = Path(execution.PROJECT_ROOT_PREFIX) / operation_id
    secret_root = Path(execution.SECRET_ROOT_PREFIX) / operation_id
    role_path = role.replace("_", "-")
    runtime_root = secret_root / "convergence-observer-runtime" / role
    return {
        "source_compose": project_root / "rendered" / role_path / "docker-compose.yml",
        "source_environment": secret_root / role_path / "runtime.env.role",
        "role_material": project_root / "incoming" / f"role-material-{role_path}.tar",
        "binding": runtime_root / "runtime-target-binding.json",
        "target_set": runtime_root / "convergence-runtime-targets.json",
        "compose": Path(execution.canonical_role_compose_path(operation_id=operation_id, role=role)),
        "environment": Path(execution.canonical_role_environment_path(operation_id=operation_id, role=role)),
        "plan": runtime_root / "compose-observer-execution-plan.json",
        "material": runtime_root / "compose-observer-execution-material.json",
        "source_manifest": runtime_root / "collector-source-manifest.json",
        "release_root": project_root / "releases" / release_sha,
    }


def _manifest_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Take only the v4 facts required to derive a role-local plan."""

    if not isinstance(manifest, Mapping) or manifest.get("schema") != runtime_targets.CUTOVER_MANIFEST_SCHEMA:
        raise ComposeInputProducerError("production cutover manifest is not v4")
    try:
        checked = cutover.validate_manifest(manifest)
    except cutover.CutoverContractError as exc:
        raise ComposeInputProducerError("production cutover manifest is invalid") from exc
    artifacts = checked["artifacts"]
    return {
        "campaign_id": checked["campaign_id"],
        "operation_id": checked["operation_id"],
        "release_sha": checked["release_sha"],
        "release_tree_sha": checked["release_tree_sha"],
        "shadow_compose_sha256": artifacts["shadow_compose_sha256"],
        "role_materials": artifacts["role_materials"],
        "role_runtime_image_ids": artifacts["role_runtime_image_ids"],
    }


def _read_v4_manifest(path: Path, *, owner_uid: int) -> tuple[dict[str, Any], str]:
    try:
        return cutover.read_root_only_manifest(path, owner_uid=owner_uid)
    except cutover.CutoverContractError as exc:
        raise ComposeInputProducerError("production cutover manifest is unavailable") from exc


def _parse_canonical_compose(payload: bytes, *, role: str) -> dict[str, Any]:
    try:
        document = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise ComposeInputProducerError("rendered role Compose is invalid") from exc
    if not isinstance(document, dict):
        raise ComposeInputProducerError("rendered role Compose is invalid")
    try:
        if canonical_role_compose_bytes(document) != payload:
            raise ComposeInputProducerError("rendered role Compose is not canonical")
        execution.validate_canonical_observer_service(
            document,
            role=role,
            label="rendered role Compose",
        )
    except (ProductionShadowRoleError, execution.ComposeObserverExecutionContractError) as exc:
        raise ComposeInputProducerError("rendered role Compose observer differs") from exc
    return document


def _environment_image_ids(payload: bytes) -> dict[str, str]:
    try:
        values = parse_env_values(payload.decode("ascii"))
    except (UnicodeDecodeError, ProductionShadowRoleError) as exc:
        raise ComposeInputProducerError("role environment is invalid") from exc
    result = {kind: values.get(name) for kind, name in IMAGE_ENV_BY_KIND.items()}
    try:
        return execution._validated_runtime_image_ids(result, label="role environment image IDs")
    except execution.ComposeObserverExecutionContractError as exc:
        raise ComposeInputProducerError("role environment image IDs differ") from exc


def _execution_environment(payload: bytes, *, operation_id: str) -> bytes:
    """Copy the sealed role environment and add only the fixed operation ID."""

    try:
        values = parse_env_values(payload.decode("ascii"))
    except (UnicodeDecodeError, ProductionShadowRoleError) as exc:
        raise ComposeInputProducerError("role environment is invalid") from exc
    key = "PRODUCTION_SHADOW_OPERATION_ID"
    current = values.get(key)
    if current is not None and current != operation_id:
        raise ComposeInputProducerError("role environment operation ID differs")
    if current == operation_id:
        return payload
    suffix = b"" if payload.endswith(b"\n") else b"\n"
    return payload + suffix + f"{key}={operation_id}\n".encode("ascii")


def _collector_source_manifest(
    *, release_root: Path, release_sha: str, release_tree_sha: str, owner_uid: int
) -> tuple[bytes, str]:
    """Hash the closed container collector source set without importing it."""

    _verify_sealed_release_identity(
        release_root=release_root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
    )
    relative_paths = _sealed_collector_python_paths(
        release_root=release_root,
        release_tree_sha=release_tree_sha,
    )
    files: dict[str, str] = {}
    for relative in relative_paths:
        text = str(relative)
        path = release_root / text
        if path.is_symlink() or text in files:
            raise ComposeInputProducerError("collector source manifest path is invalid")
        try:
            payload = read_secure_bytes(
                path,
                label="collector source manifest entry",
                owner_uid=owner_uid,
                max_size=MAX_INPUT_BYTES,
            )
        except SecureFileError as exc:
            raise ComposeInputProducerError("collector source manifest entry is unavailable") from exc
        blob = _sealed_git_blob(
            release_root=release_root,
            release_tree_sha=release_tree_sha,
            relative_path=text,
        )
        if payload != blob:
            raise ComposeInputProducerError("collector source differs from sealed Git blob")
        files[text] = hashlib.sha256(blob).hexdigest()
    document: dict[str, Any] = {
        "schema": "production-shadow-container-collector-source-manifest-v1",
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "files": files,
        "source_manifest_sha256": "0" * 64,
    }
    document["source_manifest_sha256"] = hashlib.sha256(
        _canonical_json({key: value for key, value in document.items() if key != "source_manifest_sha256"})
    ).hexdigest()
    payload = _canonical_json(document)
    if len(payload) > MAX_COLLECTOR_SOURCE_MANIFEST_BYTES:
        raise ComposeInputProducerError("collector source manifest is oversized")
    return payload, str(document["source_manifest_sha256"])


def _sealed_git_output(release_root: Path, arguments: list[str], *, label: str) -> bytes:
    _require_sealed_git_object_command(arguments)
    try:
        result = subprocess.run(
            [GIT, *GIT_STRICT_OPTIONS, "--no-replace-objects", "-C", str(release_root), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=GIT_SAFE_ENV,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ComposeInputProducerError(f"sealed Git {label} is unavailable") from exc
    if result.returncode != 0 or len(result.stdout) > MAX_INPUT_BYTES or len(result.stderr) > 64 * 1024:
        raise ComposeInputProducerError(f"sealed Git {label} is invalid")
    return result.stdout


def _require_sealed_git_object_command(arguments: list[str]) -> None:
    if (
        len(arguments) == 3
        and arguments[:2] == ["rev-parse", "--verify"]
        and isinstance(arguments[2], str)
        and SHA40_RE.fullmatch(arguments[2].removesuffix("^{commit}").removesuffix("^{tree}"))
        and arguments[2].endswith(("^{commit}", "^{tree}"))
    ):
        return
    if (
        len(arguments) == 5
        and arguments[:4] == ["ls-tree", "-r", "-z", "--full-tree"]
        and SHA40_RE.fullmatch(arguments[4])
    ):
        return
    if (
        len(arguments) == 3
        and arguments[:2] == ["cat-file", "blob"]
        and isinstance(arguments[2], str)
    ):
        tree, separator, relative = arguments[2].partition(":")
        if (
            separator
            and SHA40_RE.fullmatch(tree)
            and (
                _collector_module_path(PurePosixPath(relative)) is not None
                or relative in {
                    execution.CONTAINER_COLLECTOR_RELATIVE,
                    execution.CONTAINER_COLLECTOR_DELEGATE_RELATIVE,
                }
            )
        ):
            return
    raise ComposeInputProducerError("sealed Git command is outside fixed object reads")


def _verify_sealed_release_identity(
    *, release_root: Path, release_sha: str, release_tree_sha: str
) -> None:
    if SHA40_RE.fullmatch(release_sha) is None or SHA40_RE.fullmatch(release_tree_sha) is None:
        raise ComposeInputProducerError("sealed release identity is invalid")
    commit = _sealed_git_output(
        release_root,
        ["rev-parse", "--verify", f"{release_sha}^{{commit}}"],
        label="release commit",
    ).decode("ascii", errors="strict").strip()
    tree = _sealed_git_output(
        release_root,
        ["rev-parse", "--verify", f"{release_sha}^{{tree}}"],
        label="release tree",
    ).decode("ascii", errors="strict").strip()
    if commit != release_sha or tree != release_tree_sha:
        raise ComposeInputProducerError("sealed release commit/tree differs")


def _collector_module_path(path: PurePosixPath) -> tuple[str, bool] | None:
    if (
        path.is_absolute()
        or len(path.parts) < 2
        or path.parts[0] not in {"core", "models"}
        or path.suffix != ".py"
        or any(part in {"", ".", "..", "__pycache__"} for part in path.parts)
    ):
        return None
    parts = path.parts
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in parts[:-1]):
        return None
    stem = path.stem
    if stem == "__init__":
        module_parts = parts[:-1]
        package = True
    elif stem.isidentifier() and not keyword.iskeyword(stem):
        module_parts = (*parts[:-1], stem)
        package = False
    else:
        return None
    if not module_parts:
        return None
    return ".".join(module_parts), package


def _sealed_collector_python_paths(*, release_root: Path, release_tree_sha: str) -> list[str]:
    payload = _sealed_git_output(
        release_root,
        ["ls-tree", "-r", "-z", "--full-tree", release_tree_sha],
        label="tree enumeration",
    )
    paths: list[str] = []
    modules: set[str] = set()
    for item in payload.split(b"\0"):
        if not item:
            continue
        try:
            metadata, raw_path = item.split(b"\t", 1)
            mode, kind, _blob = metadata.decode("ascii").split(" ", 2)
            path = raw_path.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ComposeInputProducerError("sealed Git tree entry is invalid") from exc
        candidate = PurePosixPath(path)
        module = _collector_module_path(candidate)
        explicit_script = path in {
            execution.CONTAINER_COLLECTOR_RELATIVE,
            execution.CONTAINER_COLLECTOR_DELEGATE_RELATIVE,
        }
        in_project_namespace = path.startswith(("core/", "models/"))
        if not in_project_namespace and not explicit_script:
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ComposeInputProducerError("sealed Git collector source is not a regular blob")
        if explicit_script:
            paths.append(path)
            continue
        if module is None:
            raise ComposeInputProducerError("sealed Git collector module path is invalid")
        module_name, _package = module
        if module_name in modules:
            raise ComposeInputProducerError("sealed Git collector module path is ambiguous")
        modules.add(module_name)
        paths.append(path)
    required = {
        execution.CONTAINER_COLLECTOR_RELATIVE,
        execution.CONTAINER_COLLECTOR_DELEGATE_RELATIVE,
        "core/__init__.py",
        "models/__init__.py",
    }
    if not required.issubset(paths):
        raise ComposeInputProducerError("sealed Git collector scope is incomplete")
    if len(paths) > MAX_COLLECTOR_SOURCE_FILES or len(set(paths)) != len(paths):
        raise ComposeInputProducerError("sealed Git collector scope is oversized")
    return sorted(set(paths))


def _sealed_git_blob(*, release_root: Path, release_tree_sha: str, relative_path: str) -> bytes:
    return _sealed_git_output(
        release_root,
        ["cat-file", "blob", f"{release_tree_sha}:{relative_path}"],
        label="blob readback",
    )


def _safe_archive_name(value: Any) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise ComposeInputProducerError("role material archive path is unsafe")
    path = PurePosixPath(value)
    if path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ComposeInputProducerError("role material archive path is unsafe")
    return value


def _inspect_role_material_archive(
    payload: bytes,
    *,
    role: str,
    operation_id: str,
    release_sha: str,
    compose_bytes: bytes,
    environment_bytes: bytes,
    image_ids: Mapping[str, str],
) -> str:
    """Prove staged observer sources are exactly the immutable tar members."""

    expected_names = {
        "final-prepare-manifest.json",
        "role-compose.yml",
        "runtime.env.role",
        "ca.crt",
    }
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for member in archive:
                name = _safe_archive_name(member.name)
                if name in members or not member.isreg() or member.linkname:
                    raise ComposeInputProducerError("role material archive member is unsafe")
                if (
                    member.uid != 0
                    or member.gid != 0
                    or stat.S_IMODE(member.mode) != OUTPUT_MODE
                    or member.mtime != 0
                    or member.pax_headers
                    or not 1 <= member.size <= MAX_INPUT_BYTES
                ):
                    raise ComposeInputProducerError("role material archive member metadata differs")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ComposeInputProducerError("role material archive member is unreadable")
                content = stream.read(member.size + 1)
                if len(content) != member.size:
                    raise ComposeInputProducerError("role material archive member size differs")
                members[name] = content
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise ComposeInputProducerError("role material archive is invalid") from exc
    if set(members) != expected_names:
        raise ComposeInputProducerError("role material archive member set differs")
    try:
        prepare_material.validate_role_archive_bytes(payload, expected_files=members)
    except prepare_material.PrepareMaterialError as exc:
        raise ComposeInputProducerError("role material archive safety differs") from exc
    if members["role-compose.yml"] != compose_bytes or members["runtime.env.role"] != environment_bytes:
        raise ComposeInputProducerError("staged Compose or environment differs from role material archive")
    try:
        manifest = json.loads(members["final-prepare-manifest.json"].decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComposeInputProducerError("role material internal manifest is invalid") from exc
    if not isinstance(manifest, dict) or members["final-prepare-manifest.json"] != _canonical_json(manifest):
        raise ComposeInputProducerError("role material internal manifest is not canonical")
    if (
        manifest.get("operation_id") != operation_id
        or manifest.get("release_sha") != release_sha
        or manifest.get("role") != role
        or manifest.get("runtime_image_ids") != dict(image_ids)
    ):
        raise ComposeInputProducerError("role material internal manifest identity differs")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) != 3:
        raise ComposeInputProducerError("role material internal manifest entries differ")
    expected_entries = {
        name: hashlib.sha256(members[name]).hexdigest()
        for name in ("role-compose.yml", "runtime.env.role", "ca.crt")
    }
    observed_entries: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("archive_path") not in expected_entries:
            raise ComposeInputProducerError("role material internal manifest entry differs")
        name = entry["archive_path"]
        if (
            entry.get("sha256") != expected_entries[name]
            or entry.get("bytes") != len(members[name])
            or entry.get("mode") != "0600"
            or name in observed_entries
        ):
            raise ComposeInputProducerError("role material internal manifest entry differs")
        observed_entries[name] = entry["sha256"]
    if set(observed_entries) != set(expected_entries):
        raise ComposeInputProducerError("role material internal manifest entry coverage differs")
    return hashlib.sha256(
        _canonical_json(
            {
                "archive_sha256": hashlib.sha256(payload).hexdigest(),
                "internal_manifest_sha256": hashlib.sha256(members["final-prepare-manifest.json"]).hexdigest(),
                "role_compose_sha256": expected_entries["role-compose.yml"],
                "role_environment_sha256": expected_entries["runtime.env.role"],
            }
        )
    ).hexdigest()


def _binding_projection(
    document: Mapping[str, Any],
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
    role: str,
) -> dict[str, Any]:
    try:
        binding = runtime_targets.validate_observer_runtime_target_binding(
            document,
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            manifest_sha256=manifest_sha256,
            role=role,
            label="runtime target binding",
        )
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise ComposeInputProducerError("runtime target binding differs") from exc
    projection = {
        key: binding[key]
        for key in (
            "campaign_id",
            "operation_id",
            "release_sha",
            "manifest_sha256",
            "canonical_compose_sha256",
            "role",
            "role_material_sha256",
            "role_runtime_image_ids",
            "binding_sha256",
        )
    }
    return projection


def _validate_installed_target_set(
    payload: bytes,
    *,
    binding: Mapping[str, Any],
    operation_id: str,
    release_sha: str,
    role: str,
) -> None:
    try:
        target_set = runtime_targets.validate_runtime_target_payload_descriptor(
            payload,
            binding["convergence_runtime_targets"],
            operation_id=operation_id,
            release_sha=release_sha,
            canonical_compose_sha256=binding["canonical_compose_sha256"],
            label="installed convergence runtime target set",
        )
    except runtime_targets.ConvergenceRuntimeTargetBindingError as exc:
        raise ComposeInputProducerError("installed convergence runtime target set differs") from exc
    if target_set["roles"].get(role) != binding.get("runtime_target_row"):
        raise ComposeInputProducerError("installed convergence runtime target row differs")


def _execution_service(
    *,
    operation_id: str,
    release_sha: str,
    role: str,
    image_ids: Mapping[str, str],
) -> dict[str, Any]:
    shape = execution.observer_service_shape(role=role)
    release_root = execution.canonical_release_root(
        operation_id=operation_id,
        release_sha=release_sha,
    )
    input_root = execution.canonical_runtime_input_root(operation_id=operation_id, role=role)
    environment_path = execution.canonical_role_environment_path(
        operation_id=operation_id,
        role=role,
    )
    return {
        "image": image_ids["app"],
        "pull_policy": "never",
        "profiles": shape["profiles"],
        "restart": shape["restart"],
        "command": shape["command"],
        "depends_on": {f"{role}_db": {"condition": "service_healthy"}},
        "networks": shape["networks"],
        "volumes": [
            {
                "type": "bind",
                "source": release_root,
                "target": release_root,
                "read_only": True,
                "bind": {"create_host_path": False},
            },
            {
                "type": "bind",
                "source": input_root,
                "target": input_root,
                "read_only": True,
                "bind": {"create_host_path": False},
            },
        ],
        # Compose CLI --env-file performs interpolation only.  This explicit,
        # root-only file is the sole runtime environment injected into the
        # observer container.
        "env_file": [environment_path],
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }


def _immutable_execution_overlay(
    *,
    operation_id: str,
    release_sha: str,
    role: str,
    image_ids: Mapping[str, str],
) -> tuple[dict[str, Any], bytes]:
    shape = execution.observer_service_shape(role=role)
    service = _execution_service(
        operation_id=operation_id,
        release_sha=release_sha,
        role=role,
        image_ids=image_ids,
    )
    document: dict[str, Any] = {
        "services": {
            shape["service"]: service,
            f"{role}_db": {
                "image": image_ids["postgres"],
                "pull_policy": "never",
                "profiles": shape["profiles"],
                "networks": [role],
            },
        },
        "networks": {
            role: {
                "labels": dict(execution.OBSERVER_OPERATION_NETWORK_LABELS),
                "internal": True,
            }
        },
    }
    return document, canonical_role_compose_bytes(document)


def _build_material(*, plan: Mapping[str, Any], archive_inspection_sha256: str) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "production-shadow-convergence-compose-observer-material-v1",
        "campaign_id": plan["campaign_id"],
        "operation_id": plan["operation_id"],
        "release_sha": plan["release_sha"],
        "manifest_sha256": plan["manifest_sha256"],
        "role": plan["role"],
        "runtime_target_binding_sha256": plan["runtime_target_binding_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "role_material_archive_inspection_sha256": archive_inspection_sha256,
        "collector_source_manifest_sha256": plan["collector_source_manifest_sha256"],
        "material_sha256": "0" * 64,
    }
    document["material_sha256"] = hashlib.sha256(
        _canonical_json({key: value for key, value in document.items() if key != "material_sha256"})
    ).hexdigest()
    return document


def _validate_material(value: Mapping[str, Any], *, plan: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "campaign_id", "operation_id", "release_sha", "manifest_sha256",
        "role", "runtime_target_binding_sha256", "plan_sha256",
        "role_material_archive_inspection_sha256", "collector_source_manifest_sha256", "material_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ComposeInputProducerError("Compose execution material fields differ")
    if (
        value.get("schema") != "production-shadow-convergence-compose-observer-material-v1"
        or any(value.get(key) != plan.get(key) for key in (
            "campaign_id", "operation_id", "release_sha", "manifest_sha256", "role",
            "runtime_target_binding_sha256", "plan_sha256", "collector_source_manifest_sha256",
        ))
    ):
        raise ComposeInputProducerError("Compose execution material binding differs")
    expected = hashlib.sha256(
        _canonical_json({key: item for key, item in value.items() if key != "material_sha256"})
    ).hexdigest()
    if value.get("material_sha256") != expected:
        raise ComposeInputProducerError("Compose execution material digest differs")
    try:
        execution._nonzero_sha256(
            value.get("role_material_archive_inspection_sha256"),
            label="role_material_archive_inspection_sha256",
        )
    except execution.ComposeObserverExecutionContractError as exc:
        raise ComposeInputProducerError("Compose execution material archive inspection differs") from exc
    return dict(value)


def _publish_create_only_and_readback(path: Path, payload: bytes, *, label: str, owner_uid: int) -> None:
    """Create once, or accept only an identical prior create-only output."""

    try:
        write_secure_new_bytes(path, payload, label=label, mode=OUTPUT_MODE, max_size=MAX_INPUT_BYTES)
    except SecureFileError as exc:
        # A crash after the first of the two create-only files must be safely
        # recoverable without replacing that file.  Any non-identical existing
        # content remains a hard failure.
        if "already exists" not in str(exc):
            raise ComposeInputProducerError(f"{label} could not be published") from exc
    try:
        observed = read_secure_bytes(path, label=label, owner_uid=owner_uid, max_size=MAX_INPUT_BYTES)
    except SecureFileError as exc:
        raise ComposeInputProducerError(f"{label} could not be read back") from exc
    if observed != payload:
        raise ComposeInputProducerError(f"{label} read-back differs")


def _preflight_output(path: Path, payload: bytes, *, label: str, owner_uid: int) -> None:
    """Reject a collision before any output from this production run is made."""

    if not path.exists():
        return
    try:
        observed = read_secure_bytes(path, label=label, owner_uid=owner_uid, max_size=MAX_INPUT_BYTES)
    except SecureFileError as exc:
        raise ComposeInputProducerError(f"{label} collision is unsafe") from exc
    if observed != payload:
        raise ComposeInputProducerError(f"{label} collision differs")


def _reopen_candidate_anchors(
    anchors: Mapping[Path, str], *, owner_uid: int
) -> None:
    """Close the source-to-immutable-output window before publication."""

    for path, expected_sha256 in anchors.items():
        try:
            payload = read_secure_bytes(
                path,
                label="Compose input candidate anchor",
                owner_uid=owner_uid,
                max_size=MAX_INPUT_BYTES,
            )
        except SecureFileError as exc:
            raise ComposeInputProducerError("Compose input changed before publication") from exc
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ComposeInputProducerError("Compose input changed before publication")


def produce_from_validated_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    owner_uid: int = 0,
    roles: tuple[str, ...] = RUNTIME_ROLES,
) -> dict[str, dict[str, str]]:
    """Create exact plan/material pairs for the three runtime roles only."""

    if set(roles) != set(RUNTIME_ROLES) or len(roles) != len(RUNTIME_ROLES):
        raise ComposeInputProducerError("Compose input production requires exactly three runtime roles")
    projection = _manifest_projection(manifest)
    try:
        manifest_digest = execution._nonzero_sha256(manifest_sha256, label="manifest_sha256")
    except execution.ComposeObserverExecutionContractError as exc:
        raise ComposeInputProducerError("manifest digest is invalid") from exc
    candidates: dict[str, tuple[dict[str, Path], dict[str, Any], dict[str, Any], bytes, bytes, bytes, bytes]] = {}
    candidate_anchors: dict[str, dict[Path, str]] = {}
    source_manifest_candidates: dict[str, bytes] = {}
    for role in roles:
        paths = _role_paths(
            operation_id=projection["operation_id"],
            release_sha=projection["release_sha"],
            role=role,
        )
        source_manifest_payload, source_manifest_sha256 = _collector_source_manifest(
            release_root=paths["release_root"],
            release_sha=projection["release_sha"],
            release_tree_sha=projection["release_tree_sha"],
            owner_uid=owner_uid,
        )
        try:
            source_compose_bytes = read_secure_bytes(paths["source_compose"], label=f"{role} rendered Compose", owner_uid=owner_uid, max_size=MAX_INPUT_BYTES)
            source_environment_bytes = read_secure_bytes(paths["source_environment"], label=f"{role} environment", owner_uid=owner_uid, max_size=MAX_INPUT_BYTES)
            role_material_bytes = read_secure_bytes(
                paths["role_material"],
                label=f"{role} role material",
                owner_uid=owner_uid,
                max_size=MAX_INPUT_BYTES,
            )
            role_material_sha256, _ = sha256_secure_file(paths["role_material"], label=f"{role} role material", owner_uid=owner_uid, max_size=MAX_INPUT_BYTES)
            collector_path = paths["release_root"] / execution.CONTAINER_COLLECTOR_RELATIVE
            collector_bytes = read_secure_bytes(
                collector_path,
                label=f"{role} container collector",
                owner_uid=owner_uid,
                max_size=MAX_INPUT_BYTES,
            )
            collector_delegate_path = (
                paths["release_root"] / execution.CONTAINER_COLLECTOR_DELEGATE_RELATIVE
            )
            collector_delegate_bytes = read_secure_bytes(
                collector_delegate_path,
                label=f"{role} container collector delegate",
                owner_uid=owner_uid,
                max_size=MAX_INPUT_BYTES,
            )
        except SecureFileError as exc:
            raise ComposeInputProducerError(f"{role} input artifact is unavailable") from exc
        collector_sha256 = hashlib.sha256(collector_bytes).hexdigest()
        collector_delegate_sha256 = hashlib.sha256(collector_delegate_bytes).hexdigest()
        _parse_canonical_compose(source_compose_bytes, role=role)
        environment_ids = _environment_image_ids(source_environment_bytes)
        archive_inspection_sha256 = _inspect_role_material_archive(
            role_material_bytes,
            role=role,
            operation_id=projection["operation_id"],
            release_sha=projection["release_sha"],
            compose_bytes=source_compose_bytes,
            environment_bytes=source_environment_bytes,
            image_ids=environment_ids,
        )
        binding_document, _binding_bytes = _read_canonical_json(paths["binding"], label=f"{role} runtime target binding", owner_uid=owner_uid)
        binding = _binding_projection(
            binding_document,
            campaign_id=projection["campaign_id"],
            operation_id=projection["operation_id"],
            release_sha=projection["release_sha"],
            manifest_sha256=manifest_digest,
            role=role,
        )
        try:
            target_set_payload = read_secure_bytes(
                paths["target_set"],
                label=f"{role} convergence runtime target set",
                owner_uid=owner_uid,
                max_size=MAX_INPUT_BYTES,
            )
        except SecureFileError as exc:
            raise ComposeInputProducerError(f"{role} convergence runtime target set is unavailable") from exc
        _validate_installed_target_set(
            target_set_payload,
            binding=binding_document,
            operation_id=projection["operation_id"],
            release_sha=projection["release_sha"],
            role=role,
        )
        manifest_ids = projection["role_runtime_image_ids"][role]
        if (
            role_material_sha256 != projection["role_materials"][role]["sha256"]
            or binding["role_material_sha256"] != role_material_sha256
            or binding["canonical_compose_sha256"] != projection["shadow_compose_sha256"]
            or binding["role_runtime_image_ids"] != manifest_ids
            or environment_ids != manifest_ids
        ):
            raise ComposeInputProducerError(f"{role} material, image, or binding differs")
        canonical_compose, compose_bytes = _immutable_execution_overlay(
            operation_id=projection["operation_id"],
            release_sha=projection["release_sha"],
            role=role,
            image_ids=manifest_ids,
        )
        environment_bytes = _execution_environment(
            source_environment_bytes,
            operation_id=projection["operation_id"],
        )
        try:
            plan = execution.build_execution_plan(
                campaign_id=projection["campaign_id"],
                operation_id=projection["operation_id"],
                release_sha=projection["release_sha"],
                manifest_sha256=manifest_digest,
                canonical_compose_sha256=projection["shadow_compose_sha256"],
                canonical_compose=canonical_compose,
                rendered_observer_service=_execution_service(
                    operation_id=projection["operation_id"],
                    release_sha=projection["release_sha"],
                    role=role,
                    image_ids=manifest_ids,
                ),
                role=role,
                project_name=f"tb3p-{projection['operation_id'].replace('-', '')}-{role.replace('_', '-')}",
                role_compose_path=str(paths["compose"]),
                role_compose_sha256=hashlib.sha256(compose_bytes).hexdigest(),
                role_environment_path=str(paths["environment"]),
                role_environment_sha256=hashlib.sha256(environment_bytes).hexdigest(),
                collector_sha256=collector_sha256,
                collector_delegate_sha256=collector_delegate_sha256,
                collector_source_manifest_sha256=source_manifest_sha256,
                role_material_sha256=role_material_sha256,
                runtime_image_ids=manifest_ids,
                runtime_target_binding=binding,
            )
        except execution.ComposeObserverExecutionContractError as exc:
            raise ComposeInputProducerError(f"{role} Compose execution plan cannot be derived") from exc
        material = _build_material(
            plan=plan,
            archive_inspection_sha256=archive_inspection_sha256,
        )
        plan_payload = _canonical_json(plan)
        material_payload = _canonical_json(material)
        candidates[role] = (
            paths,
            plan,
            material,
            compose_bytes,
            environment_bytes,
            plan_payload,
            material_payload,
        )
        candidate_anchors[role] = {
            paths["source_compose"]: hashlib.sha256(source_compose_bytes).hexdigest(),
            paths["source_environment"]: hashlib.sha256(source_environment_bytes).hexdigest(),
            paths["role_material"]: hashlib.sha256(role_material_bytes).hexdigest(),
            paths["binding"]: hashlib.sha256(_binding_bytes).hexdigest(),
            paths["target_set"]: hashlib.sha256(target_set_payload).hexdigest(),
            collector_path: collector_sha256,
            collector_delegate_path: collector_delegate_sha256,
        }
        source_manifest_candidates[role] = source_manifest_payload

    # All local inputs and both output collision states are checked before the
    # first publication.  The material is published first and the plan second;
    # a plan therefore remains the final readiness marker.  A crash can leave
    # only exact material that a later create-only run verifies before it adds
    # the plan; the worker requires both exact files and cannot execute it.
    for role in roles:
        paths, _plan, _material, compose_bytes, environment_bytes, plan_payload, material_payload = candidates[role]
        _preflight_output(paths["compose"], compose_bytes, label=f"{role} immutable Compose overlay", owner_uid=owner_uid)
        _preflight_output(paths["environment"], environment_bytes, label=f"{role} immutable environment copy", owner_uid=owner_uid)
        _preflight_output(paths["source_manifest"], source_manifest_candidates[role], label=f"{role} collector source manifest", owner_uid=owner_uid)
        _preflight_output(paths["plan"], plan_payload, label=f"{role} Compose execution plan", owner_uid=owner_uid)
        _preflight_output(paths["material"], material_payload, label=f"{role} Compose execution material", owner_uid=owner_uid)
    for role in roles:
        _reopen_candidate_anchors(candidate_anchors[role], owner_uid=owner_uid)
    for role in roles:
        paths, _plan, _material, compose_bytes, environment_bytes, _plan_payload, _material_payload = candidates[role]
        _publish_create_only_and_readback(paths["compose"], compose_bytes, label=f"{role} immutable Compose overlay", owner_uid=owner_uid)
        _publish_create_only_and_readback(paths["environment"], environment_bytes, label=f"{role} immutable environment copy", owner_uid=owner_uid)
        _publish_create_only_and_readback(paths["source_manifest"], source_manifest_candidates[role], label=f"{role} collector source manifest", owner_uid=owner_uid)
    for role in roles:
        paths, _plan, _material, _compose_bytes, _environment_bytes, _plan_payload, material_payload = candidates[role]
        _publish_create_only_and_readback(paths["material"], material_payload, label=f"{role} Compose execution material", owner_uid=owner_uid)
    for role in roles:
        paths, _plan, _material, _compose_bytes, _environment_bytes, plan_payload, _material_payload = candidates[role]
        _publish_create_only_and_readback(paths["plan"], plan_payload, label=f"{role} Compose execution plan", owner_uid=owner_uid)

    result: dict[str, dict[str, str]] = {}
    for role in roles:
        paths, plan, material, _compose_bytes, _environment_bytes, plan_payload, material_payload = candidates[role]
        read_plan, plan_readback = _read_canonical_json(paths["plan"], label=f"{role} Compose execution plan", owner_uid=owner_uid)
        read_material, material_readback = _read_canonical_json(paths["material"], label=f"{role} Compose execution material", owner_uid=owner_uid)
        try:
            execution.validate_execution_plan(read_plan)
        except execution.ComposeObserverExecutionContractError as exc:
            raise ComposeInputProducerError(f"{role} Compose execution plan read-back is invalid") from exc
        _validate_material(read_material, plan=read_plan)
        if plan_readback != plan_payload or material_readback != material_payload:
            raise ComposeInputProducerError(f"{role} Compose execution output read-back differs")
        result[role] = {
            "plan_path": str(paths["plan"]),
            "plan_sha256": plan["plan_sha256"],
            "material_path": str(paths["material"]),
            "material_sha256": material["material_sha256"],
        }
    return result


def produce(manifest_path: Path, *, owner_uid: int = 0) -> dict[str, dict[str, str]]:
    manifest, manifest_sha256 = _read_v4_manifest(manifest_path, owner_uid=owner_uid)
    return produce_from_validated_manifest(
        manifest,
        manifest_sha256=manifest_sha256,
        owner_uid=owner_uid,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = produce(args.manifest)
    except ComposeInputProducerError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": "created", "roles": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
