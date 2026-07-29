#!/usr/bin/env python3
"""Retired direct convergence exporter.

The historical exporter imported the collector into an ambient interpreter and
then performed Object Storage egress.  That bypassed the held-FD, Git-bound
observer boundary, so it is deliberately unavailable.  Descriptor parsing is
retained only for legacy plan validation tests; no runtime snapshot, credential
import, Object Storage action, or evidence publication can occur here.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sys
from typing import Any
from uuid import UUID

SCHEMA = "three-site-staging-convergence-snapshot-export-v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")


class ConvergenceExportError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConvergenceExportError("export descriptor has duplicate fields")
        result[key] = value
    return result


def _descriptor(
    encoded: str,
    *,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
) -> dict[str, Any]:
    if len(encoded) > 32 * 1024:
        raise ConvergenceExportError("export descriptor exceeds the control-plane bound")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ConvergenceExportError) as exc:
        raise ConvergenceExportError("export descriptor is not strict base64 JSON") from exc
    fields = {"schema", "campaign_id", "release_sha", "plan_sha256", "upload"}
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != SCHEMA
        or value.get("campaign_id") != campaign_id
        or value.get("release_sha") != release_sha
        or value.get("plan_sha256") != plan_sha256
    ):
        raise ConvergenceExportError("export descriptor identity is invalid")
    upload = value["upload"]
    if not isinstance(upload, dict):
        raise ConvergenceExportError("export upload descriptor is invalid")
    # This parser is retained for old plan tests only.  The returned structure
    # is not actionable in this module because ``export`` always fails closed.
    return upload


def _identity(campaign_id: str, release_sha: str, plan_sha256: str) -> None:
    try:
        if str(UUID(campaign_id)) != campaign_id:
            raise ValueError
        if SHA40.fullmatch(release_sha) is None or SHA256.fullmatch(plan_sha256) is None:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ConvergenceExportError("convergence export identity is invalid") from exc
    return


async def export(
    *,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
    max_rows_per_table: int,
    upload: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed: only the launcher-bound observer may produce evidence."""

    del campaign_id, release_sha, plan_sha256, max_rows_per_table, upload
    raise ConvergenceExportError(
        "legacy direct convergence exporter is disabled; no unbound egress is permitted"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--upload-json-base64", required=True)
    parser.add_argument("--max-rows-per-table", type=int, default=10000)
    args = parser.parse_args(argv)
    try:
        _identity(args.campaign_id, args.release_sha, args.plan_sha256)
        upload = _descriptor(
            args.upload_json_base64,
            campaign_id=args.campaign_id,
            release_sha=args.release_sha,
            plan_sha256=args.plan_sha256,
        )
        raise ConvergenceExportError(
            "legacy direct convergence exporter is disabled; no unbound egress is permitted"
        )
    except (ConvergenceExportError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
