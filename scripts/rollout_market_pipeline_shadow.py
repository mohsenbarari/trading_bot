#!/usr/bin/env python3
"""Receiver-first PRIVATE_SHADOW rollout with exact-container rollback."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.prepare_market_pipeline_release import (
        DYNAMIC_VALUES,
        IMAGE_ID,
        parse_env,
        validate_source,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.prepare_market_pipeline_release import (
        DYNAMIC_VALUES,
        IMAGE_ID,
        parse_env,
        validate_source,
    )


CONFIRMATION = "rollout-production-market-pipeline-private-shadow"
SCHEMA = "market_pipeline_shadow_rollout/1.0"
RELEASE_SHA = re.compile(r"^[0-9a-f]{40}$")
ROLE_SERVICES = {
    "bot": (
        "market-fact-receiver",
        "market-store-adapter",
        "coin-estimator",
        "estimator-snapshot-sender",
    ),
    "web": (
        "estimator-snapshot-receiver",
        "market-processor",
        "market-fact-sync-worker",
    ),
}
CAPTURE_SERVICES = frozenset(
    {
        "market-capture-account1",
        "market-capture-account2",
        "market-capture-external",
    }
)


class RolloutError(RuntimeError):
    """A stable, non-sensitive rollout refusal."""


def _run(
    arguments: Sequence[str], *, label: str, allow_failure: bool = False
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode and not allow_failure:
        raise RolloutError(f"{label}_failed_rc_{result.returncode}")
    return result


def _text(arguments: Sequence[str], *, label: str) -> str:
    return _run(arguments, label=label).stdout.strip()


def _secure_parent(path: Path) -> None:
    parent = path.parent
    if (
        not parent.is_absolute()
        or parent in {Path("/"), Path("/root"), Path("/srv"), Path("/tmp")}
        or Path("/tmp") in parent.parents
        or "staging" in str(parent).lower()
        or parent.resolve(strict=False) != parent
    ):
        raise RolloutError("rollout_journal_parent_invalid")
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(parent, 0o700)
    info = parent.lstat()
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RolloutError("rollout_journal_parent_owner_mode_invalid")


def _write_journal(path: Path, payload: Mapping[str, Any]) -> None:
    _secure_parent(path)
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


def _read_journal(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise RolloutError("rollout_journal_owner_mode_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RolloutError("rollout_journal_invalid") from exc
    return payload


def _validate_env(role: str, env_file: Path, release_sha: str, image_id: str) -> dict[str, str]:
    values = parse_env(env_file, secure_input=True)
    source = {key: value for key, value in values.items() if key not in DYNAMIC_VALUES}
    validate_source(role, source)
    expected = {
        "MARKET_PIPELINE_RELEASE_SHA": release_sha,
        "MARKET_PIPELINE_IMAGE": image_id,
        "MARKET_PIPELINE_MODE": "live",
        "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise RolloutError("rollout_release_env_identity_mismatch")
    return values


def _compose(release_root: Path, env_file: Path, role: str) -> list[str]:
    return [
        "docker", "compose", "--env-file", str(env_file),
        "-f", str(release_root / "deploy/market-data/compose.yml"),
        "-f", str(release_root / f"deploy/market-data/compose.{role}.yml"),
        "--profile", role,
    ]


def _ids(project: str, service: str, *, running: bool = False) -> list[str]:
    mode = "-q" if running else "-aq"
    output = _text(
        [
            "docker", "ps", mode, "--no-trunc",
            "--filter", f"label=com.docker.compose.project={project}",
            "--filter", f"label=com.docker.compose.service={service}",
        ],
        label="rollout_container_inventory",
    )
    return [line for line in output.splitlines() if line]


def _inspect(container_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            _text(["docker", "inspect", container_id], label="rollout_container_inspect")
        )[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise RolloutError("rollout_container_inspect_invalid") from exc
    return payload


def _identity(
    container_id: str, *, project: str, service: str, image_id: str, release_sha: str
) -> dict[str, Any]:
    document = _inspect(container_id)
    labels = document.get("Config", {}).get("Labels", {}) or {}
    image_labels = json.loads(
        _text(["docker", "image", "inspect", image_id], label="rollout_image_inspect")
    )[0].get("Config", {}).get("Labels", {}) or {}
    state = document.get("State", {}) or {}
    if (
        str(document.get("Id") or "") != container_id
        or document.get("Image") != image_id
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != service
        or image_labels.get("org.opencontainers.image.revision") != release_sha
    ):
        raise RolloutError("rollout_container_identity_mismatch")
    return {
        "container_id": container_id,
        "running": state.get("Running") is True,
        "healthy": (state.get("Health", {}) or {}).get("Status") == "healthy",
        "image_id": image_id,
        "restart_policy": (document.get("HostConfig", {}).get("RestartPolicy", {}) or {}).get("Name"),
    }


def _validate_journal(
    payload: Mapping[str, Any], *, role: str, release_sha: str, image_id: str,
    env_sha256: str, project: str
) -> None:
    expected_keys = {
        "schema", "status", "role", "release_sha", "image_id", "env_sha256",
        "project", "services", "capture_services_started", "product_authority_changed",
        "private_shadow_only", "rollback_state_deleted", "secrets_disclosed",
    }
    if (
        set(payload) != expected_keys
        or payload.get("schema") != SCHEMA
        or payload.get("status") not in {"prepared", "in_progress", "PASS", "ROLLED_BACK"}
        or payload.get("role") != role
        or payload.get("release_sha") != release_sha
        or payload.get("image_id") != image_id
        or payload.get("env_sha256") != env_sha256
        or payload.get("project") != project
        or payload.get("capture_services_started") is not False
        or payload.get("product_authority_changed") is not False
        or payload.get("private_shadow_only") is not True
        or payload.get("rollback_state_deleted") is not False
        or payload.get("secrets_disclosed") is not False
    ):
        raise RolloutError("rollout_journal_identity_invalid")
    rows = payload.get("services")
    if not isinstance(rows, list) or [row.get("service") for row in rows] != list(
        ROLE_SERVICES[role]
    ):
        raise RolloutError("rollout_journal_service_order_invalid")
    for row in rows:
        if set(row) != {"service", "state", "container_id", "created_by_release"}:
            raise RolloutError("rollout_journal_service_schema_invalid")
        if row["state"] not in {"pending", "healthy", "rolled_back"}:
            raise RolloutError("rollout_journal_service_state_invalid")
        if row["state"] == "pending":
            if row["container_id"] is not None or row["created_by_release"] is not False:
                raise RolloutError("rollout_journal_pending_owner_invalid")
        elif (
            not isinstance(row["container_id"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["container_id"])
            or row["created_by_release"] is not True
        ):
            raise RolloutError("rollout_journal_created_owner_invalid")
    if payload["status"] == "prepared" and any(row["state"] != "pending" for row in rows):
        raise RolloutError("rollout_journal_prepared_state_invalid")
    if payload["status"] == "PASS" and any(row["state"] != "healthy" for row in rows):
        raise RolloutError("rollout_journal_pass_state_invalid")
    if payload["status"] == "ROLLED_BACK" and any(row["state"] == "healthy" for row in rows):
        raise RolloutError("rollout_journal_rollback_state_invalid")


def prepare(
    *, role: str, release_root: Path, env_file: Path, journal: Path,
    release_sha: str, image_id: str
) -> dict[str, Any]:
    values = _validate_env(role, env_file, release_sha, image_id)
    project = values["MARKET_PIPELINE_PROJECT_NAME"]
    env_sha = _sha256(env_file)
    if journal.exists():
        payload = _read_journal(journal)
        _validate_journal(
            payload, role=role, release_sha=release_sha, image_id=image_id,
            env_sha256=env_sha, project=project,
        )
        if payload["status"] == "ROLLED_BACK":
            if any(_ids(project, service) for service in ROLE_SERVICES[role]):
                raise RolloutError("rollout_rolled_back_owner_reappeared")
            for row in payload["services"]:
                row.update(
                    {"state": "pending", "container_id": None, "created_by_release": False}
                )
            payload["status"] = "prepared"
            _write_journal(journal, payload)
        return payload
    for capture in CAPTURE_SERVICES:
        if _ids(project, capture, running=True):
            raise RolloutError("rollout_capture_service_already_running")
    rows = []
    for service in ROLE_SERVICES[role]:
        existing = _ids(project, service)
        if existing:
            raise RolloutError("rollout_prior_runtime_requires_separate_upgrade_gate")
        rows.append(
            {"service": service, "state": "pending", "container_id": None, "created_by_release": False}
        )
    _run([*_compose(release_root, env_file, role), "config", "--quiet"], label="rollout_compose_config")
    payload = {
        "schema": SCHEMA,
        "status": "prepared",
        "role": role,
        "release_sha": release_sha,
        "image_id": image_id,
        "env_sha256": env_sha,
        "project": project,
        "services": rows,
        "capture_services_started": False,
        "product_authority_changed": False,
        "private_shadow_only": True,
        "rollback_state_deleted": False,
        "secrets_disclosed": False,
    }
    _write_journal(journal, payload)
    return payload


def _sha256(path: Path) -> str:
    from hashlib import sha256
    return sha256(path.read_bytes()).hexdigest()


def start_service(
    *, role: str, release_root: Path, env_file: Path, journal: Path,
    release_sha: str, image_id: str, service: str
) -> dict[str, Any]:
    values = _validate_env(role, env_file, release_sha, image_id)
    payload = _read_journal(journal)
    _validate_journal(
        payload, role=role, release_sha=release_sha, image_id=image_id,
        env_sha256=_sha256(env_file), project=values["MARKET_PIPELINE_PROJECT_NAME"],
    )
    order = ROLE_SERVICES[role]
    if service not in order:
        raise RolloutError("rollout_service_not_authorized")
    index = order.index(service)
    rows = payload["services"]
    if any(row["state"] != "healthy" for row in rows[:index]):
        raise RolloutError("rollout_receiver_first_order_violation")
    row = rows[index]
    if row["state"] == "healthy":
        identity = _identity(
            row["container_id"], project=values["MARKET_PIPELINE_PROJECT_NAME"],
            service=service, image_id=image_id, release_sha=release_sha,
        )
        if not identity["running"] or not identity["healthy"]:
            raise RolloutError("rollout_resumed_service_not_healthy")
        return payload
    if row["created_by_release"] and row["container_id"]:
        current = _ids(values["MARKET_PIPELINE_PROJECT_NAME"], service)
        if current != [row["container_id"]]:
            raise RolloutError("rollout_interrupted_owner_mismatch")
        for _attempt in range(60):
            identity = _identity(
                row["container_id"], project=values["MARKET_PIPELINE_PROJECT_NAME"],
                service=service, image_id=image_id, release_sha=release_sha,
            )
            if identity["running"] and identity["healthy"]:
                row["state"] = "healthy"
                payload["status"] = (
                    "PASS"
                    if all(item["state"] == "healthy" for item in rows)
                    else "in_progress"
                )
                _write_journal(journal, payload)
                return payload
            time.sleep(1)
        raise RolloutError("rollout_interrupted_service_health_timeout")
    if _ids(values["MARKET_PIPELINE_PROJECT_NAME"], service):
        raise RolloutError("rollout_service_owner_appeared")
    payload["status"] = "in_progress"
    _write_journal(journal, payload)
    _run(
        [*_compose(release_root, env_file, role), "up", "-d", "--no-deps", "--no-recreate", service],
        label="rollout_service_start",
    )
    ids = _ids(values["MARKET_PIPELINE_PROJECT_NAME"], service)
    if len(ids) != 1:
        raise RolloutError("rollout_service_owner_count_invalid")
    row["container_id"] = ids[0]
    row["created_by_release"] = True
    _write_journal(journal, payload)
    for _attempt in range(60):
        identity = _identity(
            ids[0], project=values["MARKET_PIPELINE_PROJECT_NAME"], service=service,
            image_id=image_id, release_sha=release_sha,
        )
        if identity["running"] and identity["healthy"]:
            row["state"] = "healthy"
            payload["status"] = (
                "PASS" if all(item["state"] == "healthy" for item in rows) else "in_progress"
            )
            _write_journal(journal, payload)
            return payload
        time.sleep(1)
    raise RolloutError("rollout_service_health_timeout")


def verify(
    *, role: str, env_file: Path, journal: Path, release_sha: str, image_id: str
) -> dict[str, Any]:
    values = _validate_env(role, env_file, release_sha, image_id)
    payload = _read_journal(journal)
    _validate_journal(
        payload, role=role, release_sha=release_sha, image_id=image_id,
        env_sha256=_sha256(env_file), project=values["MARKET_PIPELINE_PROJECT_NAME"],
    )
    if payload["status"] != "PASS":
        raise RolloutError("rollout_not_complete")
    for row in payload["services"]:
        identity = _identity(
            row["container_id"], project=values["MARKET_PIPELINE_PROJECT_NAME"],
            service=row["service"], image_id=image_id, release_sha=release_sha,
        )
        if not identity["running"] or not identity["healthy"]:
            raise RolloutError("rollout_service_not_healthy")
    for capture in CAPTURE_SERVICES:
        if _ids(values["MARKET_PIPELINE_PROJECT_NAME"], capture, running=True):
            raise RolloutError("rollout_capture_service_started")
    return payload


def rollback(
    *, role: str, env_file: Path, journal: Path, release_sha: str, image_id: str
) -> dict[str, Any]:
    values = _validate_env(role, env_file, release_sha, image_id)
    payload = _read_journal(journal)
    _validate_journal(
        payload, role=role, release_sha=release_sha, image_id=image_id,
        env_sha256=_sha256(env_file), project=values["MARKET_PIPELINE_PROJECT_NAME"],
    )
    for row in reversed(payload["services"]):
        container_id = row["container_id"]
        if row["state"] == "rolled_back":
            if _ids(values["MARKET_PIPELINE_PROJECT_NAME"], row["service"]):
                raise RolloutError("rollout_rollback_owner_reappeared")
            continue
        if not row["created_by_release"] or not container_id:
            continue
        current = _ids(values["MARKET_PIPELINE_PROJECT_NAME"], row["service"])
        if current != [container_id]:
            raise RolloutError("rollout_rollback_owner_mismatch")
        _identity(
            container_id, project=values["MARKET_PIPELINE_PROJECT_NAME"],
            service=row["service"], image_id=image_id, release_sha=release_sha,
        )
        _run(["docker", "update", "--restart=no", container_id], label="rollout_rollback_restart_disable")
        _run(["docker", "stop", "-t", "30", container_id], label="rollout_rollback_stop")
        _run(["docker", "rm", container_id], label="rollout_rollback_remove")
        if _ids(values["MARKET_PIPELINE_PROJECT_NAME"], row["service"]):
            raise RolloutError("rollout_rollback_cleanup_incomplete")
        row["state"] = "rolled_back"
        _write_journal(journal, payload)
    payload["status"] = "ROLLED_BACK"
    _write_journal(journal, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "start", "verify", "rollback"))
    parser.add_argument("--role", choices=sorted(ROLE_SERVICES), required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--service")
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if (
            args.confirm != CONFIRMATION
            or not RELEASE_SHA.fullmatch(args.release_sha)
            or not IMAGE_ID.fullmatch(args.image_id)
        ):
            raise RolloutError("rollout_invocation_invalid")
        common = {
            "role": args.role,
            "env_file": args.env_file,
            "journal": args.journal,
            "release_sha": args.release_sha,
            "image_id": args.image_id,
        }
        if args.command == "prepare":
            result = prepare(release_root=args.release_root, **common)
        elif args.command == "start":
            if not args.service:
                raise RolloutError("rollout_service_required")
            result = start_service(
                release_root=args.release_root, service=args.service, **common
            )
        elif args.command == "verify":
            result = verify(**common)
        else:
            result = rollback(**common)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "role": result["role"],
                    "release_sha": result["release_sha"],
                    "capture_services_started": False,
                    "product_authority_changed": False,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError, RolloutError) as exc:
        print(
            json.dumps(
                {"status": "FAIL", "reason_code": str(exc), "secrets_disclosed": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
