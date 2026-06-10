#!/usr/bin/env python3
"""Sample local and Iran sync-health endpoints from the foreign host."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from deploy_config import resolve_deploy_settings


OBSERVABILITY_API_KEY_HEADER = "X-Observability-Api-Key"


def parse_args() -> argparse.Namespace:
    defaults = resolve_deploy_settings()
    parser = argparse.ArgumentParser(description="Sample local and Iran sync-health endpoints.")
    parser.add_argument("--env-file", default=".env", help="Local env file path.")
    parser.add_argument("--local-url", default="http://127.0.0.1:8000/api/sync/health", help="Local sync-health URL.")
    parser.add_argument("--iran-url", default="http://127.0.0.1:8000/api/sync/health", help="Iran sync-health URL executed over SSH.")
    parser.add_argument("--iran-host", default=os.getenv("IRAN_SSH_TARGET", defaults["IRAN_SSH_TARGET"]), help="Iran SSH target.")
    parser.add_argument("--iran-dir", default=os.getenv("IRAN_DIR", defaults["IRAN_DIR"]), help="Iran repo directory.")
    parser.add_argument("--iran-port", default=os.getenv("IRAN_SSH_PORT", defaults["IRAN_SSH_PORT"]), help="Iran SSH port.")
    parser.add_argument("--ssh-option", action="append", default=["StrictHostKeyChecking=no"], help="Additional ssh -o options.")
    parser.add_argument("--skip-iran", action="store_true", help="Only sample the local host.")
    return parser.parse_args()


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def optional_observability_key() -> str:
    return os.getenv("OBSERVABILITY_API_KEY", "").strip()


def fetch_sync_health(url: str, api_key: str) -> dict:
    headers = {OBSERVABILITY_API_KEY_HEADER: api_key} if api_key else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def build_iran_ssh_command(args: argparse.Namespace) -> list[str]:
    ssh_cmd = ["ssh", "-p", str(args.iran_port)]
    for option in args.ssh_option:
        ssh_cmd.extend(["-o", option])
    remote = (
        f"cd {shlex.quote(args.iran_dir)} && "
        "set -a; [ ! -f .env ] || . ./.env; set +a; "
        "python3 scripts/sample_sync_health.py --skip-iran"
    )
    ssh_cmd.extend([args.iran_host, remote])
    return ssh_cmd


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    api_key = optional_observability_key()

    failures = 0
    samples: dict[str, dict | str] = {}
    try:
        samples["local"] = fetch_sync_health(args.local_url, api_key)
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        samples["local"] = f"{type(exc).__name__}: {exc}"
        failures += 1

    if not args.skip_iran:
        try:
            completed = subprocess.run(
                build_iran_ssh_command(args),
                check=True,
                capture_output=True,
                text=True,
            )
            samples["iran"] = json.loads(completed.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            stderr = getattr(exc, "stderr", "") or ""
            stdout = getattr(exc, "stdout", "") or ""
            detail = stderr.strip() or stdout.strip() or str(exc)
            samples["iran"] = f"{type(exc).__name__}: {detail}"
            failures += 1

    output = {"status": "ok" if failures == 0 else "partial", "failures": failures, "samples": samples}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
