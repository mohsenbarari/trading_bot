"""Sender-only fair-scheduling hotfix; preserve state, peers, and rollback."""

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


OLD = "fb95bbdece703a09eb6ca3d6b9e2fccf04abbe3b"
NEW = "c9be832aef3792f369ae11e8fca95bcd7c42821f"
OLD_IMAGE = "sha256:f5762ed500ad0c0656394e46fc98e73801d483576fba09eb40251c5f20579b9f"
NEW_IMAGE = "sha256:7311f562ff577febeb5684af8e7d8039d5a13123c6e344fe63ed3a3a175d38dc"
PORTABLE = "f21129d556b2c504312505bc23448c1318035a2f05c9d9e518e18a07eb203bf0"
PARENT_RELEASE = "4ef8e6dcb2d361b763bd8b72dc730c1f978f564a"
ROLE = "market-fact-sync-worker"
PROJECT = "market-private-pipeline-primary"
CONTAINER = f"{PROJECT}-{ROLE}-1"
ROOT = Path("/srv/trading-bot/market-pipeline-releases") / PARENT_RELEASE
STATE_ROOT = Path("/srv/trading-bot/market-data-staging-shadow/state") / ROLE
STATE = STATE_ROOT / ROLE
OWNER_LOCK = STATE / "owner.lock"
PARENT_LOCK = Path("/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock")
PRIOR_OVERRIDE = Path("/srv/trading-bot/incident-recovery/20260907-transfer/sender-recovery-hotfix.override.json")
OPS = Path("/srv/trading-bot/incident-recovery/20260921-sender-fair-scheduling")
OVERRIDE = OPS / "sender-fair-scheduling.override.json"
JOURNAL = OPS / "handoff.json"


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def utc():
    return datetime.now(timezone.utc).isoformat()


def command(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, f"command_failed:{args[0]}:{result.returncode}")
    return result.stdout


def inspect(target, *, image=False):
    return json.loads(command(["docker", *( ["image"] if image else []), "inspect", target]))[0]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


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


def compose(*, new=False):
    args = [
        "docker", "compose", "-p", PROJECT, "--profile", "web", "--env-file",
        str(ROOT / "web.release.env"),
    ]
    for name in ("deploy/market-data/compose.yml", "deploy/market-data/compose.web.yml"):
        args.extend(("-f", str(ROOT / name)))
    args.extend(("-f", str(PRIOR_OVERRIDE)))
    if new:
        args.extend(("-f", str(OVERRIDE)))
    return args


def mounts(container):
    return sorted((item["Source"], item["Destination"], item.get("RW")) for item in container["Mounts"])


def bystanders():
    identifiers = command(["docker", "ps", "-q"]).split()
    rows = json.loads(command(["docker", "inspect", *identifiers])) if identifiers else []
    return {
        row["Id"]: (row["Image"], row["State"]["StartedAt"])
        for row in rows
        if row["Name"] != f"/{CONTAINER}"
    }


def owners():
    identifiers = command(["docker", "ps", "-q"]).split()
    rows = json.loads(command(["docker", "inspect", *identifiers])) if identifiers else []
    return [
        row["Id"]
        for row in rows
        if any(mount.get("Source") == str(STATE_ROOT) for mount in row["Mounts"])
    ]


def expected_override():
    return {
        "services": {
            ROLE: {
                "image": NEW_IMAGE,
                "restart": "unless-stopped",
                "environment": {"MARKET_PIPELINE_RELEASE_SHA": NEW},
                "labels": {"org.opencontainers.image.revision": NEW},
            }
        }
    }


def validate_config(prior, candidate):
    before, after = prior["services"], candidate["services"]
    require(set(before) == set(after), "service_set_drift")
    require(all(before[name] == after[name] for name in before if name != ROLE), "unrelated_service_drift")
    expected = json.loads(json.dumps(before[ROLE]))
    expected["image"] = NEW_IMAGE
    expected["restart"] = "unless-stopped"
    expected["environment"]["MARKET_PIPELINE_RELEASE_SHA"] = NEW
    expected.setdefault("labels", {})["org.opencontainers.image.revision"] = NEW
    require(expected == after[ROLE], "unexpected_target_config_drift")
    for key in ("networks", "volumes", "secrets", "configs"):
        require(prior.get(key) == candidate.get(key), "infrastructure_config_drift")


def read_health():
    return json.loads((STATE / "health.json").read_text())


def live_probe(*, release, image, prior_mounts, started_at, minimum_acknowledged):
    container = inspect(CONTAINER)
    require(
        container["State"]["Running"]
        and container["Image"] == image
        and container["RestartCount"] == 0,
        "runtime_not_stable",
    )
    require(mounts(container) == prior_mounts, "runtime_mount_drift")
    require(owners() == [container["Id"]], "sender_owner_overlap")
    health = read_health()
    updated = datetime.fromisoformat(health["updated_at_utc"].replace("Z", "+00:00"))
    require(
        health.get("release_sha") == release
        and updated.timestamp() >= started_at
        and time.time() - updated.timestamp() < 60,
        "heartbeat_stale",
    )
    require(
        health.get("schema") == "market_fact_sync/1.0"
        and health.get("mode") == "live"
        and health.get("private_transport_only") is True
        and health.get("status") in {"live-ready", "live-degraded"},
        "sender_contract_drift",
    )
    require(health.get("dead_letter_count") == 0, "sender_delivery_blocked")
    require(int(health.get("acknowledged") or 0) > minimum_acknowledged, "ack_progress_missing")
    require(container["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped", "restart_policy_drift")
    return {
        "container_id": container["Id"],
        "docker_health": container["State"].get("Health", {}).get("Status"),
        "queue_depth": health.get("queue_depth"),
        "acknowledged": health.get("acknowledged"),
        "dead_letter_count": 0,
        "updated_at_utc": health.get("updated_at_utc"),
    }


def wait_live(**kwargs):
    deadline = time.monotonic() + 180
    last = None
    while time.monotonic() < deadline:
        try:
            return live_probe(**kwargs)
        except (KeyError, RuntimeError, ValueError) as exc:
            last = type(exc).__name__
        time.sleep(5)
    raise RuntimeError(f"sender_health_timeout:{last}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0, "root_required")
    require(OPS.resolve() == OPS, "operations_path_invalid")
    OPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(OPS, 0o700)
    require(not JOURNAL.exists(), "journal_exists_no_blind_repeat")
    require(PRIOR_OVERRIDE.is_file(), "prior_override_missing")
    safe_file(PRIOR_OVERRIDE, 0)

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
        override = expected_override()
        if OVERRIDE.exists():
            safe_file(OVERRIDE, 0)
            require(json.loads(OVERRIDE.read_text()) == override, "override_drift")
        else:
            atomic_json(OVERRIDE, override)
        safe_file(OVERRIDE, 0)
        prior_container = inspect(CONTAINER)
        require(
            prior_container["Image"] == OLD_IMAGE
            and prior_container["State"]["Running"]
            and prior_container["RestartCount"] == 0,
            "prior_runtime_drift",
        )
        require(owners() == [prior_container["Id"]], "prior_owner_overlap")
        target = inspect(NEW_IMAGE, image=True)
        portable = {key: target.get(key) for key in ("Architecture", "Config", "Created", "Os", "RootFS")}
        require(digest(portable) == PORTABLE, "image_content_mismatch")
        prior_config = json.loads(command([*compose(), "config", "--format", "json"]))
        candidate_config = json.loads(command([*compose(new=True), "config", "--format", "json"]))
        validate_config(prior_config, candidate_config)
        prior_mounts = mounts(prior_container)
        untouched = bystanders()
        prior_health = read_health()
        require(
            prior_health.get("release_sha") == OLD
            and prior_health.get("schema") == "market_fact_sync/1.0"
            and prior_health.get("mode") == "live"
            and prior_health.get("dead_letter_count") == 0,
            "prior_sender_contract_drift",
        )
        minimum_acknowledged = int(prior_health.get("acknowledged") or 0)
        record = {
            "schema": "market_sender_fair_scheduling_handoff/1.0",
            "status": "PREPARED",
            "created_at_utc": utc(),
            "old_release": OLD,
            "new_release": NEW,
            "old_image": OLD_IMAGE,
            "new_image": NEW_IMAGE,
            "data_deleted": False,
            "capture_changed": False,
            "processor_changed": False,
            "product_changed": False,
            "parent_handoff_changed": False,
            "mounts": prior_mounts,
            "bystanders": untouched,
            "prior_acknowledged": minimum_acknowledged,
        }
        if not args.apply:
            print(json.dumps({"status": "PREFLIGHT_PASS", "old_release": OLD, "new_release": NEW}))
            return
        atomic_json(JOURNAL, record)
        stopped = False
        try:
            stopped = True
            command(["docker", "stop", "--timeout", "30", CONTAINER], timeout=50)
            with held(OWNER_LOCK, 10001):
                require(owners() == [], "old_owner_not_quiesced")
                record["status"] = "OWNER_QUIESCED"
                atomic_json(JOURNAL, record)
            started = time.time()
            command([*compose(new=True), "up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE])
            record["live_probe"] = wait_live(
                release=NEW,
                image=NEW_IMAGE,
                prior_mounts=prior_mounts,
                started_at=started,
                minimum_acknowledged=minimum_acknowledged,
            )
            require(bystanders() == untouched, "unrelated_container_changed")
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
                started = time.time()
                command([*compose(), "up", "-d", "--no-deps", "--no-build", "--pull", "never", ROLE])
                record["rollback_probe"] = wait_live(
                    release=OLD,
                    image=OLD_IMAGE,
                    prior_mounts=prior_mounts,
                    started_at=started,
                    minimum_acknowledged=minimum_acknowledged,
                )
                record["status"] = "ROLLED_BACK_LIVE"
                atomic_json(JOURNAL, record)
            raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        raise SystemExit(2)
