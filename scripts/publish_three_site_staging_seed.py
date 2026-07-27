#!/usr/bin/env python3
"""Fail-closed shim for the retired single-source v1 seed publisher."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SOURCE_ROLES = ("bot_fi", "webapp_fi")
LEGACY_DISABLED = (
    "legacy v1 seed publication is disabled; use the sealed six-object v2 "
    "campaign publisher"
)


class SeedPublicationError(RuntimeError):
    """The retired v1 publication path was requested."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def confirmation_phrase(campaign_id: str, source_role: str, backup_hash: str) -> str:
    return f"publish-seed:{campaign_id}:{source_role}:{backup_hash}"


def build_plan(
    *, source_role: str, backup: dict[str, Any], inventory_result: dict[str, Any]
) -> dict[str, Any]:
    backup_hash = hashlib.sha256(_canonical_bytes(backup)).hexdigest()
    return {
        "status": "blocked",
        "reason": LEGACY_DISABLED,
        "campaign_id": inventory_result["campaign_id"],
        "release_sha": inventory_result["release_sha"],
        "source_role": source_role,
        "backup_manifest_sha256": backup_hash,
        "required_confirmation": confirmation_phrase(
            inventory_result["campaign_id"],
            source_role,
            backup_hash,
        ),
    }


def execute(
    args: argparse.Namespace,
    *,
    inventory: dict[str, Any],
    inventory_result: dict[str, Any],
    backup: dict[str, Any],
) -> dict[str, Any]:
    del args, inventory, inventory_result, backup
    raise SeedPublicationError(LEGACY_DISABLED)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-role", choices=SOURCE_ROLES, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--recipient", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    _parser().parse_args(argv)
    print(
        json.dumps(
            {
                "status": "blocked",
                "error": LEGACY_DISABLED,
                "error_class": SeedPublicationError.__name__,
            },
            sort_keys=True,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
