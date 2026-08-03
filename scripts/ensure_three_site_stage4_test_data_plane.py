#!/usr/bin/env python3
"""Idempotently open only the pinned Stage 4 test data-plane paths in Arvan ECC."""

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

from scripts.provision_arvan_witness_recovery_vps import (  # noqa: E402
    api_request,
    list_data,
    read_private_text,
    response_data,
    server_public_ipv4,
)


TOKEN_FILE = REPO_ROOT / "tmp/secrets/arvan-infra-apikey"
CAMPAIGN_ID = "fd34231d-f52e-498a-aab4-438c99d88fc5"
PRODUCTION_IPS = frozenset(
    {"65.109.216.187", "65.109.220.59", "95.38.164.29", "37.152.191.11"}
)
SERVERS = {
    "bot_fi": {
        "region": "eu-west1-a",
        "server_id": "b42750eb-1efb-4595-87b6-4f61606422f1",
        "name": "three-site-matrix-destructive-20260726-bot-fi",
        "public_ip": "130.185.121.98",
        "security_group_id": "de0e1e19-6b91-4603-a012-d87baa519d82",
    },
    "webapp_fi": {
        "region": "eu-west1-a",
        "server_id": "fcaeba99-622f-4dfc-8116-2c44bf2ef3ce",
        "name": "three-site-matrix-destructive-20260726-webapp-fi",
        "public_ip": "194.5.206.69",
        "security_group_id": "de0e1e19-6b91-4603-a012-d87baa519d82",
    },
    "webapp_ir": {
        "region": "ir-thr-fr1",
        "server_id": "1dca4b24-6aba-4d11-b430-c8c7dcce2b8a",
        "name": "three-site-matrix-destructive-20260726-webapp-ir",
        "public_ip": "188.213.198.115",
        "security_group_id": "50dd5d86-7e82-489f-b3b9-de3024d96367",
    },
    "witness": {
        "region": "eu-west1-a",
        "server_id": "3d883b04-0299-4894-8517-9fa7982586a9",
        "name": "three-site-matrix-destructive-20260726-witness",
        "public_ip": "130.185.121.152",
        "security_group_id": "de0e1e19-6b91-4603-a012-d87baa519d82",
    },
}
GROUPS = {
    "eu-west1-a": {
        "id": "de0e1e19-6b91-4603-a012-d87baa519d82",
        "real_name": "three-site-full-matrix-destructive-20260726",
    },
    "ir-thr-fr1": {
        "id": "50dd5d86-7e82-489f-b3b9-de3024d96367",
        "real_name": "three-site-full-matrix-destructive-20260726",
    },
}

# The EU group is shared by Bot-FI, WebApp-FI and Witness.  A rule can therefore
# name the exact source while the absence of a listener keeps unrelated members
# closed.  The Iran group is attached only to the pinned WebApp-IR test server.
RULES = (
    ("eu-west1-a", "stage4-bot-fi-dr-tls", "130.185.121.98/32", 8443),
    ("eu-west1-a", "stage4-webapp-fi-dr-tls", "194.5.206.69/32", 8443),
    ("eu-west1-a", "stage4-webapp-ir-dr-tls", "188.213.198.115/32", 8443),
    ("eu-west1-a", "stage4-webapp-fi-witness-tls", "194.5.206.69/32", 8444),
    ("eu-west1-a", "stage4-webapp-ir-witness-tls", "188.213.198.115/32", 8444),
    ("ir-thr-fr1", "stage4-bot-fi-dr-tls", "130.185.121.98/32", 8443),
    ("ir-thr-fr1", "stage4-webapp-fi-dr-tls", "194.5.206.69/32", 8443),
)


class Stage4DataPlaneError(RuntimeError):
    pass


def _rule_payload(description: str, source: str, port: int) -> dict[str, Any]:
    return {
        "description": description,
        "direction": "ingress",
        "protocol": "tcp",
        "port_from": str(port),
        "port_to": str(port),
        "ips": [source],
    }


def _rule_commitment() -> str:
    encoded = json.dumps(RULES, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def confirmation_phrase() -> str:
    return f"add-stage4-test-data-plane:{CAMPAIGN_ID}:{_rule_commitment()[:16]}"


def _read_server(token: str, role: str) -> dict[str, Any]:
    expected = SERVERS[role]
    server = response_data(
        api_request(
            "GET",
            f"/regions/{expected['region']}/servers/{expected['server_id']}",
            token,
        ),
        f"pinned Stage 4 server {role}",
    )
    references = server.get("security_groups") if isinstance(server, dict) else None
    if (
        not isinstance(server, dict)
        or server.get("id") != expected["server_id"]
        or server.get("name") != expected["name"]
        or server_public_ipv4(server) != expected["public_ip"]
        or str(server.get("status", "")).upper() != "ACTIVE"
        or expected["public_ip"] in PRODUCTION_IPS
        or not isinstance(references, list)
        or not any(
            isinstance(item, dict)
            and item.get("id") == expected["security_group_id"]
            for item in references
        )
    ):
        raise Stage4DataPlaneError(f"pinned non-production server identity differs for {role}")
    return server


def _read_groups(token: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for region, expected in GROUPS.items():
        matches = [
            group
            for group in list_data(
                token, f"/regions/{region}/securities", "Stage 4 test security groups"
            )
            if group.get("id") == expected["id"]
            and group.get("real_name") == expected["real_name"]
        ]
        if len(matches) != 1:
            raise Stage4DataPlaneError(f"pinned Stage 4 security group differs in {region}")
        result[region] = matches[0]
    return result


def _matching_rule(
    group: dict[str, Any], *, description: str, source: str, port: int
) -> dict[str, Any] | None:
    matches = [
        item
        for item in group.get("rules", [])
        if isinstance(item, dict) and item.get("description") == description
    ]
    if len(matches) > 1:
        raise Stage4DataPlaneError(f"duplicate Stage 4 rule exists: {description}")
    if not matches:
        return None
    rule = matches[0]
    if (
        rule.get("direction") != "ingress"
        or rule.get("protocol") != "tcp"
        or int(rule.get("port_start") or -1) != port
        or int(rule.get("port_end") or -1) != port
        or rule.get("ip") != source
        or rule.get("ether_type") != "IPv4"
    ):
        raise Stage4DataPlaneError(f"existing Stage 4 rule drifted: {description}")
    return rule


def execute(token: str, *, apply: bool, confirm: str | None) -> dict[str, Any]:
    for role in SERVERS:
        _read_server(token, role)
    groups = _read_groups(token)
    missing = [
        (region, description, source, port)
        for region, description, source, port in RULES
        if _matching_rule(
            groups[region], description=description, source=source, port=port
        )
        is None
    ]
    if not apply:
        return {
            "status": "already_present" if not missing else "planned",
            "apply": False,
            "campaign_id": CAMPAIGN_ID,
            "rule_commitment_sha256": _rule_commitment(),
            "rule_count": len(RULES),
            "missing_rule_count": len(missing),
            "required_confirmation": confirmation_phrase(),
            "server_or_volume_lifecycle_operation": False,
            "production_overlap": False,
        }
    if confirm != confirmation_phrase():
        raise Stage4DataPlaneError("Stage 4 data-plane confirmation mismatch")
    for region, description, source, port in missing:
        api_request(
            "POST",
            (
                f"/regions/{region}/securities/security-rules/"
                f"{GROUPS[region]['id']}"
            ),
            token,
            _rule_payload(description, source, port),
        )
    groups = _read_groups(token)
    absent = [
        description
        for region, description, source, port in RULES
        if _matching_rule(
            groups[region], description=description, source=source, port=port
        )
        is None
    ]
    if absent:
        raise Stage4DataPlaneError(f"Stage 4 rules did not persist: {sorted(absent)}")
    return {
        "status": "present",
        "apply": True,
        "campaign_id": CAMPAIGN_ID,
        "rule_commitment_sha256": _rule_commitment(),
        "rule_count": len(RULES),
        "added_rule_count": len(missing),
        "server_or_volume_lifecycle_operation": False,
        "production_overlap": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        result = execute(
            read_private_text(args.token_file), apply=args.apply, confirm=args.confirm
        )
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
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
