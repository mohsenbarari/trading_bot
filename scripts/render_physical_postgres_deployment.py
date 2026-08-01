#!/usr/bin/env python3
"""Validate and render the default-off physical PostgreSQL deployment files.

This is intentionally not a deployment command.  It never imports Docker,
subprocess, SSH, an HTTP client, or an Object Storage SDK.  By default it only
checks the root-owned manifest, local adapter binaries, and local adapter
attestations.  ``--render`` writes a fresh root-owned generated tree but still
does not launch anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from core.physical_postgres_deployment_scaffold import (  # noqa: E402
    AdapterInstallation,
    AdapterInstallationInspector,
    PhysicalPostgresDeploymentError,
    PhysicalPostgresDeploymentManifest,
    RenderedPhysicalPostgresDeployment,
    canonical_json_bytes,
    parse_physical_postgres_deployment_manifest,
    render_physical_postgres_deployment,
    validate_physical_postgres_deployment_manifest,
    verify_physical_postgres_adapter_installations,
)


__all__ = (
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_RENDER_ROOT",
    "FilesystemAdapterInstallationInspector",
    "PhysicalPostgresDeploymentCliError",
    "load_root_only_manifest",
    "load_templates",
    "main",
    "materialize_fresh_render",
)


DEFAULT_MANIFEST_PATH = Path(
    "/etc/trading-bot/security/physical-postgres/deployment-manifest.json"
)
DEFAULT_RENDER_ROOT = Path("/etc/trading-bot/physical-postgres/rendered")
TEMPLATE_ROOT = SOURCE_ROOT / "deploy" / "physical-postgres"
MAX_MANIFEST_BYTES = 128 * 1024
MAX_ADAPTER_BINARY_BYTES = 128 * 1024 * 1024
MAX_ATTESTATION_BYTES = 128 * 1024


class PhysicalPostgresDeploymentCliError(RuntimeError):
    """A root-only local renderer input/output is unsafe or absent."""


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PhysicalPostgresDeploymentCliError(
            "physical PostgreSQL renderer must run as root"
        )


def _require_absolute(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise PhysicalPostgresDeploymentCliError(
            f"{label} path must be canonical and absolute"
        )
    return path


def _require_root_controlled_ancestors(path: Path, *, label: str) -> None:
    """Reject symlinked or operator-writable ancestors before an open/write."""

    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise PhysicalPostgresDeploymentCliError(
                f"{label} ancestor cannot be inspected"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise PhysicalPostgresDeploymentCliError(f"{label} ancestor is unsafe")


def _read_root_only_file(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    path = _require_absolute(Path(path), label=label)
    _require_root_controlled_ancestors(path.parent, label=label)
    try:
        before = path.lstat()
    except OSError as exc:
        raise PhysicalPostgresDeploymentCliError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= maximum_bytes
    ):
        raise PhysicalPostgresDeploymentCliError(
            f"{label} is not a bounded root-only mode-0600 regular file"
        )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        expected = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_nlink,
        )
        actual = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_gid,
            opened.st_nlink,
        )
        if (
            actual != expected
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise PhysicalPostgresDeploymentCliError(f"{label} changed while being opened")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(4096, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) > maximum_bytes
            or len(payload) != opened.st_size
            or (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mode,
                after.st_uid,
                after.st_gid,
                after.st_nlink,
            )
            != actual
        ):
            raise PhysicalPostgresDeploymentCliError(f"{label} changed while being read")
        return bytes(payload)
    except PhysicalPostgresDeploymentCliError:
        raise
    except OSError as exc:
        raise PhysicalPostgresDeploymentCliError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_root_only_manifest(
    path: Path = DEFAULT_MANIFEST_PATH,
) -> PhysicalPostgresDeploymentManifest:
    """Read one exact private manifest; callers cannot select a loose env file."""

    raw = _read_root_only_file(path, label="physical PostgreSQL manifest", maximum_bytes=MAX_MANIFEST_BYTES)
    return validate_physical_postgres_deployment_manifest(
        parse_physical_postgres_deployment_manifest(raw)
    )


class FilesystemAdapterInstallationInspector(AdapterInstallationInspector):
    """Read-only root-owned binary/attestation inspector; never executes them."""

    @staticmethod
    def _sha256_file(path: Path, *, maximum_bytes: int, label: str) -> str:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != 0
                or opened.st_nlink != 1
                or not 1 <= opened.st_size <= maximum_bytes
            ):
                raise PhysicalPostgresDeploymentCliError(f"{label} is unsafe")
            digest = hashlib.sha256()
            consumed = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - consumed))
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > maximum_bytes:
                    raise PhysicalPostgresDeploymentCliError(f"{label} is too large")
                digest.update(chunk)
            if consumed != opened.st_size:
                raise PhysicalPostgresDeploymentCliError(f"{label} changed while being read")
            return digest.hexdigest()
        except PhysicalPostgresDeploymentCliError:
            raise
        except OSError as exc:
            raise PhysicalPostgresDeploymentCliError(f"{label} cannot be read") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def inspect(self, *, adapter: Any) -> AdapterInstallation:
        binary_path = _require_absolute(Path(adapter.binary_path), label="adapter binary")
        _require_root_controlled_ancestors(binary_path.parent, label="adapter binary")
        try:
            binary_metadata = binary_path.lstat()
        except OSError as exc:
            raise PhysicalPostgresDeploymentCliError("adapter binary cannot be inspected") from exc
        if (
            stat.S_ISLNK(binary_metadata.st_mode)
            or not stat.S_ISREG(binary_metadata.st_mode)
            or binary_metadata.st_uid != 0
            or binary_metadata.st_nlink != 1
            or stat.S_IMODE(binary_metadata.st_mode) != 0o755
        ):
            raise PhysicalPostgresDeploymentCliError(
                "adapter binary must be a root-owned mode-0755 regular file"
            )
        attestation_path = _require_absolute(
            Path(adapter.attestation_path), label="adapter installation attestation"
        )
        _require_root_controlled_ancestors(
            attestation_path.parent, label="adapter installation attestation"
        )
        try:
            attestation_metadata = attestation_path.lstat()
        except OSError as exc:
            raise PhysicalPostgresDeploymentCliError(
                "adapter installation attestation cannot be inspected"
            ) from exc
        if (
            stat.S_ISLNK(attestation_metadata.st_mode)
            or not stat.S_ISREG(attestation_metadata.st_mode)
            or attestation_metadata.st_uid != 0
            or attestation_metadata.st_nlink != 1
            or stat.S_IMODE(attestation_metadata.st_mode) != 0o600
        ):
            raise PhysicalPostgresDeploymentCliError(
                "adapter installation attestation must be root-only mode-0600"
            )
        return AdapterInstallation(
            binary_path=str(binary_path),
            binary_sha256=self._sha256_file(
                binary_path, maximum_bytes=MAX_ADAPTER_BINARY_BYTES, label="adapter binary"
            ),
            installation_attestation_sha256=self._sha256_file(
                attestation_path,
                maximum_bytes=MAX_ATTESTATION_BYTES,
                label="adapter installation attestation",
            ),
            owner_uid=binary_metadata.st_uid,
            mode=stat.S_IMODE(binary_metadata.st_mode),
            regular_file=stat.S_ISREG(binary_metadata.st_mode),
            ancestors_root_controlled=True,
        )


def load_templates() -> dict[str, str]:
    """Load the fixed repository templates, not caller-provided templates."""

    names = (
        "primary-postgresql.conf.template",
        "primary-pg_hba.conf.template",
        "primary-pg_ident.conf.template",
        "standby-postgresql.conf.template",
        "standby-pg_hba.conf.template",
        "docker-compose.primary.yml.template",
        "docker-compose.standby.yml.template",
    )
    templates: dict[str, str] = {}
    for name in names:
        path = TEMPLATE_ROOT / name
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PhysicalPostgresDeploymentCliError(
                f"fixed deployment template cannot be read: {name}"
            ) from exc
        if not text.endswith("\n") or b"\x00" in raw:
            raise PhysicalPostgresDeploymentCliError(
                f"fixed deployment template is malformed: {name}"
            )
        templates[name] = text
    return templates


def _require_fresh_root_only_directory(path: Path, *, label: str, postgres_gid: int) -> None:
    path = _require_absolute(path, label=label)
    _require_root_controlled_ancestors(path.parent, label=label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PhysicalPostgresDeploymentCliError(
            f"{label} must be pre-created as a root-only directory"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PhysicalPostgresDeploymentCliError(
            f"{label} must be a root-owned mode-0700 directory"
        )
    try:
        if any(path.iterdir()):
            raise PhysicalPostgresDeploymentCliError(f"{label} must be empty")
    except OSError as exc:
        raise PhysicalPostgresDeploymentCliError(f"{label} cannot be inspected") from exc
    try:
        os.chown(path, 0, postgres_gid)
        os.chmod(path, 0o750)
    except OSError as exc:
        raise PhysicalPostgresDeploymentCliError(
            f"{label} cannot be assigned to the attested PostgreSQL group"
        ) from exc


def materialize_fresh_render(
    rendered: RenderedPhysicalPostgresDeployment,
    *,
    root: Path = DEFAULT_RENDER_ROOT,
) -> None:
    """Write one fresh, non-overwriting root:attested-pg-group config tree."""

    _require_fresh_root_only_directory(
        root,
        label="physical PostgreSQL render root",
        postgres_gid=rendered.postgres_runtime_gid,
    )
    postgres_gid = rendered.postgres_runtime_gid
    created_directories: set[Path] = set()
    for relative_path, payload in rendered.files:
        target = root / relative_path
        if target.parent != root and target.parent not in created_directories:
            parent = target.parent
            try:
                parent.mkdir(mode=0o750)
            except FileExistsError as exc:
                raise PhysicalPostgresDeploymentCliError(
                    "physical PostgreSQL render subdirectory already exists"
                ) from exc
            os.chown(parent, 0, postgres_gid)
            os.chmod(parent, 0o750)
            metadata = parent.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != postgres_gid
                or stat.S_IMODE(metadata.st_mode) != 0o750
            ):
                raise PhysicalPostgresDeploymentCliError(
                    "physical PostgreSQL render subdirectory is unsafe"
                )
            created_directories.add(parent)
        descriptor: int | None = None
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o640,
            )
            written = 0
            while written < len(payload):
                written += os.write(descriptor, payload[written:])
            os.fchown(descriptor, 0, postgres_gid)
            os.fchmod(descriptor, 0o640)
            os.fsync(descriptor)
        except OSError as exc:
            raise PhysicalPostgresDeploymentCliError(
                "physical PostgreSQL render file cannot be safely written"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--render",
        action="store_true",
        help="materialize only a fresh root-owned default-off tree; never launch it",
    )
    return parser


def _result(*, status: str, **fields: Any) -> str:
    return json.dumps({"status": status, **fields}, sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _require_root()
        manifest = load_root_only_manifest()
        verified = verify_physical_postgres_adapter_installations(
            manifest, inspector=FilesystemAdapterInstallationInspector()
        )
        rendered = render_physical_postgres_deployment(
            manifest, verified_adapters=verified, templates=load_templates()
        )
        if arguments.render:
            materialize_fresh_render(rendered)
        print(
            _result(
                status="rendered-default-off-not-launch-authorized",
                rendered=bool(arguments.render),
                manifest_lock_sha256=rendered.manifest_lock_sha256,
                next_required_action="reviewed root-only execution coordinator",
            )
        )
        return 0
    except (PhysicalPostgresDeploymentError, PhysicalPostgresDeploymentCliError) as exc:
        print(
            _result(
                status="blocked",
                error=str(exc),
                error_class=type(exc).__name__,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
