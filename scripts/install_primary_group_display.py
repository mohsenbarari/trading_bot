"""Bind the existing dashboard projector to the live canonical Bot inbox.

No new collector, session, Product mode, model or data reset. The existing
conversation lock and projection quality gates remain authoritative.
"""

import argparse
from contextlib import contextmanager
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import time


DEPLOY_LOCK = Path("/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock")
SOURCE = Path("/srv/trading-bot/staging-data/coin-intelligence/private-pipeline-shadow/market-store/market-store.sqlite")
DESTINATION = Path("/srv/trading-bot/production-data/coin-intelligence/estimator-live/conversation/conversation_events.sqlite3")
CONVERSATION_LOCK = DESTINATION.parent.parent / ".conversation-writer.lock"
SERVICE = "coin-group-event-telegram.service"
TIMER = "coin-group-event-telegram.timer"
DROPIN = Path("/etc/systemd/system") / (SERVICE + ".d") / "private-primary-display.conf"


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=150)
    if result.returncode:
        raise RuntimeError("group_display_command_failed")
    return result.stdout


def render(release):
    return f'''[Unit]
Description=Project live PRIVATE_PRIMARY groups into the estimator dashboard

[Service]
EnvironmentFile=
Environment=PYTHONPATH={release}
ExecStart=
ExecStart=/usr/bin/flock --exclusive --timeout 30 {CONVERSATION_LOCK} /usr/bin/python3 {release}/scripts/project_group_market_to_estimator.py --market-store {SOURCE} --conversation-db {DESTINATION}
TimeoutStartSec=120
ReadOnlyPaths={release} {SOURCE.parent}
ReadWritePaths=
ReadWritePaths={DESTINATION.parent} {CONVERSATION_LOCK}
'''


@contextmanager
def locked(path):
    with open(path, "r") as stream:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("group_display_existing_writer_busy")
                time.sleep(0.1)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--ops-root", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch("[0-9a-f]{40}", args.release):
        raise RuntimeError("group_display_host_contract_invalid")
    for path in (SOURCE, DESTINATION, CONVERSATION_LOCK, DEPLOY_LOCK):
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise RuntimeError("group_display_path_invalid")
    if args.ops_root.resolve() != args.ops_root:
        raise RuntimeError("group_display_ops_path_invalid")
    container = json.loads(command(["docker", "inspect", "market-private-pipeline-primary-market-store-adapter-1"]))[0]
    runtime_env = dict(item.split("=", 1) for item in container["Config"]["Env"])
    if runtime_env.get("MARKET_PIPELINE_FEED_MODE") != "PRIVATE_PRIMARY" or not container["State"]["Running"]:
        raise RuntimeError("group_display_private_primary_not_active")
    mapped = [m for m in container["Mounts"] if m["Destination"] == "/var/lib/market-data/market-store"]
    if len(mapped) != 1 or Path(mapped[0]["Source"]) != SOURCE.parent:
        raise RuntimeError("group_display_store_identity_mismatch")
    release = Path("/srv/trading-bot/market-estimator-bridge-releases") / args.release
    args.ops_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.ops_root.stat().st_uid != 0 or args.ops_root.is_symlink():
        raise RuntimeError("group_display_ops_owner_invalid")
    args.ops_root.chmod(0o700)
    journal = args.ops_root / "group-display.json"
    if journal.exists() or DROPIN.exists() or DROPIN.is_symlink():
        raise RuntimeError("group_display_no_blind_repeat")
    for path in (SOURCE, DESTINATION):
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            table = "market_observations" if path == SOURCE else "canonical_group_projection"
            connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        finally:
            connection.close()
    if release.exists():
        if (release.is_symlink() or release.stat().st_uid != 0
                or (release / "RELEASE_SHA").read_text().strip() != args.release):
            raise RuntimeError("group_display_release_identity_invalid")
        for tracked in ("scripts/project_group_market_to_estimator.py", "core/market_intelligence/input_health.py"):
            if (release / tracked).read_bytes() != command(["git", "show", args.release + ":" + tracked]):
                raise RuntimeError("group_display_release_content_invalid")
    else:
        archive = command(["git", "archive", "--format=tar", args.release])
        release.mkdir(mode=0o755)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            # Exact committed tree only; reject links and traversals.
            members = bundle.getmembers()
            if any(m.issym() or m.islnk() or m.name.startswith("/") or ".." in Path(m.name).parts for m in members):
                raise RuntimeError("group_display_archive_path_invalid")
            bundle.extractall(release, members=members)
        (release / "RELEASE_SHA").write_text(args.release + "\n")
        for path in release.rglob("*"):
            path.chmod(path.stat().st_mode & ~0o222)
        release.chmod(0o555)
    base = Path("/etc/systemd/system") / SERVICE
    shutil.copyfile(base, args.ops_root / "prior.service")
    (args.ops_root / "prior.service").chmod(0o600)
    with locked(DEPLOY_LOCK):
        command(["systemctl", "stop", TIMER])
        command(["systemctl", "stop", SERVICE])
        try:
            with locked(CONVERSATION_LOCK):
                journal.write_text(json.dumps({"status": "PREPARED", "release": args.release,
                                               "source": "PRIVATE_PRIMARY", "data_deleted": False}))
                journal.chmod(0o600)
                DROPIN.parent.mkdir(exist_ok=True)
                DROPIN.write_text(render(release))
                DROPIN.chmod(0o644)
            command(["systemctl", "daemon-reload"])
            command(["systemctl", "start", SERVICE])
            command(["systemctl", "enable", "--now", TIMER])
            journal.write_text(json.dumps({"status": "APPLIED_LIVE", "release": args.release,
                                           "source": "PRIVATE_PRIMARY", "data_deleted": False}))
        except BaseException:
            command(["systemctl", "stop", SERVICE])
            if DROPIN.exists():
                # Move to the retained incident journal instead of deleting.
                shutil.move(str(DROPIN), str(args.ops_root / "failed-display.conf"))
            command(["systemctl", "daemon-reload"])
            command(["systemctl", "enable", "--now", TIMER])
            journal.write_text(json.dumps({"status": "ROLLED_BACK", "release": args.release,
                                           "data_deleted": False}))
            raise
    print(json.dumps({"status": "GROUP_DISPLAY_PRIMARY_ACTIVE", "release": args.release}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}))
        raise SystemExit(2)
