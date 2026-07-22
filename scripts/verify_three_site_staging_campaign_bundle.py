#!/usr/bin/env python3
"""Verify the four role bundles as one closed three-site staging campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from scripts.render_three_site_staging_role_compose import parse_env_values
from scripts.verify_three_site_staging_inventory import load_inventory
from scripts.verify_three_site_staging_role_bundle import (
    EXPECTED_PEERS,
    ENV_PREFIX,
    PHYSICAL_SITE,
    RoleBundleError,
    _verify_bundle_source,
    verify_role_bundle,
)


ROLES = tuple(sorted(EXPECTED_PEERS))


class CampaignBundleError(RuntimeError):
    pass


def _pairwise_entries(values: dict[str, str], *, role: str) -> list[dict[str, str]]:
    if role == "witness":
        return []
    name = f"{ENV_PREFIX[role]}_DR_PAIRWISE_KEYS_JSON"
    try:
        payload = json.loads(values[name])
    except (KeyError, json.JSONDecodeError) as exc:
        raise CampaignBundleError(f"{role} pairwise key document is invalid") from exc
    if not isinstance(payload, list):
        raise CampaignBundleError(f"{role} pairwise key document must be a list")
    return [dict(item) for item in payload]


def _verify_pairwise_contract(role_values: dict[str, dict[str, str]]) -> int:
    observations: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for role in ROLES:
        for item in _pairwise_entries(role_values[role], role=role):
            pair = (str(item["source_site"]), str(item["destination_site"]))
            observations.setdefault(pair, []).append(
                (role, str(item["key_id"]), str(item["secret"]))
            )

    expected_pairs = {
        (PHYSICAL_SITE[role], peer)
        for role, peers in EXPECTED_PEERS.items()
        for peer in peers
    }
    if set(observations) != expected_pairs:
        raise CampaignBundleError("campaign pairwise key graph differs from fixed topology")
    key_ids: dict[str, tuple[str, str]] = {}
    secrets: dict[str, tuple[str, str]] = {}
    for pair, items in observations.items():
        endpoint_roles = {role for role, _key_id, _secret in items}
        expected_roles = {
            role for role, site in PHYSICAL_SITE.items() if site in pair
        }
        identities = {(key_id, secret) for _role, key_id, secret in items}
        if len(items) != 2 or endpoint_roles != expected_roles or len(identities) != 1:
            raise CampaignBundleError(
                f"directional pairwise key is not identical at both endpoints: {pair}"
            )
        key_id, secret = next(iter(identities))
        if key_id in key_ids and key_ids[key_id] != pair:
            raise CampaignBundleError("pairwise key id is reused by another direction")
        if secret in secrets and secrets[secret] != pair:
            raise CampaignBundleError("pairwise secret is reused by another direction")
        key_ids[key_id] = pair
        secrets[secret] = pair
    return len(observations)


def _verify_witness_contract(role_values: dict[str, dict[str, str]]) -> set[str]:
    witness = role_values["witness"]
    public_keys = {
        role_values[role].get("WRITER_WITNESS_PUBLIC_KEY", "")
        for role in ("webapp-fi", "webapp-ir", "witness")
    }
    if len(public_keys) != 1 or not next(iter(public_keys)):
        raise CampaignBundleError("Witness public key differs across role bundles")

    witness_secrets: set[str] = set()
    witness_key_ids: set[str] = set()
    for role, prefix in (
        ("webapp-fi", "WEBAPP_FI"),
        ("webapp-ir", "WEBAPP_IR"),
    ):
        key_name = f"{prefix}_WITNESS_KEY_ID"
        secret_name = f"{prefix}_WITNESS_SECRET"
        endpoint_pair = (
            role_values[role].get(key_name),
            role_values[role].get(secret_name),
        )
        witness_pair = (witness.get(key_name), witness.get(secret_name))
        if endpoint_pair != witness_pair or not all(endpoint_pair):
            raise CampaignBundleError(
                f"{role} Witness credential differs from the Witness bundle"
            )
        key_id, secret = endpoint_pair
        if key_id in witness_key_ids or secret in witness_secrets:
            raise CampaignBundleError("Witness credentials are reused between WebApp sites")
        witness_key_ids.add(str(key_id))
        witness_secrets.add(str(secret))
    return witness_secrets


def _database_secrets(role_values: dict[str, dict[str, str]]) -> set[str]:
    seen: dict[str, tuple[str, str]] = {}
    for role, values in role_values.items():
        for name, value in values.items():
            if not (name.endswith("_DB_PASSWORD") or name.endswith("_POSTGRES_PASSWORD")):
                continue
            prior = seen.get(value)
            if prior is not None:
                raise CampaignBundleError(
                    f"database credential is reused across campaign roles: {prior[0]} and {role}"
                )
            seen[value] = (role, name)
    return set(seen)


def _verify_application_secret_contract(
    role_values: dict[str, dict[str, str]],
    *,
    reserved_secrets: set[str],
) -> None:
    fi = role_values["webapp-fi"]
    ir = role_values["webapp-ir"]
    bot = role_values["bot-fi"]
    webapp_jwt = fi.get("WEBAPP_JWT_SECRET_KEY")
    origin_key = fi.get("ORIGIN_READINESS_API_KEY")
    if not webapp_jwt or webapp_jwt != ir.get("WEBAPP_JWT_SECRET_KEY"):
        raise CampaignBundleError("WebApp session key differs between FI and IR")
    if not origin_key or origin_key != ir.get("ORIGIN_READINESS_API_KEY"):
        raise CampaignBundleError("origin-readiness key differs between FI and IR")
    bot_jwt = bot.get("BOT_FI_JWT_SECRET_KEY")
    if not bot_jwt or bot_jwt == webapp_jwt:
        raise CampaignBundleError("Bot and WebApp JWT credentials are not isolated")
    if {webapp_jwt, origin_key, bot_jwt} & reserved_secrets:
        raise CampaignBundleError(
            "application credential is reused as database/transport/Witness material"
        )


def verify_campaign_bundle(
    *,
    canonical_compose: dict[str, Any],
    bundles: dict[str, tuple[bytes, bytes]],
    inventory: dict[str, Any],
    approval: dict[str, Any],
    approval_policy: dict[str, Any],
    verify_files: bool,
) -> dict[str, Any]:
    if set(bundles) != set(ROLES):
        raise CampaignBundleError("campaign requires exactly one bundle for every role")
    role_values: dict[str, dict[str, str]] = {}
    role_results: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        compose_bytes, env_bytes = bundles[role]
        try:
            role_results[role] = verify_role_bundle(
                role=role,
                canonical_compose=canonical_compose,
                role_compose_bytes=compose_bytes,
                env_bytes=env_bytes,
                inventory=inventory,
                approval=approval,
                approval_policy=approval_policy,
                verify_files=verify_files,
            )
            role_values[role] = parse_env_values(env_bytes.decode("utf-8"))
        except (RoleBundleError, UnicodeDecodeError) as exc:
            raise CampaignBundleError(f"{role} role bundle is invalid") from exc

    pairwise_count = _verify_pairwise_contract(role_values)
    witness_secrets = _verify_witness_contract(role_values)
    database_secrets = _database_secrets(role_values)
    pairwise_secrets = {
        str(item["secret"])
        for role in ROLES
        for item in _pairwise_entries(role_values[role], role=role)
    }
    if database_secrets & (witness_secrets | pairwise_secrets):
        raise CampaignBundleError(
            "database credential is reused as transport or Witness material"
        )
    _verify_application_secret_contract(
        role_values,
        reserved_secrets=database_secrets | witness_secrets | pairwise_secrets,
    )

    release_shas = {result["release_sha"] for result in role_results.values()}
    inventory_hashes = {
        result["inventory_sha256"] for result in role_results.values()
    }
    if len(release_shas) != 1 or len(inventory_hashes) != 1:
        raise CampaignBundleError("role bundles are not bound to one release/inventory")
    campaign_material = {
        role: {
            "compose_sha256": role_results[role]["compose_sha256"],
            "environment_sha256": role_results[role]["environment_sha256"],
        }
        for role in ROLES
    }
    campaign_hash = hashlib.sha256(
        json.dumps(campaign_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "status": "verified",
        "campaign_id": inventory["campaign_id"],
        "release_sha": next(iter(release_shas)),
        "inventory_sha256": next(iter(inventory_hashes)),
        "campaign_bundle_sha256": campaign_hash,
        "roles": list(ROLES),
        "directional_pairwise_key_count": pairwise_count,
        "database_credential_count": len(database_secrets),
        "file_attestation": verify_files,
    }


def _parse_bundle(value: str) -> tuple[str, Path, Path]:
    fields = value.split("=", 1)
    paths = fields[1].split(",", 1) if len(fields) == 2 else []
    if len(fields) != 2 or len(paths) != 2 or fields[0] not in ROLES:
        raise CampaignBundleError(
            "--bundle must use role=/path/compose.yml,/path/role.env"
        )
    return fields[0], Path(paths[0]), Path(paths[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--bundle", action="append", required=True)
    parser.add_argument("--skip-file-attestation", action="store_true")
    args = parser.parse_args(argv)
    try:
        parsed = [_parse_bundle(value) for value in args.bundle]
        if len(parsed) != len(ROLES) or len({role for role, _c, _e in parsed}) != len(ROLES):
            raise CampaignBundleError("exactly four distinct role bundles are required")
        bundles = {
            role: (
                _verify_bundle_source(compose, expected_mode=0o640),
                _verify_bundle_source(env, expected_mode=0o600),
            )
            for role, compose, env in parsed
        }
        result = verify_campaign_bundle(
            canonical_compose=yaml.safe_load(
                args.canonical_compose.read_text(encoding="utf-8")
            ),
            bundles=bundles,
            inventory=load_inventory(args.inventory),
            approval=load_inventory(args.approval),
            approval_policy=load_inventory(args.approval_policy),
            verify_files=not args.skip_file_attestation,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
