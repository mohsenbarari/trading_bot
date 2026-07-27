#!/usr/bin/env python3
"""Render a root-only, data-only WA-IR runtime env without printing values."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from core.secure_file_io import read_secure_text, write_secure_atomic_bytes


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
OVERRIDES = {
    "DARK_STANDBY_MODE": "1",
    "TZ": "UTC",
    "PGTZ": "UTC",
}
DATABASE_ONLY_KEYS = frozenset(
    {
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_MAX_CONNECTIONS",
        "POSTGRES_SHARED_BUFFERS",
        "POSTGRES_EFFECTIVE_CACHE_SIZE",
        "POSTGRES_WORK_MEM",
        "POSTGRES_MAINTENANCE_WORK_MEM",
        "POSTGRES_RANDOM_PAGE_COST",
        "POSTGRES_EFFECTIVE_IO_CONCURRENCY",
        "POSTGRES_CHECKPOINT_TIMEOUT",
        "POSTGRES_MAX_WAL_SIZE",
        "POSTGRES_MIN_WAL_SIZE",
        "POSTGRES_WAL_BUFFERS",
        "DOCKER_LOG_MAX_SIZE",
        "DOCKER_LOG_MAX_FILE",
    }
)
REQUIRED_DATABASE_KEYS = frozenset({"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"})


def parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in read_secure_text(path, label="source runtime environment").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def render(
    source: Path,
    output: Path,
    release_sha: str,
    operation_id: str,
    db_image_id: str,
) -> None:
    if not SHA_RE.fullmatch(release_sha):
        raise ValueError("release SHA must be exactly 40 lowercase hex characters")
    if not UUID_RE.fullmatch(operation_id):
        raise ValueError("operation id must be a nonzero canonical UUID")
    if not IMAGE_ID_RE.fullmatch(db_image_id):
        raise ValueError("database image ID must be sha256 plus 64 lowercase hex characters")
    source_values = parse(source)
    missing = sorted(key for key in REQUIRED_DATABASE_KEYS if not source_values.get(key))
    if missing:
        raise ValueError("source environment lacks database-only keys: " + ",".join(missing))
    values = {key: source_values[key] for key in DATABASE_ONLY_KEYS if source_values.get(key)}
    values.update(OVERRIDES)
    values["RELEASE_SHA"] = release_sha
    values["DARK_STANDBY_OPERATION_ID"] = operation_id
    values["DARK_STANDBY_DB_IMAGE_ID"] = db_image_id
    rendered = (
        "# Generated DB-only WebApp-IR environment; root-only database credential.\n"
        + "\n".join(f"{key}={values[key]}" for key in sorted(values))
        + "\n"
    )
    write_secure_atomic_bytes(
        output,
        rendered.encode("utf-8"),
        label="dark-standby runtime environment",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--db-image-id", required=True)
    args = parser.parse_args()
    render(
        Path(args.source),
        Path(args.output),
        args.release_sha,
        args.operation_id,
        args.db_image_id,
    )
    print("dark-standby runtime env rendered; values suppressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
