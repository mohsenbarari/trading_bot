#!/usr/bin/env python3
"""Build one immutable, fully verified frozen-source adoption contract."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    adopt_three_site_staging_frozen_source_and_backup as adoption,
)


SCHEMA = "three-site-staging-frozen-source-adoption-contract-v2"
ROLE_PROJECT = {
    "bot_fi": ("trading_bot_staging", "foreign_app"),
    "webapp_fi": ("trading_bot_staging_iran", "app"),
}
DOCKER = "/usr/bin/docker"
GIT = "/usr/bin/git"
SAFE_ENV = adoption.SAFE_ENV
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContractBuildError(RuntimeError):
    """Fail-closed contract construction error."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _secure_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload, raw = adoption._secure_json(path)
    except Exception as exc:
        raise ContractBuildError(f"{label} is not secure strict JSON") from exc
    return payload, raw


def _secure_bytes(
    path: Path,
    *,
    label: str,
    maximum: int = 10 * 1024 * 1024,
    modes: tuple[int, ...] = (0o600,),
) -> bytes:
    try:
        return adoption._secure_bytes(
            path, maximum=maximum, expected_mode=modes
        )
    except Exception as exc:
        raise ContractBuildError(f"{label} is not a secure snapshot") from exc


def _run(arguments: list[str], *, timeout: int = 60) -> str:
    allowed = (
        arguments[:2] == [DOCKER, "inspect"]
        or arguments[:3] == [DOCKER, "container", "ls"]
        or arguments[:3] == [DOCKER, "volume", "inspect"]
        or arguments[:3] == [DOCKER, "image", "inspect"]
        or arguments[:3] == [GIT, "-C", arguments[2]]
    )
    if not allowed:
        raise ContractBuildError("builder command is outside read-only allowlist")
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractBuildError("required read-only command failed") from exc
    if result.returncode != 0:
        raise ContractBuildError(
            f"read-only command failed closed: {Path(arguments[0]).name}"
        )
    return result.stdout.strip()


def _docker_inspect(identity: str, template: str) -> str:
    return _run([DOCKER, "inspect", "--format", template, identity])


def _import_exact_release_module(
    release_root: Path, module_name: str, relative_path: str
) -> Any:
    expected = (release_root / relative_path).resolve()
    if not expected.is_file():
        raise ContractBuildError(
            f"exact-release module is unavailable: {module_name}"
        )
    root_text = str(release_root)
    if not sys.path or sys.path[0] != root_text:
        sys.path.insert(0, root_text)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ContractBuildError(
            f"cannot import exact-release module: {module_name}"
        ) from exc
    module_file = getattr(module, "__file__", None)
    if (
        not isinstance(module_file, str)
        or Path(module_file).resolve() != expected
    ):
        raise ContractBuildError(
            f"ambient module shadowing rejected: {module_name}"
        )
    return module


def _policy_reference(
    policy: dict[str, Any], raw: bytes
) -> tuple[str, str, str]:
    issuer = policy.get("issuer")
    actions = policy.get("actions")
    direct_actions = (
        [
            row
            for row in actions
            if isinstance(row, dict)
            and row.get("action") == adoption.SOURCE_ADOPTION_ACTION
        ]
        if isinstance(actions, list)
        else []
    )
    if not isinstance(issuer, dict) or not isinstance(
        issuer.get("public_key"), str
    ) or (
        len(direct_actions) != 1
        or direct_actions[0].get("environments") != ["staging"]
        or type(direct_actions[0].get("max_ttl_seconds")) is not int
        or not (
            adoption.MIN_APPROVAL_REMAINING_SECONDS
            <= direct_actions[0]["max_ttl_seconds"]
            <= 3600
        )
    ):
        raise ContractBuildError(
            "approval policy lacks the bounded direct source-adoption action"
        )
    try:
        public_key = base64.b64decode(issuer["public_key"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ContractBuildError("approval policy public key is malformed") from exc
    if len(public_key) != 32:
        raise ContractBuildError("approval policy public key is not Ed25519")
    return _sha256(raw), _sha256(_canonical_bytes(policy)), _sha256(public_key)


def _verify_release(release_root: Path, release_sha: str) -> None:
    expected = Path("/srv/trading-bot-three-site/releases") / release_sha
    if release_root != expected:
        raise ContractBuildError("release root is not the immutable release path")
    if _run([GIT, "-C", str(release_root), "rev-parse", "HEAD"]) != release_sha:
        raise ContractBuildError("release checkout HEAD differs from inventory")
    if _run(
        [
            GIT,
            "-C",
            str(release_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ]
    ):
        raise ContractBuildError("release checkout is dirty")


def _verify_local_image(image_id: str, *, label: str) -> None:
    if (
        IMAGE_ID_RE.fullmatch(image_id) is None
        or _run(
            [DOCKER, "image", "inspect", "--format", "{{.Id}}", image_id]
        )
        != image_id
    ):
        raise ContractBuildError(f"{label} exact image is unavailable")


def _image_command_identity(image_id: str) -> dict[str, Any]:
    try:
        entrypoint = json.loads(
            _run(
                [
                    DOCKER,
                    "image",
                    "inspect",
                    "--format",
                    "{{json .Config.Entrypoint}}",
                    image_id,
                ]
            )
        )
        command = json.loads(
            _run(
                [
                    DOCKER,
                    "image",
                    "inspect",
                    "--format",
                    "{{json .Config.Cmd}}",
                    image_id,
                ]
            )
        )
        normalized_entrypoint = adoption._command_vector(
            entrypoint, label="scratch image entrypoint"
        )
        normalized_command = adoption._command_vector(
            command, label="scratch image command"
        )
    except (json.JSONDecodeError, adoption.AdoptionError) as exc:
        raise ContractBuildError(
            "scratch image command identity is invalid"
        ) from exc
    return {
        "id": image_id,
        "entrypoint": (
            list(normalized_entrypoint)
            if normalized_entrypoint is not None
            else None
        ),
        "cmd": (
            list(normalized_command)
            if normalized_command is not None
            else None
        ),
    }


def _observe_source_volumes(
    *,
    project: str,
    app_service: str,
    app_image_id: str,
) -> dict[str, dict[str, str]]:
    raw_ids = _run(
        [
            DOCKER,
            "container",
            "ls",
            "--no-trunc",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={app_service}",
        ]
    )
    ids = [value for value in raw_ids.splitlines() if value]
    if len(ids) != 1 or re.fullmatch(r"[0-9a-f]{64}", ids[0]) is None:
        raise ContractBuildError("source application container is ambiguous")
    container_id = ids[0]
    if (
        _docker_inspect(container_id, "{{.Image}}") != app_image_id
        or _docker_inspect(
            container_id,
            '{{index .Config.Labels "com.docker.compose.project"}}',
        )
        != project
        or _docker_inspect(
            container_id,
            '{{index .Config.Labels "com.docker.compose.service"}}',
        )
        != app_service
    ):
        raise ContractBuildError(
            "source application image/project identity differs"
        )
    try:
        mounts = json.loads(
            _docker_inspect(container_id, "{{json .Mounts}}"),
            object_pairs_hook=adoption._strict_object,
        )
    except (json.JSONDecodeError, adoption.AdoptionError) as exc:
        raise ContractBuildError("source volume inventory is unreadable") from exc
    result: dict[str, dict[str, str]] = {}
    for destination in ("/app/uploads", "/app/audit_trail"):
        matching = [
            row
            for row in mounts
            if isinstance(row, dict)
            and row.get("Destination") == destination
            and row.get("Type") == "volume"
        ]
        if (
            len(matching) != 1
            or matching[0].get("RW") is not True
            or adoption.VOLUME_NAME_RE.fullmatch(
                str(matching[0].get("Name", ""))
            )
            is None
        ):
            raise ContractBuildError(
                f"source volume is not exact: {destination}"
            )
        name = str(matching[0]["Name"])
        logical = _run(
            [
                DOCKER,
                "volume",
                "inspect",
                "--format",
                '{{index .Labels "com.docker.compose.volume"}}',
                name,
            ]
        )
        volume_project = _run(
            [
                DOCKER,
                "volume",
                "inspect",
                "--format",
                '{{index .Labels "com.docker.compose.project"}}',
                name,
            ]
        )
        if (
            volume_project != project
            or adoption.SAFE_NAME_RE.fullmatch(logical) is None
        ):
            raise ContractBuildError("source volume Compose labels differ")
        result[destination] = {
            "name": name,
            "compose_volume": logical,
        }
    return result


def _verify_published_file(
    path: Path, raw: bytes, *, mode: int
) -> None:
    observed = _secure_bytes(
        path,
        label=f"published snapshot {path.name}",
        maximum=max(len(raw), 1),
        modes=(mode,),
    )
    if observed != raw:
        raise ContractBuildError(
            f"published snapshot bytes differ: {path.name}"
        )


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a complete inode without replacing an existing path."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ContractBuildError(
            "atomic no-replace publication is unavailable"
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(
            error_number, os.strerror(error_number), str(destination)
        )
    raise OSError(
        error_number, os.strerror(error_number), str(destination)
    )


def _write_new(
    path: Path,
    raw: bytes,
    *,
    mode: int = 0o600,
    reuse_exact: bool = False,
) -> bool:
    """Publish atomically; reuse only explicitly immutable exact snapshots."""
    if path.exists() or path.is_symlink():
        if not reuse_exact:
            raise FileExistsError(
                errno.EEXIST, os.strerror(errno.EEXIST), str(path)
            )
        _verify_published_file(path, raw, mode=mode)
        return False

    temporary = path.parent / (
        f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    published = False
    temporary_created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        temporary_created = True
        try:
            os.fchmod(descriptor, mode)
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise ContractBuildError(
                        "short contract snapshot write"
                    )
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        metadata = temporary.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size != len(raw)
        ):
            raise ContractBuildError(
                "temporary snapshot verification failed"
            )
        _verify_published_file(temporary, raw, mode=mode)
        try:
            _rename_noreplace(temporary, path)
            published = True
        except FileExistsError:
            if not reuse_exact:
                raise
            _verify_published_file(path, raw, mode=mode)
            return False
    finally:
        if temporary_created and not published:
            temporary.unlink(missing_ok=True)
    parent_fd = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    _verify_published_file(path, raw, mode=mode)
    return True


def _verify_inventory(
    *,
    inventory: dict[str, Any],
    inventory_approval: dict[str, Any],
    policy: dict[str, Any],
    release_root: Path,
) -> dict[str, Any]:
    try:
        verifier = _import_exact_release_module(
            release_root,
            "scripts.verify_three_site_staging_inventory",
            "scripts/verify_three_site_staging_inventory.py",
        )
        verified = verifier.verify_approved_inventory(
            inventory,
            approval=inventory_approval,
            approval_policy=policy,
            host_destructive=inventory.get("host_safety_mode")
            == "dedicated-host-destructive",
            require_fresh_approval=False,
        )
    except Exception as exc:
        raise ContractBuildError(
            "provisioned inventory approval is invalid"
        ) from exc
    if verified.get("inventory_stage") != "provisioned":
        raise ContractBuildError("inventory is not provisioned")
    return verified


def build_contract(args: argparse.Namespace) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise ContractBuildError("contract builder must run as root")
    if re.fullmatch(r"[0-9a-f]{16}", args.run_id) is None:
        raise ContractBuildError("run-id must be exactly 16 lowercase hex")
    project, app_service = ROLE_PROJECT[args.source_role]
    inventory, inventory_raw = _secure_json(
        args.inventory, label="provisioned inventory"
    )
    inventory_approval, inventory_approval_raw = _secure_json(
        args.inventory_approval, label="inventory approval"
    )
    policy, policy_raw = _secure_json(
        args.approval_policy, label="approval policy"
    )
    release_sha = str(inventory.get("release_sha", ""))
    if re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise ContractBuildError("inventory release SHA is malformed")
    _verify_release(args.release_root, release_sha)
    verified_inventory = _verify_inventory(
        inventory=inventory,
        inventory_approval=inventory_approval,
        policy=policy,
        release_root=args.release_root,
    )
    if args.source_role not in {
        str(row.get("role")) for row in inventory.get("roles", [])
        if isinstance(row, dict)
    }:
        raise ContractBuildError("source role is absent from inventory")

    campaign_root = args.inventory.parent
    secure_root = Path("/root/secure-envs/trading-bot")
    if (
        campaign_root.parent != secure_root
        or not campaign_root.name.startswith("three-site-staging-")
        or args.inventory_approval.parent != campaign_root
        or args.approval_policy.parent != campaign_root
        or args.output_directory.parent != campaign_root
    ):
        raise ContractBuildError("campaign inputs escape the secure root")
    role_slug = args.source_role.replace("_", "-")
    if args.output_directory.name != (
        f"source-adoption-output-{role_slug}-{args.run_id}"
    ):
        raise ContractBuildError("output directory is not run-bound")
    subject_path = campaign_root / (
        f"source-adoption-subject-{role_slug}-{args.run_id}.json"
    )
    action_approval_path = campaign_root / (
        f"source-adoption-approval-{role_slug}-{args.run_id}.json"
    )

    inventory_raw_sha = _sha256(inventory_raw)
    inventory_sha = _sha256(_canonical_bytes(inventory))
    approval_raw_sha = _sha256(inventory_approval_raw)
    policy_raw_sha, policy_sha, public_key_sha = _policy_reference(
        policy, policy_raw
    )
    if (
        verified_inventory.get("inventory_sha256") != inventory_sha
        or verified_inventory.get("approval_token_sha256")
        != _sha256(_canonical_bytes(inventory_approval))
    ):
        raise ContractBuildError("inventory verifier digest binding differs")

    inventory_snapshot = campaign_root / (
        f"provisioned-inventory-snapshot-{inventory_raw_sha}.json"
    )
    approval_snapshot = campaign_root / (
        f"provisioned-inventory-approval-{role_slug}-{approval_raw_sha}.json"
    )
    policy_snapshot = campaign_root / (
        f"human-approval-policy-snapshot-{policy_raw_sha}.json"
    )
    helper_source = Path(adoption.__file__).resolve()
    helper_raw = _secure_bytes(
        helper_source,
        label="audited imported source-adoption helper",
        modes=(0o700, 0o755),
    )
    helper_sha = _sha256(helper_raw)
    helper_path = campaign_root / (
        f"adopt-three-site-frozen-source-{helper_sha}.py"
    )
    env_raw = _secure_bytes(args.env_file, label="source environment")

    restore, restore_raw = _secure_json(
        args.historical_restore_evidence,
        label="historical restore evidence",
    )
    adopted, adopted_raw = _secure_json(
        args.historical_adopted_freeze_evidence,
        label="historical adopted freeze evidence",
    )
    freeze, freeze_raw = _secure_json(
        args.historical_freeze_evidence,
        label="historical freeze evidence",
    )
    bundle_reference = freeze.get("legacy_restore_bundle")
    if (
        not isinstance(bundle_reference, dict)
        or not isinstance(bundle_reference.get("path"), str)
    ):
        raise ContractBuildError("historical rollback reference is missing")
    bundle_path = Path(bundle_reference["path"])
    bundle, bundle_raw = _secure_json(
        bundle_path, label="historical rollback bundle"
    )
    compose = bundle.get("compose")
    if not isinstance(compose, dict) or not isinstance(
        compose.get("path"), str
    ):
        raise ContractBuildError("historical Compose reference is missing")
    compose_path = Path(compose["path"])
    compose_raw = _secure_bytes(
        compose_path, label="historical Compose"
    )
    source_rows = freeze.get("source_roles")
    service_images = bundle.get("service_images")
    if (
        restore.get("campaign_id") != freeze.get("campaign_id")
        or restore.get("release_sha") != freeze.get("target_release_sha")
        or not isinstance(source_rows, list)
        or source_rows
        != [
            {
                "source_role": args.source_role,
                "app_service": app_service,
                "source_release_sha": source_rows[0].get(
                    "source_release_sha"
                )
                if source_rows and isinstance(source_rows[0], dict)
                else None,
            }
        ]
        or not isinstance(service_images, dict)
        or set(service_images) != {"db", "redis", app_service}
        or restore.get("service_images") != service_images
        or any(
            IMAGE_ID_RE.fullmatch(str(value)) is None
            for value in service_images.values()
        )
    ):
        raise ContractBuildError("historical identity chain is inconsistent")
    source_release_sha = str(source_rows[0]["source_release_sha"])
    if re.fullmatch(r"[0-9a-f]{40}", source_release_sha) is None:
        raise ContractBuildError("historical source release is malformed")
    if (
        args.historical_restore_evidence.parent
        != args.historical_evidence_root
        and not args.historical_restore_evidence.is_relative_to(
            args.historical_evidence_root
        )
    ):
        raise ContractBuildError("historical evidence root is inconsistent")
    if (
        not args.historical_adopted_freeze_evidence.is_relative_to(
            args.historical_evidence_root
        )
        or not args.historical_freeze_evidence.is_relative_to(
            args.historical_evidence_root
        )
        or not bundle_path.is_relative_to(args.rollback_storage_root)
        or not compose_path.is_relative_to(args.rollback_storage_root)
    ):
        raise ContractBuildError("historical artifacts escape declared roots")
    if (
        _sha256(bundle_raw) != str(bundle_reference.get("sha256"))
        or len(bundle_raw) != bundle_reference.get("size")
        or _sha256(compose_raw) != str(compose.get("sha256"))
        or len(compose_raw) != compose.get("size")
    ):
        raise ContractBuildError("historical rollback bytes differ")

    for service, image_id in sorted(service_images.items()):
        _verify_local_image(str(image_id), label=f"historical {service}")
    _verify_local_image(
        args.scratch_postgres_image_id, label="scratch PostgreSQL"
    )
    scratch_image = _image_command_identity(
        args.scratch_postgres_image_id
    )
    source_volumes = _observe_source_volumes(
        project=project,
        app_service=app_service,
        app_image_id=str(service_images[app_service]),
    )

    payload = {
        "schema": SCHEMA,
        "current": {
            "campaign_id": verified_inventory["campaign_id"],
            "release_sha": release_sha,
            "deployment_id": verified_inventory["deployment_id"],
            "host_safety_mode": verified_inventory["host_safety_mode"],
            "campaign_root": str(campaign_root),
            "release_root": str(args.release_root),
            "inventory": {
                "path": str(inventory_snapshot),
                "raw_sha256": inventory_raw_sha,
                "canonical_sha256": inventory_sha,
            },
            "approval_policy": {
                "path": str(policy_snapshot),
                "raw_sha256": policy_raw_sha,
                "canonical_sha256": policy_sha,
                "public_key_sha256": public_key_sha,
            },
            "scratch_postgres_image": scratch_image,
            "helper": {
                "path": str(helper_path),
                "sha256": helper_sha,
            },
        },
        "historical": {
            "campaign_id": restore["campaign_id"],
            "target_release_sha": restore["release_sha"],
            "source_release_sha": source_release_sha,
            "evidence_root": str(args.historical_evidence_root),
            "rollback_storage_root": str(args.rollback_storage_root),
        },
        "roles": {
            args.source_role: {
                "project_name": project,
                "app_service": app_service,
                "env_file": str(args.env_file),
                "env_sha256": _sha256(env_raw),
                "inventory_approval_path": str(approval_snapshot),
                "expected_approval_id": verified_inventory["approval_id"],
                "expected_approval_token_sha256": verified_inventory[
                    "approval_token_sha256"
                ],
                "expected_approval_raw_sha256": approval_raw_sha,
                "source_adoption_subject_path": str(subject_path),
                "source_adoption_approval_path": str(
                    action_approval_path
                ),
                "output_dir": str(args.output_directory),
                "run_id": args.run_id,
                "restore_evidence": {
                    "path": str(args.historical_restore_evidence),
                    "sha256": _sha256(restore_raw),
                },
                "adopted_freeze_evidence": {
                    "path": str(args.historical_adopted_freeze_evidence),
                    "sha256": _sha256(adopted_raw),
                },
                "freeze_evidence": {
                    "path": str(args.historical_freeze_evidence),
                    "sha256": _sha256(freeze_raw),
                },
                "restore_bundle": {
                    "path": str(bundle_path),
                    "sha256": _sha256(bundle_raw),
                },
                "compose": {
                    "path": str(compose_path),
                    "sha256": _sha256(compose_raw),
                },
                "service_images": dict(sorted(service_images.items())),
                "source_volumes": source_volumes,
            }
        },
    }
    contract_raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    contract_sha = _sha256(contract_raw)
    contract_path = campaign_root / (
        f"source-adoption-contract-{contract_sha}.json"
    )
    if args.output is not None and args.output != contract_path:
        raise ContractBuildError(
            f"output must equal content-addressed path: {contract_path}"
        )

    adoption._install_adoption_contract(
        path=contract_path, payload=payload, raw=contract_raw
    )
    contract = adoption.CONTRACTS[args.source_role]
    adoption._validate_historical_chain(
        contract=contract,
        adopted_freeze_path=args.historical_adopted_freeze_evidence,
        restore_evidence_path=args.historical_restore_evidence,
        freeze_evidence_path=args.historical_freeze_evidence,
        restore_bundle_path=bundle_path,
    )
    snapshot = adoption._project_snapshot(contract, service_images)
    if snapshot["app_source_volumes"] != {
        destination: value["name"]
        for destination, value in source_volumes.items()
    }:
        raise ContractBuildError(
            "source volume identities changed during contract validation"
        )

    for path in (
        subject_path,
        action_approval_path,
        args.output_directory,
        contract_path,
    ):
        if path.exists() or path.is_symlink():
            raise ContractBuildError(
                f"contract output already exists: {path.name}"
            )
    reusable_snapshots = (
        (inventory_snapshot, inventory_raw, 0o600),
        (approval_snapshot, inventory_approval_raw, 0o600),
        (policy_snapshot, policy_raw, 0o600),
        (helper_path, helper_raw, 0o700),
    )
    for path, raw, mode in reusable_snapshots:
        if path.exists() or path.is_symlink():
            _verify_published_file(path, raw, mode=mode)
    for path, raw, mode in reusable_snapshots:
        _write_new(path, raw, mode=mode, reuse_exact=True)
    _write_new(contract_path, contract_raw)
    return {
        "status": "source-adoption-contract-ready",
        "source_role": args.source_role,
        "campaign_id": verified_inventory["campaign_id"],
        "release_sha": release_sha,
        "run_id": args.run_id,
        "contract": {
            "path": str(contract_path),
            "sha256": contract_sha,
        },
        "snapshots": {
            "inventory": str(inventory_snapshot),
            "inventory_approval": str(approval_snapshot),
            "approval_policy": str(policy_snapshot),
            "source_adoption_helper": str(helper_path),
        },
        "future_outputs": {
            "approval_subject": str(subject_path),
            "source_adoption_approval": str(action_approval_path),
            "backup_output_directory": str(args.output_directory),
        },
        "docker_inspection_read_only": True,
        "docker_mutation": False,
        "file_overwrite": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-role", choices=sorted(ROLE_PROJECT), required=True
    )
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument(
        "--historical-restore-evidence", type=Path, required=True
    )
    parser.add_argument(
        "--historical-adopted-freeze-evidence",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--historical-freeze-evidence", type=Path, required=True
    )
    parser.add_argument(
        "--historical-evidence-root", type=Path, required=True
    )
    parser.add_argument(
        "--rollback-storage-root", type=Path, required=True
    )
    parser.add_argument(
        "--scratch-postgres-image-id", required=True
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        result = build_contract(_parser().parse_args(argv))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc)[:400],
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
