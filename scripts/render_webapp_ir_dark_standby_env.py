#!/usr/bin/env python3
"""Render a root-only, data-only WA-IR runtime env without printing values."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
OVERRIDES = {
    "SERVER_MODE": "iran",
    "LOGICAL_AUTHORITY": "webapp",
    "PHYSICAL_SITE": "webapp_ir",
    "BACKGROUND_JOBS_ENABLED": "false",
    "WRITER_WITNESS_REQUIRED": "false",
    "WRITER_WITNESS_AUTO_RENEW_ENABLED": "false",
    "COMPOSE_PROJECT_NAME": "trading_bot_dark_ir",
    "TZ": "UTC",
    "PGTZ": "UTC",
}
FORBIDDEN_PREFIXES = ("ARVAN_S3_", "AWS_", "HETZNER_S3_")


def parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def render(source: Path, output: Path, release_sha: str) -> None:
    if not SHA_RE.fullmatch(release_sha):
        raise ValueError("release SHA must be exactly 40 lowercase hex characters")
    values = parse(source)
    for key in tuple(values):
        if key.startswith(FORBIDDEN_PREFIXES):
            del values[key]
    values.update(OVERRIDES)
    values["RELEASE_SHA"] = release_sha
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "# Generated data-only WebApp-IR environment. Do not start app/sync_worker.\n"
        + "\n".join(f"{key}={values[key]}" for key in sorted(values))
        + "\n",
        encoding="utf-8",
    )
    os.chmod(output, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--release-sha", required=True)
    args = parser.parse_args()
    render(Path(args.source), Path(args.output), args.release_sha)
    print("dark-standby runtime env rendered; values suppressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
