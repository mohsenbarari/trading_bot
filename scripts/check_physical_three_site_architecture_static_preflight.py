#!/usr/bin/env python3
"""Run the bounded, default-off three-site architecture static lint.

This command reads repository text only after ``--enable``.  It never opens a
host, provider, Object Storage, database, Docker, SSH, or credential boundary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.physical_three_site_architecture_static_preflight import (  # noqa: E402
    PHYSICAL_THREE_SITE_ARCHITECTURE_STATIC_PREFLIGHT_SCHEMA,
    PhysicalThreeSiteArchitectureStaticPreflightConfig,
    PhysicalThreeSiteArchitectureStaticPreflightError,
    inspect_physical_three_site_architecture_static_preflight,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enable",
        action="store_true",
        help="Explicitly allow bounded local repository-text inspection.",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPO_ROOT,
        help="Local repository root to inspect; no host path or network URL is accepted.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = PhysicalThreeSiteArchitectureStaticPreflightConfig(
        enabled=bool(args.enable),
        repository_root=args.repository_root,
    )
    try:
        report = inspect_physical_three_site_architecture_static_preflight(config=config)
    except PhysicalThreeSiteArchitectureStaticPreflightError as exc:
        print(
            json.dumps(
                {
                    "schema": PHYSICAL_THREE_SITE_ARCHITECTURE_STATIC_PREFLIGHT_SCHEMA,
                    "status": "blocked",
                    "error": exc.code,
                    "static_only": True,
                    "execution_authorized": False,
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "schema": report.schema,
                "status": report.status,
                "approved_route_ids": report.approved_route_ids,
                "checked_artifact_count": len(report.checked_artifacts),
                "findings": [
                    {
                        "artifact_path": item.artifact_path,
                        "code": item.code,
                        "line": item.line,
                    }
                    for item in report.findings
                ],
                "static_only": report.static_only,
                "execution_authorized": report.execution_authorized,
            },
            sort_keys=True,
        )
    )
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
