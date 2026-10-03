"""Commit-bound scoped Market handoff using the complete active Compose chain."""

from contextlib import contextmanager
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time


PROJECT = "market-private-pipeline-primary"
LOCK = Path("/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock")


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def command(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, f"command_failed:{args[0]}:{result.returncode}")
    return result.stdout


def inspect(target):
    return json.loads(command(["docker", "inspect", target]))[0]


def safe_file(path, uid=0):
    require(path.is_file() and path.resolve() == path, "unsafe_file_path")
    info = path.stat()
    require(info.st_uid == uid and info.st_nlink == 1 and not info.st_mode & 0o022, "unsafe_file_metadata")


def write_json(path, value):
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(path, uid=0):
    safe_file(path, uid)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def portable_digest(image):
    value = {k: image.get(k) for k in ("Architecture", "Config", "Created", "Os", "RootFS")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mounts(container):
    return sorted((m["Source"], m["Destination"], m.get("RW")) for m in container["Mounts"])


def bystanders(target):
    ids = command(["docker", "ps", "-q"]).split()
    rows = json.loads(command(["docker", "inspect", *ids])) if ids else []
    return {
        row["Id"]: (row["Image"], row["State"]["StartedAt"])
        for row in rows
        if row["Name"] != f"/{target}"
        and (row["Config"].get("Labels") or {}).get("com.docker.compose.project")
        and (row["Config"].get("Labels") or {}).get("com.docker.compose.oneoff") != "True"
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("market-fact-receiver", "estimator-snapshot-receiver", "market-fact-sync-worker", "market-store-adapter"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--portable-digest", required=True)
    parser.add_argument("--ops-root", type=Path, required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, "root_required")
    require(args.ops_root.resolve() == args.ops_root, "ops_path_invalid")
    args.ops_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.ops_root.chmod(0o700)
    journal_path = args.ops_root / "handoff.json"
    require(not journal_path.exists(), "journal_exists_no_blind_repeat")
    name = f"{PROJECT}-{args.role}-1"
    image = json.loads(command(["docker", "image", "inspect", args.image]))[0]
    require(portable_digest(image) == args.portable_digest, "image_content_mismatch")
    require(image["Config"]["Labels"].get("org.opencontainers.image.revision") == args.release, "image_revision_mismatch")
    new_image = image["Id"]
    old = inspect(name)
    labels = old["Config"]["Labels"]
    require(old["State"]["Running"] and labels.get("com.docker.compose.project") == PROJECT and labels.get("com.docker.compose.service") == args.role, "prior_runtime_drift")
    old_mounts = mounts(old)
    state_source = next(m["Source"] for m in old["Mounts"] if m["Destination"] == "/var/lib/market-data/state")
    owner_lock = Path(state_source) / args.role / "owner.lock"
    environment = Path(labels["com.docker.compose.project.environment_file"])
    files = [Path(p) for p in labels["com.docker.compose.project.config_files"].split(",")]
    for path in [environment, *files]:
        safe_file(path)
    compose = ["docker", "compose", "-p", PROJECT, "--profile", "bot" if args.role in {"market-fact-receiver", "market-store-adapter"} else "web", "--env-file", str(environment)]
    for path in files:
        compose.extend(("-f", str(path)))
    before = json.loads(command([*compose, "config", "--format", "json"]))
    override_path = args.ops_root / "receiver-resilience.override.json"
    write_json(override_path, {"services": {args.role: {
        "image": new_image, "restart": "unless-stopped",
        "environment": {"MARKET_PIPELINE_RELEASE_SHA": args.release},
        "labels": {"org.opencontainers.image.revision": args.release},
        "healthcheck": {"timeout": "8s"},
    }}})
    candidate = [*compose, "-f", str(override_path)]
    after = json.loads(command([*candidate, "config", "--format", "json"]))
    expected = json.loads(json.dumps(before))
    target = expected["services"][args.role]
    target.update(image=new_image, restart="unless-stopped")
    target["environment"]["MARKET_PIPELINE_RELEASE_SHA"] = args.release
    target.setdefault("labels", {})["org.opencontainers.image.revision"] = args.release
    target["healthcheck"]["timeout"] = "8s"
    require(expected == after, "unexpected_configuration_drift")
    untouched = bystanders(name)
    with locked(LOCK):
        journal = {"status": "PREPARED", "old_image": old["Image"], "new_image": new_image,
                   "role": args.role, "release": args.release, "data_deleted": False}
        write_json(journal_path, journal)
        try:
            command(["docker", "stop", "--timeout", "30", name], timeout=50)
            with locked(owner_lock, 10001):
                require(not inspect(name)["State"]["Running"], "old_owner_not_stopped")
            command([*candidate, "up", "-d", "--no-deps", "--no-build", "--pull", "never", args.role])
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                current = inspect(name)
                require(current["Image"] == new_image and mounts(current) == old_mounts, "runtime_identity_drift")
                if current["State"]["Running"]:
                    report = json.loads((Path(state_source) / args.role / "health.json").read_text())
                    updated = datetime.fromisoformat(str(report["updated_at_utc"]).replace("Z", "+00:00"))
                    started = datetime.fromisoformat(current["State"]["StartedAt"].replace("Z", "+00:00"))
                    if (report.get("release_sha") != args.release or updated < started
                            or not 0 <= (datetime.now(timezone.utc) - updated).total_seconds() <= 30):
                        time.sleep(3)
                        continue
                    if args.role == "market-fact-sync-worker":
                        if report.get("release_sha") == args.release and int(report.get("acknowledged") or 0) > 0 and report.get("dead_letter_count") == 0:
                            break  # A catch-up backlog is readiness, not liveness.
                    elif current["State"].get("Health", {}).get("Status") == "healthy":
                        break
                time.sleep(3)
            else:
                raise RuntimeError("new_receiver_health_timeout")
            require(bystanders(name) == untouched, "unrelated_container_changed")
            journal["status"] = "APPLIED_LIVE"
            write_json(journal_path, journal)
            print(json.dumps(journal))
        except BaseException:
            journal["status"] = "ROLLBACK_REQUIRED"
            write_json(journal_path, journal)
            command(["docker", "stop", "--timeout", "30", name], timeout=50)
            with locked(owner_lock, 10001):
                require(not inspect(name)["State"]["Running"], "rollback_owner_not_stopped")
            command([*compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never", args.role])
            journal["status"] = "ROLLED_BACK"
            write_json(journal_path, journal)
            raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason_code": str(exc)}))
        raise SystemExit(2)
