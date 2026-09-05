#!/usr/bin/env python3
"""Replace only Account1's Docker probe after an auditable replay quarantine.

This is an incident-scoped controller, not a Market Pipeline promotion tool.
It preserves the historical quarantine and proves that the strict replay gate
still fails after Docker starts using the narrower live-capture liveness probe.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Any, Sequence


HEX40 = re.compile(r"^[0-9a-f]{40}$")
IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ROLE = "market-capture-account1"
PROJECT = "market-private-pipeline-primary"
CONTAINER = f"{PROJECT}-{ROLE}-1"
RELEASE_BASE = Path("/srv/trading-bot/market-pipeline-releases")


class HandoffError(RuntimeError):
    """Stable, non-sensitive refusal for the narrow Account1 handoff."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise HandoffError(reason)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def run(arguments: Sequence[str], *, timeout: int = 90, allow_failure: bool = False) -> str:
    result = subprocess.run(
        list(arguments), capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode and not allow_failure:
        raise HandoffError(f"command_failed_{Path(arguments[0]).name}_{result.returncode}")
    return result.stdout


def inspect(target: str, *, image: bool = False) -> dict[str, Any]:
    prefix = ["docker", "image", "inspect"] if image else ["docker", "inspect"]
    output = run([*prefix, target])
    value = json.loads(output)[0]
    require(isinstance(value, dict), "docker_inspect_invalid")
    return value


def secure_file(path: Path, *, uid: int, mode: int = 0o600) -> os.stat_result:
    require(path.is_absolute() and path.resolve() == path, "unsafe_path")
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_uid == uid
        and stat.S_IMODE(info.st_mode) == mode,
        "unsafe_file_metadata",
    )
    return info


def secure_release_root(path: Path, release: str) -> Path:
    require(HEX40.fullmatch(release) is not None, "release_invalid")
    require(path == RELEASE_BASE / release and path.resolve() == path, "release_root_invalid")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0, "release_root_unsafe")
    for relative in ("deploy/market-data/compose.yml", "deploy/market-data/compose.web.yml"):
        candidate = path / relative
        secure_file(candidate, uid=0, mode=0o644)
    return path


def atomic_json(path: Path, value: dict[str, Any], *, uid: int = 0, gid: int = 0) -> None:
    candidate = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        candidate,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchown(stream.fileno(), uid, gid)
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


@contextmanager
def held(path: Path, uid: int):
    before = secure_file(path, uid=uid)
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = os.fstat(descriptor)
        require(
            (before.st_dev, before.st_ino) == (current.st_dev, current.st_ino),
            "lock_inode_drift",
        )
        yield
        require(path.stat().st_ino == current.st_ino, "lock_replaced")
    finally:
        os.close(descriptor)


def compose(
    *,
    release_root: Path,
    source_root: Path,
    prior_override: Path,
    live_recovery_override: Path,
    target_override: Path | None = None,
) -> list[str]:
    arguments = [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "--profile",
        "web",
        "--env-file",
        str(source_root / "web.release.env"),
    ]
    for path in (
        release_root / "deploy/market-data/compose.yml",
        release_root / "deploy/market-data/compose.web.yml",
        source_root / "account1-replay-recovery.override.yml",
        prior_override,
        live_recovery_override,
    ):
        arguments.extend(("-f", str(path)))
    if target_override is not None:
        arguments.extend(("-f", str(target_override)))
    return arguments


def compose_config(arguments: Sequence[str]) -> dict[str, Any]:
    value = json.loads(run([*arguments, "config", "--format", "json"]))
    require(isinstance(value, dict) and isinstance(value.get("services"), dict), "compose_invalid")
    return value


def liveness_test() -> list[str]:
    return [
        "CMD",
        "python",
        "-m",
        "core.market_intelligence.private_pipeline_foundation",
        "capture-liveness",
        "--role",
        ROLE,
    ]


def validate_config(old: dict[str, Any], new: dict[str, Any], *, target_release: str, target_image: str) -> None:
    old_services, new_services = old["services"], new["services"]
    require(set(old_services) == set(new_services), "service_set_drift")
    for name in old_services:
        if name != ROLE:
            require(old_services[name] == new_services[name], "unrelated_service_drift")
    expected = json.loads(json.dumps(old_services[ROLE]))
    expected["image"] = target_image
    expected["environment"]["MARKET_PIPELINE_RELEASE_SHA"] = target_release
    expected.setdefault("labels", {})["org.opencontainers.image.revision"] = target_release
    expected.setdefault("healthcheck", {})["test"] = liveness_test()
    require(expected == new_services[ROLE], "unexpected_target_config_drift")
    for name in ("networks", "volumes", "secrets", "configs"):
        require(old.get(name) == new.get(name), "infrastructure_config_drift")


def mounts(container: dict[str, Any]) -> list[tuple[str, str, bool]]:
    return sorted(
        (str(item["Source"]), str(item["Destination"]), bool(item.get("RW")))
        for item in container.get("Mounts", [])
    )


def bystanders() -> dict[str, tuple[str, str]]:
    identifiers = run(["docker", "ps", "-q"]).split()
    rows = json.loads(run(["docker", "inspect", *identifiers])) if identifiers else []
    return {
        str(row["Id"]): (str(row["Image"]), str(row["State"]["StartedAt"]))
        for row in rows
        if row.get("Name") != f"/{CONTAINER}"
    }


def owner_paths(container: dict[str, Any]) -> tuple[Path, Path]:
    paths = {item.get("Destination"): Path(item["Source"]) for item in container.get("Mounts", [])}
    state_root = paths.get("/var/lib/market-data/state")
    session_root = paths.get("/var/lib/market-data/session")
    require(state_root is not None and session_root is not None, "capture_mounts_invalid")
    state = state_root / ROLE
    return state / "owner.lock", session_root / "owner.lock"


def exact_image_portable_digest(image: str) -> str:
    value = inspect(image, image=True)
    payload = {key: value.get(key) for key in ("Architecture", "Config", "Created", "Os", "RootFS")}
    return digest(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode())


def health_document(state_lock: Path) -> dict[str, Any]:
    value = json.loads((state_lock.parent / "health.json").read_text(encoding="utf-8"))
    require(isinstance(value, dict), "capture_health_invalid")
    return value


def health_proof(
    *,
    release: str,
    image: str,
    old_mounts: list[tuple[str, str, bool]],
    state_lock: Path,
    session_lock: Path,
    minimum_sequence: int,
    since: float,
    require_docker_healthy: bool,
) -> dict[str, Any]:
    container = inspect(CONTAINER)
    require(container["State"].get("Running") and container["Image"] == image, "runtime_not_stable")
    require(mounts(container) == old_mounts, "runtime_mount_drift")
    active = [
        row for row in json.loads(run(["docker", "inspect", *run(["docker", "ps", "-q"]).split()]))
        if any(str(item.get("Source")) == str(session_lock.parent) for item in row.get("Mounts", []))
    ]
    require([row["Id"] for row in active] == [container["Id"]], "session_owner_overlap")
    health = health_document(state_lock)
    updated = datetime.fromisoformat(str(health["updated_at_utc"]).replace("Z", "+00:00")).timestamp()
    require(health.get("release_sha") == release and updated >= since and time.time() - updated < 35, "heartbeat_stale")
    require(health.get("status") == "live-ready", "capture_not_live_ready")
    sequence = health.get("capture_sequence")
    require(type(sequence) is int and sequence > minimum_sequence, "capture_sequence_not_advancing")
    quarantined = sum(
        source.get("explicit_backfill", {}).get("quarantined", 0)
        for source in health.get("sources", {}).values()
        if isinstance(source, dict)
    )
    require(type(quarantined) is int and quarantined > 0, "historical_quarantine_missing")
    docker_health = container.get("State", {}).get("Health", {}).get("Status")
    if require_docker_healthy:
        require(docker_health == "healthy", "docker_liveness_not_healthy")
        strict = subprocess.run(
            ["docker", "exec", CONTAINER, "python", "-m", "core.market_intelligence.private_pipeline_foundation", "healthcheck", "--role", ROLE],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        require(strict.returncode != 0, "strict_replay_gate_unexpectedly_passed")
        run(["docker", "exec", CONTAINER, "python", "-m", "core.market_intelligence.private_pipeline_foundation", "capture-liveness", "--role", ROLE], timeout=20)
    return {
        "container_id": container["Id"],
        "capture_sequence": sequence,
        "docker_health": docker_health,
        "historical_quarantine": quarantined,
        "updated_at_utc": health["updated_at_utc"],
    }


def wait_for_proof(**kwargs: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            return health_proof(**kwargs)
        except (HandoffError, KeyError, ValueError, OSError):
            time.sleep(4)
    raise HandoffError("liveness_probe_timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-release", required=True)
    parser.add_argument("--source-runtime-release", required=True)
    parser.add_argument("--target-release", required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--target-portable-digest", required=True)
    parser.add_argument("--prior-override", type=Path, required=True)
    parser.add_argument("--operations-dir", type=Path, required=True)
    parser.add_argument("--parent-lock", type=Path, required=True)
    parser.add_argument("--parent-release", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0, "root_required")
    for value in (args.source_release, args.source_runtime_release, args.target_release, args.parent_release):
        require(HEX40.fullmatch(value) is not None, "release_invalid")
    require(IMAGE.fullmatch(args.target_image) is not None, "target_image_invalid")
    require(HEX64.fullmatch(args.target_portable_digest) is not None, "target_digest_invalid")
    source_root = secure_release_root(RELEASE_BASE / args.source_release, args.source_release)
    target_root = secure_release_root(RELEASE_BASE / args.target_release, args.target_release)
    secure_file(source_root / "web.release.env", uid=0)
    secure_file(source_root / "account1-replay-recovery.override.yml", uid=0)
    secure_file(args.prior_override, uid=0)
    parent_info = secure_file(args.parent_lock, uid=0)
    parent_bytes = args.parent_lock.read_bytes()
    parent = json.loads(parent_bytes)
    require(
        parent.get("schema") == "market_pipeline_maintenance_lock/1.0"
        and parent.get("environment") == "production"
        and parent.get("host_role") == "web"
        and parent.get("release_sha") == args.parent_release
        and parent.get("inode") == parent_info.st_ino
        and parent.get("device") == parent_info.st_dev,
        "parent_binding_drift",
    )
    require(args.operations_dir.is_absolute() and args.operations_dir.resolve() == args.operations_dir, "operations_path_invalid")
    directory_info = args.operations_dir.lstat()
    require(stat.S_ISDIR(directory_info.st_mode) and directory_info.st_uid == 0 and stat.S_IMODE(directory_info.st_mode) == 0o700, "operations_path_invalid")
    journal = args.operations_dir / "handoff.json"
    live_recovery_override = args.operations_dir / "account1-live-recovery.runtime.json"
    override = args.operations_dir / "account1-liveness.override.json"
    require(
        not journal.exists() and not override.exists() and not live_recovery_override.exists(),
        "handoff_already_exists",
    )
    with held(args.parent_lock, 0):
        old = inspect(CONTAINER)
        labels = old.get("Config", {}).get("Labels", {})
        require(old["State"].get("Running") and labels.get("org.opencontainers.image.revision") == args.source_runtime_release, "prior_runtime_drift")
        old_mounts = mounts(old)
        state_lock, session_lock = owner_paths(old)
        authority_marker = session_lock.parent / "authority-container.json"
        secure_file(authority_marker, uid=10001)
        prior_marker_bytes = authority_marker.read_bytes()
        prior_marker = json.loads(prior_marker_bytes)
        require(
            prior_marker.get("contract") == "market_capture_authority/1.0"
            and prior_marker.get("role") == ROLE
            and prior_marker.get("release_sha") == args.source_runtime_release,
            "prior_authority_marker_drift",
        )
        prior_health = health_document(state_lock)
        sequence = prior_health.get("capture_sequence")
        require(type(sequence) is int, "prior_capture_sequence_invalid")
        # The historical live-recovery YAML is intentionally not trusted as a
        # runtime input because it is world-readable. These two documented,
        # non-secret empty recovery controls are recreated in an operations
        # file owned by root before either current or target Compose is read.
        atomic_json(
            live_recovery_override,
            {"services": {ROLE: {"environment": {
                "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC": "",
                "MARKET_CAPTURE_BACKFILL_SOURCE_CODES": "",
            }}}},
        )
        prior_compose = compose(
            release_root=source_root,
            source_root=source_root,
            prior_override=args.prior_override,
            live_recovery_override=live_recovery_override,
        )
        old_config = compose_config(prior_compose)
        expected_current = old_config["services"][ROLE]
        require(old["Image"] == expected_current.get("image"), "prior_runtime_image_drift")
        require(
            old.get("Config", {}).get("Healthcheck", {}).get("Test")
            == expected_current.get("healthcheck", {}).get("test"),
            "prior_runtime_healthcheck_drift",
        )
        atomic_json(override, {"services": {ROLE: {"image": args.target_image, "environment": {"MARKET_PIPELINE_RELEASE_SHA": args.target_release}, "labels": {"org.opencontainers.image.revision": args.target_release}}}})
        target_compose = compose(
            release_root=target_root,
            source_root=source_root,
            prior_override=args.prior_override,
            live_recovery_override=live_recovery_override,
            target_override=override,
        )
        try:
            new_config = compose_config(target_compose)
            validate_config(old_config, new_config, target_release=args.target_release, target_image=args.target_image)
            require(exact_image_portable_digest(args.target_image) == args.target_portable_digest, "target_image_content_mismatch")
            record = {
                "schema": "account1_liveness_scoped_handoff/1.0",
                "status": "PREFLIGHT_PASS",
                "created_at_utc": utc_now(),
                "source_release": args.source_release,
                "source_runtime_release": args.source_runtime_release,
                "target_release": args.target_release,
                "target_image": args.target_image,
                "target_portable_digest": args.target_portable_digest,
                "parent_lock_sha256": digest(parent_bytes),
                "prior_container_id": old["Id"],
                "sequence_before": sequence,
                "prior_marker_sha256": digest(prior_marker_bytes),
                "data_deleted": False,
                "product_changed": False,
                "queue_changed": False,
                "replay_certified": False,
            }
            if not args.apply:
                print(json.dumps(record, sort_keys=True))
                return 0
            atomic_json(journal, record)
            untouched = bystanders()
            stopped = False
            target_marker: dict[str, Any] | None = None
            try:
                record["status"] = "APPLYING"
                atomic_json(journal, record)
                run(["docker", "stop", "--time", "30", CONTAINER], timeout=50)
                stopped = True
                with ExitStack() as stack:
                    stack.enter_context(held(state_lock, 10001))
                    stack.enter_context(held(session_lock, 10001))
                    require(not [row for row in json.loads(run(["docker", "inspect", *run(["docker", "ps", "-q"]).split()])) if any(str(item.get("Source")) == str(session_lock.parent) for item in row.get("Mounts", []))], "old_owner_not_quiesced")
                    require(authority_marker.read_bytes() == prior_marker_bytes, "authority_marker_race")
                    target_marker = {
                        **prior_marker,
                        "release_sha": args.target_release,
                        "authorized_at_utc": utc_now(),
                    }
                    atomic_json(authority_marker, target_marker, uid=10001, gid=10001)
                    secure_file(authority_marker, uid=10001)
                    record["target_marker_sha256"] = digest(authority_marker.read_bytes())
                    record["status"] = "AUTHORITY_TRANSFERRED"
                    atomic_json(journal, record)
                started = time.time()
                run([*target_compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE])
                record["live_probe"] = wait_for_proof(release=args.target_release, image=args.target_image, old_mounts=old_mounts, state_lock=state_lock, session_lock=session_lock, minimum_sequence=sequence, since=started, require_docker_healthy=True)
                require(bystanders() == untouched, "unrelated_container_changed")
                require(args.parent_lock.read_bytes() == parent_bytes, "parent_lock_changed")
                record["status"] = "APPLIED_LIVE_HISTORICAL_REVIEW_RETAINED"
                record["completed_at_utc"] = utc_now()
                atomic_json(journal, record)
                print(json.dumps(record, sort_keys=True))
                return 0
            except BaseException:
                record["status"] = "ROLLBACK_REQUIRED"
                atomic_json(journal, record)
                if stopped:
                    run(["docker", "stop", "--time", "30", CONTAINER], timeout=50, allow_failure=True)
                    with ExitStack() as stack:
                        stack.enter_context(held(state_lock, 10001))
                        stack.enter_context(held(session_lock, 10001))
                        current_marker = authority_marker.read_bytes()
                        target_bytes = (
                            json.dumps(target_marker, sort_keys=True, separators=(",", ":")).encode()
                            + b"\n"
                            if target_marker is not None
                            else prior_marker_bytes
                        )
                        require(
                            current_marker in {prior_marker_bytes, target_bytes},
                            "rollback_authority_marker_drift",
                        )
                        atomic_json(authority_marker, prior_marker, uid=10001, gid=10001)
                    started = time.time()
                    run([*prior_compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE])
                    record["rollback_probe"] = wait_for_proof(release=args.source_runtime_release, image=str(old["Image"]), old_mounts=old_mounts, state_lock=state_lock, session_lock=session_lock, minimum_sequence=sequence, since=started, require_docker_healthy=False)
                    record["status"] = "ROLLED_BACK_LIVE"
                    atomic_json(journal, record)
                raise
        finally:
            if not journal.exists():
                override.unlink(missing_ok=True)
                live_recovery_override.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HandoffError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "fail", "reason_code": str(exc)}))
        raise SystemExit(1)
