"""Swap only the live market-fact sender to the group-priority image.

The handoff preserves the durable outbox and all peers.  It is deliberately
standalone: the emergency latency fix must neither roll out the architecture
branch nor restart capture, processor, receiver, or estimator services.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import time


OLD_RELEASE = "c9be832aef3792f369ae11e8fca95bcd7c42821f"
NEW_RELEASE = "0bb15ea978b5ab87b281403f5432bed6caaddd7e"
OLD_IMAGE = "sha256:fd5c0d2a4d563184d15443a6e3749ab5be1ac7558bbffac766d4778a0f8d427d"
# Docker load retains the signed OCI labels and rootfs but this daemon assigns
# its own local config ID.  The immutable metadata is verified before use.
NEW_IMAGE = "sha256:f063568469d81b249e3b5be775172d4bf289a39aa92ca1b710dd2a6fe67e1bb0"
PARENT_RELEASE = "4ef8e6dcb2d361b763bd8b72dc730c1f978f564a"
PROJECT = "market-private-pipeline-primary"
ROLE = "market-fact-sync-worker"
CONTAINER = f"{PROJECT}-{ROLE}-1"
ROOT = Path("/srv/trading-bot/market-pipeline-releases") / PARENT_RELEASE
STATE_ROOT = Path("/srv/trading-bot/market-data-staging-shadow/state") / ROLE
STATE = STATE_ROOT / ROLE
OWNER_LOCK = STATE / "owner.lock"
PARENT_LOCK = Path("/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock")
TRANSFER_OVERRIDE = Path("/srv/trading-bot/incident-recovery/20260907-transfer/sender-recovery-hotfix.override.json")
FAIR_OVERRIDE = Path("/srv/trading-bot/incident-recovery/20260921-sender-fair-scheduling/sender-fair-scheduling.override.json")
OPS = Path("/srv/trading-bot/incident-recovery/20260921-sender-group-priority")
OVERRIDE = OPS / "sender-group-priority.override.json"
JOURNAL = OPS / "handoff.json"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def command(args: list[str], timeout: int = 90) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, f"command_failed:{args[0]}:{result.returncode}")
    return result.stdout


def inspect(target: str, *, image: bool = False) -> dict:
    return json.loads(command(["docker", *(["image"] if image else []), "inspect", target]))[0]


def safe_file(path: Path, uid: int, mode: int = 0o600) -> None:
    require(path.resolve() == path and path.is_file(), "unsafe_file_path")
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_uid == uid
        and stat.S_IMODE(info.st_mode) == mode,
        "unsafe_file_metadata",
    )


def write_json(path: Path, document: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(document, stream, sort_keys=True)
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
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def held(path: Path, uid: int):
    safe_file(path, uid)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def compose(*, priority: bool) -> list[str]:
    arguments = [
        "docker", "compose", "-p", PROJECT, "--profile", "web", "--env-file",
        str(ROOT / "web.release.env"),
    ]
    for name in ("deploy/market-data/compose.yml", "deploy/market-data/compose.web.yml"):
        arguments.extend(("-f", str(ROOT / name)))
    arguments.extend(("-f", str(TRANSFER_OVERRIDE), "-f", str(FAIR_OVERRIDE)))
    if priority:
        arguments.extend(("-f", str(OVERRIDE)))
    return arguments


def expected_override() -> dict:
    return {
        "services": {
            ROLE: {
                "image": NEW_IMAGE,
                "restart": "unless-stopped",
                "environment": {"MARKET_PIPELINE_RELEASE_SHA": NEW_RELEASE},
                "labels": {"org.opencontainers.image.revision": NEW_RELEASE},
            }
        }
    }


def mount_set(container: dict) -> list[tuple[str, str, bool]]:
    return sorted(
        (item["Source"], item["Destination"], bool(item.get("RW")))
        for item in container["Mounts"]
    )


def state_owners() -> list[str]:
    ids = command(["docker", "ps", "-q"]).split()
    rows = json.loads(command(["docker", "inspect", *ids])) if ids else []
    return [
        row["Id"] for row in rows
        if any(item.get("Source") == str(STATE_ROOT) for item in row["Mounts"])
    ]


def other_containers() -> dict[str, tuple[str, str]]:
    ids = command(["docker", "ps", "-q"]).split()
    rows = json.loads(command(["docker", "inspect", *ids])) if ids else []
    return {
        row["Id"]: (row["Image"], row["State"]["StartedAt"])
        for row in rows
        if row["Name"] != f"/{CONTAINER}"
    }


def valid_config(before: dict, after: dict) -> None:
    require(set(before["services"]) == set(after["services"]), "service_set_drift")
    require(
        all(before["services"][name] == after["services"][name]
            for name in before["services"] if name != ROLE),
        "unrelated_service_drift",
    )
    expected = json.loads(json.dumps(before["services"][ROLE]))
    expected["image"] = NEW_IMAGE
    expected["restart"] = "unless-stopped"
    expected["environment"]["MARKET_PIPELINE_RELEASE_SHA"] = NEW_RELEASE
    expected.setdefault("labels", {})["org.opencontainers.image.revision"] = NEW_RELEASE
    require(expected == after["services"][ROLE], "target_config_drift")
    for key in ("networks", "volumes", "secrets", "configs"):
        require(before.get(key) == after.get(key), "infrastructure_config_drift")


def health_after(started_at: float, mounts: list[tuple[str, str, bool]]) -> dict:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        container = inspect(CONTAINER)
        if (
            container["State"]["Running"]
            and container["Image"] == NEW_IMAGE
            and container["RestartCount"] == 0
            and mount_set(container) == mounts
            and state_owners() == [container["Id"]]
        ):
            try:
                health = json.loads((STATE / "health.json").read_text())
                updated = datetime.fromisoformat(health["updated_at_utc"].replace("Z", "+00:00"))
                if (
                    health.get("release_sha") == NEW_RELEASE
                    and health.get("status") == "live-ready"
                    and health.get("private_transport_only") is True
                    and health.get("dead_letter_count") == 0
                    and int(health.get("acknowledged") or 0) > 0
                    and updated.timestamp() >= started_at
                    and time.time() - updated.timestamp() < 30
                ):
                    return {
                        "queue_depth": health.get("queue_depth"),
                        "acknowledged": health.get("acknowledged"),
                        "last_ack_latency_ms": health.get("last_ack_latency_ms"),
                        "updated_at_utc": health.get("updated_at_utc"),
                    }
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                pass
        time.sleep(3)
    raise RuntimeError("sender_priority_health_timeout")


def main() -> None:
    require(os.geteuid() == 0, "root_required")
    require(not JOURNAL.exists(), "journal_exists_no_blind_repeat")
    OPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(OPS, 0o700)
    for path in (PARENT_LOCK, TRANSFER_OVERRIDE, FAIR_OVERRIDE, OWNER_LOCK):
        safe_file(path, 0 if path == PARENT_LOCK or path in {TRANSFER_OVERRIDE, FAIR_OVERRIDE} else 10001)
    old = inspect(CONTAINER)
    require(
        old["State"]["Running"]
        and old["Image"] == OLD_IMAGE
        and old["Config"]["Labels"].get("org.opencontainers.image.revision") == OLD_RELEASE
        and old["RestartCount"] == 0,
        "prior_runtime_drift",
    )
    image = inspect(NEW_IMAGE, image=True)
    labels = image["Config"].get("Labels") or {}
    require(
        image.get("Id") == NEW_IMAGE
        and image.get("Os") == "linux"
        and image.get("Architecture") == "amd64"
        and image["Config"].get("User") == "10001:10001"
        and labels.get("org.opencontainers.image.revision") == NEW_RELEASE,
        "new_image_identity_invalid",
    )
    if OVERRIDE.exists():
        safe_file(OVERRIDE, 0)
        require(json.loads(OVERRIDE.read_text()) == expected_override(), "override_drift")
    else:
        write_json(OVERRIDE, expected_override())
        safe_file(OVERRIDE, 0)
    before = json.loads(command([*compose(priority=False), "config", "--format", "json"]))
    after = json.loads(command([*compose(priority=True), "config", "--format", "json"]))
    valid_config(before, after)
    prior_mounts = mount_set(old)
    bystanders = other_containers()
    with held(PARENT_LOCK, 0):
        lock = json.loads(PARENT_LOCK.read_text())
        require(
            lock.get("schema") == "market_pipeline_maintenance_lock/1.0"
            and lock.get("environment") == "production"
            and lock.get("host_role") == "web"
            and lock.get("release_sha") == PARENT_RELEASE,
            "parent_lock_drift",
        )
        journal = {
            "schema": "market_sender_group_priority_handoff/1.0",
            "status": "PREPARED",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "old_release": OLD_RELEASE,
            "new_release": NEW_RELEASE,
            "old_image": OLD_IMAGE,
            "new_image": NEW_IMAGE,
            "data_deleted": False,
            "capture_changed": False,
            "processor_changed": False,
            "receiver_changed": False,
            "estimator_changed": False,
            "mounts": prior_mounts,
            "bystanders": bystanders,
        }
        write_json(JOURNAL, journal)
        stopped = False
        try:
            stopped = True
            command(["docker", "stop", "--timeout", "30", CONTAINER], timeout=50)
            with held(OWNER_LOCK, 10001):
                require(state_owners() == [], "old_owner_not_quiesced")
            journal["status"] = "OWNER_QUIESCED"
            write_json(JOURNAL, journal)
            started = time.time()
            command([*compose(priority=True), "up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE])
            journal["live_probe"] = health_after(started, prior_mounts)
            require(other_containers() == bystanders, "unrelated_container_changed")
            journal["status"] = "APPLIED_LIVE"
            journal["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
            write_json(JOURNAL, journal)
            print(json.dumps({"status": journal["status"], "live_probe": journal["live_probe"]}))
        except BaseException:
            journal["status"] = "ROLLBACK_REQUIRED"
            write_json(JOURNAL, journal)
            if stopped:
                command(["docker", "stop", "--timeout", "30", CONTAINER], timeout=50)
                with held(OWNER_LOCK, 10001):
                    require(state_owners() == [], "rollback_owner_not_quiesced")
                command([*compose(priority=False), "up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE])
                journal["status"] = "ROLLED_BACK_LIVE"
                write_json(JOURNAL, journal)
            raise


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        raise SystemExit(2)
