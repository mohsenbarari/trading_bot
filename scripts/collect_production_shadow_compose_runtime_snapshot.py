#!/usr/bin/env python3
"""Container-safe, read-only convergence snapshot entrypoint.

This is intentionally not the descriptor-bound host collector.  It runs only
through the immutable Compose observer overlay, whose command and file digest
are pinned by the execution plan.  The release root is inferred from this
file's fixed location and is mounted read-only by that overlay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
from uuid import UUID


SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _release_root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if not root.is_dir() or root.joinpath("scripts").resolve() != Path(__file__).resolve().parent:
        raise RuntimeError("container collector release root differs")
    # Put the immutable release ahead of image/application paths.  The app
    # image itself remains pinned by digest in the generated overlay.
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return root


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--max-rows-per-table", required=True, type=int)
    parser.add_argument("--source-manifest-path", required=True)
    args = parser.parse_args(argv)
    try:
        if str(UUID(args.campaign_id)) != args.campaign_id:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise RuntimeError("container collector campaign identity is invalid") from exc
    if (
        SHA40_RE.fullmatch(args.release_sha) is None
        or SHA256_RE.fullmatch(args.plan_sha256) is None
        or not 1 <= args.max_rows_per_table <= 100_000
        or not Path(args.source_manifest_path).is_absolute()
    ):
        raise RuntimeError("container collector arguments are invalid")
    return args


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _arguments(argv)
        root = _release_root()
        if root.name != args.release_sha:
            raise RuntimeError("container collector release identity differs")
        from scripts.collect_three_site_staging_convergence_snapshot import collect_container_safe

        snapshot = asyncio.run(
            collect_container_safe(
                campaign_id=args.campaign_id,
                release_sha=args.release_sha,
                plan_sha256=args.plan_sha256,
                max_rows_per_table=args.max_rows_per_table,
                release_root=root,
                source_manifest_path=(
                    Path(args.source_manifest_path)
                    if args.source_manifest_path is not None
                    else None
                ),
            )
        )
        print(_canonical(snapshot))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
