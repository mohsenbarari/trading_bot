#!/usr/bin/env python3
"""Install immutable FI production-shadow precommit inputs on their hosts.

The default invocation is a read-only controller plan.  Apply mode requires
one operation-bound confirmation, copies only the six reviewed inputs for
each FI role, and invokes this exact release-bound module on the destination
host.  The host subcommand publishes the operation-scoped host-agent contract
and delegates final installation to the release-bound input installer.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    sha256_secure_file,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from scripts import (  # noqa: E402
    install_production_shadow_precommit_inputs as INSTALLER,
)
from scripts import production_shadow_cutover_controller as CUTOVER  # noqa: E402
from scripts import production_shadow_host_agent as HOST_AGENT  # noqa: E402
from scripts import production_shadow_precommit_worker as WORKER  # noqa: E402


ROOT_UID = 0
ROOT_GID = 0
FILE_MODE = 0o600
DIRECTORY_MODE = 0o700
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
PYTHON3 = "/usr/bin/python3"
SSH = "/usr/bin/ssh"
SCP = "/usr/bin/scp"
GIT = "/usr/bin/git"
WEBAPP_FI_PORT = 37067
ROLE_ORDER = ("bot_fi", "webapp_fi")
ROLE_FILENAMES = {
    "precommit_manifest": "precommit-operation.json",
    "role_material": {
        "bot_fi": "role-material-bot-fi.tar",
        "webapp_fi": "role-material-webapp-fi.tar",
    },
    "source_snapshot_manifest": "source-snapshot-manifest.json",
    "database": "database.dump",
    "uploads": "uploads.tar.gz",
    "audit": "audit.tar.gz",
}
SCRIPT_RELATIVE = Path(
    "scripts/orchestrate_production_shadow_precommit_inputs.py"
)
INSTALLER_RELATIVE = Path(
    "scripts/install_production_shadow_precommit_inputs.py"
)
HOST_AGENT_RELATIVE = Path("scripts/production_shadow_host_agent.py")
SECURE_ROOT = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
PLAN_SCHEMA = "production-shadow-precommit-input-orchestration-plan-v1"
HOST_REQUEST_SCHEMA = "production-shadow-precommit-input-host-request-v1"
HOST_RESULT_SCHEMA = "production-shadow-precommit-input-host-result-v1"
ATTESTATION_SCHEMA = (
    "production-shadow-precommit-input-readback-attestation-v1"
)
JOURNAL_SCHEMA = "production-shadow-precommit-input-orchestration-journal-v1"
EVIDENCE_SCHEMA = "production-shadow-precommit-input-orchestration-evidence-v1"
CONFIRMATION_PREFIX = "INSTALL-PRODUCTION-SHADOW-PRECOMMIT-INPUTS"
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:@=+-]+$")
BASE64_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
SAFE_GIT_ENV = {
    **SAFE_ENV,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}
INPUT_ROW_FIELDS = frozenset({"filename", "sha256", "bytes"})
HOST_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "action",
        "role",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest",
        "controller_manifest_sha256",
        "inputs",
    }
)


class PrecommitInputOrchestrationError(RuntimeError):
    """Raised when controller or host orchestration fails closed."""


@dataclass(frozen=True)
class InputFile:
    key: str
    filename: str
    path: Path
    sha256: str
    bytes: int
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    links: int
    mtime_ns: int
    ctime_ns: int

    def public_row(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "bytes": self.bytes,
        }

    def physical_row(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
            "uid": self.uid,
            "gid": self.gid,
            "links": self.links,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
        }


@dataclass(frozen=True)
class RoleClosure:
    role: str
    manifest: WORKER.PrecommitManifest
    inputs: Mapping[str, InputFile]
    installed_bindings: Mapping[str, Mapping[str, Any]]

    def public_inventory(self) -> dict[str, Any]:
        return {
            key: self.inputs[key].public_row()
            for key in sorted(self.inputs)
        }


@dataclass(frozen=True)
class ControllerClosure:
    manifest: Mapping[str, Any]
    manifest_sha256: str
    roles: Mapping[str, RoleClosure]
    closure_sha256: str


Runner = Callable[
    [Sequence[str], int, Mapping[str, str]],
    subprocess.CompletedProcess[bytes],
]


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
        raise PrecommitInputOrchestrationError(
            "orchestration document is not canonical JSON data"
        ) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PrecommitInputOrchestrationError(
                "orchestration JSON contains a duplicate key"
            )
        result[key] = value
    return result


def _decode_json(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except PrecommitInputOrchestrationError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrecommitInputOrchestrationError(
            f"{label} is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise PrecommitInputOrchestrationError(
            f"{label} must be a JSON object"
        )
    return value


def _canonical_absolute(path: Path, *, label: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path != Path(os.path.abspath(os.fspath(path)))
        or path.name in {"", ".", ".."}
    ):
        raise PrecommitInputOrchestrationError(
            f"{label} must be an absolute canonical path"
        )
    return path


def _assert_root_file_metadata(path: Path, *, label: str) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrecommitInputOrchestrationError(
            f"{label} is unavailable or unsafe"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) != FILE_MODE
        or metadata.st_nlink != 1
    ):
        raise PrecommitInputOrchestrationError(
            f"{label} metadata differs"
        )


def _read_controller_manifest(path: Path) -> tuple[dict[str, Any], str]:
    path = _canonical_absolute(path, label="controller manifest")
    try:
        manifest, observed_sha256 = CUTOVER.read_root_only_manifest(path)
        payload = read_secure_bytes(
            path,
            label="controller manifest",
            owner_uid=ROOT_UID,
            max_size=MAX_JSON_BYTES,
        )
    except (CUTOVER.CutoverContractError, SecureFileError) as exc:
        raise PrecommitInputOrchestrationError(
            "controller manifest is unavailable or invalid"
        ) from exc
    canonical = _canonical_json(manifest)
    if payload != canonical:
        raise PrecommitInputOrchestrationError(
            "controller manifest bytes are not canonical"
        )
    canonical_sha256 = hashlib.sha256(canonical).hexdigest()
    if observed_sha256 != canonical_sha256:
        raise PrecommitInputOrchestrationError(
            "controller manifest digest is unstable"
        )
    return manifest, canonical_sha256


def _validate_role_controller_binding(
    *,
    controller: Mapping[str, Any],
    controller_sha256: str,
    role: str,
    manifest: WORKER.PrecommitManifest,
) -> None:
    artifacts = controller["artifacts"]
    material = artifacts["role_materials"][role]
    if (
        manifest.operation_id != controller["operation_id"]
        or manifest.release_sha != controller["release_sha"]
        or manifest.release_tree_sha != controller["release_tree_sha"]
        or manifest.controller_manifest_sha256 != controller_sha256
        or manifest.approval_sha256 != artifacts["cutover_approval_sha256"]
        or manifest.role_material_sha256 != material["sha256"]
        or manifest.artifacts["role-material"].bytes != material["bytes"]
        or manifest.canonical_compose_sha256
        != artifacts["shadow_compose_sha256"]
        or manifest.postgres_runtime_uid != artifacts["postgres_runtime_uid"]
        or manifest.postgres_runtime_gid != artifacts["postgres_runtime_gid"]
        or dict(manifest.runtime_image_ids)
        != artifacts["role_runtime_image_ids"][role]
    ):
        raise PrecommitInputOrchestrationError(
            f"{role} precommit manifest differs from the controller"
        )
    release = manifest.artifacts["release-bundle"]
    if (
        release.sha256 != artifacts["release_bundle_sha256"]
        or release.bytes != artifacts["release_bundle_bytes"]
    ):
        raise PrecommitInputOrchestrationError(
            f"{role} release bundle binding differs from the controller"
        )
    for kind in sorted(WORKER.IMAGE_FIELDS):
        actual = manifest.image_artifacts[kind]
        expected = artifacts["image_artifacts"][kind]
        if (
            actual.archive_sha256 != expected["archive_sha256"]
            or actual.archive_bytes != expected["archive_bytes"]
            or actual.config_digest != expected["config_digest"]
            or actual.content_identity != expected["content_identity"]
            or dict(actual.content_descriptor)
            != expected["content_descriptor"]
        ):
            raise PrecommitInputOrchestrationError(
                f"{role} {kind} image binding differs from the controller"
            )


def _load_role_closure(
    *,
    controller: Mapping[str, Any],
    controller_sha256: str,
    role: str,
    precommit_manifest: Path,
    role_material: Path,
    source_snapshot_manifest: Path,
) -> RoleClosure:
    try:
        (
            _precommit_document,
            _precommit_payload,
            manifest,
            precommit_identity,
        ) = INSTALLER._load_precommit_manifest_source(  # noqa: SLF001
            _canonical_absolute(
                precommit_manifest,
                label=f"{role} precommit manifest",
            ),
            expected_role=role,
        )
        paths = WORKER.operation_paths(
            manifest.operation_id,
            manifest.release_sha,
            role,
        )
        (
            _role_payload,
            role_identity,
            role_members,
        ) = INSTALLER._load_role_material(  # noqa: SLF001
            _canonical_absolute(
                role_material,
                label=f"{role} role material",
            ),
            manifest=manifest,
            paths=paths,
        )
        (
            source_document,
            source_identity,
            source_artifacts,
        ) = INSTALLER._load_source_snapshot(  # noqa: SLF001
            _canonical_absolute(
                source_snapshot_manifest,
                label=f"{role} source snapshot manifest",
            ),
            manifest=manifest,
        )
    except (
        INSTALLER.PrecommitInputInstallError,
        WORKER.PrecommitWorkerError,
    ) as exc:
        raise PrecommitInputOrchestrationError(
            f"{role} precommit input closure is invalid"
        ) from exc
    _validate_role_controller_binding(
        controller=controller,
        controller_sha256=controller_sha256,
        role=role,
        manifest=manifest,
    )
    if source_document["legacy_release_sha"] != controller["legacy_release_sha"]:
        raise PrecommitInputOrchestrationError(
            f"{role} source snapshot legacy release differs from the controller"
        )
    material_binding = manifest.artifacts["role-material"]
    def controller_input(
        *,
        key: str,
        filename: str,
        identity: INSTALLER.FileIdentity,
    ) -> InputFile:
        metadata = identity.path.stat(follow_symlinks=False)
        return InputFile(
            key=key,
            filename=filename,
            path=identity.path,
            sha256=identity.sha256,
            bytes=identity.bytes,
            device=identity.device,
            inode=identity.inode,
            mode=metadata.st_mode,
            uid=metadata.st_uid,
            gid=metadata.st_gid,
            links=metadata.st_nlink,
            mtime_ns=metadata.st_mtime_ns,
            ctime_ns=metadata.st_ctime_ns,
        )

    rows = {
        "precommit_manifest": controller_input(
            key="precommit_manifest",
            filename=ROLE_FILENAMES["precommit_manifest"],
            identity=precommit_identity,
        ),
        "role_material": controller_input(
            key="role_material",
            filename=ROLE_FILENAMES["role_material"][role],
            identity=role_identity,
        ),
        "source_snapshot_manifest": controller_input(
            key="source_snapshot_manifest",
            filename=ROLE_FILENAMES["source_snapshot_manifest"],
            identity=source_identity,
        ),
    }
    if (
        role_identity.sha256 != material_binding.sha256
        or role_identity.bytes != material_binding.bytes
    ):
        raise PrecommitInputOrchestrationError(
            f"{role} role material differs from the precommit manifest"
        )
    source_keys = {
        "database": "database-backup",
        "uploads": "uploads-archive",
        "audit": "audit-archive",
    }
    for key, kind in source_keys.items():
        artifact = source_artifacts[kind]
        rows[key] = controller_input(
            key=key,
            filename=ROLE_FILENAMES[key],
            identity=artifact.identity,
        )
    return RoleClosure(
        role=role,
        manifest=manifest,
        inputs=rows,
        installed_bindings={
            "role_compose": {
                "sha256": hashlib.sha256(
                    role_members["role-compose.yml"]
                ).hexdigest(),
                "bytes": len(role_members["role-compose.yml"]),
            },
            "runtime_environment": {
                "sha256": hashlib.sha256(
                    role_members["runtime.env.role"]
                ).hexdigest(),
                "bytes": len(role_members["runtime.env.role"]),
            },
            "ca_certificate": {
                "sha256": hashlib.sha256(
                    role_members["ca.crt"]
                ).hexdigest(),
                "bytes": len(role_members["ca.crt"]),
            },
        },
    )


def load_controller_closure(
    *,
    controller_manifest: Path,
    bot_precommit_manifest: Path,
    webapp_precommit_manifest: Path,
    bot_role_material: Path,
    webapp_role_material: Path,
    bot_source_snapshot_manifest: Path,
    webapp_source_snapshot_manifest: Path,
) -> ControllerClosure:
    controller, controller_sha256 = _read_controller_manifest(
        controller_manifest
    )
    role_arguments = {
        "bot_fi": (
            bot_precommit_manifest,
            bot_role_material,
            bot_source_snapshot_manifest,
        ),
        "webapp_fi": (
            webapp_precommit_manifest,
            webapp_role_material,
            webapp_source_snapshot_manifest,
        ),
    }
    roles = {
        role: _load_role_closure(
            controller=controller,
            controller_sha256=controller_sha256,
            role=role,
            precommit_manifest=role_arguments[role][0],
            role_material=role_arguments[role][1],
            source_snapshot_manifest=role_arguments[role][2],
        )
        for role in ROLE_ORDER
    }
    physical = [
        (item.device, item.inode)
        for role in ROLE_ORDER
        for item in roles[role].inputs.values()
    ]
    if len(physical) != len(set(physical)):
        raise PrecommitInputOrchestrationError(
            "controller inputs must be physically distinct regular files"
        )
    closure_document = {
        "controller_manifest_sha256": controller_sha256,
        "operation_id": controller["operation_id"],
        "release_sha": controller["release_sha"],
        "roles": {
            role: {
                "inventory": roles[role].public_inventory(),
                "physical_identity_sha256": hashlib.sha256(
                    _canonical_json(
                        {
                            key: roles[role].inputs[key].physical_row()
                            for key in sorted(roles[role].inputs)
                        }
                    )
                ).hexdigest(),
            }
            for role in ROLE_ORDER
        },
    }
    return ControllerClosure(
        manifest=controller,
        manifest_sha256=controller_sha256,
        roles=roles,
        closure_sha256=hashlib.sha256(
            _canonical_json(closure_document)
        ).hexdigest(),
    )


def confirmation_phrase(operation_id: str, release_sha: str) -> str:
    return f"{CONFIRMATION_PREFIX}:{operation_id}:{release_sha}"


def _plan(closure: ControllerClosure) -> dict[str, Any]:
    manifest = closure.manifest
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "controller_manifest_sha256": closure.manifest_sha256,
        "input_closure_sha256": closure.closure_sha256,
        "required_confirmation": confirmation_phrase(
            manifest["operation_id"],
            manifest["release_sha"],
        ),
        "roles": {
            role: {
                "host": manifest["topology"][role]["host"],
                "transport": manifest["topology"][role]["transport"],
                "inputs": closure.roles[role].public_inventory(),
            }
            for role in ROLE_ORDER
        },
        "runner_invoked": False,
        "network_io": False,
        "docker_invoked": False,
        "service_mutated": False,
        "current_mutated": False,
        "source_mutated": False,
        "object_storage_mutated": False,
    }


def _default_runner(
    argv: Sequence[str],
    timeout: int,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=dict(env),
        check=False,
        shell=False,
    )


def _run_json(
    runner: Runner,
    argv: Sequence[str],
    *,
    timeout: int,
    label: str,
    env: Mapping[str, str] = SAFE_ENV,
) -> dict[str, Any]:
    if (
        not argv
        or any(
            not isinstance(token, str)
            or not token
            or SAFE_TOKEN_RE.fullmatch(token) is None
            for token in argv
        )
    ):
        raise PrecommitInputOrchestrationError(
            f"{label} argv contains an unsafe token"
        )
    try:
        completed = runner(tuple(argv), timeout, env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrecommitInputOrchestrationError(
            f"{label} could not be executed"
        ) from exc
    if (
        completed.returncode != 0
        or len(completed.stdout) > MAX_JSON_BYTES
        or len(completed.stderr) > MAX_JSON_BYTES
        or completed.stderr
    ):
        raise PrecommitInputOrchestrationError(
            f"{label} failed closed"
        )
    document = _decode_json(
        completed.stdout.rstrip(b"\n"),
        label=f"{label} result",
    )
    if completed.stdout != _canonical_json(document) + b"\n":
        raise PrecommitInputOrchestrationError(
            f"{label} result bytes are not canonical"
        )
    return document


def _encode_host_request(document: Mapping[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(_canonical_json(document)).decode(
        "ascii"
    )
    if (
        len(encoded) > MAX_JSON_BYTES
        or BASE64_RE.fullmatch(encoded) is None
    ):
        raise PrecommitInputOrchestrationError(
            "host request encoding is invalid"
        )
    return encoded


def _decode_host_request(encoded: str) -> dict[str, Any]:
    if (
        not isinstance(encoded, str)
        or len(encoded) > MAX_JSON_BYTES
        or BASE64_RE.fullmatch(encoded) is None
    ):
        raise PrecommitInputOrchestrationError(
            "host request encoding is invalid"
        )
    try:
        payload = base64.b64decode(
            encoded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeError) as exc:
        raise PrecommitInputOrchestrationError(
            "host request encoding is invalid"
        ) from exc
    document = _decode_json(payload, label="host request")
    if payload != _canonical_json(document):
        raise PrecommitInputOrchestrationError(
            "host request bytes are not canonical"
        )
    return document


def _host_request(
    closure: ControllerClosure,
    role: str,
    *,
    action: str,
) -> dict[str, Any]:
    manifest = closure.manifest
    return {
        "schema": HOST_REQUEST_SCHEMA,
        "action": action,
        "role": role,
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "controller_manifest": manifest,
        "controller_manifest_sha256": closure.manifest_sha256,
        "inputs": closure.roles[role].public_inventory(),
    }


def _release_script_path(
    manifest: Mapping[str, Any],
    relative: Path,
) -> Path:
    return (
        Path(manifest["deployment"]["shadow_root"])
        / "releases"
        / manifest["release_sha"]
        / relative
    )


def _ssh_options(
    *,
    known_hosts: Path,
    identity_file: Path,
) -> list[str]:
    known_hosts = _canonical_absolute(
        known_hosts,
        label="SSH known-hosts",
    )
    identity_file = _canonical_absolute(
        identity_file,
        label="SSH identity",
    )
    return [
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "NumberOfPasswordPrompts=0",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-o",
        "ControlMaster=no",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "AddressFamily=inet",
        "-i",
        str(identity_file),
    ]


def _validate_ssh_material(
    *,
    known_hosts: Path,
    identity_file: Path,
) -> None:
    paths = (
        (
            _canonical_absolute(
                known_hosts,
                label="SSH known-hosts",
            ),
            "SSH known-hosts",
        ),
        (
            _canonical_absolute(
                identity_file,
                label="SSH identity",
            ),
            "SSH identity",
        ),
    )
    for path, label in paths:
        try:
            payload = read_secure_bytes(
                path,
                label=label,
                owner_uid=ROOT_UID,
                max_size=4 * 1024 * 1024,
            )
        except SecureFileError as exc:
            raise PrecommitInputOrchestrationError(
                f"{label} is unavailable or unsafe"
            ) from exc
        _assert_root_file_metadata(path, label=label)
        if not payload:
            raise PrecommitInputOrchestrationError(
                f"{label} is empty"
            )


def _host_argv(
    closure: ControllerClosure,
    role: str,
    *,
    action: str,
    known_hosts: Path,
    identity_file: Path,
) -> list[str]:
    encoded = _encode_host_request(
        _host_request(closure, role, action=action)
    )
    script = _release_script_path(
        closure.manifest,
        SCRIPT_RELATIVE,
    )
    agent = [
        PYTHON3,
        "-I",
        "-B",
        str(script),
        "--host-request-b64",
        encoded,
    ]
    if role == "bot_fi":
        return agent
    topology = closure.manifest["topology"][role]
    if (
        role != "webapp_fi"
        or topology["host"] != CUTOVER.WEBAPP_FI_HOST
        or topology["ssh_user"] != "root"
        or topology["ssh_port"] != WEBAPP_FI_PORT
        or topology["transport"] != "ssh-control"
    ):
        raise PrecommitInputOrchestrationError(
            "WebApp-FI transport is not the pinned trusted SSH route"
        )
    return [
        SSH,
        "-p",
        str(WEBAPP_FI_PORT),
        *_ssh_options(
            known_hosts=known_hosts,
            identity_file=identity_file,
        ),
        f"root@{topology['host']}",
        *agent,
    ]


def _validate_host_result(
    document: Mapping[str, Any],
    closure: ControllerClosure,
    role: str,
    *,
    action: str,
) -> dict[str, Any]:
    expected = {
        "schema": HOST_RESULT_SCHEMA,
        "status": "ready" if action == "prepare" else "installed",
        "action": action,
        "role": role,
        "operation_id": closure.manifest["operation_id"],
        "release_sha": closure.manifest["release_sha"],
        "release_tree_sha": closure.manifest["release_tree_sha"],
        "controller_manifest_sha256": closure.manifest_sha256,
        "host_agent_contract_sha256": CUTOVER.HOST_AGENT_CONTRACT_SHA256,
        "expected_host": closure.manifest["topology"][role]["host"],
        "observed_host": closure.manifest["topology"][role]["host"],
        "network_io": False,
        "docker_invoked": False,
        "service_mutated": False,
        "current_mutated": False,
        "source_mutated": False,
        "object_storage_mutated": False,
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise PrecommitInputOrchestrationError(
                f"{role} host {action} result binding differs"
            )
    contract_path = (
        SECURE_ROOT
        / closure.manifest["operation_id"]
        / "host-agent-contract.json"
    )
    if document.get("host_agent_contract") != str(contract_path):
        raise PrecommitInputOrchestrationError(
            f"{role} host-agent contract path differs"
        )
    if document.get("contract_publication") not in {"created", "reused"}:
        raise PrecommitInputOrchestrationError(
            f"{role} host-agent contract publication state differs"
        )
    if action == "prepare":
        if set(document) != {
            *expected,
            "host_agent_contract",
            "contract_publication",
            "needed_files",
            "ready_files",
        }:
            raise PrecommitInputOrchestrationError(
                f"{role} prepare result fields are not exact"
            )
        filenames = {
            item.filename for item in closure.roles[role].inputs.values()
        }
        needed = document["needed_files"]
        ready = document["ready_files"]
        if (
            not isinstance(needed, list)
            or not isinstance(ready, list)
            or needed != sorted(set(needed))
            or ready != sorted(set(ready))
            or set(needed).intersection(ready)
            or set(needed).union(ready) != filenames
        ):
            raise PrecommitInputOrchestrationError(
                f"{role} prepare input state is invalid"
            )
    else:
        if set(document) != {
            *expected,
            "host_agent_contract",
            "contract_publication",
            "attestation",
        }:
            raise PrecommitInputOrchestrationError(
                f"{role} install result fields are not exact"
            )
        _validate_attestation(
            document["attestation"],
            closure,
            role,
        )
    return dict(document)


def _invoke_host(
    runner: Runner,
    closure: ControllerClosure,
    role: str,
    *,
    action: str,
    known_hosts: Path,
    identity_file: Path,
) -> dict[str, Any]:
    result = _run_json(
        runner,
        _host_argv(
            closure,
            role,
            action=action,
            known_hosts=known_hosts,
            identity_file=identity_file,
        ),
        timeout=4 * 60 * 60,
        label=f"{role} host {action}",
    )
    return _validate_host_result(
        result,
        closure,
        role,
        action=action,
    )


def _rehash_input(item: InputFile) -> None:
    try:
        digest, size = sha256_secure_file(
            item.path,
            label=f"{item.key} controller source",
            owner_uid=ROOT_UID,
            max_size=MAX_ARTIFACT_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitInputOrchestrationError(
            f"{item.key} controller source changed or became unsafe"
        ) from exc
    metadata = item.path.stat(follow_symlinks=False)
    if (
        digest != item.sha256
        or size != item.bytes
        or metadata.st_dev != item.device
        or metadata.st_ino != item.inode
        or metadata.st_mode != item.mode
        or metadata.st_uid != item.uid
        or metadata.st_gid != item.gid
        or metadata.st_nlink != item.links
        or metadata.st_mtime_ns != item.mtime_ns
        or metadata.st_ctime_ns != item.ctime_ns
    ):
        raise PrecommitInputOrchestrationError(
            f"{item.key} controller source changed"
        )


def _rehash_role_inputs(closure: ControllerClosure, role: str) -> None:
    for item in closure.roles[role].inputs.values():
        _rehash_input(item)


def _incoming_directory(operation_id: str, role: str) -> Path:
    return (
        SECURE_ROOT
        / operation_id
        / role.replace("_", "-")
        / "precommit-inputs"
        / "incoming"
    )


def _copy_local_partial(item: InputFile, destination: Path) -> None:
    _rehash_input(item)
    source_fd = destination_fd = directory_fd = -1
    try:
        source_fd = os.open(
            item.path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(source_fd)
        directory_fd = os.open(
            destination.parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        destination_fd = os.open(
            destination.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            FILE_MODE,
            dir_fd=directory_fd,
        )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                count = os.write(destination_fd, view[written:])
                if count <= 0:
                    raise PrecommitInputOrchestrationError(
                        "local input copy made no progress"
                    )
                written += count
        os.fchmod(destination_fd, FILE_MODE)
        os.fsync(destination_fd)
        os.fsync(directory_fd)
        after = os.fstat(source_fd)
        if (
            digest.hexdigest() != item.sha256
            or size != item.bytes
            or any(
                getattr(before, field) != getattr(after, field)
                for field in (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_uid",
                    "st_gid",
                    "st_nlink",
                    "st_size",
                )
            )
        ):
            raise PrecommitInputOrchestrationError(
                "local input source changed while copied"
            )
    except FileExistsError as exc:
        raise PrecommitInputOrchestrationError(
            "local transfer destination already exists unexpectedly"
        ) from exc
    except OSError as exc:
        raise PrecommitInputOrchestrationError(
            "local input transfer failed closed"
        ) from exc
    finally:
        for descriptor in (destination_fd, source_fd, directory_fd):
            if descriptor >= 0:
                os.close(descriptor)
    _rehash_input(item)


def _scp_argv(
    closure: ControllerClosure,
    item: InputFile,
    destination: Path,
    *,
    known_hosts: Path,
    identity_file: Path,
) -> list[str]:
    topology = closure.manifest["topology"]["webapp_fi"]
    return [
        SCP,
        "-B",
        "-q",
        "-P",
        str(WEBAPP_FI_PORT),
        *_ssh_options(
            known_hosts=known_hosts,
            identity_file=identity_file,
        ),
        str(item.path),
        f"root@{topology['host']}:{destination}",
    ]


def _transfer_needed(
    runner: Runner,
    closure: ControllerClosure,
    role: str,
    needed_files: Sequence[str],
    *,
    known_hosts: Path,
    identity_file: Path,
) -> None:
    by_filename = {
        item.filename: item
        for item in closure.roles[role].inputs.values()
    }
    for filename in sorted(needed_files):
        item = by_filename[filename]
        destination = (
            _incoming_directory(
                closure.manifest["operation_id"],
                role,
            )
            / f".{filename}.transfer"
        )
        if role == "bot_fi":
            _copy_local_partial(item, destination)
            continue
        _rehash_input(item)
        argv = _scp_argv(
            closure,
            item,
            destination,
            known_hosts=known_hosts,
            identity_file=identity_file,
        )
        if any(
            SAFE_TOKEN_RE.fullmatch(token) is None
            for token in argv
        ):
            raise PrecommitInputOrchestrationError(
                "WebApp-FI SCP argv contains an unsafe token"
            )
        try:
            completed = runner(
                tuple(argv),
                4 * 60 * 60,
                SAFE_ENV,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PrecommitInputOrchestrationError(
                "WebApp-FI SCP transfer could not be executed"
            ) from exc
        if (
            completed.returncode != 0
            or len(completed.stdout) > MAX_JSON_BYTES
            or len(completed.stderr) > MAX_JSON_BYTES
        ):
            raise PrecommitInputOrchestrationError(
                "WebApp-FI SCP transfer failed closed"
            )
        _rehash_input(item)


def _secure_directory_chain(anchor: Path, parts: Sequence[str]) -> Path:
    anchor.mkdir(parents=True, mode=DIRECTORY_MODE, exist_ok=True)
    anchor_metadata = anchor.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(anchor_metadata.st_mode)
        or anchor_metadata.st_uid != ROOT_UID
        or stat.S_IMODE(anchor_metadata.st_mode) != DIRECTORY_MODE
    ):
        raise PrecommitInputOrchestrationError(
            "secure directory anchor is unsafe"
        )
    descriptor = os.open(
        anchor,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        for component in parts:
            if (
                not component
                or component in {".", ".."}
                or "/" in component
                or SAFE_TOKEN_RE.fullmatch(component) is None
            ):
                raise PrecommitInputOrchestrationError(
                    "secure directory component is unsafe"
                )
            try:
                os.mkdir(
                    component,
                    DIRECTORY_MODE,
                    dir_fd=descriptor,
                )
                os.fsync(descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(
                component,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            metadata = os.fstat(next_descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != ROOT_UID
                or stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE
            ):
                os.close(next_descriptor)
                raise PrecommitInputOrchestrationError(
                    "operation directory is not root-only"
                )
            os.close(descriptor)
            descriptor = next_descriptor
    except OSError as exc:
        raise PrecommitInputOrchestrationError(
            "cannot establish secure operation directory"
        ) from exc
    finally:
        os.close(descriptor)
    return anchor.joinpath(*parts)


def _state_hash(document: Mapping[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in document.items()
        if key != "state_sha256"
    }
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _journal_initial(closure: ControllerClosure) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "status": "in-progress",
        "operation_id": closure.manifest["operation_id"],
        "release_sha": closure.manifest["release_sha"],
        "controller_manifest_sha256": closure.manifest_sha256,
        "input_closure_sha256": closure.closure_sha256,
        "completed_roles": [],
        "current_role": None,
        "results": {},
        "state_sha256": "",
    }
    document["state_sha256"] = _state_hash(document)
    return document


def _validate_journal(
    document: Mapping[str, Any],
    closure: ControllerClosure,
) -> dict[str, Any]:
    expected_fields = {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "controller_manifest_sha256",
        "input_closure_sha256",
        "completed_roles",
        "current_role",
        "results",
        "state_sha256",
    }
    if (
        set(document) != expected_fields
        or document["schema"] != JOURNAL_SCHEMA
        or document["status"] not in {"in-progress", "completed"}
        or document["operation_id"] != closure.manifest["operation_id"]
        or document["release_sha"] != closure.manifest["release_sha"]
        or document["controller_manifest_sha256"]
        != closure.manifest_sha256
        or document["input_closure_sha256"] != closure.closure_sha256
        or document["state_sha256"] != _state_hash(document)
    ):
        raise PrecommitInputOrchestrationError(
            "controller journal binding is invalid"
        )
    completed = document["completed_roles"]
    if (
        not isinstance(completed, list)
        or completed != list(ROLE_ORDER[: len(completed)])
        or document["current_role"] not in {None, *ROLE_ORDER}
        or not isinstance(document["results"], dict)
        or set(document["results"]) != set(completed)
    ):
        raise PrecommitInputOrchestrationError(
            "controller journal role state is invalid"
        )
    expected_current = (
        ROLE_ORDER[len(completed)]
        if len(completed) < len(ROLE_ORDER)
        else None
    )
    if (
        document["current_role"] not in {None, expected_current}
        or (
            document["status"] == "completed"
            and (
                completed != list(ROLE_ORDER)
                or document["current_role"] is not None
            )
        )
        or (
            document["status"] == "in-progress"
            and completed == list(ROLE_ORDER)
        )
    ):
        raise PrecommitInputOrchestrationError(
            "controller journal progress state is inconsistent"
        )
    return dict(document)


def _write_journal(path: Path, document: dict[str, Any]) -> str:
    document["state_sha256"] = _state_hash(document)
    payload = _canonical_json(document)
    existed = path.exists()
    if existed:
        try:
            existing = read_secure_bytes(
                path,
                label="precommit input orchestration journal",
                owner_uid=ROOT_UID,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise PrecommitInputOrchestrationError(
                "controller journal is unavailable or unsafe"
            ) from exc
        _assert_root_file_metadata(
            path,
            label="precommit input orchestration journal",
        )
        if existing == payload:
            return "reused"
    try:
        write_secure_atomic_bytes(
            path,
            payload,
            label="precommit input orchestration journal",
            mode=FILE_MODE,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitInputOrchestrationError(
            "controller journal could not be persisted"
        ) from exc
    return "updated" if existed else "created"


def _load_or_create_journal(
    path: Path,
    closure: ControllerClosure,
) -> dict[str, Any]:
    if not path.exists():
        document = _journal_initial(closure)
        _write_journal(path, document)
        return document
    try:
        payload = read_secure_bytes(
            path,
            label="precommit input orchestration journal",
            owner_uid=ROOT_UID,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitInputOrchestrationError(
            "controller journal is unavailable or unsafe"
        ) from exc
    _assert_root_file_metadata(
        path,
        label="precommit input orchestration journal",
    )
    document = _decode_json(payload, label="controller journal")
    if payload != _canonical_json(document):
        raise PrecommitInputOrchestrationError(
            "controller journal bytes are not canonical"
        )
    return _validate_journal(document, closure)


def _attestation_semantic(document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise PrecommitInputOrchestrationError(
            "host readback attestation is invalid"
        )
    return dict(document)


def _validate_attestation(
    document: Any,
    closure: ControllerClosure,
    role: str,
) -> dict[str, Any]:
    expected_top_fields = {
        "schema",
        "status",
        "role",
        "operation_id",
        "release_sha",
        "controller_manifest_sha256",
        "host_agent_contract_sha256",
        "artifacts",
        "docker_invoked",
        "service_mutated",
        "current_mutated",
        "source_mutated",
        "object_storage_mutated",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_top_fields
        or document.get("schema") != ATTESTATION_SCHEMA
        or document.get("status") != "verified"
        or document.get("role") != role
        or document.get("operation_id")
        != closure.manifest["operation_id"]
        or document.get("release_sha") != closure.manifest["release_sha"]
        or document.get("controller_manifest_sha256")
        != closure.manifest_sha256
        or document.get("host_agent_contract_sha256")
        != CUTOVER.HOST_AGENT_CONTRACT_SHA256
        or document.get("docker_invoked") is not False
        or document.get("service_mutated") is not False
        or document.get("current_mutated") is not False
        or document.get("source_mutated") is not False
        or document.get("object_storage_mutated") is not False
    ):
        raise PrecommitInputOrchestrationError(
            f"{role} readback attestation differs"
        )
    artifacts = document.get("artifacts")
    expected_keys = {
        "precommit_manifest",
        "role_material",
        "role_compose",
        "runtime_environment",
        "ca_certificate",
        "source_snapshot_manifest",
        "database",
        "uploads",
        "audit",
        "host_agent_contract",
    }
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != expected_keys
        or any(
            not isinstance(row, dict)
            or set(row)
            != {
                "path",
                "sha256",
                "bytes",
                "mode",
                "device",
                "inode",
                "links",
            }
            or row["mode"] != "0600"
            or not isinstance(row["device"], int)
            or isinstance(row["device"], bool)
            or row["device"] < 0
            or not isinstance(row["inode"], int)
            or isinstance(row["inode"], bool)
            or row["inode"] < 1
            or row["links"] != 1
            for row in artifacts.values()
        )
    ):
        raise PrecommitInputOrchestrationError(
            f"{role} readback artifact closure differs"
        )
    role_closure = closure.roles[role]
    operation_id = closure.manifest["operation_id"]
    paths = WORKER.operation_paths(
        operation_id,
        closure.manifest["release_sha"],
        role,
    )
    incoming = _incoming_directory(operation_id, role)
    contract_payload = _canonical_json(
        CUTOVER.host_agent_contract_document()
    )
    expected_artifacts = {
        "precommit_manifest": {
            "path": str(paths.manifest),
            **role_closure.inputs[
                "precommit_manifest"
            ].public_row(),
        },
        "role_material": {
            "path": str(paths.artifacts["role-material"]),
            **role_closure.inputs["role_material"].public_row(),
        },
        "role_compose": {
            "path": str(paths.compose),
            **role_closure.installed_bindings["role_compose"],
        },
        "runtime_environment": {
            "path": str(paths.environment),
            **role_closure.installed_bindings["runtime_environment"],
        },
        "ca_certificate": {
            "path": str(paths.secret_root / "tls" / "ca.crt"),
            **role_closure.installed_bindings["ca_certificate"],
        },
        "source_snapshot_manifest": {
            "path": str(
                incoming
                / role_closure.inputs[
                    "source_snapshot_manifest"
                ].filename
            ),
            **role_closure.inputs[
                "source_snapshot_manifest"
            ].public_row(),
        },
        "database": {
            "path": str(paths.artifacts["database-backup"]),
            **role_closure.inputs["database"].public_row(),
        },
        "uploads": {
            "path": str(paths.artifacts["uploads-archive"]),
            **role_closure.inputs["uploads"].public_row(),
        },
        "audit": {
            "path": str(paths.artifacts["audit-archive"]),
            **role_closure.inputs["audit"].public_row(),
        },
        "host_agent_contract": {
            "path": str(
                SECURE_ROOT
                / operation_id
                / "host-agent-contract.json"
            ),
            "sha256": CUTOVER.HOST_AGENT_CONTRACT_SHA256,
            "bytes": len(contract_payload),
        },
    }
    for kind, expected in expected_artifacts.items():
        actual = artifacts[kind]
        if (
            actual["path"] != expected["path"]
            or actual["sha256"] != expected["sha256"]
            or actual["bytes"] != expected["bytes"]
        ):
            raise PrecommitInputOrchestrationError(
                f"{role} {kind} readback differs"
            )
    return _attestation_semantic(document)


def _evidence_document(
    closure: ControllerClosure,
    results: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": "completed",
        "operation_id": closure.manifest["operation_id"],
        "release_sha": closure.manifest["release_sha"],
        "controller_manifest_sha256": closure.manifest_sha256,
        "input_closure_sha256": closure.closure_sha256,
        "roles": {
            role: results[role]
            for role in ROLE_ORDER
        },
        "network_scope": ["local-controller", "pinned-webapp-fi-ssh"],
        "docker_invoked": False,
        "service_mutated": False,
        "current_mutated": False,
        "source_mutated": False,
        "object_storage_mutated": False,
    }


def _publish_evidence(path: Path, document: Mapping[str, Any]) -> str:
    payload = _canonical_json(document)
    if path.exists():
        try:
            existing = read_secure_bytes(
                path,
                label="precommit input orchestration evidence",
                owner_uid=ROOT_UID,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise PrecommitInputOrchestrationError(
                "controller evidence is unavailable or unsafe"
            ) from exc
        _assert_root_file_metadata(
            path,
            label="precommit input orchestration evidence",
        )
        if existing != payload:
            raise PrecommitInputOrchestrationError(
                "controller evidence conflicts with completed state"
            )
        return "reused"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="precommit input orchestration evidence",
            mode=FILE_MODE,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitInputOrchestrationError(
            "controller evidence could not be published"
        ) from exc
    return "created"


def _apply(
    closure: ControllerClosure,
    *,
    runner: Runner,
    known_hosts: Path,
    identity_file: Path,
) -> dict[str, Any]:
    operation_id = closure.manifest["operation_id"]
    directory = _secure_directory_chain(
        SECURE_ROOT,
        (
            operation_id,
            "controller",
            "precommit-input-orchestrator",
        ),
    )
    lock_path = directory / "lock"
    lock_fd = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        FILE_MODE,
    )
    try:
        lock_metadata = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != ROOT_UID
            or stat.S_IMODE(lock_metadata.st_mode) != FILE_MODE
            or lock_metadata.st_nlink != 1
        ):
            raise PrecommitInputOrchestrationError(
                "controller orchestration lock is unsafe"
            )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PrecommitInputOrchestrationError(
                "another precommit input orchestration is active"
            ) from exc
        journal_path = directory / "journal.json"
        evidence_path = directory / "evidence.json"
        journal = _load_or_create_journal(journal_path, closure)
        completed = list(journal["completed_roles"])
        results = dict(journal["results"])
        for role in ROLE_ORDER:
            _rehash_role_inputs(closure, role)
            if role in completed:
                observed = _invoke_host(
                    runner,
                    closure,
                    role,
                    action="install",
                    known_hosts=known_hosts,
                    identity_file=identity_file,
                )["attestation"]
                if observed != results[role]:
                    raise PrecommitInputOrchestrationError(
                        f"{role} completed readback differs from the journal"
                    )
                _rehash_role_inputs(closure, role)
                continue
            journal["status"] = "in-progress"
            journal["current_role"] = role
            _write_journal(journal_path, journal)
            prepared = _invoke_host(
                runner,
                closure,
                role,
                action="prepare",
                known_hosts=known_hosts,
                identity_file=identity_file,
            )
            _transfer_needed(
                runner,
                closure,
                role,
                prepared["needed_files"],
                known_hosts=known_hosts,
                identity_file=identity_file,
            )
            installed = _invoke_host(
                runner,
                closure,
                role,
                action="install",
                known_hosts=known_hosts,
                identity_file=identity_file,
            )
            results[role] = installed["attestation"]
            _rehash_role_inputs(closure, role)
            completed.append(role)
            journal["completed_roles"] = list(completed)
            journal["results"] = dict(results)
            journal["current_role"] = None
            if completed != list(ROLE_ORDER):
                _write_journal(journal_path, journal)
        evidence = _evidence_document(closure, results)
        publication = _publish_evidence(evidence_path, evidence)
        journal["status"] = "completed"
        journal["current_role"] = None
        _write_journal(journal_path, journal)
        return {
            **evidence,
            "evidence_path": str(evidence_path),
            "evidence_sha256": hashlib.sha256(
                _canonical_json(evidence)
            ).hexdigest(),
            "evidence_publication": publication,
            "runner_invoked": True,
            "network_io": True,
        }
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def orchestrate(
    *,
    controller_manifest: Path,
    bot_precommit_manifest: Path,
    webapp_precommit_manifest: Path,
    bot_role_material: Path,
    webapp_role_material: Path,
    bot_source_snapshot_manifest: Path,
    webapp_source_snapshot_manifest: Path,
    apply: bool = False,
    confirm: str | None = None,
    known_hosts: Path = Path("/root/.ssh/known_hosts"),
    identity_file: Path = Path("/root/.ssh/id_ed25519"),
    runner: Runner | None = None,
) -> dict[str, Any]:
    if os.geteuid() != ROOT_UID or os.getegid() != ROOT_GID:
        raise PrecommitInputOrchestrationError(
            "precommit input orchestration must run as root:root"
        )
    closure = load_controller_closure(
        controller_manifest=controller_manifest,
        bot_precommit_manifest=bot_precommit_manifest,
        webapp_precommit_manifest=webapp_precommit_manifest,
        bot_role_material=bot_role_material,
        webapp_role_material=webapp_role_material,
        bot_source_snapshot_manifest=bot_source_snapshot_manifest,
        webapp_source_snapshot_manifest=webapp_source_snapshot_manifest,
    )
    required = confirmation_phrase(
        closure.manifest["operation_id"],
        closure.manifest["release_sha"],
    )
    if not apply:
        if confirm is not None:
            raise PrecommitInputOrchestrationError(
                "--confirm is valid only with --apply"
            )
        return _plan(closure)
    if confirm != required:
        raise PrecommitInputOrchestrationError(
            f"apply requires --confirm {required}"
        )
    _validate_ssh_material(
        known_hosts=known_hosts,
        identity_file=identity_file,
    )
    return _apply(
        closure,
        runner=_default_runner if runner is None else runner,
        known_hosts=known_hosts,
        identity_file=identity_file,
    )


def _validate_host_request(document: Any) -> dict[str, Any]:
    if (
        not isinstance(document, dict)
        or set(document) != HOST_REQUEST_FIELDS
        or document.get("schema") != HOST_REQUEST_SCHEMA
        or document.get("action") not in {"prepare", "install"}
        or document.get("role") not in ROLE_ORDER
    ):
        raise PrecommitInputOrchestrationError(
            "host request fields are not exact"
        )
    try:
        controller = CUTOVER.validate_manifest(
            document["controller_manifest"]
        )
    except CUTOVER.CutoverContractError as exc:
        raise PrecommitInputOrchestrationError(
            "host request controller manifest is invalid"
        ) from exc
    controller_payload = _canonical_json(controller)
    controller_sha256 = hashlib.sha256(controller_payload).hexdigest()
    if (
        document["operation_id"] != controller["operation_id"]
        or document["release_sha"] != controller["release_sha"]
        or document["release_tree_sha"] != controller["release_tree_sha"]
        or document["controller_manifest_sha256"] != controller_sha256
    ):
        raise PrecommitInputOrchestrationError(
            "host request differs from the controller manifest"
        )
    role = document["role"]
    if role not in ROLE_ORDER:
        raise PrecommitInputOrchestrationError(
            "host request role is not an FI source host"
        )
    inputs = document["inputs"]
    expected_filenames = {
        ROLE_FILENAMES["precommit_manifest"],
        ROLE_FILENAMES["role_material"][role],
        ROLE_FILENAMES["source_snapshot_manifest"],
        ROLE_FILENAMES["database"],
        ROLE_FILENAMES["uploads"],
        ROLE_FILENAMES["audit"],
    }
    if (
        not isinstance(inputs, dict)
        or set(inputs)
        != {
            "precommit_manifest",
            "role_material",
            "source_snapshot_manifest",
            "database",
            "uploads",
            "audit",
        }
    ):
        raise PrecommitInputOrchestrationError(
            "host request input closure is not exact"
        )
    observed_filenames: set[str] = set()
    for key, row in inputs.items():
        if (
            not isinstance(row, dict)
            or set(row) != INPUT_ROW_FIELDS
            or not isinstance(row["filename"], str)
            or SAFE_TOKEN_RE.fullmatch(row["filename"]) is None
            or "/" in row["filename"]
            or not isinstance(row["sha256"], str)
            or WORKER.SHA256_RE.fullmatch(row["sha256"]) is None
            or row["sha256"] == "0" * 64
            or isinstance(row["bytes"], bool)
            or not isinstance(row["bytes"], int)
            or not 1 <= row["bytes"] <= MAX_ARTIFACT_BYTES
        ):
            raise PrecommitInputOrchestrationError(
                f"host request {key} input is invalid"
            )
        observed_filenames.add(row["filename"])
    if observed_filenames != expected_filenames:
        raise PrecommitInputOrchestrationError(
            "host request filenames are not exact"
        )
    return document


def _git_output(
    runner: Runner,
    argv: Sequence[str],
    *,
    label: str,
) -> str:
    try:
        completed = runner(tuple(argv), 60, SAFE_GIT_ENV)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrecommitInputOrchestrationError(
            f"{label} could not be verified"
        ) from exc
    if (
        completed.returncode != 0
        or len(completed.stdout) > MAX_JSON_BYTES
        or len(completed.stderr) > MAX_JSON_BYTES
    ):
        raise PrecommitInputOrchestrationError(
            f"{label} verification failed closed"
        )
    try:
        return completed.stdout.decode("ascii").strip()
    except UnicodeError as exc:
        raise PrecommitInputOrchestrationError(
            f"{label} returned non-ASCII output"
        ) from exc


def _verify_release(
    request: Mapping[str, Any],
    *,
    runner: Runner,
    current_script: Path,
) -> Path:
    controller = request["controller_manifest"]
    release_root = (
        Path(controller["deployment"]["shadow_root"])
        / "releases"
        / request["release_sha"]
    )
    expected_script = release_root / SCRIPT_RELATIVE
    if current_script.resolve() != expected_script:
        raise PrecommitInputOrchestrationError(
            "host subcommand is not the release-bound orchestrator"
        )
    commands = {
        "head": [GIT, "-C", str(release_root), "rev-parse", "HEAD"],
        "tree": [
            GIT,
            "-C",
            str(release_root),
            "rev-parse",
            "HEAD^{tree}",
        ],
        "status": [
            GIT,
            "-C",
            str(release_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        "branch": [
            GIT,
            "-C",
            str(release_root),
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ],
        "remotes": [GIT, "-C", str(release_root), "remote"],
        "top": [
            GIT,
            "-C",
            str(release_root),
            "rev-parse",
            "--show-toplevel",
        ],
    }
    observed = {
        key: _git_output(
            runner,
            argv,
            label=f"release {key}",
        )
        for key, argv in commands.items()
    }
    if (
        observed["head"] != request["release_sha"]
        or observed["tree"] != request["release_tree_sha"]
        or observed["status"]
        or observed["branch"] != "HEAD"
        or observed["remotes"]
        or observed["top"] != str(release_root)
    ):
        raise PrecommitInputOrchestrationError(
            "release is not exact, detached, clean, and isolated"
        )
    release_sources = (
        SCRIPT_RELATIVE,
        INSTALLER_RELATIVE,
        HOST_AGENT_RELATIVE,
    )
    for relative in release_sources:
        source = release_root / relative
        tree_row = _git_output(
            runner,
            [
                GIT,
                "-C",
                str(release_root),
                "ls-tree",
                "HEAD",
                "--",
                relative.as_posix(),
            ],
            label=f"release source tree entry {relative}",
        )
        pieces = tree_row.split(None, 3)
        if (
            len(pieces) != 4
            or pieces[0] not in {"100644", "100755"}
            or pieces[1] != "blob"
            or not WORKER.SHA40_RE.fullmatch(pieces[2])
            or pieces[3] != relative.as_posix()
        ):
            raise PrecommitInputOrchestrationError(
                f"release source {relative} is not tracked exactly"
            )
        observed_blob = _git_output(
            runner,
            [
                GIT,
                "-C",
                str(release_root),
                "hash-object",
                "--no-filters",
                str(source),
            ],
            label=f"release source blob {relative}",
        )
        if observed_blob != pieces[2]:
            raise PrecommitInputOrchestrationError(
                f"release source {relative} differs from the Git tree"
            )
        _hash_regular_release_file(
            source,
            label=f"release source {relative}",
        )
    host_agent_path = release_root / HOST_AGENT_RELATIVE
    observed_agent = _hash_regular_release_file(
        host_agent_path,
        label="release host agent",
    )
    if (
        observed_agent
        != controller["artifacts"]["host_agent_sha256"]
    ):
        raise PrecommitInputOrchestrationError(
            "release host-agent artifact differs from the controller"
        )
    if (
        controller["artifacts"]["host_agent_contract_sha256"]
        != CUTOVER.HOST_AGENT_CONTRACT_SHA256
    ):
        raise PrecommitInputOrchestrationError(
            "controller host-agent contract digest differs"
        )
    return release_root


def _hash_regular_release_file(path: Path, *, label: str) -> str:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= 16 * 1024 * 1024
        ):
            raise PrecommitInputOrchestrationError(
                f"{label} is unsafe"
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if any(
            getattr(before, field) != getattr(after, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
            )
        ):
            raise PrecommitInputOrchestrationError(
                f"{label} changed while read"
            )
        return digest.hexdigest()
    except OSError as exc:
        raise PrecommitInputOrchestrationError(
            f"{label} is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_contract(operation_id: str) -> tuple[Path, str]:
    directory = _secure_directory_chain(
        SECURE_ROOT,
        (operation_id,),
    )
    path = directory / "host-agent-contract.json"
    document = CUTOVER.host_agent_contract_document()
    try:
        HOST_AGENT.validate_contract(document)
    except HOST_AGENT.HostAgentError as exc:
        raise PrecommitInputOrchestrationError(
            "host-agent contract schema is invalid"
        ) from exc
    payload = _canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != CUTOVER.HOST_AGENT_CONTRACT_SHA256:
        raise PrecommitInputOrchestrationError(
            "host-agent contract digest is inconsistent"
        )
    if path.exists():
        try:
            existing = read_secure_bytes(
                path,
                label="operation host-agent contract",
                owner_uid=ROOT_UID,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise PrecommitInputOrchestrationError(
                "operation host-agent contract is unsafe"
            ) from exc
        _assert_root_file_metadata(
            path,
            label="operation host-agent contract",
        )
        if existing != payload:
            raise PrecommitInputOrchestrationError(
                "operation host-agent contract conflicts"
            )
        return path, "reused"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="operation host-agent contract",
            mode=FILE_MODE,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitInputOrchestrationError(
            "operation host-agent contract could not be published"
        ) from exc
    return path, "created"


def _read_expected_host_file(
    path: Path,
    row: Mapping[str, Any],
    *,
    allow_two_links: bool = False,
) -> tuple[int, int] | None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PrecommitInputOrchestrationError(
            "host incoming input is unsafe"
        ) from exc
    try:
        before = os.fstat(descriptor)
        allowed_links = {1, 2} if allow_two_links else {1}
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink not in allowed_links
            or before.st_size != row["bytes"]
        ):
            raise PrecommitInputOrchestrationError(
                "host incoming input metadata differs"
            )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            digest.hexdigest() != row["sha256"]
            or size != row["bytes"]
            or any(
                getattr(before, field) != getattr(after, field)
                for field in (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_uid",
                    "st_gid",
                    "st_nlink",
                    "st_size",
                )
            )
        ):
            raise PrecommitInputOrchestrationError(
                "host incoming input content differs"
            )
        return before.st_dev, before.st_ino
    finally:
        os.close(descriptor)


def _remove_safe_partial(path: Path) -> None:
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) != FILE_MODE
        or metadata.st_nlink != 1
    ):
        raise PrecommitInputOrchestrationError(
            "operation transfer residue is unsafe"
        )
    path.unlink()
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _prepare_incoming(
    request: Mapping[str, Any],
) -> tuple[Path, list[str], list[str]]:
    role = request["role"]
    directory = _secure_directory_chain(
        SECURE_ROOT,
        (
            request["operation_id"],
            role.replace("_", "-"),
            "precommit-inputs",
            "incoming",
        ),
    )
    needed: list[str] = []
    ready: list[str] = []
    for key in sorted(request["inputs"]):
        row = request["inputs"][key]
        final_path = directory / row["filename"]
        partial_path = directory / f".{row['filename']}.transfer"
        final_identity = _read_expected_host_file(
            final_path,
            row,
            allow_two_links=True,
        )
        partial_identity: tuple[int, int] | None
        try:
            partial_identity = _read_expected_host_file(
                partial_path,
                row,
                allow_two_links=True,
            )
        except PrecommitInputOrchestrationError:
            if final_identity is not None:
                raise
            _remove_safe_partial(partial_path)
            partial_identity = None
        if final_identity is not None:
            if partial_identity is not None:
                if (
                    partial_identity == final_identity
                    or partial_path.stat(follow_symlinks=False).st_nlink == 1
                ):
                    partial_path.unlink()
                    directory_fd = os.open(
                        directory,
                        os.O_RDONLY | os.O_DIRECTORY,
                    )
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                else:
                    raise PrecommitInputOrchestrationError(
                        "host transfer hardlink state is inconsistent"
                    )
            _read_expected_host_file(final_path, row)
            ready.append(row["filename"])
        elif partial_identity is not None:
            ready.append(row["filename"])
        else:
            needed.append(row["filename"])
    return directory, sorted(needed), sorted(ready)


def _promote_incoming(
    request: Mapping[str, Any],
    directory: Path,
) -> Mapping[str, Path]:
    paths: dict[str, Path] = {}
    directory_fd = os.open(
        directory,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        for key in sorted(request["inputs"]):
            row = request["inputs"][key]
            final_path = directory / row["filename"]
            partial_path = directory / f".{row['filename']}.transfer"
            final_identity = _read_expected_host_file(
                final_path,
                row,
                allow_two_links=True,
            )
            partial_identity = _read_expected_host_file(
                partial_path,
                row,
                allow_two_links=True,
            )
            if final_identity is None:
                if partial_identity is None:
                    raise PrecommitInputOrchestrationError(
                        "host input transfer is incomplete"
                    )
                try:
                    os.link(
                        partial_path.name,
                        final_path.name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    os.fsync(directory_fd)
                except FileExistsError:
                    pass
                final_identity = _read_expected_host_file(
                    final_path,
                    row,
                    allow_two_links=True,
                )
            if partial_identity is not None:
                if partial_identity != final_identity:
                    raise PrecommitInputOrchestrationError(
                        "host input promotion identity differs"
                    )
                os.unlink(partial_path.name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            _read_expected_host_file(final_path, row)
            paths[key] = final_path
    finally:
        os.close(directory_fd)
    return paths


def _parse_installer_result(
    document: Any,
    *,
    request: Mapping[str, Any],
    manifest: WORKER.PrecommitManifest,
) -> dict[str, Any]:
    if (
        not isinstance(document, dict)
        or document.get("schema") != WORKER.MANIFEST_SCHEMA
        or document.get("status") not in {"installed", "already-installed"}
        or document.get("operation_id") != request["operation_id"]
        or document.get("role") != request["role"]
        or document.get("release_sha") != request["release_sha"]
        or document.get("manifest_sha256") != manifest.canonical_sha256
        or document.get("network_io") is not False
        or document.get("docker_invoked") is not False
        or document.get("service_mutated") is not False
        or document.get("current_mutated") is not False
        or document.get("source_mutated") is not False
    ):
        raise PrecommitInputOrchestrationError(
            "release-bound installer result differs"
        )
    return dict(document)


def _run_installer(
    request: Mapping[str, Any],
    release_root: Path,
    inputs: Mapping[str, Path],
    *,
    runner: Runner,
) -> tuple[WORKER.PrecommitManifest, Mapping[str, bytes]]:
    try:
        (
            _document,
            _payload,
            manifest,
            _identity,
        ) = INSTALLER._load_precommit_manifest_source(  # noqa: SLF001
            inputs["precommit_manifest"],
            expected_role=request["role"],
        )
        paths = WORKER.operation_paths(
            manifest.operation_id,
            manifest.release_sha,
            manifest.role,
        )
        (
            _archive_payload,
            _archive_identity,
            role_members,
        ) = INSTALLER._load_role_material(  # noqa: SLF001
            inputs["role_material"],
            manifest=manifest,
            paths=paths,
        )
        INSTALLER._load_source_snapshot(  # noqa: SLF001
            inputs["source_snapshot_manifest"],
            manifest=manifest,
        )
    except (
        INSTALLER.PrecommitInputInstallError,
        WORKER.PrecommitWorkerError,
    ) as exc:
        raise PrecommitInputOrchestrationError(
            "host input semantic closure is invalid"
        ) from exc
    controller = request["controller_manifest"]
    _validate_role_controller_binding(
        controller=controller,
        controller_sha256=request["controller_manifest_sha256"],
        role=request["role"],
        manifest=manifest,
    )
    installer_path = release_root / INSTALLER_RELATIVE
    if _hash_regular_release_file(
        installer_path,
        label="release precommit input installer",
    ) == "0" * 64:
        raise PrecommitInputOrchestrationError(
            "release installer digest is invalid"
        )
    confirmation = INSTALLER.confirmation_phrase(
        request["operation_id"],
        request["role"],
        request["release_sha"],
    )
    argv = [
        PYTHON3,
        "-I",
        "-B",
        str(installer_path),
        "--role",
        request["role"],
        "--precommit-manifest",
        str(inputs["precommit_manifest"]),
        "--role-material",
        str(inputs["role_material"]),
        "--source-snapshot-manifest",
        str(inputs["source_snapshot_manifest"]),
        "--apply",
        "--confirm",
        confirmation,
    ]
    result = _run_json(
        runner,
        argv,
        timeout=4 * 60 * 60,
        label="release-bound precommit input installer",
    )
    _parse_installer_result(
        result,
        request=request,
        manifest=manifest,
    )
    return manifest, role_members


def _attested_file(
    path: Path,
    *,
    label: str,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> dict[str, Any]:
    try:
        digest, size = sha256_secure_file(
            path,
            label=label,
            owner_uid=ROOT_UID,
            max_size=MAX_ARTIFACT_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitInputOrchestrationError(
            f"{label} readback is unsafe"
        ) from exc
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) != FILE_MODE
        or metadata.st_nlink != 1
        or metadata.st_size != size
    ):
        raise PrecommitInputOrchestrationError(
            f"{label} readback metadata differs"
        )
    if (
        expected_sha256 is not None
        and digest != expected_sha256
        or expected_bytes is not None
        and size != expected_bytes
    ):
        raise PrecommitInputOrchestrationError(
            f"{label} readback differs"
        )
    return {
        "path": str(path),
        "sha256": digest,
        "bytes": size,
        "mode": "0600",
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "links": metadata.st_nlink,
    }


def _build_attestation(
    request: Mapping[str, Any],
    inputs: Mapping[str, Path],
    manifest: WORKER.PrecommitManifest,
    role_members: Mapping[str, bytes],
    contract_path: Path,
) -> dict[str, Any]:
    paths = WORKER.operation_paths(
        manifest.operation_id,
        manifest.release_sha,
        manifest.role,
    )
    artifacts = {
        "precommit_manifest": _attested_file(
            paths.manifest,
            label="installed precommit manifest",
            expected_sha256=request["inputs"]["precommit_manifest"]["sha256"],
            expected_bytes=request["inputs"]["precommit_manifest"]["bytes"],
        ),
        "role_material": _attested_file(
            paths.artifacts["role-material"],
            label="installed role material",
            expected_sha256=request["inputs"]["role_material"]["sha256"],
            expected_bytes=request["inputs"]["role_material"]["bytes"],
        ),
        "role_compose": _attested_file(
            paths.compose,
            label="installed role compose",
            expected_sha256=hashlib.sha256(
                role_members["role-compose.yml"]
            ).hexdigest(),
            expected_bytes=len(role_members["role-compose.yml"]),
        ),
        "runtime_environment": _attested_file(
            paths.environment,
            label="installed runtime environment",
            expected_sha256=hashlib.sha256(
                role_members["runtime.env.role"]
            ).hexdigest(),
            expected_bytes=len(role_members["runtime.env.role"]),
        ),
        "ca_certificate": _attested_file(
            paths.secret_root / "tls" / "ca.crt",
            label="installed CA certificate",
            expected_sha256=hashlib.sha256(
                role_members["ca.crt"]
            ).hexdigest(),
            expected_bytes=len(role_members["ca.crt"]),
        ),
        "source_snapshot_manifest": _attested_file(
            inputs["source_snapshot_manifest"],
            label="source snapshot manifest input",
            expected_sha256=request["inputs"]["source_snapshot_manifest"][
                "sha256"
            ],
            expected_bytes=request["inputs"]["source_snapshot_manifest"][
                "bytes"
            ],
        ),
        "database": _attested_file(
            paths.artifacts["database-backup"],
            label="installed database snapshot",
            expected_sha256=request["inputs"]["database"]["sha256"],
            expected_bytes=request["inputs"]["database"]["bytes"],
        ),
        "uploads": _attested_file(
            paths.artifacts["uploads-archive"],
            label="installed uploads snapshot",
            expected_sha256=request["inputs"]["uploads"]["sha256"],
            expected_bytes=request["inputs"]["uploads"]["bytes"],
        ),
        "audit": _attested_file(
            paths.artifacts["audit-archive"],
            label="installed audit snapshot",
            expected_sha256=request["inputs"]["audit"]["sha256"],
            expected_bytes=request["inputs"]["audit"]["bytes"],
        ),
        "host_agent_contract": _attested_file(
            contract_path,
            label="operation host-agent contract",
            expected_sha256=CUTOVER.HOST_AGENT_CONTRACT_SHA256,
            expected_bytes=len(
                _canonical_json(CUTOVER.host_agent_contract_document())
            ),
        ),
    }
    return {
        "schema": ATTESTATION_SCHEMA,
        "status": "verified",
        "role": request["role"],
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "controller_manifest_sha256": request[
            "controller_manifest_sha256"
        ],
        "host_agent_contract_sha256": CUTOVER.HOST_AGENT_CONTRACT_SHA256,
        "artifacts": dict(sorted(artifacts.items())),
        "docker_invoked": False,
        "service_mutated": False,
        "current_mutated": False,
        "source_mutated": False,
        "object_storage_mutated": False,
    }


def host_execute(
    encoded_request: str,
    *,
    runner: Runner | None = None,
    current_script: Path | None = None,
    observed_addresses: set[str] | None = None,
) -> dict[str, Any]:
    if os.geteuid() != ROOT_UID or os.getegid() != ROOT_GID:
        raise PrecommitInputOrchestrationError(
            "host precommit input subcommand must run as root:root"
        )
    request = _validate_host_request(
        _decode_host_request(encoded_request)
    )
    active_runner = _default_runner if runner is None else runner
    release_root = _verify_release(
        request,
        runner=active_runner,
        current_script=(
            Path(__file__).resolve()
            if current_script is None
            else current_script
        ),
    )
    try:
        addresses = (
            HOST_AGENT.observe_local_ipv4_addresses()
            if observed_addresses is None
            else observed_addresses
        )
    except HOST_AGENT.HostAgentError as exc:
        raise PrecommitInputOrchestrationError(
            "host network identity could not be observed"
        ) from exc
    expected_host = request["controller_manifest"]["topology"][
        request["role"]
    ]["host"]
    if expected_host not in addresses:
        raise PrecommitInputOrchestrationError(
            "host identity differs from the controller topology"
        )
    contract_path, contract_publication = _publish_contract(
        request["operation_id"]
    )
    incoming, needed, ready = _prepare_incoming(request)
    base = {
        "schema": HOST_RESULT_SCHEMA,
        "action": request["action"],
        "role": request["role"],
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "release_tree_sha": request["release_tree_sha"],
        "controller_manifest_sha256": request[
            "controller_manifest_sha256"
        ],
        "host_agent_contract": str(contract_path),
        "host_agent_contract_sha256": CUTOVER.HOST_AGENT_CONTRACT_SHA256,
        "contract_publication": contract_publication,
        "expected_host": expected_host,
        "observed_host": expected_host,
        "network_io": False,
        "docker_invoked": False,
        "service_mutated": False,
        "current_mutated": False,
        "source_mutated": False,
        "object_storage_mutated": False,
    }
    if request["action"] == "prepare":
        return {
            **base,
            "status": "ready",
            "needed_files": needed,
            "ready_files": ready,
        }
    if needed:
        raise PrecommitInputOrchestrationError(
            "host precommit input transfer is incomplete"
        )
    inputs = _promote_incoming(request, incoming)
    manifest, role_members = _run_installer(
        request,
        release_root,
        inputs,
        runner=active_runner,
    )
    attestation = _build_attestation(
        request,
        inputs,
        manifest,
        role_members,
        contract_path,
    )
    return {
        **base,
        "status": "installed",
        "attestation": attestation,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-manifest", type=Path)
    parser.add_argument("--bot-precommit-manifest", type=Path)
    parser.add_argument("--webapp-precommit-manifest", type=Path)
    parser.add_argument("--bot-role-material", type=Path)
    parser.add_argument("--webapp-role-material", type=Path)
    parser.add_argument("--bot-source-snapshot-manifest", type=Path)
    parser.add_argument("--webapp-source-snapshot-manifest", type=Path)
    parser.add_argument(
        "--known-hosts",
        type=Path,
        default=Path("/root/.ssh/known_hosts"),
    )
    parser.add_argument(
        "--identity-file",
        type=Path,
        default=Path("/root/.ssh/id_ed25519"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--host-request-b64", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.host_request_b64 is not None:
        controller_args = (
            args.controller_manifest,
            args.bot_precommit_manifest,
            args.webapp_precommit_manifest,
            args.bot_role_material,
            args.webapp_role_material,
            args.bot_source_snapshot_manifest,
            args.webapp_source_snapshot_manifest,
            args.confirm,
        )
        if args.apply or any(value is not None for value in controller_args):
            parser.error("host mode cannot be combined with controller arguments")
        return args
    required = (
        "controller_manifest",
        "bot_precommit_manifest",
        "webapp_precommit_manifest",
        "bot_role_material",
        "webapp_role_material",
        "bot_source_snapshot_manifest",
        "webapp_source_snapshot_manifest",
    )
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        parser.error("controller mode requires all role input arguments")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.host_request_b64 is not None:
            result = host_execute(args.host_request_b64)
        else:
            result = orchestrate(
                controller_manifest=args.controller_manifest,
                bot_precommit_manifest=args.bot_precommit_manifest,
                webapp_precommit_manifest=args.webapp_precommit_manifest,
                bot_role_material=args.bot_role_material,
                webapp_role_material=args.webapp_role_material,
                bot_source_snapshot_manifest=args.bot_source_snapshot_manifest,
                webapp_source_snapshot_manifest=(
                    args.webapp_source_snapshot_manifest
                ),
                apply=args.apply,
                confirm=args.confirm,
                known_hosts=args.known_hosts,
                identity_file=args.identity_file,
            )
        print(_canonical_json(result).decode("ascii"))
        return 0
    except PrecommitInputOrchestrationError:
        print(
            _canonical_json(
                {
                    "status": "blocked",
                    "error": (
                        "production-shadow precommit input orchestration "
                        "failed closed"
                    ),
                    "error_class": "PrecommitInputOrchestrationError",
                    "docker_invoked": False,
                    "service_mutated": False,
                    "current_mutated": False,
                    "source_mutated": False,
                    "object_storage_mutated": False,
                }
            ).decode("ascii")
        )
        return 2
    except Exception:
        print(
            _canonical_json(
                {
                    "status": "blocked",
                    "error": (
                        "production-shadow precommit input orchestration "
                        "failed closed"
                    ),
                    "error_class": "PrecommitInputOrchestrationError",
                    "docker_invoked": False,
                    "service_mutated": False,
                    "current_mutated": False,
                    "source_mutated": False,
                    "object_storage_mutated": False,
                }
            ).decode("ascii")
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
