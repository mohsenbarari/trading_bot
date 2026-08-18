#!/usr/bin/env python3
"""Add the production commodity catalog to the Iran-authority staging database.

The production catalog is supplied as a small, non-sensitive JSON manifest.
This command never connects to production itself and never deletes or renames
staging rows.  Applying a manifest is explicitly gated to a database whose
name and runtime environment are both staging-scoped.  Normal ORM events are
enabled so the committed additions enter the cross-server sync outbox.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MANIFEST_SCHEMA_VERSION = 1
_DIGIT_PATTERN = re.compile(r"[0-9۰-۹٠-٩]")


class StagingCatalogSyncError(RuntimeError):
    """A manifest or staging safety contract was violated."""


@dataclass(frozen=True, slots=True)
class ManifestCommodity:
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogPlan:
    commodities_to_add: tuple[ManifestCommodity, ...]
    aliases_to_add: tuple[tuple[str, str], ...]
    existing_commodity_names: tuple[str, ...]
    existing_aliases: tuple[str, ...]


def _normalized_text(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 96:
        raise StagingCatalogSyncError(f"{field_name}_invalid")
    if _DIGIT_PATTERN.search(text):
        raise StagingCatalogSyncError(f"{field_name}_contains_digits")
    return text


def normalize_manifest(payload: object) -> tuple[ManifestCommodity, ...]:
    if not isinstance(payload, dict):
        raise StagingCatalogSyncError("manifest_object_required")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise StagingCatalogSyncError("manifest_schema_version_invalid")
    if payload.get("source") != "production-read-only":
        raise StagingCatalogSyncError("manifest_source_invalid")
    rows = payload.get("commodities")
    if not isinstance(rows, list) or not rows:
        raise StagingCatalogSyncError("manifest_commodities_required")

    commodities: list[ManifestCommodity] = []
    commodity_names: set[str] = set()
    alias_owners: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise StagingCatalogSyncError("manifest_commodity_object_required")
        name = _normalized_text(row.get("name"), field_name=f"commodity_{index}_name")
        if name in commodity_names:
            raise StagingCatalogSyncError("manifest_commodity_duplicate")
        commodity_names.add(name)
        aliases_raw = row.get("aliases")
        if not isinstance(aliases_raw, list):
            raise StagingCatalogSyncError("manifest_aliases_list_required")
        aliases: list[str] = []
        for alias_index, alias_value in enumerate(aliases_raw):
            alias = _normalized_text(
                alias_value,
                field_name=f"commodity_{index}_alias_{alias_index}",
            )
            owner = alias_owners.get(alias)
            if owner is not None and owner != name:
                raise StagingCatalogSyncError("manifest_alias_owner_conflict")
            if owner is None:
                aliases.append(alias)
                alias_owners[alias] = name
        commodities.append(ManifestCommodity(name=name, aliases=tuple(aliases)))
    for item in commodities:
        for alias in item.aliases:
            if alias in commodity_names and alias != item.name:
                raise StagingCatalogSyncError("manifest_alias_commodity_name_conflict")
    return tuple(sorted(commodities, key=lambda item: item.name))


def build_catalog_plan(
    manifest: Iterable[ManifestCommodity],
    *,
    existing_commodities: dict[str, set[str]],
    existing_alias_owners: dict[str, str],
) -> CatalogPlan:
    additions: list[ManifestCommodity] = []
    aliases_to_add: list[tuple[str, str]] = []
    existing_names: list[str] = []
    existing_aliases: list[str] = []
    for item in manifest:
        name_owner = existing_alias_owners.get(item.name)
        if name_owner is not None and name_owner != item.name:
            raise StagingCatalogSyncError("staging_commodity_name_alias_conflict")
        if item.name in existing_commodities:
            existing_names.append(item.name)
        else:
            additions.append(item)
        known_for_commodity = existing_commodities.get(item.name, set())
        for alias in item.aliases:
            if alias in existing_commodities and alias != item.name:
                raise StagingCatalogSyncError(
                    f"staging_alias_commodity_name_conflict:{alias}"
                )
            owner = existing_alias_owners.get(alias)
            if owner is not None and owner != item.name:
                raise StagingCatalogSyncError(
                    f"staging_alias_owner_conflict:{alias}"
                )
            if alias in known_for_commodity or owner == item.name:
                existing_aliases.append(alias)
            else:
                aliases_to_add.append((item.name, alias))
    return CatalogPlan(
        commodities_to_add=tuple(additions),
        aliases_to_add=tuple(aliases_to_add),
        existing_commodity_names=tuple(sorted(existing_names)),
        existing_aliases=tuple(sorted(existing_aliases)),
    )


def validate_staging_target(
    expected_database_name: str,
    *,
    expected_server_mode: str,
) -> None:
    environment = str(os.getenv("ENVIRONMENT") or "").strip().lower()
    if environment != "staging":
        raise StagingCatalogSyncError("environment_must_be_staging")
    server_mode = str(os.getenv("SERVER_MODE") or "").strip().lower()
    if expected_server_mode != "iran" or server_mode != expected_server_mode:
        raise StagingCatalogSyncError("catalog_import_requires_iran_authority")
    expected = str(expected_database_name or "").strip()
    if not expected or "staging" not in expected.lower() or "prod" in expected.lower():
        raise StagingCatalogSyncError("expected_database_name_not_staging")
    raw_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not raw_url:
        raise StagingCatalogSyncError("database_url_missing")
    actual = unquote(urlsplit(raw_url).path.lstrip("/").split("/", 1)[0])
    if actual != expected:
        raise StagingCatalogSyncError("database_url_expected_name_mismatch")


async def _load_existing(session: Any) -> tuple[dict[str, Any], dict[str, str]]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from models.commodity import Commodity

    result = await session.execute(
        select(Commodity).options(selectinload(Commodity.aliases)).order_by(Commodity.id)
    )
    commodities = list(result.scalars().unique().all())
    by_name = {str(item.name): item for item in commodities}
    aliases: dict[str, str] = {}
    for commodity in commodities:
        for alias in commodity.aliases:
            aliases[str(alias.alias)] = str(commodity.name)
    return by_name, aliases


async def synchronize_catalog(
    manifest: tuple[ManifestCommodity, ...],
    *,
    apply: bool,
) -> dict[str, object]:
    from bot.utils.redis_helpers import invalidate_commodity_cache
    from core.cache import invalidate_commodities_cache
    from core.db import AsyncSessionLocal
    from core.events import setup_event_listeners
    from models.commodity import Commodity, CommodityAlias

    setup_event_listeners()
    async with AsyncSessionLocal() as session:
        by_name, alias_owners = await _load_existing(session)
        plan = build_catalog_plan(
            manifest,
            existing_commodities={
                name: {str(alias.alias) for alias in commodity.aliases}
                for name, commodity in by_name.items()
            },
            existing_alias_owners=alias_owners,
        )
        if apply:
            for item in plan.commodities_to_add:
                commodity = Commodity(name=item.name)
                session.add(commodity)
                await session.flush()
                by_name[item.name] = commodity
            for commodity_name, alias_name in plan.aliases_to_add:
                commodity = by_name.get(commodity_name)
                if commodity is None:
                    raise StagingCatalogSyncError(
                        f"staging_commodity_missing_after_insert:{commodity_name}"
                    )
                session.add(
                    CommodityAlias(
                        commodity_id=int(commodity.id),
                        alias=alias_name,
                    )
                )
            await session.commit()
            await invalidate_commodities_cache()
            await invalidate_commodity_cache()
        else:
            await session.rollback()
    return {
        "applied": apply,
        "commodities_added": [item.name for item in plan.commodities_to_add],
        "aliases_added": [alias for _, alias in plan.aliases_to_add],
        "commodities_existing_count": len(plan.existing_commodity_names),
        "aliases_existing_count": len(plan.existing_aliases),
    }


def _read_manifest(path: str) -> tuple[object, bytes]:
    raw = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
    if len(raw) > 256 * 1024:
        raise StagingCatalogSyncError("manifest_too_large")
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StagingCatalogSyncError("manifest_json_invalid") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="JSON path or - for stdin")
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--expected-server-mode", required=True, choices=("iran",))
    parser.add_argument("--apply", action="store_true")
    return parser


async def _run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_staging_target(
        args.expected_database_name,
        expected_server_mode=args.expected_server_mode,
    )
    payload, raw = _read_manifest(args.manifest)
    manifest = normalize_manifest(payload)
    result = await synchronize_catalog(manifest, apply=bool(args.apply))
    print(
        json.dumps(
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "status": "applied" if args.apply else "dry-run",
                "manifest_sha256": sha256(raw).hexdigest(),
                "manifest_commodity_count": len(manifest),
                "manifest_alias_count": sum(len(item.aliases) for item in manifest),
                **result,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(_run(argv))
    except StagingCatalogSyncError as exc:
        print(
            json.dumps(
                {"status": "failed", "reason": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "reason": type(exc).__name__},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
