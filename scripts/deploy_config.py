#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


DEFAULT_MANIFEST = Path("deploy/production/online.env")
DEFAULTS = {
    "IRAN_HOST": "87.107.3.22",
    "IRAN_SSH_USER": "root",
    "IRAN_SSH_PORT": "22",
    "IRAN_PROJECT_DIR": "/srv/trading-bot/current",
}


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_deploy_settings(
    *,
    manifest_path: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(environ or os.environ)
    path = Path(manifest_path or env.get("DEPLOY_MANIFEST") or DEFAULT_MANIFEST)
    file_values = parse_env_file(path)

    resolved: dict[str, str] = {}
    for key, default in DEFAULTS.items():
        resolved[key] = env.get(key) or file_values.get(key) or default

    resolved["IRAN_PROJECT_DIR"] = (
        env.get("IRAN_PROJECT_DIR")
        or file_values.get("IRAN_PROJECT_DIR")
        or env.get("IRAN_DIR")
        or file_values.get("IRAN_DIR")
        or DEFAULTS["IRAN_PROJECT_DIR"]
    )
    resolved["IRAN_DIR"] = resolved["IRAN_PROJECT_DIR"]
    resolved["IRAN_SSH_TARGET"] = f"{resolved['IRAN_SSH_USER']}@{resolved['IRAN_HOST']}"
    resolved["IRAN_HOST_DISPLAY"] = resolved["IRAN_HOST"]
    resolved["DEPLOY_MANIFEST"] = str(path)
    return resolved


def shell_escape(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve shared deploy surface configuration.")
    parser.add_argument("--manifest", help="Path to the deployment manifest.")
    parser.add_argument("--format", choices={"value", "shell", "json"}, default="value")
    parser.add_argument("--key", help="Specific key to print when using value/json format.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    resolved = resolve_deploy_settings(manifest_path=args.manifest)

    if args.format == "json":
        if args.key:
            print(json.dumps({args.key: resolved[args.key]}, ensure_ascii=False))
        else:
            print(json.dumps(resolved, ensure_ascii=False, sort_keys=True))
        return 0

    if args.format == "shell":
        for key in sorted(resolved):
            print(f"{key}={shell_escape(resolved[key])}")
        return 0

    if not args.key:
        raise SystemExit("--key is required when --format=value")
    print(resolved[args.key])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
