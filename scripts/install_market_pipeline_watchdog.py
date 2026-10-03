"""Install the scoped watchdog timer after checking the live host inventory."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-role", choices=("bot", "web"), required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--ops-root", type=Path, required=True)
    parser.add_argument("--deployment-lock", type=Path, required=True)
    parser.add_argument("--project", default="market-private-pipeline-primary")
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.state_root.is_dir() or not args.deployment_lock.is_file():
        raise RuntimeError("watchdog_install_host_invalid")
    paths = (args.state_root, args.ops_root, args.deployment_lock)
    if any(str(p) != str(p.resolve()) or any(c.isspace() for c in str(p)) for p in paths):
        raise RuntimeError("watchdog_install_path_invalid")
    args.ops_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.ops_root.stat().st_uid != 0 or args.ops_root.is_symlink():
        raise RuntimeError("watchdog_install_owner_invalid")
    args.ops_root.chmod(0o700)
    destination = args.ops_root / "market_pipeline_watchdog.py"
    shutil.copyfile(Path(__file__).with_name("market_pipeline_watchdog.py"), destination)
    destination.chmod(0o700)
    command = ["/usr/bin/python3", str(destination), "--host-role", args.host_role,
               "--project", args.project, "--state-root", str(args.state_root),
               "--ops-root", str(args.ops_root), "--deployment-lock", str(args.deployment_lock)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    service = "market-pipeline-watchdog"
    unit = f'''[Unit]
Description=Recover stalled private Market pipeline services
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart={" ".join(command)} --apply
TimeoutStartSec=90
UMask=0077
NoNewPrivileges=true
ProtectHome=read-only
ProtectSystem=strict
ReadWritePaths={args.ops_root}
'''
    timer = '''[Unit]
Description=Check private Market pipeline every 15 seconds

[Timer]
OnBootSec=45s
OnUnitInactiveSec=15s
AccuracySec=1s
Persistent=true

[Install]
WantedBy=timers.target
'''
    for suffix, value in (("service", unit), ("timer", timer)):
        path = Path("/etc/systemd/system") / f"{service}.{suffix}"
        if path.is_symlink():
            raise RuntimeError("watchdog_unit_symlink")
        path.write_text(value)
        path.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", f"{service}.timer"], check=True, capture_output=True)
    subprocess.run(["systemctl", "is-active", f"{service}.timer"], check=True, capture_output=True)
    print(json.dumps({"status": "WATCHDOG_TIMER_ACTIVE", "host_role": args.host_role, "interval_seconds": 15}))


if __name__ == "__main__":
    main()
