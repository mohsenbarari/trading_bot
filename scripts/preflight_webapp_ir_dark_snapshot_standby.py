#!/usr/bin/env python3
"""Collect and verify the scoped WA-IR dark-snapshot Docker/systemd boundary.

This command intentionally has no activation path: it never invokes Compose,
starts or stops a container, changes a systemd unit, contacts a network, or
grants promotion/writer/execution authority.  It only observes the one
selected snapshot database and the two fixed local systemd units before
passing the redacted observation to the pure core verifier.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.webapp_ir_dark_snapshot_preflight import (  # noqa: E402
    WebappIrDarkSnapshotPreflightError,
    WebappIrDarkSnapshotPreflightObservation,
    verify_webapp_ir_dark_snapshot_preflight,
)


_MAX_ENV_BYTES = 64 * 1024
_CONTAINER_RE = re.compile(r"^trading_bot_wa_ir_snapshot_db_[a-z0-9][a-z0-9_-]{0,95}$", re.ASCII)
_PROMOTION_UNIT = "trading-bot-production-writer-ir-promotion-watch.service"
_REFRESH_TIMER_UNIT = "webapp-ir-snapshot-refresh.timer"
_DOCKER = ("/usr/bin/docker", "--host", "unix:///var/run/docker.sock")
_DOCKER_PS = (*_DOCKER, "ps", "--all", "--format", "{{.Names}}")
_DOCKER_INSPECT_STATE = (*_DOCKER, "inspect", "--format", "{{json .State}}")
_DOCKER_INSPECT_NETWORK = (*_DOCKER, "inspect", "--format", "{{.HostConfig.NetworkMode}}")
_DOCKER_INSPECT_PORTS = (*_DOCKER, "inspect", "--format", "{{json .NetworkSettings.Ports}}")
_SYSTEMCTL_ACTIVE = ("systemctl", "is-active")
_SYSTEMCTL_ENABLED = ("systemctl", "is-enabled")


class DarkSnapshotHostPreflightError(RuntimeError):
    """The collected Docker/systemd scope cannot prove snapshot-only posture."""


def _secure_read(path: Path) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        raise DarkSnapshotHostPreflightError("secure standby env read requires O_NOFOLLOW")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | no_follow,
        )
    except OSError as exc:
        raise DarkSnapshotHostPreflightError("cannot securely open standby env") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_nlink != 1
            or not 0 < before.st_size <= _MAX_ENV_BYTES
        ):
            raise DarkSnapshotHostPreflightError("standby env is not a root-only regular file")
        value = os.read(descriptor, _MAX_ENV_BYTES + 1)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if len(value) > _MAX_ENV_BYTES or any(
            getattr(before, field) != getattr(after, field) for field in fields
        ):
            raise DarkSnapshotHostPreflightError("standby env changed while being read")
        return value
    finally:
        os.close(descriptor)


def _selected_snapshot_container(path: Path) -> str:
    try:
        text = _secure_read(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DarkSnapshotHostPreflightError("standby env is not UTF-8") from exc
    selected: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DarkSnapshotHostPreflightError("standby env has an invalid line")
        key, value = line.split("=", 1)
        if key == "WA_IR_SNAPSHOT_DB_CONTAINER":
            if selected is not None:
                raise DarkSnapshotHostPreflightError("standby env repeats WA_IR_SNAPSHOT_DB_CONTAINER")
            selected = value.strip()
    if selected is None or _CONTAINER_RE.fullmatch(selected) is None:
        raise DarkSnapshotHostPreflightError("standby env has no safe selected snapshot container")
    return selected


def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run an allowlisted observation command without shell expansion."""

    try:
        return subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "HOME": "/nonexistent",
                "DOCKER_CONFIG": "/nonexistent",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DarkSnapshotHostPreflightError("host observation command could not run") from exc


def _docker_output(command: Sequence[str]) -> str:
    result = run_command(command)
    if result.returncode != 0:
        raise DarkSnapshotHostPreflightError("docker observation command failed")
    return result.stdout.strip()


def _unit_output(command: Sequence[str]) -> str:
    result = run_command(command)
    # systemctl deliberately returns nonzero for inactive/masked units; the
    # exact textual state remains the evidence, while execution failures do not.
    if result.returncode not in (0, 1, 3):
        raise DarkSnapshotHostPreflightError("systemctl observation command failed")
    output = result.stdout.strip()
    if not output or "\n" in output:
        raise DarkSnapshotHostPreflightError("systemctl observation output is invalid")
    return output


def _json_object(value: str, *, label: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise DarkSnapshotHostPreflightError(f"{label} is not JSON") from exc
    if not isinstance(parsed, dict):
        raise DarkSnapshotHostPreflightError(f"{label} is not an object")
    return parsed


def collect(standby_env: Path, *, now: datetime | None = None) -> WebappIrDarkSnapshotPreflightObservation:
    """Collect one fixed, redacted observation; no host state is mutated."""

    if os.geteuid() != 0:
        raise DarkSnapshotHostPreflightError("WA-IR dark snapshot preflight must run as root")
    selected = _selected_snapshot_container(standby_env)
    names = tuple(item for item in _docker_output(_DOCKER_PS).splitlines() if item)
    if names != (selected,):
        raise DarkSnapshotHostPreflightError("docker containers are not selected snapshot_db only")

    state = _json_object(_docker_output((*_DOCKER_INSPECT_STATE, selected)), label="docker state")
    health = state.get("Health")
    health_status = health.get("Status") if isinstance(health, dict) else None
    status = state.get("Status")
    if type(status) is not str or type(health_status) is not str:
        raise DarkSnapshotHostPreflightError("selected snapshot_db has no strict state and health")
    network_mode = _docker_output((*_DOCKER_INSPECT_NETWORK, selected))
    ports = _json_object(_docker_output((*_DOCKER_INSPECT_PORTS, selected)), label="docker ports")

    promotion_state = _unit_output((*_SYSTEMCTL_ACTIVE, _PROMOTION_UNIT))
    promotion_unit_state = _unit_output((*_SYSTEMCTL_ENABLED, _PROMOTION_UNIT))
    refresh_timer_state = _unit_output((*_SYSTEMCTL_ACTIVE, _REFRESH_TIMER_UNIT))
    refresh_timer_enabled_state = _unit_output((*_SYSTEMCTL_ENABLED, _REFRESH_TIMER_UNIT))
    return WebappIrDarkSnapshotPreflightObservation(
        services={"snapshot_db": {"state": status, "health": health_status}},
        network_mode=network_mode,
        published_ports=tuple(ports),
        promotion_state=promotion_state,
        promotion_unit_state=promotion_unit_state,
        refresh_timer_enabled=refresh_timer_enabled_state == "enabled",
        refresh_timer_state=refresh_timer_state,
        observed_at=now or datetime.now(timezone.utc),
    )


def execute(args: argparse.Namespace) -> dict[str, object]:
    observation = collect(Path(args.standby_env))
    result = verify_webapp_ir_dark_snapshot_preflight(
        observation,
        now=datetime.now(timezone.utc),
    )
    return {
        "status": "verified-non-authorizing",
        "observed_at": result.observation.observed_at.isoformat(),
        "services": {name: dict(value) for name, value in result.observation.services.items()},
        "network_mode": result.observation.network_mode,
        "published_ports": list(result.observation.published_ports),
        "promotion_state": result.observation.promotion_state,
        "promotion_unit_state": result.observation.promotion_unit_state,
        "refresh_timer_enabled": result.observation.refresh_timer_enabled,
        "refresh_timer_state": result.observation.refresh_timer_state,
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standby-env", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        result = execute(arguments)
    except (DarkSnapshotHostPreflightError, WebappIrDarkSnapshotPreflightError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
