"""Root-only, default-off exact-argv runner for the WA-FI helper container.

This installed-adapter component implements only the existing
``PhysicalWaFiPostgresHelperContainerRunner`` protocol. It is not a
full-matrix step, launch authority, PostgreSQL control plane, Object Storage
client, SSH client, or promotion mechanism. Importing and constructing this
runner never executes Docker or reads a host file. A process can be started
only by :meth:`run` after an explicit enable flag, an effective-root check,
strict pinned invocation validation, and local binary/path checks.

The helper contract already builds a deterministic Docker argv. This adapter
reconstructs that exact argv from independently pinned local configuration and
the immutable invocation before passing it to ``subprocess.run`` without a
shell. It deliberately returns only the existing redaction-safe runner result:
timeout, OS launch failure, and a Docker non-zero status are represented as
safe non-zero exit codes for the caller-owned helper bridge to fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wa_fi_postgres_helper_container import (
    FIXED_WA_FI_POSTGRES_HELPER_DOCKER_BINARY,
    PhysicalWaFiPostgresHelperContainerInvocation,
    PhysicalWaFiPostgresHelperContainerRunnerResult,
)


__all__ = (
    "FIXED_WA_FI_POSTGRES_HELPER_DOCKER_RUNNER_TIMEOUT_SECONDS",
    "PhysicalWaFiPostgresHelperContainerDockerRunner",
    "PhysicalWaFiPostgresHelperContainerDockerRunnerConfig",
    "PhysicalWaFiPostgresHelperContainerDockerRunnerError",
)


FIXED_WA_FI_POSTGRES_HELPER_DOCKER_RUNNER_TIMEOUT_SECONDS = 120
_RUNNER_TIMEOUT_EXIT_CODE = 124
_RUNNER_OSERROR_EXIT_CODE = 125
_MAX_DOCKER_BINARY_BYTES = 128 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_HELPER_OUTPUT_NAME_RE = re.compile(r"^pg-basebackup-helper-[0-9a-f]{32}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IMAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/-]{1,255}@sha256:[0-9a-f]{64}$",
    re.ASCII,
)
_VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$", re.ASCII)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SAFE_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_SHELL_METACHARACTERS = frozenset(" \t\r\n\x00'\"`$|&;<>\\(){}[]*?!")


class PhysicalWaFiPostgresHelperContainerDockerRunnerError(RuntimeError):
    """A local exact-argv Docker runner policy check failed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaFiPostgresHelperContainerDockerRunnerConfig:
    """Root-local pins required before this runner may execute Docker.

    The default is disabled. No setting here is a launch authorization: the
    separate helper bridge still owns request/cutover/term authorization and
    treats every non-zero returned status as a failed capture.
    """

    enabled: bool = False
    docker_binary: Path = FIXED_WA_FI_POSTGRES_HELPER_DOCKER_BINARY
    docker_binary_sha256: str | None = None
    helper_image: str | None = None
    socket_volume: str | None = None
    helper_uid: int | None = None
    helper_gid: int | None = None


def _fail(code: str) -> None:
    raise PhysicalWaFiPostgresHelperContainerDockerRunnerError(code)


def _safe_sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _safe_id(value: object, *, code: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _safe_path(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail(code)
    if any(
        not component
        or component in {".", ".."}
        or _SAFE_PATH_COMPONENT_RE.fullmatch(component) is None
        for component in value.parts[1:]
    ):
        _fail(code)
    return value


def _safe_helper_identity(value: object, *, code: str) -> int:
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        _fail(code)
    return value


def _safe_image(value: object, *, code: str) -> str:
    if type(value) is not str or _IMAGE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _safe_volume(value: object, *, code: str) -> str:
    if type(value) is not str or _VOLUME_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _assert_no_shell_metacharacters(arguments: tuple[str, ...]) -> None:
    if type(arguments) is not tuple or not arguments:
        _fail("HELPER_DOCKER_RUNNER_ARGUMENTS_INVALID")
    for value in arguments:
        if type(value) is not str or not value or any(
            character in _SHELL_METACHARACTERS for character in value
        ):
            _fail("HELPER_DOCKER_RUNNER_ARGUMENTS_INVALID")


def _invocation_sha256(invocation: PhysicalWaFiPostgresHelperContainerInvocation) -> str:
    """Recompute the helper contract's canonical immutable invocation hash."""

    try:
        canonical = canonical_json_bytes(
            {
                "docker_binary": str(invocation.docker_binary),
                "docker_binary_sha256": invocation.docker_binary_sha256,
                "helper_image": invocation.helper_image,
                "arguments": list(invocation.arguments),
                "environment": [list(item) for item in invocation.environment],
                "capture_output_root": str(invocation.capture_output_root),
                "helper_output_directory": str(invocation.helper_output_directory),
                "helper_uid": invocation.helper_uid,
                "helper_gid": invocation.helper_gid,
                "configuration_sha256": invocation.configuration_sha256,
                "installation_attestation_sha256": invocation.installation_attestation_sha256,
                "capture_configuration_sha256": invocation.capture_configuration_sha256,
                "deployment_manifest_lock_sha256": invocation.deployment_manifest_lock_sha256,
                "local_base_backup_auth_preflight_sha256": invocation.local_base_backup_auth_preflight_sha256,
                "postgres_runtime_identity_attestation_sha256": invocation.postgres_runtime_identity_attestation_sha256,
                "writer_epoch": invocation.writer_epoch,
                "writer_lease_id": invocation.writer_lease_id,
                "witness_transition_id": invocation.witness_transition_id,
                "witnessed_term_proof_sha256": invocation.witnessed_term_proof_sha256,
            }
        )
    except (AttributeError, TypeError, ValueError):
        _fail("HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
    return hashlib.sha256(canonical).hexdigest()


def _secure_docker_binary_sha256(path: Path) -> str:
    """Hash one stable root-owned executable using a non-following descriptor."""

    if not hasattr(os, "O_NOFOLLOW"):
        _fail("HELPER_DOCKER_RUNNER_PLATFORM_UNSUPPORTED")
    descriptor = -1
    try:
        before = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) & 0o022
            or not (opened.st_mode & 0o111)
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
            or opened.st_size < 1
            or opened.st_size > _MAX_DOCKER_BINARY_BYTES
        ):
            _fail("HELPER_DOCKER_RUNNER_BINARY_UNSAFE")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_DOCKER_BINARY_BYTES:
                _fail("HELPER_DOCKER_RUNNER_BINARY_UNSAFE")
            digest.update(chunk)
        after = os.fstat(descriptor)
        path_after = os.lstat(path)
        if (
            total != before.st_size
            or (after.st_dev, after.st_ino, after.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
            or (path_after.st_dev, path_after.st_ino, path_after.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
        ):
            _fail("HELPER_DOCKER_RUNNER_BINARY_UNSAFE")
        return digest.hexdigest()
    except PhysicalWaFiPostgresHelperContainerDockerRunnerError:
        raise
    except OSError:
        _fail("HELPER_DOCKER_RUNNER_BINARY_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_capture_paths(
    invocation: PhysicalWaFiPostgresHelperContainerInvocation,
    *,
    helper_uid: int,
    helper_gid: int,
) -> None:
    capture_root = _safe_path(
        invocation.capture_output_root,
        code="HELPER_DOCKER_RUNNER_CAPTURE_PATH_INVALID",
    )
    output = _safe_path(
        invocation.helper_output_directory,
        code="HELPER_DOCKER_RUNNER_CAPTURE_PATH_INVALID",
    )
    if output.parent != capture_root or _HELPER_OUTPUT_NAME_RE.fullmatch(output.name) is None:
        _fail("HELPER_DOCKER_RUNNER_CAPTURE_PATH_INVALID")
    try:
        root_metadata = os.lstat(capture_root)
        output_metadata = os.lstat(output)
        root_resolved = capture_root.resolve(strict=True)
        output_resolved = output.resolve(strict=True)
        with os.scandir(output_resolved) as entries:
            if any(entries):
                _fail("HELPER_DOCKER_RUNNER_CAPTURE_PATH_INVALID")
    except PhysicalWaFiPostgresHelperContainerDockerRunnerError:
        raise
    except OSError:
        _fail("HELPER_DOCKER_RUNNER_CAPTURE_PATH_INVALID")
    if (
        root_resolved != capture_root
        or output_resolved != output
        or stat.S_ISLNK(root_metadata.st_mode)
        or stat.S_ISLNK(output_metadata.st_mode)
        or not stat.S_ISDIR(root_metadata.st_mode)
        or not stat.S_ISDIR(output_metadata.st_mode)
        or root_metadata.st_uid != 0
        or root_metadata.st_gid != 0
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
        or output_metadata.st_uid != helper_uid
        or output_metadata.st_gid != helper_gid
        or stat.S_IMODE(output_metadata.st_mode) != 0o700
    ):
        _fail("HELPER_DOCKER_RUNNER_CAPTURE_PATH_INVALID")


def _nonzero_exit_code(value: object) -> int:
    if type(value) is not int:
        return _RUNNER_OSERROR_EXIT_CODE
    if value == 0:
        return 0
    if value < 0 or value > 125:
        return _RUNNER_OSERROR_EXIT_CODE
    return value


class PhysicalWaFiPostgresHelperContainerDockerRunner:
    """Protocol implementation that executes only one fully pinned Docker argv."""

    def __init__(
        self,
        config: PhysicalWaFiPostgresHelperContainerDockerRunnerConfig | None = None,
    ) -> None:
        if config is None:
            config = PhysicalWaFiPostgresHelperContainerDockerRunnerConfig()
        if type(config) is not PhysicalWaFiPostgresHelperContainerDockerRunnerConfig:
            _fail("HELPER_DOCKER_RUNNER_CONFIG_INVALID")
        # Constructor intentionally does not inspect euid, files, Docker, or
        # the network. All host interaction is deferred to the explicit run.
        self._config = config

    def _enabled_config(self) -> tuple[Path, str, str, str, int, int]:
        config = self._config
        if config.enabled is False:
            _fail("HELPER_DOCKER_RUNNER_DISABLED")
        if config.enabled is not True:
            _fail("HELPER_DOCKER_RUNNER_CONFIG_INVALID")
        docker_binary = _safe_path(
            config.docker_binary,
            code="HELPER_DOCKER_RUNNER_CONFIG_INVALID",
        )
        return (
            docker_binary,
            _safe_sha256(config.docker_binary_sha256, code="HELPER_DOCKER_RUNNER_CONFIG_INVALID"),
            _safe_image(config.helper_image, code="HELPER_DOCKER_RUNNER_CONFIG_INVALID"),
            _safe_volume(config.socket_volume, code="HELPER_DOCKER_RUNNER_CONFIG_INVALID"),
            _safe_helper_identity(config.helper_uid, code="HELPER_DOCKER_RUNNER_CONFIG_INVALID"),
            _safe_helper_identity(config.helper_gid, code="HELPER_DOCKER_RUNNER_CONFIG_INVALID"),
        )

    def _validate_invocation(
        self,
        invocation: object,
        *,
        docker_binary: Path,
        docker_binary_sha256: str,
        helper_image: str,
        socket_volume: str,
        helper_uid: int,
        helper_gid: int,
    ) -> PhysicalWaFiPostgresHelperContainerInvocation:
        if type(invocation) is not PhysicalWaFiPostgresHelperContainerInvocation:
            _fail("HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
        if invocation.environment != ():
            _fail("HELPER_DOCKER_RUNNER_ENVIRONMENT_FORBIDDEN")
        if (
            invocation.docker_binary != docker_binary
            or invocation.docker_binary_sha256 != docker_binary_sha256
            or invocation.helper_image != helper_image
            or invocation.helper_uid != helper_uid
            or invocation.helper_gid != helper_gid
        ):
            _fail("HELPER_DOCKER_RUNNER_PIN_MISMATCH")
        _safe_path(invocation.docker_binary, code="HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
        _safe_image(invocation.helper_image, code="HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
        for value in (
            invocation.docker_binary_sha256,
            invocation.configuration_sha256,
            invocation.installation_attestation_sha256,
            invocation.capture_configuration_sha256,
            invocation.deployment_manifest_lock_sha256,
            invocation.local_base_backup_auth_preflight_sha256,
            invocation.postgres_runtime_identity_attestation_sha256,
            invocation.witnessed_term_proof_sha256,
            invocation.invocation_sha256,
        ):
            _safe_sha256(value, code="HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
        if type(invocation.writer_epoch) is not int or invocation.writer_epoch < 1:
            _fail("HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
        _safe_id(invocation.writer_lease_id, code="HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
        _safe_id(invocation.witness_transition_id, code="HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
        _validate_capture_paths(invocation, helper_uid=helper_uid, helper_gid=helper_gid)
        expected_arguments = (
            str(docker_binary),
            "--context=default",
            "run",
            "--pull=never",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit=64",
            "--user=" + str(helper_uid) + ":" + str(helper_gid),
            "--entrypoint=pg_basebackup",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--env=PGPASSFILE=/dev/null",
            "--mount",
            "type=volume,src=" + socket_volume + ",dst=/var/run/postgresql,readonly",
            "--mount",
            "type=bind,src=" + str(invocation.helper_output_directory) + ",dst=/capture",
            helper_image,
            "--host=/var/run/postgresql",
            "--port=5432",
            "--username=physical_backup",
            "--no-password",
            "--format=tar",
            "--wal-method=none",
            "--checkpoint=fast",
            "--pgdata=/capture",
        )
        _assert_no_shell_metacharacters(expected_arguments)
        if invocation.arguments != expected_arguments:
            _fail("HELPER_DOCKER_RUNNER_ARGUMENTS_INVALID")
        _assert_no_shell_metacharacters(invocation.arguments)
        if _invocation_sha256(invocation) != invocation.invocation_sha256:
            _fail("HELPER_DOCKER_RUNNER_INVOCATION_INVALID")
        return invocation

    def run(
        self,
        *,
        invocation: PhysicalWaFiPostgresHelperContainerInvocation,
    ) -> PhysicalWaFiPostgresHelperContainerRunnerResult:
        """Run the one exact pinned argv, returning a redaction-safe status only."""

        docker_binary, expected_hash, helper_image, socket_volume, helper_uid, helper_gid = (
            self._enabled_config()
        )
        if os.geteuid() != 0:
            _fail("HELPER_DOCKER_RUNNER_ROOT_REQUIRED")
        verified = self._validate_invocation(
            invocation,
            docker_binary=docker_binary,
            docker_binary_sha256=expected_hash,
            helper_image=helper_image,
            socket_volume=socket_volume,
            helper_uid=helper_uid,
            helper_gid=helper_gid,
        )
        if _secure_docker_binary_sha256(docker_binary) != expected_hash:
            _fail("HELPER_DOCKER_RUNNER_BINARY_HASH_MISMATCH")
        try:
            result = subprocess.run(
                list(verified.arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                cwd="/",
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                shell=False,
                timeout=FIXED_WA_FI_POSTGRES_HELPER_DOCKER_RUNNER_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return PhysicalWaFiPostgresHelperContainerRunnerResult(
                exit_code=_RUNNER_TIMEOUT_EXIT_CODE
            )
        except OSError:
            return PhysicalWaFiPostgresHelperContainerRunnerResult(
                exit_code=_RUNNER_OSERROR_EXIT_CODE
            )
        return PhysicalWaFiPostgresHelperContainerRunnerResult(
            exit_code=_nonzero_exit_code(getattr(result, "returncode", None))
        )
