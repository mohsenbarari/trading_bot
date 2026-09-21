"""Processor-only archive-throttle handoff; preserve capture, facts, and peers."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import time


RELEASE = "9d752171fee23b20afdef4bd6efc8c3962262586"
IMAGE = "sha256:3c3460be96489e4356b4dbb801545831e32a19b9b74d50e280ac12907346056f"
PARENT_RELEASE = "4ef8e6dcb2d361b763bd8b72dc730c1f978f564a"
PROJECT = "market-private-pipeline-primary"
ROLE = "market-processor"
CONTAINER = f"{PROJECT}-{ROLE}-1"
ROOT = Path("/srv/trading-bot/market-pipeline-releases/f3be9ae2c4e9df0da31abaa41e91f327eb9573a2")
STATE_ROOT = Path("/srv/trading-bot/market-data-staging-shadow/state") / ROLE
STATE = STATE_ROOT / ROLE
OWNER_LOCK = STATE / "owner.lock"
PARENT_LOCK = Path("/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock")
CONFIG_FILES = (
    ROOT / "deploy/market-data/compose.yml",
    ROOT / "deploy/market-data/compose.web.yml",
    ROOT / "processor-backlog-recovery.override.yml",
    Path("/srv/trading-bot/incident-recovery/20260905-processor/processor-parent-hotfix-20260905.override.json"),
    Path("/srv/trading-bot/incident-recovery/20260907-processor/processor-replay-hotfix.override.json"),
    Path("/srv/trading-bot/incident-recovery/20260907-processor-covering/processor-covering-hotfix.override.json"),
    Path("/srv/trading-bot/incident-recovery/20260907-processor-input-isolation/processor-input-isolation-hotfix.override.json"),
    Path("/srv/trading-bot/incident-recovery/20260907-processor-live-fairness/processor-live-fairness-hotfix.override.json"),
    Path("/srv/trading-bot/incident-recovery/20260907-processor-export-capacity/processor-export-capacity.override.json"),
    Path("/srv/trading-bot/incident-recovery/20260907-processor-sparse-minutes/processor-sparse-minutes.override.json"),
    Path("/srv/trading-bot/incident-recovery/20260907-processor-latency/processor-latency.override.json"),
    Path("/srv/trading-bot/incident-recovery/20260907-processor-calibration-scan/processor-calibration-scan.override.json"),
)
OPS = Path("/srv/trading-bot/incident-recovery/20260921-processor-archive-latency")
OVERRIDE = OPS / "processor-archive-throttle.override.json"
JOURNAL = OPS / "handoff.json"
OLD_EXPORT_CAPACITY = "500"
NEW_EXPORT_CAPACITY = "25"


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def utc():
    return datetime.now(timezone.utc).isoformat()


def command(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, f"command_failed:{args[0]}:{result.returncode}")
    return result.stdout


def inspect(target):
    return json.loads(command(["docker", "inspect", target]))[0]


def safe_file(path, uid, mode=0o600):
    require(path.resolve() == path, "symlink_path")
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_uid == uid
        and stat.S_IMODE(info.st_mode) == mode,
        "unsafe_file_metadata",
    )


def atomic_json(path, value):
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
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
def held(path, uid):
    safe_file(path, uid)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def compose(*, candidate=False):
    args = [
        "docker", "compose", "-p", PROJECT, "--profile", "web", "--env-file",
        str(ROOT / "web.release.env"),
    ]
    for config in CONFIG_FILES:
        args.extend(("-f", str(config)))
    if candidate:
        args.extend(("-f", str(OVERRIDE)))
    return args


def mounts(container):
    return sorted((item["Source"], item["Destination"], item.get("RW")) for item in container["Mounts"])


def owners():
    identifiers = command(["docker", "ps", "-q"]).split()
    containers = json.loads(command(["docker", "inspect", *identifiers])) if identifiers else []
    return [
        container["Id"]
        for container in containers
        if any(mount.get("Source") == str(STATE_ROOT) for mount in container["Mounts"])
    ]


def bystanders():
    identifiers = command(["docker", "ps", "-q"]).split()
    containers = json.loads(command(["docker", "inspect", *identifiers])) if identifiers else []
    return {
        container["Id"]: (container["Image"], container["State"]["StartedAt"])
        for container in containers
        if container["Name"] != f"/{CONTAINER}"
    }


def override_payload():
    return {
        "services": {
            ROLE: {
                "environment": {
                    "MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE": NEW_EXPORT_CAPACITY,
                }
            }
        }
    }


def validate_config(current, candidate):
    before, after = current["services"], candidate["services"]
    require(set(before) == set(after), "service_set_drift")
    require(all(before[name] == after[name] for name in before if name != ROLE), "unrelated_service_drift")
    expected = json.loads(json.dumps(before[ROLE]))
    require(
        expected["environment"].get("MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE")
        == OLD_EXPORT_CAPACITY,
        "prior_export_capacity_drift",
    )
    expected["environment"]["MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE"] = NEW_EXPORT_CAPACITY
    require(expected == after[ROLE], "unexpected_processor_config_drift")
    for field in ("networks", "volumes", "secrets", "configs"):
        require(current.get(field) == candidate.get(field), "infrastructure_config_drift")


def health():
    return json.loads((STATE / "health.json").read_text())


def probe(*, prior_mounts, started_at):
    container = inspect(CONTAINER)
    require(
        container["State"]["Running"]
        and container["Image"] == IMAGE
        and container["RestartCount"] == 0,
        "processor_runtime_not_stable",
    )
    require(mounts(container) == prior_mounts, "processor_mount_drift")
    require(owners() == [container["Id"]], "processor_owner_overlap")
    report = health()
    updated = datetime.fromisoformat(report["updated_at_utc"].replace("Z", "+00:00"))
    require(
        report.get("release_sha") == RELEASE
        and updated.timestamp() >= started_at
        and time.time() - updated.timestamp() < 60,
        "processor_heartbeat_stale",
    )
    counters = report.get("counters") or {}
    require(
        report.get("schema") == "market_processor/4.0"
        and report.get("mode") == "live"
        and report.get("status") == "live-shadow-ready"
        and report.get("projection_status") in {"catching-up", "caught-up"}
        and counters.get("archive_rejected") == 0
        and int(counters.get("archive_selected") or 0) <= int(NEW_EXPORT_CAPACITY),
        "processor_contract_drift",
    )
    return {
        "container_id": container["Id"],
        "docker_health": container["State"].get("Health", {}).get("Status"),
        "updated_at_utc": report["updated_at_utc"],
        "cycle_duration_seconds": counters.get("cycle_duration_seconds"),
        "archive_duration_seconds": counters.get("archive_duration_seconds"),
        "archive_selected": counters.get("archive_selected"),
        "archive_rejected": 0,
        "pending_coin_groups": counters.get("pending_coin_groups"),
    }


def wait_live(*, prior_mounts, started_at):
    deadline = time.monotonic() + 180
    last = None
    while time.monotonic() < deadline:
        try:
            return probe(prior_mounts=prior_mounts, started_at=started_at)
        except (KeyError, RuntimeError, ValueError) as exc:
            last = type(exc).__name__
        time.sleep(5)
    raise RuntimeError(f"processor_start_timeout:{last}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0, "root_required")
    OPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(OPS, 0o700)
    require(OPS.resolve() == OPS, "operations_path_invalid")
    require(not JOURNAL.exists(), "journal_exists_no_blind_repeat")
    with held(PARENT_LOCK, 0):
        parent_bytes = PARENT_LOCK.read_bytes()
        parent = json.loads(parent_bytes)
        require(
            parent.get("schema") == "market_pipeline_maintenance_lock/1.0"
            and parent.get("environment") == "production"
            and parent.get("host_role") == "web"
            and parent.get("release_sha") == PARENT_RELEASE,
            "parent_binding_drift",
        )
        payload = override_payload()
        if OVERRIDE.exists():
            safe_file(OVERRIDE, 0)
            require(json.loads(OVERRIDE.read_text()) == payload, "override_drift")
        else:
            atomic_json(OVERRIDE, payload)
        safe_file(OVERRIDE, 0)
        old = inspect(CONTAINER)
        require(
            old["Image"] == IMAGE and old["State"]["Running"] and old["RestartCount"] == 0,
            "prior_processor_runtime_drift",
        )
        require(owners() == [old["Id"]], "prior_processor_owner_overlap")
        current = json.loads(command([*compose(), "config", "--format", "json"]))
        candidate = json.loads(command([*compose(candidate=True), "config", "--format", "json"]))
        validate_config(current, candidate)
        prior_mounts = mounts(old)
        peers = bystanders()
        prior_health = health()
        require(
            prior_health.get("release_sha") == RELEASE
            and prior_health.get("schema") == "market_processor/4.0"
            and prior_health.get("mode") == "live"
            and (prior_health.get("counters") or {}).get("archive_rejected") == 0,
            "prior_processor_contract_drift",
        )
        record = {
            "schema": "market_processor_archive_throttle_handoff/1.0",
            "status": "PREPARED",
            "created_at_utc": utc(),
            "release": RELEASE,
            "image": IMAGE,
            "old_export_capacity": int(OLD_EXPORT_CAPACITY),
            "new_export_capacity": int(NEW_EXPORT_CAPACITY),
            "data_deleted": False,
            "capture_changed": False,
            "sender_changed": False,
            "product_changed": False,
            "parent_handoff_changed": False,
            "mounts": prior_mounts,
            "bystanders": peers,
        }
        if not args.apply:
            print(json.dumps({"status": "PREFLIGHT_PASS", "new_export_capacity": int(NEW_EXPORT_CAPACITY)}))
            return
        atomic_json(JOURNAL, record)
        stopped = False
        try:
            stopped = True
            command(["docker", "stop", "--timeout", "30", CONTAINER], timeout=50)
            with held(OWNER_LOCK, 10001):
                require(owners() == [], "old_processor_not_quiesced")
                record["status"] = "OWNER_QUIESCED"
                atomic_json(JOURNAL, record)
            started = time.time()
            command([*compose(candidate=True), "up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE])
            record["live_probe"] = wait_live(prior_mounts=prior_mounts, started_at=started)
            require(bystanders() == peers, "unrelated_container_changed")
            require(PARENT_LOCK.read_bytes() == parent_bytes, "parent_lock_changed")
            record["status"] = "APPLIED_LIVE"
            record["completed_at_utc"] = utc()
            atomic_json(JOURNAL, record)
            print(json.dumps({"status": record["status"], "live_probe": record["live_probe"]}))
        except BaseException:
            record["status"] = "ROLLBACK_REQUIRED"
            atomic_json(JOURNAL, record)
            if stopped:
                command(["docker", "stop", "--timeout", "30", CONTAINER], timeout=50)
                with held(OWNER_LOCK, 10001):
                    require(owners() == [], "rollback_owner_not_quiesced")
                command([*compose(), "up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE])
                restored = inspect(CONTAINER)
                require(
                    restored["State"]["Running"]
                    and restored["Image"] == IMAGE
                    and mounts(restored) == prior_mounts
                    and owners() == [restored["Id"]]
                    and bystanders() == peers
                    and PARENT_LOCK.read_bytes() == parent_bytes,
                    "rollback_runtime_drift",
                )
                record["status"] = "ROLLED_BACK_RUNNING"
                atomic_json(JOURNAL, record)
            raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        raise SystemExit(2)
