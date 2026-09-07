"""Activate only the calibrated group-scan processor with automatic rollback."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time


OLD_RELEASE = "d08ec9b2ebf3c14781ea6d60bf7892b2b60386ed"
NEW_RELEASE = "9d752171fee23b20afdef4bd6efc8c3962262586"
OLD_IMAGE = "sha256:fb6f4d370f2ee6523cd04609f46ca1a9e6a1fdf91e27c0461ba261101d181a95"
NEW_IMAGE = "sha256:3c3460be96489e4356b4dbb801545831e32a19b9b74d50e280ac12907346056f"
NEW_TREE = "3fcb9f3d0613a8226dfc9f44dfccea7a2673a716"
NEW_INPUT_SIGNATURE = "2ee8cef3ebde58cd45b625d76f89d9b4cca5d92d2de25816d2e1d751ba5baadd"
PORTABLE_IMAGE_DIGEST = "715d6f0667ad0b675649a4e673f93e8a9045b98a9ea21219134fe9033f665ace"
PARENT_RELEASE = "4ef8e6dcb2d361b763bd8b72dc730c1f978f564a"
PROJECT = "market-private-pipeline-primary"
ROLE = "market-processor"
CONTAINER = f"{PROJECT}-{ROLE}-1"
RELEASE_ROOT = Path(
    "/srv/trading-bot/market-pipeline-releases/f3be9ae2c4e9df0da31abaa41e91f327eb9573a2"
)
STATE_ROOT = Path("/srv/trading-bot/market-data-staging-shadow/state/market-processor")
STATE = STATE_ROOT / ROLE
OWNER_LOCK = STATE / "owner.lock"
PARENT_LOCK = Path(
    "/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock"
)
OPS = Path(
    "/srv/trading-bot/incident-recovery/20260907-processor-calibration-scan"
)
OVERRIDE = OPS / "processor-calibration-scan.override.json"
JOURNAL = OPS / "handoff.json"
EXPORT_LIMIT = "500"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def command(arguments: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    require(
        result.returncode == 0,
        f"command_failed:{arguments[0]}:{result.returncode}",
    )
    return result.stdout


def inspect(target: str, *, image: bool = False) -> dict:
    arguments = ["docker"] + (["image"] if image else []) + ["inspect", target]
    return json.loads(command(arguments))[0]


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_metadata(path: Path, *, uid: int) -> tuple[int, ...]:
    require(path.resolve() == path, "symlink_path")
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_uid == uid
        and stat.S_IMODE(info.st_mode) == 0o600,
        "unsafe_file_metadata",
    )
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IMODE(info.st_mode),
        info.st_uid,
        info.st_gid,
        info.st_nlink,
    )


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def held(path: Path, *, uid: int):
    before = file_metadata(path, uid=uid)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        live = os.fstat(descriptor)
        require((live.st_dev, live.st_ino) == before[:2], "lock_inode_drift")
        yield before
        require(file_metadata(path, uid=uid) == before, "lock_replaced")
    finally:
        os.close(descriptor)


def compose(*, candidate: bool) -> list[str]:
    arguments = [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "--profile",
        "web",
        "--env-file",
        str(RELEASE_ROOT / "web.release.env"),
    ]
    for name in (
        "deploy/market-data/compose.yml",
        "deploy/market-data/compose.web.yml",
        "processor-backlog-recovery.override.yml",
    ):
        arguments += ["-f", str(RELEASE_ROOT / name)]
    for path in (
        "/srv/trading-bot/incident-recovery/20260905-processor/processor-parent-hotfix-20260905.override.json",
        "/srv/trading-bot/incident-recovery/20260907-processor/processor-replay-hotfix.override.json",
        "/srv/trading-bot/incident-recovery/20260907-processor-covering/processor-covering-hotfix.override.json",
        "/srv/trading-bot/incident-recovery/20260907-processor-input-isolation/processor-input-isolation-hotfix.override.json",
        "/srv/trading-bot/incident-recovery/20260907-processor-live-fairness/processor-live-fairness-hotfix.override.json",
        "/srv/trading-bot/incident-recovery/20260907-processor-export-capacity/processor-export-capacity.override.json",
        "/srv/trading-bot/incident-recovery/20260907-processor-sparse-minutes/processor-sparse-minutes.override.json",
        "/srv/trading-bot/incident-recovery/20260907-processor-latency/processor-latency.override.json",
    ):
        arguments += ["-f", path]
    if candidate:
        arguments += ["-f", str(OVERRIDE)]
    return arguments


def mounts(container: dict) -> list[tuple[str, str, bool]]:
    return sorted(
        (item["Source"], item["Destination"], bool(item.get("RW")))
        for item in container["Mounts"]
    )


def owners() -> list[str]:
    identifiers = command(["docker", "ps", "-q"]).split()
    rows = json.loads(command(["docker", "inspect"] + identifiers)) if identifiers else []
    return [
        row["Id"]
        for row in rows
        if any(item.get("Source") == str(STATE_ROOT) for item in row["Mounts"])
    ]


def bystanders() -> dict[str, tuple[str, str, int]]:
    identifiers = command(["docker", "ps", "-q"]).split()
    rows = json.loads(command(["docker", "inspect"] + identifiers)) if identifiers else []
    return {
        row["Id"]: (
            row["Image"],
            row["State"]["StartedAt"],
            int(row["RestartCount"]),
        )
        for row in rows
        if row["Name"] != f"/{CONTAINER}"
    }


def portable_digest(image: dict) -> str:
    payload = {
        key: image.get(key)
        for key in ("Architecture", "Config", "Created", "Os", "RootFS")
    }
    return digest(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    )


def validate_candidate_image(image: dict) -> None:
    labels = image["Config"]["Labels"]
    require(image["Id"] == NEW_IMAGE, "candidate_image_id_drift")
    require(image["Architecture"] == "amd64" and image["Os"] == "linux", "candidate_platform_drift")
    require(image["Config"]["User"] == "10001:10001", "candidate_user_drift")
    require(labels["org.opencontainers.image.revision"] == NEW_RELEASE, "candidate_release_drift")
    require(labels["io.gold-trade.release.tree"] == NEW_TREE, "candidate_tree_drift")
    require(
        labels["io.gold-trade.release.input-signature"] == NEW_INPUT_SIGNATURE,
        "candidate_signature_drift",
    )
    require(portable_digest(image) == PORTABLE_IMAGE_DIGEST, "candidate_content_drift")


def validate_config(prior: dict, candidate: dict) -> None:
    before = prior["services"]
    after = candidate["services"]
    require(set(before) == set(after), "service_set_drift")
    for role in before:
        if role != ROLE:
            require(before[role] == after[role], "unrelated_service_drift")
    expected = json.loads(json.dumps(before[ROLE]))
    expected["image"] = NEW_IMAGE
    expected["restart"] = "unless-stopped"
    expected["environment"]["MARKET_PIPELINE_RELEASE_SHA"] = NEW_RELEASE
    expected["environment"]["MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE"] = EXPORT_LIMIT
    expected.setdefault("labels", {})["org.opencontainers.image.revision"] = NEW_RELEASE
    require(expected == after[ROLE], "unexpected_target_config_drift")
    for key in ("networks", "volumes", "secrets", "configs"):
        require(prior.get(key) == candidate.get(key), "infrastructure_config_drift")


def read_health() -> dict:
    return json.loads((STATE / "health.json").read_text(encoding="utf-8"))


def wait_healthy(
    *,
    release: str,
    image: str,
    expected_mounts: list[tuple[str, str, bool]],
    expected_owner_lock: tuple[int, ...],
    since: float,
) -> dict:
    deadline = time.monotonic() + 360
    last_reason = "not_started"
    while time.monotonic() < deadline:
        try:
            container = inspect(CONTAINER)
            require(container["State"]["Running"], "container_not_running")
            require(container["Image"] == image, "runtime_image_drift")
            require(container["RestartCount"] == 0, "runtime_restarted")
            require(mounts(container) == expected_mounts, "runtime_mount_drift")
            require(owners() == [container["Id"]], "processor_owner_overlap")
            require(
                file_metadata(OWNER_LOCK, uid=10001) == expected_owner_lock,
                "owner_lock_drift",
            )
            health = read_health()
            updated = datetime.fromisoformat(
                health["updated_at_utc"].replace("Z", "+00:00")
            ).timestamp()
            require(health.get("release_sha") == release, "health_release_drift")
            require(updated >= since and time.time() - updated < 60, "heartbeat_stale")
            require(
                health.get("schema") == "market_processor/4.0"
                and health.get("mode") == "live"
                and health.get("shadow_only") is True
                and health.get("status") == "live-shadow-ready",
                "health_contract_drift",
            )
            counters = health.get("counters", {})
            require(counters.get("archive_rejected") == 0, "archive_rejected")
            require(
                counters.get("research_contexts_unavailable") == 0,
                "research_context_unavailable",
            )
            require(
                container["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped",
                "restart_policy_drift",
            )
            require(
                container["State"].get("Health", {}).get("Status") == "healthy",
                "docker_health_not_ready",
            )
            return {
                "observed_at_utc": utc_now(),
                "container_id": container["Id"],
                "updated_at_utc": health["updated_at_utc"],
                "projection_status": health.get("projection_status"),
                "pending_market_messages": counters.get("pending_market_messages"),
                "pending_coin_groups": counters.get("pending_coin_groups"),
                "cycle_duration_seconds": counters.get("cycle_duration_seconds"),
                "projection_duration_seconds": counters.get("projection_duration_seconds"),
                "archive_duration_seconds": counters.get("archive_duration_seconds"),
                "archive_rejected": 0,
                "research_contexts_unavailable": 0,
                "restart_count": 0,
                "docker_health": "healthy",
            }
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            last_reason = type(exc).__name__
        time.sleep(5)
    raise RuntimeError(f"processor_health_timeout:{last_reason}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    require(os.geteuid() == 0, "root_required")
    OPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(
        OPS.resolve() == OPS
        and OPS.stat().st_uid == 0
        and stat.S_IMODE(OPS.stat().st_mode) == 0o700,
        "operations_path_invalid",
    )
    with held(PARENT_LOCK, uid=0):
        parent_bytes = PARENT_LOCK.read_bytes()
        parent = json.loads(parent_bytes)
        parent_info = PARENT_LOCK.stat()
        require(
            parent.get("schema") == "market_pipeline_maintenance_lock/1.0"
            and parent.get("environment") == "production"
            and parent.get("host_role") == "web"
            and parent.get("release_sha") == PARENT_RELEASE
            and parent.get("inode") == parent_info.st_ino
            and parent.get("device") == parent_info.st_dev,
            "parent_binding_drift",
        )
        require(not JOURNAL.exists(), "journal_exists_no_blind_repeat")
        override = {
            "services": {
                ROLE: {
                    "image": NEW_IMAGE,
                    "restart": "unless-stopped",
                    "environment": {
                        "MARKET_PIPELINE_RELEASE_SHA": NEW_RELEASE,
                        "MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE": EXPORT_LIMIT,
                    },
                    "labels": {"org.opencontainers.image.revision": NEW_RELEASE},
                }
            }
        }
        if OVERRIDE.exists():
            file_metadata(OVERRIDE, uid=0)
            require(json.loads(OVERRIDE.read_text()) == override, "override_drift")
        else:
            atomic_json(OVERRIDE, override)
        old = inspect(CONTAINER)
        require(
            old["Image"] == OLD_IMAGE
            and old["State"]["Running"]
            and old["RestartCount"] == 0,
            "prior_runtime_drift",
        )
        require(owners() == [old["Id"]], "prior_owner_overlap")
        old_health = read_health()
        require(
            old_health.get("release_sha") == OLD_RELEASE
            and old_health.get("status") == "live-shadow-ready"
            and old_health.get("counters", {}).get("archive_rejected") == 0,
            "prior_health_drift",
        )
        old_health_time = datetime.fromisoformat(
            old_health["updated_at_utc"].replace("Z", "+00:00")
        ).timestamp()
        require(time.time() - old_health_time < 60, "prior_heartbeat_stale")
        target = inspect(NEW_IMAGE, image=True)
        validate_candidate_image(target)
        prior_config = json.loads(command(compose(candidate=False) + ["config", "--format", "json"]))
        candidate_config = json.loads(command(compose(candidate=True) + ["config", "--format", "json"]))
        validate_config(prior_config, candidate_config)
        live_environment = dict(item.split("=", 1) for item in old["Config"]["Env"])
        require(
            all(
                str(value) == live_environment.get(key)
                for key, value in prior_config["services"][ROLE]["environment"].items()
            ),
            "prior_runtime_environment_drift",
        )
        old_mounts = mounts(old)
        old_owner_lock = file_metadata(OWNER_LOCK, uid=10001)
        untouched = bystanders()
        record = {
            "schema": "market_processor_calibration_scan_handoff/1.0",
            "status": "PREPARED",
            "created_at_utc": utc_now(),
            "old_release": OLD_RELEASE,
            "new_release": NEW_RELEASE,
            "old_image": OLD_IMAGE,
            "new_image": NEW_IMAGE,
            "portable_image_digest": PORTABLE_IMAGE_DIGEST,
            "parent_lock_sha256": digest(parent_bytes),
            "owner_lock": old_owner_lock,
            "old_container_id": old["Id"],
            "mounts": old_mounts,
            "bystanders": untouched,
            "data_deleted": False,
            "capture_changed": False,
            "sender_changed": False,
            "product_changed": False,
        }
        if not arguments.apply:
            print(
                json.dumps(
                    {
                        "status": "PREFLIGHT_PASS",
                        "old_release": OLD_RELEASE,
                        "new_release": NEW_RELEASE,
                    }
                )
            )
            return 0
        atomic_json(JOURNAL, record)
        stopped = False
        try:
            stopped = True
            command(["docker", "stop", "--timeout", "30", CONTAINER], timeout=50)
            with held(OWNER_LOCK, uid=10001):
                require(owners() == [], "old_owner_not_quiesced")
                record["status"] = "OWNER_QUIESCED"
                atomic_json(JOURNAL, record)
            started = time.time()
            command(
                compose(candidate=True)
                + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE],
                timeout=90,
            )
            record["live_probe"] = wait_healthy(
                release=NEW_RELEASE,
                image=NEW_IMAGE,
                expected_mounts=old_mounts,
                expected_owner_lock=old_owner_lock,
                since=started,
            )
            require(bystanders() == untouched, "unrelated_container_changed")
            require(PARENT_LOCK.read_bytes() == parent_bytes, "parent_lock_changed")
            record["status"] = "APPLIED_LIVE"
            record["completed_at_utc"] = utc_now()
            record["bystanders_unchanged"] = True
            atomic_json(JOURNAL, record)
            print(
                json.dumps(
                    {
                        "status": record["status"],
                        "new_release": NEW_RELEASE,
                        "live_probe": record["live_probe"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        except BaseException:
            record["status"] = "ROLLBACK_REQUIRED"
            atomic_json(JOURNAL, record)
            if stopped:
                command(["docker", "stop", "--timeout", "30", CONTAINER], timeout=50)
                with held(OWNER_LOCK, uid=10001):
                    require(owners() == [], "rollback_owner_not_quiesced")
                started = time.time()
                command(
                    compose(candidate=False)
                    + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE],
                    timeout=90,
                )
                record["rollback_probe"] = wait_healthy(
                    release=OLD_RELEASE,
                    image=OLD_IMAGE,
                    expected_mounts=old_mounts,
                    expected_owner_lock=old_owner_lock,
                    since=started,
                )
                require(bystanders() == untouched, "rollback_bystander_drift")
                require(PARENT_LOCK.read_bytes() == parent_bytes, "rollback_parent_lock_drift")
                record["status"] = "ROLLED_BACK_LIVE"
                atomic_json(JOURNAL, record)
            raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__,
                }
            ),
            flush=True,
        )
        raise SystemExit(2)
