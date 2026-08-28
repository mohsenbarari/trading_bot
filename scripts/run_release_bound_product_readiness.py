#!/usr/bin/env python3
"""Run the exact release-bound Product readiness probe in one local container.

The wrapper is intentionally self-contained.  It imports only the Python
standard library, validates the installed control payload through descriptor
based reads, proves the Compose/container/image identity, and sends the exact
checked readiness script bytes through stdin to a fixed bootstrap inside the
Product container.
It never imports code from the invoking checkout or current working directory.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Iterator, Mapping, Sequence


CONFIRMATION = "run-release-bound-product-readiness"
CONTROL_MANIFEST = "control-payload.sha256"
READINESS_RELATIVE_PATH = "scripts/check_production_coin_inference_readiness.py"
CONTAINER_SNAPSHOT = "/app/runtime/product-estimator/latest-private-primary.json"
CONTAINER_MOUNTINFO = "/proc/self/mountinfo"
READINESS_CONFIRMATION = "check-production-coin-inference-readiness"
READINESS_CONTAINER_PATH = "/app/scripts/check_production_coin_inference_readiness.py"
# ``python3 -`` would expose ``__file__ == '<stdin>'`` and the delegated
# readiness script deliberately derives /app from its release path.  This
# fixed bootstrap preserves that path while the audited bytes still travel
# only through stdin; it also rechecks their manifest digest in-container.
READINESS_BOOTSTRAP = (
    "import hashlib,sys;"
    "expected=sys.argv.pop(1);"
    "payload=sys.stdin.buffer.read();"
    "hashlib.sha256(payload).hexdigest()==expected or sys.exit(73);"
    f"path={READINESS_CONTAINER_PATH!r};"
    "scope={'__name__':'__main__','__file__':path,'__package__':None};"
    "exec(compile(payload,path,'exec'),scope,scope)"
)
SCHEMA = "release_bound_product_readiness/1.0"
MAXIMUM_MANIFEST_BYTES = 2_000_000
MAXIMUM_SCRIPT_BYTES = 4_000_000
COMMAND_TIMEOUT_SECONDS = 180
DOCKER_BINARY = "/usr/bin/docker"

# The live Product container must remain LEGACY until the controller's final
# authority CAS.  Readiness therefore runs as an isolated process whose
# complete authority-sensitive environment is explicitly overridden for this
# one ``docker exec`` only.  Every item carries a value (never a host-inherited
# ``-e NAME``), keys are unique, and Docker does not mutate container config.
ISOLATED_PRIVATE_PRIMARY_ENV = (
    "PYTHONPATH=/app",
    "PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY",
    f"PRODUCT_ESTIMATOR_PRIVATE_PRIMARY_SNAPSHOT_PATH={CONTAINER_SNAPSHOT}",
    "PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS=120",
    "COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED=true",
    "COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED=true",
    "OFFER_MODEL_PRICE_GUARD_ENABLED=true",
    "COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED=false",
)

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MANIFEST_LINE = re.compile(r"([0-9a-f]{64})  (\./[A-Za-z0-9_./-]+)")

ROLE_RUNTIME = {
    "bot": {"container": "trading_bot_bot", "project": "trading_bot", "service": "bot"},
    "web": {"container": "trading_bot_app", "project": "current", "service": "app"},
}


class ReleaseBoundReadinessError(RuntimeError):
    """A stable, value-free release/readiness refusal."""


def _blocked(reason_code: str) -> bytes:
    return (
        json.dumps(
            {
                "schema": SCHEMA,
                "status": "BLOCKED",
                "reason_code": reason_code,
                "secrets_disclosed": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _stable_read_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
    require_owner: bool = True,
) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or (require_owner and before.st_uid != os.geteuid())
        or before.st_nlink != 1
        or bool(before.st_mode & 0o022)
        or before.st_size <= 0
        or before.st_size > maximum_bytes
    ):
        raise ReleaseBoundReadinessError("control_file_invalid")
    payload = bytearray()
    while len(payload) <= before.st_size:
        chunk = os.read(descriptor, min(1024 * 1024, before.st_size + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
    after = os.fstat(descriptor)
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if len(payload) != before.st_size or identity != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ReleaseBoundReadinessError("control_file_changed_during_read")
    return bytes(payload), before


def _open_directory(name: str | Path, *, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise ReleaseBoundReadinessError("control_root_unavailable") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or bool(metadata.st_mode & 0o022)
    ):
        os.close(descriptor)
        raise ReleaseBoundReadinessError("control_root_invalid")
    return descriptor


def _open_regular(name: str, *, dir_fd: int) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise ReleaseBoundReadinessError("control_file_unavailable") from exc


def _parse_manifest(payload: bytes) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ReleaseBoundReadinessError("control_manifest_invalid") from exc
    if not lines:
        raise ReleaseBoundReadinessError("control_manifest_invalid")
    entries: dict[str, str] = {}
    for line in lines:
        match = MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise ReleaseBoundReadinessError("control_manifest_invalid")
        relative = match.group(2)[2:]
        relative_path = Path(relative)
        if (
            relative in entries
            or relative_path.is_absolute()
            or not relative_path.parts
            or ".." in relative_path.parts
        ):
            raise ReleaseBoundReadinessError("control_manifest_invalid")
        entries[relative] = match.group(1)
    return entries


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _entry_matches(
    name: str,
    *,
    dir_fd: int,
    expected: os.stat_result,
) -> bool:
    try:
        observed = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_IFMT(observed.st_mode) == stat.S_IFMT(expected.st_mode)
        and (observed.st_dev, observed.st_ino) == (expected.st_dev, expected.st_ino)
    )


@contextmanager
def _release_readiness_payload(
    *,
    control_root: Path,
    release_sha: str,
    expected_manifest_sha256: str,
) -> Iterator[tuple[bytes, str]]:
    root_text = str(control_root)
    if (
        not control_root.is_absolute()
        or control_root.name != release_sha
        or os.path.realpath(root_text) != root_text
        or not HEX64.fullmatch(expected_manifest_sha256)
    ):
        raise ReleaseBoundReadinessError("control_root_invalid")
    root_descriptor = _open_directory(root_text)
    scripts_descriptor: int | None = None
    manifest_descriptor: int | None = None
    script_descriptor: int | None = None
    try:
        root_metadata = os.fstat(root_descriptor)
        if stat.S_IMODE(root_metadata.st_mode) != 0o700:
            raise ReleaseBoundReadinessError("control_root_invalid")

        manifest_descriptor = _open_regular(CONTROL_MANIFEST, dir_fd=root_descriptor)
        manifest, manifest_metadata = _stable_read_descriptor(
            manifest_descriptor, maximum_bytes=MAXIMUM_MANIFEST_BYTES
        )
        if sha256(manifest).hexdigest() != expected_manifest_sha256:
            raise ReleaseBoundReadinessError("control_manifest_digest_mismatch")
        entries = _parse_manifest(manifest)
        expected_script_sha256 = entries.get(READINESS_RELATIVE_PATH)
        if not expected_script_sha256:
            raise ReleaseBoundReadinessError("readiness_script_not_manifested")

        scripts_descriptor = _open_directory("scripts", dir_fd=root_descriptor)
        scripts_metadata = os.fstat(scripts_descriptor)
        script_descriptor = _open_regular(
            Path(READINESS_RELATIVE_PATH).name,
            dir_fd=scripts_descriptor,
        )
        script, script_metadata = _stable_read_descriptor(
            script_descriptor, maximum_bytes=MAXIMUM_SCRIPT_BYTES
        )
        if sha256(script).hexdigest() != expected_script_sha256:
            raise ReleaseBoundReadinessError("readiness_script_digest_mismatch")
        yield script, expected_script_sha256
        replacement_root = _open_directory(root_text)
        try:
            root_path_matches = (
                os.fstat(replacement_root).st_dev,
                os.fstat(replacement_root).st_ino,
            ) == (root_metadata.st_dev, root_metadata.st_ino)
        finally:
            os.close(replacement_root)
        if (
            not root_path_matches
            or not _entry_matches(
                CONTROL_MANIFEST,
                dir_fd=root_descriptor,
                expected=manifest_metadata,
            )
            or not _entry_matches(
                "scripts", dir_fd=root_descriptor, expected=scripts_metadata
            )
            or not _entry_matches(
                Path(READINESS_RELATIVE_PATH).name,
                dir_fd=scripts_descriptor,
                expected=script_metadata,
            )
            or _file_identity(os.fstat(manifest_descriptor))
            != _file_identity(manifest_metadata)
            or _file_identity(os.fstat(script_descriptor))
            != _file_identity(script_metadata)
        ):
            raise ReleaseBoundReadinessError("control_file_changed_during_execution")
    finally:
        if script_descriptor is not None:
            os.close(script_descriptor)
        if scripts_descriptor is not None:
            os.close(scripts_descriptor)
        if manifest_descriptor is not None:
            os.close(manifest_descriptor)
        os.close(root_descriptor)


def _json_list(payload: bytes, *, reason_code: str) -> list[Mapping[str, Any]]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBoundReadinessError(reason_code) from exc
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], Mapping)
    ):
        raise ReleaseBoundReadinessError(reason_code)
    return [value[0]]


def _run(
    command: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    if not command or command[0] != DOCKER_BINARY:
        raise ReleaseBoundReadinessError("docker_binary_invalid")
    try:
        return subprocess.run(
            list(command),
            check=False,
            stdin=subprocess.DEVNULL if input_bytes is None else None,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            cwd="/",
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseBoundReadinessError("docker_command_failed") from exc


def _container_identity(
    *,
    role: str,
    container: str,
    project: str,
    release_sha: str,
    release_tree: str,
    expected_image_id: str,
) -> tuple[str, str, str, int, int]:
    expected = ROLE_RUNTIME[role]
    if (
        not SAFE_NAME.fullmatch(container)
        or not SAFE_NAME.fullmatch(project)
        or container != expected["container"]
        or project != expected["project"]
    ):
        raise ReleaseBoundReadinessError("product_role_binding_invalid")

    inspected = _run(
        [DOCKER_BINARY, "inspect", "--type", "container", container]
    )
    if inspected.returncode != 0 or inspected.stderr.strip():
        raise ReleaseBoundReadinessError("product_container_inspect_failed")
    document = _json_list(
        inspected.stdout, reason_code="product_container_inspect_invalid"
    )[0]
    config = document.get("Config")
    state = document.get("State")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    image_id = str(document.get("Image") or "")
    started_at = str(state.get("StartedAt") or "") if isinstance(state, Mapping) else ""
    pid = state.get("Pid") if isinstance(state, Mapping) else None
    restart_count = document.get("RestartCount")
    if (
        not HEX64.fullmatch(str(document.get("Id") or ""))
        or document.get("Name") != f"/{container}"
        or not isinstance(state, Mapping)
        or state.get("Running") is not True
        or not started_at
        or isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or isinstance(restart_count, bool)
        or not isinstance(restart_count, int)
        or restart_count < 0
        or not isinstance(labels, Mapping)
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != expected["service"]
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
        or image_id != expected_image_id
    ):
        raise ReleaseBoundReadinessError("product_container_identity_mismatch")

    image_inspected = _run([DOCKER_BINARY, "image", "inspect", image_id])
    if image_inspected.returncode != 0 or image_inspected.stderr.strip():
        raise ReleaseBoundReadinessError("product_image_inspect_failed")
    image = _json_list(
        image_inspected.stdout, reason_code="product_image_inspect_invalid"
    )[0]
    image_config = image.get("Config")
    image_labels = (
        image_config.get("Labels") if isinstance(image_config, Mapping) else None
    )
    if (
        image.get("Id") != image_id
        or not isinstance(image_labels, Mapping)
        or image_labels.get("org.opencontainers.image.revision") != release_sha
        or image_labels.get("io.gold-trade.release.tree") != release_tree
    ):
        raise ReleaseBoundReadinessError("product_image_release_mismatch")
    return str(document["Id"]), image_id, started_at, pid, restart_count


def _single_readiness_json(
    payload: bytes,
    *,
    returncode: int,
    expected_snapshot_sha256: str,
) -> Mapping[str, Any]:
    # The delegated command has one stable JSON line.  Blank padding, logs or
    # a second document are evidence failure, not output to be filtered.
    if not payload or payload.count(b"\n") != 1 or not payload.endswith(b"\n"):
        raise ReleaseBoundReadinessError("readiness_output_not_single_json")
    line = payload[:-1]
    if not line or b"\n" in line or b"\r" in line:
        raise ReleaseBoundReadinessError("readiness_output_not_single_json")
    try:
        result = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBoundReadinessError("readiness_output_not_single_json") from exc
    if not isinstance(result, Mapping) or result.get("secrets_disclosed") is not False:
        raise ReleaseBoundReadinessError("readiness_output_invalid")
    if returncode == 0:
        expected_keys = {
            "status",
            "authority",
            "snapshot_digest",
            "snapshot_hash",
            "snapshot_version",
            "snapshot_age_seconds",
            "rate_cell_count",
            "required_source_input_trace_count",
            "source_input_trace_sha256",
            "mount_read_only",
            "enabled_flags",
            "secrets_disclosed",
        }
        enabled = result.get("enabled_flags")
        if (
            set(result) != expected_keys
            or result.get("status") != "READY"
            or result.get("authority") != "PRIVATE_PRIMARY"
            or result.get("snapshot_digest") != expected_snapshot_sha256
            or not HEX64.fullmatch(str(result.get("snapshot_hash") or ""))
            or isinstance(result.get("snapshot_version"), bool)
            or not isinstance(result.get("snapshot_version"), int)
            or result["snapshot_version"] < 1
            or result.get("rate_cell_count") != 14
            or result.get("required_source_input_trace_count") != 9
            or not HEX64.fullmatch(
                str(result.get("source_input_trace_sha256") or "")
            )
            or isinstance(result.get("snapshot_age_seconds"), bool)
            or not isinstance(result.get("snapshot_age_seconds"), (int, float))
            or not 0 <= float(result["snapshot_age_seconds"]) <= 120
            or result.get("mount_read_only") is not True
            or not isinstance(enabled, Mapping)
            or set(enabled) != {"preview", "selection", "guard"}
            or any(enabled.get(key) is not True for key in enabled)
        ):
            raise ReleaseBoundReadinessError("readiness_success_contract_invalid")
    elif returncode == 2:
        if (
            set(result) != {"status", "reason", "secrets_disclosed"}
            or result.get("status") != "BLOCKED"
            or not isinstance(result.get("reason"), str)
            or not result["reason"]
        ):
            raise ReleaseBoundReadinessError("readiness_failure_contract_invalid")
    else:
        raise ReleaseBoundReadinessError("readiness_process_failed")
    return result


def execute(args: argparse.Namespace) -> tuple[int, bytes]:
    if (
        args.confirm != CONFIRMATION
        or not HEX40.fullmatch(args.release_sha)
        or not HEX40.fullmatch(args.release_tree)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.expected_image_id)
        or not HEX64.fullmatch(args.expected_snapshot_sha256)
    ):
        raise ReleaseBoundReadinessError("invocation_contract_invalid")
    with _release_readiness_payload(
        control_root=Path(args.control_root),
        release_sha=args.release_sha,
        expected_manifest_sha256=args.expected_control_manifest_sha256,
    ) as (readiness_script, readiness_script_sha256):
        identity_before = _container_identity(
            role=args.role,
            container=args.container,
            project=args.project,
            release_sha=args.release_sha,
            release_tree=args.release_tree,
            expected_image_id=args.expected_image_id,
        )
        command = [
            DOCKER_BINARY,
            "exec",
            "-i",
        ]
        for variable in ISOLATED_PRIVATE_PRIMARY_ENV:
            command.extend(["-e", variable])
        command.extend(
            [
                identity_before[0],
                "python3",
                "-c",
                READINESS_BOOTSTRAP,
                readiness_script_sha256,
                "--environment",
                "production",
                "--production-confirmation",
                READINESS_CONFIRMATION,
                "private-primary-consumer",
                "--snapshot",
                CONTAINER_SNAPSHOT,
                "--expected-sha256",
                args.expected_snapshot_sha256,
                "--mountinfo",
                CONTAINER_MOUNTINFO,
            ]
        )
        completed = _run(command, input_bytes=readiness_script)
        identity_after = _container_identity(
            role=args.role,
            container=args.container,
            project=args.project,
            release_sha=args.release_sha,
            release_tree=args.release_tree,
            expected_image_id=args.expected_image_id,
        )
        if identity_after != identity_before:
            raise ReleaseBoundReadinessError(
                "product_container_changed_during_readiness"
            )
        if completed.stderr.strip():
            raise ReleaseBoundReadinessError("readiness_stderr_not_empty")
        _single_readiness_json(
            completed.stdout,
            returncode=completed.returncode,
            expected_snapshot_sha256=args.expected_snapshot_sha256,
        )
    return completed.returncode, completed.stdout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=tuple(ROLE_RUNTIME))
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree", required=True)
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--expected-control-manifest-sha256", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--expected-image-id", required=True)
    parser.add_argument("--expected-snapshot-sha256", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        returncode, payload = execute(args)
    except ReleaseBoundReadinessError as exc:
        returncode, payload = 2, _blocked(str(exc))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
