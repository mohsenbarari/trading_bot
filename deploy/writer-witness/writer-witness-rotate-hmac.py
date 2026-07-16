#!/usr/bin/env python3
"""Safely stage, revoke, roll back, and finish one Witness HMAC rotation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from urllib.request import urlopen


RUNTIME_ENV = Path("/etc/trading-bot-witness/runtime.env")
CLIENT_DIR = Path("/root/writer-witness-client-material")
STATE_ROOT = Path("/var/lib/trading-bot-witness/hmac-rotation")
SITE_SETTINGS = {
    "webapp_fi": ("FI", "webapp-fi.env"),
    "webapp_ir": ("IR", "webapp-ir.env"),
}


class RotationError(RuntimeError):
    """A fail-closed rotation precondition or operation failed."""


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    if not path.is_file():
        raise RotationError(f"required environment file is missing: {path}")
    if path.stat().st_mode & 0o077:
        raise RotationError(f"environment file must be owner-only: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key in values:
            raise RotationError(f"invalid or duplicate setting in {path}")
        values[key] = value.strip()
    return lines, values


def _atomic_update_env(
    path: Path,
    *,
    changes: dict[str, str],
    removals: set[str] | None = None,
) -> None:
    lines, _ = _read_env(path)
    pending = dict(changes)
    remove = removals or set()
    rendered: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            rendered.append(raw_line)
            continue
        key = line.partition("=")[0].strip()
        if key in remove:
            continue
        if key in pending:
            rendered.append(f"{key}={pending.pop(key)}")
        else:
            rendered.append(raw_line)
    if pending:
        if rendered and rendered[-1]:
            rendered.append("")
        rendered.extend(f"{key}={value}" for key, value in pending.items())
    payload = ("\n".join(rendered).rstrip() + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.rotate-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_secret(source: Path, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.copy-",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with source.open("rb") as source_handle, os.fdopen(descriptor, "wb") as target_handle:
            descriptor = -1
            shutil.copyfileobj(source_handle, target_handle)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _write_metadata(path: Path, metadata: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_metadata(path: Path) -> dict[str, object]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RotationError("rotation metadata is missing or invalid") from exc
    if not isinstance(metadata, dict):
        raise RotationError("rotation metadata is invalid")
    return metadata


def _next_key_id(current: str) -> str:
    match = re.fullmatch(r"([a-z0-9-]+)-v([1-9][0-9]*)", current)
    if not match:
        raise RotationError("current key id must end in a positive -vN generation")
    candidate = f"{match.group(1)}-v{int(match.group(2)) + 1}"
    if len(candidate) > 64:
        raise RotationError("next key id exceeds the service limit")
    return candidate


def _site_keys(site: str) -> tuple[str, str, str, str, str]:
    suffix, client_name = SITE_SETTINGS[site]
    prefix = f"WRITER_WITNESS_SERVICE_WEBAPP_{suffix}"
    return (
        f"{prefix}_KEY_ID",
        f"{prefix}_SECRET",
        f"{prefix}_PREVIOUS_KEY_ID",
        f"{prefix}_PREVIOUS_SECRET",
        client_name,
    )


def _require_root() -> None:
    if os.geteuid() != 0:
        raise RotationError("rotation must run as root")


def _require_dark_state(expected_epoch: int) -> None:
    result = subprocess.run(
        [
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "-XAt",
            "-F",
            "|",
            "-d",
            "writer_witness",
            "-c",
            (
                "SELECT authority, writer_epoch, lease_status, "
                "COALESCE(holder_site, '') FROM webapp_writer_witness_state "
                "WHERE authority='webapp';"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != f"webapp|{expected_epoch}|vacant|":
        raise RotationError("witness is not in the explicitly expected vacant state")


def _restart_and_verify() -> None:
    subprocess.run(["systemctl", "restart", "writer-witness.service"], check=True)
    subprocess.run(
        ["systemctl", "is-active", "--quiet", "writer-witness.service"], check=True
    )
    last_error: Exception | None = None
    for _ in range(20):
        try:
            with urlopen("http://127.0.0.1:8011/health/ready", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # readiness is retried and reported without secrets
            last_error = exc
        time.sleep(0.25)
    raise RotationError("writer witness did not become ready after restart") from last_error


def _validate_pair(
    runtime: dict[str, str],
    client: dict[str, str],
    key_name: str,
    secret_name: str,
) -> None:
    if (
        not runtime.get(key_name)
        or len(runtime.get(secret_name, "").encode("utf-8")) < 32
        or client.get("WRITER_WITNESS_CLIENT_KEY_ID") != runtime[key_name]
        or client.get("WRITER_WITNESS_CLIENT_SECRET") != runtime[secret_name]
    ):
        raise RotationError("client material does not match the current service credential")


def prepare(
    site: str,
    expected_epoch: int,
    runtime_path: Path = RUNTIME_ENV,
    client_dir: Path = CLIENT_DIR,
    state_root: Path = STATE_ROOT,
) -> dict[str, object]:
    _require_root()
    _require_dark_state(expected_epoch)
    key_name, secret_name, previous_key_name, previous_secret_name, client_name = _site_keys(site)
    rotation_dir = state_root / site
    if rotation_dir.exists():
        raise RotationError("unfinished rotation state already exists")
    _, runtime = _read_env(runtime_path)
    _, client = _read_env(client_dir / client_name)
    if any(
        runtime.get(name)
        for name in (previous_key_name, previous_secret_name)
    ):
        raise RotationError("the selected site already has an overlap credential")
    _validate_pair(runtime, client, key_name, secret_name)
    old_key_id = runtime[key_name]
    new_key_id = _next_key_id(old_key_id)
    if new_key_id in runtime.values():
        raise RotationError("next key id collides with an existing setting")
    new_secret = secrets.token_hex(32)

    rotation_dir.mkdir(mode=0o700, parents=True)
    os.chmod(state_root, 0o700)
    _copy_secret(runtime_path, rotation_dir / "runtime.env.before")
    _copy_secret(client_dir / client_name, rotation_dir / "client.env.before")
    metadata: dict[str, object] = {
        "site": site,
        "expected_epoch": expected_epoch,
        "old_key_id": old_key_id,
        "new_key_id": new_key_id,
        "phase": "preparing",
    }
    _write_metadata(rotation_dir / "metadata.json", metadata)
    try:
        _atomic_update_env(
            runtime_path,
            changes={
                key_name: new_key_id,
                secret_name: new_secret,
                previous_key_name: old_key_id,
                previous_secret_name: runtime[secret_name],
            },
        )
        _atomic_update_env(
            client_dir / client_name,
            changes={
                "WRITER_WITNESS_CLIENT_KEY_ID": new_key_id,
                "WRITER_WITNESS_CLIENT_SECRET": new_secret,
            },
        )
        _restart_and_verify()
    except Exception:
        _copy_secret(rotation_dir / "runtime.env.before", runtime_path)
        _copy_secret(rotation_dir / "client.env.before", client_dir / client_name)
        _restart_and_verify()
        shutil.rmtree(rotation_dir)
        raise
    metadata["phase"] = "prepared"
    _write_metadata(rotation_dir / "metadata.json", metadata)
    return metadata


def revoke(
    site: str,
    expected_epoch: int,
    runtime_path: Path = RUNTIME_ENV,
    client_dir: Path = CLIENT_DIR,
    state_root: Path = STATE_ROOT,
) -> dict[str, object]:
    _require_root()
    _require_dark_state(expected_epoch)
    key_name, secret_name, previous_key_name, previous_secret_name, client_name = _site_keys(site)
    rotation_dir = state_root / site
    metadata = _load_metadata(rotation_dir / "metadata.json")
    if metadata.get("site") != site or metadata.get("phase") != "prepared":
        raise RotationError("rotation is not ready for revocation")
    _, runtime = _read_env(runtime_path)
    _, client = _read_env(client_dir / client_name)
    _, old_client = _read_env(rotation_dir / "client.env.before")
    if (
        runtime.get(key_name) != metadata.get("new_key_id")
        or runtime.get(previous_key_name) != metadata.get("old_key_id")
        or runtime.get(previous_secret_name) != old_client.get("WRITER_WITNESS_CLIENT_SECRET")
    ):
        raise RotationError("overlap state does not match the prepared rotation")
    _validate_pair(runtime, client, key_name, secret_name)
    _copy_secret(runtime_path, rotation_dir / "runtime.env.overlap")
    try:
        _atomic_update_env(
            runtime_path,
            changes={},
            removals={previous_key_name, previous_secret_name},
        )
        _restart_and_verify()
    except Exception:
        _copy_secret(rotation_dir / "runtime.env.overlap", runtime_path)
        _restart_and_verify()
        raise
    metadata["phase"] = "revoked"
    _write_metadata(rotation_dir / "metadata.json", metadata)
    return metadata


def rollback(
    site: str,
    expected_epoch: int,
    runtime_path: Path = RUNTIME_ENV,
    client_dir: Path = CLIENT_DIR,
    state_root: Path = STATE_ROOT,
) -> dict[str, object]:
    _require_root()
    _require_dark_state(expected_epoch)
    *_, client_name = _site_keys(site)
    rotation_dir = state_root / site
    metadata = _load_metadata(rotation_dir / "metadata.json")
    if metadata.get("site") != site or metadata.get("phase") not in {
        "prepared",
        "revoked",
    }:
        raise RotationError("rotation is not eligible for rollback")
    _copy_secret(rotation_dir / "runtime.env.before", runtime_path)
    _copy_secret(rotation_dir / "client.env.before", client_dir / client_name)
    _restart_and_verify()
    metadata["phase"] = "rolled_back"
    _write_metadata(rotation_dir / "metadata.json", metadata)
    return metadata


def finish(site: str, state_root: Path = STATE_ROOT) -> dict[str, object]:
    _require_root()
    rotation_dir = state_root / site
    metadata = _load_metadata(rotation_dir / "metadata.json")
    if metadata.get("site") != site or metadata.get("phase") not in {
        "revoked",
        "rolled_back",
    }:
        raise RotationError("rotation cannot finish before revocation or rollback")
    result = {
        "site": site,
        "old_key_id": metadata.get("old_key_id"),
        "new_key_id": metadata.get("new_key_id"),
        "phase": "finished",
    }
    shutil.rmtree(rotation_dir)
    if state_root.exists() and not any(state_root.iterdir()):
        state_root.rmdir()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "revoke", "rollback", "finish"))
    parser.add_argument("--site", choices=tuple(SITE_SETTINGS), required=True)
    parser.add_argument("--expected-epoch", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args.site, args.expected_epoch)
        elif args.action == "revoke":
            result = revoke(args.site, args.expected_epoch)
        elif args.action == "rollback":
            result = rollback(args.site, args.expected_epoch)
        else:
            result = finish(args.site)
    except (OSError, subprocess.SubprocessError, RotationError) as exc:
        raise SystemExit(f"writer witness HMAC rotation failed: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
